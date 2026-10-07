"""@brief 编排原生 AgentSociety2、DeepSeek、LifeAgent 与 LifeEnv。"""

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

from pydantic import ValidationError

from worldmock.errors import IntegrityError, ProtocolError

if TYPE_CHECKING:
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.models import CounterfactualIntervention, RunIdentity
    from worldmock.life.scenario import RuntimeScenario
    from worldmock.life.semantics import ScenarioSemantics

logger = logging.getLogger(__name__)

#: 自述、工具纠错与结束共享的有限回合预算。

#: 工作验收阶段参数（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「两阶段决策」：独立工作验收保持
#: disabled/required）。
#: 运行身份覆盖的 AS2 导入包名；包目录由 importlib.util.find_spec 按实际导入解析
#: （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「运行身份」）。
#: 联系人回复的生成上限。
CONTACT_REPLY_MAX_TOKENS = 8000
#: 联系人回复的 system 提示（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」：独立调用、中文、不超过 200 汉字；
#: docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-联系人回复不虚构到场 ：联系人只通过消息来往）。中文模板逐字登记在
#: docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」。
_CONTACT_REPLY_PROMPT = (
    "你是消息收件人通讯录里的一名联系人，只通过消息和对方来往：你不在对方身边，也不会去找对方。"
    "根据你的联系人档案、这次来信与你们此前的往来，以你本人的口吻写一条自然、简短的中文回复。"
    "不说自己正在赶来、已经到了或几点到哪里，不约当面见；不说已经替对方做完了什么，也不揽下对方的活、不替对方分工；"
    "要做的事只写成打算，不替结果打包票。只输出回复正文，不超过 200 个汉字，不要解释、不要客套开场。")


