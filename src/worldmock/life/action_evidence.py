"""@brief 生活动作的持久结果、终态和因果事件编码。"""

from __future__ import annotations

from typing import Any

from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
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
    for field in ("resource_changes", "category_matches"):
        if field in result.data:
            data[field] = result.data[field]
    return EventDraft(event_type=event_type, occurred_at=result.occurred_at, agent_id=result.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      activity_id=result.activity_id, caused_by=_pending_causes(pending),
                      data=data)


def _origin_terminal_result(state: WorldState, activity, occurred_at: int,
                            title: str | None = None) -> ActionResult | None:
    """@brief 同一原动作的全部子活动结束后，给原动作写一次完整终态。"""
    peers = [row for row in state.activities.values() if row.origin_action_id == activity.origin_action_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if any(row.status not in terminal for row in peers):
        return None
    if len(peers) == 1:
        return _activity_terminal_result(activity, occurred_at, title)
    statuses = {row.status for row in peers}
    if ActivityStatus.FAILED in statuses:
        status = ActionStatus.FAILED
    elif statuses <= {ActivityStatus.COMPLETED}:
        status = ActionStatus.COMPLETED
    else:
        status = ActionStatus.CANCELLED
    rows = [row.model_dump(mode="json") for row in sorted(peers, key=lambda item: item.activity_id)]
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=status, occurred_at=occurred_at, message="Activities finished",
                        data={"activities": rows})


def _origin_terminal_draft(state: WorldState, activity, occurred_at: int,
                           additional_causes: list[str], title: str | None = None) -> EventDraft | None:
    """@brief 为原动作形成唯一终态事件；未全部结束时只留活动进度事件。"""
    result = _origin_terminal_result(state, activity, occurred_at, title)
    if result is None:
        return None
    if result.activity_id is not None:
        return _activity_terminal_draft(activity, occurred_at, additional_causes, title)
    causes = list(dict.fromkeys([activity.origin_request_event_id, *additional_causes]))
    return EventDraft(event_type="action_result", occurred_at=occurred_at,
                      agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                      action_id=activity.origin_action_id, caused_by=causes,
                      data=result.model_dump(mode="json"))


def _activity_terminal_result(activity, occurred_at: int,
                              title: str | None = None) -> ActionResult:
    """@brief 将活动生命周期终态编码为最初动作的最终结果。"""
    statuses = {ActivityStatus.COMPLETED: ActionStatus.COMPLETED,
                ActivityStatus.CANCELLED: ActionStatus.CANCELLED,
                ActivityStatus.FAILED: ActionStatus.FAILED,
                ActivityStatus.TRUNCATED: ActionStatus.CANCELLED}
    messages = {ActivityStatus.COMPLETED: "Activity completed",
                ActivityStatus.CANCELLED: "Activity cancelled",
                ActivityStatus.FAILED: "Activity failed",
                ActivityStatus.TRUNCATED: "Activity truncated at experiment end"}
    snapshot = activity.model_dump(mode="json")
    if title is not None:
        snapshot["title"] = title
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=statuses[activity.status], occurred_at=occurred_at,
                        activity_id=activity.activity_id, message=messages[activity.status],
                        error_code=activity.interruption_reason if activity.status == ActivityStatus.FAILED else None,
                        data={"activity": snapshot, **({"browse_result": activity.data["revealed"]}
                              if activity.kind == ActivityKind.BROWSE and "revealed" in activity.data else {})})


def _activity_terminal_draft(activity, occurred_at: int,
                             additional_causes: list[str],
                             title: str | None = None) -> EventDraft:
    """@brief 形成回链原请求且携带权威活动快照的生命周期终态。"""
    result = _activity_terminal_result(activity, occurred_at, title)
    data = result.model_dump(mode="json")
    data["activity"] = data["data"]["activity"]
    causes = list(dict.fromkeys([activity.origin_request_event_id, *additional_causes]))
    return EventDraft(event_type="action_result", occurred_at=occurred_at,
                      agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                      action_id=activity.origin_action_id, activity_id=activity.activity_id,
                      caused_by=causes, data=data)


def _pending_causes(pending: PendingAction) -> list[str]:
    """@brief 将动作请求和适用的独立工作验收作为直接原因。"""
    causes = [pending.requested_event_id]
    if pending.work_review_event_id:
        causes.append(pending.work_review_event_id)
    return causes


def _work_review_request_draft(now: int, pending: PendingAction, link: tuple[str, int, str | None],
                               request: dict) -> EventDraft:
    """@brief 形成系统私有的完整工作验收模型请求证据。"""
    call_id, attempt, previous_event_id = link
    causes = [pending.requested_event_id]
    if previous_event_id:
        causes.append(previous_event_id)
    data = {"phase": "request", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": request, "response": None, "error": None}
    return EventDraft(event_type="model_request", occurred_at=now, agent_id=pending.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      caused_by=causes, visibility="system",
                      source_kind="model", data=data)


def _work_review_failure_draft(now: int, pending: PendingAction,
                               link: tuple[str, str, int], error: BaseException) -> EventDraft:
    """@brief 形成工作验收配置或传输失败证据。"""
    call_id, request_event_id, attempt = link
    data = {"phase": "failure", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": None, "response": None, "error": repr(error)}
    return _work_review_terminal_draft(now, pending, request_event_id, data)


def _work_review_response_draft(now: int, pending: PendingAction,
                                link: tuple[str, str, int], outcome: tuple) -> EventDraft:
    """@brief 形成带原始响应、用量和本地验收结论的工作评审证据。"""
    call_id, request_event_id, attempt = link
    raw, decision, error = outcome
    data = {"phase": "response", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": None, "response": raw,
            "usage": raw.get("usage") if isinstance(raw, dict) else None,
            "review_decision": decision.model_dump(mode="json") if decision else None,
            "error": repr(error) if error else None}
    return _work_review_terminal_draft(now, pending, request_event_id, data)


def _work_review_terminal_draft(now: int, pending: PendingAction,
                                request_event_id: str, data: dict) -> EventDraft:
    """@brief 将工作验收终态直接回链其唯一请求。"""
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=pending.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def _result_event_data(result: ActionResult, state: WorldState,
                       title: str | None = None) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        snapshot = state.activities[result.activity_id].model_dump(mode="json")
        if title is not None:
            snapshot["title"] = title
        data["activity"] = snapshot
    return data


