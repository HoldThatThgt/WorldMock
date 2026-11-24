"""@brief 全局时钟屏障协调器：准备并行唤醒快照、开放沙盒、按人物编号重放消解冲突并原子落盘。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from worldmock.foundation.errors import BusinessRuleError, ProtocolError
from worldmock.foundation.serialization import sha256_json
from worldmock.recording_engine.events.models import RESOURCE_FLOWS_FIELD, EventDraft
from worldmock.recording_engine.storage.observation_parts import insert_parts
from worldmock.simulation_engine.actions.dispatcher import ActionHandlers
from worldmock.simulation_engine.actions.start_group import ToolInvocation
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.activities.lifecycle.activities import schedule_feedback
from worldmock.simulation_engine.activities.search import search_activity
from worldmock.simulation_engine.activities.search.search_catalog import SearchCatalogView
from worldmock.simulation_engine.activities.travel.navigation_foreground import settle_navigation
from worldmock.simulation_engine.commerce.purchase.purchase_phases import foreground_events
from worldmock.simulation_engine.decisions.sandbox import (
    AgentSandbox,
    _has_active_search,
    _keep_agent_ready,
    _rejected_draft,
    execute_invocation,
    execute_start_group,
)
from worldmock.simulation_engine.evidence.actions import (
    ActionRecordSpec,
    _activity_terminal_result,
    _result_draft_with_causes,
)
from worldmock.simulation_engine.evidence.model import record_failed_evidence, validate_failed_evidence
from worldmock.simulation_engine.evidence.stays import stay_events
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder, layer_changes
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.layers.search_projection import search_details
from worldmock.simulation_engine.observation.targets.presentation import merge_presentation
from worldmock.simulation_engine.observation.wording.activity_titles import title_index
from worldmock.simulation_engine.observation.wording.guidance import rejection_guidance
from worldmock.simulation_engine.scheduling.callbacks.callbacks import compare_callback_states
from worldmock.simulation_engine.scheduling.clock.rhythm import consume_progress_report_opportunity, next_decision_at
from worldmock.simulation_engine.scheduling.clock.time_advance import due_search_activities
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import _put_activity, _put_agent
from worldmock.state.decisions.barrier import (
    AgentWakeupInput,
    BarrierCommitReceipt,
    SandboxOutcome,
    SearchCommitReceipt,
    SearchSessionOutcome,
    SearchTaskSpec,
)
from worldmock.state.decisions.results import ActionResult
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.world.kinds import ActionStatus, ActivityKind, ActivityStatus
from worldmock.state.world.world import WorldState, active_activities

_LOGGER = logging.getLogger(__name__)


class BarrierMerger:
    """@brief 管理同一全局时刻屏障的唤醒分配、隔离沙盒开启与多人物冲突顺序仲裁。"""

    def __init__(self, context: EngineContext, actions: ActionHandlers,
                 observations: ObservationBuilder, clock: Any) -> None:
        """@brief 绑定共享世界上下文、动作处理器、观察构建器与时钟节律协调器。"""
        self._context = context
        self._actions = actions
        self._observations = observations
        self._clock = clock
        self._staged_parts: dict[str, dict[str, str]] = {}

    def prepare_barrier(self, agent_ids: tuple[int, ...] | None = None) -> tuple[AgentWakeupInput, ...]:
        """@brief 不写库也不改变世界版本，为全部当前就绪人物装配只读唤醒快照。"""
        self._require_barrier_window()
        ready = self._ready_agent_ids()
        if not ready:
            _LOGGER.error("No ready agents at current time for barrier preparation")
            raise ProtocolError("No agents are ready for barrier")
        if agent_ids is not None and tuple(agent_ids) != ready:
            _LOGGER.error("Requested barrier agents %s differ from ready agents %s", agent_ids, ready)
            raise ProtocolError("Barrier agent_ids must match scheduled ready agents")
        wakeups = tuple(self._build_wakeup(aid) for aid in ready)
        for wakeup in wakeups:
            self._staged_parts[wakeup.wakeup_id] = dict(wakeup.observation_parts)
        return wakeups

    def open_sandbox(self, wakeup: AgentWakeupInput) -> AgentSandbox:
        """@brief 按给定唤醒快照为单个人物开启隔离内存沙盒。"""
        if wakeup.world_state_version != self._context.state.version:
            _LOGGER.error("Wakeup version %d disagrees with world version %d",
                          wakeup.world_state_version, self._context.state.version)
            raise ProtocolError("Wakeup world state version mismatch")
        if wakeup.observed_at != self._context.state.current_time:
            _LOGGER.error("Wakeup time disagrees with current simulation time")
            raise ProtocolError("Wakeup time mismatch")
        self._staged_parts[wakeup.wakeup_id] = dict(wakeup.observation_parts)
        return AgentSandbox(self._context, self._actions, self._observations, wakeup)

    def commit_barrier(self, outcomes: Sequence[SandboxOutcome]) -> BarrierCommitReceipt:
        """@brief 按 agent_id 升序重放并仲裁各人物沙盒操作，单事务原子落盘并返回提交回执。"""
        self._require_barrier_window()
        ordered = self._validate_outcomes(outcomes)
        staged = self._context.state.model_copy(deep=True)
        drafts: list[EventDraft] = []
        parts: dict[str, str] = {}
        committed: list[int] = []
        conflicts: list[int] = []
        for outcome in ordered:
            self._staged_parts.pop(outcome.wakeup_id, None)
            parts.update(outcome.observation_parts)
            staged, ag_drafts, ok = self._merge_one_outcome(staged, outcome)
            drafts.extend(ag_drafts)
            (committed if ok else conflicts).append(outcome.agent_id)
        staged, all_drafts = self._finalize_barrier(staged, drafts)
        with self._context.store.connection:
            if parts:
                insert_parts(self._context.store.connection, parts)
            self._context.commit_step(staged, all_drafts)
        self._write_commit_logs(staged.current_time, committed, conflicts)
        return BarrierCommitReceipt(
            committed_agent_ids=tuple(committed), conflict_requeued_agent_ids=tuple(conflicts),
            world_state_version=staged.version, event_seq_last=self._context.store.last_event_seq,
        )

    def due_searches(self) -> tuple[tuple[SearchTaskSpec, SearchCatalogView], ...]:
        """@brief 返回所有已到计划终点、尚在进行中的搜索子任务及其只读目录视图。"""
        due = due_search_activities(self._context.state)
        return tuple(search_activity.build_search_task(self._context.inputs, self._context.state, item)
                     for item in due)

    def complete_searches(self, outcomes: Sequence[SearchSessionOutcome]) -> SearchCommitReceipt:
        """@brief 在主线程同步完成 Fact Guard 核对、属性归因与副槽位释放。"""
        due = due_search_activities(self._context.state)
        return search_activity.complete_searches(self._context, due, outcomes)

    def record_failed_evidence(self, agent_id: int, wakeup_id: str, drafts: Sequence[EventDraft]) -> None:
        """@brief 在并发异常中止前将人物尚在沙盒中的纯模型及唤醒草稿补写入库。"""
        checked = validate_failed_evidence(agent_id, wakeup_id, drafts)
        parts = self._staged_parts.pop(wakeup_id, None)
        if checked and parts and any(draft.event_type == "observation" for draft in checked):
            with self._context.store.connection:
                insert_parts(self._context.store.connection, parts)
                record_failed_evidence(self._context, agent_id, wakeup_id, checked)
            return
        record_failed_evidence(self._context, agent_id, wakeup_id, checked)

    def _require_barrier_window(self) -> None:
        """@brief 禁止在零点未决或有到期搜索未收尾时准备或提交人物唤醒屏障。"""
        if self._clock.midnight_rhythm_pending:
            _LOGGER.error("Barrier rejected while midnight rhythm is pending")
            raise ProtocolError("Midnight rhythm is pending")
        if due_search_activities(self._context.state):
            _LOGGER.error("Barrier rejected while due searches remain incomplete")
            raise ProtocolError("Due searches must complete before decision barrier")

    def _ready_agent_ids(self) -> tuple[int, ...]:
        """@brief 按人物编号升序筛选当前时刻就绪可唤醒的人物。"""
        state = self._context.state
        if state.current_time >= state.end_time:
            return ()
        return tuple(
            aid for aid in sorted(state.agents)
            if state.agents[aid].next_decision_at <= state.current_time
        )

    def _build_wakeup(self, agent_id: int) -> AgentWakeupInput:
        """@brief 为单个人物生成常规唤醒或搜索恢复唤醒的只读输入快照。"""
        state = self._context.state
        agent = state.agents[agent_id]
        obs, parts, presentation = self._observations.build_wakeup_observation(agent)
        obs["observation_event_id"] = uuid4().hex
        loop = agent.suspended_loop
        search_act = None if loop is None else state.activities.get(loop.activity_id)
        if loop is None or (search_act is not None and search_act.status == ActivityStatus.ACTIVE):
            wakeup_id = f"{state.run_id}:wake:{state.current_time}:v{state.version}:a{agent_id}"
            return AgentWakeupInput(
                wakeup_id=wakeup_id, agent_id=agent_id, observed_at=state.current_time,
                world_state_version=state.version, mode="regular", observation=obs,
                observation_parts=parts, pending_presentation=presentation, action_seq_start=1,
            )
        if search_act is None or search_act.status != ActivityStatus.COMPLETED:
            _LOGGER.error("Agent %d resume_search lacks completed search activity %s", agent_id, loop.activity_id)
            raise ProtocolError("Resumed search requires completed search activity")
        payload = _resume_search_payload(self._context, search_act, loop, obs)
        seq = _next_action_seq(loop)
        return AgentWakeupInput(
            wakeup_id=loop.wakeup_id, agent_id=agent_id, observed_at=state.current_time,
            world_state_version=state.version, mode="resume_search", observation=obs,
            observation_parts=parts, pending_presentation=presentation, action_seq_start=seq,
            suspended_loop=loop, search_result_payload=payload,
        )

    def _validate_outcomes(self, outcomes: Sequence[SandboxOutcome]) -> list[SandboxOutcome]:
        """@brief 核验屏障提交包非空、无重复人物且与当前就绪人物一致，并按 agent_id 升序排列。"""
        if not outcomes:
            _LOGGER.error("Barrier commit received no outcomes")
            raise ProtocolError("Barrier commit requires at least one outcome")
        seen: set[int] = set()
        ready = set(self._ready_agent_ids())
        for outcome in outcomes:
            if outcome.agent_id in seen:
                _LOGGER.error("Duplicate agent %d in barrier commit", outcome.agent_id)
                raise ProtocolError("Duplicate agent in barrier commit")
            if outcome.agent_id not in ready:
                _LOGGER.error("Outcome agent %d is not scheduled in current barrier", outcome.agent_id)
                raise ProtocolError("Outcome agent is not scheduled in barrier")
            seen.add(outcome.agent_id)
        return sorted(outcomes, key=lambda item: item.agent_id)

    def _merge_one_outcome(self, staged: WorldState,
                           outcome: SandboxOutcome) -> tuple[WorldState, list[EventDraft], bool]:
        """@brief 将单个人物的沙盒操作顺序重放并应用到 staged，发生跨人物冲突时仅回滚本人编辑。"""
        before = staged.model_copy(deep=True)
        prelude, direct_drafts = _partition_drafts(outcome)
        self._actions.drain_causes()
        try:
            if outcome.action_operations:
                staged, op_drafts = self._replay_agent_ops(staged, outcome)
                all_agent_drafts = [*prelude, *op_drafts]
            else:
                staged = self._apply_direct_state(staged, outcome)
                all_agent_drafts = list(direct_drafts)
        except BusinessRuleError as conflict:
            self._actions.drain_causes()
            self._actions.drain_drafts()
            conflict_evts = _conflict_drafts(self._context, before, outcome, conflict)
            return _conflict_rollback(before, outcome.agent_id), [*prelude, *conflict_evts], False
        self._context.callback_causes.update(self._actions.drain_causes())
        if outcome.action_operations:
            staged = _apply_prelude_relaxation(staged, outcome.agent_id, prelude)
        staged = self._finish_committed_agent(staged, outcome)
        return staged, all_agent_drafts, True

    def _replay_agent_ops(self, staged: WorldState,
                          outcome: SandboxOutcome) -> tuple[WorldState, list[EventDraft]]:
        """@brief 在最新共享状态 staged 上按成组开始或单条次序重放单个人物的所有已受理世界操作。"""
        op_drafts: list[EventDraft] = []
        ops, idx = outcome.action_operations, 0
        while idx < len(ops):
            if _is_start_pair(ops, idx):
                pair = ops[idx:idx + 2]
                reqs_invs = [_op_request(staged.current_time, outcome, item) for item in pair]
                ids = [(item["accepted_event_id"], item["result_event_id"]) for item in pair]
                staged, _, emitted = execute_start_group(
                    self._context, self._actions, staged, [inv for _, inv in reqs_invs], ids,
                )
                op_drafts.extend([*(draft for draft, _ in reqs_invs), *emitted])
                idx += 2
            else:
                op = ops[idx]
                req_draft, inv = _op_request(staged.current_time, outcome, op)
                staged, _, emitted = execute_invocation(
                    self._context, self._actions, staged, inv, (op["accepted_event_id"], op["result_event_id"]),
                )
                op_drafts.extend([req_draft, *emitted])
                idx += 1
        return staged, op_drafts

    def _apply_direct_state(self, staged: WorldState, outcome: SandboxOutcome) -> WorldState:
        """@brief 无操作列表的纯状态产出路径：按人物孤立复制对应人物更新与新活动记录。"""
        aid = outcome.agent_id
        changed = _put_agent(staged, outcome.state.agents[aid].model_copy(deep=True))
        for key, act in outcome.state.activities.items():
            if act.agent_id == aid and key not in self._context.state.activities:
                changed = _put_activity(changed, changed.agents[aid], act.model_copy(deep=True))
        return changed

    def _finish_committed_agent(self, staged: WorldState, outcome: SandboxOutcome) -> WorldState:
        """@brief 为无冲突人物更新挂起/完成态、消费播报与反馈时机及产品曝光参照。"""
        aid = outcome.agent_id
        staged = consume_progress_report_opportunity(staged, aid, self._context.config.progress_report_interval_seconds)
        for act in staged.activities.values():
            if act.agent_id == aid and act.status == ActivityStatus.ACTIVE and (
                act.next_interaction_at is not None and act.next_interaction_at <= staged.current_time
            ):
                staged.activities[act.activity_id] = schedule_feedback(
                    act, staged.current_time, self._context.config.activity_feedback_seconds)
        staged = self._observations.snapshot_decision_references(staged, [aid])
        agent = staged.agents[aid]
        if outcome.status == "suspended":
            loop = outcome.state.agents[aid].suspended_loop
            agent = agent.model_copy(update={"suspended_loop": loop, "pending_wake_reasons": []}, deep=True)
            return _put_agent(staged, agent.model_copy(update={
                "next_decision_at": next_decision_at(self._context, staged, agent)}))
        keep_loop = agent.suspended_loop if _has_active_search(staged, aid) else None
        agent = agent.model_copy(update={"suspended_loop": keep_loop, "pending_wake_reasons": []}, deep=True)
        next_at = (
            staged.current_time
            if _keep_agent_ready(staged, outcome.action_operations, outcome.evidence_drafts, aid)
            else next_decision_at(self._context, staged, agent)
        )
        staged = _put_agent(staged, agent.model_copy(update={"next_decision_at": next_at}))
        if outcome.pending_presentation:
            staged = merge_presentation(staged, {aid: outcome.pending_presentation})
        return staged

    def _finalize_barrier(self, staged: WorldState, drafts: list[EventDraft]) -> tuple[WorldState, list[EventDraft]]:
        """@brief 在屏障提交末尾统一结算个人事务、阈下回调、导航前台、购买前台与地点停留事件。"""
        staged, errand_drafts = errands.settle(staged, drafts)
        drafts.extend(errand_drafts)
        cb = compare_callback_states(self._context.state, staged, self._context.catalog,
                                     drafts, self._context.callback_causes)
        staged, nav = settle_navigation(self._context.state, cb.state, self._context.catalog)
        all_drafts = [*drafts, *cb.drafts, *nav, *foreground_events(self._context.state, staged),
                      *stay_events(self._context.state, staged, drafts)]
        staged.version = self._context.state.version + 1
        return staged, all_drafts

    def _write_commit_logs(self, moment: int, committed: list[int], conflicts: list[int]) -> None:
        """@brief 记录本次屏障提交的运维日志。"""
        self._context.ops.log(
            moment, "barrier", "info",
            {"code": "BARRIER_COMMITTED",
             "detail": f"Barrier committed agents={committed} conflicts={conflicts}",
             "committed_agent_ids": committed, "conflict_requeued_agent_ids": conflicts},
        )


def _resume_search_payload(context: EngineContext, act: Any, loop: SuspendedLoopState,
                           obs: dict[str, Any]) -> dict[str, Any]:
    """@brief 为搜索到期后恢复原对话构造包含 Fact Guard 报价、详情与观察层差量的工具结果载荷。"""
    index = title_index(context.catalog)
    term = _activity_terminal_result(context.state, act, act.ended_at or context.state.current_time, index)
    base_map = {key: value for row in loop.base_layers for key, value in row.items()}
    return {
        "activity_id": act.activity_id,
        "result": public_result(term.model_dump(mode="json"), context.inputs, {}),
        "search": search_details(act.model_dump(mode="json"), context.inputs),
        "changes": layer_changes(base_map, obs),
        "events": [],
    }


def _next_action_seq(loop: SuspendedLoopState) -> int:
    """@brief 计算搜索恢复后不会与挂起前动作冲突的下一连续动作序号。"""
    seqs = [len(loop.accepted_action_ids)]
    for cid in (loop.action_id, *loop.accepted_action_ids):
        tail = cid.rsplit(":", 1)[-1]
        if tail.isascii() and tail.isdigit():
            seqs.append(int(tail))
    return max(seqs) + 1


def _is_start_pair(ops: tuple[dict[str, Any], ...], index: int) -> bool:
    """@brief 判断当前位置是否为同一回合相邻的两个不同开始工具操作。"""
    if index + 1 >= len(ops):
        return False
    first, second = ops[index]["tool_name"], ops[index + 1]["tool_name"]
    return first in START_TOOLS and second in START_TOOLS and first != second


def _op_request(occurred_at: int, outcome: SandboxOutcome, op: dict[str, Any]) -> tuple[EventDraft, ToolInvocation]:
    """@brief 由待重放操作描述重建 action_requested 草稿与 ToolInvocation。"""
    req_sha = sha256_json({"tool_name": op["tool_name"], "request": op["request"]})
    req_data: dict[str, Any] = {"tool_name": op["tool_name"], "request": op["request"], "request_sha256": req_sha}
    if op.get("note") is not None:
        req_data["self_note"] = op["note"]
    req_draft = EventDraft(
        event_id=op["requested_event_id"], event_type="action_requested", occurred_at=occurred_at,
        agent_id=outcome.agent_id, wakeup_id=outcome.wakeup_id, action_id=op["action_id"],
        caused_by=[outcome.observation_event_id], visibility="agent", source_kind="model", data=req_data,
    )
    inv = ToolInvocation(outcome.agent_id, outcome.wakeup_id, op["action_id"], op["tool_name"],
                         op["request"], req_sha, req_draft.event_id, op["observation"], op.get("note"))
    return req_draft, inv


def _partition_drafts(outcome: SandboxOutcome) -> tuple[list[EventDraft], tuple[EventDraft, ...]]:
    """@brief 将沙盒证据草稿拆分为重放前保留的证据及本地已拒绝记录，与全量原始草稿。"""
    op_ids = {op["requested_event_id"] for op in outcome.action_operations}
    op_ids.update(op["accepted_event_id"] for op in outcome.action_operations)
    op_ids.update(op["result_event_id"] for op in outcome.action_operations)
    kept: list[EventDraft] = []
    for draft in outcome.evidence_drafts:
        if draft.event_id in op_ids or draft.event_type in {"environment_event", "action_accepted"}:
            continue
        if draft.event_type == "action_result" and draft.data.get("status") != ActionStatus.REJECTED.value:
            continue
        kept.append(draft)
    return kept, outcome.evidence_drafts


def _conflict_drafts(
    context: EngineContext,
    state: WorldState,
    outcome: SandboxOutcome,
    conflict: BusinessRuleError,
) -> list[EventDraft]:
    """@brief 为因跨人物资源冲突被回滚的操作补写行动请求、行动拒绝与反馈结果草稿。"""
    guidance = rejection_guidance(conflict)
    emitted: list[EventDraft] = []
    now = outcome.observation_payload.get("current_time", outcome.state.current_time)
    ops = outcome.action_operations
    paired = len(ops) == 2 and {op["tool_name"] for op in ops} == set(START_TOOLS)
    group_causes = [op["requested_event_id"] for op in ops]
    for op in ops:
        req_draft, inv = _op_request(now, outcome, op)
        spec = ActionRecordSpec(outcome.wakeup_id, inv.action_id, inv.tool_name,
                                inv.request_sha256, req_draft.event_id)
        result = ActionResult(
            action_id=inv.action_id, agent_id=outcome.agent_id, status=ActionStatus.REJECTED,
            occurred_at=now, message=guidance["reason"], error_code=str(conflict), data=guidance,
        )
        causes = group_causes if paired else [req_draft.event_id]
        emitted.extend([req_draft, _rejected_draft(context, state, spec, (result, guidance, causes)),
                        _result_draft_with_causes(spec, result, "action_result", causes)])
    return emitted


def _conflict_rollback(before: WorldState, agent_id: int) -> WorldState:
    """@brief 回滚冲突人物的世界更改，清除挂起状态并安排同一刻带拒绝反馈重入。"""
    agent = before.agents[agent_id]
    reasons = list(agent.pending_wake_reasons)
    if "rejection_feedback" not in reasons:
        reasons.append("rejection_feedback")
    updated = agent.model_copy(
        update={"suspended_loop": None, "next_decision_at": before.current_time, "pending_wake_reasons": reasons},
        deep=True,
    )
    return _put_agent(before, updated)


def _apply_prelude_relaxation(staged: WorldState, agent_id: int, drafts: Sequence[EventDraft]) -> WorldState:
    """@brief 在重放已受理操作后同步保留同唤醒前期被拒动作产生的放松需要脉冲差额。"""
    delta = sum(
        float(flow["amount"])
        for draft in drafts
        if draft.event_type == "action_result"
        for flow in draft.data.get(RESOURCE_FLOWS_FIELD, ())
        if flow.get("resource") == "relaxation_need"
    )
    if not delta:
        return staged
    agent = staged.agents[agent_id]
    body = agent.body.model_copy(update={"relaxation_need": min(1.0, max(0.0, agent.body.relaxation_need + delta))})
    return _put_agent(staged, agent.model_copy(update={"body": body}, deep=True))
