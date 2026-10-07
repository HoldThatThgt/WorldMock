"""@brief 社交动作的引擎侧执行：瞬时消息、交谈与通话前台活动（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。

域校验与记录结构复用 life.social 纯函数；本模块补足引擎侧接线：构造 CHAT/CALL
前台活动（这两种 kind 不在可供性注册表内，不经 activities.start_activity 的
注册表 MET 查表）、按区间推进并结算社交需要账本（felt.apply_social_need_ledger）。

交谈语义（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）：对方对发起人选择交谈，而发起人正有以对方为伙伴的
进行中交谈，这就是接受：不再发邀请，接受方交谈的计划结束时刻对齐发起方；同一对人物只能有一项进行中交谈，重复发起
以 CHAT_ALREADY_ACTIVE 拒绝。一方结束交谈时，对方以其为伙伴的进行中交谈在同一秒结束（partner_ended），成为待收尾。
缓解按区间内双方交谈同时进行的秒数结算（每小时各按交谈账本缓解），其余秒数按发起方 0.2/小时，以推进前的状态判定，
不受人物推进顺序影响。
model 策略联系人回复的实际模型调用沿用 work_review 的外部调用模式：引擎只登记
social.ModelReplyRequest 待宿主执行（本仓库引擎无模型直连通道，见交付报告）。
"""

from __future__ import annotations
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.life import felt, social
from worldmock.life.activities import ActivityAdvance, ActivityOrigin
from worldmock.life.body import ActivityFactors, apply_social_ledger, settle_body
from worldmock.life.guidance import Refusal
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    BodyState,
    StartSocialActivityRequest,
    WorldState,
    active_activities,
)
from worldmock.life.scenario import BodyConfig

#: 一方结束交谈时对方交谈的结束原因（boundary_type 与 interruption_reason 同值）。
PARTNER_ENDED = "partner_ended"
#: 派生时长截取区间（秒，docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）：交谈 60–7200，通话 600–3600。
CHAT_DURATION_BOUNDS = (60, 7200)
CALL_DURATION_BOUNDS = (600, 3600)


@dataclass(frozen=True, slots=True)
class SocialStart:
    """@brief 一次社交动作受理的结果：活动、交谈邀请（接受时为 None）与被接受的对方交谈（已标记接受）。"""

    agent: AgentState
    activity: ActivityState
    invite: social.SentMessage | None
    accepted: ActivityState | None = None


def social_duration_seconds(kind: str, derived_seconds: int) -> int:
    """@brief 交谈、通话的计划时长：场景反馈间隔派生值按区间截取到整分钟（通话 600–3600 秒，交谈 60–7200 秒）。"""
    low, high = CALL_DURATION_BOUNDS if kind == "call" else CHAT_DURATION_BOUNDS
    return min(max(derived_seconds, low), high) // 60 * 60


def partner_presence(state: WorldState, partner_id: str) -> social.AgentPresence:
    """@brief 从世界状态解析对方的在场事实（找不到对方即业务拒绝）；途中的人物不在任何地点。"""
    matches = [agent for agent in state.agents.values() if f"agent:{agent.agent_id}" == partner_id]
    if len(matches) != 1:
        raise BusinessRuleError("SOCIAL_PARTNER_NOT_FOUND")
    other = matches[0]
    active = active_activities(state, other.agent_id)
    kinds = {activity.kind for activity in active}
    return social.AgentPresence(
        agent_id=partner_id, name=other.name,
        location=other.location_id if ActivityKind.TRAVEL not in kinds else f"途中:{other.agent_id}",
        activity_family=active[0].kind.value if active else "idle",
        awake=ActivityKind.SLEEP not in kinds)


def pair_chat(state: WorldState, agent_id: int, partner_id: str) -> ActivityState | None:
    """@brief 人物以对方为伙伴的进行中交谈（同一对人物至多一项）。"""
    return next((activity for activity in active_activities(state, agent_id)
                 if activity.kind == ActivityKind.CHAT and activity.data.get("partner_id") == partner_id), None)


