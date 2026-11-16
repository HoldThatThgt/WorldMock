"""@brief 运行入口：准入、身份冻结与分支恢复，再在一个事件循环里装配唯一的模型客户端与运行宿主，运行到结束（docs/design/10-runtime.md）。"""

from __future__ import annotations

from worldmock.configuration.model.model_metadata import credential_value, frozen_metadata_loader
import copy
import hashlib
import logging
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from worldmock.foundation.errors import IntegrityError
from worldmock.runtime.monitoring.status import DriverHeartbeat

if TYPE_CHECKING:
    from worldmock.agents.life_agent import PersonInputs
    from worldmock.configuration.model.model_client import ModelClient
    from worldmock.recording_engine.storage.store import FootprintStore
    from worldmock.runtime.lifecycle.run_host import RunHost
    from worldmock.state.decisions.results import CounterfactualIntervention
    from worldmock.state.identity import RunIdentity
    from worldmock.configuration.inputs.environment import EnvironmentInputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """@brief 新建正式运行所需的完整输入。

    :param environment_root: 已冻结生活场景路径。
    :param run_dir: 必须尚不存在的新运行目录。
    :param run_id: 调用方分配的稳定运行身份。
    :param source_group_id: 首次运行的来源组身份，必须等于 run_id。
    :param same_second_continuation: 同秒续接开关（缺省开），冻结进清单 stages.action 与人物配置；
        恢复与重放继承父清单的值（docs/design/04-simulation-engine.md「世界与请求」）。
    """

    environment_root: Path
    run_dir: Path
    run_id: str
    source_group_id: str
    same_second_continuation: bool = True


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    """@brief 从共同检查点建立新分支所需的完整输入。

    :param environment_root: 与父运行身份一致的冻结场景路径。
    :param parent_run_dir: 只读父运行目录。
    :param checkpoint_id: SQLite 中已提交的共同检查点身份。
    :param run_dir: 必须尚不存在的新分支目录。
    """

    environment_root: Path
    parent_run_dir: Path
    checkpoint_id: str
    run_dir: Path


@dataclass(frozen=True, slots=True)
class CounterfactualRequest:
    """@brief 从共同检查点建立并实际重演一个封闭世界条件假设。

    :param base: 普通恢复所需的父运行、检查点和新目录。
    :param intervention: 在子分支首次决策前应用一次的严格干预。
    """

    base: ResumeRequest
    intervention: CounterfactualIntervention

    def __post_init__(self) -> None:
        """@brief 拒绝绕开严格恢复或生活干预模型的输入。"""
        from worldmock.state.decisions.results import CounterfactualIntervention
        from worldmock.state.identity import RunIdentity

        if not isinstance(self.base, ResumeRequest):
            raise TypeError("Counterfactual base must be a ResumeRequest")
        if type(self.intervention) is not CounterfactualIntervention:
            raise TypeError("Counterfactual intervention must use the strict life model")


@dataclass(frozen=True, slots=True)
class RunResult:
    """@brief 一次运行到精确结束边界后的结果。

    :param run_id: 实际运行或恢复分支身份。
    :param run_dir: 唯一运行目录。
    :param current_time: 已确认的最终模拟时间。
    :param step_count: 本进程运行宿主完成的步数。
    """

    run_id: str
    run_dir: Path
    current_time: datetime
    step_count: int


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    """@brief 统一新建与恢复后的内部启动输入。"""

    run_dir: Path
    run_id: str
    inputs: EnvironmentInputs
    start_time: datetime
    stage_identity: dict[str, Any]
    agents: dict[int, PersonInputs]
    memory_records: Mapping[int, tuple[dict[str, Any], ...]]
    run_identity: RunIdentity | None = None
    intervention: CounterfactualIntervention | None = None
    intervention_sha256: str | None = None
    same_second_continuation: bool = True


