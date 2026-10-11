"""一次购买按下单的内容 app 分订单：付款抽取、购买时长与分段、逐张结算，配送和运费仍按店铺。"""

from datetime import timedelta

import pytest

from tests.life.conftest import advance_until_ready, conclude_request, purchase
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, reach
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life import time_advance
from worldmock.life.order_routing import payment_order_key, payment_orders
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.purchase_phases import ORDERING, PAYMENT, activity_phases, advance_phases, phase_boundaries
from worldmock.life.randomness import random_stream

#: 夹具运行的第一个本地日。
FIRST_DAY = "2026-01-05"


def at_store(persona, config):
    persona["initial_location"] = "四季便利"


def business_events(engine, kind):
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def records(engine, result):
    """一条购买动作创建的商品履约记录，按 order_id 字符串升序。"""
    activity = engine.context.state.activities[result.activity_id]
    return [engine.context.state.deliveries[key] for key in sorted(activity.data["order_ids"])]


def accepted_event(engine, result):
    return next(row for row in events(engine, "action_result") if row.action_id == result.action_id)


def scheduled_arrivals(engine):
    return [row for row in engine.context.state.pending_events if row.kind == "delivery_arrival"]


def drawn_duration(engine, result):
    """这条购买动作抽到的购买时长，键见配置「唯一种子与随机键」。"""
    return random_stream(engine.context.state.random_state, f"purchase-duration:{result.action_id}").randint(60, 300)


def edited_rows(root, name, identity, change):
    """改写夹具副本里一份目录文件的若干行并刷新清单。"""
    rows = catalog_rows(root, name)
    for row in rows:
        if row[identity] in change:
            row.update(change[row[identity]])
    write_catalog_rows(root, name, rows)


def without_payment_apps(tmp_path):
    """夹具副本：不登记任何绑定 purchase 的功能 app，每张订单必然直付。"""
    root = copy_environment(tmp_path / "direct")
    rows = [row for row in catalog_rows(root, "app-catalog.jsonl") if row.get("actions") != ["purchase"]]
    write_catalog_rows(root, "app-catalog.jsonl", rows)
    return root


def with_many_shops(tmp_path, count=12):
    """夹具副本：多登记 count 个内容 app，各绑定一件快递类商品；一次全买就是 count 张订单。"""
    root = copy_environment(tmp_path / "shops")
    apps, goods = catalog_rows(root, "app-catalog.jsonl"), catalog_rows(root, "merchant-catalog.jsonl")
    for number in range(1, count + 1):
        apps.append({"app_id": f"shop-{number:02d}", "browse_duration_seconds": 600,
                     "name": f"测试商城{number:02d}", "role": "content"})
        goods.append({"app_ids": [f"shop-{number:02d}"], "category": "goods", "goods_id": f"shop-item-{number:02d}",
                      "name": f"商城货品{number:02d}", "price_fen": 100})
    write_catalog_rows(root, "app-catalog.jsonl", sorted(apps, key=lambda row: row["app_id"]))
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda row: row["goods_id"]))
    return root, [f"商城货品{number:02d}" for number in range(1, count + 1)]


def squeezed_label(engine, floor):
    """按固定种子挑一个请求身份：它抽到的购买时长短于 floor 秒（付款合计的下限），购买因此被付款撑长。"""
    state = engine.context.state.random_state
    return next(f"bulk-{index}" for index in range(200)
                if random_stream(state, f"purchase-duration:action-bulk-{index}").randint(60, 300) < floor)


def tamper_orders(engine, changes):
    """把已提交世界里若干条记录改成引擎自己写不出的样子并保持存储版本连续，用于制造运行故障的前提。"""
    staged = engine.context.state.model_copy(deep=True)
    for identity, values in changes.items():
        staged.deliveries[identity] = staged.deliveries[identity].model_copy(update=values)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_orders_tampered"})])
    engine.context.state = staged


