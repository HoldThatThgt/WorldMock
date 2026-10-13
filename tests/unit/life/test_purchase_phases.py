"""订单键、下单 app 的抽取、付款抽取，以及购买时长与分段的逐秒结果。"""

from dataclasses import replace
from datetime import datetime
from itertools import product
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from tests.unit.life.test_environment_commerce import environment, pending, runtime, world  # noqa: F401
from worldmock.life import basket
from worldmock.life.basket import activity_orders, build_orders, order_payment, quoted_basket
from worldmock.life.order_routing import payment_order_key, payment_orders
from worldmock.life.purchase_offers import order_app, quote_goods
from worldmock.life.purchase_phases import (
    ORDERING, PAYMENT, OrderPayment, PurchasePhase, activity_phases, foreground_events, order_payments,
    purchase_seconds, split_phases,
)
from worldmock.life.randomness import random_stream

#: 购买开始时刻；例子里的钟点都写成相对它的秒数。
START = 1_000_000


def drawn(*orders):
    """各张订单（下单 app, 支付 app, 付款秒数）按订单键升序排成付款抽取。"""
    rows = [OrderPayment(payment_order_key(app), app, payment_app, seconds) for app, payment_app, seconds in orders]
    return tuple(sorted(rows, key=lambda row: row.order_key))


def spans(phases):
    """各段写成（步骤, 前台 app, 相对开始的起秒, 止秒），便于对照规格的表。"""
    return [(phase.step, phase.app_id, phase.started_at - START, phase.ended_at - START) for phase in phases]


def catalog_with(inputs, **fields):
    """只替换目录的派生索引，其余登记对象不变。"""
    return replace(inputs, catalog=replace(inputs.catalog, **{
        key: MappingProxyType(value) for key, value in fields.items()}))


def cafe_only(inputs, *goods):
    """让这些商品远程只能由三杯咖啡供应：面馆不再卖它们，便利店和二味小炒本来就没有绑定内容 app。"""
    stores = dict(inputs.catalog.goods_by_store)
    stores["noodle-1"] = tuple(key for key in stores["noodle-1"] if key not in goods)
    return catalog_with(inputs, goods_by_store=stores)


def local_date(state):
    return datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()


def test_payment_order_key_is_the_canonical_app_list_and_the_store_order_sorts_last():
    assert payment_order_key("meituan") == '["meituan"]'
    assert payment_order_key("美团外卖") == '["美团外卖"]'
    assert payment_order_key(None) == "[null]"
    keys = [payment_order_key(app) for app in (None, "zhihu", "meituan", "Amazon", "eleme")]
    assert sorted(keys) == ['["Amazon"]', '["eleme"]', '["meituan"]', '["zhihu"]', "[null]"]


def test_two_goods_from_one_store_on_one_app_are_one_order_in_one_delivery_group(environment):
    state = world(environment)
    groups, total = quoted_basket(environment, state, 1, ("beef-noodles", "lemon-tea"), None)
    (offers,) = groups.values()
    assert {(offer.store_id, offer.app_id) for offer in offers} == {("noodle-1", "meituan")}
    assert list(payment_orders(offers)) == ['["meituan"]'] and total == 2800 + 1200 + 500
    orders = build_orders(runtime(environment).context, state, pending(), groups)
    assert [order.delivery_fee_fen for order in orders] == [500, 0]
    assert len({(order.payment_app_id, order.payment_duration_seconds) for order in orders}) == 1


def test_two_stores_on_one_app_are_one_order_with_two_delivery_groups_and_two_fees(environment):
    inputs = cafe_only(environment, "lemon-tea")
    inputs = catalog_with(inputs, apps_by_store={**inputs.catalog.apps_by_store, "cafe": ("meituan",)})
    state = world(inputs)
    groups, total = quoted_basket(inputs, state, 1, ("beef-noodles", "lemon-tea", "lemon-tea"), None)
    assert len(groups) == 2 and total == 2800 + 1200 * 2 + 500 * 2
    orders = build_orders(runtime(inputs).context, state, pending(), groups)
    assert {order.store_id for order in orders} == {"cafe", "noodle-1"}
    (key,) = payment_orders(orders)
    assert key == '["meituan"]' and len({order.group_key for order in orders}) == 2
    assert sorted(order.delivery_fee_fen for order in orders) == [0, 500, 500]
    assert len({(order.payment_app_id, order.payment_duration_seconds) for order in orders}) == 1
    assert [order.order_id for order in orders] == [f"order-buy-{number:04d}" for number in (1, 2, 3)]
    assert [order.group_key for order in orders] == sorted(order.group_key for order in orders)


