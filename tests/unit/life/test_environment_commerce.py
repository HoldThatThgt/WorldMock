"""商品报价、购买时间与付款收货的当前行为验证。"""

from dataclasses import replace
from datetime import datetime, time, timedelta
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog.models import Goods, Location, OpeningHours
from worldmock.environment import load_environment
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.input_models import ModelMetadata
from worldmock.life.action_contract import binding_action
from worldmock.life.admission import Blocked, Call, Context, group_refusal
from worldmock.life.basket import activity_orders, delivery_events, quoted_basket
from worldmock.life.body_config import assemble_body_config
from worldmock.life.commerce import deliver_order
from worldmock.life.guidance import refusal_for_code
from worldmock.life.models import ActivityStatus, AgentState, BodyState, WorldState, WORLD_SCHEMA_VERSION
from worldmock.life.order_notices import (delivered_notices, goods_names, goods_text, shipped_notice,
                                          shipping_delay_seconds)
from worldmock.life.parcel import collect_parcels
from worldmock.life.purchase_offers import courier_offer, delivery_offer, quote_goods
from worldmock.life.purchase_phases import advance_phases, phase_boundaries
from worldmock.life.randomness import random_stream
from worldmock.life.receipt import settle_receipt
from worldmock.life.storefront_runtime import StorefrontRuntime


@pytest.fixture
def environment():
    path = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"
    metadata = lambda config: ModelMetadata(requested_model=config.requested_model,
        metadata_url=config.metadata_url, context_window=262144, max_output_tokens=8192)
    return load_environment(path, metadata_loader=metadata)


def world(inputs, location="home", balance=200000, random_state="commerce-tests"):
    start = int(inputs.config.start_time.timestamp())
    body = BodyState(energy_kcal=1200.0, sleep_pressure=0.4, sleep_deficit_hours=0.0, sleep_need_hours=7.5,
                     chronotype_midsleep=4.0, relaxation_need=0.6, social_need=0.2,
                     social_need_growth_per_hour=0.06)
    agent = AgentState(agent_id=1, name="林舟", location_id=location, balance_account_id="agent:1", body=body,
                       body_settled_at=start, next_decision_at=start)
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
        end_time=start + 86400 * 10, run_id="commerce-test", timezone=inputs.config.timezone,
        persona_sha256=inputs.persona_sha256, config_sha256=inputs.config_sha256,
        agents={1: agent}, accounts={"agent:1": balance, "external:scenario": -balance},
        random_seed=7, random_state=random_state)


def runtime(inputs):
    event = SimpleNamespace(action_id="buy", event_id="accepted-result", data={"status": "in_progress"})
    store = SimpleNamespace(read_agent_events=lambda event_type, agent_id: [event])
    context = SimpleNamespace(inputs=inputs, catalog=inputs.catalog, persona=inputs.persona,
                              config=inputs.config, body=assemble_body_config(inputs.persona), store=store, callback_causes={})
    return StorefrontRuntime(context)


def pending():
    return SimpleNamespace(agent_id=1, action_id="buy", decision_id="decision", requested_event_id="request")


def finish(state, activity):
    current = activity.planned_end_at
    activity = activity.model_copy(update={"elapsed_seconds": activity.planned_duration_seconds,
        "last_settled_at": current, "status": ActivityStatus.COMPLETED, "ended_at": current,
        "boundary_type": "planned_end"}, deep=True)
    agent = state.agents[1].model_copy(update={"body_settled_at": current}, deep=True)
    return state.model_copy(update={"current_time": current, "agents": {1: agent},
                                  "activities": {activity.activity_id: activity}}, deep=True), activity


def operate_phases(state, activity):
    """逐个段边界推进到最后一个段结束，不跳过任何边界；受理那一次提交已经处理了购买开始那一秒。"""
    since = state.current_time
    for moment in phase_boundaries(state, activity):
        state = advance_phases(state.model_copy(update={"current_time": moment}, deep=True), activity, since)
        since = moment
    return state


