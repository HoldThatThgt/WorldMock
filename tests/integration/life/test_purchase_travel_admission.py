"""真实购买与出行组合的在场、总价和整组回滚。"""

import pytest

from tests.life.conftest import advance_next, conclude_request, start_activity
from tests.life.environment_runtime import act, events, reach
from worldmock.catalog.graph import Graph
from worldmock.catalog.models import MapEdge, MapGraph
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import quoted_basket
from worldmock.life.guidance import rejection_guidance
from worldmock.life.models import StartActivityRequest
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.travel import pay_travel_fare, plan_trip
from worldmock.life.weather import current_weather_factor


def _configure(persona, config):
    config["progress_report_interval_seconds"] = 1


def _at_store(persona, config):
    _configure(persona, config)
    persona["initial_location"] = "四季便利"


def _group(action, goods, reverse):
    items = [{"action": action, "target": "公司"}, {"action": "purchase", "target": goods}]
    return StartActivityRequest(items=list(reversed(items)) if reverse else items)


def _business(engine, kind):
    return [event for event in events(engine, "environment_event") if event.data.get("kind") == kind]


def _next_decision(engine):
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)


def _with_budget(engine_factory, difference):
    probe = engine_factory()
    state = probe.context.state
    quote = quote_goods(probe.context.inputs, state, 1, "online-notebook")
    _, total = quoted_basket(probe.context.inputs, state, 1, ("online-notebook",))
    trip = plan_trip(probe.context.catalog.graph, "home", "office", "taxi", current_weather_factor(state))
    engine = engine_factory(mutate=lambda persona, config: persona.update(
        initial_balance_fen=total + trip.fare_fen + difference))
    assert quote_goods(engine.context.inputs, engine.context.state, 1, "online-notebook") == quote
    return engine, quote, total, trip


def _assert_rollback(engine, before):
    after = engine.context.state
    assert after.accounts == before.accounts
    assert after.deliveries == before.deliveries
    expected = dict(before.activities)
    if "progress_report_due" in before.agents[1].pending_wake_reasons:
        interval = engine.context.config.progress_report_interval_seconds
        for identity, activity in before.activities.items():
            if activity.status == "active" and before.current_time - activity.report_anchor_at >= interval:
                expected[identity] = activity.model_copy(update={"report_anchor_at": before.current_time})
    assert after.activities == expected
    assert after.random_state == before.random_state
    assert after.sessions == before.sessions and after.agents[1].location_id == before.agents[1].location_id
    for kind in ("app_touch", "purchase_payment", "goods_received"):
        assert not [event for event in _business(engine, kind) if event.state_version_after > before.version]


@pytest.mark.parametrize("action", ["walk", "drive", "take_taxi", "take_bus"])
@pytest.mark.parametrize("reverse", [False, True])
def test_local_purchase_and_departure_reject_the_whole_request(engine_factory, action, reverse):
    engine = engine_factory(mutate=_at_store)
    before = engine.context.state.model_copy(deep=True)
    _, result = act(engine, "local-departure", _group(action, "纸巾", reverse))
    assert result.status == "rejected" and result.error_code == "ACTIVITY_PRECONDITION_UNMET"
    _assert_rollback(engine, before)


