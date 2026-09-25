"""@brief 编排原生 AgentSociety2、DeepSeek、LifeAgent 与 LifeEnv。"""

from __future__ import annotations

import copy
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

from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.checkpoint import restore_directory
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import (
    CounterfactualIntervention,
    ModelIdentity,
    RunIdentity,
)
from worldmock.catalog import validate_scenario_against_catalog
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.state_commit import commit_state
from worldmock.life.semantics import ScenarioSemantics, load_runtime_scenario
from worldmock.life.run_identity import require_category_identity
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from worldmock.opslog import OpsLogger

logger = logging.getLogger(__name__)

#: 自述、工具纠错与结束共享的有限回合预算。
MAX_REACT_TURNS = 16

#: 工作验收阶段参数（docs/dev/SPEC-life-realism.md「两阶段决策」：独立工作验收保持
#: disabled/required）。
_WORK_REVIEW_THINKING = "disabled"
_WORK_REVIEW_TOOL_CHOICE = "required"
#: 联系人回复的生成上限。
CONTACT_REPLY_MAX_TOKENS = 8000
#: 联系人回复的 system 提示（docs/dev/SPEC-life-runtime.md「社交最小可行版」：独立调用、中文、不超过 200 汉字）。
_CONTACT_REPLY_PROMPT = (
    "你是消息收件人通讯录里的一名联系人。根据你的联系人档案与收到的消息，"
    "以你本人的口吻写一条自然、简短的中文回复。只输出回复正文，不超过 200 个汉字，"
    "不要解释、不要客套开场。")


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
    scenario: RuntimeScenario
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
        "AGENTSOCIETY_LLM_API_BASE": DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_LLM_MODEL": DEEPSEEK_MODEL,
        "AGENTSOCIETY_CODER_LLM_API_KEY": secret,
        "AGENTSOCIETY_CODER_LLM_API_BASE": DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_CODER_LLM_MODEL": DEEPSEEK_MODEL,
        "AGENTSOCIETY_EMBEDDING_MODEL": "",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "AGENTSOCIETY_LLM_REQUEST_TIMEOUT": "300",
        "MEM0_TELEMETRY": "False",
        "ANONYMIZED_TELEMETRY": "False",
    }
    os.environ.update(variables)
    _prepend_pythonpath(root)
    _require_loaded_config()


def _read_deepseek_key() -> str:
    """@brief 只读取固定私密配置中的多把 DeepSeek 节点密钥，不输出其内容。

    值为逗号分隔的至少一个非空密钥段；逐段 strip、按出现顺序去重后
    以逗号 join 返回（单把密钥也 join），由 Router 侧拆分为多 deployment。
    """
    path = Path.home() / ".config" / "worldmock" / "deepseek.env"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("DeepSeek credential file is missing or unsafe")
    values = dotenv_values(path, interpolate=False)
    unknown = set(values) - {"DEEPSEEK_NODE_KEYS"}
    raw = values.get("DEEPSEEK_NODE_KEYS")
    if unknown or not isinstance(raw, str) or not raw.strip():
        raise IntegrityError("DeepSeek credential file has an invalid schema")
    segments = [item.strip() for item in raw.split(",")]
    if not segments or any(not item for item in segments):
        raise IntegrityError("DeepSeek credential file has an invalid schema")
    return ",".join(dict.fromkeys(segments))


def _prepend_pythonpath(root: Path) -> None:
    """@brief 让 Ray worker 从当前精确 checkout 导入 custom 与 worldmock。"""
    current = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    required = [str(root), str(root / "src")]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([*required, *current]))


def _require_loaded_config() -> None:
    """@brief 拒绝进程早先导入的不同 AS2 模型配置。"""
    from agentsociety2.config.config import Config

    expected = (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)
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
    semantics, digest = load_runtime_scenario(scenario_path)
    _admit_scenario(semantics)
    scenario = semantics.scenario
    identity = _run_identity(request, scenario)
    kwargs = {
        "scenario_path": str(scenario_path),
        "run_dir": str(run_dir),
        "run_identity": identity.model_dump(mode="json"),
        "open_existing": False,
    }
    _ops_log(run_dir, scenario.timezone, scenario.start_at, "RUN_STARTED", "Run started",
             run_id=request.run_id, code_sha256=identity.code_sha256, scenario_sha256=digest,
             two_stage_model_identity=two_stage_model_identity())
    return _PreparedRun(
        Path(request.project_root).resolve(), scenario_path, run_dir, request.run_id,
        scenario, scenario.start_at, kwargs, "create",
    )