@pytest.mark.parametrize("random_state", [f"order-app-{index}" for index in range(16)])
def test_ordering_app_is_drawn_once_per_store_and_local_date(environment, random_state):
    """绑了两个内容 app 的店：同一本地日的几种商品抽到同一个下单 app，是一张订单、一个配送组、一份运费。"""
    goods = ("chips", "lemon-tea", "playing-cards")
    inputs = cafe_only(environment, *goods)
    state = world(inputs, random_state=random_state)
    assert inputs.catalog.apps_by_store["cafe"] == ("eleme", "meituan")
    expected = random_stream(random_state, f"order-app:cafe:{local_date(state)}").choice(["eleme", "meituan"])
    offers = [quote_goods(inputs, state, 1, key, None) for key in goods]
    assert {(offer.store_id, offer.app_id) for offer in offers} == {("cafe", expected)}
    groups, total = quoted_basket(inputs, state, 1, goods, None)
    assert len(groups) == 1 and total == 800 + 1200 + 1500 + 500
    orders = build_orders(runtime(inputs).context, state, pending(), groups)
    assert list(payment_orders(orders)) == [payment_order_key(expected)]
    assert [order.delivery_fee_fen for order in orders] == [500, 0, 0]
    tomorrow = state.model_copy(update={"current_time": state.current_time + 86400})
    again = random_stream(random_state, f"order-app:cafe:{local_date(tomorrow)}").choice(["eleme", "meituan"])
    assert {quote_goods(inputs, tomorrow, 1, key, None).app_id for key in goods} == {again}


def test_both_apps_of_a_store_are_drawn_across_runs(environment):
    inputs = cafe_only(environment, "lemon-tea")
    apps = {quote_goods(inputs, world(inputs, random_state=f"order-app-{index}"), 1, "lemon-tea", None).app_id
            for index in range(16)}
    assert apps == {"eleme", "meituan"}


def test_order_app_returns_the_preferred_app_within_the_apps_and_otherwise_draws(environment):
    """十六个随机态下：意向在给定的 app 之内时总返回意向，不抽；不在其内或为 None 时等于按
    order-app:{source}:{local_date} 在给定的 app 里抽到的那个。店铺与快递两种来源相同。"""
    drawn_apps = set()
    for index in range(16):
        state = world(environment, random_state=f"order-app-{index}")
        for source, apps in (("cafe", ("eleme", "meituan")), ("online-notebook:courier", ("bilibili", "meituan"))):
            drawn = random_stream(state.random_state, f"order-app:{source}:{local_date(state)}").choice(sorted(apps))
            drawn_apps.add((source, drawn))
            assert [order_app(state, source, apps, prefer) for prefer in apps] == list(apps)
            assert {order_app(state, source, apps, prefer) for prefer in (None, "keep", "douyin")} == {drawn}
    assert len(drawn_apps) == 4


@pytest.mark.parametrize("random_state", [f"courier-{index}" for index in range(8)])
def test_courier_goods_keep_drawing_their_ordering_app_per_goods(environment, random_state):
    """绑定在 app 上的快递类商品仍按商品抽：键是 order-app:{goods_id}:courier:{local_date}。"""
    bound = ("bilibili", "douyin")
    notebook = environment.catalog.goods["online-notebook"].model_copy(update={"app_ids": bound})
    planner = notebook.model_copy(update={"goods_id": "online-planner", "name": "平台手账"})
    inputs = catalog_with(environment, goods={**environment.catalog.goods, "online-notebook": notebook,
                                              "online-planner": planner})
    state = world(inputs, random_state=random_state)
    for goods_id in ("online-notebook", "online-planner"):
        key = f"order-app:{goods_id}:courier:{local_date(state)}"
        offer = quote_goods(inputs, state, 1, goods_id, None)
        assert offer.app_id == random_stream(random_state, key).choice(list(bound))
        assert (offer.store_id, offer.fulfillment, offer.destination_id) == (None, "station_pickup", "parcel-station")


