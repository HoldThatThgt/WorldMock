"""@brief 认证足迹的应用、收货、时间、推送及真实事件所有权。"""

from __future__ import annotations

from collections import defaultdict

from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.footprints.app_usage import _app_binding
from worldmock.footprints.purchases import event_orders, orders_by_id
from worldmock.life.models import DeliveryState
from worldmock.life.order_notices import ORDER_DELIVERED, ORDER_PLACED, ORDER_SHIPPED, shipping_delay_seconds
from worldmock.life.order_routing import payment_order_key


_COMMON_APP = {"fact_kind", "agent_id", "activity_id", "app_id", "source_event_id"}
_APP_FIELDS = {
    "app_use": {"segments", "status"},
    "app_touch": {"occurred_at"},
    "payment_notification": {"occurred_at", "amount_fen", "order_ids", "title", "summary"},
    "order_notification": {"occurred_at", "order_ids", "title", "summary"},
    "content_consumed": {"content_id", "interval_start", "interval_end", "position_before", "position_after",
                         "completed", "push_id"},
    "push_delivered": {"push_id", "content_id", "session_id", "occurred_at"},
    "push_opened": {"push_id", "content_id", "session_id", "occurred_at"},
    "push_released": {"push_id", "content_id", "session_id", "occurred_at"},
}
#: 引擎代为使用 app 的活动种类：导航属于出行，下单和支付属于购买。
_AUTOMATIC_ACTIVITY_TYPES = frozenset({"travel", "purchase"})


def validate_footprint_facts(events: list[dict], activities: list[dict], purchases: list[dict],
                              app_usage: list[dict], state: dict) -> None:
    """@brief 重算之外再检查足迹不变量：app 使用与前台区间、内容、推送、订单、付款和订单通知。"""
    sources = {event["event_id"]: event for event in events}
    for row in [*purchases, *app_usage]:
        key = "request_event_id" if row["fact_kind"] == "order_attempt" else "source_event_id"
        source = sources.get(row[key])
        if source is None or source["agent_id"] != row["agent_id"]:
            raise IntegrityError("Footprint source event is missing or owned by another persona")
    by_activity = {row["activity_id"]: row for row in activities}
    foreground = _foreground_events(events)
    _app_invariants(app_usage, by_activity)
    _automatic_app_use(app_usage, by_activity, foreground)
    _navigation_intervals(by_activity, foreground)
    _app_intervals(app_usage)
    _content_positions(app_usage, by_activity)
    _push_references(app_usage, sources)
    _order_invariants(events, purchases, state, sources)
    _purchase_intervals(by_activity, foreground, state.get("deliveries", {}))
    _payment_invariants(events, state, sources)
    _order_notifications(events, state)


def _app_invariants(rows: list[dict], activities: dict[str, dict]) -> None:
    """@brief 所有应用行都来自带真实应用身份的活动、引擎代为使用 app 的出行或购买，或真实推送。"""
    for row in rows:
        kind = row["fact_kind"]
        if kind not in _APP_FIELDS or set(row) != _COMMON_APP | _APP_FIELDS[kind]:
            raise IntegrityError("App usage fact fields are outside the current contract")
        if not isinstance(row["app_id"], str) or not row["app_id"]:
            raise IntegrityError("App usage fact requires an actual app identity")
        if kind in {"push_delivered", "app_touch", "payment_notification", "order_notification"}:
            if row["activity_id"] is not None:
                raise IntegrityError("System app fact or push delivery cannot invent an activity")
            continue
        activity = activities.get(row["activity_id"])
        if kind == "app_use" and activity is not None and activity["activity_type"] in _AUTOMATIC_ACTIVITY_TYPES:
            continue
        binding = None if activity is None else _app_binding(activity)
        if binding is None or activity["activity_type"] != "interact":
            raise IntegrityError("App usage activity has no actual app identity")
        if kind.startswith("push_"):
            continue
        if row["app_id"] != binding:
            raise IntegrityError("App usage identity disagrees with its activity")
        if kind == "app_use" and row["segments"] != activity["segments"]:
            raise IntegrityError("App use intervals disagree with actual execution")


