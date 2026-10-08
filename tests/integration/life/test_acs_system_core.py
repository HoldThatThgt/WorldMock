"""@brief 加油、长途到站领取、实际驾车日与闹钟的真实纯合同反例。"""

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime

import pytest
from pydantic import ValidationError

from tests.catalog.conftest import edit_row, read_payload, write_payload
from tests.life.purchase_worlds import ENVIRONMENT, at_time
from tests.integration.life.test_commerce import order_request
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.mapgen import generate_map
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.constants import DELIVERY_METERS_PER_SECOND, DELIVERY_PREP_SECONDS
from worldmock.exchange.operators import delivery_seconds
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.alarm import set_alarm
from worldmock.life.schedule import alarm_time
from worldmock.life.basket import plan_recipient_delivery, purchase_order
from worldmock.life.body import last_flows
from worldmock.life.commerce import deliver_order
from worldmock.life.fuel import charge_driving_days, driving_dates, require_driving_days
from worldmock.life.load_checks import check_parcel_stations
from worldmock.life.models import ActivityState, DeliveryState, PendingAction, PurchaseItem, PurchaseOrderRequest
from worldmock.life.models import SetAlarmRequest, WorldState
from worldmock.life.order_routing import order_requests
from worldmock.life.parcel import collect_parcels, nearest_parcel_station
from worldmock.life.receipt import settle_receipt
from worldmock.life.scenario import AgentConfig
from worldmock.life.world_setup import _initial_state, load_runtime_scenario
from worldmock.scheduling.effects import apply_scheduled_effect, process_scheduled_events


def system_world(tmp_path, shipping=None, owns_car=True, days=2):
    """@brief 复制真实发布目录、重新生成图并通过生产加载器造双人世界。"""
    environment = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT, environment)
    directory = environment / "catalog"
    locations = read_payload(directory, "locations.json")
    locations["locations"].extend([
        {"location_id": "gas", "name": "测试加油站", "kind": "gas_station", "app_ids": [],
         "shipping_seconds": None},
        {"location_id": "parcel-a", "name": "甲驿站", "kind": "parcel_station", "app_ids": [],
         "shipping_seconds": None},
        {"location_id": "parcel-b", "name": "乙驿站", "kind": "parcel_station", "app_ids": [],
         "shipping_seconds": None},
    ])
    locations["locations"].sort(key=lambda row: row["location_id"])
    write_payload(directory, "locations.json", locations)
    graph = generate_map([row["location_id"] for row in locations["locations"]], 7)
    write_payload(directory, "map.json", graph.model_dump(mode="json"))
    goods = read_payload(directory, "goods.json")
    goods["goods"].append({"goods_id": "fuel-pack", "name": "测试燃油", "category": "fuel",
                           "price_fen": 20000, "store_ids": "all"})
    goods["goods"].sort(key=lambda row: row["goods_id"])
    write_payload(directory, "goods.json", goods)
    apps = read_payload(directory, "apps.json")
    apps["apps"].append({"app_id": "gas-pay", "name": "加油支付", "purposes": ["payment"], "payment_app_ids": []})
    apps["apps"].sort(key=lambda row: row["app_id"])
    write_payload(directory, "apps.json", apps)
    edit_row(directory, "locations.json", "store", {"app_ids": ["meituan"], "shipping_seconds": shipping})
    edit_row(directory, "contents.json", "topic-sales", {"duration_seconds": 64800})
    path = environment / "scenarios/multi-person.json"
    document = json.loads(path.read_bytes())
    document["end_at"] = "2026-01-07T00:00:00+08:00"
    document["locations"].extend({"location_id": value} for value in ("gas", "parcel-a", "parcel-b"))
    document["accounts"].append({"account_id": "merchant:gas", "money_fen": 0, "role": "merchant:gas"})
    for agent in document["agents"]:
        agent.update(owns_car=owns_car, initial_driving_days=days, initial_balance_fen=100000)
    path.write_bytes(canonical_json(document) + b"\n")
    semantics, digest = load_runtime_scenario(path)
    state = _initial_state(semantics, digest, "system-pure")
    state.pending_events = []
    return semantics, state