async def run_world(request: RunRequest) -> RunResult:
    """@brief 新建并运行到场景排他结束时间。

    :param request: 新运行完整输入。
    :returns: 已确认结束时间与推进次数。
    """
    try:
        prepared = _prepare_new_run(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock run failed")
        raise


async def resume_world(request: ResumeRequest) -> RunResult:
    """@brief 从 SQLite 共同检查点创建分支并运行到结束。

    :param request: 恢复分支完整输入。
    :returns: 子分支已确认结束时间与推进次数。
    """
    try:
        prepared = _prepare_resume(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock resume failed")
        raise


async def replay_world(request: CounterfactualRequest) -> RunResult:
    """@brief 从共同检查点建立分支、应用世界条件干预并重新运行。

    :param request: 严格恢复输入和一次封闭干预。
    :returns: 子分支已确认结束时间与推进次数。
    """
    try:
        prepared = _prepare_resume(request.base, request.intervention)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock counterfactual replay failed")
        raise


def _prepare_new_run(request: RunRequest) -> _PreparedRun:
    """@brief 完整环境纯准入后冻结运行身份，形成唯一人物与新世界参数；失败时不留下运行目录（docs/design/10-runtime.md）。"""
    from worldmock.agents.life_agent import person_inputs
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.configuration.inputs.environment import load_environment

    run_dir = Path(request.run_dir).resolve()
    _require_run_shell(run_dir)
    if not request.run_id.strip() or request.source_group_id != request.run_id:
        logger.error("A fresh run requires matching run_id and source_group_id")
        raise ValueError("A fresh run requires matching run_id and source_group_id")
    root = Path(request.environment_root).absolute()
    inputs = load_environment(root)
    identity = _run_identity(request, inputs)
    stage = run_setup.stage_model_identity(inputs.config.model, inputs.model_metadata,
                                           request.same_second_continuation)
    _ops_log(run_dir, inputs.config.timezone, inputs.config.start_time,
             {"code": "RUN_STARTED", "detail": "Run started", "run_id": request.run_id,
              "code_sha256": identity.code_sha256, "persona_sha256": inputs.persona_sha256,
              "config_sha256": inputs.config_sha256, "stage_model_identity": stage})
    agents = {inputs.persona.agent_id: person_inputs(inputs.persona, stage)}
    records = {agent_id: () for agent_id in identity.agent_ids}
    return _PreparedRun(run_dir, request.run_id, inputs, inputs.config.start_time, stage, agents, records,
                        run_identity=identity, same_second_continuation=request.same_second_continuation)


def _run_identity(request: RunRequest, inputs: EnvironmentInputs) -> RunIdentity:
    """@brief 冻结唯一人物、配置时间、实际模型和锁定代码身份。"""
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.state.identity import RunIdentity

    root = run_setup.project_root()
    git_commit, git_dirty = run_setup._git_state(root)
    return RunIdentity(run_id=request.run_id, source_group_id=request.source_group_id,
                       agent_ids=(inputs.persona.agent_id,),
                       start_at=inputs.config.start_time.isoformat(), end_at=inputs.config.stop_time.isoformat(),
                       timezone=inputs.config.timezone, model_identity=run_setup._model_identity(inputs.config.model),
                       code_sha256=run_setup.runtime_code_sha256(), git_commit=git_commit, git_dirty=git_dirty)


def _prepare_resume(request: ResumeRequest,
                    intervention: CounterfactualIntervention | None = None) -> _PreparedRun:
    """@brief 先打开父运行、取到检查点并校验检查点世界，再用父清单冻结的模型元数据加载环境（不访问模型服务）、
    读取凭据、核对身份，最后原子恢复工作区与世界；父运行的库、清单或检查点不对时，不加载环境、不读取凭据、不复制任何文件。

    凭据缺失时不建分支目录（docs/design/10-runtime.md）。
    """
    from worldmock.agents.life_agent import person_inputs
    from worldmock.configuration.inputs.environment import load_environment
    from worldmock.recording_engine.storage.store import FootprintStore
    from worldmock.simulation_engine.decisions.continuation import continuation_enabled
    from worldmock.simulation_engine.world.run_identity import require_environment_identity
    from worldmock.state.world.world import WorldState

    destination = Path(request.run_dir).resolve()
    _require_run_shell(destination)
    root = Path(request.environment_root).absolute()
    digest = _intervention_sha256(intervention) if intervention is not None else None
    metadata = {"branch_kind": "counterfactual", "intervention_sha256": digest} if digest else None
    parent = FootprintStore(Path(request.parent_run_dir))
    try:
        checkpoint = parent.get_checkpoint(request.checkpoint_id)
        WorldState.model_validate(checkpoint.state)
        inputs = load_environment(root, metadata_loader=frozen_metadata_loader(parent.manifest))
        credential_value(inputs.config.model)
        _require_runtime_identity(parent.manifest, inputs)
        require_environment_identity(parent.manifest, inputs)
        manifest, state, records = _restore_branch(parent, checkpoint, destination, metadata)
    finally:
        parent.close()
    continuation = continuation_enabled(manifest)
    stage = manifest["stage_model_identity"]
    agents = {inputs.persona.agent_id: person_inputs(inputs.persona, stage)}
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(inputs.config.timezone))
    _ops_log(destination, inputs.config.timezone, start,
             {"code": "RUN_RESUMED", "detail": "Run resumed from checkpoint", "run_id": manifest["run_id"],
              "code_sha256": manifest.get("code_sha256"), "checkpoint_id": request.checkpoint_id})
    return _PreparedRun(destination, manifest["run_id"], inputs, start, stage, agents, records,
                        intervention=intervention, intervention_sha256=digest, same_second_continuation=continuation)


def _intervention_sha256(intervention: CounterfactualIntervention) -> str:
    """@brief 计算与 LifeEngine 相同的规范反事实干预身份。"""
    from worldmock.foundation.serialization import sha256_json

    payload = {
        "tool_name": "counterfactual_intervention",
        "request": intervention.model_dump(mode="json"),
    }
    return sha256_json(payload)


def _require_runtime_identity(manifest: dict[str, Any], inputs: EnvironmentInputs) -> None:
    """@brief 恢复核对代码、配置模型与继承的阶段开关。"""
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.simulation_engine.decisions.continuation import continuation_enabled

    identity = run_setup.validate_manifest_stage_identity(manifest)
    expected = run_setup._model_identity(inputs.config.model).model_dump(mode="json")
    stages = run_setup.stage_model_identity(inputs.config.model, inputs.model_metadata, continuation_enabled(manifest))
    if manifest.get("model_identity") != expected or identity != stages:
        logger.error("Checkpoint model identity differs from the current contract")
        raise IntegrityError("Checkpoint model identity differs from the current LifeAgent contract")
    if manifest.get("code_sha256") != run_setup.runtime_code_sha256():
        logger.error("Checkpoint code identity differs from the current runtime")
        raise IntegrityError("Checkpoint code identity differs from the current runtime")


def _ops_log(run_dir: Path, timezone_name: str, sim_time: int | datetime | None,
             message: dict[str, Any], level: str = "info") -> None:
    """@brief 运行进程侧写一条 runtime 操作日志，随写随关不驻留句柄。"""
    from worldmock.recording_engine.storage.opslog import OpsLogger

    writer = OpsLogger(run_dir, timezone_name)
    try:
        writer.log(sim_time, "runtime", level, message)
    finally:
        writer.close()


def _ops_crash_log(prepared: _PreparedRun, host: RunHost | None, error: BaseException) -> None:
    """@brief 崩溃路径操作日志；自身任何故障只记日志，不得掩盖原始异常。"""
    try:
        sim_time = host.current_time if host is not None and host.engine is not None else prepared.start_time
        _ops_log(prepared.run_dir, prepared.inputs.config.timezone, sim_time,
                 {"code": "RUN_CRASHED", "detail": f"Run crashed: {type(error).__name__}: {error}",
                  "run_id": prepared.run_id}, level="error")
    except Exception:
        logger.exception("Run crash operations log failed")


def _require_run_shell(run_dir: Path) -> None:
    """@brief 只允许不存在的目录或 CLI 创建且仅含 status.json 的运行壳。"""
    if not run_dir.exists():
        return
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise FileExistsError(f"Run destination is not a safe directory: {run_dir}")
    entries = list(run_dir.iterdir())
    if len(entries) != 1 or entries[0].name != "status.json" or not entries[0].is_file():
        raise FileExistsError(f"Run directory already contains artifacts: {run_dir}")


def _restore_branch(
    parent: FootprintStore,
    checkpoint: Any,
    destination: Path,
    branch_metadata: dict[str, str] | None,
) -> tuple[dict, dict, dict[int, tuple[dict[str, Any], ...]]]:
    """@brief 在外层临时目录恢复数据库和人物，完整后才发布子目录；人物工作区按快照恢复并严格读回。"""
    from worldmock.agents.memory.workspace import restore_workspaces
    from worldmock.recording_engine.storage.checkpoint import restore_branch
    from worldmock.recording_engine.storage.store import FootprintStore

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".worldmock-resume-", dir=destination.parent))
    staged_run = stage_root / "run"
    child: FootprintStore | None = None
    try:
        child = restore_branch(parent, checkpoint.checkpoint_id, staged_run, branch_metadata)
        records = restore_workspaces(checkpoint.snapshots, child.assets, staged_run,
                                     tuple(sorted(int(item) for item in child.manifest["agent_ids"])))
        manifest, state = dict(child.manifest), child.state
        child.close()
        child = None
        _transfer_status_shell(destination, staged_run)
        staged_run.rename(destination)
        return manifest, state, records
    except Exception:
        logger.exception("Common checkpoint branch restoration failed")
        raise
    finally:
        if child is not None:
            child.close()
        shutil.rmtree(stage_root, ignore_errors=True)