def _app_intervals(rows: list[dict]) -> None:
    """@brief 同一时刻只有一个 app 在前台：同一人物全部 app 使用的半开区间不允许重叠，导航、下单、支付一起查。"""
    grouped = defaultdict(list)
    for row in rows:
        if row["fact_kind"] == "app_use":
            grouped[row["agent_id"]].extend(row["segments"])
    for segments in grouped.values():
        end = None
        for segment in sorted(segments, key=lambda value: (value["started_at"], value["ended_at"])):
            if segment["started_at"] >= segment["ended_at"] or end is not None and segment["started_at"] < end:
                raise IntegrityError("App use intervals overlap or have no positive duration")
            end = segment["ended_at"]


def _content_positions(rows: list[dict], activities: dict[str, dict]) -> None:
    """@brief 一轮内容的全局位置单调，真实消费区间须落在实际活动区间内。"""
    previous = {}
    for row in rows:
        if row["fact_kind"] != "content_consumed":
            continue
        before, after = row["position_before"], row["position_after"]
        key = row["agent_id"], row["content_id"]
        if not 0 <= before <= after or before < previous.get(key, 0):
            raise IntegrityError("Content positions move backwards within a consumption round")
        start, end = row["interval_start"], row["interval_end"]
        if not start < end or not any(segment["started_at"] <= start < end <= segment["ended_at"]
                                     for segment in activities[row["activity_id"]]["segments"]):
            raise IntegrityError("Content consumption interval is outside actual activity segments")
        previous[key] = 0 if row["completed"] else after


def _push_references(rows: list[dict], sources: dict[str, dict]) -> None:
    """@brief 打开、释放和钉住消费只引用同人物此前真实投递的推送。"""
    pushes = {row["push_id"]: row for row in rows if row["fact_kind"] == "push_delivered"}
    for row in rows:
        kind = row["fact_kind"]
        if kind not in {"push_opened", "push_released", "content_consumed"} or row["push_id"] is None:
            continue
        push = pushes.get(row["push_id"])
        if push is None or push["agent_id"] != row["agent_id"]:
            raise IntegrityError("Push reference has no delivery owned by this persona")
        if sources[push["source_event_id"]]["event_seq"] >= sources[row["source_event_id"]]["event_seq"]:
            raise IntegrityError("Push reference precedes its actual delivery")
        if (push["app_id"], push["content_id"]) != (row["app_id"], row["content_id"]):
            raise IntegrityError("Push reference disagrees with its app or content")
        if kind.startswith("push_") and row["session_id"] != push["session_id"]:
            raise IntegrityError("Push transition disagrees with its actual session")


def _order_invariants(events: list[dict], rows: list[dict], state: dict, sources: dict[str, dict]) -> None:
    """@brief 每张真实订单恰一购买，到站不收货，真正收到才有唯一收货效果。"""
    orders = orders_by_id(events)
    if orders != state.get("deliveries", {}):
        raise IntegrityError("Export order events disagree with the final checkpoint")
    by_kind = defaultdict(list)
    received = defaultdict(list)
    for row in rows:
        if row["fact_kind"] != "order_attempt":
            by_kind[(row["fact_kind"], row["order_id"])].append(row)
    for event in events:
        if event["event_type"] != "environment_event":
            continue
        kind = event["data"].get("kind")
        if kind in {"delivery_arrival", "goods_received"} and _account_changes(event):
            raise IntegrityError("Order arrival and receipt cannot change account balances")
        if kind == "goods_received":
            received[event["data"]["order_id"]].append(event)
    if set(received) - set(orders):
        raise IntegrityError("Goods receipt references an unknown order")
    for identity, order in orders.items():
        _validate_order_shape(order)
        purchase = by_kind[("purchase", identity)]
        if len(purchase) != 1 or purchase[0]["agent_id"] != order["agent_id"]:
            raise IntegrityError("Every order requires exactly one purchase owned by its payer")
        _receipt_invariants(order, by_kind[("receipt", identity)], received[identity], sources)
        _pickup_invariants(order, by_kind[("pickup_ready", identity)], sources)