def fuel_request(recipient="agent:1", app=None, fulfillment="pickup"):
    """@brief 加油支付 app 只是现场付款，不是远程购物 app。"""
    return order_request("gas", "gas", app, fulfillment, ("fuel-pack",), recipient, "gas-pay")


def place(state, identity, location):
    """@brief 精确设置纯合同的现场人物。"""
    state.agents[identity] = state.agents[identity].model_copy(update={"location_id": location})


def parcel_request(semantics, state, goods=("tissue",), recipient="agent:1"):
    """@brief 长途内部请求明确填写实际履约驿站，不双读家庭地址。"""
    quote = plan_recipient_delivery(state, semantics, 1, recipient, "store")
    return order_request("store", quote.destination_id, fulfillment=quote.kind.value, goods=goods, recipient=recipient)


def pending_alarm(state, at, identity="wake"):
    """@brief 用真实请求事件身份和内容摘要构造严格待提交请求。"""
    request = {"at": at}
    requested = EventDraft(event_type="action_requested", occurred_at=state.current_time, agent_id=1,
                           action_id=identity, data={"tool_name": "set_alarm", "request": request})
    pending = PendingAction(run_id=state.run_id, round_id="round", decision_id="decision", action_id=identity,
        agent_id=1, observed_at=state.current_time, world_state_version=state.version, order=1,
        tool_name="set_alarm", request=request, request_sha256=hashlib.sha256(canonical_json(request)).hexdigest(),
        requested_event_id=requested.event_id)
    return pending, requested


def stamp(value):
    """@brief 测试日期同样使用带偏移 ISO 真正换算。"""
    return int(datetime.fromisoformat(value).timestamp())


def test_fuel_instant_self_purchase_adds_seven_without_body_or_inventory(tmp_path):
    semantics, state = system_world(tmp_path)
    place(state, 1, "gas")
    state.agents[1].last_driving_date = "2026-01-05"
    before = state.model_dump_json()
    body = state.agents[1].body.model_dump()
    outcome = purchase_order(state, semantics, 1, fuel_request())
    order = outcome.orders[0]
    assert outcome.state.agents[1].driving_days == 9
    assert outcome.state.agents[1].last_driving_date == "2026-01-05"
    assert outcome.state.agents[1].body.model_dump() == body
    assert order.fulfillment == "pickup" and order.app_id is None and order.payment_app_id == "gas-pay"
    assert order.arrived_at == order.received_at == order.created_at and outcome.scheduled == ()
    assert outcome.receipts[0].data["provided"] == outcome.receipts[0].data["applied"] == 7
    assert outcome.receipts[0].data["resource_flows"] == [] and last_flows() == ()
    assert outcome.state.accounts["agent:1"] == 80000 and outcome.state.accounts["merchant:gas"] == 20000
    assert "inventory" not in outcome.state.agents[1].model_dump() and state.model_dump_json() == before
    restored = WorldState.model_validate_json(outcome.state.model_dump_json())
    replay = deliver_order(restored, semantics, order.order_id)
    assert replay.state is restored and replay.receipts == () and replay.state.agents[1].driving_days == 9


@pytest.mark.parametrize("failure", ["no_car", "away", "remote", "other"])
def test_invalid_fuel_is_rejected_atomically_before_any_payment(tmp_path, monkeypatch, failure):
    semantics, state = system_world(tmp_path, owns_car=failure != "no_car", days=0)
    place(state, 1, "gas" if failure != "away" else "home")
    place(state, 2, "gas")
    request = fuel_request("agent:2" if failure == "other" else "agent:1",
                           "meituan" if failure == "remote" else None,
                           "delivery" if failure == "remote" else "pickup")
    before = state.model_dump_json()
    monkeypatch.setattr("worldmock.life.basket.settle_money_transfer", lambda *args: pytest.fail("Payment occurred"))
    with pytest.raises(PreconditionError, match="FUEL_PURCHASE_INVALID"):
        purchase_order(state, semantics, 1, request)
    assert state.model_dump_json() == before and last_flows() == ()


