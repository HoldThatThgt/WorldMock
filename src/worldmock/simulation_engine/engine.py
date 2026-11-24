"""@brief 唯一生活运行模拟引擎门面：同步方法、沙盒与时钟屏障编排，不保存第二份世界或业务簿记。"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.recording_engine.events.models import AssetRef, EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.actions.dispatcher import ActionHandlers
from worldmock.simulation_engine.activities.search.search_catalog import SearchCatalogView
from worldmock.simulation_engine.commerce.purchase.storefront_runtime import StorefrontRuntime
from worldmock.simulation_engine.decisions.merger import BarrierMerger
from worldmock.simulation_engine.decisions.sandbox import AgentSandbox
from worldmock.simulation_engine.evidence.memory_digest import DigestOutcome
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.simulation_engine.observation.memory import search
from worldmock.simulation_engine.scheduling.clock.time_advance import TimeAdvanceCoordinator
from worldmock.simulation_engine.world import world_updates as world_ops
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.counterfactual import CounterfactualRuntime
from worldmock.simulation_engine.world.world_setup import create_world, open_world
from worldmock.simulation_engine.world.world_updates import meal_windows_by_agent
from worldmock.state.decisions.barrier import (
    AgentWakeupInput,
    BarrierCommitReceipt,
    SandboxOutcome,
    SearchCommitReceipt,
    SearchSessionOutcome,
    SearchTaskSpec,
)
from worldmock.state.decisions.results import ActionResult, CounterfactualIntervention, CounterfactualReceipt
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.identity import ModelIdentity, RunIdentity
from worldmock.state.interaction.requests import PlanDayRequest, RecallTaskSummaryRequest, SearchMemoryRequest
from worldmock.state.planning.schedule import DayPlan

if TYPE_CHECKING:
    from worldmock.recording_engine.events.models import Checkpoint
    from worldmock.simulation_engine.body import felt

_LOGGER = logging.getLogger(__name__)


class LifeEngine:
    """@brief 仅编排公开离散时钟屏障、沙盒与搜索会话接口的同步引擎门面。"""

    def __init__(self, inputs: EnvironmentInputs, store: FootprintStore) -> None:
        """@brief 建立唯一共享上下文并装配六个无冗余职责协作者。"""
        self.context = EngineContext(inputs, store)
        self.observations = ObservationBuilder(self.context)
        self.actions = ActionHandlers(self.context, StorefrontRuntime(self.context))
        self.clock = TimeAdvanceCoordinator(self.context, self.observations)
        self.barriers = BarrierMerger(self.context, self.actions, self.observations, self.clock)
        self.counterfactual = CounterfactualRuntime(self.context)

    @classmethod
    def create(
        cls,
        environment: EnvironmentInputs | Path,
        run_dir: Path,
        identity: RunIdentity,
        model_identity: ModelIdentity | Mapping[str, Any],
        options: dict | None = None,
    ) -> "LifeEngine":
        """@brief 核验多人物冻结环境与 v8 模型身份，建立全新运行。"""
        return cls(*create_world(environment, run_dir, identity, model_identity, options or {}))

    @classmethod
    def open(cls, environment: EnvironmentInputs | Path, run_dir: Path) -> "LifeEngine":
        """@brief 重新核对冻结环境与输入摘要并打开已有运行。"""
        return cls(*open_world(environment, run_dir))

    def scheduling_state(self) -> SchedulingState:
        """@brief 返回带时区当前时刻、按编号升序的就绪人物和下一事件边界。"""
        return self.clock.scheduling_state()

    def advance(self, tick: int, target: datetime) -> list[ActionResult]:
        """@brief 推进共享离散时钟至下一事件边界并结算活动、后台触发与节律。"""
        return self.clock.advance(tick, target)

    def prepare_barrier(self, agent_ids: tuple[int, ...] | None = None) -> tuple[AgentWakeupInput, ...]:
        """@brief 校验就绪人物列表并只读构造同刻并发唤醒所需的全部 AgentWakeupInput。"""
        return self.barriers.prepare_barrier(agent_ids)

    def open_sandbox(self, wakeup: AgentWakeupInput) -> AgentSandbox:
        """@brief 按给定唤醒快照为单个人物开启隔离状态沙盒句柄。"""
        return self.barriers.open_sandbox(wakeup)

    def commit_barrier(self, outcomes: Sequence[SandboxOutcome]) -> BarrierCommitReceipt:
        """@brief 按 agent_id 升序仲裁并发沙盒结果、回滚冲突者并单事务提交足迹。"""
        return self.barriers.commit_barrier(outcomes)

    def due_searches(self) -> tuple[tuple[SearchTaskSpec, SearchCatalogView], ...]:
        """@brief 返回所有达到计划终点的搜索子任务及其只读目录视图。"""
        return self.barriers.due_searches()

    def complete_searches(self, outcomes: Sequence[SearchSessionOutcome]) -> SearchCommitReceipt:
        """@brief 按 Fact Guard 复核到期搜索成果、归因应用并恢复人物待唤醒状态。"""
        return self.barriers.complete_searches(outcomes)

    def record_failed_evidence(self, agent_id: int, wakeup_id: str, drafts: Sequence[EventDraft]) -> None:
        """@brief 并发唤醒故障中止前补写未落盘的纯模型与唤醒证据。"""
        self.barriers.record_failed_evidence(agent_id, wakeup_id, drafts)

    @property
    def midnight_rhythm_pending(self) -> bool:
        """@brief 零点边界之后是否尚待完成日计划与回顾判定。"""
        return self.clock.midnight_rhythm_pending

    def decide_midnight_rhythm(self, decision: Any = None) -> None:
        """@brief 在零点计划与回顾登记后完成零点节律提交。"""
        self.clock.decide_midnight_rhythm(decision)

    def register_day_plan(
        self, agent_id: int, request: PlanDayRequest, sources: list[str], evidence: list[EventDraft],
    ) -> DayPlan:
        """@brief 为指定人物登记当日计划并同事务保存其模型证据。"""
        return self.clock.register_day_plan(agent_id, request, sources, evidence)

    def register_memory_digest(self, agent_id: int, outcome: DigestOutcome, evidence: list[EventDraft]) -> None:
        """@brief 为指定人物登记一期回顾并同事务保存其模型证据。"""
        self.clock.register_memory_digest(agent_id, outcome, evidence)

    def read_task_summary(self, agent_id: int, request: RecallTaskSummaryRequest,
                          observed: dict[str, Any]) -> dict[str, Any]:
        """@brief 基于已提交观察只读回看指定人物的活动或目标进展。"""
        return copy.deepcopy(self.actions.read_task_summary(agent_id, request, observed))

    def search_memory(self, agent_id: int, request: SearchMemoryRequest,
                      cutoff_seq: int | None = None) -> dict[str, Any]:
        """@brief 按指定人物与事件序号上界只读检索历史记忆。"""
        return copy.deepcopy(search.search_memory(self.context, agent_id, request, cutoff_seq))

    def apply_counterfactual(self, intervention: CounterfactualIntervention) -> CounterfactualReceipt:
        """@brief 在开放分支上施加严格反事实干预并清除全员挂起循环。"""
        return self.counterfactual.apply_counterfactual(intervention)

    def meal_windows_by_agent(self) -> dict[int, "felt.MealWindows"]:
        """@brief 返回全员冻结人物声明的用餐时间窗口映射。"""
        return meal_windows_by_agent(self.context.inputs.personas)

    def fail_run(self, reason: str, code: str | None = None) -> None:
        """@brief 追加唯一运行故障事件并写入系统错误运维日志。"""
        del code
        if not reason:
            _LOGGER.error("Run failure error must not be empty")
            raise ProtocolError("Run failure error must not be empty")
        if self.context.store.has_event("run_failed"):
            return
        self.context.store.append_evidence([world_ops._failure_draft(self.context.state.current_time, reason)])
        self.context.ops.log(
            self.context.state.current_time, "engine", "error",
            {"code": "RUN_FAILED", "detail": f"Run failed: {reason}"},
        )

    def checkpoint(self, agent_snapshots: dict[str, AssetRef]) -> "Checkpoint":
        """@brief 校验全员工作区快照完整后生成并提交共同检查点。"""
        self.clock._require_midnight_decided()
        expected = {f"agent:{agent_id}" for agent_id in self.context.store.manifest["agent_ids"]}
        if set(agent_snapshots) != expected:
            _LOGGER.error("Checkpoint agent snapshot set %s mismatches %s", sorted(agent_snapshots), sorted(expected))
            raise IntegrityError("Checkpoint agent snapshot set is incomplete")
        receipt = self._life_snapshot()
        reference = self.context.store.assets.put_json(receipt, {"kind": "life_checkpoint"})
        snapshots = dict(agent_snapshots)
        snapshots["life"] = reference
        committed = self.context.store.checkpoint(self.context.state.model_dump(mode="json"), snapshots)
        self.context.ops.log(
            self.context.state.current_time, "checkpoint", "info",
            {"code": "CHECKPOINT_COMMITTED", "detail": f"Checkpoint {committed.checkpoint_id} committed",
             "event_seq_last": committed.last_event_seq},
        )
        return committed

    def close(self) -> None:
        """@brief 关闭唯一运行存储连接与运维日志句柄。"""
        self.context.store.close()
        self.context.ops.close()

    def _life_snapshot(self) -> dict[str, Any]:
        """@brief 生成检查点与重放核对的引擎多人物摘要收据。"""
        return {
            "schema_version": 2,
            "run_id": self.context.state.run_id,
            "state_version": self.context.state.version,
            "current_time": self.context.state.current_time,
            "agent_ids": sorted(self.context.state.agents),
            "persona_sha256s": {str(k): v for k, v in sorted(self.context.inputs.persona_sha256s.items())},
            "config_sha256": self.context.inputs.config_sha256,
        }
