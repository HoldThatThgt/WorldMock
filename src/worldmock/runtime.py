"""@brief 编排原生 AgentSociety2、DeepSeek、LifeAgent 与 LifeEnv。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import restore_directory
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import (
    CounterfactualIntervention,
    ModelIdentity,
    RunIdentity,
)
from worldmock.life.scenario import LifeScenario, load_scenario

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL = "https://api.deepseek.com/beta"
_DEEPSEEK_MODEL = "deepseek-flash"


@dataclass(frozen=True, slots=True)
class RunRequest:
    """@brief 新建正式运行所需的完整输入。

    :param project_root: 包含 custom 与 src 的 WorldMock 根目录。
    :param scenario_path: 已冻结生活场景路径。
    :param run_dir: 必须尚不存在的新运行目录。
    :param run_id: 调用方分配的稳定运行身份。
    :param source_group_id: 首次运行的来源组身份，必须等于 run_id。
    """

    project_root: Path
    scenario_path: Path
    run_dir: Path
    run_id: str
    source_group_id: str


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
    scenario: LifeScenario
    start_time: datetime
    env_kwargs: dict[str, Any]
    workspace_mode: str
    intervention: CounterfactualIntervention | None = None
    intervention_sha256: str | None = None


async def run_world(request: RunRequest) -> RunResult:
    """@brief 新建并运行到场景排他结束时间。

    :param request: 新运行完整输入。
    :returns: 已确认结束时间与模型用量。
    """
    try:
        _configure_process(request.project_root)
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
    try:
        _configure_process(request.project_root)
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
    try:
        _configure_process(request.base.project_root)
        prepared = _prepare_resume(request.base, request.intervention)
        return await _execute(prepared)
    except Exception:
        logger.exception("WorldMock counterfactual replay failed")
        raise


def _configure_process(project_root: Path) -> None:
    """@brief 从唯一私密文件配置显式 DeepSeek 角色和 worker 导入路径。"""
    root = Path(project_root).resolve()
    if not (root / "custom" / "agents" / "life_agent.py").is_file():
        raise FileNotFoundError("WorldMock project root is incomplete")
    secret = _read_deepseek_key()
    variables = {
        "WORKSPACE_PATH": str(root),
        "AGENTSOCIETY_LLM_API_KEY": secret,
        "AGENTSOCIETY_LLM_API_BASE": _DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_LLM_MODEL": _DEEPSEEK_MODEL,
        "AGENTSOCIETY_CODER_LLM_API_KEY": secret,
        "AGENTSOCIETY_CODER_LLM_API_BASE": _DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_CODER_LLM_MODEL": _DEEPSEEK_MODEL,
        "AGENTSOCIETY_EMBEDDING_MODEL": "",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "MEM0_TELEMETRY": "False",
        "ANONYMIZED_TELEMETRY": "False",
    }
    os.environ.update(variables)
    _prepend_pythonpath(root)
    _require_loaded_config()


def _read_deepseek_key() -> str:
    """@brief 只读取固定私密配置中的 DeepSeek 密钥，不输出其内容。"""
    path = Path.home() / ".config" / "worldmock" / "deepseek.env"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("DeepSeek credential file is missing or unsafe")
    values = dotenv_values(path, interpolate=False)
    unknown = set(values) - {"DEEPSEEK_API_KEY"}
    key = values.get("DEEPSEEK_API_KEY")
    if unknown or not isinstance(key, str) or not key.strip():
        raise IntegrityError("DeepSeek credential file has an invalid schema")
    return key.strip()


def _prepend_pythonpath(root: Path) -> None:
    """@brief 让 Ray worker 从当前精确 checkout 导入 custom 与 worldmock。"""
    current = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    required = [str(root), str(root / "src")]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([*required, *current]))


def _require_loaded_config() -> None:
    """@brief 拒绝进程早先导入的不同 AS2 模型配置。"""
    from agentsociety2.config.config import Config

    expected = (_DEEPSEEK_BASE_URL, _DEEPSEEK_MODEL)
    default = (Config.LLM_API_BASE, Config.LLM_MODEL)
    coder = (Config.CODER_LLM_API_BASE, Config.CODER_LLM_MODEL)
    if default != expected or coder != expected or Config.EMBEDDING_MODEL:
        raise RuntimeError("AgentSociety2 was imported before the required model configuration")


def _prepare_new_run(request: RunRequest) -> _PreparedRun:
    """@brief 校验新目录并形成 LifeEnv 新建参数。"""
    run_dir = Path(request.run_dir).resolve()
    _require_run_shell(run_dir)
    if not request.run_id.strip() or request.source_group_id != request.run_id:
        raise ValueError("A fresh run requires matching run_id and source_group_id")
    scenario_path = Path(request.scenario_path).resolve()
    scenario, _ = load_scenario(scenario_path)
    identity = _run_identity(request, scenario)
    kwargs = {
        "scenario_path": str(scenario_path),
        "run_dir": str(run_dir),
        "run_identity": identity.model_dump(mode="json"),
        "open_existing": False,
    }
    return _PreparedRun(
        Path(request.project_root).resolve(), scenario_path, run_dir, request.run_id,
        scenario, scenario.start_at, kwargs, "create",
    )


def _run_identity(request: RunRequest, scenario: LifeScenario) -> RunIdentity:
    """@brief 形成与场景和当前 LifeAgent 合同绑定的运行身份。"""
    return RunIdentity(
        run_id=request.run_id,
        source_group_id=request.source_group_id,
        agent_ids=tuple(sorted(item.agent_id for item in scenario.agents)),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        model_identity=_model_identity(),
        code_sha256=runtime_code_sha256(Path(request.project_root)),
    )


def _model_identity() -> ModelIdentity:
    """@brief 形成不含凭据的显式 DeepSeek、提示和工具身份。"""
    from custom.agents.life_agent import life_prompt_sha256, life_tool_schema_sha256

    return ModelIdentity(
        provider="deepseek",
        base_url=_DEEPSEEK_BASE_URL,
        requested_model=_DEEPSEEK_MODEL,
        thinking="disabled",
        tool_choice="required",
        max_retries=0,
        prompt_sha256=life_prompt_sha256(),
        tool_schema_sha256=life_tool_schema_sha256(),
    )


def _prepare_resume(
    request: ResumeRequest,
    intervention: CounterfactualIntervention | None = None,
) -> _PreparedRun:
    """@brief 校验父身份并原子恢复普通或反事实子分支。"""
    destination = Path(request.run_dir).resolve()
    _require_run_shell(destination)
    scenario_path = Path(request.scenario_path).resolve()
    scenario, _ = load_scenario(scenario_path)
    digest = _intervention_sha256(intervention) if intervention is not None else None
    metadata = (
        {"branch_kind": "counterfactual", "intervention_sha256": digest}
        if digest is not None
        else None
    )
    parent = FootprintStore(Path(request.parent_run_dir))
    try:
        checkpoint = parent.get_checkpoint(request.checkpoint_id)
        _require_runtime_identity(parent.manifest, Path(request.project_root))
        manifest, state = _restore_branch(parent, checkpoint, destination, metadata)
    finally:
        parent.close()
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(scenario.timezone))
    kwargs = {"scenario_path": str(scenario_path), "run_dir": str(destination), "open_existing": True}
    return _PreparedRun(
        Path(request.project_root).resolve(), scenario_path, destination, manifest["run_id"],
        scenario, start, kwargs, "restored", intervention, digest,
    )


def _intervention_sha256(intervention: CounterfactualIntervention) -> str:
    """@brief 计算与 LifeEngine 相同的规范反事实干预身份。"""
    payload = {
        "tool_name": "counterfactual_intervention",
        "request": intervention.model_dump(mode="json"),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _require_runtime_identity(manifest: dict[str, Any], project_root: Path) -> None:
    """@brief 拒绝用不同代码、端点、模型、提示或工具恢复父运行。"""
    expected = _model_identity().model_dump(mode="json")
    if manifest.get("model_identity") != expected:
        raise IntegrityError("Checkpoint model identity differs from the current LifeAgent contract")
    if manifest.get("code_sha256") != runtime_code_sha256(project_root):
        raise IntegrityError("Checkpoint code identity differs from the current runtime")


def runtime_code_sha256(project_root: Path) -> str:
    """@brief 哈希全部生产 Python 源码与根依赖锁定文件。"""
    root = Path(project_root).resolve()
    package = root / "vendor" / "agentsociety2" / "packages" / "agentsociety2" / "agentsociety2"
    directories = (root / "src", root / "custom", package)
    files = [path for directory in directories for path in directory.rglob("*.py")]
    files.extend((root / "pyproject.toml", root / "uv.lock"))
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            raise IntegrityError("Runtime identity source is missing or unsafe")
        label = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(label).to_bytes(8, "big"))
        digest.update(label)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


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
) -> tuple[dict, dict]:
    """@brief 在外层临时目录恢复数据库和人物，完整后才发布子目录。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".worldmock-resume-", dir=destination.parent))
    staged_run = stage_root / "run"
    child: FootprintStore | None = None
    try:
        child = parent.restore(checkpoint.checkpoint_id, staged_run, branch_metadata)
        _restore_agents(checkpoint, child, staged_run)
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