@pytest.mark.parametrize("seconds,kind", [(86400, "delivery"), (86401, "station_pickup")])
def test_shipping_threshold_and_quote_use_registered_complete_duration(tmp_path, seconds, kind):
    semantics, state = system_world(tmp_path, shipping=seconds)
    quote = plan_recipient_delivery(state, semantics, 1, "agent:1", "store")
    outcome = purchase_order(state, semantics, 1,
                             order_request("store", quote.destination_id, fulfillment=quote.kind.value,
                                           goods=("tissue",)))
    order = outcome.orders[0]
    assert order.fulfillment == kind and order.expected_at == state.current_time + seconds
    assert order.expected_at == quote.expected_at and order.destination_id == quote.destination_id
    assert order.delivery_address_id == "home" and order.arrived_at is order.received_at is None
    ready = at_time(outcome.state, order.expected_at)
    result, drafts, _ = process_scheduled_events(ready, ready.current_time, semantics)
    if kind == "station_pickup":
        assert result.deliveries[order.order_id].status == "ready_for_pickup" and len(drafts) == 1
        assert result.agents == ready.agents and result.deliveries[order.order_id].received_at is None
    else:
        assert result.deliveries[order.order_id].status == "delivered" and len(drafts) == 2
        assert result.deliveries[order.order_id].arrived_at == result.deliveries[order.order_id].received_at


@pytest.mark.parametrize("kind,field", [("pickup", "arrived_at"), ("pickup", "received_at"),
                                       ("delivery", "arrived_at"), ("delivery", "received_at"),
                                       ("station_pickup", "arrived_at")])
def test_order_timestamps_match_actual_fulfillment_on_restore(tmp_path, kind, field):
    semantics, state = system_world(tmp_path, shipping=90000 if kind == "station_pickup" else 3600)
    if kind == "pickup":
        place(state, 1, "gas")
        order = purchase_order(state, semantics, 1, fuel_request()).orders[0]
    else:
        outcome = purchase_order(state, semantics, 1, parcel_request(semantics, state))
        order = deliver_order(at_time(outcome.state, outcome.orders[0].expected_at),
                              semantics, outcome.orders[0].order_id).order
    document = order.model_dump(mode="json")
    DeliveryState.model_validate(document)
    document[field] += 1
    if field == "arrived_at" and document["received_at"] is not None:
        document["received_at"] += 1
    with pytest.raises(ValidationError, match="timestamp disagrees with fulfillment"):
        DeliveryState.model_validate(document)


@pytest.mark.parametrize("where,seconds,kind", [("office", 86401, "station_pickup"),
                                                (None, 86401, "station_pickup"),
                                                ("office", 86400, "delivery"), (None, 86400, None)])
def test_null_shipping_quotes_once_and_preserves_duration_after_home_redirect(tmp_path, monkeypatch,
                                                                              where, seconds, kind):
    semantics, state = system_world(tmp_path)
    place(state, 1, "office")
    if where is None:
        state.activities["journey"] = ActivityState(activity_id="journey", agent_id=1, location_id="office",
            kind="travel", status="active", started_at=state.current_time,
            planned_end_at=state.current_time + 100000, last_settled_at=state.current_time,
            elapsed_seconds=0, planned_duration_seconds=100000, origin_action_id="a", origin_decision_id="d",
            origin_request_event_id="r", destination_id="home", travel_mode="walking",
            resource_claims=set(semantics.affordances.resource_claims("travel", "walking")))
    original = type(semantics.catalog).shortest_m
    quote_address = where if where is not None else "home"
    distance = int((seconds - DELIVERY_PREP_SECONDS) * DELIVERY_METERS_PER_SECOND)
    quoted = []

    def quoted_distance(catalog, origin, destination):
        if origin == "store" and destination == quote_address:
            quoted.append(destination)
            return distance
        return original(catalog, origin, destination)

    monkeypatch.setattr(type(semantics.catalog), "shortest_m", quoted_distance)
    before = state.model_dump_json()
    if kind is None:
        monkeypatch.setattr("worldmock.life.basket.settle_money_transfer", lambda *args: pytest.fail("Payment occurred"))
        with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
            purchase_order(state, semantics, 1, order_request("store", "home", goods=("tissue",)))
        assert state.model_dump_json() == before
        return
    quote = plan_recipient_delivery(state, semantics, 1, "agent:1", "store")
    assert quoted == [quote_address]
    assert quote.kind == kind and quote.expected_at == state.current_time + seconds
    if kind == "station_pickup":
        assert quote.delivery_address_id == "home"
        assert quote.destination_id == nearest_parcel_station(semantics, "home")
        assert delivery_seconds(quote.trip.distance_meters) < seconds
        selected = tuple(location for location in semantics.scenario.locations
                         if location.location_id != quote.destination_id)
        with pytest.raises(RegistrationError, match="PARCEL_STATION_REQUIRED"):
            check_parcel_stations(replace(semantics, scenario=semantics.scenario.model_copy(update={"locations": selected})))
    else:
        assert quote.delivery_address_id == quote.destination_id == "office"
    outcome = purchase_order(state, semantics, 1,
        order_request("store", quote.destination_id, fulfillment=quote.kind.value, goods=("tissue",)))
    assert outcome.orders[0].expected_at == quote.expected_at and outcome.orders[0].fulfillment == kind


