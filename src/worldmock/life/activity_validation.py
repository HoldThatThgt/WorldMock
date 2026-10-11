"""@brief 恢复严格核对真实容器、内容进度和冻结行程。"""
from __future__ import annotations
import logging
import math
from worldmock.errors import IntegrityError
from worldmock.life.interact import RELIEF_PER_HOUR
from worldmock.life.models import ActivityKind, WorldState
from worldmock.life.travel import persisted_trip

_CONTENT_FIELDS = {"action", "app_id", "content_id", "start_position_seconds", "pinned_by_push_id", "accepted_event_id",
                   "draw_index", "played", "exertion", "relaxation_relief_per_hour", "social_relief_per_hour"}
_BROWSE_FIELDS = {"action", "app_id", "content_id", "exertion", "relaxation_relief_per_hour",
                  "social_relief_per_hour", "accepted_event_id"}

def validate_activity_targets(state: WorldState, catalog, persona) -> None:
    """@brief 所有持久活动必须属于唯一人物和同一冻结目录，不接受退役字段。"""
    for activity in state.activities.values():
        if not _target_is_valid(activity, catalog, persona):
            logging.error("Persisted activity target is invalid: %s", activity.activity_id)
            raise IntegrityError("Persisted activity target is invalid")

def _target_is_valid(activity, catalog, persona) -> bool:
    """@brief 仅互动含内容容器，出行按原始天气和完整图核对。"""
    if activity.agent_id != persona.agent_id or activity.location_id not in catalog.locations:
        return False
    if activity.kind == ActivityKind.TRAVEL:
        return _travel_target_is_valid(activity, catalog)
    if activity.destination_id is not None:
        return False
    if activity.kind != ActivityKind.INTERACT:
        return True
    data = activity.data
    if not isinstance(data.get("accepted_event_id"), str) or not data["accepted_event_id"]:
        return False
    if data.get("action") != "interact" or "content_id" not in data or "app_id" not in data:
        return False
    app = catalog.apps.get(data["app_id"]) if isinstance(data["app_id"], str) else None
    if data["app_id"] is not None and (app is None or app.role != "content"):
        return False
    if data["content_id"] is None:
        return (set(data) == _BROWSE_FIELDS and app is not None and app.browse_duration_seconds is not None
                and data["exertion"] == "idle" and data["relaxation_relief_per_hour"] == 0
                and data["social_relief_per_hour"] == 0)
    content = catalog.contents.get(data["content_id"])
    if set(data) != _CONTENT_FIELDS or not _content_binding(content, activity, catalog):
        return False
    if (data["exertion"] != content.exertion
            or data["relaxation_relief_per_hour"] != RELIEF_PER_HOUR[content.relaxation_relief]
            or data["social_relief_per_hour"] != RELIEF_PER_HOUR[content.social_relief]):
        return False
    if not _position_is_valid(data["start_position_seconds"], content.duration_seconds):
        return False
    if not _position_is_valid(activity.media_position_seconds, content.duration_seconds):
        return False
    index, pinned = data["draw_index"], data["pinned_by_push_id"]
    if type(index) is not int or index < 0 or (pinned is not None and (not isinstance(pinned, str) or not pinned)):
        return False
    return _played_is_valid(data["played"], activity, catalog)

def _content_binding(content, activity, catalog) -> bool:
    """@brief app 绑定与当前活动开始地点类型分别验证，不能借用另一侧候选。"""
    if content is None:
        return False
    if activity.data["app_id"] is not None:
        return activity.data["app_id"] in content.app_ids
    return catalog.locations[activity.location_id].kind in content.location_kinds

def _played_is_valid(played, activity, catalog) -> bool:
    """@brief 已完成消费区间仍须属于原容器且达到真实内容末端。"""
    if not isinstance(played, list):
        return False
    for row in played:
        if not isinstance(row, dict) or set(row) != {"content_id", "from_position", "to_position"}:
            return False
        content = catalog.contents.get(row["content_id"])
        if not _content_binding(content, activity, catalog):
            return False
        if not _position_is_valid(row["from_position"], content.duration_seconds):
            return False
        if row["from_position"] == content.duration_seconds or row["to_position"] != content.duration_seconds:
            return False
    return True

def _position_is_valid(position, duration_seconds: int) -> bool:
    """@brief 秒数必须有限且不超过内容总长，布尔值不是秒数。"""
    return type(position) in {int, float} and math.isfinite(position) and 0 <= position <= duration_seconds

def _travel_target_is_valid(activity, catalog) -> bool:
    """@brief 冻结行程按原出行方式和原天气核对，不随之后天气重新规划。"""
    if "navigation_app_id" in activity.data:
        return False
    try:
        persisted_trip(catalog, activity)
    except (IntegrityError, KeyError, ValueError):
        logging.error("Persisted travel plan does not match its catalog")
        return False
    return True
