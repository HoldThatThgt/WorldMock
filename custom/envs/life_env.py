"""@brief AgentSociety2 原生 LifeEnv 扩展，委托唯一生活领域服务。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from agentsociety2.env import EnvBase
from agentsociety2.society import SocietyCheckpointRequest

from worldmock.errors import ProtocolError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention, REQUEST_MODELS, RunIdentity
from worldmock.status import write_status

#: 修订合同（L1606/L1624-1626）要求环境侧可受理的四件新生活工具。
_REVISION_CONTRACT_TOOLS = frozenset({
    "plan_day", "revise_day_plan", "send_message", "start_social_activity"})


class LifeEnv(EnvBase):
    """@brief 为 DirectRouter 提供严格决策轮、事件调度和共同检查点。"""

    def __init__(self, scenario_path: str | None = None, run_dir: str | None = None,
                 run_identity: dict | None = None, open_existing: bool = False,
                 agent_taste_weights: dict | None = None):
        """@brief 注册扫描可无参数构造；实际 init 必须具有完整路径。"""
        super().__init__()
        self.scenario_path = Path(scenario_path).resolve() if scenario_path else None
        self.run_dir = Path(run_dir).resolve() if run_dir else None
        self.run_identity = RunIdentity.model_validate(run_identity) if run_identity else None
        self.open_existing = open_existing
        self.agent_taste_weights = agent_taste_weights
        self.engine: LifeEngine | None = None

    @classmethod
    def init_description(cls) -> str:
        """@brief 描述唯一新建或明确打开运行的构造参数。"""
        return ("LifeEnv requires scenario_path, run_dir, and run_identity for a new run. "
                "Set open_existing=false for a new run or "
                "open_existing=true only for an existing immutable run directory.")

    async def init(self, start_datetime: datetime) -> None:
        """@brief 校验起始时钟并创建或明确打开运行。"""
        await super().init(start_datetime)
        if self.scenario_path is None or self.run_dir is None:
            raise ProtocolError("LifeEnv init requires scenario_path and run_dir")
        if self.engine is not None:
            raise ProtocolError("LifeEnv was already initialized")
        if self.open_existing:
            self.engine = LifeEngine.open(self.scenario_path, self.run_dir)
        else:
            if self.run_identity is None:
                raise ProtocolError("New LifeEnv run requires explicit run_identity")
            from worldmock.runtime import two_stage_model_identity
            self.engine = LifeEngine.create(self.scenario_path, self.run_dir, self.run_identity,
                                            two_stage_model_identity(), self.agent_taste_weights)
        if int(start_datetime.timestamp()) != self.engine.state.current_time:
            self.engine.close()
            self.engine = None
            raise ProtocolError("LifeEnv start time disagrees with the scenario")
        self._validate_registered_tool_surface()
        self._workspace_root = self.run_dir

    def _validate_registered_tool_surface(self) -> None:
        """@brief 核对环境与人物两侧登记的完整新工具 Schema 闭集。

        LifeEnv 的工具登记 = 领域 REQUEST_MODELS（引擎 _dispatch 的唯一
        受理闭集）。四件修订合同工具缺失说明领域模型未迁移完成；人物侧
        开放集合与登记闭集不一致说明 agent/env 工具面漂移——两者都在
        init 时 fail-closed，而不是运行中 KeyError。
        """
        from custom.agents.life_agent import life_action_tool_names

        missing = _REVISION_CONTRACT_TOOLS - set(REQUEST_MODELS)
        if missing:
            raise ProtocolError(
                f"LifeEnv registered tool surface is missing revision tools: {sorted(missing)}")
        if life_action_tool_names() != frozenset(REQUEST_MODELS):
            raise ProtocolError(
                "LifeEnv tool surface disagrees with the LifeAgent action tool set")

    async def begin_decision_round(self, contexts: tuple[Any, ...]) -> str:
        """@brief 登记共同版本和实际结算顺序。"""
        engine = self._require_engine()
        write_status(engine.store.run_dir, "running", "deciding")
        engine.begin_decision_round(list(contexts))
        return contexts[0].round_id

    async def observe(self, context: Any) -> dict:
        """@brief 返回严格人物视图并追加只读观察证据。"""
        return self._require_engine().observe(context)

    async def submit_action(self, call: Any):
        """@brief 登记宿主绑定的有类型生活动作。"""
        engine = self._require_engine()
        receipt = engine.submit_action(call)
        await engine.prepare_pending_work_review(call.action_id)
        return receipt

    async def commit_decision_round(self, round_id: str):
        """@brief 原子结算人物批次，随后送达待发的联系人 model 回复（L1628）。"""
        engine = self._require_engine()
        write_status(engine.store.run_dir, "running", "resolving")
        results = engine.commit_decision_round(round_id)
        from worldmock.runtime import settle_pending_model_replies
        await settle_pending_model_replies(engine)
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
        """@brief 对全部人物工作区取不可变快照并提交共同检查点。"""
        engine = self._require_engine()
        write_status(engine.store.run_dir, "running", "checkpointing")
        return self._checkpoint(engine, request)

    def _checkpoint(self, engine: LifeEngine, request: Any):
        """@brief 校验共同身份并为每个人物工作区形成素材快照。"""
        if not isinstance(request, SocietyCheckpointRequest):
            raise ProtocolError("Checkpoint request must use SocietyCheckpointRequest")
        if request.run_id != engine.state.run_id or int(request.current_time.timestamp()) != engine.state.current_time:
            raise ProtocolError("Checkpoint request identity or time is stale")
        expected = set(engine.store.manifest["agent_ids"])
        if {agent_id for agent_id, _ in request.agent_workspaces} != expected:
            raise ProtocolError("Checkpoint request agent set is incomplete")
        snapshots = {f"agent:{agent_id}": snapshot_directory(Path(path), engine.store.assets)
                     for agent_id, path in request.agent_workspaces}
        return engine.checkpoint(snapshots)

    async def step(self, tick: int, t: datetime) -> None:
        """@brief 直接推进到 Society 已选定的下一个严格边界。"""
        engine = self._require_engine()
        write_status(engine.store.run_dir, "running", "advancing")
        engine.advance(tick, t)
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
        target = Path(workspace_path).resolve() if workspace_path else engine.store.run_dir
        if target != engine.store.run_dir:
            raise ProtocolError("LifeEnv workspace must be its footprint run directory")
        self._workspace_root = target

    async def restore(self, workspace_path: Path) -> bool:
        """@brief 从明确已有的分支运行恢复；任何缺失或损坏直接失败。"""
        if self.scenario_path is None:
            raise ProtocolError("LifeEnv restore requires scenario_path")
        if self.engine is not None:
            self.engine.close()
        self.run_dir = Path(workspace_path).resolve()
        self.engine = LifeEngine.open(self.scenario_path, self.run_dir)
        self._workspace_root = self.run_dir
        timezone = self.engine.scheduling_state().current_time.tzinfo
        self.t = datetime.fromtimestamp(self.engine.state.current_time, timezone)
        return True

    @classmethod
    async def from_workspace(cls, workspace_path: Path, **init_kwargs: Any) -> "LifeEnv":
        """@brief 构造实例后严格恢复已有运行，不允许隐式 fresh。"""
        module = cls(**init_kwargs)
        await module.restore(Path(workspace_path))
        return module

    async def close(self) -> None:
        """@brief 关闭持久连接并释放环境实例。"""
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
            raise ExceptionGroup("LifeEnv close and failure recording both failed",
                                 [close_error, record_error]) from close_error

    def _require_engine(self) -> LifeEngine:
        """@brief 拒绝在 init 或 restore 之前调用生活接口。"""
        if self.engine is None:
            raise ProtocolError("LifeEnv is not initialized")
        return self.engine