@pytest.mark.parametrize("activity", ["away", "sleep", "travel"])
def test_long_shipping_uses_recipient_home_and_arrival_has_no_receipt_effect(tmp_path, activity):
    semantics, state = system_world(tmp_path, shipping=90000)
    station = nearest_parcel_station(semantics, "home")
    other_station = next(value for value in ("parcel-a", "parcel-b") if value != station)
    place(state, 2, other_station)
    if activity != "away":
        state.activities[activity] = ActivityState(activity_id=activity, agent_id=2, location_id=other_station,
            kind="sleep" if activity == "sleep" else "travel", status="active", started_at=state.current_time,
            planned_end_at=state.current_time + 100000, last_settled_at=state.current_time,
            elapsed_seconds=0, planned_duration_seconds=100000, origin_action_id="a", origin_decision_id="d",
            origin_request_event_id="r", destination_id="office" if activity == "travel" else None,
            travel_mode="walking" if activity == "travel" else None,
            resource_claims=set(semantics.affordances.resource_claims(
                "sleep" if activity == "sleep" else "travel", "walking" if activity == "travel" else None)))
    before = state.model_dump_json()
    outcome = purchase_order(state, semantics, 1, parcel_request(semantics, state, ("rice-ball",), "agent:2"))
    order = outcome.orders[0]
    assert order.destination_id == station and order.delivery_address_id == "home"
    assert outcome.scheduled[0].agent_id == 2 and outcome.receipts == ()
    source = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                        action_id="purchase", data={"orders": [order.model_dump(mode="json")]})
    outcome.state.pending_events[0].caused_by = [source.event_id]
    if activity != "away":
        outcome.state.activities[activity] = outcome.state.activities[activity].model_copy(
            update={"last_settled_at": order.expected_at, "elapsed_seconds": 90000})
    ready = at_time(outcome.state, order.expected_at)
    arrived, drafts, wakes = process_scheduled_events(ready, ready.current_time, semantics)
    assert len(drafts) == 1 and drafts[0].agent_id == 2 and drafts[0].visibility == "agent"
    assert drafts[0].caused_by == [source.event_id] and drafts[0].event_type == "environment_event"
    assert drafts[0].data["orders"][0]["status"] == "ready_for_pickup"
    assert arrived.agents == ready.agents and arrived.accounts == ready.accounts and last_flows() == ()
    assert wakes == ({} if activity == "sleep" else {2: {"delivery_arrival"}})
    replay = apply_scheduled_effect(arrived, outcome.scheduled[0], semantics)
    assert replay.state is arrived and replay.data == {} and replay.receipts == ()
    assert state.model_dump_json() == before


def test_long_contact_and_wrong_home_address_reject_before_payment(tmp_path, monkeypatch):
    semantics, state = system_world(tmp_path, shipping=90000)
    monkeypatch.setattr("worldmock.life.basket.settle_money_transfer", lambda *args: pytest.fail("Payment occurred"))
    before = state.model_dump_json()
    for recipient, destination in (("contact-colleague", "office"), ("agent:2", "office")):
        with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
            purchase_order(state, semantics, 1,
                           order_request("store", destination, goods=("tissue",), recipient=recipient))
    assert state.model_dump_json() == before