def _run_identity(request: RunRequest, scenario: RuntimeScenario) -> RunIdentity:
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


def _admit_scenario(semantics: ScenarioSemantics) -> None:
    """@brief 目录准入七条规则任一违例即拒绝启动（fail-closed，英文错误）。"""
    result = validate_scenario_against_catalog(semantics.refs, semantics.catalog)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"Scenario admission rejected by catalog: {detail}")


def _model_identity() -> ModelIdentity:
    """@brief 形成不含凭据的显式 DeepSeek、提示和工具身份。

    顶层 thinking/tool_choice 取行动阶段参数并直接来自 B2 导出常量
    （单一事实源）；两阶段完整形状见 two_stage_model_identity()。
    """
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        life_prompt_sha256,
        life_tool_schema_sha256,
    )

    return ModelIdentity(
        provider="openai_compatible",
        base_url=DEEPSEEK_BASE_URL,
        requested_model=DEEPSEEK_MODEL,
        thinking=ACTION_THINKING["thinking"]["type"],
        tool_choice=ACTION_TOOL_CHOICE,
        max_retries=0,
        prompt_sha256=life_prompt_sha256(),
        tool_schema_sha256=life_tool_schema_sha256(),
    )


def two_stage_model_identity() -> dict[str, Any]:
    """@brief 两阶段决策与工作验收的完整冻结请求身份（docs/dev/SPEC-life-realism.md「两阶段决策」）。

    决策自述/行动阶段参数取自 LifeAgent 导出常量（B2 设计决定 8 的单一事实源）；
    运行中不变更。不发送 reasoning_effort（以 None 表示该字段不进请求）。
    """
    from agentsociety2.config.llm_dispatcher import TRANSPORT_MAX_RETRIES
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        ACTION_MAX_TOKENS,
        DECISION_STATEMENT_MAX_TOKENS,
        DECISION_STATEMENT_TEMPERATURE,
    )

    return {
        "provider": "openai_compatible",
        "base_url": DEEPSEEK_BASE_URL,
        "requested_model": DEEPSEEK_MODEL,
        "max_retries": 0,
        "transport_max_retries": TRANSPORT_MAX_RETRIES,
        "max_react_turns": MAX_REACT_TURNS,
        "strict": True,
        "reasoning_effort": None,
        "stages": {
            "decision_statement": {
                "thinking": "disabled",
                "temperature": DECISION_STATEMENT_TEMPERATURE,
                "max_tokens": DECISION_STATEMENT_MAX_TOKENS,
                "tools": None,
                "tool_choice": None,
            },
            "action": {
                "thinking": ACTION_THINKING["thinking"]["type"],
                "tool_choice": ACTION_TOOL_CHOICE,
                "max_tokens": ACTION_MAX_TOKENS,
            },
            "work_review": {
                "thinking": _WORK_REVIEW_THINKING,
                "tool_choice": _WORK_REVIEW_TOOL_CHOICE,
            },
        },
    }


def _prepare_resume(
    request: ResumeRequest,
    intervention: CounterfactualIntervention | None = None,
) -> _PreparedRun:
    """@brief 校验父身份并原子恢复普通或反事实子分支。"""
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
        _require_runtime_identity(parent.manifest, Path(request.project_root))
        require_category_identity(parent.manifest, semantics)
        manifest, state = _restore_branch(parent, checkpoint, destination, metadata)
    finally:
        parent.close()
    start = datetime.fromtimestamp(state["current_time"], ZoneInfo(scenario.timezone))
    kwargs = {"scenario_path": str(scenario_path), "run_dir": str(destination), "open_existing": True}
    _ops_log(destination, scenario.timezone, start, "RUN_RESUMED", "Run resumed from checkpoint",
             run_id=manifest["run_id"], code_sha256=manifest.get("code_sha256"),
             scenario_sha256=manifest.get("scenario_sha256"), checkpoint_id=request.checkpoint_id)
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
    if manifest.get("two_stage_model_identity") != two_stage_model_identity():
        raise IntegrityError("Checkpoint decision stage identity differs from the current LifeAgent contract")
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


