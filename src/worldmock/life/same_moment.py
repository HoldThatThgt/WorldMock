"""@brief 同刻决策：同一模拟秒内的轮数、受限轮阶梯、跨决策预算、本轮唤醒原因、受限轮复核、拒绝脉冲与消息机会。

依据 docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「规则」与「实现约定」：决策不消耗模拟时间；没有进行中活动或有待收尾活动
的人物在同一秒再决策（规则 2）；同一人物同一秒的决策轮数由场景 max_same_moment_decisions 封顶，达到上限后至多再给
三个受限轮，每个受限轮只开放一类动作（规则 3：有待收尾只开放收尾；没有进行中与待收尾的活动时开始或恢复，暂停的活动不占
名额，所以不再按名额分流；开始或恢复被拒后留在原阶段）；受限轮用完仍要同一秒再决策时，环境不替人物造活动，写 decision_budget_exhausted
并把下一次决策排在一个结算间隔之后（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「失败后强制等待与跨决策预算」）；
有进行中活动且没有待收尾活动时瞬时动作成功后按决策节律，业务拒绝后未达上限在同一秒再给一次机会（规则 4）；同刻轮次的
唤醒原因按本轮替换（规则 6）；受理、业务拒绝与重放各计一轮（规则 9），时钟推进时轮数与阶段归零；名称类拒绝
（error_contract.NAME_REJECTION_CODES）例外：每人每模拟秒的前两次不计同刻轮数、不施加 service_refused 情绪脉冲，第 3 次起
照常（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数 ，裁决 D7；免计次数是实验假设）；人物消息送达时清醒且未达上限的收件人在同一秒
获得机会，决策轮观察以全文呈现过的消息随本轮提交记为已读（「人物之间的消息」；docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章
「直接消息截断」），轮前在睡的人物不记已读（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）。
引擎只接线，计数、阶梯与预算判定都在本模块。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from worldmock.footprints.models import EventDraft
from worldmock.life.activity_control import concludable, pending_conclusions
from worldmock.life.error_contract import NAME_REJECTION_CODES
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.models import (ActionStatus, ActivityState, ActivityStatus, AgentState,
                                   SameMomentStage, WorldState, active_activities)
from worldmock.life.rhythm import is_asleep, read_new_messages
from worldmock.life.schedule import WakeReason
from worldmock.life.social_actions import PARTNER_ENDED

#: 当前业务拒绝都产生服务拒绝脉冲。
SERVICE_REFUSED = "service_refused"
#: 每人每模拟秒至多几次名称类拒绝不计同刻轮数、不施加情绪脉冲（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数 ；实验假设，登记在
#: docs/EXPERIMENT.md#experiment-worldmock-实验设置「活动节奏与同刻决策上限」）；超出的照常计轮、照常施加脉冲。
FREE_NAME_REJECTIONS_PER_SECOND = 2

#: 受限轮不开放的内部标准动作：到店购买与远程下单是瞬时的生活动作，不产生进行中活动（规则 3）。
RESTRICTED_EXCLUDED_ACTIONS = frozenset({"purchase"})

#: 各阶段接受的请求类（引擎按请求派生 start、resume、wait、conclude、conclude_pending、other）：
#: conclude 指结束能结束的活动（进行中、暂停中或待收尾），conclude_pending 指只点名待收尾活动（全部或其中几项；开放
#: 结束的阶段同样接受它），受限轮的收尾因此不会被业务拒绝；收尾阶段一直持续到没有待收尾活动。resume_or_conclude 与
#: conclude_only 留在阶段闭集与本表中，现行阶梯不再进入它们（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-裁决记录 R2）。
_STAGE_REQUEST_KINDS = {
    SameMomentStage.OPEN: frozenset({"start", "resume", "wait", "conclude", "conclude_pending", "other"}),
    SameMomentStage.CONCLUDE_PENDING: frozenset({"conclude_pending"}),
    SameMomentStage.START_OR_RESUME: frozenset({"start", "resume", "wait"}),
    SameMomentStage.RESUME_OR_CONCLUDE: frozenset({"resume", "conclude", "conclude_pending"}),
    SameMomentStage.CONCLUDE_ONLY: frozenset({"conclude", "conclude_pending"}),
}

#: 达到同刻上限后至多再给的受限轮数（规则 3）；用完仍要同一秒再决策即跨决策预算耗尽。
RESTRICTED_ROUNDS = 3
#: 跨决策预算耗尽时写入的 environment_event 种类（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章，裁决 #25）。
BUDGET_EXHAUSTED_KIND = "decision_budget_exhausted"


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """@brief 一轮结算后的同刻排期输入。

    :param now: 当前模拟时刻（epoch 秒）
    :param agent: 已计入本轮的人物状态
    :param active: 本轮结算后人物是否有进行中活动
    :param rejected: 本轮是否为业务拒绝（重放按存档结果）
    :param limit: 场景的同刻决策上限
    :param pending: 本轮结算后人物是否有待收尾活动
    """

    now: int
    agent: AgentState
    active: bool
    rejected: bool
    limit: int
    pending: bool = False


def count_round(agent: AgentState) -> AgentState:
    """@brief 受理、业务拒绝与重放各使本秒的同刻轮数加一（第一章「实现约定」）；免计的名称类拒绝不经这里
    （rejection_counts）。"""
    return agent.model_copy(update={"same_moment_rounds": agent.same_moment_rounds + 1}, deep=True)


def free_rejection_flags(codes: Sequence[str | None]) -> list[bool]:
    """@brief 同一人物同一模拟秒内按结算先后的业务拒绝码逐个是否免计（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数 ）：名称
    类拒绝码
    的前 FREE_NAME_REJECTIONS_PER_SECOND 次免计（不计同刻轮数、不施加情绪脉冲），此后的名称类拒绝与其余拒绝一样照常。"""
    flags, seen = [], 0
    for code in codes:
        named = code in NAME_REJECTION_CODES
        flags.append(named and seen < FREE_NAME_REJECTIONS_PER_SECOND)
        seen += named
    return flags


def rejection_counts(state: WorldState, agent_id: int, error_code: str) -> bool:
    """@brief 这次业务拒绝是否计入同刻轮数并照常施加情绪脉冲：本人本秒此前已结算的拒绝（依据已存的动作结果）之后，它是
    本秒第 3 次起的名称类拒绝或不是名称类拒绝时计入。"""
    return not free_rejection_flags([*_second_rejections(state, agent_id), error_code])[-1]


def rejection_pulse(state: WorldState, agent_id: int, error_code: str) -> str | None:
    """@brief 这次业务拒绝施加的情绪脉冲键：免计的名称类拒绝不施加；其余按 refusal_pulse_key，其中 service_refused 每人
    每秒至多一次（「拒绝的情绪代价与重估间隔」）。"""
    if not rejection_counts(state, agent_id, error_code):
        return None
    key = refusal_pulse_key(error_code)
    return None if key == SERVICE_REFUSED and refusal_pulse_applied(state, agent_id) else key


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
    阶段保持不变直到时钟推进复位；否则（没有进行中与待收尾的活动，两个名额都空着）一律开始或恢复（start_or_resume），
    开始或恢复被拒后留在这一阶段。
    """
    agent = outcome.agent
    if agent.same_moment_rounds < outcome.limit:
        return SameMomentStage.OPEN
    if outcome.pending:
        return SameMomentStage.CONCLUDE_PENDING
    if outcome.active:
        return agent.same_moment_stage
    return SameMomentStage.START_OR_RESUME


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