def _validate_order_shape(order: dict) -> None:
    """@brief 当前严格订单模型认证份数、三种状态及全部运输时刻。"""
    try:
        DeliveryState.model_validate(order)
    except ValidationError as error:
        raise IntegrityError(f"Export order violates the current receipt contract: {error}") from error


def _account_changes(event: dict) -> bool:
    """@brief 收货不能夹带顶层或结果内的账户变化。"""
    return any(key.startswith("account:") and value != 0
               for key, value in event.get("resource_changes", {}).items())


def _receipt_invariants(order: dict, receipts: list[dict], received: list[dict], sources: dict[str, dict]) -> None:
    """@brief 每个已履约订单必须具有恰一条本人的收货事实。"""
    delivered = order["status"] == "delivered"
    if len(receipts) != int(delivered) or len(received) != int(delivered):
        raise IntegrityError("Receipt and goods received counts disagree with the actual order status")
    if not delivered:
        return
    receipt, event = receipts[0], received[0]
    fields = ("received_at", "goods_id", "category", "quantity")
    if receipt["agent_id"] != order["agent_id"] or any(receipt[field] != order[field] for field in fields):
        raise IntegrityError("Receipt identity, quantity, owner, or time disagrees with its order")
    data = event["data"]
    if (event["agent_id"] != order["agent_id"] or event["occurred_at"] != order["received_at"]
            or event["source_kind"] != "environment" or event["visibility"] != "agent"
            or any(data[field] != order[field] for field in ("goods_id", "category", "quantity", "destination_id"))
            or receipt["source_event_id"] != event["event_id"]
            or any(receipt[field] != data[field] for field in ("provided", "applied"))):
        raise IntegrityError("Goods received evidence disagrees with the actual recipient or receipt")
    _receipt_cause(order, event, sources)


def _receipt_cause(order: dict, event: dict, sources: dict[str, dict]) -> None:
    """@brief 收货只由真实下单、送达或本人领取结果造成，排期身份不是事件身份。"""
    causes = [sources.get(identity) for identity in event["caused_by"]]
    if len(causes) != 1 or causes[0] is None:
        raise IntegrityError("Goods receipt requires one true fulfillment cause event")
    cause = causes[0]
    if order["fulfillment"] == "pickup":
        if (cause["event_type"] != "environment_event" or cause["data"].get("kind") != "purchase_payment"
                or order["order_id"] not in cause["data"]["order_ids"]
                or cause["occurred_at"] != order["paid_at"]):
            raise IntegrityError("Goods receipt cause is not the actual fulfilled order event")
    else:
        expected_type = "environment_event" if order["fulfillment"] == "delivery" else "action_result"
        if (cause["event_type"] != expected_type
                or not any(value["order_id"] == order["order_id"] and value["status"] == "delivered"
                           for value in event_orders(cause))):
            raise IntegrityError("Goods receipt cause is not the actual fulfilled order event")
    if order["fulfillment"] == "station_pickup" and cause["agent_id"] != event["agent_id"]:
        raise IntegrityError("Parcel receipt must be caused by its recipient collection result")