def test_same_purchase_splits_notifications_by_recipient_without_extra_fee(tmp_path, monkeypatch):
    import worldmock.life.basket as basket

    semantics, state = system_world(tmp_path, shipping=90000)
    calls = []
    original = basket.settle_money_transfer
    monkeypatch.setattr(basket, "settle_money_transfer", lambda *args: (calls.append(args), original(*args))[1])
    request = PurchaseOrderRequest(items=tuple(PurchaseItem(store_id="store", goods_id="tissue", quantity=1,
                                  recipient_id=f"agent:{identity}") for identity in (1, 2)),
                                  fulfillment="station_pickup", destination_id=nearest_parcel_station(semantics, "home"),
                                  app_id="meituan", payment_app_id="alipay")
    outcome = purchase_order(state, semantics, 1, request)
    assert len(calls) == 1 and outcome.total_price_fen == 1700
    assert [order.delivery_fee_fen for order in outcome.orders] == [500, 0]
    assert len(outcome.scheduled) == 2 and {event.agent_id for event in outcome.scheduled} == {1, 2}
    arrived, drafts, wakes = process_scheduled_events(at_time(outcome.state, outcome.orders[0].expected_at),
                                                     outcome.orders[0].expected_at, semantics)
    assert len(drafts) == 2 and {event.agent_id for event in drafts} == {1, 2}
    assert arrived.accounts == outcome.state.accounts and wakes == {1: {"delivery_arrival"}, 2: {"delivery_arrival"}}


def test_collection_receives_only_all_self_orders_and_has_true_result_cause_after_restore(tmp_path):
    semantics, state = system_world(tmp_path, shipping=90000)
    first = purchase_order(state, semantics, 1, parcel_request(semantics, state, ("rice-ball", "tissue")))
    other = purchase_order(first.state, semantics, 1,
                           parcel_request(semantics, first.state, ("rice-ball",), "agent:2"))
    arrived, arrival_events, _ = process_scheduled_events(at_time(other.state, first.orders[0].expected_at),
                                                         first.orders[0].expected_at, semantics)
    station = first.orders[0].destination_id
    place(arrived, 1, station)
    before = arrived.model_dump_json()
    restore = WorldState.model_validate_json(before)
    outcome = collect_parcels(restore, semantics, 1, station)
    assert len(outcome.orders) == len(outcome.receipts) == 2
    assert {order.order_id for order in outcome.orders} == {order.order_id for order in first.orders}
    assert outcome.state.deliveries[other.orders[0].order_id].status == "ready_for_pickup"
    assert all(order.received_at == restore.current_time >= order.arrived_at for order in outcome.orders)
    assert outcome.state.accounts == restore.accounts and outcome.state.activities == restore.activities
    assert outcome.state.current_time == restore.current_time and last_flows() == ()
    assert all("inventory" not in agent.model_dump() for agent in outcome.state.agents.values())
    assert "contact_holdings" not in outcome.state.model_dump()
    source = EventDraft(event_type="action_result", occurred_at=restore.current_time, agent_id=1,
                        action_id="collect", data={"orders": [item.model_dump(mode="json") for item in outcome.orders]})
    for receipt in outcome.receipts:
        receipt.caused_by = [source.event_id]
        assert receipt.agent_id == 1 and receipt.data["kind"] == "goods_received"
        assert receipt.caused_by != [other.scheduled[0].scheduled_id]
    final = WorldState.model_validate_json(outcome.state.model_dump_json())
    with pytest.raises(PreconditionError, match="PARCEL_NOT_READY"):
        collect_parcels(final, semantics, 1, station)
    for order in outcome.orders:
        replay = settle_receipt(final, semantics, order.order_id)
        assert replay.state is final and replay.receipts == ()
    assert arrived.model_dump_json() == before