def late_acceptance_refusal(state: WorldState, agent_id: int, partner_id: str) -> Refusal | None:
    """@brief 接受交谈时发起方的交谈只剩不足一分钟：对齐后的交谈短于最短时长，以 ACTIVITY_TOO_SHORT 引导性拒绝。

    对方没有以本人为伙伴的进行中交谈（这次不是接受）时为 None；发起方结束后再对其发起交谈就是新邀请
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。交互对象层的交谈说明与受理用同一判定。
    """
    invitation = pair_chat(state, int(partner_id.split(":", 1)[1]), f"agent:{agent_id}")
    if invitation is None or invitation.planned_end_at - state.current_time >= CHAT_DURATION_BOUNDS[0]:
        return None
    return Refusal("ACTIVITY_TOO_SHORT", "The other person's conversation has less than one minute remaining.",
                   "Wait for the current conversation to end, then start a new one.")


def start_chat(state: WorldState, agent: AgentState, request: StartSocialActivityRequest,
               origin: ActivityOrigin, message_id: str) -> SocialStart:
    """@brief 受理交谈：共同在场且对方可交谈；对方正以本人为伙伴交谈时即接受，不再发邀请、计划结束对齐发起方。

    接受时发起方的交谈只剩不足一分钟，以 ACTIVITY_TOO_SHORT 返回原因与下一步（late_acceptance_refusal）。
    """
    partner = partner_presence(state, request.partner_id)
    if pair_chat(state, agent.agent_id, request.partner_id) is not None:
        raise BusinessRuleError("CHAT_ALREADY_ACTIVE")
    partner_id = int(request.partner_id.split(":", 1)[1])
    invitation = pair_chat(state, partner_id, f"agent:{agent.agent_id}")
    if invitation is not None:
        if partner.location != agent.location_id:
            raise ValueError("chat acceptance requires co-presence")
        late = late_acceptance_refusal(state, agent.agent_id, request.partner_id)
        if late is not None:
            raise late.error()
        activity = _social_activity(agent, ActivityKind.CHAT, invitation.planned_end_at - state.current_time,
                                    origin, state.current_time,
                                    {"partner_id": request.partner_id, "accepted": True})
        accepted = invitation.model_copy(update={"data": {**invitation.data, "accepted": True}}, deep=True)
        return SocialStart(agent.model_copy(deep=True), activity, None, accepted)
    invite = social.OutgoingMessage(
        message_id=message_id, sender_id=f"agent:{agent.agent_id}",
        recipient_id=request.partner_id,
        text=f"{agent.name} 邀请你交谈约 {request.duration_minutes} 分钟",
        created_at=state.current_time)
    started = social.start_chat(agent.location_id, partner, request.duration_minutes, invite)
    activity = _social_activity(agent, ActivityKind.CHAT, request.duration_minutes * 60,
                                origin, state.current_time,
                                {"partner_id": request.partner_id, "accepted": False})
    return SocialStart(agent.model_copy(deep=True), activity,
                       social.SentMessage(message=started.invite_message, arrival=started.arrival))


def start_call(agent: AgentState, contact: social.Contact, request: StartSocialActivityRequest,
               origin: ActivityOrigin, now: int) -> SocialStart:
    """@brief 受理通话：面向场景联系人，10-60 分钟，
    占用说与听（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。"""
    started = social.start_call(contact, request.duration_minutes)
    activity = _social_activity(agent, ActivityKind.CALL, started.duration_minutes * 60,
                                origin, now,
                                {"contact_id": started.contact_id})
    return SocialStart(agent.model_copy(deep=True), activity, None)


def _social_activity(agent: AgentState, kind: ActivityKind, duration_seconds: int,
                     origin: ActivityOrigin, now: int, data: dict) -> ActivityState:
    """@brief 构造 CHAT/CALL 前台活动（不经可供性注册表）。"""
    if duration_seconds < 60:
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    return ActivityState(
        activity_id=(f"activity-{origin.action_id}:" + f"{origin.activity_ordinal:04d}"
                     if origin.activity_ordinal > 1 else f"activity-{origin.action_id}"),
        agent_id=agent.agent_id, location_id=agent.location_id, kind=kind,
        status=ActivityStatus.ACTIVE, started_at=now, planned_end_at=now + duration_seconds,
        last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=duration_seconds,
        target_id=None, resource_claims=set(social.SOCIAL_RESOURCE_CLAIMS),
        origin_action_id=origin.action_id, origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id, data=dict(data), report_anchor_at=now)


