"""@brief AgentSociety2 原生 LifeEnv 扩展，委托唯一生活领域服务。"""

from __future__ import annotations
from worldmock.runtime.framework.setup import stage_model_identity
from worldmock.runtime.lifecycle.runner import settle_pending_day_plans, settle_pending_memory_digests
from custom.agents.life_agent import life_action_tool_names, life_query_tool_names
from worldmock.configuration.inputs.environment import load_environment

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agentsociety2.config.llm_dispatcher import close_cached_async_clients
from agentsociety2.env import EnvBase
from agentsociety2.society import SocietyCheckpointRequest

from worldmock.foundation.errors import BusinessRuleError, ProtocolError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.observation.wording.guidance import query_rejection
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.interaction.requests import REQUEST_MODELS, RecallTaskSummaryRequest, SearchMemoryRequest
from worldmock.state.identity import RunIdentity
from worldmock.runtime.monitoring.status import write_status

#: 稳定工具面（docs/design/06-agent.md「提示与工具」）：环境侧必须恰好受理七个请求工具；
#: 零点计划 plan_day 是运行时后台任务，不是人物工具（docs/design/04-simulation-engine.md）。
_STABLE_TOOL_SURFACE = frozenset(
    {
        "start_main_activity",
        "start_side_activity",
        "recall_task_summary",
        "report_status",
        "pause_activity",
        "conclude_activity",
        "revise_day_plan",
    }
)

#: read_query 的白名单：回看任务与 QUERY_MODELS 的只读查询，工具名 → 请求类型（docs/design/06-agent.md「提示与工具」）；
#: 与稳定工具面一样由环境一侧显式写出，init 时与人物侧的只读查询工具集核对。
_READ_QUERY_MODELS = {"recall_task_summary": RecallTaskSummaryRequest, "search_memory": SearchMemoryRequest}


