"""真实购买与出行组合的槽位、在场、总价和整组回滚。"""

import pytest

from tests.life.conftest import (
    advance_next, conclude_request, do_at, purchase, target_actions, travel, travel_rows, use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from tests.life.prerequisite_inputs import CLOCK, OFFICE, app_content, dingtalk_environment, example_window, moment
from worldmock.catalog.graph import Graph
from worldmock.catalog.models import MapEdge, MapGraph
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import quoted_basket
from worldmock.life.guidance import rejection_guidance
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.travel import pay_travel_fare, plan_trip
from worldmock.life.weather import current_weather_factor

#: 开始组有两条调用时追加在拒绝原因后的一句。
NEITHER = " Neither start request was started."
#: 四种固定出行方式的名称，按工具层的行序。
METHODS = ["步行", "驾车", "打车", "公共交通"]


def _configure(persona, config):
    config["progress_report_interval_seconds"] = 1


def _at_store(persona, config):
    _configure(persona, config)
    persona["initial_location"] = "四季便利"


def _group(engine, label, method, goods, reverse):
    """同一决策登记去公司的出行与一次购买（reverse 时购买在前），返回（出行结果，购买结果）。"""
    requests = [travel("公司", method), purchase(goods)]
    _, results = act_group(engine, label, *(reversed(requests) if reverse else requests))
    return tuple(reversed(results)) if reverse else tuple(results)


def _target_item(position, written, code, reason):
    """业务拒绝为负责的调用写下的明细：路径落在该调用的 target 上，written 是它所写的原值，不带候选。"""
    return {"path": ["tool_calls", position, "function", "arguments", "target"], "written": written,
            "error_code": code, "allowed_values": [], "expected": reason, "why": reason, "candidates": []}


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


@pytest.mark.parametrize("method", METHODS, ids=["walk", "drive", "take_taxi", "take_bus"])
@pytest.mark.parametrize("reverse", [False, True])
def test_local_purchase_and_departure_reject_the_whole_request(engine_factory, method, reverse):
    engine = engine_factory(mutate=_at_store)
    before = engine.context.state.model_copy(deep=True)
    departure, bought = _group(engine, "local-departure", method, "纸巾", reverse)
    # 到店购买不能与出发同组开始：两条动作得到同一份拒绝，明细只落在购买那条调用上
    reason = "A local purchase cannot start with departure."
    assert departure.status == bought.status == "rejected"
    assert departure.error_code == bought.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert departure.message == bought.message == reason + NEITHER
    assert departure.data == bought.data
    assert bought.data["next_step"] == "Complete or conclude the local purchase before leaving."
    assert bought.data["items"] == [_target_item(0 if reverse else 1, ["纸巾"], "ACTIVITY_PRECONDITION_UNMET", reason)]
    _assert_rollback(engine, before)


def test_existing_local_purchase_blocks_departure_until_concluded(engine_factory):
    engine = engine_factory(mutate=_at_store)
    _, bought = act(engine, "local-purchase", purchase("纸巾"))
    assert bought.status == "in_progress"
    _next_decision(engine)
    before = engine.context.state.model_copy(deep=True)
    name = before.activities[bought.activity_id].name
    reason = f"Conclude {name} before departure."
    observed, rejected = act(engine, "leave-purchase", travel("公司"))
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert rejected.message == reason and rejected.data["next_step"] == f"Conclude {name}."
    assert rejected.data["unmet"] == ["activities_before_departure"]
    assert rejected.data["items"] == [_target_item(0, "公司", "ACTIVITY_PRECONDITION_UNMET", reason)]
    # 工具层去公司的每种出行方式都写着与受理一字不差的原因；购买不能暂停，原因里只有结束
    options = travel_rows(observed, "公司")
    assert list(options) == METHODS
    assert all(option["available"] is False and option["reason"] == reason for option in options.values())
    assert "pause" not in reason
    _assert_rollback(engine, before)
    _, concluded = act(engine, "cancel-local", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed"
    assert all(order.status == "cancelled" and order.paid_at is None for order in engine.context.state.deliveries.values())
    _, departed = act(engine, "leave-after-cancel", travel("公司"))
    assert departed.status == "in_progress"
    assert _business(engine, "purchase_payment") == _business(engine, "goods_received") == []


@pytest.mark.parametrize("reverse", [False, True])
def test_combined_fare_and_purchase_shortfall_rejects_before_either_starts(engine_factory, reverse):
    engine, _, total, trip = _with_budget(engine_factory, -1)
    assert trip.fare_fen > 0 and total > 0
    before = engine.context.state.model_copy(deep=True)
    assert before.accounts["agent:1"] >= max(total, trip.fare_fen)
    departure, bought = _group(engine, "short-total", "打车", "平台笔记本", reverse)
    # 余额少于「货款 + 运费 + 同组车费」：车费参与合计，两条调用都负责，各有一条明细
    reason = "The caller's available balance is below the actual purchase total."
    assert departure.status == bought.status == "rejected"
    assert departure.error_code == bought.error_code == "INSUFFICIENT_FUNDS"
    assert departure.message == bought.message == reason + NEITHER
    assert departure.data == bought.data
    written = [["平台笔记本"], "公司"] if reverse else ["公司", ["平台笔记本"]]
    assert bought.data["items"] == [_target_item(position, target, "INSUFFICIENT_FUNDS", reason)
                                    for position, target in enumerate(written)]
    _assert_rollback(engine, before)


@pytest.mark.parametrize("reverse", [False, True])
def test_exact_combined_budget_pays_fare_then_frozen_courier_quote_at_completion(engine_factory, reverse):
    engine, quote, total, trip = _with_budget(engine_factory, 0)
    departure, bought = _group(engine, "exact-total", "打车", "平台笔记本", reverse)
    assert departure.status == bought.status == "in_progress", (departure.error_code, bought.error_code)
    assert engine.context.state.accounts["agent:1"] == total
    # 出行与快递类购买同一时刻开始，各占一个槽位；商品履约记录只记在购买那条动作的结果里
    trip_activity = engine.context.state.activities[departure.activity_id]
    purchase_activity = engine.context.state.activities[bought.activity_id]
    assert (trip_activity.kind, trip_activity.resource_claims) == ("travel", {"first"})
    assert (purchase_activity.kind, purchase_activity.resource_claims) == ("purchase", {"second"})
    assert trip_activity.started_at == purchase_activity.started_at
    order = next(iter(engine.context.state.deliveries.values()))
    assert order.order_id == f"order-{bought.action_id}-0001"
    assert [row["order_id"] for row in bought.data["orders"]] == [order.order_id] and departure.data["orders"] == []
    assert (order.store_id, order.app_id, order.destination_id, order.expected_at) == (
        quote.store_id, quote.app_id, quote.destination_id, quote.expected_at)
    assert order.total_price_fen == total and order.paid_at is None
    reach(engine, purchase_activity.planned_end_at)
    order = engine.context.state.deliveries[order.order_id]
    assert order.paid_at == purchase_activity.planned_end_at and order.status == "pending"
    assert order.received_at is None and engine.context.state.accounts == {"agent:1": 0, "external:scenario": 0}
    payments = _business(engine, "purchase_payment")
    assert len(payments) == 1 and payments[0].data["amount_fen"] == total
    assert payments[0].occurred_at == purchase_activity.planned_end_at


@pytest.mark.parametrize("reverse", [False, True])
def test_delivery_and_departure_preserve_the_real_initial_quote_and_destination(engine_factory, reverse):
    engine = engine_factory()
    state = engine.context.state
    quote = quote_goods(engine.context.inputs, state, 1, "lemon-tea")
    departure, bought = _group(engine, "delivery-departure", "步行", "柠檬茶", reverse)
    assert departure.status == bought.status == "in_progress", (departure.error_code, bought.error_code)
    # 与出发同组的外卖按出发前的停留地点报价，收货地点就是出发地
    order = next(iter(engine.context.state.deliveries.values()))
    assert order.fulfillment == "delivery" and order.destination_id == "home"
    assert (order.store_id, order.app_id, order.expected_at, order.delivery_fee_fen) == (
        quote.store_id, quote.app_id, quote.expected_at, quote.delivery_fee_fen)
    assert engine.context.state.activities[departure.activity_id].status == "active"
    purchase_activity = engine.context.state.activities[bought.activity_id]
    reach(engine, purchase_activity.planned_end_at)
    current = engine.context.state.deliveries[order.order_id]
    assert current.paid_at == purchase_activity.planned_end_at and current.received_at is None
    assert current.destination_id == "home" and current.store_id == quote.store_id
    assert _business(engine, "purchase_payment")[0].data["amount_fen"] == 1700


@pytest.mark.parametrize("goods,accepted", [("平台笔记本", True), ("柠檬茶", False)])
def test_in_transit_purchase_accepts_only_goods_without_a_stay_destination(engine_factory, goods, accepted):
    engine = engine_factory(mutate=_configure)
    _, departure = act(engine, "departure", travel("公司"))
    assert departure.status == "in_progress"
    _next_decision(engine)
    before = engine.context.state.model_copy(deep=True)
    _, result = act(engine, "in-transit-purchase", purchase(goods))
    if accepted:
        assert result.status == "in_progress", result.error_code
        order = next(iter(engine.context.state.deliveries.values()))
        assert order.fulfillment == "station_pickup" and order.destination_id == "parcel-station"
        assert order.paid_at is None and engine.context.state.accounts == before.accounts
        assert engine.context.state.activities[departure.activity_id].status == "active"
    else:
        # 店铺商品要送到停留地点，人在路上时下不了单
        reason = "Place the order after arriving at a location."
        assert result.status == "rejected" and result.error_code == "ACTIVITY_PRECONDITION_UNMET"
        assert result.message == reason and result.data["unmet"] == ["delivery_destination"]
        assert result.data["items"] == [_target_item(0, [goods], "ACTIVITY_PRECONDITION_UNMET", reason)]
        _assert_rollback(engine, before)


def test_existing_courier_purchase_allows_departure_without_requoting(engine_factory):
    engine = engine_factory(mutate=_configure)
    _, bought = act(engine, "courier", purchase("平台笔记本"))
    assert bought.status == "in_progress"
    _next_decision(engine)
    before = next(iter(engine.context.state.deliveries.values())).model_copy(deep=True)
    _, departure = act(engine, "courier-departure", travel("公司"))
    assert departure.status == "in_progress", departure.error_code
    assert engine.context.state.deliveries[before.order_id] == before
    assert engine.context.state.activities[bought.activity_id].status == "active"


def test_existing_location_content_occupies_the_first_slot_but_app_content_can_depart(engine_factory):
    for request, accepted in ((do_at("家", "测试休息"), False), (use_app("哔哩哔哩"), True)):
        engine = engine_factory(mutate=_configure)
        _, started = act(engine, "content", request)
        assert started.status == "in_progress", started.error_code
        _next_decision(engine)
        before = engine.context.state.model_copy(deep=True)
        name = before.activities[started.activity_id].name
        _, departure = act(engine, "content-departure", travel("公司"))
        assert departure.status == ("in_progress" if accepted else "rejected")
        if not accepted:
            # 在地点做事与出行同占槽位 1：出发被占用者挡住；占用者是进行中的 interact，可以结束也可以暂停
            reason = f"The first activity slot is occupied by {name}."
            assert name.startswith("进行测试休息（") and before.activities[started.activity_id].resource_claims == {"first"}
            assert departure.error_code == "RESOURCE_CONFLICT" and departure.message == reason
            assert departure.data["unmet"] == ["resource_claims"]
            assert departure.data["next_step"] == f"Conclude or pause {name} first."
            assert departure.data["items"] == [_target_item(0, "公司", "RESOURCE_CONFLICT", reason)]
            _assert_rollback(engine, before)
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


def test_tool_layer_quotes_the_declared_distance_for_every_travel_method(engine_factory, tmp_path):
    """家到公司声明 12,600 米、当天小雨：工具层给出的用时和车费就是按声明值算出的结果。"""
    engine = engine_factory(_declared_world(tmp_path, ("home", "office", 12600)))
    state, graph = engine.context.state, engine.context.catalog.graph
    assert state.weather_by_date == {"2026-01-05": "light_rain"} and current_weather_factor(state) == 0.85
    assert graph.distance_meters("home", "office") == 12600 and graph.path("home", "office") == ("home", "office")
    trips = {mode: plan_trip(graph, "home", "office", mode, 0.85) for mode in ("walking", "driving", "taxi", "bus")}
    assert {mode: (trip.duration_seconds, trip.fare_fen) for mode, trip in trips.items()} == {
        "walking": (12353, 0), "driving": (2118, 2520), "taxi": (2118, 3780), "bus": (2965, 200)}
    observed, result = act(engine, "declared-drive", travel("公司", "驾车"))
    assert {method: (row["available"], row["price"], row["duration"])
            for method, row in travel_rows(observed, "公司").items()} == {
        "步行": (True, "0 元", "约 3.5 小时"), "驾车": (True, "25.2 元", "约 35 分钟"),
        "打车": (True, "37.8 元", "约 35 分钟"), "公共交通": (True, "2 元", "约 49 分钟")}
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.planned_duration_seconds == 2118 and activity.planned_end_at - activity.started_at == 2118
    assert (activity.data["distance_meters"], activity.data["path"]) == (12600, ["home", "office"])
    assert engine.context.state.accounts["agent:1"] == 200000 - 2520


@pytest.mark.parametrize("condition,method,seconds", [
    ("sunny", "驾车", 1800), ("cloudy", "驾车", 1800), ("light_rain", "驾车", 2118),
    ("heavy_rain", "驾车", 2572), ("snow", "驾车", 3000), ("sunny", "步行", 10500)])
def test_accepted_trip_over_the_declared_distance_takes_the_documented_time_in_each_weather(
        engine_factory, tmp_path, condition, method, seconds):
    """12,600 米驾车：晴和多云 30 分钟，小雨 35 分 18 秒，大雨 42 分 52 秒，雪 50 分钟；晴天步行 2 小时 55 分。"""
    seed = _seed_with_weather(condition)
    engine = engine_factory(_declared_world(tmp_path, ("home", "office", 12600)),
                            mutate=lambda persona, config: config.update(seed=seed))
    assert engine.context.state.weather_by_date == {"2026-01-05": condition}
    assert engine.context.catalog.shortest_m("home", "office") == 12600
    _, result = act(engine, f"declared-{condition}", travel("公司", method))
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
    _, result = act(engine, "declared-delivery", purchase(goods))
    assert result.status == "in_progress", result.error_code
    order = next(iter(engine.context.state.deliveries.values()))
    assert (order.store_id, order.destination_id) == (store, "office")
    assert order.expected_at - state.current_time == seconds


NOTICE = app_content("dingtalk-notice", "公告")


def _dingtalk(engine_factory, tmp_path, *contents):
    """钉钉例子的真实引擎：夹具副本加钉钉与给定内容（缺省只有打卡），运行从 10 月 12 日（周一）08:00 开始。"""
    return engine_factory(dingtalk_environment(tmp_path / "input", *contents), mutate=example_window())


@pytest.mark.parametrize("reverse", [False, True])
def test_clock_out_and_departure_in_one_round_judge_the_prerequisite_at_the_origin(engine_factory, tmp_path, reverse):
    """17:35 在办公室同一轮里打卡并出发：前提按出发前的停留地点判定，两项一起受理；打卡在路上做到 17:36:00 完成，
    不因离开而中断。"""
    engine = _dingtalk(engine_factory, tmp_path)
    stage_moment(engine, moment(12, 17, 35), "office")
    requests = [travel("家", "驾车"), use_app("钉钉")]
    observed, results = act_group(engine, "leave", *(reversed(requests) if reverse else requests))
    departure, clock = reversed(results) if reverse else results
    assert target_actions(observed, "钉钉")["interact"]["available"] is True
    assert departure.status == clock.status == "in_progress", (departure.error_code, clock.error_code)
    state = engine.context.state
    trip, activity = state.activities[departure.activity_id], state.activities[clock.activity_id]
    road = next(session for session in state.sessions.values() if session.status == "active")
    assert road.kind == "transit" and (road.origin_id, road.destination_id) == ("office", "home")
    assert set(road.activity_ids) == {trip.activity_id, activity.activity_id}
    assert (activity.data["content_id"], activity.location_id) == ("dingtalk-clock", "office")
    assert (activity.started_at, activity.planned_end_at) == (moment(12, 17, 35), moment(12, 17, 36))
    assert trip.planned_end_at > moment(12, 17, 36)
    reach(engine, moment(12, 17, 36))
    state = engine.context.state
    assert state.activities[clock.activity_id].status == "completed"
    assert state.activities[clock.activity_id].ended_at == moment(12, 17, 36)
    assert state.activities[departure.activity_id].status == "active"
    assert state.agents[1].content_progress["dingtalk-clock"].completed_rounds == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_departure_toward_the_office_with_clock_in_rejects_the_whole_group(engine_factory, tmp_path, reverse):
    """08:10 在家同一轮出发去办公室并打开钉钉：目的地不参与判断，整组被拒；没有出行活动、没有车费、会话不变。"""
    engine = _dingtalk(engine_factory, tmp_path)
    stage_moment(engine, moment(12, 8, 10), "home")
    before = engine.context.state.model_copy(deep=True)
    requests = [travel(OFFICE, "驾车"), use_app("钉钉")]
    observed, results = act_group(engine, "arrive-and-clock", *(reversed(requests) if reverse else requests))
    reason = f"Nothing in 钉钉 can start here now: it requires presence at {OFFICE}."
    assert target_actions(observed, "钉钉")["interact"]["reason"] == reason
    assert travel_rows(observed, OFFICE)["驾车"]["available"] is True
    departure, clock = reversed(results) if reverse else results
    assert departure.status == clock.status == "rejected"
    assert departure.error_code == clock.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert departure.message == clock.message == reason + NEITHER
    assert departure.data == clock.data
    assert (clock.data["next_step"], clock.data["fix"], clock.data["unmet"]) == (
        f"Go to {OFFICE}.", None, ["content_location"])
    assert clock.data["items"] == [_target_item(0 if reverse else 1, ["钉钉"], "CONTENT_PREREQUISITE_UNMET", reason)]
    _assert_rollback(engine, before)
    assert engine.context.state.activities == {} and engine.context.state.accounts["agent:1"] == 200000


def test_same_round_departure_selects_among_the_contents_eligible_at_the_origin(engine_factory, tmp_path):
    """出发的同一轮里挑内容仍用出发前的停留地点筛选：公告早上在家已经做完一轮，下班时在办公室同组出发并打开钉钉，
    合格内容是打卡和公告，没做过的打卡优先；按出发后的在路上筛，就只剩公告。"""
    engine = _dingtalk(engine_factory, tmp_path, CLOCK, NOTICE)
    stage_moment(engine, moment(12, 8, 10), "home")
    _, morning = act(engine, "notice", use_app("钉钉"))
    assert engine.context.state.activities[morning.activity_id].data["content_id"] == "dingtalk-notice"
    finish_activity(engine, morning, "notice")
    assert engine.context.state.agents[1].content_progress["dingtalk-notice"].completed_rounds == 1
    stage_moment(engine, moment(12, 17, 35), "office")
    _, (departure, clock) = act_group(engine, "leave", travel("家", "驾车"), use_app("钉钉"))
    assert departure.status == clock.status == "in_progress", (departure.error_code, clock.error_code)
    assert engine.context.state.activities[clock.activity_id].data["content_id"] == "dingtalk-clock"
