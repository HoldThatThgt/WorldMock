"""@brief 生活动作的持久结果、终态和因果事件编码。"""

from __future__ import annotations

import logging
from typing import Any

from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.activity_titles import TitleIndex, activity_snapshot
from worldmock.life.models import (ActionReceipt, ActionResult, ActionStatus, ActivityKind, ActivityState,
                                   ActivityStatus, PendingAction, StoredAction, WorldState)


def _remember_action(state: WorldState, pending: PendingAction, result: ActionResult) -> WorldState:
    """@brief 保存这条动作的回执与即时结果，供本秒的拒绝计数与活动终结时的开始动作核对。"""
    receipt = ActionReceipt(action_id=pending.action_id, round_id=pending.round_id,
                            decision_id=pending.decision_id, request_sha256=pending.request_sha256)
    stored = StoredAction(action_id=pending.action_id, tool_name=pending.tool_name,
                          request_sha256=pending.request_sha256, receipt=receipt.model_dump(mode="json"),
                          result=result.model_dump(mode="json"))
    actions = dict(state.processed_actions)
    actions[pending.action_id] = stored
    return state.model_copy(update={"processed_actions": actions}, deep=True)


def prune_processed_actions(state: WorldState) -> WorldState:
    """@brief 时钟推进时只留下规则仍会读取的动作：进行中或暂停活动的开始动作；其余的完整记录在动作事件里
    （docs/design/04-simulation-engine.md「原子性与恢复」）。

    @param state 已移到新时刻、尚未结算这次推进的世界副本
    @return processed_actions 只含这些开始动作的世界副本，其余字段原样
    """
    origins = {activity.origin_action_id for activity in state.activities.values()
               if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}}
    kept = {key: stored for key, stored in state.processed_actions.items() if key in origins}
    return state.model_copy(update={"processed_actions": kept})


def require_origin_action(state: WorldState, activity: ActivityState) -> None:
    """@brief 活动终结时开始它的那条动作必须仍在 processed_actions 里。

    @exception ProtocolError 开始动作缺失（Activity origin action is missing from processed actions）
    """
    if activity.origin_action_id not in state.processed_actions:
        logging.error("Activity %s origin action %s is missing from processed actions",
                      activity.activity_id, activity.origin_action_id)
        raise ProtocolError("Activity origin action is missing from processed actions")


def _result_draft(pending: PendingAction, result: ActionResult, event_type: str) -> EventDraft:
    """@brief 将动作结果与原请求事件建立直接因果回链。"""
    data = result.model_dump(mode="json")
    changes = data["data"].pop("resource_changes", {})
    return EventDraft(event_type=event_type, occurred_at=result.occurred_at, agent_id=result.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      activity_id=result.activity_id, caused_by=_pending_causes(pending),
                      data=data, resource_changes=changes)


def _activity_terminal_result(state: WorldState, activity, occurred_at: int,
                              index: TitleIndex) -> ActionResult:
    """@brief 将活动生命周期终态编码为最初动作的最终结果；一条动作只开始一项活动，活动终结即动作终结。"""
    statuses = {ActivityStatus.COMPLETED: ActionStatus.COMPLETED,
                ActivityStatus.CANCELLED: ActionStatus.CANCELLED,
                ActivityStatus.FAILED: ActionStatus.FAILED,
                ActivityStatus.TRUNCATED: ActionStatus.CANCELLED}
    messages = {ActivityStatus.COMPLETED: "Activity completed",
                ActivityStatus.CANCELLED: "Activity cancelled",
                ActivityStatus.FAILED: "Activity failed",
                ActivityStatus.TRUNCATED: "Activity truncated at experiment end"}
    snapshot = activity_snapshot(activity, index)
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=statuses[activity.status], occurred_at=occurred_at,
                        activity_id=activity.activity_id, message=messages[activity.status],
                        error_code=activity.interruption_reason if activity.status == ActivityStatus.FAILED else None,
                        data={"activity": snapshot, "orders": activity_orders(state, [activity])})


def _activity_terminal_draft(state: WorldState, activity, occurred_at: int,
                             additional_causes: list[str],
                             index: TitleIndex) -> EventDraft:
    """@brief 形成回链原请求且携带权威活动快照的生命周期终态。"""
    result = _activity_terminal_result(state, activity, occurred_at, index)
    data = result.model_dump(mode="json")
    data["activity"] = data["data"]["activity"]
    causes = list(dict.fromkeys([activity.origin_request_event_id, *additional_causes]))
    return EventDraft(event_type="action_result", occurred_at=occurred_at,
                      agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                      action_id=activity.origin_action_id, activity_id=activity.activity_id,
                      caused_by=causes, data=data)


def _pending_causes(pending: PendingAction) -> list[str]:
    """@brief 动作结果只回链实际登记的动作请求。"""
    return [pending.requested_event_id]


def activity_orders(state: WorldState, activities) -> list[dict]:
    """@brief 动作结果只携带这些活动实际创建的订单快照。"""
    identities = {identity for activity in activities for identity in activity.data.get("order_ids", [])}
    return [state.deliveries[identity].model_dump(mode="json") for identity in sorted(identities)]


def start_orders(before: WorldState, state: WorldState, activities) -> list[dict]:
    """@brief 开始结果携带这条动作创建的订单，以及它在驿站领取的订单在领取后的快照。

    :param before: 这条动作派发前的世界；到站待取而派发后已收货的订单就是这次领取的。
    """
    identities = {identity for activity in activities for identity in activity.data.get("order_ids", [])}
    identities |= {identity for identity, order in before.deliveries.items()
                   if order.status == "ready_for_pickup" and state.deliveries[identity].status == "delivered"}
    return [state.deliveries[identity].model_dump(mode="json") for identity in sorted(identities)]


def placed_orders(payload: dict) -> list[dict]:
    """@brief 动作结果里这条动作自己下的订单；它在驿站领取的快递只带领取后的快照，不算下单。"""
    return [order for order in payload.get("orders", [])
            if (order.get("fulfillment"), order.get("status")) != ("station_pickup", "delivered")]




def _result_event_data(result: ActionResult, state: WorldState,
                       index: TitleIndex) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        data["activity"] = activity_snapshot(state.activities[result.activity_id], index)
    return data
