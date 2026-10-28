"""@brief LifeEnv 使用的生活运行门面，职责与状态由显式对象承担。"""

from __future__ import annotations
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.world import world_updates as world_ops

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.events.models import AssetRef, EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.world.counterfactual import CounterfactualRuntime
from worldmock.simulation_engine.decisions.decision_coordinator import DecisionCoordinator
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.evidence.memory import due_reflection
from worldmock.simulation_engine.evidence.model import ModelEvidenceRecorder
from worldmock.state.decisions.results import (
    ActionReceipt,
    ActionResult,
    CounterfactualIntervention,
    CounterfactualReceipt,
)
from worldmock.state.interaction.requests import PlanDayRequest, RecallTaskSummaryRequest
from worldmock.state.identity import RunIdentity
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.state.planning.schedule import DayPlan
from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.simulation_engine.actions.dispatcher import ActionHandlers
from worldmock.simulation_engine.commerce.purchase.storefront_runtime import StorefrontRuntime
from worldmock.simulation_engine.scheduling.clock.time_advance import TimeAdvanceCoordinator
from worldmock.simulation_engine.world.world_setup import create_world, open_world
from worldmock.simulation_engine.world.world_updates import meal_windows_by_agent
from worldmock.simulation_engine.decisions.continuation import CONTINUATION_KEY, same_second_chain

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState
    from worldmock.recording_engine.events.models import Checkpoint
    from worldmock.simulation_engine.body import felt