def test_collection_away_pending_and_another_person_orders_never_receive(tmp_path):
    semantics, state = system_world(tmp_path, shipping=90000)
    outcome = purchase_order(state, semantics, 1, parcel_request(semantics, state, ("rice-ball",), "agent:2"))
    station = outcome.orders[0].destination_id
    place(outcome.state, 2, station)
    before = outcome.state.model_dump_json()
    with pytest.raises(PreconditionError, match="PARCEL_NOT_READY"):
        collect_parcels(outcome.state, semantics, 2, station)
    assert outcome.state.model_dump_json() == before
    arrived, _, _ = process_scheduled_events(at_time(outcome.state, outcome.orders[0].expected_at),
                                             outcome.orders[0].expected_at, semantics)
    place(arrived, 1, station)
    place(arrived, 2, "office")
    before = arrived.model_dump_json()
    for identity, code in ((1, "PARCEL_NOT_READY"), (2, "ACTIVITY_PRECONDITION_UNMET")):
        with pytest.raises(PreconditionError, match=code):
            collect_parcels(arrived, semantics, identity, station)
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET"):
        settle_receipt(arrived, semantics, outcome.orders[0].order_id)
    assert arrived.model_dump_json() == before


def test_missing_closest_selected_station_rejects_instead_of_choosing_farther(tmp_path):
    semantics, _ = system_world(tmp_path, shipping=90000)
    station = nearest_parcel_station(semantics, "home")
    scenario = semantics.scenario.model_copy(update={"locations": tuple(location for location in semantics.scenario.locations
                                                                       if location.location_id != station)})
    with pytest.raises(RegistrationError, match="PARCEL_STATION_REQUIRED"):
        check_parcel_stations(replace(semantics, scenario=scenario))
    catalog = replace(semantics.catalog, locations={key: value for key, value in semantics.catalog.locations.items()
                                                  if value.kind != "parcel_station"})
    with pytest.raises(RegistrationError, match="PARCEL_STATION_REQUIRED"):
        check_parcel_stations(replace(semantics, catalog=catalog))


def test_closest_station_distance_tie_uses_location_identity_on_registered_map(tmp_path):
    semantics, _ = system_world(tmp_path, shipping=90000)
    directory = tmp_path / "environment/catalog"
    graph = generate_map(tuple(semantics.catalog.locations), 2386)
    write_payload(directory, "map.json", graph.model_dump(mode="json"))
    semantics, _ = load_runtime_scenario(tmp_path / "environment/scenarios/multi-person.json")
    assert semantics.catalog.shortest_m("home", "parcel-a") == semantics.catalog.shortest_m("home", "parcel-b")
    assert nearest_parcel_station(semantics, "home") == "parcel-a"


def test_different_recipient_homes_same_station_share_one_payment_and_fee(tmp_path, monkeypatch):
    semantics, _ = system_world(tmp_path, shipping=90000)
    directory = tmp_path / "environment/catalog"
    locations = read_payload(directory, "locations.json")
    locations["locations"].append({"location_id": "home-2", "name": "第二住处", "kind": "home",
                                    "app_ids": [], "shipping_seconds": None})
    locations["locations"].sort(key=lambda row: row["location_id"])
    write_payload(directory, "locations.json", locations)
    graph = generate_map([row["location_id"] for row in locations["locations"]], 7)
    write_payload(directory, "map.json", graph.model_dump(mode="json"))
    path = tmp_path / "environment/scenarios/multi-person.json"
    document = json.loads(path.read_bytes())
    document["agents"][1]["home_location_id"] = "home-2"
    document["locations"].append({"location_id": "home-2"})
    path.write_bytes(canonical_json(document) + b"\n")
    semantics, digest = load_runtime_scenario(path)
    state = _initial_state(semantics, digest, "different-homes")
    bindings = [{"store_id": "store", "goods_id": "tissue", "recipient_id": f"agent:{identity}",
                 "app_id": "meituan", "payment_app_id": "alipay", "destination_id": "parcel-a",
                 "selected_action": {"fulfillment": "station_pickup"}}
                for identity in (1, 2)]
    requests = order_requests(state, semantics, 1, bindings)
    assert len(requests) == 1 and requests[0].destination_id == "parcel-a"
    calls = []
    from worldmock.life import basket

    original = basket.settle_money_transfer
    monkeypatch.setattr(basket, "settle_money_transfer", lambda *args: (calls.append(args), original(*args))[1])
    outcome = purchase_order(state, semantics, 1, requests[0])
    assert len(calls) == 1 and outcome.total_price_fen == 1700
    assert [order.delivery_address_id for order in outcome.orders] == ["home", "home-2"]
    assert {order.destination_id for order in outcome.orders} == {"parcel-a"}
    assert len(outcome.scheduled) == 2 and sum(order.delivery_fee_fen for order in outcome.orders) == 500