def _pickup_invariants(order: dict, rows: list[dict], sources: dict[str, dict]) -> None:
    """@brief 长途已到站订单恰一本人可取通知，来源为真实到站并指向真实购买。"""
    expected = int(order["fulfillment"] == "station_pickup" and order["arrived_at"] is not None)
    if len(rows) != expected:
        raise IntegrityError("Parcel pickup notification count disagrees with actual arrival")
    if not rows:
        return
    row = rows[0]
    owner = order["agent_id"]
    source = sources[row["source_event_id"]]
    if (row["agent_id"] != owner or row["arrived_at"] != order["arrived_at"]
            or row["app_id"] != order["app_id"] or row["pickup_location_id"] != order["destination_id"]
            or source["event_type"] != "environment_event" or source["data"].get("kind") != "delivery_arrival"
            or source["agent_id"] != owner or source["visibility"] != "agent"
            or source["occurred_at"] != order["arrived_at"]
            or not any(value["order_id"] == order["order_id"] and value["status"] == "ready_for_pickup"
                       for value in event_orders(source))):
        raise IntegrityError("Parcel pickup notification disagrees with its true recipient arrival")
    causes = [sources.get(identity) for identity in source["caused_by"]]
    if (len(causes) != 1 or causes[0] is None or causes[0]["event_type"] != "environment_event"
            or causes[0]["agent_id"] != order["agent_id"]
            or causes[0]["data"].get("kind") != "purchase_payment"
            or order["order_id"] not in causes[0]["data"]["order_ids"]):
        raise IntegrityError("Parcel arrival must reference its true payer purchase result")


def _payment_invariants(events: list[dict], state: dict, sources: dict[str, dict]) -> None:
    """@brief 每张已付款订单恰一付款，支付 app 与下单 app 不全为空时恰一条「支付成功」；订单是一次购买里下单 app 相同的全部记录。

    记录按（购买活动，app_id）归组，到店记录的 app_id 为空，合成一张到店订单；不同购买的订单互不合并。
    """
    orders = state.get("deliveries", {})
    purchases = _purchase_activities(state)
    groups = defaultdict(list)
    payments = defaultdict(list)
    notifications = defaultdict(list)
    for identity, order in orders.items():
        groups[(purchases.get(identity), order["app_id"])].append(order)
    for event in events:
        kind = event["data"].get("kind")
        if kind not in {"purchase_payment", "payment_notification"}:
            continue
        identities = event["data"]["order_ids"]
        if any(identity not in orders for identity in identities):
            raise IntegrityError("Purchase payment references an unknown order")
        group = (purchases.get(identities[0]), orders[identities[0]]["app_id"])
        (payments if kind == "purchase_payment" else notifications)[group].append(event)
    for group, values in groups.items():
        paid = all(order["paid_at"] is not None for order in values)
        if any((order["paid_at"] is not None) != paid for order in values):
            raise IntegrityError("Purchase order has partial payment")
        if len(payments[group]) != int(paid):
            raise IntegrityError("Purchase order payment count disagrees with actual payment")
        notified = paid and (values[0]["payment_app_id"] is not None or values[0]["app_id"] is not None)
        if len(notifications[group]) != int(notified):
            raise IntegrityError("Payment notification count disagrees with actual payment")
        if paid:
            _validate_payment_order(group[0], values, payments[group][0], sources)
            if notified:
                _validate_payment_notification(values, payments[group][0], notifications[group][0])


def _purchase_activities(state: dict) -> dict[str, str]:
    """@brief 商品履约记录所属的购买活动，只取最终检查点里购买活动列出的记录身份。"""
    return {identity: activity["activity_id"] for activity in state.get("activities", {}).values()
            if activity["kind"] == "purchase" for identity in activity["data"]["order_ids"]}


