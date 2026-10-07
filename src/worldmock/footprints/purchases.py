"""@brief 从真实订单、到站与收货事件投影购买、尝试、可取通知和收货事实。"""

from __future__ import annotations

from worldmock.life.vocabulary import way_options

#: 统一购买方式保留当前同义解析，不读取退役方式。
ORDER_WAYS = ("购买",)


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
    if event["event_type"] == "environment_event" and data.get("kind") == "delivery_arrival":
        return data["orders"]
    return []


def _purchases(events: list[dict]) -> list[dict]:
    """@brief 订单按身份首次结果只计一次，购买始终属于实际付款人。"""
    rows, seen = [], set()
    fields = ("goods_id", "category", "store_id", "app_id", "payment_app_id", "unit_price_fen",
              "delivery_fee_fen", "quantity", "total_price_fen", "fulfillment", "recipient_id",
              "request_message_id", "created_at")
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
                rows.append({"fact_kind": "pickup_ready", "agent_id": int(order["recipient_id"].split(":", 1)[1]),
                    "order_id": order["order_id"], "app_id": order["app_id"],
                    "pickup_location_id": order["destination_id"], "arrived_at": order["arrived_at"],
                    "source_event_id": event["event_id"]})
    return rows


def _receipts(events: list[dict], orders: dict[str, dict]) -> list[dict]:
    """@brief 模拟收货人行来源于本人收货，联系人行来源于付款人的真实交付。"""
    rows, contacts = [], set()
    for event in events:
        if event["event_type"] == "environment_event" and event["data"].get("kind") == "goods_received":
            data = event["data"]
            order = orders[data["order_id"]]
            rows.append(_receipt(order, event, data["provided"], data["applied"]))
        for order in event_orders(event):
            if (not order["recipient_id"].startswith("agent:") and order["status"] == "delivered"
                    and order["order_id"] not in contacts):
                contacts.add(order["order_id"])
                rows.append(_receipt(order, event, None, None))
    return rows


def _receipt(order: dict, event: dict, provided, applied) -> dict:
    """@brief 收货行保留真实收货时间和效果，不制造 app 触点或二次付款。"""
    return {"fact_kind": "receipt", "agent_id": event["agent_id"], "order_id": order["order_id"],
            "recipient_id": order["recipient_id"], "received_at": order["received_at"],
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
        way = _order_way(event)
        if way is not None:
            rows.append({"fact_kind": "order_attempt", "agent_id": event["agent_id"], "action_id": event["action_id"],
                "request_event_id": event["event_id"], "way": way, "status": result.get("status"),
                "error_code": result.get("error_code"), "occurred_at": event["occurred_at"]})
    return rows


def _order_way(event: dict) -> str | None:
    """@brief 只把当前 start_activity 购买方式及其现行同义词归为尝试。"""
    data = event["data"]
    if (event["event_type"] != "action_requested" or data.get("tool_name") != "start_activity"
            or data.get("reused")):
        return None
    return "购买" if any("购买" in way_options(item["way"]) for item in data["request"]["items"]) else None
