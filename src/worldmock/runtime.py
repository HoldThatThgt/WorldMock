"""@brief 编排原生 AgentSociety2、声明的模型服务、LifeAgent 与 LifeEnv。"""

from __future__ import annotations

from worldmock.model_endpoint import IndependentModelClient
import copy
import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError, ProtocolError

if TYPE_CHECKING:
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.models import CounterfactualIntervention, RunIdentity
    from worldmock.environment import EnvironmentInputs
    from worldmock.input_models import PersonaConfig

logger = logging.getLogger(__name__)

#: 自述、工具纠错与结束共享的有限回合预算。

#: 工作验收阶段参数（独立工作验收保持
#: disabled/required）。
#: 运行身份覆盖的 AS2 导入包名；包目录由 importlib.util.find_spec 按实际导入解析
#: （docs/design/06-agent.md「宿主模型协议」）。
@dataclass(frozen=True, slots=True)
class RunRequest:
    """@brief 新建正式运行所需的完整输入。

    :param project_root: 包含 custom 与 src 的 WorldMock 根目录。
    :param environment_root: 已冻结生活场景路径。
    :param run_dir: 必须尚不存在的新运行目录。
    :param run_id: 调用方分配的稳定运行身份。
    :param source_group_id: 首次运行的来源组身份，必须等于 run_id。
    :param same_second_continuation: 同秒续接开关（缺省开），冻结进清单 stages.action 与人物配置；
        恢复与重放继承父清单的值（docs/design/04-simulation-engine.md「世界与请求」）。
    """

    project_root: Path
    environment_root: Path
    run_dir: Path
    run_id: str
    source_group_id: str
    same_second_continuation: bool = True


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    """@brief 从共同检查点建立新分支所需的完整输入。

    :param project_root: 包含 custom 与 src 的 WorldMock 根目录。
    :param environment_root: 与父运行身份一致的冻结场景路径。
    :param parent_run_dir: 只读父运行目录。
    :param checkpoint_id: SQLite 中已提交的共同检查点身份。
    :param run_dir: 必须尚不存在的新分支目录。
    """

    project_root: Path
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
        from worldmock.life.models import CounterfactualIntervention, RunIdentity

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
    :param step_count: 本进程完成的 Society 推进次数。
    """

    run_id: str
    run_dir: Path
    current_time: datetime
    step_count: int


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    """@brief 统一新建与恢复后的内部启动输入。"""

    project_root: Path
    environment_root: Path
    run_dir: Path
    run_id: str
    inputs: EnvironmentInputs
    start_time: datetime
    env_kwargs: dict[str, Any]
    workspace_mode: str
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
    """@brief 完整环境纯准入后配置进程，再冻结运行身份，形成唯一人物与新世界参数。

    进程配置排在读完环境之后、读模型身份之前（读身份才导入 custom 与 AgentSociety2），失败时不留下运行目录
    （docs/design/10-runtime.md「实际框架边界」）。
    """
    from worldmock import run_setup
    from worldmock.environment import load_environment

    run_dir = Path(request.run_dir).resolve()
    _require_run_shell(run_dir)
    if not request.run_id.strip() or request.source_group_id != request.run_id:
        logger.error("A fresh run requires matching run_id and source_group_id")
        raise ValueError("A fresh run requires matching run_id and source_group_id")
    root = Path(request.environment_root).absolute()
    inputs = load_environment(root)
    run_setup._configure_process(request.project_root, inputs.config.model)
    identity = _run_identity(request, inputs)
    kwargs = {"environment_root": str(root), "run_dir": str(run_dir),
              "run_identity": identity.model_dump(mode="json"), "open_existing": False,
              "create_options": {"same_second_continuation": request.same_second_continuation}}
    _ops_log(run_dir, inputs.config.timezone, inputs.config.start_time,
             {"code": "RUN_STARTED", "detail": "Run started", "run_id": request.run_id,
              "code_sha256": identity.code_sha256, "persona_sha256": inputs.persona_sha256,
              "config_sha256": inputs.config_sha256, "stage_model_identity": run_setup.stage_model_identity(
                  inputs.config.model, inputs.model_metadata, request.same_second_continuation)})
    return _PreparedRun(Path(request.project_root).resolve(), root, run_dir, request.run_id,
                        inputs, inputs.config.start_time, kwargs, "create",
                        same_second_continuation=request.same_second_continuation)


def _run_identity(request: RunRequest, inputs: EnvironmentInputs) -> RunIdentity:
    """@brief 冻结唯一人物、配置时间、实际模型和锁定代码身份。"""
    from worldmock import run_setup
    from worldmock.life.models import RunIdentity

    root = Path(request.project_root)
    git_commit, git_dirty = run_setup._git_state(root)
    return RunIdentity(run_id=request.run_id, source_group_id=request.source_group_id,
                       agent_ids=(inputs.persona.agent_id,),
                       start_at=inputs.config.start_time.isoformat(), end_at=inputs.config.stop_time.isoformat(),
                       timezone=inputs.config.timezone, model_identity=run_setup._model_identity(inputs.config.model),
                       code_sha256=run_setup.runtime_code_sha256(root), git_commit=git_commit, git_dirty=git_dirty)


def _prepare_resume(request: ResumeRequest,
                    intervention: CounterfactualIntervention | None = None) -> _PreparedRun:
    """@brief 校验三个输入身份后原子恢复工作区与世界，人物卡片沿用工作区里冻结的那一份。

    取到检查点并校验过检查点世界之后、核对运行身份之前配置进程：父库或检查点不存在仍先于进程配置报出，
    配置失败时不建分支目录（docs/design/10-runtime.md「实际框架边界」）。
    """
    from worldmock import run_setup
    from worldmock.environment import load_environment
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.continuation import continuation_enabled
    from worldmock.life.run_identity import require_environment_identity
    from worldmock.life.models import WorldState

    destination = Path(request.run_dir).resolve()
    _require_run_shell(destination)
    root = Path(request.environment_root).absolute()
    inputs = load_environment(root)
    digest = _intervention_sha256(intervention) if intervention is not None else None
    metadata = {"branch_kind": "counterfactual", "intervention_sha256": digest} if digest else None
    parent = FootprintStore(Path(request.parent_run_dir))
    try:
        checkpoint = parent.get_checkpoint(request.checkpoint_id)
        WorldState.model_validate(checkpoint.state)
        run_setup._configure_process(request.project_root, inputs.config.model)
        _require_runtime_identity(parent.manifest, Path(request.project_root), inputs)
        require_environment_identity(parent.manifest, inputs)
        manifest, state = _restore_branch(parent, checkpoint, destination, metadata, inputs)
    finally:
        parent.close()
    continuation = continuation_enabled(manifest)
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(inputs.config.timezone))
    kwargs = {"environment_root": str(root), "run_dir": str(destination), "open_existing": True}
    _ops_log(destination, inputs.config.timezone, start,
             {"code": "RUN_RESUMED", "detail": "Run resumed from checkpoint", "run_id": manifest["run_id"],
              "code_sha256": manifest.get("code_sha256"), "checkpoint_id": request.checkpoint_id})
    return _PreparedRun(Path(request.project_root).resolve(), root, destination, manifest["run_id"],
                        inputs, start, kwargs, "restored", intervention, digest, continuation)


def _intervention_sha256(intervention: CounterfactualIntervention) -> str:
    """@brief 计算与 LifeEngine 相同的规范反事实干预身份。"""
    from worldmock.footprints.models import sha256_json

    payload = {
        "tool_name": "counterfactual_intervention",
        "request": intervention.model_dump(mode="json"),
    }
    return sha256_json(payload)


def _require_runtime_identity(manifest: dict[str, Any], project_root: Path, inputs: EnvironmentInputs) -> None:
    """@brief 恢复核对代码、配置模型与继承的阶段开关。"""
    from worldmock import run_setup
    from worldmock.life.continuation import continuation_enabled

    identity = run_setup.validate_manifest_stage_identity(manifest)
    expected = run_setup._model_identity(inputs.config.model).model_dump(mode="json")
    stages = run_setup.stage_model_identity(inputs.config.model, inputs.model_metadata, continuation_enabled(manifest))
    if manifest.get("model_identity") != expected or identity != stages:
        logger.error("Checkpoint model identity differs from the current contract")
        raise IntegrityError("Checkpoint model identity differs from the current LifeAgent contract")
    if manifest.get("code_sha256") != run_setup.runtime_code_sha256(project_root):
        logger.error("Checkpoint code identity differs from the current runtime")
        raise IntegrityError("Checkpoint code identity differs from the current runtime")


def _ops_log(run_dir: Path, timezone_name: str, sim_time: int | datetime | None,
             message: dict[str, Any], level: str = "info") -> None:
    """@brief 运行进程侧写一条 runtime 操作日志，随写随关不驻留句柄。"""
    from worldmock.opslog import OpsLogger

    writer = OpsLogger(run_dir, timezone_name)
    try:
        writer.log(sim_time, "runtime", level, message)
    finally:
        writer.close()


def _ops_crash_log(prepared: _PreparedRun, society: Any, error: BaseException) -> None:
    """@brief 崩溃路径操作日志；自身任何故障只记日志，不得掩盖原始异常。"""
    try:
        sim_time = society.current_time if society is not None else prepared.start_time
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
    inputs: EnvironmentInputs,
) -> tuple[dict, dict]:
    """@brief 在外层临时目录恢复数据库和人物，完整后才发布子目录；人物的结构化卡片须与冻结的人设表一致。"""
    from worldmock.footprints.checkpoint import restore_branch
    from worldmock.footprints.store import FootprintStore

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".worldmock-resume-", dir=destination.parent))
    staged_run = stage_root / "run"
    child: FootprintStore | None = None
    try:
        child = restore_branch(parent, checkpoint.checkpoint_id, staged_run, branch_metadata)
        _restore_agents(checkpoint, child, staged_run, inputs)
        manifest, state = dict(child.manifest), child.state
        child.close()
        child = None
        _transfer_status_shell(destination, staged_run)
        staged_run.rename(destination)
        return manifest, state
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


def _restore_agents(checkpoint: Any, child: FootprintStore, run_dir: Path, inputs: EnvironmentInputs) -> None:
    """@brief 从同一权威检查点恢复清单中的全部人物工作区；人物配置的续接开关须与清单一致，
    结构化卡片须与用冻结的人设表重新构造出的卡片相等。"""
    from worldmock.footprints.checkpoint import restore_directory
    from worldmock.life.continuation import continuation_enabled

    agent_ids = tuple(sorted(int(item) for item in child.manifest["agent_ids"]))
    for agent_id in agent_ids:
        reference = checkpoint.snapshots.get(f"agent:{agent_id}")
        if reference is None:
            raise IntegrityError("Checkpoint is missing an agent workspace snapshot")
        destination = run_dir / "as2" / "agents" / f"agent_{agent_id:04d}"
        restore_directory(reference, child.assets, destination)
        _validate_agent_workspace(destination, agent_id, continuation_enabled(child.manifest),
                                  inputs.persona, child.manifest["stage_model_identity"])


def _validate_agent_workspace(workspace: Path, agent_id: int, continuation: bool,
                              persona: PersonaConfig, model_identity: dict) -> None:
    """@brief 发布恢复分支前严格验证人物身份、配置、冻结的续接开关（与父清单相同）、结构化卡片和已用记忆。"""
    from worldmock import run_setup

    from agentsociety2.agent.memory import AgentMemoryStore

    agent_path = workspace / "AGENT.json"
    config_path = workspace / "config.json"
    for path in (agent_path, config_path):
        if not path.is_file() or path.is_symlink():
            raise IntegrityError("Restored agent identity artifact is missing or unsafe")
    try:
        agent = json.loads(agent_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("Restored agent identity artifact is invalid") from exc
    if (not isinstance(agent, dict) or not isinstance(config, dict)
            or agent.get("schema_version") != 1 or agent.get("agent_class") != "LifeAgent"
            or type(agent.get("agent_id")) is not int or agent["agent_id"] != agent_id):
        raise IntegrityError("Restored agent identity does not match the manifest")
    profile = agent.get("profile")
    if not isinstance(profile, dict) or profile.get("same_second_continuation") is not continuation:
        logger.error("Restored LifeAgent same_second_continuation differs from the parent manifest")
        raise IntegrityError("Restored agent same_second_continuation must match the parent manifest")
    if not bool(config.get("enable_memory", True)):
        raise IntegrityError("Restored LifeAgent must keep memory enabled")
    if profile.get("model_identity") != model_identity:
        logger.error("Restored LifeAgent model configuration differs from the parent manifest")
        raise IntegrityError("Restored LifeAgent model configuration must match the parent manifest")
    _validate_persona_card_struct(profile, persona)
    if (type(config.get("max_react_turns")) is not int
            or config["max_react_turns"] != model_identity["max_react_turns"]):
        logger.error("Restored LifeAgent ReAct budget differs from the frozen runtime budget")
        raise IntegrityError("Restored LifeAgent max_react_turns must match the frozen runtime budget")
    store = AgentMemoryStore(workspace, agent_id=agent_id)
    memory_paths = (store.memory_md_path, store.episodes_path, store.state_path)
    if agent.get("initialized_at") is not None or any(path.exists() for path in memory_paths):
        try:
            store.validate_existing()
        except (OSError, UnicodeError, ValueError) as exc:
            raise IntegrityError("Restored agent memory is invalid") from exc


def _validate_persona_card_struct(profile: Any, persona: PersonaConfig) -> None:
    """@brief 恢复时核对冻结的结构化卡片：必须与用冻结的人设表重新构造出的卡片逐值相等。

    卡片是人设表的直接构造（docs/design/06-agent.md「人物身份与画像」），所以缺失、损坏或被改过的卡片都表现为不相等。
    """
    from worldmock.profile.models import persona_card, persona_card_struct

    if isinstance(profile, dict) and "taste_weights" in profile:
        logger.error("Restored persona profile contains retired taste weights")
        raise IntegrityError("Restored persona profile contains retired taste weights")
    struct = profile.get("persona_card_struct") if isinstance(profile, dict) else None
    if struct != persona_card_struct(persona_card(persona)):
        logger.error("Restored persona card differs from persona.toml")
        raise IntegrityError("Restored persona card differs from persona.toml")


async def _execute(prepared: _PreparedRun) -> RunResult:
    """@brief 构造原生 Ray actor、runner 与 Society 并运行到结束。"""
    from agentsociety2.config.llm_dispatcher import init_dispatchers

    import ray

    owned_ray = not ray.is_initialized()
    await init_dispatchers()
    actor = None
    society = None
    failure: BaseException | None = None
    try:
        actor, society = await _build_society(prepared)
        await society.init()
        if prepared.intervention is not None:
            if prepared.intervention_sha256 is None:
                raise RuntimeError("Counterfactual intervention identity is unavailable")
            await society.apply_counterfactual(
                prepared.intervention,
                prepared.intervention_sha256,
            )
        await society.run()
        _ops_log(prepared.run_dir, prepared.inputs.config.timezone, society.current_time,
                 {"code": "RUN_ENDED", "detail": "Run finished", "run_id": prepared.run_id,
                  "steps": society.step_count})
        return RunResult(prepared.run_id, prepared.run_dir, society.current_time, society.step_count)
    except BaseException as error:
        failure = error
        _ops_crash_log(prepared, society, error)
        raise
    finally:
        errors = await _cleanup_runtime(society, actor, owned_ray)
        if errors:
            if failure is not None:
                raise BaseExceptionGroup("WorldMock run and cleanup failed", [failure, *errors])
            if len(errors) == 1:
                raise errors[0]
            raise BaseExceptionGroup("WorldMock cleanup failed", errors)


async def _cleanup_runtime(society: Any, actor: Any, owned_ray: bool) -> list[BaseException]:
    """@brief 保留清理故障并始终尝试释放 Society、actor 与自有 Ray。"""
    import ray

    errors = []
    if society is not None:
        try:
            await society.close()
        except BaseException as error:
            logger.exception("Society close failed")
            errors.append(error)
    if actor is not None and ray.is_initialized():
        try:
            ray.kill(actor, no_restart=True)
        except BaseException as error:
            logger.exception("Direct environment actor termination failed")
            errors.append(error)
    if owned_ray and ray.is_initialized():
        try:
            ray.shutdown()
        except BaseException as error:
            logger.exception("Owned Ray shutdown failed")
            errors.append(error)
    return errors


async def _build_society(prepared: _PreparedRun) -> tuple[Any, Any]:
    """@brief 连接 LifeEnvActor、LifeEnvProxy、ServiceProxy 与 AgentSociety；环境 actor 与代理取带只读查询转发的
    Life 子类（src/worldmock/life/read_query_runtime.py），装配 AS2 原类时回看任务到不了环境。"""
    from worldmock.life.read_query_runtime import LifeEnvProxy, life_env_actor_class

    from agentsociety2.agent.service_proxy import build_service_proxy
    from agentsociety2.config.llm_dispatcher import build_client_for_role
    from agentsociety2.env.direct_env_actor import DirectEnvActorSpec
    from agentsociety2.society import AgentSociety, AgentSocietyConfig

    _require_custom_modules(prepared.project_root)
    clients = {role: build_client_for_role(role) for role in ("coder", "default")}
    specs = _society_specs(prepared)
    spec = DirectEnvActorSpec(
        ("LifeEnv",), {"LifeEnv": prepared.env_kwargs}, str(prepared.run_dir),
        str(prepared.project_root), clients,
    )
    actor = life_env_actor_class().remote(spec)
    try:
        env = LifeEnvProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"])
        service = build_service_proxy(env, run_dir=prepared.run_dir, trace=False, replay=False)
        config = AgentSocietyConfig(
            prepared.run_id, specs, "LifeAgent", env, service,
            prepared.start_time, prepared.inputs.config.stop_time, prepared.run_dir,
            prepared.project_root, prepared.workspace_mode,
        )
        return actor, AgentSociety(config)
    except BaseException as error:
        _terminate_failed_actor(actor, error)
        raise


def _society_specs(prepared: _PreparedRun) -> tuple[dict[str, Any], ...]:
    """@brief 新建时由人设表构造唯一卡片，恢复读取已验证工作区；两条路径都不为人物卡片调用模型。"""
    from worldmock import run_setup
    from worldmock.opslog import OpsLogger

    if prepared.workspace_mode != "create":
        return _restored_specs(prepared)
    inputs = prepared.inputs
    specs, warnings = run_setup.creation_inputs(inputs, prepared.same_second_continuation)
    options = prepared.env_kwargs["create_options"]
    options.update(input_warnings=warnings, persona_habit_responses=habit_responses(specs))
    writer = OpsLogger(prepared.run_dir, inputs.config.timezone)
    try:
        for warning in warnings:
            writer.log(prepared.start_time, "runtime", "warn",
                       {"code": warning["code"], "detail": "Input warning frozen into the run manifest",
                        "agent_id": warning["agent_id"], "message": warning["message"], "details": warning["details"]})
    finally:
        writer.close()
    return specs


def _restored_specs(prepared: _PreparedRun) -> tuple[dict[str, Any], ...]:
    """@brief 人物与模型配置逐字沿用检查点已恢复的工作区。"""
    identity = prepared.inputs.persona.agent_id
    workspace = prepared.run_dir / "as2" / "agents" / f"agent_{identity:04d}"
    from worldmock.run_setup import stage_model_identity

    model = stage_model_identity(prepared.inputs.config.model, prepared.inputs.model_metadata,
                                 prepared.same_second_continuation)
    _validate_agent_workspace(workspace, identity, prepared.same_second_continuation,
                              prepared.inputs.persona, model)
    agent = json.loads((workspace / "AGENT.json").read_text(encoding="utf-8"))
    config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    return ({"id": identity, "profile": agent["profile"], "config": config},)


def habit_responses(specs: tuple[dict[str, Any], ...]) -> dict[int, list[str]]:
    """@brief 人设层习惯签名的反应文本（人物编号 → 文本，按卡片顺序）：取工作区输入里由人设表构造的结构化卡片，
    没有卡片的人物不出现；经 create_options 冻结进运行清单 persona_habit_responses
    （docs/design/09-io-formats.md「输入与模型身份」）。"""
    return {spec["id"]: [habit["response"] for habit in spec["profile"]["persona_card_struct"]["habits"]]
            for spec in specs if "persona_card_struct" in spec["profile"]}








def _terminate_failed_actor(actor: Any, cause: BaseException) -> None:
    """@brief 在 Society 尚未返回前释放已创建 actor，并保留双重故障。"""
    import ray

    try:
        ray.kill(actor, no_restart=True)
    except BaseException as cleanup_error:
        logger.exception("Direct environment actor rollback failed")
        raise BaseExceptionGroup(
            "Society construction and actor rollback failed",
            [cause, cleanup_error],
        ) from cause


class PlanDayClient(IndependentModelClient):
    """@brief Plan day 业务客户端，协议重试仍由业务调用方计数。"""

    def __init__(self, model_identity: dict[str, Any]):
        """@brief 使用冻结身份建立业务客户端。"""
        super().__init__(model_identity, "Plan day")


async def settle_pending_day_plans(engine: Any, client: Any | None = None) -> list[dict]:
    """@brief 零点计划后台任务（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：为缺当日计划的人物生成当日计划。

    LifeEnv 在零点推进之后、启动检查点之后与普通恢复的 init 中 await 它。不经 LifeAgent，
    不读写人物工作区与记忆运行时，不产生 observation、decision_started、memory_updated。按
    plan_submitted_dates 找出缺当日计划的人物（按编号），运行已到终点或无人缺计划时在构造客户端之前返回。
    输出不合法时附回英文错误重试，至多 3 次；重试耗尽或传输失败时先提交已有尝试的证据再抛出（运行故障）。
    """
    from worldmock.life import day_planning


    missing = day_planning.agents_missing_plan(engine.context.state, engine.context.config.timezone)
    if not missing:
        return []
    caller = client if client is not None else PlanDayClient(engine.context.store.manifest["stage_model_identity"])
    registered = []
    for agent_id in missing:
        registered.append(await _generate_day_plan(engine, caller, agent_id))
    return registered


async def _generate_day_plan(engine: Any, caller: Any, agent_id: int) -> dict:
    """@brief 为一个人物生成并登记当日计划：请求与响应逐次留证，合法输出经引擎系统登记入口与证据一次提交。"""
    from worldmock.life import day_planning
    from worldmock.life.day_plan_inputs import plan_inputs


    now, timezone_name = engine.context.state.current_time, engine.context.config.timezone
    inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, agent_id)
    sentences = [sentence for sentence, _ in inputs.experiences]
    original, kept = day_planning.fit_plan_day(
        inputs.payload, sentences, caller.model_name, engine.context.inputs.model_metadata)
    sources = [*(source for _, source in inputs.experiences[len(sentences) - kept:]), *inputs.sources]
    date = day_planning.plan_date(now, timezone_name)
    request, evidence, previous, attempt = original, [], None, 1
    while True:
        call_id = f"plan-day:{agent_id}:{date}:attempt:{attempt}"
        evidence.append(day_planning.request_draft(now, agent_id, (call_id, attempt, previous), request))
        link = (call_id, attempt, evidence[-1].event_id)
        raw = await _call_plan_day(engine, caller, request, (evidence, link, agent_id))
        try:
            parsed = day_planning.parse_plan_day(raw, now, timezone_name)
        except day_planning.PlanDayOutputError as error:
            evidence.append(day_planning.response_draft(now, agent_id, link, raw, error))
            if attempt > engine.context.config.model.day_plan_validation_max_retries:
                engine.context.store.append_evidence(evidence)
                raise ProtocolError("plan_day output validation retries exhausted") from error
            attempt, previous = attempt + 1, evidence[-1].event_id
            request = day_planning.prepare_plan_day_correction(original, raw, error)
            continue
        evidence.append(day_planning.response_draft(now, agent_id, link, raw, None))
        break
    plan = engine.register_day_plan(agent_id, parsed, sources, evidence)
    return {"agent_id": agent_id, "date": date, "goals": len(plan.goals)}


async def _call_plan_day(engine: Any, caller: Any, request: dict, context: tuple[list, tuple, int]) -> dict:
    """@brief 发出一次零点计划请求；传输或配置失败时先提交已有尝试的证据与失败证据再抛出。"""
    from worldmock.life import day_planning


    evidence, link, agent_id = context
    try:
        return await caller.call(request)
    except BaseException as error:
        logger.error("Plan day model request failed: %s", type(error).__name__)
        evidence.append(day_planning.failure_draft(engine.context.state.current_time, agent_id, link, error))
        engine.context.store.append_evidence(evidence)
        raise


def _require_custom_modules(project_root: Path) -> None:
    """@brief 经实际 AS2 scanner 核对 LifeAgent 与 LifeEnv 均被接受。"""
    from agentsociety2.registry import scan_and_register_custom_modules

    result = scan_and_register_custom_modules(project_root)
    errors = [*result.get("errors", []), *result.get("registration_errors", [])]
    agents = {item["type"] for item in result.get("agents", [])}
    environments = {item["type"] for item in result.get("envs", [])}
    if errors or "LifeAgent" not in agents or "LifeEnv" not in environments:
        raise RuntimeError("LifeAgent or LifeEnv custom module registration failed")
