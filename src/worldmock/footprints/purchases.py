"""@brief 从真实订单、到站与收货事件投影购买、尝试、可取通知和收货事实。"""

from __future__ import annotations

def project_purchase_facts(events: list[dict]) -> list[dict]:
    """@brief 订单首次出现是购买，到站是通知，真正收到才有收货行。"""
    orders = orders_by_id(events)
    return _purchases(events) + _order_attempts(events) + _pickup_ready(events) + _receipts(events, orders)


def orders_by_id(events: list[dict]) -> dict[str, dict]:
    """@brief 订单最后状态只取真实动作结果和配送到期订单快照。"""
    orders = {}
    for event in events:
        for order in event_orders(event):
            orders[order["order_id"]] = order
    return orders


def event_orders(event: dict) -> list[dict]:
    """@brief 当前订单的两个生产证据入口，不读取持有与库存载荷。"""
    data = event["data"]
    if event["event_type"] == "action_result":
        return _result_orders(data.get("data", {}))
    if event["event_type"] == "activity_progress":
        return data.get("orders", [])
    if event["event_type"] == "environment_event" and data.get("kind") == "delivery_arrival":
        return data["orders"]
    return []


def _purchases(events: list[dict]) -> list[dict]:
    """@brief 订单按身份首次结果只计一次，购买始终属于实际付款人。"""
    rows, seen = [], set()
    fields = ("goods_id", "category", "store_id", "app_id", "payment_app_id", "unit_price_fen",
              "delivery_fee_fen", "quantity", "total_price_fen", "fulfillment", "created_at")
    for event in events:
        if event["event_type"] != "action_result":
            continue
        for order in event_orders(event):
            if order["order_id"] in seen:
                continue
            seen.add(order["order_id"])
            rows.append({"fact_kind": "purchase", "agent_id": order["agent_id"], "order_id": order["order_id"],
                         **{field: order[field] for field in fields}, "action_id": event["action_id"],
                         "source_event_id": event["event_id"]})
    return rows


def _result_orders(data: dict) -> list[dict]:
    """@brief 读取整组顶层订单及仍在运行的逐项实际结果订单。"""
    nested = [order for item in data.get("activity_results", []) for order in item.get("orders", [])]
    return [*data.get("orders", []), *nested]


def _pickup_ready(events: list[dict]) -> list[dict]:
    """@brief 每张真实到站订单生成收货人通知；领取不会再次生成通知。"""
    rows = []
    for event in events:
        if event["event_type"] != "environment_event" or event["data"].get("kind") != "delivery_arrival":
            continue
        for order in event_orders(event):
            if order["status"] == "ready_for_pickup":
                rows.append({"fact_kind": "pickup_ready", "agent_id": order["agent_id"],
                    "order_id": order["order_id"], "app_id": order["app_id"],
                    "pickup_location_id": order["destination_id"], "arrived_at": order["arrived_at"],
                    "source_event_id": event["event_id"]})
    return rows


def _receipts(events: list[dict], orders: dict[str, dict]) -> list[dict]:
    """@brief 每条本人收货事实保留真实身体效果，付款和到站不能代替收货。"""
    return [_receipt(orders[event["data"]["order_id"]], event, event["data"]["provided"], event["data"]["applied"])
            for event in events if event["event_type"] == "environment_event"
            and event["data"].get("kind") == "goods_received"]


def _receipt(order: dict, event: dict, provided, applied) -> dict:
    """@brief 收货行保留真实收货时间和效果，不制造 app 触点或二次付款。"""
    return {"fact_kind": "receipt", "agent_id": event["agent_id"], "order_id": order["order_id"],
            "received_at": order["received_at"],
            "goods_id": order["goods_id"], "category": order["category"], "quantity": order["quantity"],
            "provided": provided, "applied": applied, "source_event_id": event["event_id"]}


def _order_attempts(events: list[dict]) -> list[dict]:
    """@brief 统一购买请求保留首个结果状态，重放和非法结构不重复计尝试。"""
    results = {}
    for event in events:
        if event["event_type"] == "action_result":
            results.setdefault(event["action_id"], event["data"])
    rows = []
    for event in events:
        result = results.get(event["action_id"], {})
        if result.get("error_code") == "ACTION_REQUEST_INVALID":
            continue
        action = _order_action(event)
        if action is not None:
            rows.append({"fact_kind": "order_attempt", "agent_id": event["agent_id"], "action_id": event["action_id"],
                "request_event_id": event["event_id"], "action": action, "status": result.get("status"),
                "error_code": result.get("error_code"), "occurred_at": event["occurred_at"]})
    return rows


def _order_action(event: dict) -> str | None:
    """@brief 只把未重放的当前购买请求归为尝试，不解析已退役方式。"""
    data = event["data"]
    if (event["event_type"] != "action_requested" or data.get("tool_name") != "start_side_activity"
            or data.get("reused")):
        return None
    return "purchase" if data["request"]["action"] == "purchase" else None
