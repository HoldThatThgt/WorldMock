"""@brief 地点类与人物交互菜单的声明、交集和统一准入判断。"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import Field, model_validator

from worldmock.catalog.affordances import SURFACE_TOOLS, default_affordances
from worldmock.errors import IntegrityError
from worldmock.life.models import FrozenModel, REQUEST_MODELS, activity_start_tool

#: 每份非空菜单必须显式保留的生活工具，避免切断计划、恢复与既有活动控制。
PROTECTED_INTERACTION_TOOLS = frozenset({
    "start_basic_activity",
    "plan_day",
    "revise_day_plan",
    "set_activity_state",
    "continue_activity",
    "change_playback_speed",
    "game_action",
})

#: 每份非空菜单必须显式保留的活动类型，保证人物始终可以等待与睡眠。
PROTECTED_ACTIVITY_KINDS = frozenset({"wait", "sleep"})

_KNOWN_TOOLS = frozenset(REQUEST_MODELS)
_AFFORDANCES = default_affordances()
_KNOWN_ACTIVITY_KINDS = frozenset(_AFFORDANCES.kinds)
_CONTENT_CATEGORIES = frozenset(
    kind.name for kind in _AFFORDANCES.families["entertainment"].content_kinds
)
_LIFESTYLE_CATEGORIES = frozenset(
    kind.name for kind in _AFFORDANCES.families["lifestyle"].content_kinds
)


def _derive_activity_start_tools() -> dict[str, str]:
    """@brief 从注册表 surface 投影活动类型对应的既有启动工具。"""
    result: dict[str, str] = {}
    for name, spec in _AFFORDANCES.kinds.items():
        tools = SURFACE_TOOLS[spec.surface]
        if spec.activity_type in tools:
            result[name] = spec.activity_type
        elif len(tools) == 1:
            result[name] = tools[0]
        elif tools:
            message = f"Affordance activity {name!r} has no unique start tool"
            logging.error(message)
            raise IntegrityError(message)
    return result


_ACTIVITY_START_TOOLS = _derive_activity_start_tools()


class InteractionMenu(FrozenModel):
    """@brief 一份只引用现有生活工具与注册表活动类型的冻结交互菜单。"""

    allowed_tools: tuple[str, ...] = Field(min_length=1)
    allowed_activity_kinds: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_declaration(self) -> "InteractionMenu":
        """@brief 拒绝空白、重复、未知名称和缺少保护项的声明。"""
        _validate_names(self.allowed_tools, _KNOWN_TOOLS, "tool")
        _validate_names(self.allowed_activity_kinds, _KNOWN_ACTIVITY_KINDS, "activity kind")
        missing_tools = PROTECTED_INTERACTION_TOOLS - set(self.allowed_tools)
        if missing_tools:
            _reject(f"interaction menu is missing protected tools: {sorted(missing_tools)}")
        missing_kinds = PROTECTED_ACTIVITY_KINDS - set(self.allowed_activity_kinds)
        if missing_kinds:
            _reject(f"interaction menu is missing protected activity kinds: {sorted(missing_kinds)}")
        return self


def resolve_interaction_menu(scenario: Any, agent_id: int, location_id: str) -> InteractionMenu:
    """@brief 按权威地点类与人物声明求交集并返回确定性排序的有效菜单。

    @param scenario 已通过场景准入的 LifeScenario 或 RuntimeScenario。
    @param agent_id 当前人物身份。
    @param location_id 当前权威地点身份。
    @return 本轮有效交互菜单。
    """
    agent = _unique_item(scenario.agents, "agent_id", agent_id, "agent")
    location = _unique_item(scenario.locations, "location_id", location_id, "location")
    menus = getattr(scenario, "interaction_menus_by_location_kind", None)
    if not isinstance(menus, dict):
        _integrity_error("Interaction menu location mapping is unavailable")
    location_menu = menus.get(location.kind)
    if not isinstance(location_menu, InteractionMenu):
        _integrity_error("Interaction menu for the current location kind is unavailable")
    tools = set(location_menu.allowed_tools)
    kinds = set(location_menu.allowed_activity_kinds)
    agent_menu = getattr(agent, "interaction_menu", ...)
    if agent_menu is ... or agent_menu is not None and not isinstance(agent_menu, InteractionMenu):
        _integrity_error("Interaction menu agent declaration is invalid")
    if agent_menu is not None:
        tools.intersection_update(agent_menu.allowed_tools)
        kinds.intersection_update(agent_menu.allowed_activity_kinds)
    return InteractionMenu(
        allowed_tools=tuple(sorted(tools)),
        allowed_activity_kinds=tuple(sorted(kinds)),
    )


def menu_allows_activity(menu: InteractionMenu, kind: str) -> bool:
    """@brief 判断菜单是否同时允许活动类型及其既有启动工具。

    @param menu 已解析的有效菜单。
    @param kind 注册表活动类型；社交活动 chat/call 只核对社交启动工具。
    @return 活动可由当前菜单新建或恢复时返回 true。
    """
    normalized = _normalize_activity_kind(kind)
    if normalized in {"chat", "call"}:
        return "start_social_activity" in menu.allowed_tools
    tool = _ACTIVITY_START_TOOLS.get(normalized)
    return bool(
        tool
        and tool in menu.allowed_tools
        and normalized in menu.allowed_activity_kinds
    )


def menu_allows_category(menu: InteractionMenu, category: str) -> bool:
    """@brief 按既有下钻品类到启动资格的映射判断菜单是否允许该参数。

    @param menu 已解析的有效菜单。
    @param category 现有观察声明的下钻品类；真实存在性仍由原入口校验。
    @return 工具与对应启动资格均允许时返回 true。
    """
    if "browse_activities" not in menu.allowed_tools:
        return False
    if category == "game_session":
        return menu_allows_activity(menu, "play_game")
    if category == "work_contract":
        return menu_allows_activity(menu, "work")
    if category == "foods":
        return "purchase" in menu.allowed_tools or menu_allows_activity(menu, "consume")
    if category in _CONTENT_CATEGORIES:
        return menu_allows_activity(menu, "watch")
    if category in _LIFESTYLE_CATEGORIES:
        return menu_allows_activity(menu, "lifestyle")
    return False


def menu_allows_action(
    menu: InteractionMenu,
    tool_name: str,
    request: Any,
    activity_kind: str | None = None,
) -> bool:
    """@brief 统一判断工具、共享活动参数、下钻品类与暂停恢复是否获菜单允许。

    @param menu 当前有效菜单。
    @param tool_name REQUEST_MODELS 中的生活工具名。
    @param request 已通过对应请求模型解析的参数。
    @param activity_kind 恢复操作所指活动的权威类型；非恢复或身份未知时为 null。
    @return 菜单允许时返回 true；领域可行性仍由原校验负责。
    """
    if tool_name not in menu.allowed_tools:
        return False
    if tool_name == "start_parallel_activities":
        return all(menu_allows_action(menu, activity_start_tool(item), item) for item in request.activities)
    if tool_name in {"start_basic_activity", "start_targeted_activity"}:
        return menu_allows_activity(menu, _request_value(request, "activity_type"))
    if tool_name == "start_travel":
        return menu_allows_activity(menu, "travel")
    if tool_name == "consume":
        return menu_allows_activity(menu, "consume")
    if tool_name == "browse_activities":
        return menu_allows_category(menu, _request_value(request, "category"))
    if tool_name == "set_activity_state" and _request_value(request, "operation") == "resume":
        return activity_kind is None or menu_allows_activity(menu, activity_kind)
    return True


def _validate_names(values: tuple[str, ...], allowed: frozenset[str], label: str) -> None:
    """@brief 校验一个声明数组的文本、唯一性与闭集归属。"""
    if any(not isinstance(value, str) or not value.strip() for value in values):
        _reject(f"interaction menu {label} names must be nonblank strings")
    if len(values) != len(set(values)):
        _reject(f"interaction menu {label} names must not contain duplicates")
    unknown = set(values) - allowed
    if unknown:
        _reject(f"interaction menu contains unknown {label} names: {sorted(unknown)}")


def _normalize_activity_kind(kind: Any) -> str:
    """@brief 把 lifestyle 条目参数归一为注册表活动类型，其余取字符串值。"""
    value = str(kind)
    return "lifestyle" if value.startswith("lifestyle:") else value


def _request_value(request: Any, field: str) -> Any:
    """@brief 从已解析模型或测试用映射读取一个请求字段。"""
    return request.get(field) if isinstance(request, dict) else getattr(request, field, None)


def _unique_item(items: tuple[Any, ...], field: str, value: Any, label: str) -> Any:
    """@brief 从已准入场景取唯一身份对象，损坏运行时失败关闭。"""
    matches = [item for item in items if getattr(item, field, None) == value]
    if len(matches) != 1:
        _integrity_error(f"Interaction menu {label} identity is missing or duplicated")
    return matches[0]


def _reject(message: str) -> None:
    """@brief 记录英文声明错误并以 Pydantic 可承接的 ValueError 拒绝。"""
    logging.error(message)
    raise ValueError(message)


def _integrity_error(message: str) -> None:
    """@brief 记录英文运行时完整性错误并终止。"""
    logging.error(message)
    raise IntegrityError(message)