@pytest.mark.parametrize("update", [{"owns_car": "true"}, {"owns_car": 1}, {"initial_driving_days": True},
                                    {"initial_driving_days": -1}, {"initial_driving_days": 1.0},
                                    {"owns_car": False, "initial_driving_days": 1}])
def test_car_environment_fields_are_strict_and_never_inferred_from_story(tmp_path, update):
    semantics, _ = system_world(tmp_path)
    payload = semantics.scenario.agents[0].model_dump(mode="json")
    payload["background_story"] = "This persona owns a car in their background story. " * 4
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**payload, **update})
    for missing in ("owns_car", "initial_driving_days"):
        with pytest.raises(ValidationError):
            AgentConfig.model_validate({key: value for key, value in payload.items() if key != missing})


def test_driving_admission_no_car_and_cross_midnight_balance_are_atomic(tmp_path):
    semantics, state = system_world(tmp_path, owns_car=False, days=0)
    with pytest.raises(PreconditionError, match="CAR_REQUIRED"):
        require_driving_days(state, semantics, 1, state.current_time + 60)
    configs = tuple(config.model_copy(update={"owns_car": True}) for config in semantics.scenario.agents)
    semantics = replace(semantics, scenario=semantics.scenario.model_copy(update={"agents": configs}))
    state.current_time = stamp("2026-01-05T23:59:59+08:00")
    state.agents[1].driving_days = 1
    before = state.model_dump_json()
    require_driving_days(state, semantics, 1, state.current_time + 1)
    with pytest.raises(PreconditionError, match="DRIVING_DAYS_EXHAUSTED"):
        require_driving_days(state, semantics, 1, state.current_time + 2)
    assert state.model_dump_json() == before


def test_actual_driving_dates_zero_repeat_midnight_and_refuel_do_not_double_charge(tmp_path):
    semantics, state = system_world(tmp_path)
    start = stamp("2026-01-05T23:59:59+08:00")
    assert charge_driving_days(state, semantics, 1, start, start) is state
    first = charge_driving_days(state, semantics, 1, start, start + 1)
    assert first.agents[1].driving_days == 1 and first.agents[1].last_driving_date == "2026-01-05"
    assert charge_driving_days(first, semantics, 1, start, start + 1) is first
    second = charge_driving_days(first, semantics, 1, start + 1, start + 2)
    assert second.agents[1].driving_days == 0 and second.agents[1].last_driving_date == "2026-01-06"
    second.current_time = start + 2
    require_driving_days(second, semantics, 1, start + 60)
    place(second, 1, "gas")
    refueled = purchase_order(second, semantics, 1, fuel_request()).state
    assert refueled.agents[1].driving_days == 7 and refueled.agents[1].last_driving_date == "2026-01-06"
    assert charge_driving_days(refueled, semantics, 1, start + 2, start + 60) is refueled
    restored = WorldState.model_validate_json(refueled.model_dump_json())
    assert charge_driving_days(restored, semantics, 1, start + 2, start + 60) is restored
    spanning = charge_driving_days(state, semantics, 1, start, start + 2)
    assert spanning.agents[1].driving_days == 0 and spanning.agents[1].last_driving_date == "2026-01-06"
    assert charge_driving_days(spanning, semantics, 1, start, start + 2) is spanning