def _validate_payment_order(purchase: str | None, orders: list[dict], event: dict,
                            sources: dict[str, dict]) -> None:
    """@brief 付款的购买活动、金额及这张订单的完整记录集合与同刻购买终态、实际受理来源相符。"""
    data = event["data"]
    identity = data["activity_id"]
    amount = sum(order["total_price_fen"] for order in orders)
    owner = orders[0]["agent_id"]
    if (identity != purchase or data["order_ids"] != sorted(order["order_id"] for order in orders)
            or data["amount_fen"] != amount or event["agent_id"] != owner
            or any(order["paid_at"] != event["occurred_at"] for order in orders)):
        raise IntegrityError("Purchase payment disagrees with its actual order")
    changes = {f"account:agent:{owner}": -amount, "account:external:scenario": amount}
    if event.get("resource_changes", {}) != changes:
        raise IntegrityError("Purchase payment account changes disagree with its actual amount")
    causes = [sources.get(key) for key in event["caused_by"]]
    if (len(causes) != 1 or causes[0] is None or causes[0]["event_type"] != "action_result"
            or causes[0]["agent_id"] != owner or causes[0]["data"].get("status") not in {"in_progress", "completed"}
            or not any(row["activity_id"] == identity and row["kind"] == "purchase"
                       for row in causes[0]["data"].get("data", {}).get("activities", []))):
        raise IntegrityError("Purchase payment must reference its actual successful purchase start result")
    terminal = [row for source in sources.values() for row in (source["data"].get("activity"),)
                if source["event_type"] == "activity_progress" and source["agent_id"] == owner
                and source["state_version_after"] == event["state_version_after"]
                and isinstance(row, dict) and row["activity_id"] == identity and row["status"] == "completed"
                and row["ended_at"] == event["occurred_at"]]
    if not terminal:
        raise IntegrityError("Purchase payment requires actual same-second purchase completion")


def _validate_payment_notification(orders: list[dict], payment: dict, notification: dict) -> None:
    """@brief 消费通知只引用同一张订单的付款，同刻正文一致；发通知的是支付 app，直付时是下单的内容 app。"""
    data = notification["data"]
    senders = {order["payment_app_id"] if order["payment_app_id"] is not None else order["app_id"]
               for order in orders}
    if (notification["caused_by"] != [payment["event_id"]]
            or notification["occurred_at"] != payment["occurred_at"]
            or notification["state_version_after"] != payment["state_version_after"]
            or notification["agent_id"] != payment["agent_id"]
            or data["order_ids"] != payment["data"]["order_ids"]
            or data["amount_fen"] != payment["data"]["amount_fen"]
            or senders != {data["app_id"]}
            or notification.get("resource_changes", {})):
        raise IntegrityError("Payment notification disagrees with its actual purchase payment")


def _foreground_events(events: list[dict]) -> dict[str, list[dict]]:
    """@brief 前台区间事件按所属活动归组，组内保持事件先后。"""
    grouped = defaultdict(list)
    for event in events:
        if event["event_type"] == "environment_event" and event["data"].get("kind") == "app_foreground":
            grouped[event["activity_id"]].append(event)
    return grouped


def _interval(event: dict) -> dict:
    """@brief 一条前台事件的区间，写成与活动执行区间相同的形状。"""
    return {"started_at": event["data"]["interval_start"], "ended_at": event["data"]["interval_end"]}


def _within(segments: list[dict], execution: list[dict]) -> bool:
    """@brief 每一段都落在某一段真实执行区间之内。"""
    return all(any(outer["started_at"] <= inner["started_at"] and inner["ended_at"] <= outer["ended_at"]
                   for outer in execution) for inner in segments)


def _automatic_app_use(rows: list[dict], activities: dict[str, dict], foreground: dict[str, list[dict]]) -> None:
    """@brief 引擎代为使用的 app 行等于所属活动该 app 全部前台区间合并的结果，状态取所属活动的状态。

    所属活动只能是出行或购买；购买的区间须落在购买真实执行的区间之内，出行的由导航区间那一条检查。
    """
    expected: dict[tuple, list[dict]] = {}
    for activity_id, events in foreground.items():
        for event in events:
            segments = expected.setdefault((activity_id, event["data"]["app_id"]), [])
            if segments and segments[-1]["ended_at"] == event["data"]["interval_start"]:
                segments[-1]["ended_at"] = event["data"]["interval_end"]
            else:
                segments.append(_interval(event))
    actual = {(row["activity_id"], row["app_id"]): row for row in rows if row["fact_kind"] == "app_use"
              and activities[row["activity_id"]]["activity_type"] in _AUTOMATIC_ACTIVITY_TYPES}
    if set(actual) != set(expected):
        raise IntegrityError("Automatic app use disagrees with its foreground events")
    for (activity_id, _), row in actual.items():
        activity = activities[activity_id]
        outside = activity["activity_type"] == "purchase" and not _within(row["segments"], activity["segments"])
        if row["segments"] != expected[activity_id, row["app_id"]] or row["status"] != activity["status"] or outside:
            raise IntegrityError("Automatic app use disagrees with its foreground events")


