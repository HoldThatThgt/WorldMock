"""@brief LifeEnv 使用的生活运行门面，职责与状态由显式对象承担。"""

from __future__ import annotations
from worldmock.errors import IntegrityError
from worldmock.life.state_commit import validated_payload
from worldmock.life import world_ops

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldmock.errors import ProtocolError
from worldmock.footprints.models import AssetRef, EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.counterfactual_runtime import CounterfactualRuntime
from worldmock.life.decision_coordinator import DecisionCoordinator
from worldmock.life.engine_context import EngineContext
from worldmock.life.model_evidence import ModelEvidenceRecorder
from worldmock.life.models import (
    ActionReceipt,
    ActionResult,
    CounterfactualIntervention,
    CounterfactualReceipt,
    PlanDayRequest,
    RecallTaskSummaryRequest,
    RunIdentity,
)
from worldmock.life.observe import ObservationBuilder
from worldmock.life.schedule import DayPlan
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.session_actions import ActionHandlers
from worldmock.life.storefront_runtime import StorefrontRuntime
from worldmock.life.time_advance import TimeAdvanceCoordinator
from worldmock.life.work_review_runtime import WorkReviewCoordinator
from worldmock.life.world_setup import create_world, open_world
from worldmock.life.world_ops import meal_windows_by_agent
from worldmock.life.continuation import CONTINUATION_KEY, same_second_chain
from worldmock.life.state_commit import commit_state

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState
    from worldmock.footprints.models import Checkpoint
    from worldmock.life import felt


class LifeEngine:
    """@brief 仅编排现有公开生活协议，不保存第二份世界或业务簿记。"""

    def __init__(self, semantics: ScenarioSemantics, scenario_sha256: str, corpus_root: Path, store: FootprintStore):
        """@brief 建立唯一上下文并绑定明确职责。"""
        self.context = EngineContext(semantics, scenario_sha256, corpus_root, store)
        self.observations = ObservationBuilder(self.context)
        self.actions = ActionHandlers(self.context, StorefrontRuntime(self.context))
        self.decisions = DecisionCoordinator(self.context, self.actions, self.observations)
        self.clock = TimeAdvanceCoordinator(self.context, self.observations)
        self.reviews = WorkReviewCoordinator(self.context, self.actions)
        self.evidence = ModelEvidenceRecorder(self.context, self.decisions)
        self.counterfactual = CounterfactualRuntime(self.context)

    @classmethod
    def create(
        cls,
        scenario_path: Path,
        run_dir: Path,
        run_identity: RunIdentity,
        stage_identity: dict | None = None,
        create_options: dict | None = None,
    ) -> "LifeEngine":
        """@brief 真实装配冻结输入并新建唯一运行。"""
        return cls(*create_world(scenario_path, run_dir, run_identity, stage_identity, create_options or {}))

    @classmethod
    def open(cls, scenario_path: Path, run_dir: Path) -> "LifeEngine":
        """@brief 重新核对实际输入并只打开已存在的运行。"""
        return cls(*open_world(scenario_path, run_dir))

    def observe(self, context: "DecisionContext") -> dict:
        """@brief 提交一次真实观察并附当前同秒续接。"""
        self.decisions.validate_context(context)
        data = self.observations._observation_data(self.context.state.agents[context.agent_id])
        stored = {key: value for key, value in data.items() if key not in {"progress_layer", "progress_meta"}}
        draft = EventDraft(
            event_type="observation",
            occurred_at=self.context.state.current_time,
            agent_id=context.agent_id,
            decision_id=context.decision_id,
            observed_at=self.context.state.current_time,
            data=stored,
        )
        data["observation_event_id"] = commit_state(self.context.store, self.context.state, [draft])[0].event_id
        data[CONTINUATION_KEY] = same_second_chain(
            self.context.store, self.context.state, self.observations, context.agent_id, data["observation_event_id"]
        )
        return data

    def begin_decision_round(self, contexts: list["DecisionContext"]) -> None:
        """@brief 按当前可恢复顺序开始决策轮。"""
        self.decisions.begin_decision_round(contexts, self.clock._ready_agent_ids())

    def submit_action(self, call: "DirectToolCall") -> ActionReceipt:
        """@brief 将真实宿主请求交给决策协调登记。"""
        return self.decisions.submit_action(call)

    async def prepare_pending_work_review(self, action_id: str) -> None:
        """@brief 为已登记的工作动作执行独立验收。"""
        pending = next((item for item in self.decisions.round.pending_actions if item.action_id == action_id), None)
        if pending is None:
            raise ProtocolError("Pending work review action does not exist")
        await self.reviews.prepare(pending)

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
        return meal_windows_by_agent(self.context.scenario)

    def fail_run(self, error: str) -> None:
        """@brief 追加唯一 run_failed，使后续故障覆盖先前完成候选。"""
        if not error:
            raise ProtocolError("Run failure error must not be empty")
        events = self.context.store.read_events(self.context.store.last_event_seq)
        if any((event.event_type == "run_failed" for event in events)):
            return
        commit_state(
            self.context.store, self.context.state,
            [world_ops._failure_draft(self.context.state.current_time, error, None)],
        )
        self.context.ops.log(self.context.state.current_time, "engine", "error", "RUN_FAILED", f"Run failed: {error}")

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
        committed = self.context.store.checkpoint(validated_payload(self.context.state), snapshots)
        self.context.ops.log(
            self.context.state.current_time,
            "checkpoint",
            "info",
            "CHECKPOINT_COMMITTED",
            f"Checkpoint {committed.checkpoint_id} committed",
            event_seq_last=committed.last_event_seq,
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
            "scenario_sha256": self.context.scenario_sha256,
            "corpus_manifest_sha256": self.context.scenario.corpus_manifest_sha256,
        }
