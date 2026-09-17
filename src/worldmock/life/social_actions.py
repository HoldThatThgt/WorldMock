"""@brief 社交动作的引擎侧执行：瞬时消息、交谈与通话前台活动（修订合同 L1616-1633）。

域校验与记录结构复用 life.social 纯函数；本模块补足引擎侧接线：构造 CHAT/CALL
前台活动（这两种 kind 不在可供性注册表内，不经 activities.start_activity 的
注册表 MET 查表）、按区间推进并结算社交需要账本（felt.apply_social_need_ledger
与 social.chat_settlement 的速率）。

聊天接受语义（L1625）：对方在其下一次决策开始与发起方的交谈（对方存在以本方
为伙伴的进行中 CHAT 活动）则双方各按 0.5/小时缓解，否则发起方按 0.2/小时。
model 策略联系人回复的实际模型调用沿用 work_review 的外部调用模式：引擎只登记
social.ModelReplyRequest 待宿主执行（本仓库引擎无模型直连通道，见交付报告）。
"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.life import felt, social
from worldmock.life.activities import ActivityAdvance, ActivityOrigin
from worldmock.life.body import ActivityFactors, settle_body
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    BodyState,
    StartSocialActivityRequest,
    WorldState,
)
from worldmock.life.scenario import BodyConfig

#: 交谈代谢当量（experiment_assumption：坐姿交谈约 1.5 MET）。
CHAT_MET = 1.5
#: 通话代谢当量（experiment_assumption：坐姿通话约 1.3 MET）。
CALL_MET = 1.3
#: 社交活动的资源锁：位置恒占 + 注意力（通话占用注意力，L1626）。
SOCIAL_RESOURCE_CLAIMS = frozenset({"position", "attention"})


@dataclass(frozen=True, slots=True)
class SocialStart:
    """@brief 一次社交动作受理的结果：活动与（交谈时的）邀请送达结构。"""

    agent: AgentState
    activity: ActivityState
    invite: social.SentMessage | None


def partner_presence(state: WorldState, partner_id: str) -> social.AgentPresence:
    """@brief 从世界状态解析对方的在场事实（找不到对方即业务拒绝）。"""
    matches = [agent for agent in state.agents.values() if f"agent:{agent.agent_id}" == partner_id]
    if len(matches) != 1:
        raise BusinessRuleError("SOCIAL_PARTNER_NOT_FOUND")
    other = matches[0]
    activity = state.activities.get(other.current_activity_id or "")
    active = activity is not None and activity.status == ActivityStatus.ACTIVE
    kind = activity.kind if activity is not None else None
    return social.AgentPresence(
        agent_id=partner_id, name=other.name, location=other.location_id,
        activity_family=kind.value if active and kind else "idle",
        awake=not (active and kind == ActivityKind.SLEEP),
        actively_gaming=active and kind == ActivityKind.PLAY_GAME)


def start_chat(state: WorldState, agent: AgentState, request: StartSocialActivityRequest,
               origin: ActivityOrigin, message_id: str) -> SocialStart:
    """@brief 受理交谈：共同在场且对方可交谈，作为发起方前台活动结算（L1625）。"""
    partner = partner_presence(state, request.partner_id)
    if agent.current_activity_id is not None:
        raise BusinessRuleError("RESOURCE_CONFLICT")
    invite = social.OutgoingMessage(
        message_id=message_id, sender_id=f"agent:{agent.agent_id}",
        recipient_id=request.partner_id,
        text=f"{agent.name} 邀请你交谈约 {request.duration_minutes} 分钟",
        created_at=state.current_time)
    started = social.start_chat(agent.location_id, partner, request.duration_minutes, invite)
    activity = _social_activity(agent, ActivityKind.CHAT, request.duration_minutes * 60,
                                origin, state.current_time,
                                {"partner_id": request.partner_id, "accepted": False})
    return SocialStart(agent.model_copy(update={"current_activity_id": activity.activity_id},
                                        deep=True), activity,
                       social.SentMessage(message=started.invite_message, arrival=started.arrival))


def start_call(agent: AgentState, contact: social.Contact, request: StartSocialActivityRequest,
               origin: ActivityOrigin, now: int) -> SocialStart:
    """@brief 受理通话：面向场景联系人，10-60 分钟，占用注意力（L1626）。"""
    if agent.current_activity_id is not None:
        raise BusinessRuleError("RESOURCE_CONFLICT")
    started = social.start_call(contact, request.duration_minutes)
    activity = _social_activity(agent, ActivityKind.CALL, started.duration_minutes * 60,
                                origin, now,
                                {"contact_id": started.contact_id})
    return SocialStart(agent.model_copy(update={"current_activity_id": activity.activity_id},
                                        deep=True), activity, None)


def _social_activity(agent: AgentState, kind: ActivityKind, duration_seconds: int,
                     origin: ActivityOrigin, now: int, data: dict) -> ActivityState:
    """@brief 构造 CHAT/CALL 前台活动（不经可供性注册表）。"""
    if duration_seconds < 60:
        raise BusinessRuleError("ACTIVITY_TOO_SHORT")
    return ActivityState(
        activity_id=f"activity-{origin.action_id}", agent_id=agent.agent_id, kind=kind,
        status=ActivityStatus.ACTIVE, started_at=now, planned_end_at=now + duration_seconds,
        last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=duration_seconds,
        target_id=None, resource_claims=set(SOCIAL_RESOURCE_CLAIMS),
        origin_action_id=origin.action_id, origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id, data=dict(data))


def chat_accepted(state: WorldState, initiator_id: int) -> bool:
    """@brief 判定交谈已被对方接受：对方存在以本方为伙伴的进行中 CHAT 活动。"""
    for activity in state.activities.values():
        if (activity.kind == ActivityKind.CHAT and activity.status == ActivityStatus.ACTIVE
                and activity.data.get("partner_id") == f"agent:{initiator_id}"):
            return True
    return False


def advance_social(activity: ActivityState, agent: AgentState, now: int,
                   config: BodyConfig, accepted: bool) -> ActivityAdvance:
    """@brief 推进 CHAT/CALL 活动一个区间：身体结算 + 社交需要账本（镜像 advance_activity）。"""
    if activity.status != ActivityStatus.ACTIVE:
        return ActivityAdvance(activity.model_copy(deep=True), agent.model_copy(deep=True), None, False)
    stop = min(now, activity.planned_end_at)
    seconds = stop - activity.last_settled_at
    if seconds < 0:
        raise ValueError("activity settlement time moved backwards")
    met = CHAT_MET if activity.kind == ActivityKind.CHAT else CALL_MET
    factors = ActivityFactors(met=met)
    settlement = settle_body(agent.body, activity.kind, seconds, config, factors)
    body = _social_body_relief(settlement.after, activity, seconds, accepted)
    elapsed = activity.elapsed_seconds + seconds
    completed = elapsed >= activity.planned_duration_seconds
    updates = {"last_settled_at": stop, "elapsed_seconds": elapsed}
    if completed:
        updates.update(status=ActivityStatus.COMPLETED, boundary_type="planned_end",
                       ended_at=stop, next_interaction_at=None)
    changed = activity.model_copy(update=updates, deep=True)
    current = None if completed else agent.current_activity_id
    changed_agent = agent.model_copy(update={"body": body, "current_activity_id": current}, deep=True)
    return ActivityAdvance(changed, changed_agent, settlement, completed)


def _social_body_relief(body: BodyState, activity: ActivityState, seconds: int,
                        accepted: bool) -> BodyState:
    """@brief 按活动类型应用社交需要账本缓解（速率来自 social 域常量）。"""
    hours = seconds / 3600
    if activity.kind == ActivityKind.CALL:
        exposure = felt.SocialExposure(awake_hours=hours, call_hours=hours)
        return _apply_ledger(body, exposure)
    if accepted:
        exposure = felt.SocialExposure(awake_hours=hours, conversation_hours=hours)
        return _apply_ledger(body, exposure)
    declined = social.CHAT_RELIEF_DECLINED_PER_HOUR * hours
    growth_only = _apply_ledger(body, felt.SocialExposure(awake_hours=hours))
    need = max(0.0, growth_only.social_need - declined)
    return growth_only.model_copy(update={"social_need": need}, deep=True)


def _apply_ledger(body: BodyState, exposure: felt.SocialExposure) -> BodyState:
    """@brief 经 felt 账本推进社交需要（钳制 [0, 1] 由账本承担）。"""
    need = felt.apply_social_need_ledger(body.social_need, exposure)
    return body.model_copy(update={"social_need": need}, deep=True)


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
        current = None if agent.current_activity_id == activity.activity_id else agent.current_activity_id
        return agent.model_copy(update={"current_activity_id": current}, deep=True), changed
    if operation == "pause":
        raise BusinessRuleError("SOCIAL_ACTIVITY_CANNOT_PAUSE")
    if operation != "resume":
        raise BusinessRuleError("SOCIAL_ACTIVITY_OPERATION_UNKNOWN")
    return agent, activity