def _navigation_intervals(activities: dict[str, dict], foreground: dict[str, list[dict]]) -> None:
    """@brief 导航区间属于出行、步骤全为导航、一趟出行只有一个导航 app，不越出这趟出行，不与槽位 2 活动的执行区间重叠。"""
    for activity_id, events in foreground.items():
        trip = activities[activity_id]
        steps = {event["data"]["step"] for event in events}
        if trip["activity_type"] != "travel":
            if "navigation" in steps:
                raise IntegrityError("Navigation app use overlaps a side activity or leaves its trip")
            continue
        intervals = [_interval(event) for event in events]
        busy = [segment for other in activities.values()
                if other["agent_id"] == trip["agent_id"] and "second" in other["resource_claims"]
                for segment in other["segments"]]
        overlapping = any(inner["started_at"] < outer["ended_at"] and outer["started_at"] < inner["ended_at"]
                          for inner in intervals for outer in busy)
        if (steps != {"navigation"} or len({event["data"]["app_id"] for event in events}) != 1
                or not _within(intervals, trip["segments"]) or overlapping):
            raise IntegrityError("Navigation app use overlaps a side activity or leaves its trip")


def _purchase_intervals(activities: dict[str, dict], foreground: dict[str, list[dict]], orders: dict) -> None:
    """@brief 购买的下单与支付区间和各张订单一致。

    每张付款时长大于 0 的订单，付款段 10 至 20 秒，按订单键的顺序排在购买末尾、首尾相接；订单上的付款起止只在
    购买走到那一刻时写下，与支付前台区间一致，购买被截断时区间止于购买结束。下单区间的 app 是该购买某张订单的
    下单 app，且全部早于第一个付款段。
    """
    for purchase in activities.values():
        if purchase["activity_type"] != "purchase":
            continue
        records: dict[str, dict] = {}
        for identity in purchase["data"]["order_ids"]:
            records.setdefault(payment_order_key(orders[identity]["app_id"]), orders[identity])
        paying = [records[key] for key in sorted(records) if records[key]["payment_duration_seconds"] > 0]
        planned_end = purchase["started_at"] + purchase["planned_duration_seconds"]
        stop = planned_end if purchase["ended_at"] is None else purchase["ended_at"]
        first = cursor = planned_end - sum(record["payment_duration_seconds"] for record in paying)
        expected = []
        for record in paying:
            start, cursor = cursor, cursor + record["payment_duration_seconds"]
            written = (start if start <= stop else None, cursor if cursor <= stop else None)
            if (not 10 <= record["payment_duration_seconds"] <= 20
                    or (record["payment_started_at"], record["payment_ended_at"]) != written):
                raise IntegrityError("Purchase app use disagrees with its order phases")
            if start < stop:
                app = record["payment_app_id"] if record["payment_app_id"] is not None else record["app_id"]
                expected.append((app, start, min(cursor, stop)))
        _compare_purchase_events(foreground.get(purchase["activity_id"], []), expected,
                                 ({record["app_id"] for record in records.values()}, first))


