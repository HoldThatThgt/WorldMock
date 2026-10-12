"""购买时写想用的 app：经真实请求、引擎与 SQLite 验证 prefer 怎样认、怎样改变外卖选店与下单 app、什么时候不看它，
以及购买结果里的 channel 与工具层的 prefer_apps（docs/design/04-simulation-engine.md「购买、付款与收货」）。

夹具环境 2026 年 1 月 5 日 08:00 人在家：一心面馆（2,524 米）只登记了美团，三杯咖啡（4,729 米）登记了饿了么和美团，
四季便利与二味小炒没有登记 app。
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest

from tests.footprints.recording import artifact_rows, export_completed
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, environment, person
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import (SIDE_TOOL, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, purchase, stage_day_plan, tool_targets, travel)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from tests.life.phone_records import foreground, notices, phone_events
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.life.engine import LifeEngine
from worldmock.life.models import StartSideActivityRequest
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.randomness import random_stream
from worldmock.life.tool_groups import compact_tool_layer

ZONE = ZoneInfo("Asia/Shanghai")
FIRST_DAY = "2026-01-05"
#: 两家能外卖的店送到家的预计送达：一心面馆 600 秒加 2,524 米除以每秒 5 米，三杯咖啡 600 秒加 4,729 米除以每秒 5 米。
NEAR = int(datetime(2026, 1, 5, 8, 18, 25, tzinfo=ZONE).timestamp())
FAR = int(datetime(2026, 1, 5, 8, 25, 46, tzinfo=ZONE).timestamp())
APP_NAMES = {"bilibili": "哔哩哔哩", "eleme": "饿了么", "meituan": "美团"}
NEITHER = " Neither start request was started."
PURCHASE_KEYS = {"fact_kind", "agent_id", "order_id", "goods_id", "category", "store_id", "app_id", "payment_app_id",
                 "unit_price_fen", "delivery_fee_fen", "quantity", "total_price_fen", "fulfillment", "created_at",
                 "action_id", "source_event_id"}


def _orders(engine, result) -> list:
    """一条购买动作创建的商品履约记录，按 order_id 升序。"""
    activity = engine.context.state.activities[result.activity_id]
    return [engine.context.state.deliveries[key] for key in sorted(activity.data["order_ids"])]


def _routes(engine, result) -> list[tuple]:
    """各条记录的路线：（商品，店铺，下单 app，预计送达）。"""
    return [(order.goods_id, order.store_id, order.app_id, order.expected_at) for order in _orders(engine, result)]


def _bought(engine, label, *goods, prefer=None):
    """当前时刻提交一次购买，断言受理并返回结果。"""
    _, result = act(engine, label, purchase(*goods, prefer=prefer))
    assert result.status == "in_progress", (result.error_code, result.message)
    return result


def _side_record(engine, label) -> dict:
    """此刻观察里第一条副工具请求的进度层记录。"""
    return progress_calls(engine.observe(context(engine, label)), SIDE_TOOL)[0]


def _edited(tmp_path, name, change, *files):
    """夹具副本：给定目录文件的每一行交给 change 改写（就地修改），改完按生产入口刷新清单。"""
    root = copy_environment(tmp_path / name)
    for relative in files:
        rows = catalog_rows(root, relative)
        for row in rows:
            change(relative, row)
        write_catalog_rows(root, relative, rows)
    return root


def _closed(*location_ids):
    """改写函数：给定地点只在周二营业，1 月 5 日周一整天打烊。"""
    def change(relative, row):
        if row.get("location_id") in location_ids:
            row["opens"] = {"weekly": {"tue": [["00:00", "24:00"]]}}
    return change


def _declared(tmp_path, cafe_m, noodle_m):
    """夹具副本加 map.jsonl：声明家到三杯咖啡、家到一心面馆的距离。"""
    root = copy_environment(tmp_path / f"declared-{cafe_m}-{noodle_m}")
    write_catalog_rows(root, "map.jsonl", [{"a": "cafe", "b": "home", "length_m": cafe_m},
                                           {"a": "home", "b": "noodle-1", "length_m": noodle_m}])
    return root


def _at_store(persona, config):
    """人物从四季便利开始。"""
    persona["initial_location"] = "四季便利"


def _attentive(persona, config):
    """每秒都到播报间隔：人物刚出发就有决策机会。"""
    config["progress_report_interval_seconds"] = 1


def _next_decision(engine):
    """推进到人物下一次有决策机会。"""
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)


def _on_the_road(engine):
    """从家出发去公司，推进到路上的下一次决策机会，返回出行的受理结果。"""
    _, departure = act(engine, "departure", travel("公司"))
    assert departure.status == "in_progress"
    _next_decision(engine)
    return departure


def _host(engine):
    """真实宿主：准备好本轮观察的 LifeAgent，用它的严格校验判断请求形状。"""
    stage_day_plan(engine)
    decision = context(engine, "host")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    agent = person(environment(engine), decision, ScriptedModel([]))
    agent.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    return agent


@pytest.mark.parametrize("arguments,why", [
    ({"action": "purchase", "target": ["柠檬茶"]}, "This required field was omitted."),
    ({"action": "interact", "target": ["哔哩哔哩"], "prefer": "饿了么"}, "prefer must be null when action is interact"),
])
def test_host_and_environment_refuse_the_same_prefer_shapes(engine_factory, arguments, why):
    """缺 prefer 与打开 app 带 prefer：宿主报 ARGUMENTS_INVALID，绕过宿主直接登记得 ACTION_REQUEST_INVALID；
    明细都定位到 prefer，原因相同；什么都没有开始，时钟没有动。"""
    error = _host(engine_factory()).validate_react_decisions([ReactDecision("", SIDE_TOOL, arguments, "")], False)
    [item] = error.report.items
    assert error.report.error_code == "ARGUMENTS_INVALID" and item.why == why
    assert list(item.path[-2:]) == ["arguments", "prefer"] and error.report.reason == why
    engine = engine_factory()
    before = engine.context.state.current_time
    _, result = act(engine, "bypass", StartSideActivityRequest.model_construct(**arguments))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    [written] = result.data["items"]
    assert written["path"] == ["tool_calls", 0, "function", "arguments", "prefer"] and written["why"] == why
    assert result.data["reason"] == error.report.reason
    assert engine.context.state.current_time == before
    assert not engine.context.state.activities and not engine.context.state.deliveries


@pytest.mark.parametrize("prefer", ["饿了么", "美团外卖", "随便写的名字"])
def test_any_non_blank_prefer_the_host_lets_through_is_accepted(engine_factory, prefer):
    """宿主放行的任何非空 prefer，认得出认不出，环境都受理。"""
    arguments = {"action": "purchase", "target": ["柠檬茶"], "prefer": prefer}
    assert _host(engine_factory()).validate_react_decisions([ReactDecision("", SIDE_TOOL, arguments, "")], False) == ""
    _bought(engine_factory(), "tea", "柠檬茶", prefer=prefer)


@pytest.mark.parametrize("written", ["美团外卖", "支付宝", "抖音", "keep", "不存在的 app"])
def test_prefer_that_names_no_order_app_is_silently_ignored(engine_factory, written):
    """写成简称、功能 app、只有内容的 app、大小写不同或不存在的名称：照样受理，记录与没有意向时逐字段相同；
    不退回、不给候选、不记规整，结果里只有 channel 说明实际走了美团，所写原文留在 arguments 里。"""
    plain = engine_factory()
    expected = [order.model_dump() for order in _orders(plain, _bought(plain, "tea", "柠檬茶"))]
    engine = engine_factory()
    result = _bought(engine, "tea", "柠檬茶", prefer=written)
    assert [order.model_dump() for order in _orders(engine, result)] == expected
    assert not {"items", "resolved", "notes"} & set(result.data)
    assert [row for row in events(engine, "action_result") if row.data["status"] == "rejected"] == []
    record = _side_record(engine, "after")
    assert record["arguments"] == {"action": "purchase", "target": ["柠檬茶"], "prefer": written}
    assert [row["channel"] for row in record["result"]["details"]["purchases"]] == ["美团"]
    assert not {"items", "resolved", "notes"} & set(record["result"]["details"])


def test_normalised_prefer_counts_without_a_resolution_record(engine_factory):
    """「饿 了 么」规整后对上饿了么：与写「饿了么」得到同一条记录，受理结果的 details 里不记规整；商品名照旧记规整，
    resolved 里只有商品名那一条。"""
    routes = []
    for written in ("饿了么", "饿 了 么"):
        engine = engine_factory()
        result = _bought(engine, "tea", "柠檬茶", prefer=written)
        assert "resolved" not in result.data
        assert "resolved" not in _side_record(engine, "during")["result"]["details"]
        routes.append(_routes(engine, result))
    assert routes == [[("lemon-tea", "cafe", "eleme", FAR)]] * 2
    engine = engine_factory()
    result = _bought(engine, "spaced", "柠 檬 茶", prefer="饿 了 么")
    assert result.data["resolved"] == [{"written": "柠 檬 茶", "as": "柠檬茶"}]
    details = _side_record(engine, "during")["result"]["details"]
    assert details["resolved"] == [{"written": "柠 檬 茶", "as": "柠檬茶"}]
    assert _routes(engine, result) == [("lemon-tea", "cafe", "eleme", FAR)]


def test_usable_prefer_may_move_the_order_to_a_farther_store(engine_factory):
    """没有意向时从最近的一心面馆经美团送；写饿了么时只在登记了饿了么的三杯咖啡里选，更远、更晚到。"""
    catalog = engine_factory().context.catalog
    assert (catalog.shortest_m("home", "noodle-1"), catalog.shortest_m("home", "cafe")) == (2524, 4729)
    for prefer, expected in ((None, ("noodle-1", "meituan", 2524, NEAR)), ("饿了么", ("cafe", "eleme", 4729, FAR))):
        engine = engine_factory()
        (order,) = _orders(engine, _bought(engine, "tea", "柠檬茶", prefer=prefer))
        assert (order.store_id, order.app_id, order.distance_meters, order.expected_at) == expected
        assert (order.fulfillment, order.destination_id, order.total_price_fen) == ("delivery", "home", 1700)


@pytest.mark.parametrize("cafe_m,noodle_m,store", [(None, None, "noodle-1"), (2000, 3000, "cafe"),
                                                   (3000, 3000, "cafe")])
def test_preferred_app_store_is_chosen_by_todays_rule(engine_factory, tmp_path, cafe_m, noodle_m, store):
    """写美团：两家登记了美团的店里照今天的规则选，最近的一家；声明距离调换远近后选另一家；同距取较小的店铺身份。"""
    root = None if cafe_m is None else _declared(tmp_path, cafe_m, noodle_m)
    engine = engine_factory(root)
    if cafe_m is not None:
        catalog = engine.context.catalog
        assert (catalog.shortest_m("home", "cafe"), catalog.shortest_m("home", "noodle-1")) == (cafe_m, noodle_m)
    (order,) = _orders(engine, _bought(engine, "tea", "柠檬茶", prefer="美团"))
    assert (order.store_id, order.app_id) == (store, "meituan")


def test_store_chosen_through_the_preferred_app_is_not_drawn_again(engine_factory, tmp_path):
    """只有三杯咖啡能外卖：写当天没抽到的那个 app，记录上就是它；不写时是当天抽到的。"""
    def change(relative, row):
        if row["location_id"] == "noodle-1":
            row["app_ids"] = []

    root = _edited(tmp_path, "cafe-only", change, "location.jsonl")
    state = engine_factory(root).context.state
    drawn = random_stream(state.random_state, f"order-app:cafe:{FIRST_DAY}").choice(["eleme", "meituan"])
    (other,) = {"eleme", "meituan"} - {drawn}
    for prefer, expected in ((None, drawn), (APP_NAMES[other], other), (APP_NAMES[drawn], drawn)):
        engine = engine_factory(root)
        assert _routes(engine, _bought(engine, "tea", "柠檬茶", prefer=prefer)) == [
            ("lemon-tea", "cafe", expected, FAR)]


def test_preferred_app_store_must_be_open_now(engine_factory, tmp_path):
    """三杯咖啡此刻打烊：饿了么用不上，另选一心面馆，经美团下单。"""
    root = _edited(tmp_path, "cafe-closed", _closed("cafe"), "location.jsonl")
    engine = engine_factory(root)
    assert _routes(engine, _bought(engine, "tea", "柠檬茶", prefer="饿了么")) == [
        ("lemon-tea", "noodle-1", "meituan", NEAR)]


def test_prefer_neither_creates_nor_removes_refusals(engine_factory, tmp_path):
    """带着用得上的饿了么：人在路上买店铺商品照旧被拒；两家能外卖的店都打烊时照旧点出最近一家有售的地点。"""
    engine = engine_factory(mutate=_attentive)
    _on_the_road(engine)
    _, on_road = act(engine, "on-road", purchase("柠檬茶", prefer="饿了么"))
    assert (on_road.status, on_road.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert on_road.message == "Place the order after arriving at a location."
    root = _edited(tmp_path, "both-closed", _closed("cafe", "noodle-1"), "location.jsonl")
    closed = engine_factory(root)
    _, result = act(closed, "closed", purchase("柠檬茶", prefer="饿了么"))
    assert (result.status, result.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert result.message == "Purchase requires presence at 四季便利 while it is open."
    assert not closed.context.state.deliveries and not closed.context.state.activities


def test_buyer_standing_in_a_selling_store_buys_there_whatever_prefer(engine_factory):
    """人在四季便利买柠檬茶、写饿了么：到店购买，没有下单 app，没有下单段，没有「订单已下单」，channel 是「到店」。"""
    engine = engine_factory(mutate=_at_store)
    result = _bought(engine, "local", "柠檬茶", prefer="饿了么")
    (order,) = _orders(engine, result)
    assert (order.fulfillment, order.app_id, order.store_id, order.delivery_fee_fen) == ("pickup", None, "store", 0)
    assert [row["channel"] for row in _side_record(engine, "during")["result"]["details"]["purchases"]] == ["到店"]
    finish_activity(engine, result, "local")
    assert [row for row in foreground(engine, result.activity_id) if row[1] == "ordering"] == []
    assert [row for row in notices(engine) if row[1] == "订单已下单"] == []
    assert engine.context.state.deliveries[order.order_id].status == "delivered"
    assert [row["channel"] for row in _side_record(engine, "after")["result"]["details"]["purchases"]] == ["到店"]


def test_closed_store_where_the_buyer_stands_refuses_as_today(engine_factory, tmp_path):
    """四季便利此刻打烊、三杯咖啡营业，人在四季便利写饿了么：照今天的规则以 LOCATION_CLOSED 拒绝，不改成外卖。"""
    root = _edited(tmp_path, "store-closed", _closed("store"), "location.jsonl")
    engine = engine_factory(root, mutate=_at_store)
    _, result = act(engine, "closed", purchase("柠檬茶", prefer="饿了么"))
    assert (result.status, result.error_code) == ("rejected", "LOCATION_CLOSED")
    assert result.message == "The location is closed at the current time."
    assert not engine.context.state.deliveries and not engine.context.state.activities


def _courier(state, app_id) -> tuple[int, int]:
    """经给定 app 下单的平台笔记本：按这个 app 的两个键算出的（预计到站，运费）。"""
    days = random_stream(state.random_state, f"courier-days:online-notebook:{app_id}:{FIRST_DAY}").choice((2, 3, 4, 5))
    arrival = datetime.combine(datetime(2026, 1, 5).date() + timedelta(days=days), time.min, ZONE)
    fee = random_stream(state.random_state, f"courier-fee:online-notebook:{app_id}").choice((0, 500))
    return int(arrival.timestamp()), fee


def test_courier_goods_use_the_preferred_app_when_registered_on_them(engine_factory, tmp_path):
    """平台笔记本登记哔哩哔哩和美团：写当天没抽到的那个，记录上就是它，到站和运费按它的键取；写不在其内的饿了么或
    null 时与按商品和日期抽的相同；人在路上三种写法同样成立。购买进行中和做完后，结果的 channel 都是记录上下单 app
    的完整名称。"""
    def change(relative, row):
        if row["goods_id"] == "online-notebook":
            row["app_ids"] = ["bilibili", "meituan"]

    root = _edited(tmp_path, "two-apps", change, "merchant-catalog.jsonl")
    state = engine_factory(root).context.state
    drawn = random_stream(state.random_state, f"order-app:online-notebook:courier:{FIRST_DAY}").choice(
        ["bilibili", "meituan"])
    (other,) = {"bilibili", "meituan"} - {drawn}
    for prefer, expected in ((APP_NAMES[other], other), ("饿了么", drawn), (None, drawn)):
        route = (expected, "station_pickup", *_courier(state, expected))
        engine = engine_factory(root)
        result = _bought(engine, "book", "平台笔记本", prefer=prefer)
        (order,) = _orders(engine, result)
        assert (order.app_id, order.fulfillment, order.expected_at, order.delivery_fee_fen) == route
        assert _channels(engine, "during") == [("平台笔记本", APP_NAMES[expected], False)]
        finish_activity(engine, result, "book")
        assert _channels(engine, "after") == [("平台笔记本", APP_NAMES[expected], True)]
        road = engine_factory(root, mutate=_attentive)
        departure = _on_the_road(road)
        (order,) = _orders(road, _bought(road, "on-road", "平台笔记本", prefer=prefer))
        assert (order.app_id, order.fulfillment, order.expected_at, order.delivery_fee_fen) == route
        assert road.context.state.activities[departure.activity_id].status == "active"


def test_each_goods_routes_on_its_own_under_one_prefer(engine_factory):
    """牛肉面和柠檬茶写饿了么：柠檬茶经饿了么从三杯咖啡来，牛肉面只有一心面馆能送、另选美团；两张订单、两份运费、
    两条付款、两条「订单已下单」、两趟到达。不写时一张订单一份运费；柠檬茶写两次时同一条路线、一份运费。"""
    engine = engine_factory()
    result = _bought(engine, "two", "牛肉面", "柠檬茶", prefer="饿了么")
    orders = _orders(engine, result)
    assert sorted(_routes(engine, result)) == [("beef-noodles", "noodle-1", "meituan", NEAR),
                                               ("lemon-tea", "cafe", "eleme", FAR)]
    assert len({order.group_key for order in orders}) == 2 and sum(order.total_price_fen for order in orders) == 5000
    finish_activity(engine, result, "two")
    assert sorted(row.data["amount_fen"] for row in phone_events(engine, "purchase_payment")) == [1700, 3300]
    assert sorted(row[2] for row in notices(engine) if row[1] == "订单已下单") == ["eleme", "meituan"]
    reach(engine, FAR)
    assert sorted(row.occurred_at for row in phone_events(engine, "delivery_arrival")) == [NEAR, FAR]
    plain = engine_factory()
    single = _orders(plain, _bought(plain, "two", "牛肉面", "柠檬茶"))
    assert {(order.store_id, order.app_id, order.group_key) for order in single} == {
        ("noodle-1", "meituan", single[0].group_key)}
    assert sum(order.total_price_fen for order in single) == 4500
    twice = engine_factory()
    result = _bought(twice, "twice", "柠檬茶", "柠檬茶", prefer="饿了么")
    orders = _orders(twice, result)
    assert _routes(twice, result) == [("lemon-tea", "cafe", "eleme", FAR)] * 2
    assert len({order.group_key for order in orders}) == 1 and sum(order.total_price_fen for order in orders) == 2900


def _one_hour(persona, config):
    """运行一小时，导出时足够两趟外卖都送到。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def test_one_store_keeps_one_ordering_app_within_a_purchase(engine_factory, tmp_path):
    """薯片、柠檬茶、牛肉面写饿了么：薯片与柠檬茶同在（三杯咖啡，饿了么）一个配送组、一份运费，牛肉面在（一心面馆，
    美团）；每家店只对应一个下单 app；导出认证通过。"""
    engine = engine_factory(mutate=_one_hour)
    result = _bought(engine, "three", "薯片", "柠檬茶", "牛肉面", prefer="饿了么")
    orders = _orders(engine, result)
    assert sorted((order.goods_id, order.store_id, order.app_id) for order in orders) == [
        ("beef-noodles", "noodle-1", "meituan"), ("chips", "cafe", "eleme"), ("lemon-tea", "cafe", "eleme")]
    cafe = [order for order in orders if order.store_id == "cafe"]
    assert len({order.group_key for order in cafe}) == 1 and sum(order.delivery_fee_fen for order in cafe) == 500
    apps = {}
    for order in orders:
        apps.setdefault(order.store_id, set()).add(order.app_id)
    assert all(len(found) == 1 for found in apps.values())
    export_completed(engine, tmp_path / "export")
    bought = [row for row in artifact_rows(tmp_path / "export", "purchases") if row["fact_kind"] == "purchase"]
    assert sorted((row["store_id"], row["app_id"]) for row in bought) == [
        ("cafe", "eleme"), ("cafe", "eleme"), ("noodle-1", "meituan")]


