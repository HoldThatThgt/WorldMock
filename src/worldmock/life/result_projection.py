"""@brief 把实际动作结果投影成人物可见事实，不公开内部状态或自动渠道。"""

from __future__ import annotations

from typing import Any

from worldmock.environment import EnvironmentInputs
from worldmock.life.action_evidence import placed_orders
from worldmock.life.readable import duration_phrase, iso_time, money_phrase, portion_phrase

#: 以下载荷已经由各所属行为构造为人物可见名称与文字。
_PASSTHROUGH_DETAILS = ("recall", "reason", "next_step", "fix", "items", "outcome", "resolved", "notes",
                        "errands", "goods")


def public_result(result: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 只给本次结果、实际公开明细和运行时区的时刻。"""
    return {"occurred_at": iso_time(result["occurred_at"], inputs.config.timezone), "status": result["status"],
            "error_code": result.get("error_code"), "message": result["message"],
            "details": _details(result, inputs)}


def _details(result: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 这条动作下的订单、计划时长与到达由实际快照派生，系统足迹和在驿站领取的包裹不进入购买明细。"""
    payload = result.get("data") or {}
    details = {key: payload[key] for key in _PASSTHROUGH_DETAILS if key in payload}
    orders = placed_orders(payload)
    if orders:
        details["purchases"] = [_purchase(order, inputs) for order in orders]
    if result["status"] == "in_progress":
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in _activity_rows(payload)]
        details.update(_interact_details(_activity_rows(payload), inputs))
    if payload.get("resumed"):
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in payload["resumed"]]
    arrivals = [item["data"]["arrival"] for item in _activity_rows(payload) if item["data"].get("arrival")]
    if arrivals:
        row = arrivals[0]
        details["arrival"] = {"place": inputs.catalog.locations[row["location_id"]].display_name,
                              "access": "进入" if row["access_status"] == "allowed" else "没能进去"}
    return details


def _interact_details(activities: list[dict], inputs: EnvironmentInputs) -> dict:
    """@brief 开始结果展示实际内容，纯目录浏览展示真实应用供应商品。"""
    activity = next((row for row in activities if row.get("kind") == "interact"), None)
    if activity is None:
        return {}
    data, catalog = activity["data"], inputs.catalog
    app_id, content_id = data["app_id"], data["content_id"]
    app = None if app_id is None else catalog.apps[app_id].name
    if content_id is not None:
        content = catalog.contents[content_id]
        return {"content": {"app": app, "title": content.title, "summary": content.summary,
                            "duration": duration_phrase(content.duration_seconds),
                            "pushed": data["pinned_by_push_id"] is not None}}
    return {"app": app, "duration": duration_phrase(catalog.apps[app_id].browse_duration_seconds)}


def _activity_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 单项和整组结果统一读取实际活动快照。"""
    return [payload["activity"]] if "activity" in payload else payload.get("activities", [])


def _purchase(order: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 商品、报价或实际付款、冻结送达地点和时刻，不含应用、账户与身份。"""
    row = {"product": inputs.catalog.goods[order["goods_id"]].name,
           "quantity": portion_phrase(order["quantity"]),
           "destination": inputs.catalog.locations[order["destination_id"]].display_name,
           "expected_at": iso_time(order["expected_at"], inputs.config.timezone)}
    row["paid" if order["paid_at"] is not None else "price"] = money_phrase(order["total_price_fen"])
    return row