def test_existing_local_purchase_blocks_departure_until_concluded(engine_factory):
    engine = engine_factory(mutate=_at_store)
    _, purchase = act(engine, "local-purchase", start_activity("purchase", "纸巾"))
    assert purchase.status == "in_progress"
    _next_decision(engine)
    before = engine.context.state.model_copy(deep=True)
    observed, rejected = act(engine, "leave-purchase", start_activity("walk", "公司"))
    assert rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "公司")
    option = next(row for row in target["actions"] if row["action"] == "walk")
    assert option["available"] is False and "Conclude" in option["reason"]
    assert "pause" not in option["reason"]
    _assert_rollback(engine, before)
    name = before.activities[purchase.activity_id].name
    _, concluded = act(engine, "cancel-local", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed"
    assert all(order.status == "cancelled" and order.paid_at is None for order in engine.context.state.deliveries.values())
    _, departed = act(engine, "leave-after-cancel", start_activity("walk", "公司"))
    assert departed.status == "in_progress"
    assert _business(engine, "purchase_payment") == _business(engine, "goods_received") == []


@pytest.mark.parametrize("reverse", [False, True])
def test_combined_fare_and_purchase_shortfall_rejects_before_either_starts(engine_factory, reverse):
    engine, _, total, trip = _with_budget(engine_factory, -1)
    assert trip.fare_fen > 0 and total > 0
    before = engine.context.state.model_copy(deep=True)
    assert before.accounts["agent:1"] >= max(total, trip.fare_fen)
    _, result = act(engine, "short-total", _group("take_taxi", "平台笔记本", reverse))
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_FUNDS"
    _assert_rollback(engine, before)


@pytest.mark.parametrize("reverse", [False, True])
def test_exact_combined_budget_pays_fare_then_frozen_courier_quote_at_completion(engine_factory, reverse):
    engine, quote, total, trip = _with_budget(engine_factory, 0)
    _, result = act(engine, "exact-total", _group("take_taxi", "平台笔记本", reverse))
    assert result.status == "in_progress", result.error_code
    assert engine.context.state.accounts["agent:1"] == total
    purchase = next(row for row in engine.context.state.activities.values() if row.kind == "purchase")
    order = next(iter(engine.context.state.deliveries.values()))
    assert (order.store_id, order.app_id, order.destination_id, order.expected_at) == (
        quote.store_id, quote.app_id, quote.destination_id, quote.expected_at)
    assert order.total_price_fen == total and order.paid_at is None
    reach(engine, purchase.planned_end_at)
    order = engine.context.state.deliveries[order.order_id]
    assert order.paid_at == purchase.planned_end_at and order.status == "pending"
    assert order.received_at is None and engine.context.state.accounts == {"agent:1": 0, "external:scenario": 0}
    payments = _business(engine, "purchase_payment")
    assert len(payments) == 1 and payments[0].data["amount_fen"] == total
    assert payments[0].occurred_at == purchase.planned_end_at


@pytest.mark.parametrize("reverse", [False, True])
def test_delivery_and_departure_preserve_the_real_initial_quote_and_destination(engine_factory, reverse):
    engine = engine_factory()
    state = engine.context.state
    quote = quote_goods(engine.context.inputs, state, 1, "lemon-tea")
    _, result = act(engine, "delivery-departure", _group("walk", "柠檬茶", reverse))
    assert result.status == "in_progress", result.error_code
    order = next(iter(engine.context.state.deliveries.values()))
    assert order.fulfillment == "delivery" and order.destination_id == "home"
    assert (order.store_id, order.app_id, order.expected_at, order.delivery_fee_fen) == (
        quote.store_id, quote.app_id, quote.expected_at, quote.delivery_fee_fen)
    purchase = next(row for row in engine.context.state.activities.values() if row.kind == "purchase")
    reach(engine, purchase.planned_end_at)
    current = engine.context.state.deliveries[order.order_id]
    assert current.paid_at == purchase.planned_end_at and current.received_at is None
    assert current.destination_id == "home" and current.store_id == quote.store_id
    assert _business(engine, "purchase_payment")[0].data["amount_fen"] == 1700


@pytest.mark.parametrize("goods,accepted", [("平台笔记本", True), ("柠檬茶", False)])
def test_in_transit_purchase_accepts_only_goods_without_a_stay_destination(engine_factory, goods, accepted):
    engine = engine_factory(mutate=_configure)
    _, travel = act(engine, "departure", start_activity("walk", "公司"))
    assert travel.status == "in_progress"
    _next_decision(engine)
    before = engine.context.state.model_copy(deep=True)
    _, result = act(engine, "in-transit-purchase", start_activity("purchase", goods))
    if accepted:
        assert result.status == "in_progress", result.error_code
        order = next(iter(engine.context.state.deliveries.values()))
        assert order.fulfillment == "station_pickup" and order.destination_id == "parcel-station"
        assert order.paid_at is None and engine.context.state.accounts == before.accounts
    else:
        assert result.status == "rejected" and result.error_code == "ACTIVITY_PRECONDITION_UNMET"
        _assert_rollback(engine, before)


def test_existing_courier_purchase_allows_departure_without_requoting(engine_factory):
    engine = engine_factory(mutate=_configure)
    _, purchase = act(engine, "courier", start_activity("purchase", "平台笔记本"))
    assert purchase.status == "in_progress"
    _next_decision(engine)
    before = next(iter(engine.context.state.deliveries.values())).model_copy(deep=True)
    _, travel = act(engine, "courier-departure", start_activity("walk", "公司"))
    assert travel.status == "in_progress", travel.error_code
    assert engine.context.state.deliveries[before.order_id] == before
    assert engine.context.state.activities[purchase.activity_id].status == "active"


def test_existing_location_content_requires_presence_but_app_content_can_depart(engine_factory):
    for target, accepted in (("家", False), ("哔哩哔哩", True)):
        engine = engine_factory(mutate=_configure)
        _, started = act(engine, "content", start_activity("interact", target))
        assert started.status == "in_progress", started.error_code
        _next_decision(engine)
        _, travel = act(engine, "content-departure", start_activity("walk", "公司"))
        assert travel.status == ("in_progress" if accepted else "rejected")
        if not accepted:
            assert travel.error_code == "ACTIVITY_PRECONDITION_UNMET"
        assert engine.context.state.activities[started.activity_id].status == "active"


@pytest.mark.parametrize("mode", ["walk", "flying"])
def test_trip_plan_rejects_action_names_and_unknown_modes_without_changing_the_graph(loaded_catalog, mode, caplog):
    graph = loaded_catalog.graph
    before = graph.shortest_paths("home")
    with pytest.raises(ValueError, match="Travel mode is outside the fixed set"):
        plan_trip(graph, "home", "office", mode)
    assert graph.shortest_paths("home") == before
    assert "Travel mode is outside the fixed set" in caplog.text


def test_same_place_is_not_a_positive_duration_trip(loaded_catalog, caplog):
    graph = loaded_catalog.graph
    assert graph.path("home", "home") == ("home",)
    assert graph.distance_meters("home", "home") == 0
    with pytest.raises(BusinessRuleError, match="^ROUTE_DURATION_INVALID$") as failure:
        plan_trip(graph, "home", "home", "walking")
    assert rejection_guidance(failure.value)["reason"] == "The route duration exceeds the maximum travel duration."
    assert "Travel route duration is outside the supported interval" in caplog.text


@pytest.mark.parametrize("last_length,accepted", [(208, True), (209, False)])
def test_snow_trip_accepts_one_day_and_rejects_the_next_longer_published_path(last_length, accepted, caplog):
    nodes = tuple(f"place-{index:02d}" for index in range(33))
    edges = tuple(MapEdge(a=nodes[index], b=nodes[index + 1], length_m=2000 if index < 31 else last_length)
                  for index in range(32))
    graph = Graph(MapGraph(nodes=nodes, edges=edges))
    assert graph.path(nodes[0], nodes[-1]) == nodes
    assert graph.distance_meters(nodes[0], nodes[-1]) == 62000 + last_length
    if accepted:
        trip = plan_trip(graph, nodes[0], nodes[-1], "walking", 0.6)
        assert trip.path == nodes and trip.distance_meters == 62208
        assert trip.duration_seconds == 86400 and trip.fare_fen == 0
        assert trip.origin_id == nodes[0] and trip.destination_id == nodes[-1]
    else:
        with pytest.raises(BusinessRuleError, match="^ROUTE_DURATION_INVALID$"):
            plan_trip(graph, nodes[0], nodes[-1], "walking", 0.6)
        assert "Travel route duration is outside the supported interval" in caplog.text


def test_fare_shortfall_preserves_the_actual_world_and_produces_the_declared_refusal(engine_factory, caplog):
    engine = engine_factory(mutate=lambda persona, config: persona.update(initial_balance_fen=199))
    state = engine.context.state
    trip = plan_trip(engine.context.catalog.graph, "home", "office", "bus", current_weather_factor(state))
    assert trip.fare_fen == 200
    before = state.model_dump(mode="json")
    event_seq = engine.context.store.last_event_seq
    with pytest.raises(BusinessRuleError, match="^INSUFFICIENT_TRAVEL_FUNDS$") as failure:
        pay_travel_fare(state, 1, trip)
    assert rejection_guidance(failure.value) == {
        "reason": "The caller's available balance is below the route fare.", "next_step": None, "fix": None,
    }
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == event_seq
    assert "INSUFFICIENT_TRAVEL_FUNDS" in caplog.text


@pytest.mark.parametrize("mode,fare,balance", [("walking", 0, 0), ("bus", 200, 200)])
def test_departure_fare_receipt_uses_the_exact_world_accounts_and_zero_sum_transfer(engine_factory, mode, fare, balance):
    engine = engine_factory(mutate=lambda persona, config: persona.update(initial_balance_fen=balance))
    state = engine.context.state
    before = state.model_dump(mode="json")
    trip = plan_trip(engine.context.catalog.graph, "home", "office", mode, current_weather_factor(state))
    assert trip.fare_fen == fare
    changed, receipt = pay_travel_fare(state, 1, trip)
    assert changed.accounts == {"agent:1": 0, "external:scenario": 0}
    assert sum(changed.accounts.values()) == sum(state.accounts.values())
    assert receipt == {
        "trip_id": trip.trip_id, "fare_fen": fare, "currency": "CNY",
        "payer_account_id": "agent:1", "payee_account_id": "external:scenario",
        "payment_timing": "accepted_departure", "cancellation_refund_fen": 0,
        "resource_changes": {"account:agent:1": -fare, "account:external:scenario": fare},
    }
    assert state.model_dump(mode="json") == before
    if fare == 0:
        assert changed.model_dump(mode="json") == before
    else:
        assert changed.model_copy(update={"accounts": state.accounts}).model_dump(mode="json") == before


def _declared_world(tmp_path, *rows):
    """夹具副本加 map.jsonl：每行声明一对地点的距离，清单按生产发布入口刷新。"""
    from tests.life.environment_inputs import copy_environment, write_catalog_rows

    root = copy_environment(tmp_path / "declared-input")
    write_catalog_rows(root, "map.jsonl", [{"a": a, "b": b, "length_m": meters} for a, b, meters in rows])
    return root


def _seed_with_weather(condition):
    """找出运行首日抽到指定天气的最小种子；声明的距离不随种子变化。"""
    import hashlib

    from worldmock.exchange.constants import WEATHER_FACTORS
    from worldmock.life.randomness import random_stream

    for seed in range(500):
        state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
        if random_stream(state, "weather:2026-01-05").choice(tuple(WEATHER_FACTORS)) == condition:
            return seed
    raise AssertionError(condition)


def test_tool_layer_quotes_the_declared_distance_for_every_travel_action(engine_factory, tmp_path):
    """家到公司声明 12,600 米、当天小雨：工具层给出的用时和车费就是按声明值算出的结果。"""
    engine = engine_factory(_declared_world(tmp_path, ("home", "office", 12600)))
    state, graph = engine.context.state, engine.context.catalog.graph
    assert state.weather_by_date == {"2026-01-05": "light_rain"} and current_weather_factor(state) == 0.85
    assert graph.distance_meters("home", "office") == 12600 and graph.path("home", "office") == ("home", "office")
    trips = {mode: plan_trip(graph, "home", "office", mode, 0.85) for mode in ("walking", "driving", "taxi", "bus")}
    assert {mode: (trip.duration_seconds, trip.fare_fen) for mode, trip in trips.items()} == {
        "walking": (12353, 0), "driving": (2118, 2520), "taxi": (2118, 3780), "bus": (2965, 200)}
    observed, result = act(engine, "declared-drive", start_activity("drive", "公司"))
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "公司")
    assert {row["action"]: (row["available"], row["price"], row["duration"]) for row in target["actions"]} == {
        "walk": (True, "0 元", "约 3.5 小时"), "drive": (True, "25.2 元", "约 35 分钟"),
        "take_taxi": (True, "37.8 元", "约 35 分钟"), "take_bus": (True, "2 元", "约 49 分钟")}
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.planned_duration_seconds == 2118 and activity.planned_end_at - activity.started_at == 2118
    assert (activity.data["distance_meters"], activity.data["path"]) == (12600, ["home", "office"])
    assert engine.context.state.accounts["agent:1"] == 200000 - 2520


