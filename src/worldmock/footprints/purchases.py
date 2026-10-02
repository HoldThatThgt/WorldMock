"""@brief 从已提交事件投影购买事实：成功购买、尝试下单与应用浏览（docs/dev/SPEC-persona-prompt.md 第七章「导出」）。

成功购买取动作结果顶层 data.orders 与逐项结果 activity_results[*].orders 中的订单，按订单去重，与动作的整体状态
无关（并行的「吃＋买」整体为 in_progress 时订单同样计入），重放回执带着同一订单，不重复计；每行另记订单载荷的
channel_id（from 解析为渠道时的 App 标识，否则为 null，docs/dev/SPEC-dayrun-fidelity.md 3.3）。
尝试下单是方式为到店、远程下单或「购买」的请求：所写方式按宿主与环境同一张
方式同义表归一（name_resolution.way_options），「外卖」「自取」「买」这类同义写法与原词一样计入，way 记归一后的下单方式，
不指向下单方式的写法不计；带首个结算结果的状态与错误码。应用浏览是方式为浏览的活动，带最后一次快照的状态。
"""

from __future__ import annotations

from worldmock.life.name_resolution import way_options

#: 下单的取得方式短语（与 life.interaction_menu.NAMED_PURCHASE_WAYS 的取值相同：到店、远程下单与由系统定方式的「购买」）。
ORDER_WAYS = ("到店", "远程下单", "购买")
#: 方式「浏览」开始的活动类型。
BROWSE_ACTIVITY_KIND = "browse"


def project_purchase_facts(events: list[dict]) -> list[dict]:
    """@brief 按事件顺序投影 purchase、order_attempt、app_browse 三类事实行（依次排列）。"""
    return _purchases(events) + _order_attempts(events) + _app_browses(events)


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
                         "product_id": order["product_id"], "supply_id": order["supply_id"],
                         "quantity": order["quantity"], "total_price_fen": order["total_price_fen"],
                         "fulfillment": order["fulfillment"], "recipient_id": order["recipient_id"],
                         "request_message_id": order["request_message_id"], "channel_id": order.get("channel_id"),
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


def _app_browses(events: list[dict]) -> list[dict]:
    """@brief 浏览活动：按首次出现排序，状态与来源取截止前最后一次权威快照。"""
    latest: dict[str, tuple[dict, str]] = {}
    for event in events:
        snapshot = event["data"].get("activity")
        if snapshot is not None and snapshot["kind"] == BROWSE_ACTIVITY_KIND:
            latest[snapshot["activity_id"]] = (snapshot, event["event_id"])
    return [{"fact_kind": "app_browse", "agent_id": snapshot["agent_id"], "activity_id": activity_id,
             "started_at": snapshot["started_at"], "status": snapshot["status"], "source_event_id": source}
            for activity_id, (snapshot, source) in latest.items()]