def _restore_agents(checkpoint: Any, child: FootprintStore, run_dir: Path) -> None:
    """@brief 从同一权威检查点恢复清单中的全部人物工作区。"""
    agent_ids = tuple(sorted(int(item) for item in child.manifest["agent_ids"]))
    for agent_id in agent_ids:
        reference = checkpoint.snapshots.get(f"agent:{agent_id}")
        if reference is None:
            raise IntegrityError("Checkpoint is missing an agent workspace snapshot")
        destination = run_dir / "as2" / "agents" / f"agent_{agent_id:04d}"
        restore_directory(reference, child.assets, destination)
        _validate_agent_workspace(destination, agent_id)


def _validate_agent_workspace(workspace: Path, agent_id: int) -> None:
    """@brief 发布恢复分支前严格验证人物身份、配置和已用记忆。"""
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
    if not bool(config.get("enable_memory", True)):
        raise IntegrityError("Restored LifeAgent must keep memory enabled")
    store = AgentMemoryStore(workspace, agent_id=agent_id)
    memory_paths = (store.memory_md_path, store.episodes_path, store.state_path)
    if agent.get("initialized_at") is not None or any(path.exists() for path in memory_paths):
        try:
            store.validate_existing()
        except (OSError, UnicodeError, ValueError) as exc:
            raise IntegrityError("Restored agent memory is invalid") from exc


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
        return RunResult(
            prepared.run_id, prepared.run_dir, society.current_time,
            society.step_count, society.token_stats,
        )
    except BaseException as error:
        failure = error
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
    """@brief 连接 DirectEnvActor、EnvRouterProxy、ServiceProxy 与 AgentSociety。"""
    from agentsociety2.agent.service_proxy import build_service_proxy
    from agentsociety2.config.llm_dispatcher import build_client_for_role
    from agentsociety2.env.direct_env_actor import DirectEnvActorSpec, get_direct_env_actor_class
    from agentsociety2.env.env_router_proxy import EnvRouterProxy
    from agentsociety2.society import AgentSociety, AgentSocietyConfig

    _require_custom_modules(prepared.project_root)
    clients = {role: build_client_for_role(role) for role in ("coder", "default")}
    spec = DirectEnvActorSpec(
        ("LifeEnv",), {"LifeEnv": prepared.env_kwargs}, str(prepared.run_dir),
        str(prepared.project_root), clients,
    )
    actor = get_direct_env_actor_class().remote(spec)
    try:
        env = EnvRouterProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"])
        service = build_service_proxy(env, run_dir=prepared.run_dir, trace=False, replay=False)
        config = AgentSocietyConfig(
            prepared.run_id, _agent_specs(prepared.scenario), "LifeAgent", env, service,
            prepared.start_time, prepared.scenario.end_at, prepared.run_dir,
            prepared.project_root, prepared.workspace_mode,
        )
        return actor, AgentSociety(config)
    except BaseException as error:
        _terminate_failed_actor(actor, error)
        raise


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


def _require_custom_modules(project_root: Path) -> None:
    """@brief 经实际 AS2 scanner 核对 LifeAgent 与 LifeEnv 均被接受。"""
    from agentsociety2.registry import scan_and_register_custom_modules

    result = scan_and_register_custom_modules(project_root)
    errors = [*result.get("errors", []), *result.get("registration_errors", [])]
    agents = {item["type"] for item in result.get("agents", [])}
    environments = {item["type"] for item in result.get("envs", [])}
    if errors or "LifeAgent" not in agents or "LifeEnv" not in environments:
        raise RuntimeError("LifeAgent or LifeEnv custom module registration failed")


def _agent_specs(scenario: LifeScenario) -> tuple[dict[str, Any], ...]:
    """@brief 从冻结生活人物形成原生 runner 工作区输入。"""
    specs = []
    for agent in sorted(scenario.agents, key=lambda item: item.agent_id):
        profile = agent.model_dump(mode="json")
        profile["id"] = agent.agent_id
        config = {
            "enable_memory": True,
            "enable_todo_list": False,
            "max_react_turns": 10,
            "disabled_skill_ids": [],
            "default_activated_skill_ids": [],
        }
        specs.append({"id": agent.agent_id, "profile": profile, "config": config})
    return tuple(specs)
