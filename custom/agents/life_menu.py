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


def visible_products(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 商品身份只绑定使用合同，不再携带任一门店的报价库存。"""
    catalog = data.get("products")
    if not isinstance(catalog, list):
        _runtime_error("LifeAgent products catalog must be a list")
    result = {}
    for item in catalog:
        if not isinstance(item, dict):
            _runtime_error("LifeAgent product must be an object")
        product_id, modes = item.get("product_id"), item.get("use_modes")
        duration, required = item.get("use_duration_seconds"), item.get("required_location_id")
        if not isinstance(product_id, str) or not product_id.strip():
            _runtime_error("LifeAgent product identity is invalid")
        if product_id in result:
            _runtime_error("LifeAgent product identities are duplicated")
        if not isinstance(modes, list) or not modes or not all(isinstance(mode, str) for mode in modes):
            _runtime_error("LifeAgent product use modes are invalid")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            _runtime_error("LifeAgent product use contract is invalid")
        if required is not None and not isinstance(required, str):
            _runtime_error("LifeAgent product required location is invalid")
        result[product_id] = {"use_modes": tuple(sorted(set(modes))), "duration_seconds": duration,
                              "required_location_id": required}
    return result


def visible_supply_contracts(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 只从实际呈现供给生成可购买合同，资格与履约条件分别保留。"""
    catalog = data.get("supplies")
    if not isinstance(catalog, list):
        _runtime_error("LifeAgent supplies catalog must be a list")
    locations = _location_ids(data)
    result, seen = {}, set()
    for item in catalog:
        _validate_supply_row(item)
        supply_id = item["supply_id"]
        if item["location_id"] not in locations or not set(item["delivery_destination_ids"]) <= locations:
            _runtime_error("LifeAgent supply references an unknown location")
        if supply_id in seen:
            _runtime_error("LifeAgent supply identities are duplicated")
        seen.add(supply_id)
        if not item["purchase_allowed"]:
            continue
        modes = [mode for mode in item["fulfillment_modes"]
                 if mode == "delivery" or item["pickup_allowed"]]
        if modes:
            result[supply_id] = {**item, "fulfillment_modes": tuple(modes)}
    return result


def _location_ids(data: dict) -> set[str]:
    """@brief 已呈现供给的门店与履约范围必须引用公开地点身份。"""
    rows = data.get("locations")
    if not isinstance(rows, list):
        _runtime_error("LifeAgent locations catalog must be a list")
    result = set()
    for row in rows:
        value = row.get("location_id") if isinstance(row, dict) else None
        if not isinstance(value, str) or not value.strip() or value in result:
            _runtime_error("LifeAgent location identities are invalid")
        result.add(value)
    return result


def _validate_supply_row(item: Any) -> None:
    """@brief 验证供给身份、报价三态和世界资格，拒绝不完整的工具合同。"""
    if not isinstance(item, dict):
        _runtime_error("LifeAgent supply must be an object")
    for field in ("supply_id", "product_id", "location_id", "seller_account_id"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            _runtime_error("LifeAgent supply identity is invalid")
    stock, version, price = item.get("current_stock"), item.get("price_version"), item.get("price_fen")
    if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
        _runtime_error("LifeAgent supply stock is invalid")
    if item.get("quote_status") not in {"known", "unknown"}:
        _runtime_error("LifeAgent supply quote status is invalid")
    if item["quote_status"] == "known":
        if (not isinstance(version, str) or not version.strip() or isinstance(price, bool)
                or not isinstance(price, int) or price <= 0):
            _runtime_error("LifeAgent known supply quote is invalid")
    elif version is not None or price is not None:
        _runtime_error("LifeAgent unknown supply quote contains a price")
    for field in ("purchase_allowed", "pickup_allowed", "access_allowed", "service_allowed"):
        if not isinstance(item.get(field), bool):
            _runtime_error("LifeAgent supply qualification is invalid")
    modes, destinations = item.get("fulfillment_modes"), item.get("delivery_destination_ids")
    if (not isinstance(modes, list) or not modes or not set(modes) <= {"pickup", "delivery"}
            or not isinstance(destinations, list) or not all(isinstance(value, str) for value in destinations)):
        _runtime_error("LifeAgent supply fulfillment is invalid")
    if item.get("opening_status") not in {"open", "closed", "unknown"}:
        _runtime_error("LifeAgent supply opening status is invalid")
    if item["purchase_allowed"] and not (stock > 0 and version is not None and item["access_allowed"]
                                         and item["service_allowed"] and item["opening_status"] == "open"):
        _runtime_error("LifeAgent supply purchase qualification is inconsistent")


def discovery_contract(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """@brief 查询筛选条件来自公开地图词表，未知业务身份不能由模型编造。"""
    catalog = data.get("discovery_catalog")
    if not isinstance(catalog, dict):
        _runtime_error("LifeAgent discovery catalog must be an object")
    result = {}
    for field in ("product_ids", "business_kinds"):
        values = catalog.get(field)
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            _runtime_error("LifeAgent discovery catalog values are invalid")
        result[field] = tuple(sorted(set(values)))
    return result


def bind_storefront_schemas(functions: dict, contracts: dict) -> None:
    """@brief 实际模型 Schema 使用供给身份和可选发现条件，消费仍按商品身份。"""
    if "discover_places" in functions:
        properties = functions["discover_places"]["parameters"]["properties"]
        for field, key in (("product_id", "product_ids"), ("business_kind", "business_kinds")):
            string_schema = next(item for item in properties[field]["anyOf"] if item["type"] == "string")
            values = list(contracts["discover_places"][key])
            if values:
                string_schema["enum"] = values
            else:
                properties[field]["anyOf"] = [{"type": "null"}]
    if "purchase" not in functions:
        return
    supplies = contracts["purchase"]
    properties = functions["purchase"]["parameters"]["properties"]
    item_properties = properties["items"]["items"]["properties"]
    item_properties["supply_id"]["enum"] = sorted(supplies)
    item_properties["price_version"]["enum"] = sorted({item["price_version"] for item in supplies.values()})
    destinations = {value for item in supplies.values() for value in item["delivery_destination_ids"]}
    destinations.update(item["location_id"] for item in supplies.values() if item["pickup_allowed"])
    properties["destination_id"]["enum"] = sorted(destinations)
    properties["fulfillment"]["enum"] = sorted({mode for item in supplies.values()
                                                for mode in item["fulfillment_modes"]})


def validate_discovery(request: BaseModel, contract: dict) -> str:
    """@brief 本地查询校验与实际 Schema 共用同一可见业务词表。"""
    for field, key in (("product_id", "product_ids"), ("business_kind", "business_kinds")):
        value = getattr(request, field)
        if value is not None and value not in contract[key]:
            return _denied(f"discover_places {field} is not visible")
    return ""


def validate_purchase(request: BaseModel, catalog: dict[str, dict[str, Any]]) -> str:
    """@brief 校验供给报价配对、重复项、同地点同卖方及每条供给的履约条件。"""
    if not 1 <= len(request.items) <= 5:
        return _denied("purchase requires one to five items")
    selected, seen = [], set()
    for item in request.items:
        supply = catalog.get(item.supply_id)
        if supply is None or item.price_version != supply["price_version"]:
            return _denied("purchase supply_id and price_version are not a visible pair")
        if item.supply_id in seen:
            return _denied("purchase supply items must be unique")
        seen.add(item.supply_id)
        if item.quantity > supply["current_stock"]:
            return _denied("purchase quantity exceeds the visible current stock")
        selected.append(supply)
    if len({(item["location_id"], item["seller_account_id"]) for item in selected}) != 1:
        return _denied("purchase items must share one location and seller account")
    for supply in selected:
        if request.fulfillment not in supply["fulfillment_modes"]:
            return _denied("purchase fulfillment is not supported by every selected supply")
        if request.fulfillment == "pickup":
            if not supply["pickup_allowed"] or request.destination_id != supply["location_id"]:
                return _denied("purchase pickup requires the selected store location")
        elif request.destination_id not in supply["delivery_destination_ids"]:
            return _denied("purchase destination is outside the selected supply delivery scope")
    return ""
