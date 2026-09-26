"""@brief 同刻决策：同一模拟秒内的轮数、受限轮阶梯、本轮唤醒原因、受限轮复核、拒绝脉冲与消息机会。

依据 docs/dev/SPEC-time-and-energy.md 第一章「规则」与「实现约定」：决策不消耗模拟时间；没有进行中活动或有待收尾活动
的人物在同一秒再决策（规则 2）；同一人物同一秒的决策轮数由场景 max_same_moment_decisions 封顶，达到上限后每个受限轮
只开放一类动作（规则 3：有待收尾只开放收尾；否则有空名额开始或恢复；名额被暂停活动占满时恢复或结束；上一受限轮的开始
或恢复被拒后，名额满先只开放结束、此后只开放等待，名额未满直接只开放等待）；有进行中活动且没有待收尾活动时瞬时动作
成功后按决策节律，业务拒绝后未达上限在同一秒再给一次机会（规则 4）；同刻轮次的唤醒原因按本轮替换（规则 6）；受理、
业务拒绝与重放各计一轮（规则 9），时钟推进时轮数与阶段归零；人物消息送达时清醒且未达上限的收件人在同一秒获得机会，
决策轮观察展示过的消息随本轮提交记为已读（「人物之间的消息」）。引擎只接线，计数与阶梯都在本模块。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.life.activity_control import occupies_slot, pending_conclusions
from worldmock.life.models import (ActionStatus, ActivityKind, ActivityState, ActivityStatus, AgentState,
                                   SameMomentStage, WorldState, active_activities)
from worldmock.life.rhythm import read_new_messages
from worldmock.life.schedule import WakeReason
from worldmock.life.social_actions import PARTNER_ENDED

#: 业务拒绝对应的情绪脉冲键：工作返工单独计，两种品类配对说明不施加脉冲，其余都是服务拒绝。
SERVICE_REFUSED = "service_refused"
_REWORK_CODE = "WORK_REQUIRES_REWORK"
_NO_PULSE_CODES = frozenset({"CATEGORY_PAIRING_NO_EFFECT", "CATEGORY_PAIRING_UNDEFINED"})

#: 受限轮不开放的内部标准动作：到店购买与远程下单是瞬时的生活动作，不产生进行中活动（规则 3）。
RESTRICTED_EXCLUDED_ACTIONS = frozenset({"purchase"})

#: 各阶段开放的世界请求工具（宿主据此收窄工具面，引擎按 _STAGE_REQUEST_KINDS 复核）；None 表示 open 不收窄。
#: 记忆查询是观察，不在此列、照常开放。
STAGE_TOOLS: dict[SameMomentStage, frozenset[str] | None] = {
    SameMomentStage.OPEN: None,
    SameMomentStage.CONCLUDE_PENDING: frozenset({"conclude_activity"}),
    SameMomentStage.START_OR_RESUME: frozenset({"start_activity", "report_status"}),
    SameMomentStage.RESUME_OR_CONCLUDE: frozenset({"report_status", "conclude_activity"}),
    SameMomentStage.CONCLUDE_ONLY: frozenset({"conclude_activity"}),
    SameMomentStage.WAIT_ONLY: frozenset({"start_activity"}),
}

#: 各阶段接受的请求类（引擎按请求派生 start、resume、wait、conclude、conclude_pending、other）：
#: conclude 指结束占名额的活动，conclude_pending 指一次点名全部待收尾活动（待收尾活动也占名额，所以开放结束的
#: 阶段同样接受它），受限轮的收尾与结束因此都不会被业务拒绝。
_STAGE_REQUEST_KINDS = {
    SameMomentStage.OPEN: frozenset({"start", "resume", "wait", "conclude", "conclude_pending", "other"}),
    SameMomentStage.CONCLUDE_PENDING: frozenset({"conclude_pending"}),
    SameMomentStage.START_OR_RESUME: frozenset({"start", "resume", "wait"}),
    SameMomentStage.RESUME_OR_CONCLUDE: frozenset({"resume", "conclude", "conclude_pending"}),
    SameMomentStage.CONCLUDE_ONLY: frozenset({"conclude", "conclude_pending"}),
    SameMomentStage.WAIT_ONLY: frozenset({"wait"}),
}

#: 达到上限、本轮后没有进行中活动也没有待收尾活动时的下一阶段：键为（本轮阶段, 名额是否被占满）。
#: 进入受限轮或收尾之后按名额分流；开始或恢复被拒后名额满先只开放结束，否则只开放等待；结束之后只开放等待。
_LADDER = {
    (SameMomentStage.OPEN, False): SameMomentStage.START_OR_RESUME,
    (SameMomentStage.OPEN, True): SameMomentStage.RESUME_OR_CONCLUDE,
    (SameMomentStage.CONCLUDE_PENDING, False): SameMomentStage.START_OR_RESUME,
    (SameMomentStage.CONCLUDE_PENDING, True): SameMomentStage.RESUME_OR_CONCLUDE,
    (SameMomentStage.START_OR_RESUME, False): SameMomentStage.WAIT_ONLY,
    (SameMomentStage.START_OR_RESUME, True): SameMomentStage.CONCLUDE_ONLY,
    (SameMomentStage.RESUME_OR_CONCLUDE, False): SameMomentStage.START_OR_RESUME,
    (SameMomentStage.RESUME_OR_CONCLUDE, True): SameMomentStage.CONCLUDE_ONLY,
    (SameMomentStage.CONCLUDE_ONLY, False): SameMomentStage.WAIT_ONLY,
    (SameMomentStage.CONCLUDE_ONLY, True): SameMomentStage.CONCLUDE_ONLY,
    (SameMomentStage.WAIT_ONLY, False): SameMomentStage.WAIT_ONLY,
    (SameMomentStage.WAIT_ONLY, True): SameMomentStage.CONCLUDE_ONLY,
}


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """@brief 一轮结算后的同刻排期输入。

    :param now: 当前模拟时刻（epoch 秒）
    :param agent: 已计入本轮的人物状态
    :param active: 本轮结算后人物是否有进行中活动
    :param rejected: 本轮是否为业务拒绝（重放按存档结果）
    :param limit: 场景的同刻决策上限
    :param pending: 本轮结算后人物是否有待收尾活动
    :param slots_full: 本轮结算后两个名额是否都被占着
    """

    now: int
    agent: AgentState
    active: bool
    rejected: bool
    limit: int
    pending: bool = False
    slots_full: bool = False


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


def next_stage(outcome: RoundOutcome) -> SameMomentStage:
    """@brief 本轮之后的受限轮阶段（规则 3）。

    未达上限保持 open；达到上限后有待收尾活动只开放收尾（conclude_pending）；有进行中活动且没有待收尾时不再进入受限轮，
    阶段保持不变直到时钟推进复位；否则按（本轮阶段, 名额是否占满）的阶梯推进。
    """
    agent = outcome.agent
    if agent.same_moment_rounds < outcome.limit:
        return SameMomentStage.OPEN
    if outcome.pending:
        return SameMomentStage.CONCLUDE_PENDING
    if outcome.active:
        return agent.same_moment_stage
    return _LADDER[(agent.same_moment_stage, outcome.slots_full)]


def schedule_after_round(outcome: RoundOutcome,
                         rhythm_next: Callable[[], int]) -> tuple[int, list[str], SameMomentStage]:
    """@brief 安排本轮之后的下一次决策，返回 (next_decision_at, pending_wake_reasons, same_moment_stage)。

    没有进行中活动、有待收尾活动，或有进行中活动而业务拒绝且未达上限：同一秒再决策，原因按本轮替换（规则 2、4、6）；
    其余情形（有进行中活动且无待收尾时的成功，或达到上限的拒绝）按决策节律，只在此时调用 rhythm_next，原因清空。
    """
    stage = next_stage(outcome)
    below_limit = outcome.agent.same_moment_rounds < outcome.limit
    if not outcome.active or outcome.pending or outcome.rejected and below_limit:
        return outcome.now, round_wake_reasons(outcome.active, outcome.rejected), stage
    return rhythm_next(), [], stage


def request_kind(tool_name: str, canonical_actions: Sequence[str], named: Sequence[ActivityState],
                 pending_ids: frozenset[str]) -> str:
    """@brief 受限轮复核的请求类（引擎解析名称与对象后传入，宿主收窄与引擎复核用同一口径）。

    :param tool_name: 世界请求工具名。
    :param canonical_actions: start_activity 各项的内部标准动作（其他工具为空）。
    :param named: report_status、conclude_activity 点名的权威活动（其他工具为空）。
    :param pending_ids: 人物当前全部待收尾活动身份。
    :returns: wait（只等待）、start（开始持续活动，不含受限轮排除的动作）、resume（只恢复暂停活动）、
              conclude_pending（一次点名全部待收尾活动）、conclude（只结束占名额的活动）或 other。
    """
    if tool_name == "start_activity":
        if list(canonical_actions) == ["wait"]:
            return "wait"
        return "other" if RESTRICTED_EXCLUDED_ACTIONS & set(canonical_actions) else "start"
    if tool_name == "report_status":
        return "resume" if named and all(item.status == ActivityStatus.PAUSED for item in named) else "other"
    if tool_name == "conclude_activity":
        if pending_ids and {item.activity_id for item in named} == pending_ids:
            return "conclude_pending"
        return "conclude" if named and all(occupies_slot(item) for item in named) else "other"
    return "other"


def require_stage_action(stage: SameMomentStage, request_kind: str) -> None:
    """@brief 受限轮复核（第一章「实现约定」）：请求类不属于本阶段开放的动作类即业务拒绝。

    :param stage: 人物当前的受限轮阶段
    :param request_kind: 引擎派生的请求类：start（开始持续活动）、resume（恢复暂停活动）、wait（只等待）、
                         conclude（结束占名额的活动）、conclude_pending（一次点名全部待收尾活动）、other
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
    与轮前有待收尾活动的轮次（只接受收尾，第七章「活动一结束就要结束它」）回应不了消息，不记已读：这些收件人
    照常在其后的轮次或下一边界获得消息机会。

    :param state: 本轮全部动作结算后的世界副本
    :param observed: 本轮观察所用的轮前世界状态
    :param agent_ids: 本轮的全部人物
    """
    read = {}
    for agent_id in agent_ids:
        before = observed.agents[agent_id]
        if before.same_moment_stage != SameMomentStage.OPEN or pending_conclusions(observed, agent_id):
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


def merge_partner_endings(before: WorldState, state: WorldState, limit: int) -> WorldState:
    """@brief 交谈对方结束、本人交谈在同一秒结束（partner_ended）而待收尾的人物，在同一秒再获得机会（第五章「交谈」）。

    在本轮全部动作结算后合并，不被对方本轮的受理排期覆盖：next_decision_at 取当前时刻；pending_wake_reasons
    替换为本轮原因，仍有其他进行中活动记 planned_end，否则记 no_active_activity；已达同刻上限时按规则 3 的阶梯从
    收尾一支重新开始（conclude_pending），每次至多另加三轮。轮数在其决策时照常计入。

    :param before: 本轮之前的世界（判定哪些交谈是本轮由对方结束的）。
    :param state: 本轮全部动作结算后的世界副本。
    """
    ready = {}
    for activity in sorted(state.activities.values(), key=lambda item: item.activity_id):
        previous = before.activities.get(activity.activity_id)
        if (activity.boundary_type != PARTNER_ENDED or previous is None
                or previous.status != ActivityStatus.ACTIVE or activity.concluded_at is not None):
            continue
        agent = state.agents[activity.agent_id]
        reason = WakeReason.PLANNED_END if active_activities(state, agent.agent_id) else WakeReason.NO_ACTIVE_ACTIVITY
        stage = SameMomentStage.CONCLUDE_PENDING if agent.same_moment_rounds >= limit else agent.same_moment_stage
        ready[agent.agent_id] = agent.model_copy(update={
            "next_decision_at": state.current_time, "pending_wake_reasons": [reason.value],
            "same_moment_stage": stage}, deep=True)
    if not ready:
        return state
    return state.model_copy(update={"agents": {**state.agents, **ready}}, deep=True)