def test_courier_goods_bound_to_the_same_apps_draw_independently_of_each_other(environment):
    bound = ("bilibili", "douyin")
    notebook = environment.catalog.goods["online-notebook"].model_copy(update={"app_ids": bound})
    planner = notebook.model_copy(update={"goods_id": "online-planner", "name": "平台手账"})
    inputs = catalog_with(environment, goods={**environment.catalog.goods, "online-notebook": notebook,
                                              "online-planner": planner})
    pairs = set()
    for index in range(48):
        state = world(inputs, random_state=f"courier-{index}")
        pairs.add(tuple(quote_goods(inputs, state, 1, key, None).app_id for key in ("online-notebook", "online-planner")))
    assert pairs == set(product(bound, repeat=2))


class _Recorded:
    """记录每次抽取的键与参数：choice 取末尾的候选，randint 取上限。"""

    def __init__(self, key, log):
        self.key, self.log = key, log

    def choice(self, candidates):
        self.log.append((self.key, list(candidates)))
        return candidates[-1]

    def randint(self, low, high):
        self.log.append((self.key, (low, high)))
        return high


def test_payment_draw_appends_one_null_and_skips_the_duration_for_direct_payment_at_a_store(environment, monkeypatch):
    """候选是绑定了 purchase 的功能 app 按身份升序、末尾恰一个 null；到店直付时长为 0，不取付款时长的随机流。"""
    log = []
    monkeypatch.setattr(basket, "random_stream", lambda state, key: _Recorded(key, log))
    context, state = runtime(environment).context, world(environment)
    assert order_payment(context, state, "buy", None) == (None, 0)
    assert log == [("function-app:buy:[null]", ["alipay", "wechat", None])]
    log.clear()
    assert order_payment(context, state, "buy", "meituan") == (None, 20)
    assert log == [('function-app:buy:["meituan"]', ["alipay", "wechat", None]),
                   ('payment-duration:buy:["meituan"]', (10, 20))]


def test_payment_draw_is_stable_per_action_and_order_and_stays_in_range(environment):
    context, state = runtime(environment).context, world(environment)
    seen = {"store": set(), "app": set()}
    for index in range(60):
        action = f"buy-{index}"
        at_store, in_app = order_payment(context, state, action, None), order_payment(context, state, action, "meituan")
        assert at_store == order_payment(context, state, action, None)
        assert in_app == order_payment(context, state, action, "meituan")
        assert at_store[1] == 0 if at_store[0] is None else 10 <= at_store[1] <= 20
        assert 10 <= in_app[1] <= 20
        seen["store"].add(at_store[0])
        seen["app"].add(in_app[0])
    assert seen == {"store": {"alipay", "wechat", None}, "app": {"alipay", "wechat", None}}


def test_without_a_payment_app_every_order_pays_directly(environment):
    inputs = catalog_with(environment, apps_by_action={
        key: value for key, value in environment.catalog.apps_by_action.items() if key != "purchase"})
    context, state = runtime(inputs).context, world(inputs)
    for index in range(20):
        assert order_payment(context, state, f"buy-{index}", None) == (None, 0)
        app, seconds = order_payment(context, state, f"buy-{index}", "meituan")
        assert app is None and 10 <= seconds <= 20


@pytest.mark.parametrize("drawn_seconds,total,actual", [(200, 45, 200), (60, 45, 60), (60, 60, 60), (60, 80, 80),
                                                        (300, 301, 301), (61, 0, 61)])