class LifeEnv(EnvBase):
    """@brief 为 DirectRouter 提供严格决策轮、事件调度和共同检查点。"""

    def __init__(
        self,
        environment_root: str | None = None,
        run_dir: str | None = None,
        run_identity: dict | None = None,
        open_existing: bool = False,
        create_options: dict | None = None,
    ):
        """@brief 注册扫描可无参数构造；实际 init 必须具有完整路径。

        create_options 是新建运行的附加输入：input_warnings（运行输入警告，
        冻结进清单；docs/design/08-configuration.md「校验与输入冻结」）、persona_habit_responses（人设层习惯签名的
        反应文本，冻结进清单）与 same_second_continuation
        （同秒续接开关，缺省开，冻结进清单 stages.action；docs/design/04-simulation-engine.md「世界与请求」）。
        """
        super().__init__()
        self.environment_root = Path(environment_root).resolve() if environment_root else None
        self.run_dir = Path(run_dir).resolve() if run_dir else None
        self.run_identity = RunIdentity.model_validate(run_identity) if run_identity else None
        self.open_existing = open_existing
        self.create_options = dict(create_options or {})
        self.engine: LifeEngine | None = None

    @classmethod
    def init_description(cls) -> str:
        """@brief 描述唯一新建或明确打开运行的构造参数。"""
        return (
            "LifeEnv requires environment_root, run_dir, and run_identity for a new run. "
            "Set open_existing=false for a new run or "
            "open_existing=true only for an existing immutable run directory."
        )

    async def init(self, start_datetime: datetime) -> None:
        """@brief 校验起始时钟并创建或明确打开运行；普通恢复在首次决策之前先补齐缺失的回顾，再补齐缺失的当日计划。

        锁定的 AS2 在恢复模式不形成初始检查点、首轮决策又先于推进，从回顾或计划尚未生成的检查点普通恢复时，
        零点的回顾与计划只能在这里执行（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）；此调用早于 AS2
        登记运行故障的范围，任务异常先经 engine.fail_run 登记再抛出。反事实分支在干预检查点之后执行。
        """
        await super().init(start_datetime)
        if self.environment_root is None or self.run_dir is None:
            raise ProtocolError("LifeEnv init requires environment_root and run_dir")
        if self.engine is not None:
            raise ProtocolError("LifeEnv was already initialized")
        if self.open_existing:
            self.engine = LifeEngine.open(self.environment_root, self.run_dir)
        else:
            if self.run_identity is None:
                raise ProtocolError("New LifeEnv run requires explicit run_identity")
            inputs = load_environment(self.environment_root)
            identity = stage_model_identity(inputs.config.model, inputs.model_metadata,
                                             self.create_options.get("same_second_continuation", True))
            self.engine = LifeEngine.create(
                inputs, self.run_dir, self.run_identity, identity, self.create_options
            )
        if int(start_datetime.timestamp()) != self.engine.context.state.current_time:
            self.engine.close()
            self.engine = None
            raise ProtocolError("LifeEnv start time disagrees with the environment")
        self._validate_registered_tool_surface()
        self._workspace_root = self.run_dir
        if self.open_existing and self.engine.context.store.manifest.get("branch_kind") == "resume":
            await self._settle_midnight_tasks_on_resume(self.engine)

    async def _settle_midnight_tasks_on_resume(self, engine: LifeEngine) -> None:
        """@brief 普通恢复时依次执行回顾与零点计划两个后台任务；任一异常先登记运行故障再抛出。"""

        try:
            await settle_pending_memory_digests(engine)
            await settle_pending_day_plans(engine)
        except Exception as error:
            engine.fail_run(repr(error))
            raise

    def _validate_registered_tool_surface(self) -> None:
        """@brief 核对环境与人物两侧登记的工具闭集：请求工具与稳定工具面一致、人物侧生活动作工具与请求工具一致、
        人物侧只读查询工具与 read_query 白名单一致。

        LifeEnv 的工具登记 = 领域 REQUEST_MODELS（引擎 _dispatch 的唯一受理闭集）；任何一项不一致都在 init 时
        fail-closed，而不是运行中 KeyError。
        """

        registered = frozenset(REQUEST_MODELS)
        drift = registered ^ _STABLE_TOOL_SURFACE
        if drift:
            raise ProtocolError(
                f"LifeEnv registered tool surface disagrees with the stable tool surface: {sorted(drift)}"
            )
        if life_action_tool_names() != registered:
            raise ProtocolError("LifeEnv tool surface disagrees with the LifeAgent action tool set")
        if life_query_tool_names() != frozenset(_READ_QUERY_MODELS):
            raise ProtocolError("LifeEnv read query surface disagrees with the LifeAgent query tool set")

    async def begin_decision_round(self, contexts: tuple[Any, ...]) -> str:
        """@brief 登记共同版本和实际结算顺序。"""
        engine = self._require_engine()
        write_status(engine.context.store.run_dir, "running", "deciding")
        engine.begin_decision_round(list(contexts))
        return contexts[0].round_id

    async def observe(self, context: Any) -> dict:
        """@brief 返回严格人物视图并追加只读观察证据。"""
        return self._require_engine().observe(context)

    async def submit_action(self, call: Any):
        """@brief 登记宿主绑定的有类型生活动作。"""
        engine = self._require_engine()
        return engine.submit_action(call)

    async def read_query(self, context: Any, tool: str, request: Any) -> dict:
        """@brief 对已冻结观察即时回答回看任务与检索记忆，不产生世界动作、事件或人物记忆；白名单以外的工具名或与工具名
        不符的请求类型一律是协议错误，业务拒绝以五字段形状返回。"""
        engine = self._require_engine()
        engine.decisions.validate_context(context)
        engine.decisions.require_round(context.round_id)
        if type(request) is not _READ_QUERY_MODELS.get(tool):
            logging.error("Read query tool and request type do not match: %s", tool)
            raise ProtocolError("Read query tool and request type do not match")
        rows = engine.context.store.read_decision_observations(
            context.agent_id, context.decision_id, int(context.observed_at.timestamp()))
        if len(rows) != 1:
            logging.error("Read query requires one frozen decision observation, found %d", len(rows))
            raise ProtocolError("Read query requires one frozen decision observation")
        try:
            if tool == "recall_task_summary":
                return engine.read_task_summary(context.agent_id, request, rows[0].data)
            return engine.search_memory(context.agent_id, request, rows[0])
        except BusinessRuleError as error:
            return query_rejection(error)

    async def commit_decision_round(self, round_id: str):
        """@brief 原子结算唯一人物的决策批次并提交真实环境证据。"""
        engine = self._require_engine()
        write_status(engine.context.store.run_dir, "running", "resolving")
        results = engine.commit_decision_round(round_id)
        return results

    async def abort_decision_round(self, round_id: str, error: str) -> None:
        """@brief 保存失败证据且不执行尚未结算的生活动作。"""
        self._require_engine().abort_decision_round(round_id, error)

    async def scheduling_state(self):
        """@brief 返回 AS2 的严格 SchedulingState。"""
        return self._require_engine().scheduling_state()

    async def record_model_exchange(self, exchange: Any) -> None:
        """@brief 保存实际模型请求、响应或失败证据。"""
        self._require_engine().record_model_exchange(exchange)

    async def record_memory_update(self, evidence: Any) -> None:
        """@brief 保存人物工作区已落盘记忆的不可变身份。"""
        self._require_engine().record_memory_update(evidence)

    async def checkpoint(self, request: Any):
        """@brief 对全部人物工作区取不可变快照并提交共同检查点，随后先补齐待生成的回顾，再为缺当日计划的人物执行零点计划。

        覆盖新建运行的初始检查点与反事实分支的干预检查点（启动路径，docs/design/04-simulation-engine.md）；
        新登记的回顾与计划由下一个共同检查点封存。没有待生成的回顾、无人缺当日计划时两个任务都不构造客户端即返回。
        """
        engine = self._require_engine()
        write_status(engine.context.store.run_dir, "running", "checkpointing")
        committed = self._checkpoint(engine, request)
        await settle_pending_memory_digests(engine)
        await settle_pending_day_plans(engine)
        return committed

    def _checkpoint(self, engine: LifeEngine, request: Any):
        """@brief 校验共同身份并为每个人物工作区形成素材快照。"""
        if not isinstance(request, SocietyCheckpointRequest):
            raise ProtocolError("Checkpoint request must use SocietyCheckpointRequest")
        if (
            request.run_id != engine.context.state.run_id
            or int(request.current_time.timestamp()) != engine.context.state.current_time
        ):
            raise ProtocolError("Checkpoint request identity or time is stale")
        expected = set(engine.context.store.manifest["agent_ids"])
        if {agent_id for agent_id, _ in request.agent_workspaces} != expected:
            raise ProtocolError("Checkpoint request agent set is incomplete")
        snapshots = {
            f"agent:{agent_id}": snapshot_directory(Path(path), engine.context.store.assets)
            for agent_id, path in request.agent_workspaces
        }
        return engine.checkpoint(snapshots)

    async def step(self, tick: int, t: datetime) -> None:
        """@brief 直接推进到 Society 已选定的下一个严格边界；零点边界先生成回顾，再生成当日计划，最后判定该秒的唤醒与决策。"""
        engine = self._require_engine()
        write_status(engine.context.store.run_dir, "running", "advancing")
        engine.advance(tick, t)
        if engine.midnight_rhythm_pending:
            await settle_pending_memory_digests(engine)
            await settle_pending_day_plans(engine)
            engine.decide_midnight_rhythm()
        self.t = t

    async def set_current_time(self, t: datetime) -> None:
        """@brief 核对原生 Society 确认的环境时钟，禁止绕过领域推进修改世界时间。"""
        engine = self._require_engine()
        if int(t.timestamp()) != engine.context.state.current_time:
            raise ProtocolError("Society current time disagrees with the environment")
        self.t = t

    async def fail_run(self, error: str) -> None:
        """@brief 接收 Society 致命通知并持久化唯一 run_failed 证据。"""
        self._require_engine().fail_run(error)

    async def apply_counterfactual(self, intervention: CounterfactualIntervention):
        """@brief 在明确反事实分支的首次决策前提交一次封闭干预。"""
        return self._require_engine().apply_counterfactual(intervention)

    async def to_workspace(self, workspace_path: Path | None = None) -> None:
        """@brief 绑定已有运行目录，不创建第二份环境状态。"""
        engine = self._require_engine()
        target = Path(workspace_path).resolve() if workspace_path else engine.context.store.run_dir
        if target != engine.context.store.run_dir:
            raise ProtocolError("LifeEnv workspace must be its footprint run directory")
        self._workspace_root = target

    async def restore(self, workspace_path: Path) -> bool:
        """@brief 从明确已有的分支运行恢复；任何缺失或损坏直接失败。"""
        if self.environment_root is None:
            raise ProtocolError("LifeEnv restore requires environment_root")
        if self.engine is not None:
            self.engine.close()
        self.run_dir = Path(workspace_path).resolve()
        self.engine = LifeEngine.open(self.environment_root, self.run_dir)
        self._workspace_root = self.run_dir
        timezone = self.engine.scheduling_state().current_time.tzinfo
        self.t = datetime.fromtimestamp(self.engine.context.state.current_time, timezone)
        return True

    @classmethod
    async def from_workspace(cls, workspace_path: Path, **init_kwargs: Any) -> "LifeEnv":
        """@brief 构造实例后严格恢复已有运行，不允许隐式 fresh。"""
        module = cls(**init_kwargs)
        await module.restore(Path(workspace_path))
        return module

    async def close(self) -> None:
        """@brief 在本 actor 的事件循环里关闭并移出 LiteLLM 缓存的异步客户端（零点计划与回顾用过的），再关闭持久连接并释放
        环境实例。"""
        try:
            await close_cached_async_clients()
        finally:
            if self.engine is not None:
                try:
                    self.engine.close()
                except Exception as close_error:
                    self._record_close_failure(close_error)
                    raise
                else:
                    self.engine = None

    def _record_close_failure(self, close_error: Exception) -> None:
        """@brief close 失败后尝试记录；双重故障保留两项异常。"""
        try:
            self._require_engine().fail_run(f"{type(close_error).__name__}: {close_error}")
        except Exception as record_error:
            raise ExceptionGroup(
                "LifeEnv close and failure recording both failed", [close_error, record_error]
            ) from close_error

    def _require_engine(self) -> LifeEngine:
        """@brief 拒绝在 init 或 restore 之前调用生活接口。"""
        if self.engine is None:
            raise ProtocolError("LifeEnv is not initialized")
        return self.engine