def test_purchase_lasts_its_drawn_duration_with_the_payment_inside_the_last_seconds(engine_factory):
    """一张外卖订单：实际购买时长取抽到的时长与付款时长中较大的一个，付款包在里面而不是加在后面。"""
    engine = engine_factory()
    _, result = act(engine, "noodles", purchase("牛肉面"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    (order,) = records(engine, result)
    assert order.app_id == "meituan" and 10 <= order.payment_duration_seconds <= 20
    drawn = drawn_duration(engine, result)
    assert activity.planned_duration_seconds == drawn == activity.planned_end_at - activity.started_at
    paying = activity.planned_end_at - order.payment_duration_seconds
    assert [(phase.step, phase.started_at, phase.ended_at) for phase in activity_phases(engine.context.state, activity)] == [
        (ORDERING, activity.started_at, paying), (PAYMENT, paying, activity.planned_end_at)]
    reach(engine, activity.planned_end_at)
    assert engine.context.state.current_time == activity.started_at + drawn
    assert engine.context.state.activities[result.activity_id].status == "completed"
    (paid,) = records(engine, result)
    assert (paid.payment_started_at, paid.payment_ended_at, paid.paid_at) == (
        paying, activity.planned_end_at, activity.planned_end_at)


def test_two_stores_on_one_content_app_pay_once_and_deliver_two_trips_with_two_fees(engine_factory, tmp_path):
    """同一个内容 app 上点了两家店的外卖：一张订单一条付款，两个配送组各收一份运费、各送一趟，两条到达事件的
    起因都是同一条付款事件。"""
    root = copy_environment(tmp_path / "one-app")
    edited_rows(root, "merchant-catalog.jsonl", "goods_id", {"lemon-tea": {"store_kinds": ["cafe"]}})
    # 咖啡店只留美团；饿了么改绑到便利店，便利店不卖这两样东西，不影响这次购买的选店。
    edited_rows(root, "location.jsonl", "location_id", {"cafe": {"app_ids": ["meituan"]},
                                                        "store": {"app_ids": ["eleme"]}})
    engine = engine_factory(environment_root=root)
    _, result = act(engine, "two-stores", purchase("牛肉面", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    orders = records(engine, result)
    assert [(order.goods_id, order.store_id, order.app_id, order.destination_id) for order in orders] == [
        ("lemon-tea", "cafe", "meituan", "home"), ("beef-noodles", "noodle-1", "meituan", "home")]
    assert list(payment_orders(tuple(orders))) == ['["meituan"]']
    assert len({order.group_key for order in orders}) == 2
    assert [order.delivery_fee_fen for order in orders] == [500, 500]
    assert len({(order.payment_app_id, order.payment_duration_seconds) for order in orders}) == 1
    accepted = accepted_event(engine, result)
    activity = engine.context.state.activities[result.activity_id]
    finish_activity(engine, result, "two-stores")
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.data == {"kind": "purchase_payment", "activity_id": activity.activity_id,
                            "order_ids": [order.order_id for order in orders], "amount_fen": 2800 + 1200 + 500 * 2}
    assert payment.caused_by == [accepted.event_id] and payment.occurred_at == activity.planned_end_at
    assert payment.resource_changes == {"account:agent:1": -5000, "account:external:scenario": 5000}
    assert engine.context.state.accounts["agent:1"] == 200000 - 5000
    notices = business_events(engine, "payment_notification")
    assert len(notices) == int(orders[0].payment_app_id is not None)
    assert all(row.caused_by == [payment.event_id] and row.data["order_ids"] == payment.data["order_ids"]
               for row in notices)
    trips = scheduled_arrivals(engine)
    assert sorted(row.scheduled_id for row in trips) == [f"delivery-{order.order_id}" for order in orders]
    assert all(row.caused_by == [payment.event_id] for row in trips)
    reach(engine, max(order.expected_at for order in orders))
    arrivals = business_events(engine, "delivery_arrival")
    assert sorted([row["order_id"] for row in arrival.data["orders"]] for arrival in arrivals) == [
        [order.order_id] for order in orders]
    assert all(arrival.caused_by == [payment.event_id] for arrival in arrivals)
    received = {row.data["order_id"]: row.caused_by for row in business_events(engine, "goods_received")}
    assert received == {arrival.data["orders"][0]["order_id"]: [arrival.event_id] for arrival in arrivals}
    assert {order.status for order in records(engine, result)} == {"delivered"}


def test_store_bound_to_two_content_apps_keeps_one_ordering_app_and_charges_one_fee(engine_factory, tmp_path):
    """一家店绑了两个内容 app：一次购买里这家店的几种商品都在同一个下单 app 上，一个配送组、一份运费、一趟配送。"""
    root = copy_environment(tmp_path / "two-apps")
    edited_rows(root, "merchant-catalog.jsonl", "goods_id",
                {key: {"store_kinds": ["cafe"]} for key in ("chips", "lemon-tea", "playing-cards")})
    engine = engine_factory(environment_root=root)
    assert engine.context.catalog.apps_by_store["cafe"] == ("eleme", "meituan")
    chosen = random_stream(engine.context.state.random_state, f"order-app:cafe:{FIRST_DAY}").choice(
        ["eleme", "meituan"])
    _, result = act(engine, "cafe", purchase("薯片", "柠檬茶", "扑克牌", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    orders = records(engine, result)
    assert [order.goods_id for order in orders] == ["chips", "lemon-tea", "lemon-tea", "playing-cards"]
    assert {(order.store_id, order.app_id) for order in orders} == {("cafe", chosen)}
    assert len({order.group_key for order in orders}) == 1
    assert [order.delivery_fee_fen for order in orders] == [500, 0, 0, 0]
    assert list(payment_orders(tuple(orders))) == [payment_order_key(chosen)]
    finish_activity(engine, result, "cafe")
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.data["amount_fen"] == 800 + 1200 * 2 + 1500 + 500
    (trip,) = scheduled_arrivals(engine)
    assert trip.data == {"order_ids": [order.order_id for order in orders]} and trip.caused_by == [payment.event_id]


def test_direct_payment_inside_the_content_app_still_takes_ten_to_twenty_seconds(engine_factory, tmp_path):
    """没有支付 app 时订单在下单的内容 app 里直付：付款段照样占 10 至 20 秒，开始和走完各写一次，走完不扣款。"""
    engine = engine_factory(environment_root=without_payment_apps(tmp_path))
    _, result = act(engine, "direct", purchase("牛肉面"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    (order,) = records(engine, result)
    assert (order.app_id, order.payment_app_id) == ("meituan", None)
    assert 10 <= order.payment_duration_seconds <= 20
    assert (order.payment_started_at, order.payment_ended_at) == (None, None)
    assert activity.planned_duration_seconds == drawn_duration(engine, result)
    paying = activity.planned_end_at - order.payment_duration_seconds
    assert paying in phase_boundaries(engine.context.state, activity)
    reach(engine, paying)
    (order,) = records(engine, result)
    assert (order.payment_started_at, order.payment_ended_at, order.paid_at) == (paying, None, None)
    assert engine.context.state.accounts["agent:1"] == 200000
    reach(engine, activity.planned_end_at)
    (order,) = records(engine, result)
    assert (order.payment_started_at, order.payment_ended_at, order.paid_at) == (
        paying, activity.planned_end_at, activity.planned_end_at)
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.data["amount_fen"] == 3300 and engine.context.state.accounts["agent:1"] == 200000 - 3300
    assert business_events(engine, "payment_notification") == [] and business_events(engine, "app_touch") == []


def test_buying_at_the_store_with_direct_payment_takes_no_payment_time(engine_factory, tmp_path):
    """到店当场买又直付：没有 app 可开，付款时长为 0、没有付款段，购买只有计划结束这一个边界。"""
    engine = engine_factory(environment_root=without_payment_apps(tmp_path), mutate=at_store)
    _, result = act(engine, "counter", purchase("柠檬茶", "纸巾"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    orders = records(engine, result)
    assert {(order.app_id, order.payment_app_id, order.payment_duration_seconds) for order in orders} == {
        (None, None, 0)}
    assert list(payment_orders(tuple(orders))) == ["[null]"]
    assert activity_phases(engine.context.state, activity) == ()
    assert phase_boundaries(engine.context.state, activity) == ()
    assert activity.planned_duration_seconds == drawn_duration(engine, result)
    assert {order.expected_at for order in orders} == {activity.planned_end_at}
    reach(engine, activity.planned_end_at)
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.data["amount_fen"] == 1800 and len(business_events(engine, "goods_received")) == 2
    assert all((order.payment_started_at, order.payment_ended_at, order.paid_at, order.status)
               == (None, None, activity.planned_end_at, "delivered") for order in records(engine, result))
    assert business_events(engine, "payment_notification") == []


def test_three_orders_are_settled_one_payment_each_when_the_purchase_completes(engine_factory):
    """在便利店一次买店里的纸巾、美团上的牛肉面和哔哩哔哩上的笔记本：三张订单按键升序各一条付款，
    金额各是该订单的货款加运费，全部在购买完成那一秒扣款。"""
    engine = engine_factory(mutate=at_store)
    _, result = act(engine, "three", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    by_key = payment_orders(tuple(records(engine, result)))
    assert list(by_key) == ['["bilibili"]', '["meituan"]', "[null]"]
    (parcel,), (noodles,), (tissue,) = by_key.values()
    assert [order.fulfillment.value for order in (parcel, noodles, tissue)] == [
        "station_pickup", "delivery", "pickup"]
    assert activity.planned_duration_seconds == drawn_duration(engine, result)
    phases = activity_phases(engine.context.state, activity)
    assert [(phase.step, phase.order_key) for phase in phases if phase.step == ORDERING] == [
        (ORDERING, '["bilibili"]'), (ORDERING, '["meituan"]')]
    assert phases[0].started_at == activity.started_at and phases[-1].ended_at == activity.planned_end_at
    assert all(later.started_at == earlier.ended_at for earlier, later in zip(phases, phases[1:]))
    accepted = accepted_event(engine, result)
    finish_activity(engine, result, "three")
    payments = business_events(engine, "purchase_payment")
    assert [row.data["order_ids"] for row in payments] == [[parcel.order_id], [noodles.order_id], [tissue.order_id]]
    assert [row.data["amount_fen"] for row in payments] == [parcel.total_price_fen, 2800 + 500, 600]
    assert parcel.total_price_fen in {1200, 1700}
    assert all(row.caused_by == [accepted.event_id] and row.occurred_at == activity.planned_end_at
               for row in payments)
    assert all(row.resource_changes == {"account:agent:1": -row.data["amount_fen"],
                                        "account:external:scenario": row.data["amount_fen"]} for row in payments)
    assert len({row.state_version_after for row in payments}) == 1
    total = sum(row.data["amount_fen"] for row in payments)
    assert engine.context.state.accounts == {"agent:1": 200000 - total, "external:scenario": total - 200000}
    final = {order.order_id: order for order in records(engine, result)}
    assert {order.paid_at for order in final.values()} == {activity.planned_end_at}
    paying = [phase for phase in phases if phase.step == PAYMENT]
    for phase in paying:
        (order,) = [row for row in final.values() if payment_order_key(row.app_id) == phase.order_key]
        assert (order.payment_started_at, order.payment_ended_at) == (phase.started_at, phase.ended_at)
        assert phase.app_id == (order.payment_app_id or order.app_id)
    assert len(paying) == sum(1 for order in final.values() if order.payment_duration_seconds)
    notices = business_events(engine, "payment_notification")
    sources = {tuple(row.data["order_ids"]): row.event_id for row in payments}
    assert [(row.data["app_id"], row.caused_by) for row in notices] == [
        (order.payment_app_id, [sources[(order.order_id,)]]) for order in (parcel, noodles, tissue)
        if order.payment_app_id is not None]
    (received,) = business_events(engine, "goods_received")
    assert received.data["order_id"] == tissue.order_id and received.caused_by == [sources[(tissue.order_id,)]]
    assert {row.scheduled_id: row.caused_by for row in scheduled_arrivals(engine)} == {
        f"delivery-{noodles.order_id}": [sources[(noodles.order_id,)]],
        f"delivery-{parcel.order_id}": [sources[(parcel.order_id,)]]}


def test_balance_short_at_the_start_buys_none_of_the_orders(engine_factory):
    """开始时余额够付其中两张订单、不够整次：整组拒绝，不留记录和活动。"""
    def mutate(persona, config):
        at_store(persona, config)
        persona["initial_balance_fen"] = 5000

    engine = engine_factory(mutate=mutate)
    before = engine.context.state
    _, result = act(engine, "short", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_FUNDS"
    assert result.message == "The caller's available balance is below the actual purchase total."
    after = engine.context.state
    assert after.deliveries == {} and after.activities == before.activities
    assert after.accounts == before.accounts and after.pending_events == before.pending_events
    assert business_events(engine, "purchase_payment") == []


def test_balance_short_at_completion_cancels_every_order_without_payment_or_notice(engine_factory, tmp_path):
    """开始时够、完成时不够：三张订单全部取消，活动失败，没有付款、通知、收货或到达排程；走完的付款段保留。"""
    root = copy_environment(tmp_path / "expense")
    write_catalog_rows(root, "callbacks.jsonl", [{
        "rule_id": "purchase-expense", "trigger": {"class": "action", "key": "purchase"},
        "effect": {"type": "transfer", "direction": "expense", "amount_fen": 199999}}])
    engine = engine_factory(root, mutate=at_store)
    _, result = act(engine, "drained", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    assert engine.context.state.accounts["agent:1"] == 1
    activity = engine.context.state.activities[result.activity_id]
    reach(engine, activity.planned_end_at)
    failed = engine.context.state.activities[result.activity_id]
    assert (failed.status, failed.boundary_type, failed.interruption_reason) == (
        "failed", "payment_failed", "INSUFFICIENT_FUNDS")
    orders = records(engine, result)
    assert len(orders) == 3 and {(order.status, order.paid_at, order.received_at) for order in orders} == {
        ("cancelled", None, None)}
    assert all(order.payment_ended_at - order.payment_started_at == order.payment_duration_seconds
               for order in orders if order.payment_duration_seconds)
    assert engine.context.state.accounts["agent:1"] == 1 and scheduled_arrivals(engine) == []
    for kind in ("purchase_payment", "payment_notification", "goods_received", "delivery_arrival"):
        assert business_events(engine, kind) == []


def test_payments_longer_than_the_drawn_duration_stretch_the_purchase_and_start_at_acceptance(
        engine_factory, tmp_path):
    """十二张订单的付款合计超过抽到的购买时长：购买按付款合计结束，没有下单段，不报运行故障；
    第一个付款段从购买开始那一秒起，它的开始时刻在受理的同一次提交里写入。"""
    root, names = with_many_shops(tmp_path)
    engine = engine_factory(environment_root=root)
    label = squeezed_label(engine, floor=10 * len(names))
    _, result = act(engine, label, purchase(*names))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    orders = records(engine, result)
    by_key = payment_orders(tuple(orders))
    assert list(by_key) == [f'["shop-{number:02d}"]' for number in range(1, 13)]
    total = sum(rows[0].payment_duration_seconds for rows in by_key.values())
    assert all(10 <= order.payment_duration_seconds <= 20 for order in orders)
    assert drawn_duration(engine, result) < 120 <= total == activity.planned_duration_seconds
    phases = activity_phases(engine.context.state, activity)
    assert [phase.step for phase in phases] == [PAYMENT] * 12 and phases[0].started_at == activity.started_at
    snapshot = {row["order_id"]: row["payment_started_at"] for row in result.data["orders"]}
    first = by_key['["shop-01"]'][0]
    assert snapshot == {order.order_id: activity.started_at if order is first else None for order in orders}
    assert accepted_event(engine, result).data["data"]["orders"] == result.data["orders"]
    finish_activity(engine, result, label)
    assert engine.context.state.current_time >= activity.started_at + total
    assert engine.context.state.activities[result.activity_id].status == "completed"
    assert not [row for row in events(engine) if row.event_type == "run_failed"]
    final = payment_orders(tuple(records(engine, result)))
    cursor = activity.started_at
    for (order,) in final.values():
        assert (order.payment_started_at, order.payment_ended_at) == (
            cursor, cursor + order.payment_duration_seconds)
        cursor = order.payment_ended_at
    assert cursor == activity.planned_end_at
    payments = business_events(engine, "purchase_payment")
    assert [row.data["order_ids"] for row in payments] == [[rows[0].order_id] for rows in final.values()]
    assert {row.occurred_at for row in payments} == {activity.planned_end_at}
    spent = sum(order.total_price_fen for order in orders)
    assert engine.context.state.accounts["agent:1"] == 200000 - spent == 200000 - sum(
        row.data["amount_fen"] for row in payments)


def assert_cut_short(engine, result, now):
    """购买被提前结束或截止截断：全部记录取消；走完的付款段两端都在，正在进行的只有开始时刻，没开始的都为空。"""
    activity = engine.context.state.activities[result.activity_id]
    orders = payment_orders(tuple(records(engine, result)))
    shapes = set()
    for phase in activity_phases(engine.context.state, activity):
        (order,) = orders[phase.order_key]
        started = phase.started_at if phase.started_at <= now else None
        ended = phase.ended_at if phase.ended_at <= now else None
        assert (order.payment_started_at, order.payment_ended_at) == (started, ended)
        assert (order.status, order.paid_at, order.received_at) == ("cancelled", None, None)
        shapes.add((started is not None, ended is not None))
    assert (True, False) in shapes and (False, False) in shapes
    assert engine.context.state.accounts["agent:1"] == 200000 and scheduled_arrivals(engine) == []
    for kind in ("purchase_payment", "payment_notification", "goods_received"):
        assert business_events(engine, kind) == []


def test_early_conclusion_cancels_every_record_and_leaves_the_running_payment_phase_open(engine_factory, tmp_path):
    def mutate(persona, config):
        config["progress_report_interval_seconds"] = 1

    root, names = with_many_shops(tmp_path)
    engine = engine_factory(environment_root=root, mutate=mutate)
    label = squeezed_label(engine, floor=10 * len(names))
    _, result = act(engine, label, purchase(*names))
    assert result.status == "in_progress", result.error_code
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[result.activity_id]
    now = engine.context.state.current_time
    assert activity.status == "active" and activity.started_at < now < activity.started_at + 10
    _, ended = act(engine, "stop-buying", conclude_request(activity.name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    assert engine.context.state.activities[result.activity_id].status == "cancelled"
    assert_cut_short(engine, result, now)


def test_run_stop_truncates_the_purchase_and_leaves_the_running_payment_phase_open(engine_factory, tmp_path):
    def mutate(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(hours=1)

    root, names = with_many_shops(tmp_path)
    engine = engine_factory(environment_root=root, mutate=mutate)
    stage_moment(engine, moment=engine.context.state.end_time - 5)
    label = squeezed_label(engine, floor=10 * len(names))
    _, result = act(engine, label, purchase(*names))
    assert result.status == "in_progress", result.error_code
    reach(engine, engine.context.state.end_time)
    assert engine.context.state.activities[result.activity_id].status == "truncated"
    assert_cut_short(engine, result, engine.context.state.end_time)


def test_one_courier_delivery_group_with_two_arrival_days_arrives_in_two_trips(engine_factory, tmp_path):
    """同一个内容 app 上的两件快递到站日不同：一个配送组、一张订单、一条付款，分两趟到站，各一条到达事件。"""
    root = copy_environment(tmp_path / "parcels")
    goods = catalog_rows(root, "merchant-catalog.jsonl")
    names = {f"parcel-{number}": f"测试包裹{number}" for number in range(1, 7)}
    goods.extend({"app_ids": ["bilibili"], "category": "goods", "goods_id": key, "name": name, "price_fen": 900}
                 for key, name in names.items())
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda row: row["goods_id"]))
    engine = engine_factory(environment_root=root, mutate=lambda persona, config: config.update(
        stop_time=config["start_time"] + timedelta(days=6)))
    days = {key: quote_goods(engine.context.inputs, engine.context.state, 1, key).expected_at for key in names}
    early = min(days, key=lambda key: (days[key], key))
    late = min((key for key in days if days[key] != days[early]), key=lambda key: (days[key], key))
    _, result = act(engine, "parcels", purchase(names[late], names[early]))
    assert result.status == "in_progress", result.error_code
    finish_activity(engine, result, "parcels")
    orders = {order.goods_id: order for order in records(engine, result)}
    assert len({order.group_key for order in orders.values()}) == 1
    assert (orders[early].expected_at, orders[late].expected_at) == (days[early], days[late])
    assert sum(order.delivery_fee_fen for order in orders.values()) in {0, 500}
    (payment,) = business_events(engine, "purchase_payment")
    assert payment.data["order_ids"] == sorted(order.order_id for order in orders.values())
    first, second = scheduled_arrivals(engine)
    assert [(row.scheduled_id, row.due_at, row.data, row.caused_by) for row in (first, second)] == [
        (f"delivery-{orders[key].order_id}", days[key], {"order_ids": [orders[key].order_id]}, [payment.event_id])
        for key in (early, late)]
    assert first.sequence < second.sequence
    reach(engine, days[late])
    arrivals = business_events(engine, "delivery_arrival")
    assert [(row.occurred_at, [item["order_id"] for item in row.data["orders"]], row.caused_by)
            for row in arrivals] == [(days[key], [orders[key].order_id], [payment.event_id]) for key in (early, late)]
    assert {order.status for order in records(engine, result)} == {"ready_for_pickup"}


def test_takeaway_due_before_the_payment_arrives_in_the_same_advance_that_completes_the_purchase(engine_factory):
    """预计到达早于付款完成的那一趟外卖在购买完成的同一次推进里送达：先付款、再送达、再收货，不会先送达后付款。"""
    engine = engine_factory()
    _, result = act(engine, "late-pay", purchase("牛肉面"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    (order,) = records(engine, result)
    assert order.expected_at > activity.planned_end_at
    tamper_orders(engine, {order.order_id: {"expected_at": activity.planned_end_at - 30}})
    reach(engine, activity.planned_end_at)
    (payment,) = business_events(engine, "purchase_payment")
    (arrival,) = business_events(engine, "delivery_arrival")
    (received,) = business_events(engine, "goods_received")
    assert payment.occurred_at == arrival.occurred_at == received.occurred_at == activity.planned_end_at
    assert payment.event_seq < arrival.event_seq < received.event_seq
    assert len({payment.state_version_after, arrival.state_version_after, received.state_version_after}) == 1
    assert arrival.caused_by == [payment.event_id] and received.caused_by == [arrival.event_id]
    (final,) = records(engine, result)
    assert (final.status, final.paid_at, final.arrived_at, final.received_at) == (
        "delivered", activity.planned_end_at, activity.planned_end_at, activity.planned_end_at)
    assert scheduled_arrivals(engine) == []


def test_advance_that_jumps_over_a_phase_boundary_stops_the_run(engine_factory, monkeypatch):
    """推进不在段边界停下时（这里让调度看不到购买的段边界），越过付款段开始的那一次推进报运行故障，
    不补写付款时刻，也不付款。"""
    engine = engine_factory()
    _, result = act(engine, "skipped", purchase("牛肉面"))
    activity = engine.context.state.activities[result.activity_id]
    (order,) = records(engine, result)
    paying = activity.planned_end_at - order.payment_duration_seconds
    assert activity.started_at < paying and paying in phase_boundaries(engine.context.state, activity)
    monkeypatch.setattr(time_advance, "phase_boundaries", lambda state, purchase_activity: ())
    with pytest.raises(IntegrityError, match="^Purchase phase boundary was skipped$"):
        reach(engine, activity.planned_end_at)
    assert engine.context.state.current_time < paying
    (order,) = records(engine, result)
    assert (order.payment_started_at, order.payment_ended_at, order.paid_at) == (None, None, None)
    assert business_events(engine, "purchase_payment") == []
    clock = engine.context.state.model_copy(update={"current_time": activity.planned_end_at}, deep=True)
    with pytest.raises(IntegrityError, match="^Purchase phase boundary was skipped$"):
        advance_phases(clock, activity, activity.started_at)


def test_purchase_with_only_some_records_paid_stops_the_run_at_completion(engine_factory):
    engine = engine_factory(mutate=at_store)
    _, result = act(engine, "partial", purchase("纸巾", "平台笔记本"))
    activity = engine.context.state.activities[result.activity_id]
    tissue, parcel = records(engine, result)
    assert (tissue.goods_id, parcel.goods_id) == ("tissue", "online-notebook")
    tamper_orders(engine, {tissue.order_id: {"paid_at": engine.context.state.current_time}})
    with pytest.raises(IntegrityError, match="^Purchase contains partially paid orders$"):
        reach(engine, activity.planned_end_at)
    assert engine.context.state.accounts["agent:1"] == 200000
    assert business_events(engine, "purchase_payment") == []
    assert engine.context.state.deliveries[parcel.order_id].paid_at is None