def _transfer_status_shell(destination: Path, staged_run: Path) -> None:
    """@brief 在完整恢复发布前保留 CLI 的可变状态文件。"""
    if not destination.exists():
        return
    source = destination / "status.json"
    shutil.copy2(source, staged_run / "status.json")
    source.unlink()
    destination.rmdir()


async def _execute(prepared: _PreparedRun) -> RunResult:
    """@brief 在驱动心跳内装配唯一的模型客户端与运行宿主，按启动种类启动并运行到结束；关闭失败与运行失败一并保留。"""
    from worldmock.runtime.lifecycle.run_host import RunHost

    with DriverHeartbeat(prepared.run_dir):
        host: RunHost | None = None
        failure: BaseException | None = None
        try:
            host = RunHost(prepared.run_dir, prepared.inputs, _model_client(prepared), prepared.agents,
                           prepared.memory_records)
            await _start(host, prepared)
            await host.run()
            _ops_log(prepared.run_dir, prepared.inputs.config.timezone, host.current_time,
                     {"code": "RUN_ENDED", "detail": "Run finished", "run_id": prepared.run_id,
                      "steps": host.step_count})
            return RunResult(prepared.run_id, prepared.run_dir, host.current_time, host.step_count)
        except BaseException as error:
            failure = error
            _ops_crash_log(prepared, host, error)
            raise
        finally:
            if host is not None:
                await _close_host(host, failure)


