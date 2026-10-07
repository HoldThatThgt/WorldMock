"""@brief 对持久活动核对发布目录中的动作、内容、应用、交流对象及路线身份。"""

from __future__ import annotations

import logging

from worldmock.errors import IntegrityError
from worldmock.exchange.types import ActionCategory, AppPurpose
from worldmock.life.models import ActivityKind, ActivityState, WorldState
from worldmock.life.travel import persisted_trip

_LOGGER = logging.getLogger(__name__)
#: 数据集动作快照的唯一键集合。
_ACTION_FIELDS = frozenset({"action_id", "category", "app_id", "manner", "communication_mode", "counterpart",
                            "content_id",
                           "start_position_seconds", "pinned_by_push_id", "draw_index", "played"})


def validate_activity_targets(state: WorldState, rules, scenario) -> None:
    """@brief 用冻结场景与发布目录核对全部持久活动，不读取退休活动类型。"""
    people = {person.agent_id: person for person in scenario.agents}
    for activity in state.activities.values():
        if not _target_is_valid(activity, state, rules, people):
            _LOGGER.error("Persisted activity target is invalid: %s", activity.activity_id)
            raise IntegrityError("Persisted activity target is invalid")


def _target_is_valid(activity: ActivityState, state: WorldState, rules, people: dict) -> bool:
    """@brief 原生活活动只有路线身份；统一动作必须绑定同一发布动作和内容。"""
    if activity.kind == ActivityKind.TRAVEL:
        return _travel_target_is_valid(activity, rules)
    if activity.destination_id is not None:
        return False
    if activity.kind != ActivityKind.ACTION:
        return True
    data = activity.data
    if (set(data) != _ACTION_FIELDS or activity.agent_id not in people
            or not isinstance(data["action_id"], str) or not isinstance(data["content_id"], str)):
        return False
    action = rules.actions.get(data["action_id"])
    if (action is None or data["category"] != action.category or data["manner"] != action.manner
            or data["communication_mode"] != action.communication_mode):
        return False
    app = data["app_id"]
    if (app is None and action.app_ids) or (app is not None and app not in action.app_ids):
        return False
    content = rules.contents.get(data["content_id"])
    if not _content_binding_is_valid(content, action.action_id, app):
        return False
    if not _position_is_valid(data["start_position_seconds"], content.duration_seconds):
        return False
    if not 0 <= activity.media_position_seconds <= content.duration_seconds:
        return False
    index, pinned = data["draw_index"], data["pinned_by_push_id"]
    if type(index) is not int or index < 0 or (pinned is not None and (not isinstance(pinned, str) or not pinned)):
        return False
    return (_played_is_valid(data["played"], rules, action.action_id, app)
            and _counterpart_is_valid(activity, action, state, people[activity.agent_id]))


def _content_binding_is_valid(content, action_id: str, app_id: str | None) -> bool:
    """@brief 内容只属于当前动作，应用必须属于该条内容声明的应用子集。"""
    return (content is not None and content.action_id == action_id
            and (app_id in content.app_ids if app_id is not None else not content.app_ids))


def _played_is_valid(played: list, rules, action_id: str, app_id: str | None) -> bool:
    """@brief 已完成内容保留本动作的真实消费区间，不接受外来内容或超出时长的区间。"""
    if not isinstance(played, list):
        return False
    for row in played:
        if not isinstance(row, dict) or set(row) != {"content_id", "from_position", "to_position"}:
            return False
        if not isinstance(row["content_id"], str):
            return False
        content = rules.contents.get(row["content_id"])
        if not _content_binding_is_valid(content, action_id, app_id):
            return False
        if (not _position_is_valid(row["from_position"], content.duration_seconds)
                or row["from_position"] == content.duration_seconds or row["to_position"] != content.duration_seconds):
            return False
    return True


def _position_is_valid(position, duration_seconds: int) -> bool:
    """@brief 消费位置必须是有限非负秒数，布尔值不代表秒数。"""
    return type(position) in {int, float} and 0 <= position <= duration_seconds


def _counterpart_is_valid(activity: ActivityState, action, state: WorldState, owner) -> bool:
    """@brief 无应用社交引用另一真实人物；应用社交引用本人登记的联系人。"""
    counterpart = activity.data["counterpart"]
    if action.category != ActionCategory.SOCIAL:
        return counterpart is None
    if activity.data["app_id"] is None:
        return isinstance(counterpart, str) and counterpart in {
            f"agent:{agent_id}" for agent_id in state.agents if agent_id != activity.agent_id}
    return isinstance(counterpart, str) and counterpart in {contact.contact_id for contact in owner.contacts}


def _travel_target_is_valid(activity: ActivityState, rules) -> bool:
    """@brief 原始起点、终点、方式、路径及米数必须属于同一发布图。"""
    navigation = activity.data.get("navigation_app_id")
    if navigation is not None:
        app = rules.apps.get(navigation) if isinstance(navigation, str) else None
        if app is None or AppPurpose.NAVIGATION not in app.purposes:
            return False
    try:
        persisted_trip(rules.graph, activity)
    except (IntegrityError, KeyError, ValueError):
        return False
    return True