def budget_exhausted(outcome: RoundOutcome) -> bool:
    """@brief 跨决策预算是否耗尽：本秒轮数已达上限加三（受限轮用完），本轮之后仍没有进行中活动或仍有待收尾活动。

    单次决策的回合预算（宿主的 ReAct 回合）与此分开计（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「失败后强制等待与跨决策预算」）。
    """
    used_up = outcome.agent.same_moment_rounds >= outcome.limit + RESTRICTED_ROUNDS
    return used_up and (not outcome.active or outcome.pending)


def exhausted_schedule(outcome: RoundOutcome, feedback_seconds: int,
                       error_code: str | None) -> tuple[tuple[int, list[str], SameMomentStage], EventDraft]:
    """@brief 跨决策预算耗尽时的排期与事件（裁决 #25）：环境不替人物造活动，下一次决策排在 now + 结算间隔。

    时钟因此可以推进（技术停顿，身体按静息结算），不记成人物主动等待；原因清空，阶段照阶梯，推进时复位。
    environment_event 只供系统（visibility=system），data 带最近的拒绝码与下一次决策时刻。

    :param feedback_seconds: 场景 activity_feedback_seconds。
    :param error_code: 本人在本秒最近一次业务拒绝的错误码；本秒没有拒绝时为 None。
    :returns: ((next_decision_at, pending_wake_reasons, same_moment_stage), 事件草稿)
    """
    next_at = outcome.now + feedback_seconds
    draft = EventDraft(event_type="environment_event", occurred_at=outcome.now, agent_id=outcome.agent.agent_id,
                       visibility="system", source_kind="environment",
                       data={"kind": BUDGET_EXHAUSTED_KIND, "error_code": error_code, "next_decision_at": next_at})
    return (next_at, [], next_stage(outcome)), draft