def test_balance_is_checked_against_the_preferred_routes(engine_factory):
    """余额 47 元买牛肉面和柠檬茶：不写意向一份运费 45 元受理；写饿了么多出一份运费共 50 元，整组以余额不足拒绝，
    不为余额放弃意向，一件也不买。"""
    def poor(persona, config):
        persona["initial_balance_fen"] = 4700

    _bought(engine_factory(mutate=poor), "plain", "牛肉面", "柠檬茶")
    engine = engine_factory(mutate=poor)
    _, result = act(engine, "split", purchase("牛肉面", "柠檬茶", prefer="饿了么"))
    assert (result.status, result.error_code) == ("rejected", "INSUFFICIENT_FUNDS")
    assert result.message == "The caller's available balance is below the actual purchase total."
    assert not engine.context.state.deliveries and not engine.context.state.activities
    assert engine.context.state.accounts["agent:1"] == 4700


def test_purchase_in_the_same_reply_as_departure(engine_factory):
    """在家同一次回复里出发并经饿了么买柠檬茶：受理，路线按出发前的家算；人在四季便利同样提交时，到店购买不能随出发开始。"""
    engine = engine_factory()
    _, (trip, bought) = act_group(engine, "leave", travel("公司"), purchase("柠檬茶", prefer="饿了么"))
    assert (trip.status, bought.status) == ("in_progress", "in_progress"), bought.error_code
    (order,) = _orders(engine, bought)
    assert (order.store_id, order.app_id, order.destination_id, order.expected_at) == ("cafe", "eleme", "home", FAR)
    store = engine_factory(mutate=_at_store)
    _, (trip, local) = act_group(store, "leave", travel("公司"), purchase("柠檬茶", prefer="饿了么"))
    assert trip.error_code == local.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert local.data["reason"] == "A local purchase cannot start with departure." + NEITHER
    assert local.data["next_step"] == "Complete or conclude the local purchase before leaving."