class LifeEngine:
    """@brief 仅编排现有公开生活协议，不保存第二份世界或业务簿记。"""

    def __init__(self, inputs: EnvironmentInputs, store: FootprintStore):
        """@brief 建立唯一上下文并绑定明确职责。"""
        self.context = EngineContext(inputs, store)
        self.observations = ObservationBuilder(self.context)
        self.actions = ActionHandlers(self.context, StorefrontRuntime(self.context))
        self.decisions = DecisionCoordinator(self.context, self.actions, self.observations)
        self.clock = TimeAdvanceCoordinator(self.context, self.observations)
        self.evidence = ModelEvidenceRecorder(self.context, self.decisions)
        self.counterfactual = CounterfactualRuntime(self.context)

    @classmethod
    def create(
        cls,
        environment: EnvironmentInputs | Path,
        run_dir: Path,
        run_identity: RunIdentity,
        stage_identity: dict,
        create_options: dict | None = None,
    ) -> "LifeEngine":
        """@brief 真实装配冻结输入并新建唯一运行。"""
        return cls(*create_world(environment, run_dir, run_identity, stage_identity, create_options or {}))

    @classmethod
    def open(cls, environment: EnvironmentInputs | Path, run_dir: Path) -> "LifeEngine":
        """@brief 重新核对实际输入并只打开已存在的运行。"""
        return cls(*open_world(environment, run_dir))

    def observe(self, context: "DecisionContext") -> dict:
        """@brief 提交一次真实观察并附当前同秒续接；返回的观察与存储层解码后的观察（去掉只给宿主的键）逐字节相同。"""
        self.decisions.validate_context(context)
        data = self.observations._observation_data(self.context.state.agents[context.agent_id])
        stored, parts = self.observations.stored_observation(data)
        draft = EventDraft(
            event_type="observation",
            occurred_at=self.context.state.current_time,
            agent_id=context.agent_id,
            decision_id=context.decision_id,
            observed_at=self.context.state.current_time,
            data=stored,
        )
        self.context.store.require_committed(self.context.state.model_dump(mode="json"))
        data["observation_event_id"] = self.context.store.append_observation(draft, parts)
        data[CONTINUATION_KEY] = same_second_chain(
            self.context.store, self.context.state, self.observations, context.agent_id, data["observation_event_id"]
        )
        return data

    def due_reflection(self, context: "DecisionContext") -> dict[str, str] | None:
        """@brief 只读：本次决策取观察之前该不该先做就寝反思；不写事件，不改世界。"""
        self.decisions.validate_context(context)
        self.decisions.require_round(context.round_id)
        return due_reflection(self.context.state, context.agent_id)

    def begin_decision_round(self, contexts: list["DecisionContext"]) -> None:
        """@brief 按当前可恢复顺序开始决策轮。"""
        self.decisions.begin_decision_round(contexts, self.clock._ready_agent_ids())

    def submit_action(self, call: "DirectToolCall") -> ActionReceipt:
        """@brief 将真实宿主请求交给决策协调登记。"""
        return self.decisions.submit_action(call)


    def commit_decision_round(self, round_id: str) -> list[ActionResult]:
        """@brief 按冻结顺序原子结算一轮。"""
        return self.decisions.commit_decision_round(round_id)

    def abort_decision_round(self, round_id: str, error: Exception | str) -> None:
        """@brief 原子登记失败而不执行该轮动作。"""
        self.decisions.abort_decision_round(round_id, error)

    def scheduling_state(self) -> "SchedulingState":
        """@brief 取得共享时钟下的真实调度状态。"""
        return self.clock.scheduling_state()

    def record_model_exchange(self, exchange: Any) -> None:
        """@brief 由证据登记职责保存实际模型往来。"""
        self.evidence.record_model_exchange(exchange)

    def record_memory_update(self, evidence: Any) -> None:
        """@brief 由证据登记职责验证已落盘的人物记忆。"""
        self.evidence.record_memory_update(evidence)

    def advance(self, tick: int, target: datetime) -> list[ActionResult]:
        """@brief 按精确的下一边界推进活动与身体。"""
        return self.clock.advance(tick, target, self.decisions.round is not None)

    @property
    def midnight_rhythm_pending(self) -> bool:
        """@brief 零点计划之后是否仍需完成节律判定。"""
        return self.clock.midnight_rhythm_pending

    def decide_midnight_rhythm(self) -> None:
        """@brief 在当日计划登记后提交零点判定。"""
        return self.clock.decide_midnight_rhythm()

    def register_day_plan(
        self, agent_id: int, request: PlanDayRequest, sources: list[str], evidence: list[EventDraft]
    ) -> DayPlan:
        """@brief 系统登记今日计划并同事务保存其模型证据。"""
        return self.clock.register_day_plan(agent_id, request, sources, evidence)

    def read_task_summary(self, agent_id: int, request: RecallTaskSummaryRequest, observed: dict) -> dict:
        """@brief 从真实已提交观察回看任务，读取不改变世界。"""
        return self.actions.read_task_summary(agent_id, request, observed)

    def apply_counterfactual(self, intervention: CounterfactualIntervention) -> CounterfactualReceipt:
        """@brief 在首轮之前对真实反事实分支提交严格干预。"""
        return self.counterfactual.apply_counterfactual(intervention, self.decisions.round is not None)

    def meal_windows_by_agent(self) -> dict[int, "felt.MealWindows"]:
        """@brief 返回冻结人物声明的用餐时间窗口。"""
        return meal_windows_by_agent(self.context.persona)

    def fail_run(self, error: str) -> None:
        """@brief 追加唯一 run_failed，使后续故障覆盖先前完成候选。"""
        if not error:
            raise ProtocolError("Run failure error must not be empty")
        if self.context.store.has_event("run_failed"):
            return
        self.context.store.append_evidence([world_ops._failure_draft(self.context.state.current_time, error, None)])
        self.context.ops.log(self.context.state.current_time, "engine", "error",
                             {"code": "RUN_FAILED", "detail": f"Run failed: {error}"})

    def checkpoint(self, agent_snapshots: dict[str, AssetRef]) -> "Checkpoint":
        """@brief 合并全部人物和 life 快照后提交一个共同检查点。"""
        self.clock._require_midnight_decided()
        expected = {f"agent:{agent_id}" for agent_id in self.context.store.manifest["agent_ids"]}
        if set(agent_snapshots) != expected:
            raise IntegrityError("Checkpoint agent snapshot set is incomplete")
        receipt = self._life_snapshot()
        reference = self.context.store.assets.put_json(receipt, {"kind": "life_checkpoint"})
        snapshots = dict(agent_snapshots)
        snapshots["life"] = reference
        committed = self.context.store.checkpoint(self.context.state.model_dump(mode="json"), snapshots)
        self.context.ops.log(
            self.context.state.current_time,
            "checkpoint",
            "info",
            {"code": "CHECKPOINT_COMMITTED",
            "detail": f"Checkpoint {committed.checkpoint_id} committed",
            "event_seq_last": committed.last_event_seq},
        )
        return committed

    def close(self) -> None:
        """@brief 关闭当前运行的唯一存储连接。"""
        self.context.store.close()
        self.context.ops.close()

    def _life_snapshot(self) -> dict:
        """@brief 生成恢复时必须重放核对的 life 参与者收据。"""
        return {
            "schema_version": 1,
            "run_id": self.context.state.run_id,
            "state_version": self.context.state.version,
            "current_time": self.context.state.current_time,
            "persona_sha256": self.context.inputs.persona_sha256,
            "config_sha256": self.context.inputs.config_sha256,
        }