def shared_chat_seconds(state: WorldState, activity: ActivityState, target: int) -> int:
    """@brief 本区间内双方交谈同时进行的秒数：对方以本人为伙伴的交谈在区间起点进行中时取两者共同的部分，否则为 0。

    区间起点是本项推进前的结算时刻；对方的交谈按区间起点时的状态判定（本次推进中先结算完成的，按其结束时刻），
    所以结果与人物推进顺序无关（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。
    """
    start = activity.last_settled_at
    partner_id = str(activity.data.get("partner_id", ""))
    if activity.kind != ActivityKind.CHAT or not partner_id.startswith("agent:"):
        return 0
    stops = [(other.planned_end_at if other.status == ActivityStatus.ACTIVE else other.ended_at)
             for other in state.activities.values()
             if other.agent_id == int(partner_id.split(":", 1)[1]) and other.kind == ActivityKind.CHAT
             and other.data.get("partner_id") == f"agent:{activity.agent_id}" and other.started_at <= start
             and (other.status == ActivityStatus.ACTIVE or (other.ended_at or start) > start)]
    if not stops:
        return 0
    return max(0, min(target, activity.planned_end_at, max(stops)) - start)


def advance_social(activity: ActivityState, agent: AgentState, now: int,
                   config: BodyConfig, shared_seconds: int) -> ActivityAdvance:
    """@brief 推进 CHAT/CALL 活动一个区间：身体结算 + 社交需要账本（镜像 advance_activity）。"""
    if activity.status != ActivityStatus.ACTIVE:
        return ActivityAdvance(activity.model_copy(deep=True), agent.model_copy(deep=True), None, False)
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if seconds < 0:
        raise ValueError("activity settlement time moved backwards")
    met = social.CHAT_MET if activity.kind == ActivityKind.CHAT else social.CALL_MET
    factors = ActivityFactors(met=met)
    settlement = settle_body(agent.body, activity.kind, seconds, config, factors)
    body = _social_body_relief(settlement.after, activity, seconds, shared_seconds)
    changed_agent = agent.model_copy(update={"body": body, "body_settled_at": stop}, deep=True)
    changed, completed = _advanced_activity(activity, stop, seconds)
    return ActivityAdvance(changed, changed_agent, settlement, completed)


def advance_social_shared(activity: ActivityState, agent: AgentState, now: int,
                          settlement, shared_seconds: int) -> ActivityAdvance:
    """@brief 在共同身体结算后只推进社交活动与其独立缓解；shared_seconds 为双方交谈同时进行的秒数。"""
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if activity.status != ActivityStatus.ACTIVE or seconds < 0:
        raise ValueError("social activity settlement interval is invalid")
    body = _social_body_relief(agent.body, activity, seconds, shared_seconds)
    changed, completed = _advanced_activity(activity, stop, seconds)
    changed_agent = agent.model_copy(update={"body": body}, deep=True)
    return ActivityAdvance(changed, changed_agent, settlement, completed)


def _advanced_activity(activity: ActivityState, stop: int, seconds: int) -> tuple[ActivityState, bool]:
    """@brief 累加社交活动进度，到计划时长即完成（planned_end）。"""
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed}
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end",
                       ended_at=stop, next_interaction_at=None)
    return activity.model_copy(update=updates, deep=True), completed


def _social_body_relief(body: BodyState, activity: ActivityState, seconds: int,
                        shared_seconds: int) -> BodyState:
    """@brief 按活动类型应用社交需要账本缓解：通话按通话账本；交谈同时进行的秒数按交谈账本，其余按发起方速率。"""
    hours = seconds / 3600
    if activity.kind == ActivityKind.CALL:
        exposure = felt.SocialExposure(awake_hours=hours, call_hours=hours)
        return apply_social_ledger(body, exposure)
    shared = min(max(shared_seconds, 0), seconds) / 3600
    after = apply_social_ledger(body, felt.SocialExposure(awake_hours=hours, conversation_hours=shared))
    declined = social.CHAT_RELIEF_DECLINED_PER_HOUR * (hours - shared)
    return after.model_copy(update={"social_need": max(0.0, after.social_need - declined)}, deep=True)