def _model_client(prepared: _PreparedRun) -> ModelClient:
    """@brief 运行装配里构造模型客户端的唯一一处：端点与两类重试次数取冻结的阶段身份，凭据只在这里读取。"""
    from worldmock.configuration.model.model_client import ModelClient

    identity = prepared.stage_identity
    return ModelClient(base_url=identity["base_url"], api_key=credential_value(prepared.inputs.config.model),
                       max_retries=identity["max_retries"], transport_max_retries=identity["transport_max_retries"])


async def _start(host: RunHost, prepared: _PreparedRun) -> None:
    """@brief 按启动种类选一种启动顺序：有运行身份是新建，没有干预是普通恢复，否则是反事实；干预身份缺失时拒绝进入运行。"""
    if prepared.run_identity is not None:
        await host.start_new(prepared.run_identity, prepared.stage_identity, _creation_options(prepared))
    elif prepared.intervention is None:
        await host.start_resume()
    elif prepared.intervention_sha256 is None:
        logger.error("Counterfactual intervention identity is unavailable")
        raise RuntimeError("Counterfactual intervention identity is unavailable")
    else:
        await host.start_counterfactual(prepared.intervention, prepared.intervention_sha256)


def _creation_options(prepared: _PreparedRun) -> dict[str, Any]:
    """@brief 新建运行的引擎选项：同秒续接开关、输入警告与习惯签名反应文本；每条警告写一条运维日志，不调用模型。"""
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.recording_engine.storage.opslog import OpsLogger

    inputs = prepared.inputs
    specs, warnings = run_setup.creation_inputs(inputs, prepared.same_second_continuation)
    writer = OpsLogger(prepared.run_dir, inputs.config.timezone)
    try:
        for warning in warnings:
            writer.log(prepared.start_time, "runtime", "warn",
                       {"code": warning["code"], "detail": "Input warning frozen into the run manifest",
                        "agent_id": warning["agent_id"], "message": warning["message"], "details": warning["details"]})
    finally:
        writer.close()
    return {"same_second_continuation": prepared.same_second_continuation, "input_warnings": warnings,
            "persona_habit_responses": habit_responses(specs)}


async def _close_host(host: RunHost, failure: BaseException | None) -> None:
    """@brief 关闭运行宿主；关闭失败时写日志，运行本身已失败则两项故障并入一个异常组，否则原样抛出关闭故障。"""
    try:
        await host.close()
    except BaseException as error:
        logger.exception("Run host close failed")
        if failure is not None:
            raise BaseExceptionGroup("WorldMock run and cleanup failed", [failure, error])
        raise


def habit_responses(specs: tuple[dict[str, Any], ...]) -> dict[int, list[str]]:
    """@brief 人设层习惯签名的反应文本（人物编号 → 文本，按卡片顺序）：取工作区输入里由人设表构造的结构化卡片，
    没有卡片的人物不出现；经 create_options 冻结进运行清单 persona_habit_responses
    （docs/design/09-io-formats.md「输入与模型身份」）。"""
    return {spec["id"]: [habit["response"] for habit in spec["profile"]["persona_card_struct"]["habits"]]
            for spec in specs if "persona_card_struct" in spec["profile"]}
