"""@brief 运行入口：准入、身份冻结与分支恢复，再在一个事件循环里装配唯一的模型注册表与运行宿主（docs/design/10-runtime.md）。"""

from __future__ import annotations

import copy
import logging
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.configuration.model.model_metadata import frozen_metadata_loader, verify_active_credentials
from worldmock.foundation.errors import IntegrityError
from worldmock.runtime.monitoring.status import DriverHeartbeat

if TYPE_CHECKING:
    from worldmock.agents.life_agent import PersonInputs
    from worldmock.configuration.inputs.environment import EnvironmentInputs
    from worldmock.configuration.model.model_registry import ModelRegistry
    from worldmock.recording_engine.storage.store import FootprintStore
    from worldmock.runtime.lifecycle.run_host import RunHost
    from worldmock.state.decisions.results import CounterfactualIntervention
    from worldmock.state.identity import RunIdentity

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """@brief 新建正式运行所需的完整输入。

    :param environment_root: 已冻结生活场景路径。
    :param run_dir: 必须尚不存在的新运行目录。
    :param run_id: 调用方分配的稳定运行身份。
    :param source_group_id: 首次运行的来源组身份，必须等于 run_id。
    """

    environment_root: Path
    run_dir: Path
    run_id: str
    source_group_id: str


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

        if not isinstance(self.base, ResumeRequest):
            logger.error("Counterfactual base is not a ResumeRequest")
            raise TypeError("Counterfactual base must be a ResumeRequest")
        if type(self.intervention) is not CounterfactualIntervention:
            logger.error("Counterfactual intervention does not use the strict model")
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
    agents: dict[int, PersonInputs]
    memory_records: Mapping[int, tuple[dict[str, Any], ...]]
    run_identity: RunIdentity | None = None
    intervention: CounterfactualIntervention | None = None
    intervention_sha256: str | None = None


