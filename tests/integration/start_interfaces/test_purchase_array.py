"""购买数组经真实引擎：一份一条商品履约记录、同名两次买两份、运费按配送组、活动名的「×N」、回调与驿站取件。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import advance_next, advance_until_ready, context, do_at, progress_calls, purchase, travel
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import (
    act, act_group, action_result, events, finish_activity, reach, settle_decision,
)

_ZONE = ZoneInfo("Asia/Shanghai")


def _orders(engine, result) -> list:
    """一条购买动作创建的商品履约记录，按 order_id 字符串升序。"""
    activity = engine.context.state.activities[result.activity_id]
    return [engine.context.state.deliveries[key] for key in sorted(activity.data["order_ids"])]


def _effects(engine) -> list:
    """已落库的回调效果事件。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == "callback_effect"]


def test_same_name_twice_buys_two_portions_in_one_delivery_group(engine_factory):
    """规格的例子：牛肉面一份、柠檬茶两份，三条数量为 1 的记录、一个配送组、运费只收一次。"""
    engine = engine_factory()
    _, result = act(engine, "basket", purchase("牛肉面", "柠檬茶", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    orders = _orders(engine, result)
    assert [order.order_id for order in orders] == [f"order-action-basket-{number:04d}" for number in (1, 2, 3)]
    assert [(order.goods_id, order.quantity, order.unit_price_fen, order.delivery_fee_fen) for order in orders] == [
        ("beef-noodles", 1, 2800, 500), ("lemon-tea", 1, 1200, 0), ("lemon-tea", 1, 1200, 0)]
    assert {(order.store_id, order.app_id, order.destination_id, order.fulfillment.value) for order in orders} == {
        ("noodle-1", "meituan", "home", "delivery")}
    assert len({order.group_key for order in orders}) == 1 and sum(order.total_price_fen for order in orders) == 5700
    arrival = datetime(2026, 1, 5, 8, 18, 25, tzinfo=_ZONE)
    assert {order.expected_at for order in orders} == {int(arrival.timestamp())}
    assert {order.distance_meters for order in orders} == {2524}
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name == "购买牛肉面、柠檬茶×2（1月5日 08:00 起）" and activity.resource_claims == {"second"}
    assert activity.activity_id == "activity-action-basket-purchase"
    assert [order["order_id"] for order in result.data["orders"]] == [order.order_id for order in orders]
    assert engine.context.state.accounts["agent:1"] == 200000
    finish_activity(engine, result, "basket")
    assert engine.context.state.accounts["agent:1"] == 200000 - 5700
    record = progress_calls(engine.observe(context(engine, "after")), "start_side_activity")[0]
    assert record["arguments"] == {"action": "purchase", "target": ["牛肉面", "柠檬茶", "柠檬茶"], "prefer": None}
    purchases = record["result"]["details"]["purchases"]
    assert [(row["product"], row["quantity"], row["channel"]) for row in purchases] == [
        ("牛肉面", "1 份", "美团"), ("柠檬茶", "1 份", "美团"), ("柠檬茶", "1 份", "美团")]


def test_more_than_ten_portions_are_accepted_with_four_digit_order_numbers(engine_factory):
    """十件以上被受理；order_id 的序号补零到四位，按字符串升序与编号次序一致。"""
    engine = engine_factory()
    names = ["薯片"] * 7 + ["柠檬茶"] * 4 + ["牛肉面"]
    _, result = act(engine, "many", purchase(*names))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    identities = activity.data["order_ids"]
    assert identities == [f"order-action-many-{number:04d}" for number in range(1, 13)]
    assert identities == sorted(identities) and len(set(identities)) == 12
    orders = [engine.context.state.deliveries[key] for key in identities]
    assert [order.goods_id for order in orders] == sorted(order.goods_id for order in orders)
    assert all(order.quantity == 1 for order in orders)
    fees = {}
    for order in orders:
        fees[order.group_key] = fees.get(order.group_key, 0) + order.delivery_fee_fen
    assert all(fee == 500 for fee in fees.values())
    assert [order.delivery_fee_fen for order in orders].count(500) == len(fees)
    assert activity.name == "购买牛肉面、薯片×7、柠檬茶×4（1月5日 08:00 起）"


def test_two_stores_make_two_delivery_groups_with_two_fees(engine_factory, tmp_path):
    """柠檬茶只有咖啡店出售时，牛肉面与柠檬茶来自两家店：两个配送组，各收一次运费。"""
    root = copy_environment(tmp_path / "two-stores")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    next(row for row in rows if row["goods_id"] == "lemon-tea")["store_kinds"] = ["cafe"]
    write_catalog_rows(root, "merchant-catalog.jsonl", rows)
    engine = engine_factory(environment_root=root)
    _, result = act(engine, "split", purchase("牛肉面", "柠檬茶", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    orders = _orders(engine, result)
    assert {order.store_id for order in orders} == {"noodle-1", "cafe"}
    groups = {}
    for order in orders:
        groups.setdefault(order.group_key, []).append(order)
    assert len(groups) == 2
    assert sorted(sum(order.delivery_fee_fen for order in rows) for rows in groups.values()) == [500, 500]
    assert sum(order.total_price_fen for order in orders) == 2800 + 1200 * 2 + 500 * 2
    assert [order.order_id for order in orders] == sorted(order.order_id for order in orders)
    assert [order.group_key for order in orders] == sorted(order.group_key for order in orders)


def test_balance_short_at_payment_cancels_the_whole_purchase(engine_factory):
    """开始时够、付款时不够：整次购买取消，不会只买其中几件，不扣款也不收货。"""
    def mutate(persona, config):
        persona["initial_balance_fen"] = 5000
        config["reconsideration_interval_seconds"] = 60

    engine = engine_factory(mutate=mutate)
    _, basket = act(engine, "basket", purchase("牛肉面", "薯片"))
    assert basket.status == "in_progress", basket.error_code
    planned = engine.context.state.activities[basket.activity_id].planned_duration_seconds
    assert planned > 60 and advance_until_ready(engine)
    assert engine.context.state.activities[basket.activity_id].status == "active"
    _, taxi = act(engine, "taxi", travel("五里公园", "打车"))
    assert taxi.status == "in_progress", taxi.error_code
    balance = engine.context.state.accounts["agent:1"]
    assert balance < sum(order.total_price_fen for order in _orders(engine, basket))
    reach(engine, engine.context.state.activities[basket.activity_id].planned_end_at)
    failed = engine.context.state.activities[basket.activity_id]
    assert failed.status == "failed" and failed.interruption_reason == "INSUFFICIENT_FUNDS"
    assert {order.status for order in _orders(engine, basket)} == {"cancelled"}
    assert engine.context.state.accounts["agent:1"] == balance
    kinds = [row.data.get("kind") for row in events(engine, "environment_event")]
    assert "purchase_payment" not in kinds and "goods_received" not in kinds


def _callback_environment(tmp_path, rules):
    """夹具副本加一份回调声明并刷新清单。"""
    root = copy_environment(tmp_path / "callbacks")
    state = {"state": "stock", "initial": 3, "cap": 3, "reset_on_goods": []}
    write_catalog_rows(root, "callbacks.jsonl", [state, *sorted(rules, key=lambda item: item["rule_id"])])
    return root


def test_one_purchase_of_several_goods_triggers_its_action_callback_once(engine_factory, tmp_path):
    """回调的动作触发按开始调用求值：一次购买无论几件只触发一次，原因回链这条调用自己的受理结果。"""
    rules = [{"rule_id": "buy", "trigger": {"class": "action", "key": "purchase"},
              "effect": {"type": "state_delta", "state": "stock", "delta": -1}},
             {"rule_id": "ride", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
              "effect": {"type": "transfer", "direction": "expense", "amount_fen": 30}},
             {"rule_id": "stroll", "trigger": {"class": "action", "key": "travel", "travel_method_id": "walking"},
              "effect": {"type": "state_delta", "state": "stock", "delta": -1}}]
    engine = engine_factory(environment_root=_callback_environment(tmp_path, rules))
    _, (trip, basket) = act_group(engine, "pair", travel("公司", "驾车"), purchase("薯片", "薯片", "柠檬茶", "牛肉面"))
    assert (trip.status, basket.status) == ("in_progress", "in_progress")
    effects = _effects(engine)
    assert [row.data["rule_id"] for row in effects] == ["ride", "buy"]
    assert engine.context.state.agents[1].callback_states == {"stock": 2}
    results = {row.action_id: row.event_id for row in events(engine, "action_result")}
    assert effects[0].caused_by == [results["action-pair-1"]] and effects[1].caused_by == [results["action-pair-2"]]
    fare = 500
    assert engine.context.state.accounts["agent:1"] == 200000 - fare - 30


def _parcel_environment(persona, config):
    """运行六个本地日，足够快递到站。"""
    config["stop_time"] = config["start_time"] + timedelta(days=6)


def _to_station(engine):
    """驾车到快递驿站并收尾。"""
    _, trip = act(engine, f"to-station-{engine.context.state.current_time}", travel("快递驿站", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, f"at-station-{engine.context.state.current_time}")
    assert engine.context.state.agents[1].location_id == "parcel-station"


def _until_free(engine):
    """推进并处理决策机会，直到人物有决策机会而手上没有进行中或待收尾的活动。"""
    counter = 0
    while True:
        counter += 1
        assert counter < 500
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        occupied = [item for item in engine.context.state.activities.values()
                    if item.status == "active" or item.status != "paused" and item.concluded_at is None
                    and item.status != "truncated"]
        if not occupied:
            return
        settle_decision(engine, f"free-{counter}-{engine.context.state.current_time}")


def _two_unit_station(tmp_path):
    """夹具副本：快递驿站多绑定一个五分钟的单元「驿站等候」，用来证明点名哪个单元都一样取件。"""
    root = copy_environment(tmp_path / "station")
    rows = catalog_rows(root, "content-catalog.jsonl")
    service = next(row for row in rows if row["content_id"] == "parcel-service")
    rows.append({**service, "content_id": "parcel-wait", "duration_seconds": 300, "title": "驿站等候"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return root


def test_station_offers_nothing_while_no_parcel_is_ready(engine_factory, tmp_path):
    """收件地点没有到站包裹时，那里的任何一件事都以 PARCEL_NOT_READY 拒绝，工具层各行同样标注。"""
    engine = engine_factory(_two_unit_station(tmp_path))
    _to_station(engine)
    reason = "No parcels are ready for you at this station."
    for unit in ("驿站取件", "驿站等候"):
        observed, result = act(engine, f"pickup-{unit}", do_at("快递驿站", unit))
        assert result.status == "rejected" and result.error_code == "PARCEL_NOT_READY"
        assert result.message == reason
        assert result.data["unmet"] == ["parcel_ready"] and result.data["fix"] is None
        station = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "快递驿站")
        assert [(row["action"], row["detail"], row["available"], row["reason"])
                for row in station["actions"]] == [
            ("interact", "驿站取件", False, reason), ("interact", "驿站等候", False, reason)]
    assert all(item.kind.value == "travel" for item in engine.context.state.activities.values())


def test_naming_a_unit_at_the_station_collects_every_ready_parcel(engine_factory, tmp_path):
    """有到站包裹时点名驿站的任一单元（这里点名第二个）：一次领完并开始该单元，收货事件回链这条受理结果，
    结果带领取后的快照。"""
    engine = engine_factory(_two_unit_station(tmp_path), mutate=_parcel_environment)
    _, bought = act(engine, "notebook", purchase("平台笔记本", "平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "notebook")
    parcels = _orders(engine, bought)
    assert {order.fulfillment.value for order in parcels} == {"station_pickup"}
    assert {order.destination_id for order in parcels} == {"parcel-station"}
    reach(engine, max(order.expected_at for order in parcels))
    _until_free(engine)
    assert {engine.context.state.deliveries[order.order_id].status for order in parcels} == {"ready_for_pickup"}
    _to_station(engine)
    observed, pickup = act(engine, "pickup", do_at("快递驿站", "驿站等候"))
    assert pickup.status == "in_progress", pickup.error_code
    station = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "快递驿站")
    assert [(row["detail"], row["available"], row["duration"]) for row in station["actions"]] == [
        ("驿站取件", True, "约 3 分钟"), ("驿站等候", True, "约 5 分钟")]
    activity = engine.context.state.activities[pickup.activity_id]
    assert activity.name.startswith("进行驿站等候（") and activity.data["content_id"] == "parcel-wait"
    assert activity.planned_duration_seconds == 300 and activity.resource_claims == {"first"}
    assert {engine.context.state.deliveries[order.order_id].status for order in parcels} == {"delivered"}
    snapshots = action_result(engine, "action-pickup").data["orders"]
    assert [row["order_id"] for row in snapshots] == [order.order_id for order in parcels]
    assert {row["status"] for row in snapshots} == {"delivered"}
    assert all(row["received_at"] == engine.context.state.current_time for row in snapshots)
    accepted = next(row for row in events(engine, "action_result") if row.action_id == "action-pickup")
    received = [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]
    assert sorted(row.data["order_id"] for row in received) == [order.order_id for order in parcels]
    assert all(row.caused_by == [accepted.event_id] for row in received)


def test_pickup_and_purchase_in_one_group_keep_their_own_orders_and_evidence(engine_factory):
    """在驿站同一轮点名取件并再买一份快递：取件结果只带领取后的快照，购买结果只带它自己下的那条记录；
    公开证据里取件动作没有任何订单事实，购买动作的订单事实只指向新下的记录，两条到站包裹各有一条收货事实。"""
    from worldmock.evaluation.public_evidence import worldmock_public_evidence

    engine = engine_factory(mutate=_parcel_environment)
    _, bought = act(engine, "notebook", purchase("平台笔记本", "平台笔记本"))
    finish_activity(engine, bought, "notebook")
    parcels = [order.order_id for order in _orders(engine, bought)]
    reach(engine, max(order.expected_at for order in _orders(engine, bought)))
    _until_free(engine)
    _to_station(engine)
    _, (pickup, again) = act_group(engine, "both", do_at("快递驿站", "驿站取件"), purchase("平台笔记本"))
    assert (pickup.status, again.status) == ("in_progress", "in_progress"), (pickup.error_code, again.error_code)
    (fresh,) = [order.order_id for order in _orders(engine, again)]
    assert [(row["order_id"], row["status"]) for row in pickup.data["orders"]] == [
        (identity, "delivered") for identity in parcels]
    assert [(row["order_id"], row["status"]) for row in action_result(engine, again.action_id).data["orders"]] == [
        (fresh, "pending")]
    recorded = events(engine)
    owner = {row.event_id: row.action_id for row in recorded}
    facts = {(row.action, row.interaction, row.object_type, row.object_key, row.state,
              tuple(owner[identity] for identity in row.source_event_ids))
             for row in worldmock_public_evidence(recorded, [])}
    assert {fact for fact in facts if pickup.action_id in fact[5]} == {
        ("interact", "interact", "content", "parcel-service", "requested", (pickup.action_id,)),
        ("interact", "interact", "content", "parcel-service", "started", (pickup.action_id,))}
    assert {fact for fact in facts if again.action_id in fact[5]} == {
        ("purchase", "order", "order", fresh, "requested", (again.action_id,)),
        ("purchase", "order", "order", fresh, "pending", (again.action_id,))}
    assert {fact[:5] for fact in facts if fact[1] == "receive"} == {
        ("purchase", "receive", "order", identity, "received") for identity in parcels}
