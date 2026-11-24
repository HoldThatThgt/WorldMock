"""@brief 动作账本与动作结果事件的纯粹构建，统一使用 wakeup_id 归因。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.projections.commerce.action_orders import activity_orders
from worldmock.simulation_engine.observation.wording.activity_titles import TitleIndex, activity_snapshot
from worldmock.state.decisions.results import ActionResult
from worldmock.state.decisions.state import StoredAction
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActionStatus, ActivityStatus
from worldmock.state.world.world import WorldState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActionRecordSpec:
    """@brief 单条动作登记的身份与因果请求引用。"""

    #: 本次唤醒身份。
    wakeup_id: str
    #: 动作身份。
    action_id: str
    #: 世界工具名称。
    tool_name: str
    #: 规范请求摘要。
    request_sha256: str
    #: 对应 action_requested 事件身份。
    requested_event_id: str


def _remember_action(state: WorldState, spec: ActionRecordSpec, result: ActionResult) -> WorldState:
    """@brief 保存动作摘要与即时结果，供活动终结时核对开始动作。"""
    stored = StoredAction(action_id=spec.action_id, tool_name=spec.tool_name,
                          request_sha256=spec.request_sha256, result=result.model_dump(mode="json"))
    actions = dict(state.processed_actions)
    actions[spec.action_id] = stored
    return state.model_copy(update={"processed_actions": actions}, deep=True)


def prune_processed_actions(state: WorldState) -> WorldState:
    """@brief 时钟推进时只留下规则仍会读取的动作：进行中或暂停活动的开始动作。"""
    origins = {activity.origin_action_id for activity in state.activities.values()
               if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}}
    kept = {key: stored for key, stored in state.processed_actions.items() if key in origins}
    return state.model_copy(update={"processed_actions": kept})


def require_origin_action(state: WorldState, activity: ActivityState) -> None:
    """@brief 活动终结时开始它的那条动作必须仍在 processed_actions 里。"""
    if activity.origin_action_id not in state.processed_actions:
        _LOGGER.error("Activity %s origin action %s is missing from processed actions",
                      activity.activity_id, activity.origin_action_id)
        raise ProtocolError("Activity origin action is missing from processed actions")


def _result_draft(spec: ActionRecordSpec, result: ActionResult, event_type: str) -> EventDraft:
    """@brief 将动作结果与原请求事件建立直接因果回链。"""
    return _result_draft_with_causes(spec, result, event_type, [spec.requested_event_id])


def _result_draft_with_causes(
    spec: ActionRecordSpec, result: ActionResult, event_type: str, causes: list[str],
) -> EventDraft:
    """@brief 将动作拒绝或结果草稿绑定到指定因果请求事件列表。"""
    data = result.model_dump(mode="json")
    changes = data["data"].pop("resource_changes", {})
    return EventDraft(event_type=event_type, occurred_at=result.occurred_at, agent_id=result.agent_id,
                      wakeup_id=spec.wakeup_id, action_id=spec.action_id,
                      activity_id=result.activity_id, caused_by=causes,
                      data=data, resource_changes=changes)


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
                      agent_id=activity.agent_id, wakeup_id=activity.origin_wakeup_id,
                      action_id=activity.origin_action_id, activity_id=activity.activity_id,
                      caused_by=causes, data=data)


def _result_event_data(result: ActionResult, state: WorldState,
                       index: TitleIndex) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        data["activity"] = activity_snapshot(state.activities[result.activity_id], index)
    return data