def test_local_goods_choose_present_store_and_no_shipping(environment):
    state = world(environment, "store")
    offer = quote_goods(environment, state, 1, "lemon-tea", None)
    assert (offer.store_id, offer.app_id, offer.destination_id, offer.fulfillment) == ("store", None, "store", "pickup")
    assert offer.delivery_fee_fen == 0


def test_delivery_chooses_lowest_price_then_distance_then_identity(environment):
    """同一商品各店同价，外卖取离收货地点最近的店，同距取较小的店铺身份；预计到达随这家店的距离而定。"""
    state = world(environment)
    offer = quote_goods(environment, state, 1, "lemon-tea", None)
    stores = [key for key, goods in environment.catalog.goods_by_store.items()
              if "lemon-tea" in goods and environment.catalog.apps_by_store[key]]
    expected = min(stores, key=lambda key: (environment.catalog.shortest_m(key, "home"), key))
    assert offer.store_id == expected
    assert offer.destination_id == "home" and offer.delivery_fee_fen == 500
    assert offer.expected_at == state.current_time + 600 + (offer.distance_meters + 4) // 5


def test_routes_are_fixed_first_and_quoted_by_the_split_offer_functions(environment):
    """先定路线、后报价：对 quote_goods 选中的（店，app）直接调 delivery_offer 得到同一份报价；对没被选中的较远的店
    调用，得到那家店的距离和预计送达，app 原样写进报价；courier_offer 对商品登记的每个 app 各按自己的两个键给出
    到站日和运费；到店报价没有 app。"""
    state = world(environment)
    goods = environment.catalog.goods["lemon-tea"]
    chosen = quote_goods(environment, state, 1, "lemon-tea", None)
    assert (chosen.store_id, chosen.app_id) == ("noodle-1", "meituan")
    assert delivery_offer(environment, state, goods, (chosen.store_id, chosen.app_id), "home") == chosen
    far = delivery_offer(environment, state, goods, ("cafe", "eleme"), "home")
    meters = environment.catalog.shortest_m("cafe", "home")
    assert meters > chosen.distance_meters
    assert (far.store_id, far.app_id, far.distance_meters, far.destination_id) == ("cafe", "eleme", meters, "home")
    assert far.expected_at == state.current_time + 600 + -(-meters // 5)
    assert (far.fulfillment, far.delivery_fee_fen, far.trip_id) == ("delivery", 500, "cafe->home:delivery")
    notebook = environment.catalog.goods["online-notebook"].model_copy(update={"app_ids": ("bilibili", "meituan")})
    zone = ZoneInfo(state.timezone)
    day = datetime.fromtimestamp(state.current_time, zone).date()
    offers = {}
    for app in notebook.app_ids:
        offer = offers[app] = courier_offer(environment, state, notebook, app)
        days = random_stream(state.random_state, f"courier-days:online-notebook:{app}:{day.isoformat()}").choice(
            (2, 3, 4, 5))
        fee = random_stream(state.random_state, f"courier-fee:online-notebook:{app}").choice((0, 500))
        assert offer.app_id == app and offer.delivery_fee_fen == fee
        assert offer.expected_at == int(datetime.combine(day + timedelta(days=days), time.min, zone).timestamp())
        assert (offer.store_id, offer.fulfillment, offer.destination_id) == (None, "station_pickup", "parcel-station")
    pickup = quote_goods(environment, world(environment, "store"), 1, "lemon-tea", "eleme")
    assert (pickup.fulfillment, pickup.store_id, pickup.app_id) == ("pickup", "store", None)


def test_closed_present_store_does_not_change_to_delivery(environment):
    catalog = environment.catalog
    closed = catalog.locations["store"].model_copy(update={"opens": OpeningHours(weekly={})})
    inputs = replace(environment, catalog=replace(catalog, locations=MappingProxyType({**catalog.locations,
                                                                                   "store": closed})))
    with pytest.raises(BusinessRuleError, match="LOCATION_CLOSED"):
        quote_goods(inputs, world(inputs, "store"), 1, "lemon-tea", None)


def test_no_remote_channel_requires_real_store(environment):
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
        quote_goods(environment, world(environment), 1, "tissue", None)


def test_preview_courier_is_stable_local_midnight_and_has_no_route(environment):
    state = world(environment)
    offer = quote_goods(environment, state, 1, "online-notebook", None)
    assert offer == quote_goods(environment, state, 1, "online-notebook", None)
    arrival = datetime.fromtimestamp(offer.expected_at, ZoneInfo(state.timezone))
    created = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    assert arrival.time() == time.min
    assert 2 <= (arrival.date() - created.date()).days <= 5
    assert offer.store_id is None and offer.trip_id is None
    assert offer.path == ("parcel-station",) and offer.distance_meters == offer.prep_seconds == 0
    assert offer.delivery_fee_fen in {0, 500}


def test_request_freezes_preview_does_not_pay_or_generate_receipt(environment):
    state = world(environment)
    preview = quote_goods(environment, state, 1, "online-notebook", None)
    changed, activity = runtime(environment).prepare_purchase(state, pending(), ("online-notebook",), None)
    order = activity_orders(changed, activity)[0]
    assert changed.accounts == state.accounts and state.deliveries == {}
    assert order.expected_at == preview.expected_at and order.app_id == preview.app_id
    assert order.status == "pending" and order.paid_at is None and not changed.pending_events
    assert 60 <= activity.planned_duration_seconds <= 300 and activity.resource_claims == {"second"}
    assert activity.name.startswith("购买平台笔记本")
    assert "recipient_id" not in order.model_dump() and "seller_account_id" not in order.model_dump()
    restored = WorldState.model_validate(changed.model_dump(mode="json"))
    assert restored.activities[activity.activity_id].planned_duration_seconds == activity.planned_duration_seconds


@pytest.mark.parametrize("goods,label,total", [
    (("lemon-tea", "lemon-tea"), "购买柠檬茶×2（", 2400),
    (("playing-cards", "lemon-tea", "rice-ball", "lemon-tea", "lemon-tea"), "购买柠檬茶×3、扑克牌、饭团（", 6100),
    (("lemon-tea",) * 11, "购买柠檬茶×11（", 13200),
])
def test_repeated_goods_are_separate_portions_of_one_purchase_activity(environment, goods, label, total):
    """同一商品写几次就是几份、份数不设上限：每份一条履约记录，记录按配送组再按商品身份编号，活动名把同一商品合写。"""
    state = world(environment, "store")
    assert quoted_basket(environment, state, 1, goods, None)[1] == total
    changed, activity = runtime(environment).prepare_purchase(state, pending(), goods, None)
    orders = activity_orders(changed, activity)
    assert [order.order_id for order in orders] == [f"order-buy-{index:04d}" for index in range(1, len(goods) + 1)]
    assert [order.order_id for order in orders] == sorted(order.order_id for order in orders)
    assert [order.goods_id for order in orders] == sorted(goods)
    assert all(order.quantity == 1 and order.status == "pending" for order in orders)
    assert activity.activity_id == "activity-buy-purchase" and activity.name.startswith(label)
    assert list(changed.activities) == [activity.activity_id] and changed.accounts == state.accounts
    assert not state.deliveries and not state.activities


def _basket_refusal(environment, state, goods):
    """整组核验里只有这一条购买调用时的结论（槽位、对象条件与余额同一入口）。"""
    names = {key: row.display_name for key, row in environment.catalog.locations.items()}
    rows = tuple({"object": environment.catalog.goods[key].name, "kind": "supply", "object_id": key,
                  "selected_action": binding_action("purchase")} for key in goods)
    return group_refusal(Context(state, 1, names, {}, environment), [Call("start_side_activity", "purchase", rows)])


def test_basket_precheck_merges_delivery_fee_once(environment):
    """报价合并运费但不核对余额；余额由整组核验按合并后的总价核对一次。"""
    goods = ("lemon-tea", "playing-cards")
    groups, total = quoted_basket(environment, world(environment, "cafe"), 1, goods, None)
    assert len(groups) == 1 and total == 2700
    assert quoted_basket(environment, world(environment, "cafe", balance=2699), 1, goods, None)[1] == 2700
    assert _basket_refusal(environment, world(environment, "cafe", balance=2700), goods) is None
    assert _basket_refusal(environment, world(environment, "cafe", balance=2699), goods) == Blocked(
        refusal_for_code("INSUFFICIENT_FUNDS"), ("start_side_activity",))
    remote = ("lemon-tea", "online-notebook")
    fee_groups, fee_total = quoted_basket(environment, world(environment), 1, remote, None)
    assert fee_total == 2400 + 500 * sum(any(row.delivery_fee_fen for row in rows) for rows in fee_groups.values())
    assert _basket_refusal(environment, world(environment, balance=fee_total), remote) is None
    assert _basket_refusal(environment, world(environment, balance=fee_total - 1), remote) == Blocked(
        refusal_for_code("INSUFFICIENT_FUNDS"), ("start_side_activity",))


def test_normal_completion_pays_the_local_order_and_receives_its_goods_once(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                               ("lemon-tea", "playing-cards"), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    changed, events = commerce.complete_purchase(state, activity)
    assert changed.accounts == {"agent:1": 197300, "external:scenario": -197300}
    payment = [event for event in events if event.data["kind"] == "purchase_payment"]
    receipts = [event for event in events if event.data["kind"] == "goods_received"]
    assert len(payment) == 1 and len(receipts) == 2
    assert payment[0].caused_by == ["accepted-result"]
    assert payment[0].resource_changes == {"account:agent:1": -2700, "account:external:scenario": 2700}
    assert all(receipt.caused_by == [payment[0].event_id] for receipt in receipts)
    assert all(order.paid_at == state.current_time and order.received_at == state.current_time
               for order in changed.deliveries.values())
    assert changed.agents[1].body.relaxation_need == pytest.approx(0.4)
    replayed, duplicate_events = commerce.complete_purchase(changed, activity)
    assert replayed.accounts == changed.accounts and duplicate_events == []
    for order in replayed.deliveries.values():
        assert settle_receipt(replayed, commerce.context, order.order_id).receipts == ()


def test_completion_insufficient_funds_cancels_entire_purchase_without_charge(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                               ("lemon-tea", "online-notebook"), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state = state.model_copy(update={"accounts": {"agent:1": 0, "external:scenario": 0}}, deep=True)
    changed, events = commerce.complete_purchase(state, activity)
    assert changed.accounts == state.accounts and events == []
    assert all(order.status == "cancelled" and order.paid_at is None for order in changed.deliveries.values())
    assert changed.activities[activity.activity_id].status == ActivityStatus.FAILED
    assert changed.agents[1].body == state.agents[1].body


def test_cancel_before_completion_never_pays_schedules_or_receives(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(), ("lemon-tea",), None)
    changed = commerce.cancel_purchase(state, activity)
    assert changed.accounts == state.accounts and not changed.pending_events
    assert next(iter(changed.deliveries.values())).status == "cancelled"
    assert commerce.complete_purchase(changed, activity)[1] == []


def test_courier_arrival_only_ready_then_collect_all_and_no_double_effect(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state, _ = commerce.complete_purchase(state, activity)
    order = activity_orders(state, activity)[0]
    due = state.model_copy(update={"current_time": order.expected_at}, deep=True)
    arrived = deliver_order(due, commerce.context, order.order_id)
    assert arrived.order.status == "ready_for_pickup" and arrived.receipts == ()
    assert arrived.state.agents[1].body == state.agents[1].body
    agent = arrived.state.agents[1].model_copy(update={"location_id": "parcel-station"}, deep=True)
    at_station = arrived.state.model_copy(update={"agents": {1: agent}}, deep=True)
    outcome = collect_parcels(at_station, commerce.context, 1, "parcel-station")
    assert outcome.orders[0].status == "delivered" and len(outcome.receipts) == 1
    assert outcome.state.agents[1].body.relaxation_need == pytest.approx(0.5)
    with pytest.raises(PreconditionError, match="PARCEL_NOT_READY"):
        collect_parcels(outcome.state, commerce.context, 1, "parcel-station")


def test_unpaid_order_cannot_be_delivered_or_received(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",), None)
    order = activity_orders(state, activity)[0]
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_PAID"):
        deliver_order(state, commerce.context, order.order_id)
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_PAID"):
        settle_receipt(state, commerce.context, order.order_id)


@pytest.mark.parametrize("random_state", [f"payment-{index}" for index in range(12)])
def test_payment_phases_and_notifications_follow_each_order_and_its_selected_app(environment, random_state):
    """到店订单加一张快递订单：付款段按订单排在购买末尾，逐边界只写付款时刻不扣款；完成时一张订单一条付款。
    带下单 app 的快递订单有「订单已下单」，它的「支付成功」由支付 app 发、直付时由下单 app 发；
    到店订单只在抽到支付 app 时有「支付成功」。"""
    commerce = runtime(environment)
    initial = world(environment, "store", random_state=random_state)
    state, activity = commerce.prepare_purchase(initial, pending(), ("lemon-tea", "online-notebook"), None)
    since = state.current_time
    for moment in phase_boundaries(state, activity):
        state = advance_phases(state.model_copy(update={"current_time": moment}, deep=True), activity, since)
        since = moment
        assert state.accounts == initial.accounts
        assert all(order.paid_at is None for order in state.deliveries.values())
    state, activity = finish(state, activity)
    changed, events = commerce.complete_purchase(state, activity)
    parcel, local = sorted(changed.deliveries.values(), key=lambda order: order.app_id is None)
    assert (parcel.app_id, local.app_id) == ("bilibili", None)
    paid = [event for event in events if event.data["kind"] == "purchase_payment"]
    notices = [event for event in events if event.data["kind"] == "payment_notification"]
    assert [event.data["order_ids"] for event in paid] == [[parcel.order_id], [local.order_id]]
    assert not [event for event in events if event.data["kind"] == "app_touch"]
    senders = [parcel.payment_app_id or "bilibili", *([] if local.payment_app_id is None else [local.payment_app_id])]
    assert [notice.data["app_id"] for notice in notices] == senders
    (placed,) = [event for event in events if event.data["kind"] == "order_notification"]
    assert placed.data == {"kind": "order_notification", "app_id": "bilibili", "order_ids": [parcel.order_id],
                           "title": "订单已下单", "summary": "已下单：平台笔记本"}
    assert placed.caused_by == [paid[0].event_id] and placed.visibility == "system"
    # 每张订单先写付款事件，再写「订单已下单」，再写「支付成功」。
    assert [event.data["kind"] for event in events][:3] == [
        "purchase_payment", "order_notification", "payment_notification"]
    assert 10 <= parcel.payment_duration_seconds <= 20
    assert local.payment_duration_seconds == 0 if local.payment_app_id is None else (
        10 <= local.payment_duration_seconds <= 20)
    timed = [order for order in (parcel, local) if order.payment_duration_seconds]
    cursor = activity.planned_end_at - sum(order.payment_duration_seconds for order in timed)
    assert cursor >= activity.started_at
    for order in timed:
        assert (order.payment_started_at, order.payment_ended_at) == (
            cursor, cursor + order.payment_duration_seconds)
        cursor = order.payment_ended_at
    assert cursor == activity.planned_end_at
    if local.payment_duration_seconds == 0:
        assert local.payment_started_at is None and local.payment_ended_at is None
    for notice in notices:
        source = next(event for event in paid if event.event_id == notice.caused_by[0])
        assert notice.data["amount_fen"] == source.data["amount_fen"]
        assert notice.data["order_ids"] == source.data["order_ids"]
        assert notice.resource_changes == {}
    assert sum(changed.accounts.values()) == sum(initial.accounts.values())


def test_skipped_phase_boundary_is_a_run_fault_and_completion_needs_the_finished_payment(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store", random_state="payment-0"),
                                               pending(), ("lemon-tea", "online-notebook"), None)
    assert phase_boundaries(state, activity)
    state, activity = finish(state, activity)
    with pytest.raises(IntegrityError, match="^Purchase phase boundary was skipped$"):
        advance_phases(state, activity, activity.started_at)
    with pytest.raises(IntegrityError, match="^Purchase completion requires actual payment operation evidence$"):
        commerce.complete_purchase(state, activity)


def test_completion_needs_the_finished_payment_phase_of_an_order_paid_inside_its_content_app(environment):
    """没有登记支付 app 时订单在下单的内容 app 里直付：支付 app 为空，付款仍占 10 至 20 秒。
    判据是付款时长大于 0，所以这个付款段没有走完就到完成同样是运行故障，不扣款也不改记录。"""
    catalog = environment.catalog
    inputs = replace(environment, catalog=replace(catalog, apps_by_action=MappingProxyType({
        key: value for key, value in catalog.apps_by_action.items() if key != "purchase"})))
    commerce = runtime(inputs)
    accepted, running = commerce.prepare_purchase(world(inputs), pending(), ("online-notebook",), None)
    (order,) = activity_orders(accepted, running)
    assert (order.app_id, order.payment_app_id) == ("bilibili", None)
    assert 10 <= order.payment_duration_seconds <= 20 and order.payment_ended_at is None
    state, activity = finish(accepted, running)
    before = state.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="^Purchase completion requires actual payment operation evidence$"):
        commerce.complete_purchase(state, activity)
    assert state.model_dump(mode="json") == before
    assert state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    assert activity_orders(state, activity) == (order,) and order.paid_at is None and order.status == "pending"
    state, activity = finish(operate_phases(accepted, running), running)
    paid, events = commerce.complete_purchase(state, activity)
    assert [(event.data["kind"], event.data.get("app_id")) for event in events] == [
        ("purchase_payment", None), ("order_notification", "bilibili"), ("payment_notification", "bilibili")]
    (settled,) = activity_orders(paid, activity)
    assert (settled.payment_ended_at, settled.paid_at) == (activity.planned_end_at, activity.planned_end_at)
    assert paid.accounts["agent:1"] == 200000 - settled.total_price_fen


def paid_purchase(environment, goods, location="home"):
    """经真实受理、付款段和完成结算得到已付款世界、购买活动与当次事件。"""
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, location), pending(), goods, None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state, events = commerce.complete_purchase(state, activity)
    return state, activity, events


def arrivals(state):
    return [row for row in state.pending_events if row.kind == "delivery_arrival"]


def test_completion_schedules_each_trip_with_its_own_payment_as_the_only_cause(environment):
    state, activity, events = paid_purchase(environment, ("lemon-tea", "online-notebook"), "store")
    payments = {tuple(event.data["order_ids"]): event.event_id for event in events
                if event.data.get("kind") == "purchase_payment"}
    local, parcel = sorted(activity_orders(state, activity), key=lambda order: order.fulfillment)
    assert (local.fulfillment, parcel.fulfillment) == ("pickup", "station_pickup") and len(payments) == 2
    (scheduled,) = arrivals(state)
    assert scheduled.scheduled_id == f"delivery-{parcel.order_id}" and scheduled.agent_id == 1
    assert scheduled.data == {"order_ids": [parcel.order_id]} and scheduled.due_at == parcel.expected_at
    assert scheduled.caused_by == [payments[(parcel.order_id,)]]
    assert scheduled.caused_by != [payments[(local.order_id,)]] and scheduled.caused_by != ["accepted-result"]


def test_trips_sharing_arrival_time_method_and_destination_stay_separate_per_delivery_group(environment):
    state, activity, _ = paid_purchase(environment, ("online-notebook",))
    first = activity_orders(state, activity)[0]
    other_group = json.dumps([None, first.destination_id, "douyin", first.fulfillment], separators=(",", ":"))
    assert first.group_key < other_group
    second = first.model_copy(update={"order_id": "order-buy-2", "app_id": "douyin", "group_key": other_group})
    assert (second.expected_at, second.fulfillment, second.destination_id) == (
        first.expected_at, first.fulfillment, first.destination_id)
    idle = state.model_copy(update={"pending_events": []})
    scheduled = delivery_events(idle, (second, first), {first.order_id: "paid-first", second.order_id: "paid-second"})
    # 排程序号先按趟的顺序分给各趟的发货通知，再按同样的顺序分给各趟的到达。
    assert [(row.scheduled_id, row.data, row.caused_by, row.sequence) for row in scheduled] == [
        (f"shipped-{first.order_id}", {"order_ids": [first.order_id]}, ["paid-first"], 1),
        ("shipped-order-buy-2", {"order_ids": ["order-buy-2"]}, ["paid-second"], 2),
        (f"delivery-{first.order_id}", {"order_ids": [first.order_id]}, ["paid-first"], 3),
        ("delivery-order-buy-2", {"order_ids": ["order-buy-2"]}, ["paid-second"], 4)]
    assert [row.kind for row in scheduled] == ["order_shipped"] * 2 + ["delivery_arrival"] * 2
    shipped = first.paid_at + shipping_delay_seconds(first.expected_at - first.paid_at)
    assert [row.due_at for row in scheduled] == [shipped, shipped, first.expected_at, first.expected_at]
    assert all(row.agent_id == 1 for row in scheduled)


def test_one_delivery_group_with_two_arrival_days_schedules_two_trips_in_arrival_order(environment):
    state, activity, _ = paid_purchase(environment, ("online-notebook",))
    early = activity_orders(state, activity)[0]
    late = early.model_copy(update={"order_id": "order-buy-0", "expected_at": early.expected_at + 86400})
    same_day = early.model_copy(update={"order_id": "order-buy-3"})
    unpaid = early.model_copy(update={"order_id": "order-buy-4", "paid_at": None})
    assert late.group_key == early.group_key == same_day.group_key
    causes = {early.order_id: "paid", late.order_id: "paid", same_day.order_id: "paid"}
    scheduled = delivery_events(state, (late, unpaid, same_day, early), causes)
    busy = max(row.sequence for row in state.pending_events)
    paid = early.paid_at
    assert [(row.scheduled_id, row.due_at, row.data["order_ids"], row.sequence) for row in scheduled] == [
        (f"shipped-{early.order_id}", paid + shipping_delay_seconds(early.expected_at - paid),
         [early.order_id, "order-buy-3"], busy + 1),
        ("shipped-order-buy-0", paid + shipping_delay_seconds(late.expected_at - paid), ["order-buy-0"], busy + 2),
        (f"delivery-{early.order_id}", early.expected_at, [early.order_id, "order-buy-3"], busy + 3),
        ("delivery-order-buy-0", late.expected_at, ["order-buy-0"], busy + 4)]
    assert all(row.caused_by == ["paid"] for row in scheduled)


def test_trip_whose_expected_arrival_precedes_payment_is_due_at_payment(environment):
    state, activity, _ = paid_purchase(environment, ("online-notebook",))
    order = activity_orders(state, activity)[0]
    overdue = order.model_copy(update={"expected_at": order.paid_at - 30})
    on_time = order.model_copy(update={"order_id": "order-buy-9", "expected_at": order.paid_at})
    scheduled = delivery_events(state, (on_time, overdue), {order.order_id: "paid", "order-buy-9": "paid"})
    # 预计到达不晚于付款的两趟：到达的到期时刻取付款时刻，发货通知也落在付款那一秒，序号排在到达之前。
    assert [(row.kind, row.data["order_ids"], row.due_at) for row in scheduled] == [
        ("order_shipped", [order.order_id], order.paid_at), ("order_shipped", ["order-buy-9"], order.paid_at),
        ("delivery_arrival", [order.order_id], order.paid_at), ("delivery_arrival", ["order-buy-9"], order.paid_at)]
    assert [row.sequence for row in scheduled] == sorted(row.sequence for row in scheduled)


@pytest.mark.parametrize("interval,delay", [(60, 6), (61, 7), (700, 70), (1, 1), (0, 0), (186540, 18654)])
def test_shipping_notice_comes_a_tenth_of_the_way_to_arrival_rounded_up_to_the_second(interval, delay):
    assert shipping_delay_seconds(interval) == delay


def test_goods_list_sorts_by_goods_identity_and_folds_repeated_portions(environment):
    state, activity, events = paid_purchase(environment, ("lemon-tea", "beef-noodles", "lemon-tea"))
    orders = activity_orders(state, activity)
    assert goods_names(orders, environment.catalog) == ["牛肉面", "柠檬茶×2"]
    assert goods_text(orders, environment.catalog) == "牛肉面、柠檬茶×2"
    texts = {event.data["title"]: event.data["summary"] for event in events
             if event.data["kind"] in {"order_notification", "payment_notification"}}
    assert texts == {"订单已下单": "已下单：牛肉面、柠檬茶×2", "支付成功": "已消费 57.00 元，购买牛肉面、柠檬茶×2"}
    assert state.activities[activity.activity_id].name.startswith("购买牛肉面、柠檬茶×2（")


def test_shipped_and_delivered_notices_name_the_trip_and_point_at_their_real_causes(environment):
    state, activity, events = paid_purchase(environment, ("lemon-tea", "beef-noodles", "lemon-tea"))
    (payment,) = [event for event in events if event.data["kind"] == "purchase_payment"]
    orders = activity_orders(state, activity)
    (shipping,) = [row for row in state.pending_events if row.kind == "order_shipped"]
    (arriving,) = arrivals(state)
    head = min(order.order_id for order in orders)
    paid_at, due_at = orders[0].paid_at, orders[0].expected_at
    assert (shipping.scheduled_id, arriving.scheduled_id) == (f"shipped-{head}", f"delivery-{head}")
    assert shipping.due_at == paid_at + shipping_delay_seconds(due_at - paid_at) and arriving.due_at == due_at
    assert shipping.sequence + 1 == arriving.sequence and shipping.caused_by == [payment.event_id]
    assert shipping.data == arriving.data == {"order_ids": sorted(order.order_id for order in orders)}
    sent = shipped_notice(state.model_copy(update={"current_time": shipping.due_at}), shipping, environment.catalog)
    assert (sent.occurred_at, sent.caused_by, sent.visibility, sent.agent_id) == (
        shipping.due_at, [payment.event_id], "system", 1)
    assert sent.data == {"kind": "order_notification", "app_id": "meituan", "title": "订单已发货",
                         "order_ids": shipping.data["order_ids"], "summary": "牛肉面、柠檬茶×2已发货"}
    arrived = state.model_copy(update={"current_time": due_at}, deep=True)
    for identity in arriving.data["order_ids"]:
        arrived = deliver_order(arrived, runtime(environment).context, identity).state
    arrival = SimpleNamespace(event_id="arrival-event", occurred_at=due_at, data={
        "orders": [arrived.deliveries[key].model_dump(mode="json") for key in arriving.data["order_ids"]]})
    (notice,) = delivered_notices(arrived, arrival, environment.catalog)
    assert (notice.occurred_at, notice.caused_by, notice.visibility) == (due_at, ["arrival-event"], "system")
    assert notice.data == {"kind": "order_notification", "app_id": "meituan", "title": "订单已送达",
                           "order_ids": shipping.data["order_ids"], "summary": "牛肉面、柠檬茶×2已送达"}


def test_parcel_reaching_the_station_gets_no_delivered_notice(environment):
    state, activity, _ = paid_purchase(environment, ("online-notebook",))
    (order,) = activity_orders(state, activity)
    arrived = deliver_order(state.model_copy(update={"current_time": order.expected_at}, deep=True),
                            runtime(environment).context, order.order_id).state
    assert arrived.deliveries[order.order_id].status == "ready_for_pickup"
    arrival = SimpleNamespace(event_id="arrival-event", occurred_at=order.expected_at, data={
        "orders": [arrived.deliveries[order.order_id].model_dump(mode="json")]})
    assert delivered_notices(arrived, arrival, environment.catalog) == []