def end_partner_chats(before: WorldState, after: WorldState) -> tuple[WorldState, list[int]]:
    """@brief 本次结束的交谈：对方以其为伙伴的进行中交谈在同一秒结束（partner_ended），成为待收尾。

    :param before: 本次动作之前的世界。
    :param after: 本次动作之后的世界。
    :returns: (结束了对方交谈的世界, 对方人物身份列表)。
    """
    ended = [activity for activity in before.activities.values()
             if activity.kind == ActivityKind.CHAT and activity.status == ActivityStatus.ACTIVE
             and after.activities[activity.activity_id].status != ActivityStatus.ACTIVE]
    activities = dict(after.activities)
    partners: list[int] = []
    for activity in sorted(ended, key=lambda item: item.activity_id):
        partner_id = str(activity.data.get("partner_id", ""))
        if not partner_id.startswith("agent:"):
            continue
        other = pair_chat(after, int(partner_id.split(":", 1)[1]), f"agent:{activity.agent_id}")
        if other is None:
            continue
        activities[other.activity_id] = other.model_copy(update={
            "status": ActivityStatus.COMPLETED, "ended_at": after.current_time, "next_interaction_at": None,
            "boundary_type": PARTNER_ENDED, "interruption_reason": PARTNER_ENDED}, deep=True)
        partners.append(other.agent_id)
    if not partners:
        return after, []
    return after.model_copy(update={"activities": activities}), partners


def schedule_template_reply(state: WorldState, message: social.Message, contact: social.Contact) -> WorldState:
    """@brief template 策略联系人落一条占位回复（social.template_reply），登记为 CONTACT_REPLY_DELAY_SECONDS 后
    送达的预排事件，到期写入收件箱并以 message_arrival 唤醒，睡眠中过滤（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）。"""

    incoming = social.IncomingMessage(message_id=message.message_id, sender_id=message.sender_id,
                                      contact_id=contact.contact_id, text=message.text,
                                      received_at=message.created_at)
    reply = social.template_reply(incoming, f"reply-{message.message_id}", state.current_time)
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    event = ScheduledEvent(
        scheduled_id=f"contact-reply:{reply.message.message_id}", kind=ScheduledKind.MESSAGE_ARRIVAL,
        due_at=state.current_time + social.CONTACT_REPLY_DELAY_SECONDS, sequence=sequence,
        agent_id=int(message.sender_id.split(":", 1)[1]), data={"message": reply.message.model_dump(mode="json")})
    events = sorted([*state.pending_events, event], key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    return state.model_copy(update={"pending_events": events})


def social_activity_change(agent: AgentState, activity: ActivityState, operation: str,
                           now: int) -> tuple[AgentState, ActivityState]:
    """@brief CHAT/CALL 的暂停/恢复/取消（不经注册表反馈闭集）。

    交谈/通话不可暂停（对话连续），取消任意时刻允许（挂断）；取消保留已结算
    进度，与既有 cancel 语义一致。
    """
    if operation == "cancel":
        if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
            raise BusinessRuleError("ACTIVITY_ALREADY_TERMINAL")
        changed = activity.model_copy(
            update={"status": ActivityStatus.CANCELLED, "ended_at": activity.last_settled_at,
                    "boundary_type": "cancelled", "next_interaction_at": None,
                    "interruption_reason": "agent_cancelled"}, deep=True)
        return agent.model_copy(deep=True), changed
    if operation == "pause":
        raise BusinessRuleError("SOCIAL_ACTIVITY_CANNOT_PAUSE")
    if operation != "resume":
        raise BusinessRuleError("SOCIAL_ACTIVITY_OPERATION_UNKNOWN")
    return agent, activity