@pytest.mark.parametrize("start,end,dates", [
    ("2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", ("2026-03-08",)),
    ("2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00", ("2026-11-01",)),
    ("2026-03-08T23:59:59-04:00", "2026-03-09T00:00:01-04:00", ("2026-03-08", "2026-03-09")),
])
def test_driving_days_use_local_dates_across_daylight_saving(start, end, dates):
    assert driving_dates(stamp(start), stamp(end), "America/New_York") == dates


@pytest.mark.parametrize("at", ["2026-01-05T10:00:00", "2026-01-05T10:00:00.0+08:00", "2026-01-05",
                                "2026-02-30T10:00:00+08:00", "2026-01-05T10:00:00+99:00",
                                "2026-01-05T10:00:00+08:60", 123, True])
def test_alarm_model_rejects_non_whole_second_or_unzoned_values(at):
    with pytest.raises(ValidationError):
        SetAlarmRequest.model_validate({"at": at})
    with pytest.raises(ValidationError):
        SetAlarmRequest.model_validate({"at": "2026-01-05T10:00:00+08:00", "app": None})


@pytest.mark.parametrize("offset", [0, -1, 172800, 172801])
def test_alarm_bounds_reject_atomically(tmp_path, offset):
    semantics, state = system_world(tmp_path)
    at = datetime.fromtimestamp(state.current_time + offset, semantics.scenario.start_at.tzinfo).isoformat()
    pending, _ = pending_alarm(state, at)
    before = state.model_dump_json()
    with pytest.raises(PreconditionError, match="ALARM_TIME_INVALID"):
        set_alarm(state, pending, SetAlarmRequest(at=at))
    assert state.model_dump_json() == before


def test_alarm_same_second_sources_and_checkpoint_restore_ring_once(tmp_path):
    semantics, state = system_world(tmp_path)
    at = "2026-01-05T00:01:00+08:00"
    pending, requested = pending_alarm(state, at)
    scheduled, result = set_alarm(state, pending, SetAlarmRequest(at=at))
    assert result == {"alarm_id": "alarm-wake", "at": at}
    assert scheduled.pending_events[0].data == {"request_event_id": requested.event_id}
    replay, same_result = set_alarm(scheduled, pending, SetAlarmRequest(at=at))
    assert replay is scheduled and same_result == result
    source = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                        action_id=pending.action_id, data=result)
    scheduled.pending_events[0].caused_by = [source.event_id]
    other_pending, other_requested = pending_alarm(state, at, "wake-other")
    scheduled, other_result = set_alarm(scheduled, other_pending, SetAlarmRequest(at=at))
    other_source = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                              action_id=other_pending.action_id, data=other_result)
    scheduled.pending_events[1].caused_by = [other_source.event_id]
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id, "agent_ids": [1, 2]},
                                  state.model_dump(mode="json"))
    scheduled.version = state.version + 1
    store.commit(scheduled.model_dump(mode="json"), [requested, source, other_requested, other_source])
    asset = store.assets.put_json(scheduled.model_dump(mode="json"), {"kind": "test_snapshot"})
    checkpoint = store.checkpoint(scheduled.model_dump(mode="json"), {"life": asset, "agent:1": asset, "agent:2": asset})
    store.close()
    reopened = FootprintStore(tmp_path / "run")
    restored = WorldState.model_validate(reopened.get_checkpoint(checkpoint.checkpoint_id).state)
    now = alarm_time(at)
    rung, drafts, wakes = process_scheduled_events(at_time(restored, now), now, semantics)
    assert wakes == {1: {"alarm_ring"}} and len(drafts) == 2 and rung.pending_events == []
    assert all(event.data.keys() == {"kind", "scheduled_id"} and event.data["kind"] == "alarm_ring" for event in drafts)
    assert [event.caused_by for event in drafts] == [[source.event_id], [other_source.event_id]]
    assert all(event.agent_id == 1 and event.visibility == "agent" for event in drafts)
    rung.version = restored.version + 1
    reopened.commit(rung.model_dump(mode="json"), drafts)
    after = WorldState.model_validate_json(rung.model_dump_json())
    _, no_events, no_wakes = process_scheduled_events(after, now, semantics)
    assert no_events == [] and no_wakes == {} and reopened.last_event_seq == 7
    reopened.close()
