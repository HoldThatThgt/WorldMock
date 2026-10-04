"""@brief 地点类与人物交互菜单的声明、交集，人物动词与方式的词表，以及按私有绑定解析的统一动作准入判断。

人物所选的（对象，动词，方式）经本轮观察的私有绑定映射为内部标准动作（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章
「请求形状与菜单」）；菜单、习惯归并与证据都按映射后的内部动作判定，不按短语反解。
"""

from __future__ import annotations
from worldmock.life import vocabulary

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import Field, model_validator

from worldmock.errors import IntegrityError
from worldmock.life.models import FrozenModel
from worldmock.catalog.affordances import START_ACTIVITY_ACTIONS

#: 「自身」对象的动作（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「保护项修订」）：每份菜单必须保留，
#: 否则人物在该地点既不能等待也不能睡眠。人物所见分别是动词 interact 的「自己」对象（方式「等待」）与动词 sleep。
PROTECTED_SELF_ACTIONS = frozenset({"wait", "sleep"})

#: 稳定工具面里唯一受菜单剪裁的工具：其对象的动作按映射后的内部动作由菜单判定。
MENU_SCOPED_TOOL = "start_activity"
#: 指名购买的占位绑定类别：obtain 的目标不在本轮列出时，受理前由环境按名称解析为门店供给。
NAMED_SUPPLY_KIND = "named_supply"


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


def menu_allows_action(menu: InteractionMenu, tool_name: str, canonical_actions: Sequence[str]) -> bool:
    """@brief 判断本次工具调用是否获菜单允许。

    地点菜单不再剪裁工具（「保护项修订」）：除 start_activity 外的稳定工具恒可调用；start_activity 的各项先经本轮
    私有绑定映射为内部标准动作（resolve_selection），再逐项落在有效菜单的动作闭集内判定。

    @param menu 当前有效菜单。
    @param tool_name REQUEST_MODELS 中的稳定工具名。
    @param canonical_actions start_activity 各项映射后的内部标准动作（其他工具为空）。
    @return 菜单允许时返回 true；领域可行性仍由原校验负责。
    """
    if tool_name != MENU_SCOPED_TOOL:
        return True
    return bool(canonical_actions) and all(action in menu.allowed_actions for action in canonical_actions)


def binding_action(way: str, canonical: str, **execution: Any) -> dict[str, Any]:
    """@brief 私有绑定的一项动作：动词、方式、内部标准动作与执行参数（出行方式、履约方式、use_mode），说明另填。

    :param way: 方式短语闭集 WAYS 中的一项；动词由方式唯一确定。
    :param canonical: 映射后的内部标准动作（须与方式的内部动作一致）。
    """
    verb, expected = vocabulary.WAYS[way]
    if expected != canonical:
        _integrity_error("Interaction way does not map to the bound internal action")
    return {"verb": verb, "label": way, "canonical_action": canonical,
            "travel_mode": execution.pop("travel_mode", None), "note": None, **execution}


def public_action(action: dict[str, Any]) -> dict[str, Any]:
    """@brief 交互对象公开行的一项动作：「动词：方式」的可读短语与未满足条件的一句说明（没有时为 null）。"""
    return {"action": f"{action['verb']}{vocabulary.VERB_WAY_SEPARATOR}{action['label']}", "note": action["note"]}


def named_supply_row(name: str, way: str) -> dict[str, Any]:
    """@brief 指名购买的占位绑定行：人物所写的名称与映射为购买的所选动作，供给由环境按名称解析后替换。

    :param way: 到店、远程下单或「购买」；「购买」的履约方式在解析时由系统决定，占位行上为 None。
    """
    fulfillment = next((mode for mode, label in vocabulary.PURCHASE_WAYS.items() if label == way), None)
    action = binding_action(way, "purchase", fulfillment=fulfillment)
    return {"object": name, "kind": NAMED_SUPPLY_KIND, "object_id": None, "actions": [action],
            "selected_action": action}


def activity_phrase(action: dict[str, Any]) -> str:
    """@brief 活动名称里的「做什么」：出行写「步行前往」「驾车前往」，其余取所选方式短语。"""
    return f"{action['label']}前往" if action["canonical_action"] == "travel_to" else action["label"]


def merged_selection(canonical: Sequence[str], ways: Sequence[str]) -> bool:
    """@brief 两项是否合成一次动作（沿用同短语合并，「购买」已在受理前定为到店或远程下单）：同为方式相同的取得（都到店或
    都远程下单）合成一单（再按收货地拆单），同为方式相同的食用或使用合成一餐（各项去向都是自己）；「制作」按一份库存
    转换配方执行，一次只做一件，「给予」逐项瞬时转移，都不合并；其余两项各自受理。"""
    if len(canonical) == 1:
        return True
    same = canonical[0] == canonical[1] and ways[0] == ways[1]
    return same and canonical[0] in {"purchase", "consume"} and ways[0] not in {"制作", vocabulary.GIVE_WAY}


def _validate_names(values: tuple[str, ...], allowed: frozenset[str], label: str) -> None:
    """@brief 校验一个声明数组的文本、唯一性与闭集归属。"""
    if any(not isinstance(value, str) or not value.strip() for value in values):
        _reject(f"interaction menu {label} names must be nonblank strings")
    if len(values) != len(set(values)):
        _reject(f"interaction menu {label} names must not contain duplicates")
    unknown = set(values) - allowed
    if unknown:
        _reject(f"interaction menu contains unknown {label} names: {sorted(unknown)}")


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