def test_purchase_takes_the_larger_of_its_drawn_duration_and_the_payment_total(drawn_seconds, total, actual):
    payments = drawn(("hema", "alipay", total // 2), ("luckin", None, total - total // 2)) if total else ()
    assert purchase_seconds(drawn_seconds, payments) == actual


def test_one_order_orders_first_and_pays_in_the_last_seconds():
    """规格的例子：瑞幸咖啡下单，支付宝付 15 秒，购买 200 秒；抽到直付时瑞幸咖啡一直开到付款完成。"""
    assert spans(split_phases(START, 200, drawn(("luckin", "alipay", 15)))) == [
        (ORDERING, "luckin", 0, 185), (PAYMENT, "alipay", 185, 200)]
    assert spans(split_phases(START, 200, drawn(("luckin", None, 15)))) == [
        (ORDERING, "luckin", 0, 185), (PAYMENT, "luckin", 185, 200)]


@pytest.mark.parametrize("seconds,expected", [
    (200, [(ORDERING, "hema", 0, 51), (ORDERING, "luckin", 51, 102), (ORDERING, "meituan", 102, 155),
           (PAYMENT, "alipay", 155, 170), (PAYMENT, "wechat", 170, 182), (PAYMENT, "meituan", 182, 200)]),
    (60, [(ORDERING, "hema", 0, 5), (ORDERING, "luckin", 5, 10), (ORDERING, "meituan", 10, 15),
          (PAYMENT, "alipay", 15, 30), (PAYMENT, "wechat", 30, 42), (PAYMENT, "meituan", 42, 60)]),
])
def test_three_orders_share_the_ordering_time_then_pay_one_after_another(seconds, expected):
    """规格的例子：付款 15、12、18 秒合计 45 秒；200 秒时前 155 秒下单，60 秒时前 15 秒下单。"""
    payments = drawn(("meituan", None, 18), ("hema", "alipay", 15), ("luckin", "wechat", 12))
    assert [row.app_id for row in payments] == ["hema", "luckin", "meituan"]
    assert purchase_seconds(seconds, payments) == seconds
    assert spans(split_phases(START, seconds, payments)) == expected


def test_payments_that_fill_or_exceed_the_drawn_duration_leave_no_ordering_time():
    """规格的例子：三张各 20 秒、抽到 60 秒时没有下单段；再加一张 20 秒时购买持续 80 秒。"""
    three = drawn(("hema", "alipay", 20), ("luckin", "wechat", 20), ("meituan", None, 20))
    assert purchase_seconds(60, three) == 60
    assert spans(split_phases(START, 60, three)) == [
        (PAYMENT, "alipay", 0, 20), (PAYMENT, "wechat", 20, 40), (PAYMENT, "meituan", 40, 60)]
    four = drawn(("hema", "alipay", 20), ("luckin", "wechat", 20), ("meituan", None, 20), ("eleme", "alipay", 20))
    assert purchase_seconds(60, four) == 80
    assert spans(split_phases(START, 80, four)) == [
        (PAYMENT, "alipay", 0, 20), (PAYMENT, "alipay", 20, 40), (PAYMENT, "wechat", 40, 60),
        (PAYMENT, "meituan", 60, 80)]


def test_store_order_takes_no_ordering_time_and_pays_after_the_app_orders():
    """规格的例子：到店订单抽到支付宝 14 秒，美团订单直付 12 秒，购买 200 秒，下单的 174 秒全部归美团。"""
    payments = drawn((None, "alipay", 14), ("meituan", None, 12))
    assert [row.order_key for row in payments] == ['["meituan"]', "[null]"]
    assert spans(split_phases(START, 200, payments)) == [
        (ORDERING, "meituan", 0, 174), (PAYMENT, "meituan", 174, 186), (PAYMENT, "alipay", 186, 200)]


def test_only_store_orders_have_no_app_in_front_until_a_payment_app_opens():
    assert spans(split_phases(START, 100, drawn((None, "alipay", 14)))) == [(PAYMENT, "alipay", 86, 100)]
    assert split_phases(START, 100, drawn((None, None, 0))) == ()
    assert split_phases(START, 100, ()) == ()


def test_ordering_time_shorter_than_the_order_count_goes_to_the_last_order():
    payments = drawn(("hema", "alipay", 20), ("luckin", "wechat", 20), ("meituan", None, 18))
    assert spans(split_phases(START, 60, payments)) == [
        (ORDERING, "meituan", 0, 2), (PAYMENT, "alipay", 2, 22), (PAYMENT, "wechat", 22, 42),
        (PAYMENT, "meituan", 42, 60)]
    assert spans(split_phases(START, 67, drawn(("hema", "alipay", 20), ("luckin", "wechat", 20),
                                                ("meituan", None, 20))))[:3] == [
        (ORDERING, "hema", 0, 2), (ORDERING, "luckin", 2, 4), (ORDERING, "meituan", 4, 7)]


@pytest.mark.parametrize("apps", range(1, 6))
@pytest.mark.parametrize("ordering", [0, 1, 2, 3, 7, 60, 155, 290])
def test_phases_never_overlap_and_tile_the_purchase_when_an_app_order_exists(apps, ordering):
    names = [f"app-{index}" for index in range(apps)]
    payments = drawn((None, "alipay", 13), *((name, None if index % 2 else "wechat", 10 + index)
                                             for index, name in enumerate(names)))
    seconds = ordering + sum(row.seconds for row in payments)
    phases = split_phases(START, seconds, payments)
    assert all(phase.ended_at > phase.started_at for phase in phases)
    assert phases[0].started_at == START and phases[-1].ended_at == START + seconds
    assert all(later.started_at == earlier.ended_at for earlier, later in zip(phases, phases[1:]))
    placed = [phase for phase in phases if phase.step == ORDERING]
    assert sum(phase.ended_at - phase.started_at for phase in placed) == ordering
    assert [phase.app_id for phase in placed] == sorted(phase.app_id for phase in placed)
    assert all(phase.ended_at - phase.started_at == ordering // apps for phase in placed[:-1] if len(placed) == apps)
    paid = [phase for phase in phases if phase.step == PAYMENT]
    assert [phase.order_key for phase in paid] == [row.order_key for row in payments]
    assert [phase.ended_at - phase.started_at for phase in paid] == [row.seconds for row in payments]
    assert paid[-1] == PurchasePhase(PAYMENT, "[null]", "alipay", START + seconds - 13, START + seconds)


@pytest.mark.parametrize("random_state", [f"payment-{index}" for index in range(6)])
def test_accepted_purchase_freezes_the_phases_its_records_and_duration_describe(environment, random_state):
    """受理后的段只由活动的开始时刻、计划时长和记录上的付款抽取算出：到店订单加一张快递订单。"""
    state = world(environment, "store", random_state=random_state)
    changed, activity = runtime(environment).prepare_purchase(state, pending(), ("lemon-tea", "online-notebook"), None)
    orders = activity_orders(changed, activity)
    payments = order_payments(orders)
    assert [(row.order_key, row.app_id) for row in payments] == [('["bilibili"]', "bilibili"), ("[null]", None)]
    for row in payments:
        assert (row.payment_app_id, row.seconds) == order_payment(
            runtime(environment).context, state, "buy", row.app_id)
    duration = random_stream(random_state, "purchase-duration:buy").randint(60, 300)
    assert activity.planned_duration_seconds == duration == purchase_seconds(duration, payments)
    phases = activity_phases(changed, activity)
    assert phases == split_phases(state.current_time, duration, payments)
    assert phases[0] == PurchasePhase(ORDERING, '["bilibili"]', "bilibili", state.current_time,
                                      state.current_time + duration - sum(row.seconds for row in payments))
    local = next(order for order in orders if order.app_id is None)
    assert local.expected_at == state.current_time + duration == activity.planned_end_at
    assert all(order.payment_started_at is None and order.payment_ended_at is None for order in orders)


def accepted_purchase(inputs, goods, location="home", random_state="commerce-tests"):
    """受理一次购买：返回带着受理结果身份的世界与购买活动（受理结果身份由决策协调在同一次提交里写进活动）。"""
    state, activity = runtime(inputs).prepare_purchase(world(inputs, location, random_state=random_state),
                                                      pending(), goods, None)
    activity = activity.model_copy(update={"data": {**activity.data, "accepted_event_id": "accepted-result"}})
    return state.model_copy(update={"activities": {activity.activity_id: activity}}), activity


def at_moment(state, activity, now, status="active"):
    """世界走到 now：购买结算到这一刻，状态缺省仍在进行。"""
    moved = activity.model_copy(update={
        "last_settled_at": now, "elapsed_seconds": now - activity.started_at, "status": status,
        "ended_at": None if status == "active" else now})
    person = state.agents[1].model_copy(update={"body_settled_at": now})
    return state.model_copy(update={"current_time": now, "agents": {1: person},
                                    "activities": {activity.activity_id: moved}})


def written(events):
    return [(event.occurred_at, event.data["app_id"], event.data["step"], event.data["interval_start"],
             event.data["interval_end"]) for event in events]


def test_each_phase_with_an_app_in_front_writes_one_interval_the_second_it_ends(environment):
    """一次从三杯咖啡、一心面馆各买一件再加一件快递：逐边界推进，每个段在结束那一秒各写一条，不多不少。"""
    accepted, activity = accepted_purchase(environment, ("beef-noodles", "online-notebook"))
    phases = activity_phases(accepted, activity)
    assert {phase.step for phase in phases} == {ORDERING, PAYMENT} and len(phases) == 4
    assert foreground_events(accepted, accepted) == []
    before, seen = accepted, []
    for boundary in sorted({phase.ended_at for phase in phases}):
        status = "completed" if boundary == activity.planned_end_at else "active"
        after = at_moment(accepted, activity, boundary, status)
        events = foreground_events(before, after)
        seen.extend(written(events))
        assert all((event.visibility, event.source_kind, event.activity_id, event.caused_by, event.agent_id)
                   == ("system", "environment", activity.activity_id, ["accepted-result"], 1) for event in events)
        # 同一秒随后的决策轮提交不再写一遍。
        assert foreground_events(after, after) == []
        before = after
    assert seen == [(phase.ended_at, phase.app_id, phase.step, phase.started_at, phase.ended_at) for phase in phases]


def test_concluding_the_purchase_early_cuts_the_running_phase_and_keeps_the_finished_ones(environment):
    accepted, activity = accepted_purchase(environment, ("beef-noodles", "online-notebook"))
    first, second = activity_phases(accepted, activity)[:2]
    inside = at_moment(accepted, activity, second.started_at + 3)
    assert written(foreground_events(at_moment(accepted, activity, first.ended_at), inside)) == []
    stopped = at_moment(accepted, activity, second.started_at + 3, "cancelled")
    assert written(foreground_events(inside, stopped)) == [
        (second.started_at + 3, second.app_id, second.step, second.started_at, second.started_at + 3)]
    # 恰在一个段结束的那一秒结束购买：这一段已由走到这一秒的那次提交写出，下一段长度为 0，不写。
    boundary = at_moment(accepted, activity, first.ended_at)
    assert written(foreground_events(accepted, boundary)) == [
        (first.ended_at, first.app_id, first.step, first.started_at, first.ended_at)]
    assert foreground_events(boundary, at_moment(accepted, activity, first.ended_at, "cancelled")) == []
    # 受理的那一秒就结束：没有任何区间。
    assert foreground_events(accepted, at_moment(accepted, activity, activity.started_at, "cancelled")) == []


def test_run_cutoff_inside_a_phase_writes_the_cut_interval_in_the_same_commit(environment):
    accepted, activity = accepted_purchase(environment, ("beef-noodles",))
    ordering, paying = activity_phases(accepted, activity)
    cutoff = paying.started_at + 4
    truncated = at_moment(accepted, activity, cutoff, "truncated")
    assert written(foreground_events(at_moment(accepted, activity, paying.started_at), truncated)) == [
        (cutoff, paying.app_id, PAYMENT, paying.started_at, cutoff)]
    # 截止恰在下单段结束、付款段开始的那一秒：只有走完的下单段。
    edge = at_moment(accepted, activity, ordering.ended_at, "truncated")
    assert written(foreground_events(accepted, edge)) == [
        (ordering.ended_at, ordering.app_id, ORDERING, ordering.started_at, ordering.ended_at)]


def test_purchase_that_fails_for_funds_keeps_every_interval_because_all_phases_have_run(environment):
    accepted, activity = accepted_purchase(environment, ("beef-noodles",))
    ordering, paying = activity_phases(accepted, activity)
    failed = at_moment(accepted, activity, activity.planned_end_at, "failed")
    assert written(foreground_events(at_moment(accepted, activity, paying.started_at), failed)) == [
        (paying.ended_at, paying.app_id, PAYMENT, paying.started_at, paying.ended_at)]


@pytest.mark.parametrize("direct", [True, False])
def test_store_purchase_has_no_ordering_interval_and_no_interval_at_all_when_paid_directly(environment, direct):
    seeds = (f"payment-{index}" for index in range(64))
    accepted, activity = next(
        (state, item) for state, item in (accepted_purchase(environment, ("tissue",), "store", seed) for seed in seeds)
        if (activity_orders(state, item)[0].payment_app_id is None) is direct)
    (order,) = activity_orders(accepted, activity)
    done = at_moment(accepted, activity, activity.planned_end_at, "completed")
    events = written(foreground_events(accepted, done))
    if direct:
        assert order.payment_duration_seconds == 0 and events == []
        return
    started = activity.planned_end_at - order.payment_duration_seconds
    assert events == [(activity.planned_end_at, order.payment_app_id, PAYMENT, started, activity.planned_end_at)]


def test_foreground_events_ignore_everything_that_is_not_a_running_purchase(environment):
    accepted, activity = accepted_purchase(environment, ("beef-noodles",))
    done = at_moment(accepted, activity, activity.planned_end_at, "completed")
    # 已经结束的购买在之后的提交里不再写任何区间。
    later = done.model_copy(update={"current_time": activity.planned_end_at + 60})
    assert foreground_events(done, later) == []