@dataclass(frozen=True, slots=True)
class RunRequest:
    """@brief 新建正式运行所需的完整输入。

    :param project_root: 包含 custom 与 src 的 WorldMock 根目录。
    :param scenario_path: 已冻结生活场景路径。
    :param run_dir: 必须尚不存在的新运行目录。
    :param run_id: 调用方分配的稳定运行身份。
    :param source_group_id: 首次运行的来源组身份，必须等于 run_id。
    :param same_second_continuation: 同秒续接开关（缺省开），冻结进清单 stages.action 与人物配置；
        恢复与重放继承父清单的值（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「同秒续接」）。
    """

    project_root: Path
    scenario_path: Path
    run_dir: Path
    run_id: str
    source_group_id: str
    same_second_continuation: bool = True


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    """@brief 从共同检查点建立新分支所需的完整输入。

    :param project_root: 包含 custom 与 src 的 WorldMock 根目录。
    :param scenario_path: 与父运行身份一致的冻结场景路径。
    :param parent_run_dir: 只读父运行目录。
    :param checkpoint_id: SQLite 中已提交的共同检查点身份。
    :param run_dir: 必须尚不存在的新分支目录。
    """

    project_root: Path
    scenario_path: Path
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
    :param token_stats: 按模型汇总的 calls/input/output。
    """

    run_id: str
    run_dir: Path
    current_time: datetime
    step_count: int
    token_stats: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    """@brief 统一新建与恢复后的内部启动输入。"""

    project_root: Path
    scenario_path: Path
    run_dir: Path
    run_id: str
    scenario: RuntimeScenario
    start_time: datetime
    env_kwargs: dict[str, Any]
    workspace_mode: str
    intervention: CounterfactualIntervention | None = None
    intervention_sha256: str | None = None
    same_second_continuation: bool = True


async def run_world(request: RunRequest) -> RunResult:
    """@brief 新建并运行到场景排他结束时间。

    :param request: 新运行完整输入。
    :returns: 已确认结束时间与模型用量。
    """
    from worldmock import run_setup

    try:
        run_setup._configure_process(request.project_root)
        prepared = _prepare_new_run(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock run failed")
        raise


async def resume_world(request: ResumeRequest) -> RunResult:
    """@brief 从 SQLite 共同检查点创建分支并运行到结束。

    :param request: 恢复分支完整输入。
    :returns: 子分支已确认结束时间与模型用量。
    """
    from worldmock import run_setup

    try:
        run_setup._configure_process(request.project_root)
        prepared = _prepare_resume(request)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock resume failed")
        raise


async def replay_world(request: CounterfactualRequest) -> RunResult:
    """@brief 从共同检查点建立分支、应用世界条件干预并重新运行。

    :param request: 严格恢复输入和一次封闭干预。
    :returns: 子分支已确认结束时间与模型用量。
    """
    from worldmock import run_setup

    try:
        run_setup._configure_process(request.base.project_root)
        prepared = _prepare_resume(request.base, request.intervention)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock counterfactual replay failed")
        raise










def _prepare_new_run(request: RunRequest) -> _PreparedRun:
    """@brief 校验新目录并形成 LifeEnv 新建参数。"""
    from worldmock import run_setup
    from worldmock.life.world_setup import load_runtime_scenario

    run_dir = Path(request.run_dir).resolve()
    _require_run_shell(run_dir)
    if not request.run_id.strip() or request.source_group_id != request.run_id:
        raise ValueError("A fresh run requires matching run_id and source_group_id")
    scenario_path = Path(request.scenario_path).resolve()
    semantics, digest = load_runtime_scenario(scenario_path)
    _admit_scenario(semantics)
    scenario = semantics.scenario
    identity = _run_identity(request, scenario)
    kwargs = {
        "scenario_path": str(scenario_path),
        "run_dir": str(run_dir),
        "run_identity": identity.model_dump(mode="json"),
        "open_existing": False,
        "create_options": {"same_second_continuation": request.same_second_continuation},
    }
    _ops_log(run_dir, scenario.timezone, scenario.start_at, "RUN_STARTED", "Run started",
             run_id=request.run_id, code_sha256=identity.code_sha256, scenario_sha256=digest,
             stage_model_identity=run_setup.stage_model_identity(request.same_second_continuation))
    return _PreparedRun(
        Path(request.project_root).resolve(), scenario_path, run_dir, request.run_id,
        scenario, scenario.start_at, kwargs, "create",
        same_second_continuation=request.same_second_continuation,
    )


def _run_identity(request: RunRequest, scenario: RuntimeScenario) -> RunIdentity:
    """@brief 形成与场景、当前 LifeAgent 合同和代码（含锁定的 AS2）绑定的运行身份，另记检出的 Git 状态。"""
    from worldmock import run_setup
    from worldmock.life.models import CounterfactualIntervention, RunIdentity

    root = Path(request.project_root)
    git_commit, git_dirty = run_setup._git_state(root)
    return RunIdentity(
        run_id=request.run_id,
        source_group_id=request.source_group_id,
        agent_ids=tuple(sorted(item.agent_id for item in scenario.agents)),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        model_identity=run_setup._model_identity(),
        code_sha256=run_setup.runtime_code_sha256(root),
        git_commit=git_commit,
        git_dirty=git_dirty,
    )


def _admit_scenario(semantics: ScenarioSemantics) -> None:
    """@brief 使用唯一环境登记器验证当前场景引用，不读取退役的品类引用模型。"""
    from worldmock.catalog.admission import validate_scenario_against_catalog

    validate_scenario_against_catalog(semantics.scenario, semantics.catalog)


def _prepare_resume(
    request: ResumeRequest,
    intervention: CounterfactualIntervention | None = None,
) -> _PreparedRun:
    """@brief 校验父身份并原子恢复普通或反事实子分支。"""
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.continuation import continuation_enabled
    from worldmock.life.run_identity import require_environment_identity
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.life.models import WorldState

    destination = Path(request.run_dir).resolve()
    _require_run_shell(destination)
    scenario_path = Path(request.scenario_path).resolve()
    semantics, scenario_digest = load_runtime_scenario(scenario_path)
    _admit_scenario(semantics)
    scenario = semantics.scenario
    digest = _intervention_sha256(intervention) if intervention is not None else None
    metadata = (
        {"branch_kind": "counterfactual", "intervention_sha256": digest}
        if digest is not None
        else None
    )
    parent = FootprintStore(Path(request.parent_run_dir))
    try:
        if parent.manifest.get("scenario_sha256") != scenario_digest:
            raise IntegrityError("Checkpoint scenario identity differs from the requested scenario")
        checkpoint = parent.get_checkpoint(request.checkpoint_id)
        WorldState.model_validate(checkpoint.state)
        _require_runtime_identity(parent.manifest, Path(request.project_root))
        require_environment_identity(parent.manifest, semantics)
        manifest, state = _restore_branch(parent, checkpoint, destination, metadata, scenario)
    finally:
        parent.close()
    continuation = continuation_enabled(manifest)
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(scenario.timezone))
    kwargs = {"scenario_path": str(scenario_path), "run_dir": str(destination), "open_existing": True}
    _ops_log(destination, scenario.timezone, start, "RUN_RESUMED", "Run resumed from checkpoint",
             run_id=manifest["run_id"], code_sha256=manifest.get("code_sha256"),
             scenario_sha256=manifest.get("scenario_sha256"), checkpoint_id=request.checkpoint_id)
    return _PreparedRun(
        Path(request.project_root).resolve(), scenario_path, destination, manifest["run_id"],
        scenario, start, kwargs, "restored", intervention, digest, continuation,
    )


def _intervention_sha256(intervention: CounterfactualIntervention) -> str:
    """@brief 计算与 LifeEngine 相同的规范反事实干预身份。"""
    from worldmock.footprints.models import sha256_json

    payload = {
        "tool_name": "counterfactual_intervention",
        "request": intervention.model_dump(mode="json"),
    }
    return sha256_json(payload)


def _require_runtime_identity(manifest: dict[str, Any], project_root: Path) -> None:
    """@brief 拒绝用不同代码（含实际导入的 AS2）、端点、模型、提示或工具恢复父运行。

    清单的 git_commit 与 git_dirty 只记录，不参与比对。阶段身份先经五个入口共用的清单校验函数拒绝退役阶段与旧键，
    再做等值比对；同秒续接开关继承父清单的值（按父清单的开关构造期望身份），恢复与重放不能覆盖。
    """
    from worldmock import run_setup
    from worldmock.life.continuation import continuation_enabled


    identity = run_setup.validate_manifest_stage_identity(manifest)
    expected = run_setup._model_identity().model_dump(mode="json")
    if manifest.get("model_identity") != expected:
        raise IntegrityError("Checkpoint model identity differs from the current LifeAgent contract")
    if identity != run_setup.stage_model_identity(continuation_enabled(manifest)):
        raise IntegrityError("Checkpoint decision stage identity differs from the current LifeAgent contract")
    if manifest.get("code_sha256") != run_setup.runtime_code_sha256(project_root):
        raise IntegrityError("Checkpoint code identity differs from the current runtime")














def _ops_log(run_dir: Path, timezone_name: str, sim_time: int | datetime | None,
             code: str, detail: str, level: str = "info", **keys: Any) -> None:
    """@brief 运行进程侧写一条 runtime 操作日志，随写随关不驻留句柄。"""
    from worldmock.opslog import OpsLogger

    writer = OpsLogger(run_dir, timezone_name)
    try:
        writer.log(sim_time, "runtime", level, code, detail, **keys)
    finally:
        writer.close()


def _ops_crash_log(prepared: _PreparedRun, society: Any, error: BaseException) -> None:
    """@brief 崩溃路径操作日志；自身任何故障只记日志，不得掩盖原始异常。"""
    try:
        sim_time = society.current_time if society is not None else prepared.start_time
        _ops_log(prepared.run_dir, prepared.scenario.timezone, sim_time, "RUN_CRASHED",
                 f"Run crashed: {type(error).__name__}: {error}",
                 level="error", run_id=prepared.run_id)
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
    branch_metadata: dict[str, str] | None = None,
    scenario: RuntimeScenario | None = None,
) -> tuple[dict, dict]:
    """@brief 在外层临时目录恢复数据库和人物，完整后才发布子目录；给出场景时另核对结构化卡片与场景作息一致。"""
    from worldmock.footprints.checkpoint import restore_branch
    from worldmock.footprints.store import FootprintStore

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".worldmock-resume-", dir=destination.parent))
    staged_run = stage_root / "run"
    child: FootprintStore | None = None
    try:
        child = restore_branch(parent, checkpoint.checkpoint_id, staged_run, branch_metadata)
        _restore_agents(checkpoint, child, staged_run, scenario)
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


def _restore_agents(checkpoint: Any, child: FootprintStore, run_dir: Path,
                    scenario: RuntimeScenario | None = None) -> None:
    """@brief 从同一权威检查点恢复清单中的全部人物工作区；人物配置的续接开关须与清单一致，
    给出场景时结构化卡片的作息须与场景显式声明的作息一致。"""
    from worldmock.footprints.checkpoint import restore_directory
    from worldmock.life.continuation import continuation_enabled

    agent_ids = tuple(sorted(int(item) for item in child.manifest["agent_ids"]))
    configs = {item.agent_id: item for item in scenario.agents} if scenario is not None else {}
    for agent_id in agent_ids:
        reference = checkpoint.snapshots.get(f"agent:{agent_id}")
        if reference is None:
            raise IntegrityError("Checkpoint is missing an agent workspace snapshot")
        destination = run_dir / "as2" / "agents" / f"agent_{agent_id:04d}"
        restore_directory(reference, child.assets, destination)
        _validate_agent_workspace(destination, agent_id, continuation_enabled(child.manifest),
                                  configs.get(agent_id))


def _validate_agent_workspace(workspace: Path, agent_id: int, continuation: bool,
                              agent_config: Any = None) -> None:
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
    _validate_persona_card_struct(agent.get("profile"), agent_config)
    if type(config.get("max_react_turns")) is not int or config["max_react_turns"] != run_setup.MAX_REACT_TURNS:
        logger.error("Restored LifeAgent ReAct budget differs from the frozen runtime budget")
        raise IntegrityError("Restored LifeAgent max_react_turns must match the frozen runtime budget")
    store = AgentMemoryStore(workspace, agent_id=agent_id)
    memory_paths = (store.memory_md_path, store.episodes_path, store.state_path)
    if agent.get("initialized_at") is not None or any(path.exists() for path in memory_paths):
        try:
            store.validate_existing()
        except (OSError, UnicodeError, ValueError) as exc:
            raise IntegrityError("Restored agent memory is invalid") from exc


def _validate_persona_card_struct(profile: Any, agent_config: Any = None) -> None:
    """@brief 恢复时校验人物 profile 中的结构化卡片（persona_card_struct；没有卡片的人物不带该键）。

    新建运行保存的是经场景作息覆盖后的卡片（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值 ），覆盖是不动点：给出场景人物声明时，
    对恢复的卡片再覆盖一次必须不改任何值，否则说明卡片作息与场景显式声明的作息不一致。
    """
    from worldmock.profile.injection import apply_scenario_rhythm
    from worldmock.profile.models import PersonaCardStruct

    if isinstance(profile, dict) and "taste_weights" in profile:
        logger.error("Restored persona profile contains retired taste weights")
        raise IntegrityError("Restored persona profile contains retired taste weights")
    struct = profile.get("persona_card_struct") if isinstance(profile, dict) else None
    if struct is None:
        return
    try:
        card = PersonaCardStruct.model_validate(struct)
    except ValidationError as exc:
        logger.error("Restored persona card structure is invalid")
        raise IntegrityError("Restored persona card structure is invalid") from exc
    if agent_config is not None and apply_scenario_rhythm(card, agent_config).card != card:
        logger.error("Restored persona card rhythm disagrees with the scenario-declared rhythm")
        raise IntegrityError("Restored persona card rhythm disagrees with the scenario-declared rhythm")


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
        actor, society = _build_society(prepared)
        await society.init()
        if prepared.intervention is not None:
            if prepared.intervention_sha256 is None:
                raise RuntimeError("Counterfactual intervention identity is unavailable")
            await society.apply_counterfactual(
                prepared.intervention,
                prepared.intervention_sha256,
            )
        await society.run()
        _ops_log(prepared.run_dir, prepared.scenario.timezone, society.current_time,
                 "RUN_ENDED", "Run finished", run_id=prepared.run_id, steps=society.step_count)
        return RunResult(
            prepared.run_id, prepared.run_dir, society.current_time,
            society.step_count, society.token_stats,
        )
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


def _build_society(prepared: _PreparedRun) -> tuple[Any, Any]:
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
            prepared.start_time, prepared.scenario.end_at, prepared.run_dir,
            prepared.project_root, prepared.workspace_mode,
        )
        return actor, AgentSociety(config)
    except BaseException as error:
        _terminate_failed_actor(actor, error)
        raise


def _society_specs(prepared: _PreparedRun) -> tuple[dict[str, Any], ...]:
    """@brief 人物工作区输入。新建运行：编译卡片、经场景作息覆盖渲染，并计算运行输入警告——警告经
    create_options 冻结进运行清单 input_warnings，另逐条写一条 ops 警告，永不使创建失败
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 ）。恢复运行不重新编译，人物 profile 随检查点工作区恢复、警告随父清单继承。
    """
    from worldmock import run_setup
    from worldmock.opslog import OpsLogger

    if prepared.workspace_mode != "create":
        return run_setup._agent_specs(prepared.scenario, persona_compiler=_no_persona_compilation,
                            same_second_continuation=prepared.same_second_continuation)
    compiled = {agent.agent_id: run_setup._compile_persona(agent)
                for agent in sorted(prepared.scenario.agents, key=lambda item: item.agent_id)}
    specs, warnings = run_setup.creation_inputs(prepared.scenario_path, prepared.scenario, compiled,
                                      prepared.same_second_continuation)
    options = prepared.env_kwargs["create_options"]
    options["input_warnings"] = warnings
    options["persona_habit_responses"] = habit_responses(specs)
    writer = OpsLogger(prepared.run_dir, prepared.scenario.timezone)
    try:
        for warning in warnings:
            writer.log(prepared.start_time, "runtime", "warn", warning["code"],
                       "Input warning frozen into the run manifest", agent_id=warning["agent_id"],
                       message=warning["message"], details=warning["details"])
    finally:
        writer.close()
    return specs


def habit_responses(specs: tuple[dict[str, Any], ...]) -> dict[int, list[str]]:
    """@brief 人设层习惯签名的反应文本（人物编号 → 文本，按卡片顺序）：取工作区输入里经场景作息覆盖后的结构化卡片，
    没有卡片的人物不出现；经 create_options 冻结进运行清单 persona_habit_responses，引擎排序候选时据此给相关度
    （docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。"""
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


class ContactReplyClient(IndependentModelClient):
    """@brief Contact reply 业务客户端，协议重试仍由业务调用方计数。"""

    def __init__(self, model_identity: dict[str, Any]):
        """@brief 使用冻结身份建立业务客户端。"""
        super().__init__(model_identity, "Contact reply")


def prepare_contact_reply(request: Any, contact: dict[str, Any] | None,
                          model_name: str) -> dict[str, Any]:
    """@brief 形成联系人回复的独立请求（thinking=disabled、无工具、无传输重试）。

    载荷带来信与它的事实（发件人姓名、场景本地时刻、presence 固定 remote、联系人的收货地名），以及双方往来与对方
    实际收到的物品（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-联系人回复不虚构到场 ）。
    """
    payload = {"contact": contact if contact is not None
               else {"contact_id": request.contact_id},
               "sender_name": request.sender_name,
               "now": request.now,
               "presence": request.presence,
               "receive_location": request.receive_location,
               "incoming_message": request.incoming_text,
               "history": [item.model_dump(mode="json") for item in request.history],
               "received": [item.model_dump(mode="json") for item in request.received],
               "reply_char_limit": request.max_chars}
    messages = [
        {"role": "system", "content": _CONTACT_REPLY_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
    return {"model": model_name, "messages": messages, "stream": False,
            "max_tokens": CONTACT_REPLY_MAX_TOKENS, "max_retries": 0,
            "extra_body": {"thinking": {"type": "disabled"}}}


def parse_contact_reply(raw: dict[str, Any]) -> str:
    """@brief 从原始响应提取唯一回复文本（结构异常按运行故障传播）。"""
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProtocolError("Contact reply response must contain exactly one choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProtocolError("Contact reply response has no reply text")
    return content.strip()


async def settle_pending_model_replies(engine: Any, client: Any | None = None) -> list[dict]:
    """@brief 执行引擎登记的联系人 model
    回复并登记预排送达（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。

    每轮结算后调用：从足迹库取未结算的 model_npc_reply_pending 请求，经
    独立 DeepSeek 调用生成不超过 200 汉字的回复，请求/原始响应/用量保存为
    事件证据，回复经 social.finalize_model_reply 登记为 CONTACT_REPLY_DELAY_SECONDS
    后到期的预排送达，到期只进入收件箱（不改资源或任务）。回复经后验守卫：命中越界说法时在本次结算内
    重试一次，仍命中则用固定兜底句（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-联系人回复不虚构到场 ）。传输与结构异常按运行故障传播
    （与工作验收同边界）；同一消息最多回复一次（幂等键 reply_to_message_id），重试也只写一条回复事件。
    """
    from worldmock.life import social as social_domain


    pending = _pending_reply_requests(engine)
    if not pending:
        return []
    caller = client if client is not None else ContactReplyClient(
        engine.context.store.manifest["model_identity"])
    settled = []
    for event, request in pending:
        model_request = prepare_contact_reply(
            request, _contact_profile(engine, request), caller.model_name)
        text, attempts = await _guarded_contact_reply(caller, model_request)
        reply = social_domain.finalize_model_reply(
            request, text, f"reply-{request.reply_to_message_id}", engine.context.state.current_time)
        settled.append(_commit_contact_reply(engine, event, attempts, reply))
    return settled


async def _guarded_contact_reply(caller: Any, model_request: dict[str, Any]) -> tuple[str, list[dict]]:
    """@brief 守卫下的一条回复：命中越界说法时以附回纠正的请求重试一次，仍命中则取固定兜底句。

    :returns: 回复正文与每次尝试的记录（请求、原始响应、回复正文与命中的类别码）。
    """
    from worldmock.life.reply_guard import FALLBACK_REPLY, REPLY_ATTEMPT_LIMIT, guard_matches, retry_request

    attempts: list[dict] = []
    request = model_request
    while len(attempts) < REPLY_ATTEMPT_LIMIT:
        raw = await caller.call(request)
        text = parse_contact_reply(raw)
        matched = guard_matches(text)
        attempts.append({"request": request, "response": raw, "text": text, "matched": matched})
        if not matched:
            return text, attempts
        request = retry_request(model_request, text, matched)
    return FALLBACK_REPLY, attempts


def _pending_reply_requests(engine: Any) -> list[tuple[Any, Any]]:
    """@brief 取尚未回复的 pending 请求（事件序升序，幂等去重）。"""
    from worldmock.life.social import ModelReplyRequest


    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    replied = {event.data.get("reply_to_message_id") for event in events
               if event.event_type == "environment_event"
               and event.data.get("kind") == "model_npc_reply"}
    pending = []
    for event in events:
        if (event.event_type != "environment_event"
                or event.data.get("kind") != "model_npc_reply_pending"):
            continue
        request = ModelReplyRequest.model_validate(event.data["request"])
        if request.reply_to_message_id not in replied:
            pending.append((event, request))
    return pending


def _contact_profile(engine: Any, request: Any) -> dict[str, Any] | None:
    """@brief 从场景声明查找联系人公开档案（找不到返回 None）。"""
    for agent in engine.context.semantics.scenario.agents:
        for contact in agent.contacts:
            if contact.contact_id == request.contact_id:
                return {"contact_id": contact.contact_id,
                        "salutation": contact.salutation,
                        "relation": contact.relation,
                        "closeness": contact.closeness,
                        "channels": list(contact.channels)}
    return None


def _commit_contact_reply(engine: Any, pending_event: Any, attempts: list[dict], reply: Any) -> dict:
    """@brief 一次事务提交回复证据并登记预排送达（版本 +1，资源零变动）。

    回复生成时即留 model_npc_reply 证据：request、response、usage 取最后一次尝试，attempts 保存每次尝试，
    guard_applied 表示两次都命中而改用了兜底句；收件箱条目在 CONTACT_REPLY_DELAY_SECONDS 后由预排事件
    message_arrival 送达，收件人届时以 message_arrival 唤醒、睡眠中过滤（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）。
    """
    from worldmock.footprints.models import EventDraft, canonical_json
    from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS
    from worldmock.life.models import validated_payload
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


    recipient = int(reply.message.recipient_id.split(":", 1)[1])
    staged = engine.context.state.model_copy(deep=True)
    sequence = max((item.sequence for item in staged.pending_events), default=0) + 1
    staged.pending_events.append(ScheduledEvent(
        scheduled_id=f"contact-reply:{reply.message.message_id}", kind=ScheduledKind.MESSAGE_ARRIVAL,
        due_at=staged.current_time + CONTACT_REPLY_DELAY_SECONDS, sequence=sequence, agent_id=recipient,
        data={"message": reply.message.model_dump(mode="json")}))
    staged.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    staged.version = engine.context.state.version + 1
    last = attempts[-1]
    drafts = [
        EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                   agent_id=recipient, source_kind="model", visibility="system",
                   caused_by=[pending_event.event_id],
                   data={"kind": "model_npc_reply", "source_kind": reply.source_kind,
                         "reply_to_message_id": reply.message.message_id.removeprefix("reply-"),
                         "request": last["request"], "response": last["response"],
                         "usage": last["response"].get("usage"),
                         "reply": reply.message.model_dump(mode="json"),
                         "attempts": attempts, "guard_applied": bool(last["matched"])}),
    ]
    engine.context.store.commit(validated_payload(staged), drafts)
    engine.context.state = staged
    return {"reply_to_message_id": reply.message.message_id.removeprefix("reply-"),
            "reply_message_id": reply.message.message_id,
            "source_kind": reply.source_kind}


class PlanDayClient(IndependentModelClient):
    """@brief Plan day 业务客户端，协议重试仍由业务调用方计数。"""

    def __init__(self, model_identity: dict[str, Any]):
        """@brief 使用冻结身份建立业务客户端。"""
        super().__init__(model_identity, "Plan day")


async def settle_pending_day_plans(engine: Any, client: Any | None = None) -> list[dict]:
    """@brief 零点计划后台任务（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「执行位置」）：为缺当日计划的人物生成当日计划。

    与联系人回复并列：LifeEnv 在零点推进之后、启动检查点之后与普通恢复的 init 中 await 它。不经 LifeAgent，
    不读写人物工作区与记忆运行时，不产生 observation、decision_started、memory_updated。按
    plan_submitted_dates 找出缺当日计划的人物（按编号），运行已到终点或无人缺计划时在构造客户端之前返回。
    输出不合法时附回英文错误重试，至多 3 次；重试耗尽或传输失败时先提交已有尝试的证据再抛出（运行故障）。
    """
    from worldmock.life import day_planning


    missing = day_planning.agents_missing_plan(engine.context.state, engine.context.scenario.timezone)
    if not missing:
        return []
    caller = client if client is not None else PlanDayClient(engine.context.store.manifest["model_identity"])
    registered = []
    for agent_id in missing:
        registered.append(await _generate_day_plan(engine, caller, agent_id))
    return registered


async def _generate_day_plan(engine: Any, caller: Any, agent_id: int) -> dict:
    """@brief 为一个人物生成并登记当日计划：请求与响应逐次留证，合法输出经引擎系统登记入口与证据一次提交。"""
    from worldmock.life import day_planning
    from worldmock.life.day_plan_inputs import plan_inputs
    from worldmock.life.models import validated_payload


    now, timezone_name = engine.context.state.current_time, engine.context.scenario.timezone
    inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, agent_id)
    sentences = [sentence for sentence, _ in inputs.experiences]
    original, kept = day_planning.fit_plan_day(inputs.payload, sentences, caller.model_name)
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
            if attempt > day_planning.PLAN_DAY_VALIDATION_RETRIES:
                engine.context.store.commit(validated_payload(engine.context.state), evidence)
                raise ProtocolError("plan_day output remained invalid after three retries") from error
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
    from worldmock.life.models import validated_payload


    evidence, link, agent_id = context
    try:
        return await caller.call(request)
    except BaseException as error:
        logger.error("Plan day model request failed: %s", type(error).__name__)
        evidence.append(day_planning.failure_draft(engine.context.state.current_time, agent_id, link, error))
        engine.context.store.commit(validated_payload(engine.context.state), evidence)
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
















def _no_persona_compilation(agent: Any) -> None:
    """@brief 恢复运行不重新编译：人物卡片随检查点工作区原样恢复。"""
    return None