def request_kind(tool_name: str, canonical_actions: Sequence[str], named: Sequence[ActivityState],
                 pending_ids: frozenset[str]) -> str:
    """@brief 受限轮复核的请求类（引擎解析名称与对象后传入，宿主收窄与引擎复核用同一口径）。

    :param tool_name: 世界请求工具名。
    :param canonical_actions: start_activity 各项的内部标准动作（其他工具为空）。
    :param named: report_status、conclude_activity 点名的权威活动（其他工具为空）。
    :param pending_ids: 人物当前全部待收尾活动身份。
    :returns: wait（只等待）、start（开始持续活动，不含受限轮排除的动作）、resume（只恢复暂停活动）、
              conclude_pending（只点名待收尾活动，全部或其中几项）、conclude（只结束能结束的活动）或 other。
    """
    if tool_name == "start_activity":
        if list(canonical_actions) == ["wait"]:
            return "wait"
        return "other" if RESTRICTED_EXCLUDED_ACTIONS & set(canonical_actions) else "start"
    if tool_name == "report_status":
        return "resume" if named and all(item.status == ActivityStatus.PAUSED for item in named) else "other"
    if tool_name == "conclude_activity":
        if named and {item.activity_id for item in named} <= pending_ids:
            return "conclude_pending"
        return "conclude" if named and all(concludable(item) for item in named) else "other"
    return "other"


def stage_refusal(stage: SameMomentStage, request_kind: str) -> Refusal | None:
    """@brief 受限轮复核的判定本身：请求类不属于本阶段开放的动作类时返回 SAME_MOMENT_ACTION_RESTRICTED，否则 None。

    工具层的可用性说明与受理共用这一判定，所以说明与拒绝原因一字不差（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。

    :param stage: 人物当前的受限轮阶段
    :param request_kind: 引擎派生的请求类：start（开始持续活动）、resume（恢复暂停活动）、wait（只等待）、
                         conclude（结束能结束的活动）、conclude_pending（只点名待收尾活动）、other
    """
    if request_kind in _STAGE_REQUEST_KINDS.get(stage, frozenset()):
        return None
    return refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED")


def require_stage_action(stage: SameMomentStage, request_kind: str) -> None:
    """@brief 受限轮复核（第一章「实现约定」）：请求类不属于本阶段开放的动作类即业务拒绝。

    :raises PreconditionError: SAME_MOMENT_ACTION_RESTRICTED（stage_refusal 的原因），计入同刻轮数
    """
    refusal = stage_refusal(stage, request_kind)
    if refusal is not None:
        raise refusal.error()


def refusal_pulse_key(error_code: str) -> str | None:
    """@brief 业务拒绝码对应的情绪脉冲键：品类配对说明不施加，其余记 service_refused；免计的
    名称类拒绝（3.4）是否施加由 rejection_pulse 判定。"""
    return SERVICE_REFUSED


def _second_rejections(state: WorldState, agent_id: int) -> list[str | None]:
    """@brief 本人在当前模拟秒已结算的业务拒绝码（依据已存的动作结果，按结算先后；重放不另存结果）。"""
    return [stored.result.get("error_code") for stored in state.processed_actions.values()
            if stored.result is not None
            and stored.result.get("agent_id") == agent_id
            and stored.result.get("occurred_at") == state.current_time
            and stored.result.get("status") == ActionStatus.REJECTED.value]