def _compare_purchase_events(events: list[dict], payments: list[tuple], ordering: tuple[set, int]) -> None:
    """@brief 一次购买的前台事件：支付区间恰为各张订单应有的那些，下单区间的 app 属于这次购买且不晚于第一个付款段。

    :param payments: 应有的支付区间（app，开始，结束）。
    :param ordering: （这次购买各张订单的下单 app，第一个付款段的开始时刻）。
    """
    apps, first_payment = ordering
    paid = sorted((event["data"]["app_id"], event["data"]["interval_start"], event["data"]["interval_end"])
                  for event in events if event["data"]["step"] == "payment")
    ordered = [event["data"] for event in events if event["data"]["step"] == "ordering"]
    if (len(paid) + len(ordered) != len(events) or paid != sorted(payments)
            or any(data["app_id"] not in apps or data["interval_end"] > first_payment for data in ordered)):
        raise IntegrityError("Purchase app use disagrees with its order phases")


def _expected_order_notices(orders: dict, purchases: dict[str, str], last: int) -> dict[tuple, tuple]:
    """@brief 由最终检查点里的记录算出应有的订单通知。

    :param last: 导出里最后一条事件的时刻；发货时刻晚于它的那一趟还没有发货通知。
    :return: 键是（标题，这组记录里最小的记录身份），值是（时刻，这组记录按身份升序）。
    """
    placed, trips = defaultdict(list), defaultdict(list)
    for identity in sorted(orders):
        order = orders[identity]
        if order["paid_at"] is None:
            continue
        if order["app_id"] is not None:
            placed[purchases.get(identity), order["app_id"]].append(order)
        if order["fulfillment"] != "pickup":
            trips[purchases.get(identity), order["group_key"], order["expected_at"]].append(order)
    expected = {(ORDER_PLACED, rows[0]["order_id"]): (rows[0]["paid_at"], rows) for rows in placed.values()}
    for rows in trips.values():
        head = rows[0]
        paid_at = head["paid_at"]
        shipped_at = paid_at + shipping_delay_seconds(max(head["expected_at"], paid_at) - paid_at)
        if shipped_at <= last:
            expected[ORDER_SHIPPED, head["order_id"]] = (shipped_at, rows)
        if head["fulfillment"] == "delivery" and head["status"] == "delivered":
            expected[ORDER_DELIVERED, head["order_id"]] = (head["arrived_at"], rows)
    return expected


def _order_notifications(events: list[dict], state: dict) -> None:
    """@brief 三种订单通知的条数与内容。

    每张带下单 app 的已付款订单恰一条已下单；每趟已到发货时刻的配送恰一条已发货；每趟已送达的外卖恰一条已送达，
    快递和到店没有。时刻、app、记录集合与起因都和真实的付款或到达事件一致。
    """
    orders = state.get("deliveries", {})
    expected = _expected_order_notices(orders, _purchase_activities(state), events[-1]["occurred_at"])
    paid, arrived, actual = {}, {}, defaultdict(list)
    for event in events:
        kind = event["data"].get("kind")
        if kind == "purchase_payment":
            paid.update(dict.fromkeys(event["data"]["order_ids"], event["event_id"]))
        elif kind == "delivery_arrival":
            arrived.update({order["order_id"]: event["event_id"] for order in event["data"]["orders"]
                            if order["status"] == "delivered"})
        elif kind == "order_notification":
            actual[event["data"]["title"], event["data"]["order_ids"][0]].append(event)
    if set(actual) != set(expected) or any(len(rows) != 1 for rows in actual.values()):
        raise IntegrityError("Order notification count disagrees with actual payment, shipment or delivery")
    for (title, head), (moment, rows) in expected.items():
        event = actual[title, head][0]
        cause = (arrived if title == ORDER_DELIVERED else paid).get(head)
        if (event["occurred_at"] != moment or event["agent_id"] != rows[0]["agent_id"]
                or event["data"]["app_id"] != rows[0]["app_id"]
                or event["data"]["order_ids"] != [row["order_id"] for row in rows]
                or event["caused_by"] != [cause] or event.get("resource_changes", {})):
            raise IntegrityError("Order notification disagrees with its actual order event")