def _ops_log(run_dir: Path, timezone_name: str, sim_time: int | datetime | None,
             code: str, detail: str, level: str = "info", **keys: Any) -> None:
    """@brief 运行进程侧写一条 runtime 操作日志，随写随关不驻留句柄。"""
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
    if type(config.get("max_react_turns")) is not int or config["max_react_turns"] != MAX_REACT_TURNS:
        logger.error("Restored LifeAgent ReAct budget differs from the frozen runtime budget")
        raise IntegrityError("Restored LifeAgent max_react_turns must match the frozen runtime budget")
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
    """@brief 连接 DirectEnvActor、EnvRouterProxy、ServiceProxy 与 AgentSociety。"""
    from agentsociety2.agent.service_proxy import build_service_proxy
    from agentsociety2.config.llm_dispatcher import build_client_for_role
    from agentsociety2.env.direct_env_actor import DirectEnvActorSpec, get_direct_env_actor_class
    from agentsociety2.env.env_router_proxy import EnvRouterProxy
    from agentsociety2.society import AgentSociety, AgentSocietyConfig

    _require_custom_modules(prepared.project_root)
    clients = {role: build_client_for_role(role) for role in ("coder", "default")}
    create_mode = prepared.workspace_mode == "create"
    specs = _agent_specs(
        prepared.scenario,
        persona_compiler=(None if create_mode else _no_persona_compilation),
        content_genres=(_content_genre_catalog(prepared.scenario_path, prepared.scenario)
                        if create_mode else None),
    )
    if create_mode:
        prepared.env_kwargs["agent_taste_weights"] = {
            spec_id: profile.get("taste_weights", {})
            for spec_id, profile in ((item["id"], item["profile"]) for item in specs)}
    spec = DirectEnvActorSpec(
        ("LifeEnv",), {"LifeEnv": prepared.env_kwargs}, str(prepared.run_dir),
        str(prepared.project_root), clients,
    )
    actor = get_direct_env_actor_class().remote(spec)
    try:
        env = EnvRouterProxy(actor, run_dir=prepared.run_dir, env_module_types=["LifeEnv"])
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


class ContactReplyClient:
    """@brief 联系人 model 回复的独立 DeepSeek 客户端（工作验收同模式）。

    复用 AS2 已维护的单次 LiteLLM 客户端，身份必须与运行 manifest 一致；
    不重试业务或协议错误；瞬时传输错误使用调度器独立的三次重试预算。
    """

    def __init__(self, model_identity: dict[str, Any]):
        """@brief 建立与运行 manifest 完全相同的 DeepSeek 客户端。"""
        from agentsociety2.config import build_client_for_role

        client = build_client_for_role("default")
        if (client.model_name != model_identity["requested_model"]
                or client.base_url != model_identity["base_url"]):
            raise ProtocolError("Contact reply client identity disagrees with the run manifest")
        if model_identity["max_retries"] != 0:
            raise ProtocolError("Contact reply request parameters disagree with the run manifest")
        self._client = client
        self.model_name = model_identity["requested_model"]

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        """@brief 发出一次无内部重试的实际请求并返回可持久化原始响应。"""
        response = await self._client.call(**copy.deepcopy(request))
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json")
        elif isinstance(response, dict):
            payload = dict(response)
        else:
            raise ProtocolError("Contact reply model response is not serializable")
        if not isinstance(payload, dict):
            raise ProtocolError("Contact reply model response is not a serialized object")
        return payload