def _channels(engine, label) -> list[tuple]:
    """此刻观察里第一条副工具请求的购买明细：（商品，channel，付没付款），每行的键次序先断言；整层通过层校验。"""
    observed = engine.observe(context(engine, label))
    rows = progress_calls(observed, SIDE_TOOL)[0]["result"]["details"]["purchases"]
    for row in rows:
        assert list(row)[:5] == ["product", "quantity", "channel", "destination", "expected_at"]
        assert list(row)[5:] in (["paid"], ["price"])
    validate_layer(observed["progress_layer"])
    return [(row["product"], row["channel"], "paid" in row) for row in rows]


def test_purchase_result_names_the_actual_channel_in_both_results(engine_factory):
    """购买进行中和做完后各观察一次：每行都带 channel，是记录上下单 app 的完整名称或「到店」；分成两张订单时各行
    各写各的；提前结束的购买 channel 仍在、配的是 price；每次的进度层都通过层校验。"""
    engine = engine_factory()
    result = _bought(engine, "split", "牛肉面", "柠檬茶", prefer="饿了么")
    assert _channels(engine, "during") == [("柠檬茶", "饿了么", False), ("牛肉面", "美团", False)]
    finish_activity(engine, result, "split")
    assert _channels(engine, "after") == [("柠檬茶", "饿了么", True), ("牛肉面", "美团", True)]
    local = engine_factory(mutate=_at_store)
    result = _bought(local, "local", "纸巾", prefer="美团")
    assert _channels(local, "during") == [("纸巾", "到店", False)]
    finish_activity(local, result, "local")
    assert _channels(local, "after") == [("纸巾", "到店", True)]
    stopped = engine_factory(mutate=_attentive)
    result = _bought(stopped, "stopped", "柠檬茶", prefer="饿了么")
    assert advance_until_ready(stopped)
    name = stopped.context.state.activities[result.activity_id].name
    _, ended = act(stopped, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    assert stopped.context.state.activities[result.activity_id].status == "cancelled"
    assert _channels(stopped, "after") == [("柠檬茶", "饿了么", False)]


def test_tool_layer_lists_every_order_app_whatever_the_moment_or_place(engine_factory, tmp_path):
    """prefer_apps 是全部下单 app 的完整名称，按 app_id 升序；换时刻、换地点、三杯咖啡打烊后都相同；分组渲染后还在；
    整层通过层校验。没有任何下单 app 的环境里它是空数组。"""
    def opens_until_nine(relative, row):
        if row["location_id"] == "cafe":
            row["opens"] = {"weekly": {"mon": [["07:00", "09:00"]]}}

    engine = engine_factory(_edited(tmp_path, "cafe-morning", opens_until_nine, "location.jsonl"))
    expected = ["哔哩哔哩", "饿了么", "美团"]
    layers = [engine.observe(context(engine, "home"))["tool_layer"]]
    _, trip = act(engine, "away", travel("公司", "驾车"))
    finish_activity(engine, trip, "away")
    assert engine.context.state.agents[1].location_id == "office"
    layers.append(engine.observe(context(engine, "office"))["tool_layer"])
    later = datetime(2026, 1, 5, 9, 30, tzinfo=ZONE)
    stage_moment(engine, int(later.timestamp()))
    assert not engine.context.catalog.locations["cafe"].is_open(later)
    layers.append(engine.observe(context(engine, "closed"))["tool_layer"])
    for layer in layers:
        assert layer["callable_index"]["prefer_apps"] == expected
        validate_layer(layer)
        assert compact_tool_layer(layer)["callable_index"]["prefer_apps"] == expected
    empty = engine_factory(_without_order_apps(tmp_path))
    layer = empty.observe(context(empty, "none"))["tool_layer"]
    assert layer["callable_index"]["prefer_apps"] == []
    validate_layer(layer)


def _without_order_apps(tmp_path):
    """夹具副本：没有任何店登记 app，平台笔记本下架，只用于下单的饿了么、美团随之去掉。"""
    root = copy_environment(tmp_path / "no-order-apps")
    write_catalog_rows(root, "app-catalog.jsonl", [row for row in catalog_rows(root, "app-catalog.jsonl")
                                                   if row["app_id"] not in {"eleme", "meituan"}])
    write_catalog_rows(root, "merchant-catalog.jsonl", [row for row in catalog_rows(root, "merchant-catalog.jsonl")
                                                        if row["goods_id"] != "online-notebook"])
    write_catalog_rows(root, "location.jsonl", [{**row, "app_ids": []}
                                                for row in catalog_rows(root, "location.jsonl")])
    return root


def test_goods_row_previews_the_route_without_prefer(engine_factory):
    """人在家时柠檬茶一行按没有意向的路线写运费和预计到达；紧接着写饿了么购买，实际送达更晚，以购买结果为准。"""
    engine = engine_factory()
    observed, result = act(engine, "tea", purchase("柠檬茶", prefer="饿了么"))
    row = next(item for item in tool_targets(observed)["柠檬茶"]["actions"] if item["action"] == "purchase")
    assert row["note"] == "运费5 元，预计01月05日 08:18到达"
    assert result.status == "in_progress" and _routes(engine, result) == [("lemon-tea", "cafe", "eleme", FAR)]


def test_copied_candidate_is_accepted_as_written(engine_factory):
    """购买写了哔哩哔哩、prefer 写饿了么：退回的候选恰是打开哔哩哔哩、prefer 为 null；原样再提交就开始使用哔哩哔哩。"""
    engine = engine_factory()
    _, refused = act(engine, "wrong", purchase("哔哩哔哩", prefer="饿了么"))
    assert (refused.status, refused.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    [item] = refused.data["items"]
    assert item["error_code"] == "ACTION_NOT_OFFERED"
    [candidate] = item["candidates"]
    assert candidate["tool"] == SIDE_TOOL
    assert candidate["arguments"] == {"action": "interact", "target": ["哔哩哔哩"], "prefer": None}
    _, started = act(engine, "copied", StartSideActivityRequest.model_validate(candidate["arguments"]))
    assert started.status == "in_progress", started.error_code
    activity = engine.context.state.activities[started.activity_id]
    assert (activity.kind, activity.data["app_id"]) == ("interact", "bilibili")


def test_accepted_route_is_frozen_through_a_checkpoint_and_reopened_run(engine_factory, tmp_path):
    """经饿了么的购买进行到一半生成检查点并重开：记录上的店、下单 app、预计送达不变，做完后付款与通知照旧。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result = _bought(engine, "tea", "柠檬茶", prefer="饿了么")
    assert _routes(engine, result) == [("lemon-tea", "cafe", "eleme", FAR)]
    advance_next(engine)
    assert engine.context.state.activities[result.activity_id].status == "active"
    before = _orders(engine, result)
    workspace = tmp_path / "agent"
    write_agent_workspace(workspace)
    point = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    restore_branch(engine.context.store, point.checkpoint_id, tmp_path / "branch").close()
    child = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        assert _orders(child, result) == before
        assert _routes(child, result) == [("lemon-tea", "cafe", "eleme", FAR)]
        finish_activity(child, result, "tea")
        assert [row.data["amount_fen"] for row in phone_events(child, "purchase_payment")] == [1700]
        assert [(row[1], row[2]) for row in notices(child)][:1] == [("订单已下单", "eleme")]
        reach(child, FAR)
        assert [row.occurred_at for row in phone_events(child, "delivery_arrival")] == [FAR]
    finally:
        child.close()


def test_settled_purchase_sentence_keeps_its_wording_without_the_channel(engine_factory):
    """经饿了么买完并离开这段停留后，记忆层里那一句仍是今天的写法，不带下单 app。"""
    engine = engine_factory()
    result = _bought(engine, "tea", "柠檬茶", prefer="饿了么")
    finish_activity(engine, result, "tea")
    (order,) = _orders(engine, result)
    _, trip = act(engine, "away", travel("公司"))
    assert trip.status == "in_progress", trip.error_code
    _next_decision(engine)
    memory = json.dumps(engine.observe(context(engine, "road"))["memory_layer"], ensure_ascii=False)
    paid = datetime.fromtimestamp(order.paid_at, ZONE)
    assert f"{paid:%H:%M} 买了柠檬茶 1 份，付 17 元，预计 08:25 送到家" in memory
    assert "饿了么" not in memory and "eleme" not in memory
