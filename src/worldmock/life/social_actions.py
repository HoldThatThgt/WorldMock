"""@brief 统一 social 动作的共同在场邀请、真实远程通信和实际秒数缓解。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.exchange.constants import CHAT_RELIEF_DECLINED_PER_HOUR
from worldmock.exchange.types import ActionCategory
from worldmock.life import felt, social
from worldmock.life.activities import ActivityAdvance, advance_activity, advance_activity_shared
from worldmock.life.body import apply_social_ledger
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState, WorldState
from worldmock.life.models import active_activities
from worldmock.life.scenario import BodyConfig
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

PARTNER_ENDED = "partner_ended"


@dataclass(frozen=True, slots=True)
class SocialStart:
    """@brief 已选内容的社交活动及新邀请；接受保留双方各自内容。"""

    agent: AgentState
    activity: ActivityState
    invite: social.SentMessage | None
    accepted: ActivityState | None = None


def _is_chat(activity: ActivityState) -> bool:
    """@brief 共同在场社交由统一动作的类别与空 app 身份判断。"""
    return (activity.kind == ActivityKind.ACTION and activity.data["category"] == ActionCategory.SOCIAL
            and activity.data["app_id"] is None)


def partner_presence(state: WorldState, partner_id: str) -> social.AgentPresence:
    """@brief 实际人物途中不算共同在场，睡眠时不可交谈。"""
    matches = [agent for agent in state.agents.values() if f"agent:{agent.agent_id}" == partner_id]
    if len(matches) != 1:
        raise BusinessRuleError("SOCIAL_PARTNER_NOT_FOUND")
    other = matches[0]
    active = active_activities(state, other.agent_id)
    kinds = {activity.kind for activity in active}
    return social.AgentPresence(agent_id=partner_id, name=other.name,
        location=other.location_id if ActivityKind.TRAVEL not in kinds else f"途中:{other.agent_id}",
        activity_family=active[0].kind.value if active else "idle", awake=ActivityKind.SLEEP not in kinds)


def pair_chat(state: WorldState, agent_id: int, partner_id: str) -> ActivityState | None:
    """@brief 同一人物与同一对方至多有一项进行中的共同在场社交。"""
    return next((activity for activity in active_activities(state, agent_id)
                 if _is_chat(activity) and activity.data["counterpart"] == partner_id), None)


def start_chat(state: WorldState, activity: ActivityState, message_id: str) -> SocialStart:
    """@brief 发起邀请或接受对方已有社交；接受可剩不到一分钟，不重新选择内容。"""
    agent = state.agents[activity.agent_id]
    counterpart = activity.data["counterpart"]
    partner = partner_presence(state, counterpart)
    existing = pair_chat(state, agent.agent_id, counterpart)
    if existing is not None and existing.activity_id != activity.activity_id:
        raise BusinessRuleError("CHAT_ALREADY_ACTIVE")
    if partner.location != agent.location_id or not partner.awake:
        raise BusinessRuleError("CHAT_PARTNER_NOT_AVAILABLE")
    invitation = pair_chat(state, int(counterpart.split(":", 1)[1]), f"agent:{agent.agent_id}")
    if invitation is not None:
        return SocialStart(agent.model_copy(deep=True), activity, None, invitation)
    invite = social.OutgoingMessage(message_id=message_id, sender_id=f"agent:{agent.agent_id}",
        recipient_id=counterpart, text=f"{agent.name} 邀请你交谈", created_at=state.current_time)
    started = social.start_chat(agent.location_id, partner, activity.planned_end_at - state.current_time, invite)
    return SocialStart(agent.model_copy(deep=True), activity,
                       social.SentMessage(message=started.invite_message, arrival=started.arrival))


def start_remote_social(activity: ActivityState, contact: social.Contact,
                        communication_mode: str) -> social.CallStart | None:
    """@brief 文字只记录内容主题；语音才调用真实通话域函数，二者不生成对方回复。"""
    if activity.data["counterpart"] != contact.contact_id:
        raise BusinessRuleError("CONTACT_NOT_FOUND")
    if communication_mode == "text":
        return None
    if communication_mode != "voice":
        logging.error("Remote social action requires text or voice communication mode")
        raise ValueError("remote social action requires text or voice communication mode")
    return social.start_call(contact, activity.planned_end_at - activity.last_settled_at)


def shared_chat_seconds(state: WorldState, activity: ActivityState, target: int) -> int:
    """@brief 按区间开始事实取双方实际社交重叠秒数，不受人物推进顺序影响。"""
    start = activity.last_settled_at
    if not _is_chat(activity):
        return 0
    counterpart = activity.data["counterpart"]
    if not counterpart.startswith("agent:"):
        return 0
    stops = [(other.planned_end_at if other.status == ActivityStatus.ACTIVE else other.ended_at)
             for other in state.activities.values()
             if other.agent_id == int(counterpart.split(":", 1)[1]) and _is_chat(other)
             and other.data["counterpart"] == f"agent:{activity.agent_id}" and other.started_at <= start
             and (other.status == ActivityStatus.ACTIVE or (other.ended_at or start) > start)]
    return max(0, min(target, activity.planned_end_at, max(stops)) - start) if stops else 0


def advance_social(activity: ActivityState, agent: AgentState, now: int,
                   config: BodyConfig, shared_seconds: int) -> ActivityAdvance:
    """@brief 统一活动真实身体推进后，单独应用社交账本且保留内容游标。"""
    advance = advance_activity(activity, agent, now, config)
    seconds = advance.activity.last_settled_at - activity.last_settled_at
    body = _social_body_relief(advance.agent.body, activity, seconds, shared_seconds)
    return ActivityAdvance(advance.activity, advance.agent.model_copy(update={"body": body}, deep=True),
                           advance.settlement, advance.completed)


def advance_social_shared(activity: ActivityState, agent: AgentState, now: int,
                          settlement, shared_seconds: int) -> ActivityAdvance:
    """@brief 共同身体只结算一次，社交按本活动真实区间推进游标与缓解。"""
    advance = advance_activity_shared(activity, agent, now, settlement)
    seconds = advance.activity.last_settled_at - activity.last_settled_at
    body = _social_body_relief(advance.agent.body, activity, seconds, shared_seconds)
    return ActivityAdvance(advance.activity, advance.agent.model_copy(update={"body": body}, deep=True),
                           advance.settlement, advance.completed)


def _social_body_relief(body: BodyState, activity: ActivityState, seconds: int,
                        shared_seconds: int) -> BodyState:
    """@brief 远程文字与语音按0.4每小时；当面共同秒数0.5，其余发起秒数0.2。"""
    hours = seconds / 3600
    if activity.data["app_id"] is not None:
        return apply_social_ledger(body, felt.SocialExposure(awake_hours=hours, remote_hours=hours))
    shared = min(max(shared_seconds, 0), seconds) / 3600
    after = apply_social_ledger(body, felt.SocialExposure(awake_hours=hours, conversation_hours=shared))
    return after.model_copy(update={"social_need": max(0.0,
        after.social_need - CHAT_RELIEF_DECLINED_PER_HOUR * (hours - shared))}, deep=True)


def end_partner_chats(before: WorldState, after: WorldState) -> tuple[WorldState, list[int]]:
    """@brief 一方结束的同一秒结束对方共同在场社交，不改变对方已消费的内容位置。"""
    ended = [activity for activity in before.activities.values()
             if _is_chat(activity) and activity.status == ActivityStatus.ACTIVE
             and after.activities[activity.activity_id].status != ActivityStatus.ACTIVE]
    activities, partners = dict(after.activities), []
    for activity in sorted(ended, key=lambda item: item.activity_id):
        counterpart = activity.data["counterpart"]
        other = pair_chat(after, int(counterpart.split(":", 1)[1]), f"agent:{activity.agent_id}")
        if other is None:
            continue
        activities[other.activity_id] = other.model_copy(update={"status": ActivityStatus.COMPLETED,
            "ended_at": after.current_time, "next_interaction_at": None, "boundary_type": PARTNER_ENDED,
            "interruption_reason": PARTNER_ENDED}, deep=True)
        partners.append(other.agent_id)
    return (after.model_copy(update={"activities": activities}, deep=True), partners) if partners else (after, [])


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
