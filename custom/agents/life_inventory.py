"""@brief 人物消费工具的持有物合同，不依赖商家报价可见期。"""

from __future__ import annotations

import math
from typing import Any

from worldmock.errors import ProtocolError


def visible_inventory_contract(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 仅开放当前地点正持有量且使用地点、时长可行的物品；实际用量由环境结算。"""
    items = _inventory_items(data)
    inventory = data.get("available_inventory")
    location_id = data.get("location_id")
    if not isinstance(inventory, dict) or not isinstance(location_id, str):
        raise ProtocolError("LifeAgent available inventory or location is invalid")
    result = {}
    for product_id, quantity in inventory.items():
        if (not isinstance(product_id, str) or isinstance(quantity, bool)
                or not isinstance(quantity, (int, float)) or not math.isfinite(quantity)):
            raise ProtocolError("LifeAgent available inventory entry is invalid")
        if quantity <= 0:
            continue
        item = items.get(product_id)
        if item is None:
            raise ProtocolError("LifeAgent held product contract is unavailable")
        if item["required_location_id"] not in {None, location_id} or item["duration_seconds"] < 60:
            continue
        result[product_id] = {key: item[key] for key in ("name", "use_modes", "duration_seconds")}
    return result


def _inventory_items(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 严格读取已持有物品的完整使用合同，不以商品报价替代缺失字段。"""
    catalog = data.get("inventory_items")
    if not isinstance(catalog, list):
        raise ProtocolError("LifeAgent inventory_items must be a list")
    result = {}
    fields = {"product_id", "name", "use_modes", "use_duration_seconds", "required_location_id"}
    for item in catalog:
        if not isinstance(item, dict) or set(item) != fields:
            raise ProtocolError("LifeAgent inventory item contract fields are invalid")
        product_id, name = item["product_id"], item["name"]
        modes, duration, required = item["use_modes"], item["use_duration_seconds"], item["required_location_id"]
        if not isinstance(product_id, str) or not product_id.strip() or not isinstance(name, str) or not name.strip():
            raise ProtocolError("LifeAgent inventory item identity or name is invalid")
        if (not isinstance(modes, list) or not modes
                or any(mode not in ("eat", "use") for mode in modes) or len(modes) != len(set(modes))):
            raise ProtocolError("LifeAgent inventory item use modes are invalid")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise ProtocolError("LifeAgent inventory item duration is invalid")
        if required is not None and (not isinstance(required, str) or not required.strip()):
            raise ProtocolError("LifeAgent inventory item required location is invalid")
        if product_id in result:
            raise ProtocolError("LifeAgent inventory item identities are duplicated")
        result[product_id] = {"name": name, "use_modes": tuple(sorted(modes)),
                              "duration_seconds": duration, "required_location_id": required}
    return result


def bind_consume_schema(function: dict[str, Any], items: dict[str, dict[str, Any]]) -> None:
    """@brief 把本轮消费物品身份、名称、方式与时长绑定到实际模型工具。"""
    properties = function["parameters"]["properties"]
    identity = properties["items"]["items"]["properties"]["product_id"]
    identity["enum"] = sorted(items)
    identity["description"] = "Currently held items: " + "; ".join(
        f"{product_id}: {items[product_id]['name']}" for product_id in sorted(items))
    properties["use_mode"]["enum"] = sorted({mode for item in items.values() for mode in item["use_modes"]})
    properties["duration_seconds"]["enum"] = sorted({item["duration_seconds"] for item in items.values()})