def latest_rejection_code(state: WorldState, agent_id: int) -> str | None:
    """@brief 本人在当前模拟秒最近一次计入同刻轮数的业务拒绝的错误码（依据已存的动作结果，按结算先后；免计的名称类拒绝
    不算，3.4），没有时为 None。"""
    codes = _second_rejections(state, agent_id)
    counted = [code for code, free in zip(codes, free_rejection_flags(codes)) if not free]
    return counted[-1] if counted else None


def refusal_pulse_applied(state: WorldState, agent_id: int) -> bool:
    """@brief 本人在当前模拟秒是否已施加过 service_refused 脉冲（「拒绝的情绪代价与重估间隔」：每人每秒至多一次）。

    依据已存的动作结果判定：本人、发生于当前时刻、状态为拒绝、不是免计的名称类拒绝且错误码的脉冲键为 service_refused；
    不另设世界字段。
    """
    codes = _second_rejections(state, agent_id)
    return any(not free and refusal_pulse_key(str(code)) == SERVICE_REFUSED
               for code, free in zip(codes, free_rejection_flags(codes)))


def reset_same_moment(state: WorldState) -> WorldState:
    """@brief 时钟推进时每个人物的同刻轮数归零、受限轮阶段复位为 open（第一章「实现约定」）。"""
    agents = {agent_id: agent.model_copy(update={"same_moment_rounds": 0,
                                                 "same_moment_stage": SameMomentStage.OPEN}, deep=True)
              for agent_id, agent in state.agents.items()}
    return state.model_copy(update={"agents": agents}, deep=True)


def read_observed_messages(state: WorldState, observed: WorldState,
                           presented: Mapping[int, frozenset[str]]) -> WorldState:
    """@brief 决策轮提交时，把本轮观察以全文呈现过的消息记为已读（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」）。

    「已读」即全文已呈现：只读 presented[人物]（该人物本轮观察的 presented_message_ids）中已在轮前收件箱里的消息，
    看过的消息不在其后的边界再以 message_arrival 唤醒；没有出现在本 session 进度层的消息保持未读。本轮内送达的消息仍未读，
    由 merge_message_readiness 给同秒机会，或在下一边界提供。受限轮（轮前阶段不是 open）与轮前有待收尾活动的轮次
    （只接受收尾，docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章「活动一结束就要结束它」）回应不了消息，不记已读：这些收件人
    照常在其后的轮次或下一边界获得消息机会。轮前在睡的人物（睡眠中只因睡眠允许的原因得到决策轮）也不记已读：睡眠中不读
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ），消息留到醒来后的第一个边界；本轮才开始睡的人物轮前醒着，照常记已读。

    :param state: 本轮全部动作结算后的世界副本
    :param observed: 本轮观察所用的轮前世界状态
    :param presented: 本轮各人物的观察中全文已呈现的收件箱消息身份（人物 → 消息身份集合）
    """
    read = {}
    for agent_id, message_ids in presented.items():
        before = observed.agents[agent_id]
        if (before.same_moment_stage != SameMomentStage.OPEN or pending_conclusions(observed, agent_id)
                or is_asleep(observed, agent_id)):
            continue
        agent = read_new_messages(state.agents[agent_id], only=set(message_ids) & set(before.inbox))
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
        if is_asleep(state, agent_id) or agent.same_moment_rounds >= limit:
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
    收尾一支重新开始（conclude_pending），每次至多另加三轮。轮数在其决策时照常计入。本轮才受理（接受）、又在本轮
    被对方结束的交谈同样在这一秒收尾，收尾前时钟不推进（第一章规则 1、2）。

    :param before: 本轮之前的世界（判定哪些交谈是本轮由对方结束的：本轮之前已结束的不算）。
    :param state: 本轮全部动作结算后的世界副本。
    """
    ready = {}
    for activity in sorted(state.activities.values(), key=lambda item: item.activity_id):
        previous = before.activities.get(activity.activity_id)
        if (activity.boundary_type != PARTNER_ENDED or activity.concluded_at is not None
                or activity.ended_at != state.current_time
                or previous is not None and previous.status != ActivityStatus.ACTIVE):
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