def prepare_contact_reply(request: Any, contact: dict[str, Any] | None,
                          model_name: str) -> dict[str, Any]:
    """@brief 形成联系人回复的独立请求（thinking=disabled、无工具、无重试）。"""
    payload = {"contact": contact if contact is not None
               else {"contact_id": request.contact_id},
               "incoming_message": request.incoming_text,
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
    """@brief 执行引擎登记的联系人 model 回复并登记预排送达（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。

    每轮结算后调用：从足迹库取未结算的 model_npc_reply_pending 请求，经
    独立 DeepSeek 调用生成不超过 200 汉字的回复，请求/原始响应/用量保存为
    事件证据，回复经 social.finalize_model_reply 登记为 CONTACT_REPLY_DELAY_SECONDS
    后到期的预排送达，到期只进入收件箱（不改资源或任务）。传输与结构异常按运行故障传播
    （与工作验收同边界）；同一消息最多回复一次（幂等键 reply_to_message_id）。
    """
    from worldmock.life import social as social_domain

    pending = _pending_reply_requests(engine)
    if not pending:
        return []
    caller = client if client is not None else ContactReplyClient(
        engine.store.manifest["model_identity"])
    settled = []
    for event, request in pending:
        model_request = prepare_contact_reply(
            request, _contact_profile(engine, request), caller.model_name)
        raw = await caller.call(model_request)
        text = parse_contact_reply(raw)
        reply = social_domain.finalize_model_reply(
            request, text, f"reply-{request.reply_to_message_id}", engine.state.current_time)
        settled.append(_commit_contact_reply(engine, event, model_request, raw, reply))
    return settled


def _pending_reply_requests(engine: Any) -> list[tuple[Any, Any]]:
    """@brief 取尚未回复的 pending 请求（事件序升序，幂等去重）。"""
    from worldmock.life.social import ModelReplyRequest

    events = engine.store.read_events(engine.store.last_event_seq)
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
    for agent in engine.semantics.scenario.agents:
        for contact in agent.contacts:
            if contact.contact_id == request.contact_id:
                return {"contact_id": contact.contact_id,
                        "salutation": contact.salutation,
                        "relation": contact.relation,
                        "closeness": contact.closeness,
                        "channels": list(contact.channels)}
    return None


def _commit_contact_reply(engine: Any, pending_event: Any, model_request: dict,
                          raw: dict, reply: Any) -> dict:
    """@brief 一次事务提交回复证据并登记预排送达（版本 +1，资源零变动）。

    回复生成时即留 model_npc_reply 证据；收件箱条目在 CONTACT_REPLY_DELAY_SECONDS 后由预排事件
    message_arrival 送达，收件人届时以 message_arrival 唤醒、睡眠中过滤（docs/CONTRACTS.md「联系人回复」）。
    """
    from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

    recipient = int(reply.message.recipient_id.split(":", 1)[1])
    staged = engine.state.model_copy(deep=True)
    sequence = max((item.sequence for item in staged.pending_events), default=0) + 1
    staged.pending_events.append(ScheduledEvent(
        scheduled_id=f"contact-reply:{reply.message.message_id}", kind=ScheduledKind.MESSAGE_ARRIVAL,
        due_at=staged.current_time + CONTACT_REPLY_DELAY_SECONDS, sequence=sequence, agent_id=recipient,
        data={"message": reply.message.model_dump(mode="json")}))
    staged.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    staged.version = engine.state.version + 1
    drafts = [
        EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                   agent_id=recipient, source_kind="model", visibility="system",
                   caused_by=[pending_event.event_id],
                   data={"kind": "model_npc_reply", "source_kind": reply.source_kind,
                         "reply_to_message_id": reply.message.message_id.removeprefix("reply-"),
                         "request": model_request, "response": raw,
                         "usage": raw.get("usage"),
                         "reply": reply.message.model_dump(mode="json")}),
    ]
    commit_state(engine.store, staged, drafts)
    engine.state = staged
    return {"reply_to_message_id": reply.message.message_id.removeprefix("reply-"),
            "reply_message_id": reply.message.message_id,
            "source_kind": reply.source_kind}


def _require_custom_modules(project_root: Path) -> None:
    """@brief 经实际 AS2 scanner 核对 LifeAgent 与 LifeEnv 均被接受。"""
    from agentsociety2.registry import scan_and_register_custom_modules

    result = scan_and_register_custom_modules(project_root)
    errors = [*result.get("errors", []), *result.get("registration_errors", [])]
    agents = {item["type"] for item in result.get("agents", [])}
    environments = {item["type"] for item in result.get("envs", [])}
    if errors or "LifeAgent" not in agents or "LifeEnv" not in environments:
        raise RuntimeError("LifeAgent or LifeEnv custom module registration failed")


