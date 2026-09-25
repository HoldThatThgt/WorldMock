"""@brief 同刻决策：同一模拟秒内的轮数、受限轮阶梯、本轮唤醒原因、受限轮复核、拒绝脉冲与消息机会。

依据 docs/dev/SPEC-time-and-energy.md 第一章「规则」与「实现约定」：决策不消耗模拟时间；没有进行中活动的人物在同一秒
再决策（规则 2）；同一人物同一秒的决策轮数由场景 max_same_moment_decisions 封顶，达到上限后每个受限轮只开放一类
动作（规则 3）；有进行中活动时瞬时动作成功后按决策节律，业务拒绝后未达上限在同一秒再给一次机会（规则 4）；同刻轮次的
唤醒原因按本轮替换（规则 6）；受理、业务拒绝与重放各计一轮（规则 9），时钟推进时轮数与阶段归零；人物消息送达时清醒且
未达上限的收件人在同一秒获得机会，决策轮观察展示过的消息随本轮提交记为已读（「人物之间的消息」）。引擎只接线，
计数与阶梯都在本模块。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.life.models import (ActionStatus, ActivityKind, AgentState, SameMomentStage, WorldState,
                                   active_activities)
from worldmock.life.rhythm import read_new_messages
from worldmock.life.schedule import WakeReason

#: 业务拒绝对应的情绪脉冲键：工作返工单独计，两种品类配对说明不施加脉冲，其余都是服务拒绝。
SERVICE_REFUSED = "service_refused"
_REWORK_CODE = "WORK_REQUIRES_REWORK"
_NO_PULSE_CODES = frozenset({"CATEGORY_PAIRING_NO_EFFECT", "CATEGORY_PAIRING_UNDEFINED"})

#: 各阶段接受的请求类（引擎按请求派生 start、resume、wait、other）；未列出的阶段在本阶段没有规则，一律越界。
_STAGE_REQUEST_KINDS = {
    SameMomentStage.OPEN: frozenset({"start", "resume", "wait", "other"}),
    SameMomentStage.START_OR_RESUME: frozenset({"start", "resume", "wait"}),
    SameMomentStage.WAIT_ONLY: frozenset({"wait"}),
}

#: 达到上限且本轮结束时仍没有进行中活动时的下一阶段：先开放开始或恢复，开始或恢复未成则只开放等待。
_LADDER = {
    SameMomentStage.OPEN: SameMomentStage.START_OR_RESUME,
    SameMomentStage.START_OR_RESUME: SameMomentStage.WAIT_ONLY,
    SameMomentStage.WAIT_ONLY: SameMomentStage.WAIT_ONLY,
}


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """@brief 一轮结算后的同刻排期输入。

    :param now: 当前模拟时刻（epoch 秒）
    :param agent: 已计入本轮的人物状态
    :param active: 本轮结算后人物是否有进行中活动
    :param rejected: 本轮是否为业务拒绝（重放按存档结果）
    :param limit: 场景的同刻决策上限
    """

    now: int
    agent: AgentState
    active: bool
    rejected: bool
    limit: int


def count_round(agent: AgentState) -> AgentState:
    """@brief 受理、业务拒绝与重放各使本秒的同刻轮数加一（第一章「实现约定」）。"""
    return agent.model_copy(update={"same_moment_rounds": agent.same_moment_rounds + 1}, deep=True)


def round_wake_reasons(active: bool, rejected: bool) -> list[str]:
    """@brief 同刻再决策轮的本轮唤醒原因（规则 6）：没有进行中活动记 no_active_activity，业务拒绝记 rejection_feedback。

    两者同时成立时按 WakeReason 枚举顺序记两项；上一轮原因不带入。
    """
    hits = set()
    if rejected:
        hits.add(WakeReason.REJECTION_FEEDBACK)
    if not active:
        hits.add(WakeReason.NO_ACTIVE_ACTIVITY)
    return [reason.value for reason in WakeReason if reason in hits]


def next_stage(agent: AgentState, active: bool, limit: int) -> SameMomentStage:
    """@brief 本轮之后的受限轮阶段（规则 3）。

    未达上限保持 open；达到上限而本轮后仍没有进行中活动时按阶梯推进（open→start_or_resume→wait_only，
    wait_only 保持）；有进行中活动时不再进入受限轮，阶段保持不变，直到时钟推进复位。
    """
    if agent.same_moment_rounds < limit:
        return SameMomentStage.OPEN
    if active:
        return agent.same_moment_stage
    return _LADDER.get(agent.same_moment_stage, SameMomentStage.WAIT_ONLY)


def schedule_after_round(outcome: RoundOutcome,
                         rhythm_next: Callable[[], int]) -> tuple[int, list[str], SameMomentStage]:
    """@brief 安排本轮之后的下一次决策，返回 (next_decision_at, pending_wake_reasons, same_moment_stage)。

    没有进行中活动，或有进行中活动而业务拒绝且未达上限：同一秒再决策，原因按本轮替换（规则 2、4、6）；
    其余情形（有进行中活动时的成功，或达到上限的拒绝）按决策节律，只在此时调用 rhythm_next，原因清空。
    """
    stage = next_stage(outcome.agent, outcome.active, outcome.limit)
    below_limit = outcome.agent.same_moment_rounds < outcome.limit
    if not outcome.active or outcome.rejected and below_limit:
        return outcome.now, round_wake_reasons(outcome.active, outcome.rejected), stage
    return rhythm_next(), [], stage


def require_stage_action(stage: SameMomentStage, request_kind: str) -> None:
    """@brief 受限轮复核（第一章「实现约定」）：请求类不属于本阶段开放的动作类即业务拒绝。

    :param stage: 人物当前的受限轮阶段
    :param request_kind: 引擎派生的请求类：start（开始持续活动）、resume（恢复暂停活动）、wait（只等待）、other
    :raises BusinessRuleError: SAME_MOMENT_ACTION_RESTRICTED，计入同刻轮数
    """
    if request_kind not in _STAGE_REQUEST_KINDS.get(stage, frozenset()):
        raise BusinessRuleError("SAME_MOMENT_ACTION_RESTRICTED")


def refusal_pulse_key(error_code: str) -> str | None:
    """@brief 业务拒绝的情绪脉冲键：返工记 work_review_returned，品类配对说明不施加，其余记 service_refused。"""
    if error_code == _REWORK_CODE:
        return "work_review_returned"
    if error_code in _NO_PULSE_CODES:
        return None
    return SERVICE_REFUSED


def refusal_pulse_applied(state: WorldState, agent_id: int) -> bool:
    """@brief 本人在当前模拟秒是否已施加过 service_refused 脉冲（「拒绝的情绪代价与重估间隔」：每人每秒至多一次）。

    依据已存的动作结果判定：本人、发生于当前时刻、状态为拒绝且错误码的脉冲键为 service_refused；不另设世界字段。
    """
    return any(
        stored.result is not None
        and stored.result.get("agent_id") == agent_id
        and stored.result.get("occurred_at") == state.current_time
        and stored.result.get("status") == ActionStatus.REJECTED.value
        and refusal_pulse_key(str(stored.result.get("error_code"))) == SERVICE_REFUSED
        for stored in state.processed_actions.values())


def reset_same_moment(state: WorldState) -> WorldState:
    """@brief 时钟推进时每个人物的同刻轮数归零、受限轮阶段复位为 open（第一章「实现约定」）。"""
    agents = {agent_id: agent.model_copy(update={"same_moment_rounds": 0,
                                                 "same_moment_stage": SameMomentStage.OPEN}, deep=True)
              for agent_id, agent in state.agents.items()}
    return state.model_copy(update={"agents": agents}, deep=True)


def read_observed_messages(state: WorldState, observed: WorldState, agent_ids: Iterable[int]) -> WorldState:
    """@brief 决策轮提交时，把本轮观察展示过的消息记为已读（第一章「人物之间的消息」）。

    本轮观察取自轮前状态 observed：只读其中已在收件箱的消息，看过的消息不在其后的边界再以 message_arrival 唤醒；
    本轮内送达的消息仍未读，由 merge_message_readiness 给同秒机会，或在下一边界提供。受限轮（轮前阶段不是 open）
    回应不了消息，不记已读：已达同刻上限的收件人照常在下一边界获得消息机会。

    :param state: 本轮全部动作结算后的世界副本
    :param observed: 本轮观察所用的轮前世界状态
    :param agent_ids: 本轮的全部人物
    """
    read = {}
    for agent_id in agent_ids:
        before = observed.agents[agent_id]
        if before.same_moment_stage != SameMomentStage.OPEN:
            continue
        agent = read_new_messages(state.agents[agent_id], only=set(before.inbox))
        if agent is not state.agents[agent_id]:
            read[agent_id] = agent
    if not read:
        return state
    return state.model_copy(update={"agents": {**state.agents, **read}}, deep=True)


def merge_message_readiness(state: WorldState, recipient_ids: Iterable[int], limit: int) -> WorldState:
    """@brief 人物消息与交谈邀请送达后，清醒且未达同刻上限的收件人在同一秒获得机会（第一章「人物之间的消息」）。

    在本轮全部动作结算后合并，不被收件人本轮的受理排期覆盖：next_decision_at 取当前时刻，pending_wake_reasons
    并入 message_arrival 并按 WakeReason 枚举排序。睡眠中或已达同刻上限的收件人不变，未读消息在下一边界以
    message_arrival 提供机会（睡眠中过滤）。
    """
    ready = {}
    for agent_id in sorted(set(recipient_ids)):
        agent = state.agents[agent_id]
        asleep = any(item.kind == ActivityKind.SLEEP for item in active_activities(state, agent_id))
        if asleep or agent.same_moment_rounds >= limit:
            continue
        reasons = {*agent.pending_wake_reasons, WakeReason.MESSAGE_ARRIVAL.value}
        ready[agent_id] = agent.model_copy(update={
            "next_decision_at": state.current_time,
            "pending_wake_reasons": [reason.value for reason in WakeReason if reason.value in reasons]}, deep=True)
    if not ready:
        return state
    return state.model_copy(update={"agents": {**state.agents, **ready}}, deep=True)
