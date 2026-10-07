"""@brief 从真实动作结果投影食品购买与尝试下单，重放订单不重复计数。

成功购买读取 ActionResult.data.orders 和逐项 activity_results 的订单；并行活动还在进行时已经成单的购买照样计入。
食品、店铺、app、金额和配送方式直接来自已提交订单，不从人物叙述推测。到店购买的 app_id 为 null。
尝试下单按当前方式同义表归一为到店、远程下单或购买，保留原动作首个结果，不产生活动浏览事实。
"""

from __future__ import annotations

from worldmock.life.vocabulary import way_options

#: 下单的取得方式短语（与 life.interaction_menu.NAMED_PURCHASE_WAYS 的取值相同：到店、远程下单与由系统定方式的「购买」）。
ORDER_WAYS = ("到店", "远程下单", "购买")


def project_purchase_facts(events: list[dict]) -> list[dict]:
    """@brief 按事件顺序投影 purchase 与 order_attempt 两类事实行（依次排列）。"""
    return _purchases(events) + _order_attempts(events)


def _purchases(events: list[dict]) -> list[dict]:
    """@brief 动作结果中的订单：顶层与逐项结果都取，按订单身份只记首次出现的那条结果。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for event in events:
        if event["event_type"] != "action_result":
            continue
        for order in _result_orders(event["data"].get("data", {})):
            if order["order_id"] in seen:
                continue
            seen.add(order["order_id"])
            rows.append({"fact_kind": "purchase", "agent_id": order["agent_id"], "order_id": order["order_id"],
                         "food_id": order["food_id"], "store_id": order["store_id"],
                         "app_id": order["app_id"], "unit_price_fen": order["unit_price_fen"],
                         "delivery_fee_fen": order["delivery_fee_fen"],
                         "quantity": order["quantity"], "total_price_fen": order["total_price_fen"],
                         "fulfillment": order["fulfillment"], "recipient_id": order["recipient_id"],
                         "request_message_id": order["request_message_id"],
                         "created_at": order["created_at"], "action_id": event["action_id"],
                         "source_event_id": event["event_id"]})
    return rows


def _result_orders(data: dict) -> list[dict]:
    """@brief 一次动作结果携带的全部订单：顶层 orders 在前，逐项结果的 orders 在后。"""
    nested = [order for item in data.get("activity_results", []) for order in item.get("orders", [])]
    return [*data.get("orders", []), *nested]


def _order_attempts(events: list[dict]) -> list[dict]:
    """@brief 方式为到店、远程下单或「购买」（含同义写法）的请求（重放请求不另计），way 为归一后的下单方式；状态与错误码
    取该动作首个结算结果，尚无结果时为空；明确非法的请求不解释为下单尝试。"""
    results: dict[str, dict] = {}
    for event in events:
        if event["event_type"] == "action_result":
            results.setdefault(event["action_id"], event["data"])
    rows = []
    for event in events:
        result = results.get(event["action_id"], {})
        if result.get("error_code") == "ACTION_REQUEST_INVALID":
            continue
        way = _order_way(event)
        if way is None:
            continue
        rows.append({"fact_kind": "order_attempt", "agent_id": event["agent_id"], "action_id": event["action_id"],
                     "request_event_id": event["event_id"], "way": way, "status": result.get("status"),
                     "error_code": result.get("error_code"), "occurred_at": event["occurred_at"]})
    return rows


def _order_way(event: dict) -> str | None:
    """@brief 非重放的开始活动请求中第一个下单方式（归一后的，_ordered）；不是下单请求时为 None。

    请求事件只存所写原文；受理结果的 details.resolved 只在受理时才有，且对下单方式与同一张同义表的归一相同，所以这里按
    同一函数归一，被拒与尚无结果的请求也照样分类。"""
    data = event["data"]
    if (event["event_type"] != "action_requested" or data.get("tool_name") != "start_activity"
            or data.get("reused")):
        return None
    ways = (item["way"] for item in data["request"]["items"])
    return next((way for way in map(_ordered, ways) if way is not None), None)


def _ordered(written: str) -> str | None:
    """@brief 所写方式对应的下单方式：原词或方式同义表（去掉空白、统一全半角后）恰好指向到店、远程下单与「购买」中的一个时
    为它（如「外卖」为远程下单、「自取」为到店、「买」为「购买」），否则为 None。"""
    hits = way_options(written) & frozenset(ORDER_WAYS)
    return next(iter(hits)) if len(hits) == 1 else None