def _agent_specs(scenario: RuntimeScenario, *,
                 persona_compiler=None, content_genres=None) -> tuple[dict[str, Any], ...]:
    """@brief 从冻结生活人物形成原生 runner 工作区输入。

    新建运行时为声明 background_story 的人物注入 PersonaCard 渲染文本
    （A9 compile_persona -> render_card，B2 LifeAgent 读取 profile["persona_card"]），
    并由同一次编译产出确定性口味权重 profile["taste_weights"]（修订合同
    docs/dev/SPEC-life-realism.md「画像编译」：card.tastes.content_genres x 场景内容目录，经 compute_taste_weights
    匹配；写入工作区档案随检查点原样携带）。恢复运行不重新编译也不重算——
    人物 profile 由检查点工作区原样恢复，分支人物身份与父运行保持一致。
    persona_compiler/content_genres 供测试注入确定性桩（注入文本桩时只影响
    卡片文本，不触发真实模型调用与口味计算）。
    """
    contents = tuple({"item_id": item.item_id,
                      "genre": tuple((content_genres or {}).get(item.item_id) or ())}
                     for item in scenario.contents)
    specs = []
    for agent in sorted(scenario.agents, key=lambda item: item.agent_id):
        profile = agent.model_dump(mode="json")
        profile["id"] = agent.agent_id
        if persona_compiler is not None:
            card_text, card = persona_compiler(agent), None
        else:
            card_text, card = _persona_payload(agent)
        if card_text is not None:
            profile["persona_card"] = card_text
        if card is not None:
            profile["taste_weights"] = _card_taste_weights(card, contents)
        config = {
            "enable_memory": True,
            "enable_todo_list": False,
            "max_react_turns": MAX_REACT_TURNS,
            "disabled_skill_ids": [],
            "default_activated_skill_ids": [],
        }
        specs.append({"id": agent.agent_id, "profile": profile, "config": config})
    return tuple(specs)


def _persona_payload(agent: Any) -> tuple[str | None, Any]:
    """@brief 一次 background_story 编译同时产出渲染文本与结构化卡片。

    @param agent 场景人物配置（无 background_story 声明时返回 (None, None)）
    @return (PersonaCard 渲染文本, 结构化 PersonaCard) 二元组
    """
    card = _compile_persona(agent)
    if card is None:
        return None, None
    from worldmock.profile.textgen import render_card

    return render_card(card), card


def _compile_persona(agent: Any) -> Any:
    """@brief 场景 background_story -> 结构化 PersonaCard（真实调用走三次一致性抽取）。"""
    if agent.background_story is None:
        return None
    from worldmock.profile.compile import compile_persona

    return compile_persona(agent.background_story).card


def _card_taste_weights(card: Any, contents: tuple[dict, ...]) -> dict[str, float]:
    """@brief 卡片口味 x 场景内容目录 -> 确定性权重（compute_taste_weights 生产接线）。"""
    from worldmock.profile.taste import compute_taste_weights

    return compute_taste_weights(card.tastes.content_genres, contents)


def _content_genre_catalog(scenario_path: Path,
                           scenario: RuntimeScenario) -> dict[str, tuple[str, ...]]:
    """@brief 从内容语料条目收集 genre 表（item_id -> genre 元组，缺失即失败）。

    @param scenario_path 冻结场景路径（用于解析相对 corpus_root）
    @param scenario 组装后的运行时场景
    @return 场景内容目录的 genre 查找表（条目缺失 genre 视为空口味）
    """
    from worldmock.catalog.admission import CONTENT_CORPORA

    root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
    genres: dict[str, tuple[str, ...]] = {}
    for relative, mode in CONTENT_CORPORA:
        document = json.loads((root / relative).read_text(encoding="utf-8"))
        items = [document] if mode == "single" else (
            document if isinstance(document, list) else [])
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("item_id"), str):
                genres[item["item_id"]] = tuple(item.get("genre") or [])
    return genres


def _no_persona_compilation(agent: Any) -> None:
    """@brief 恢复运行不重新编译：人物卡片随检查点工作区原样恢复。"""
    return None
