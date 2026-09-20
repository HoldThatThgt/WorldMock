"""@brief LifeAgent 对冻结交互菜单的解析、投影和宿主边界校验。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from worldmock.life.interaction_menu import (
    InteractionMenu,
    menu_allows_action,
    menu_allows_activity,
    menu_allows_category,
)
from worldmock.life.models import StartBasicActivityRequest


def parse_interaction_menu(data: dict[str, Any]) -> InteractionMenu:
    """@brief 从非只读观察读取并严格核验已解析的有效菜单。

    @param data 当前冻结环境观察的数据段。
    @return 通过闭集、保护项和确定性顺序校验的菜单。
    """
    raw = data.get("interaction_menu")
    if not isinstance(raw, dict):
        return _invalid_menu("LifeAgent interaction menu must be an object")
    try:
        menu = InteractionMenu.model_validate(raw)
    except ValidationError as error:
        return _invalid_menu(f"LifeAgent interaction menu is invalid: {error}", error)
    if menu.allowed_tools != tuple(sorted(menu.allowed_tools)):
        return _invalid_menu("LifeAgent interaction menu tools must be sorted")
    if menu.allowed_activity_kinds != tuple(sorted(menu.allowed_activity_kinds)):
        return _invalid_menu("LifeAgent interaction menu activity kinds must be sorted")
    return menu


def filter_targeted_kinds(menu: InteractionMenu, kinds: tuple[str, ...]) -> tuple[str, ...]:
    """@brief 只保留同时获得启动工具和活动类型准入的定向类型。"""
    return tuple(kind for kind in kinds if menu_allows_activity(menu, kind))


def filter_targets(
    menu: InteractionMenu,
    targets: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """@brief 只过滤模型可操作的目标投影，不修改观察中的目录事实。"""
    return {
        kind: values if menu_allows_activity(menu, kind) else ()
        for kind, values in targets.items()
    }


def filter_browse_categories(
    menu: InteractionMenu,
    categories: frozenset[str],
) -> frozenset[str]:
    """@brief 用共享品类映射收窄当前真实存在的下钻键。"""
    return frozenset(category for category in categories
                     if menu_allows_category(menu, category))


def filter_action_contracts(
    menu: InteractionMenu,
    contracts: dict[str, Any],
) -> dict[str, Any]:
    """@brief 收窄瞬时动作的派生身份合同并保持既有键集合。"""
    result = {}
    for tool_name, contract in contracts.items():
        allowed = tool_name in menu.allowed_tools
        if tool_name == "start_travel":
            allowed = allowed and menu_allows_activity(menu, "travel")
        elif tool_name == "consume":
            allowed = allowed and menu_allows_activity(menu, "consume")
        result[tool_name] = contract if allowed else _empty_contract(contract)
    return result


def filter_action_models(
    menu: InteractionMenu,
    models: dict[str, type[BaseModel]],
) -> dict[str, type[BaseModel]]:
    """@brief 在既有状态、阶段和可见性门控之后与菜单工具轴取交集。"""
    return {name: model for name, model in models.items()
            if name in menu.allowed_tools}


def filter_activity_controls(menu: InteractionMenu, controls: dict[str, Any]) -> dict[str, Any]:
    """@brief 保留取消等既有控制，只从可恢复身份中移除禁止的新启动资格。"""
    kinds = controls["paused_activity_kinds"]
    resumable = tuple(
        activity_id for activity_id in controls["resumable_paused_ids"]
        if menu_allows_activity(menu, kinds[activity_id])
    )
    return {**controls, "menu_resumable_paused_ids": resumable}


def basic_activity_types(menu: InteractionMenu, data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 生成基础活动 Schema 的本轮闭集，包括已下钻的生活活动身份。"""
    direct = tuple(kind for kind in ("wait", "sleep")
                   if menu_allows_activity(menu, kind))
    if not menu_allows_activity(menu, "lifestyle"):
        return direct
    return (*direct, *_visible_lifestyle_types(data.get("recent_action_results")))


def bind_basic_activity_schema(
    schemas: list[dict[str, Any]],
    activity_types: tuple[str, ...],
) -> None:
    """@brief 把菜单允许且已发现的基础活动写入实际模型请求枚举。"""
    basic = next(
        (item for item in schemas if item["function"]["name"] == "start_basic_activity"),
        None,
    )
    if basic is None:
        return
    properties = basic["function"]["parameters"]["properties"]
    properties["activity_type"]["enum"] = list(activity_types)


def menu_action_error(
    menu: InteractionMenu,
    action: str,
    request: BaseModel,
    controls: dict[str, Any],
    basic_types: tuple[str, ...],
) -> str:
    """@brief 在本地校验与直接派发边界复用一次冻结菜单。

    @return 允许时为空字符串，禁止时为英文协议错误。
    """
    activity_kind = _resume_kind(action, request, controls)
    if action == "set_activity_state" and str(getattr(request, "operation", "")) == "resume":
        if activity_kind is None:
            return ""
    if not menu_allows_action(menu, action, request, activity_kind):
        return _denied(f"interaction menu denies {action}")
    if action == "start_basic_activity" and request.activity_type not in basic_types:
        return _denied(
            "start_basic_activity activity_type is not visible in the interaction menu")
    return ""


def _visible_lifestyle_types(results: Any) -> tuple[str, ...]:
    """@brief 从本轮可见下钻结果提取通过基础请求模型核验的生活活动身份。"""
    if not isinstance(results, list):
        _runtime_error("LifeAgent recent action results are invalid")
    values = set()
    for entry in results:
        payload = entry.get("result") if isinstance(entry, dict) else None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get("tool") != "browse_activities":
            continue
        items = data.get("items")
        if not isinstance(items, list):
            _runtime_error("LifeAgent browsed activity items must be a list")
        for item in items:
            value = item.get("activity_type") if isinstance(item, dict) else None
            if value is None:
                continue
            try:
                StartBasicActivityRequest(activity_type=value, planned_duration_seconds=60)
            except ValidationError as error:
                logging.error("LifeAgent browsed lifestyle activity type is invalid: %s", error)
                raise RuntimeError("LifeAgent browsed lifestyle activity type is invalid") from error
            values.add(value)
    return tuple(sorted(values))


def _resume_kind(action: str, request: BaseModel, controls: dict[str, Any]) -> str | None:
    """@brief 取得恢复请求对应的冻结暂停活动类型。"""
    if action != "set_activity_state" or str(getattr(request, "operation", "")) != "resume":
        return None
    return controls["paused_activity_kinds"].get(request.activity_id)


def _empty_contract(contract: Any) -> Any:
    """@brief 按既有容器类型形成不可用的派生合同。"""
    return {} if isinstance(contract, Mapping) else ()


def _invalid_menu(message: str, cause: Exception | None = None):
    """@brief 记录英文完整性错误并让非只读人物失败关闭。"""
    logging.error(message)
    if cause is None:
        raise RuntimeError(message)
    raise RuntimeError("LifeAgent interaction menu is invalid") from cause


def _denied(message: str) -> str:
    """@brief 记录英文宿主菜单拒绝并返回同一协议错误。"""
    logging.error(message)
    return message


def _runtime_error(message: str) -> None:
    """@brief 记录英文观察完整性错误并终止准备。"""
    logging.error(message)
    raise RuntimeError(message)