async def run_world(request: RunRequest) -> RunResult:
    """@brief 新建并运行到场景排他结束时间。"""
    try:
        prepared = _prepare_new_run(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock run failed")
        raise


async def resume_world(request: ResumeRequest) -> RunResult:
    """@brief 从 SQLite 共同检查点创建分支并运行到结束。"""
    try:
        prepared = _prepare_resume(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock resume failed")
        raise


async def replay_world(request: CounterfactualRequest) -> RunResult:
    """@brief 从共同检查点建立分支、应用世界条件干预并重新运行。"""
    try:
        prepared = _prepare_resume(request.base, request.intervention)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock counterfactual replay failed")
        raise


def _prepare_new_run(request: RunRequest) -> _PreparedRun:
    """@brief 完整环境纯准入、凭据核验并冻结 v8 运行身份；失败时不留下运行目录。"""
    from worldmock.configuration.inputs.environment import load_environment

    run_dir = Path(request.run_dir).resolve()
    _require_run_shell(run_dir)
    if not request.run_id.strip() or request.source_group_id != request.run_id:
        logger.error("A fresh run requires matching run_id and source_group_id")
        raise ValueError("A fresh run requires matching run_id and source_group_id")
    inputs = load_environment(Path(request.environment_root).absolute())
    verify_active_credentials(inputs.config)
    identity = _run_identity(request, inputs)
    _ops_log(run_dir, inputs.config.timezone, inputs.config.start_time, {
        "code": "RUN_STARTED", "detail": "Run started", "run_id": request.run_id,
        "code_sha256": identity.code_sha256, "agent_ids": list(identity.agent_ids),
        "persona_sha256s": {str(agent_id): digest for agent_id, digest in identity.persona_sha256s.items()},
        "config_sha256": inputs.config_sha256,
        "model_identity": identity.model_identity.model_dump(mode="json"),
    })
    from worldmock.agents.life_agent import person_inputs

    agents = {persona.agent_id: person_inputs(persona) for persona in inputs.personas}
    records = {agent_id: () for agent_id in identity.agent_ids}
    return _PreparedRun(run_dir, request.run_id, inputs, inputs.config.start_time,
                        agents, records, run_identity=identity)


def _run_identity(request: RunRequest, inputs: EnvironmentInputs) -> RunIdentity:
    """@brief 冻结多人物标识、人设摘要、配置时间、v8 模型身份与锁定代码身份。"""
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.state.identity import RunIdentity

    root = run_setup.project_root()
    git_commit, git_dirty = run_setup._git_state(root)
    return RunIdentity(
        run_id=request.run_id, source_group_id=request.source_group_id,
        agent_ids=tuple(persona.agent_id for persona in inputs.personas),
        persona_sha256s=dict(inputs.persona_sha256s),
        start_at=inputs.config.start_time.isoformat(), end_at=inputs.config.stop_time.isoformat(),
        timezone=inputs.config.timezone, code_sha256=run_setup.runtime_code_sha256(),
        model_identity=run_setup.model_identity(inputs.config, inputs.model_metadata),
        git_commit=git_commit, git_dirty=git_dirty,
    )


def _prepare_resume(
    request: ResumeRequest, intervention: CounterfactualIntervention | None = None,
) -> _PreparedRun:
    """@brief 先开父运行并校验检查点与 v8 身份，核验凭据通过后才原子恢复全员工作区与世界分支。"""
    from worldmock.configuration.inputs.environment import load_environment
    from worldmock.recording_engine.storage.store import FootprintStore
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
        verify_active_credentials(inputs.config)
        _require_runtime_identity(parent.manifest, inputs)
        require_environment_identity(parent.manifest, inputs)
        manifest, state, records = _restore_branch(parent, checkpoint, destination, metadata)
    finally:
        parent.close()
    from worldmock.agents.life_agent import person_inputs

    agents = {persona.agent_id: person_inputs(persona) for persona in inputs.personas}
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(inputs.config.timezone))
    _ops_log(destination, inputs.config.timezone, start, {
        "code": "RUN_RESUMED", "detail": "Run resumed from checkpoint", "run_id": manifest["run_id"],
        "code_sha256": manifest.get("code_sha256"), "checkpoint_id": request.checkpoint_id,
    })
    return _PreparedRun(destination, manifest["run_id"], inputs, start, agents, records,
                        intervention=intervention, intervention_sha256=digest)


def _intervention_sha256(intervention: CounterfactualIntervention) -> str:
    """@brief 计算与 LifeEngine 相同的规范反事实干预身份。"""
    from worldmock.foundation.serialization import sha256_json

    payload = {"tool_name": "counterfactual_intervention", "request": intervention.model_dump(mode="json")}
    return sha256_json(payload)


def _require_runtime_identity(manifest: dict[str, Any], inputs: EnvironmentInputs) -> None:
    """@brief 恢复与反事实启动前核对清单 v8 的版本、全员标识、人设摘要、多模型身份与代码指纹。"""
    from worldmock.runtime.framework import setup as run_setup
    from worldmock.state.identity import (
        RUN_MANIFEST_SCHEMA_VERSION,
        ModelIdentity,
        check_persona_sha256s,
        validate_persona_sha256s_input,
    )

    if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        logger.error("Checkpoint manifest schema version %s disagrees with %s",
                     manifest.get("schema_version"), RUN_MANIFEST_SCHEMA_VERSION)
        raise IntegrityError("Checkpoint manifest schema version is incompatible")
    expected_agents = tuple(persona.agent_id for persona in inputs.personas)
    if manifest.get("agent_ids") != list(expected_agents):
        logger.error("Checkpoint agent_ids disagree with environment inputs")
        raise IntegrityError("Checkpoint agent identity differs from environment inputs")
    try:
        raw_personas = validate_persona_sha256s_input(manifest.get("persona_sha256s"))
        if not isinstance(raw_personas, dict):
            raise ValueError("persona_sha256s must be a mapping")
        persisted_personas = {int(agent_id): digest for agent_id, digest in raw_personas.items()}
        check_persona_sha256s(persisted_personas, set(expected_agents))
        persisted_model = ModelIdentity.model_validate(manifest.get("model_identity"))
    except (ValidationError, ValueError) as error:
        logger.error("Checkpoint identity payload failed validation: %s", error)
        raise IntegrityError("Checkpoint model identity differs from the current LifeAgent contract") from error
    if persisted_personas != dict(inputs.persona_sha256s):
        logger.error("Checkpoint persona_sha256s disagree with environment inputs")
        raise IntegrityError("Checkpoint persona identity differs from environment inputs")
    if persisted_model != run_setup.model_identity(inputs.config, inputs.model_metadata):
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
        logger.error("Run destination is not a safe directory: %s", run_dir)
        raise FileExistsError(f"Run destination is not a safe directory: {run_dir}")
    entries = list(run_dir.iterdir())
    if len(entries) != 1 or entries[0].name != "status.json" or not entries[0].is_file():
        logger.error("Run directory already contains artifacts: %s", run_dir)
        raise FileExistsError(f"Run directory already contains artifacts: {run_dir}")


def _restore_branch(
    parent: FootprintStore,
    checkpoint: Any,
    destination: Path,
    branch_metadata: dict[str, str] | None,
) -> tuple[dict, dict, dict[int, tuple[dict[str, Any], ...]]]:
    """@brief 在外层临时目录恢复数据库和多人物工作区，完整后才发布子目录。"""
    from worldmock.agents.memory.workspace import restore_workspaces
    from worldmock.recording_engine.storage.checkpoint import restore_branch

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
    """@brief 在驱动心跳内装配唯一模型注册表与运行宿主并运行到结束；保证注册表始终关闭。"""
    from worldmock.runtime.lifecycle.run_host import RunHost

    with DriverHeartbeat(prepared.run_dir):
        registry: ModelRegistry | None = None
        host: RunHost | None = None
        failure: BaseException | None = None
        try:
            registry = _model_registry(prepared)
            host = RunHost(prepared.run_dir, prepared.inputs, registry, prepared.agents, prepared.memory_records)
            registry = None
            await _start(host, prepared)
            await host.run()
            _ops_log(prepared.run_dir, prepared.inputs.config.timezone, host.current_time, {
                "code": "RUN_ENDED", "detail": "Run finished",
                "run_id": prepared.run_id, "steps": host.step_count,
            })
            return RunResult(prepared.run_id, prepared.run_dir, host.current_time, host.step_count)
        except BaseException as error:
            failure = error
            _ops_crash_log(prepared, host, error)
            raise
        finally:
            if host is not None:
                await _close_cleanup(host.close, failure)
            elif registry is not None:
                await _close_cleanup(registry.close, failure)


def _model_registry(prepared: _PreparedRun) -> ModelRegistry:
    """@brief 运行装配里构造模型注册表的唯一一处；多档案连接池由注册表集中持有。"""
    from worldmock.configuration.model.model_registry import ModelRegistry

    return ModelRegistry(prepared.inputs.config, prepared.inputs.model_metadata)


async def _start(host: RunHost, prepared: _PreparedRun) -> None:
    """@brief 按启动种类选一种启动顺序；有运行身份是新建，没有干预是普通恢复，否则是反事实。"""
    if prepared.run_identity is not None:
        await host.start_new(prepared.run_identity, _creation_options(prepared))
    elif prepared.intervention is None:
        await host.start_resume()
    elif prepared.intervention_sha256 is None:
        logger.error("Counterfactual intervention identity is unavailable")
        raise RuntimeError("Counterfactual intervention identity is unavailable")
    else:
        await host.start_counterfactual(prepared.intervention, prepared.intervention_sha256)


def _creation_options(prepared: _PreparedRun) -> dict[str, Any]:
    """@brief 新建运行的引擎选项：输入警告与习惯签名反应文本；每条警告写一条运维日志，不调用模型。"""
    from worldmock.recording_engine.storage.opslog import OpsLogger
    from worldmock.runtime.framework import setup as run_setup

    inputs = prepared.inputs
    specs, warnings = run_setup.creation_inputs(inputs)
    writer = OpsLogger(prepared.run_dir, inputs.config.timezone)
    try:
        for warning in warnings:
            writer.log(prepared.start_time, "runtime", "warn", {
                "code": warning["code"], "detail": "Input warning frozen into the run manifest",
                "agent_id": warning["agent_id"], "message": warning["message"], "details": warning["details"],
            })
    finally:
        writer.close()
    return {"input_warnings": warnings, "persona_habit_responses": habit_responses(specs)}


async def _close_cleanup(closer: Any, failure: BaseException | None) -> None:
    """@brief 在 finally 中统一关闭宿主或尚未移交的注册表；双重失败合并进异常组。"""
    try:
        await closer()
    except BaseException as error:
        logger.exception("Run resource cleanup failed")
        if failure is not None:
            raise BaseExceptionGroup("WorldMock run and cleanup failed", [failure, error]) from error
        raise


def habit_responses(specs: tuple[dict[str, Any], ...]) -> dict[int, list[str]]:
    """@brief 人设层习惯签名的反应文本（人物编号 -> 文本，按卡片顺序）；冻结进清单 persona_habit_responses。"""
    return copy.deepcopy({
        spec["id"]: [habit["response"] for habit in spec["profile"]["persona_card_struct"]["habits"]]
        for spec in specs if "persona_card_struct" in spec["profile"]
    })
