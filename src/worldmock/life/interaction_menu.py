"""@brief 地点类与人物交互菜单的声明、交集和统一动作准入判断。"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import Field, model_validator

from worldmock.errors import IntegrityError
from worldmock.life.models import FrozenModel, START_ACTIVITY_ACTIONS

#: 「自身」对象的动作（docs/dev/SPEC-agent-interaction.md「保护项修订」）：每份菜单必须保留，
#: 否则人物在该地点既不能等待也不能睡眠。其余三类保护项是工具可见性，不由地点菜单剪裁。
PROTECTED_SELF_ACTIONS = frozenset({"wait", "sleep"})

#: 稳定工具面里唯一受菜单剪裁的工具：其 action 参数取交互对象的动作枚举。
MENU_SCOPED_TOOL = "start_activity"


class InteractionMenu(FrozenModel):
    """@brief 一份只剪裁交互对象动作枚举的冻结交互菜单。"""

    allowed_actions: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_declaration(self) -> "InteractionMenu":
        """@brief 拒绝空白、重复、未知动作名和缺少「自身」保护动作的声明。"""
        _validate_names(self.allowed_actions, START_ACTIVITY_ACTIONS, "action")
        missing = PROTECTED_SELF_ACTIONS - set(self.allowed_actions)
        if missing:
            _reject(f"interaction menu is missing protected actions: {sorted(missing)}")
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
    actions = set(location_menu.allowed_actions)
    agent_menu = getattr(agent, "interaction_menu", ...)
    if agent_menu is ... or agent_menu is not None and not isinstance(agent_menu, InteractionMenu):
        _integrity_error("Interaction menu agent declaration is invalid")
    if agent_menu is not None:
        actions.intersection_update(agent_menu.allowed_actions)
    return InteractionMenu(allowed_actions=tuple(sorted(actions)))


def menu_allows_action(menu: InteractionMenu, tool_name: str, request: Any) -> bool:
    """@brief 判断本次工具调用是否获菜单允许。

    地点菜单不再剪裁工具（「保护项修订」）：除 start_activity 外的稳定工具恒可调用，
    start_activity 则按其 action 参数落在有效菜单的动作枚举内判定。

    @param menu 当前有效菜单。
    @param tool_name REQUEST_MODELS 中的稳定工具名。
    @param request 已通过对应请求模型解析的参数。
    @return 菜单允许时返回 true；领域可行性仍由原校验负责。
    """
    if tool_name != MENU_SCOPED_TOOL:
        return True
    action = _request_value(request, "action")
    return isinstance(action, str) and action in menu.allowed_actions


def _validate_names(values: tuple[str, ...], allowed: frozenset[str], label: str) -> None:
    """@brief 校验一个声明数组的文本、唯一性与闭集归属。"""
    if any(not isinstance(value, str) or not value.strip() for value in values):
        _reject(f"interaction menu {label} names must be nonblank strings")
    if len(values) != len(set(values)):
        _reject(f"interaction menu {label} names must not contain duplicates")
    unknown = set(values) - allowed
    if unknown:
        _reject(f"interaction menu contains unknown {label} names: {sorted(unknown)}")


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
