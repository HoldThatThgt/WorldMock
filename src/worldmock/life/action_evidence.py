"""@brief 生活动作的持久结果、终态和因果事件编码。"""

from __future__ import annotations

from typing import Any

from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.activity_titles import TitleIndex, activity_snapshot
from worldmock.life.models import (ActionReceipt, ActionResult, ActionStatus, ActivityKind,
                                   ActivityStatus, PendingAction, StoredAction, WorldState)


def _remember_action(state: WorldState, pending: PendingAction, result: ActionResult) -> WorldState:
    """@brief 保存终态结果供同一 action_id 严格幂等复用。"""
    receipt = ActionReceipt(action_id=pending.action_id, round_id=pending.round_id,
                            decision_id=pending.decision_id, request_sha256=pending.request_sha256)
    stored = StoredAction(action_id=pending.action_id, tool_name=pending.tool_name,
                          request_sha256=pending.request_sha256, receipt=receipt.model_dump(mode="json"),
                          result=result.model_dump(mode="json"))
    actions = dict(state.processed_actions)
    actions[pending.action_id] = stored
    return state.model_copy(update={"processed_actions": actions}, deep=True)


def _terminal_action_records(state: WorldState, result: ActionResult) -> dict[str, StoredAction]:
    """@brief 以活动最终结果替换最初动作的持久幂等结果。"""
    if result.action_id not in state.processed_actions:
        raise ProtocolError("Activity origin action is missing from processed actions")
    actions = dict(state.processed_actions)
    stored = actions[result.action_id]
    actions[result.action_id] = stored.model_copy(update={"result": result.model_dump(mode="json")}, deep=True)
    return actions


def _result_draft(pending: PendingAction, result: ActionResult, event_type: str) -> EventDraft:
    """@brief 将动作结果与原请求事件建立直接因果回链。"""
    data = result.model_dump(mode="json")
    changes = data["data"].pop("resource_changes", {})
    return EventDraft(event_type=event_type, occurred_at=result.occurred_at, agent_id=result.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      activity_id=result.activity_id, caused_by=_pending_causes(pending),
                      data=data, resource_changes=changes)


def _origin_terminal_result(state: WorldState, activity, occurred_at: int,
                            index: TitleIndex) -> ActionResult | None:
    """@brief 同一原动作的全部子活动结束后，给原动作写一次完整终态。"""
    peers = [row for row in state.activities.values() if row.origin_action_id == activity.origin_action_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if any(row.status not in terminal for row in peers):
        return None
    if len(peers) == 1:
        return _activity_terminal_result(state, activity, occurred_at, index)
    statuses = {row.status for row in peers}
    if ActivityStatus.FAILED in statuses:
        status = ActionStatus.FAILED
    elif statuses <= {ActivityStatus.COMPLETED}:
        status = ActionStatus.COMPLETED
    else:
        status = ActionStatus.CANCELLED
    rows = [activity_snapshot(row, index) for row in sorted(peers, key=lambda item: item.activity_id)]
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=status, occurred_at=occurred_at, message="Activities finished",
                        data={"activities": rows, "orders": activity_orders(state, peers)})


def _origin_terminal_draft(state: WorldState, activity, occurred_at: int,
                           additional_causes: list[str], index: TitleIndex) -> EventDraft | None:
    """@brief 为原动作形成唯一终态事件；未全部结束时只留活动进度事件。"""
    result = _origin_terminal_result(state, activity, occurred_at, index)
    if result is None:
        return None
    if result.activity_id is not None:
        return _activity_terminal_draft(state, activity, occurred_at, additional_causes, index)
    causes = list(dict.fromkeys([activity.origin_request_event_id, *additional_causes]))
    return EventDraft(event_type="action_result", occurred_at=occurred_at,
                      agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                      action_id=activity.origin_action_id, caused_by=causes,
                      data=result.model_dump(mode="json"))


def _activity_terminal_result(state: WorldState, activity, occurred_at: int,
                              index: TitleIndex) -> ActionResult:
    """@brief 将活动生命周期终态编码为最初动作的最终结果。"""
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


def collected_orders(before: WorldState, after: WorldState) -> list[dict]:
    """@brief 这次受理在驿站领取的商品履约记录在领取后的快照：受理前已到站、受理后已收货。"""
    return [order.model_dump(mode="json") for identity, order in sorted(after.deliveries.items())
            if order.status == "delivered" and identity in before.deliveries
            and before.deliveries[identity].status == "ready_for_pickup"]










def _result_event_data(result: ActionResult, state: WorldState,
                       index: TitleIndex) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        data["activity"] = activity_snapshot(state.activities[result.activity_id], index)
    return data