@pytest.mark.parametrize("condition,action,seconds", [
    ("sunny", "drive", 1800), ("cloudy", "drive", 1800), ("light_rain", "drive", 2118),
    ("heavy_rain", "drive", 2572), ("snow", "drive", 3000), ("sunny", "walk", 10500)])
def test_accepted_trip_over_the_declared_distance_takes_the_documented_time_in_each_weather(
        engine_factory, tmp_path, condition, action, seconds):
    """12,600 米驾车：晴和多云 30 分钟，小雨 35 分 18 秒，大雨 42 分 52 秒，雪 50 分钟；晴天步行 2 小时 55 分。"""
    seed = _seed_with_weather(condition)
    engine = engine_factory(_declared_world(tmp_path, ("home", "office", 12600)),
                            mutate=lambda persona, config: config.update(seed=seed))
    assert engine.context.state.weather_by_date == {"2026-01-05": condition}
    assert engine.context.catalog.shortest_m("home", "office") == 12600
    _, result = act(engine, f"declared-{condition}", start_activity(action, "公司"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.planned_duration_seconds == seconds
    assert activity.data["distance_meters"] == 12600 and activity.data["path"] == ["home", "office"]
    reach(engine, activity.planned_end_at)
    final = engine.context.state.activities[result.activity_id]
    assert final.status == "completed" and final.ended_at - final.started_at == seconds
    assert final.route_progress_meters == 12600 and engine.context.state.agents[1].location_id == "office"


@pytest.mark.parametrize("cafe_m,noodle_m,store,goods,seconds", [
    (4500, 13400, "cafe", "柠檬茶", 1500), (13400, 4500, "noodle-1", "柠檬茶", 1500),
    (4500, 13400, "noodle-1", "牛肉面", 3280)])
def test_delivery_store_and_expected_arrival_follow_the_declared_distances(
        engine_factory, tmp_path, cafe_m, noodle_m, store, goods, seconds):
    """外卖从声明距离最近的店送出，时长是备餐 600 秒加距离除以 5 米每秒，不乘天气系数。"""
    def at_office(persona, config):
        persona["initial_location"] = "公司"

    root = _declared_world(tmp_path, ("cafe", "office", cafe_m), ("noodle-1", "office", noodle_m))
    engine = engine_factory(root, mutate=at_office)
    catalog, state = engine.context.catalog, engine.context.state
    assert current_weather_factor(state) == 0.85
    assert (catalog.shortest_m("cafe", "office"), catalog.shortest_m("noodle-1", "office")) == (cafe_m, noodle_m)
    goods_id = next(key for key, item in catalog.goods.items() if item.name == goods)
    quote = quote_goods(engine.context.inputs, state, 1, goods_id)
    assert (quote.store_id, quote.destination_id, quote.fulfillment) == (store, "office", "delivery")
    assert quote.distance_meters == catalog.shortest_m(store, "office")
    assert quote.expected_at - state.current_time == seconds == 600 + -(-quote.distance_meters // 5)
    _, result = act(engine, "declared-delivery", start_activity("purchase", goods))
    assert result.status == "in_progress", result.error_code
    order = next(iter(engine.context.state.deliveries.values()))
    assert (order.store_id, order.destination_id) == (store, "office")
    assert order.expected_at - state.current_time == seconds
