"""门店供给整单结算、履约与原子性行为验收。"""

from tests.data_paths import ENVIRONMENT_ROOT

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.basket import held_quantity, purchase_order
from worldmock.life.commerce import deliver_order
from worldmock.life.models import AgentState, BodyState, PurchaseOrderRequest, WORLD_SCHEMA_VERSION, WorldState
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.life.storefront_state import initial_supply_state
from worldmock.scheduling.effects import apply_scheduled_effect


def epoch(text):
    """生成本地测试窗口中的明确时区时间。"""
    return int(datetime.fromisoformat(text).timestamp())


@pytest.fixture
def storefront_case():
    """从正式冻结地图与目录组装真实领域语义和隔离世界。"""
    semantics, digest = load_runtime_scenario(ENVIRONMENT_ROOT / 'scenarios/single-person.json')
    scenario = semantics.scenario
    now = epoch('2026-01-05T12:00:00+08:00')
    agent = AgentState(agent_id=1, name='测试人物', location_id='home', balance_account_id='agent:1',
                       body_settled_at=now,
                       body=BodyState(energy_kcal=1800, entertainment_need=0.2), next_decision_at=now)
    state = WorldState(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=now,
        end_time=int(scenario.end_at.timestamp()), run_id='storefront-domain', timezone=scenario.timezone,
        scenario_sha256=digest, content_manifest_sha256=scenario.corpus_manifest_sha256,
        agents={1: agent}, accounts=dict(semantics.accounts), random_seed=1, random_state='test-state',
        **initial_supply_state(scenario, now))
    market = [supply for supply in scenario.supplies if supply.location_id == 'market']
    primary = next(supply for supply in market
                   if supply.supply_id == 'supply-market-food-6923644266066')
    second = next(supply for supply in market
                  if supply.seller_account_id == primary.seller_account_id
                  and supply.product_id != primary.product_id)
    alternative = next(supply for supply in scenario.supplies
                       if supply.product_id == primary.product_id and supply.location_id != 'market')
    return semantics, state, primary, second, alternative


def replace_supply(semantics, supply_id, **fields):
    """在指定供给上设置测试条件，保持其他门店供给原样。"""
    supplies = tuple(item.model_copy(update=fields, deep=True) if item.supply_id == supply_id else item
                     for item in semantics.scenario.supplies)
    product_id = next(item.product_id for item in supplies if item.supply_id == supply_id)
    products = semantics.scenario.products
    if 'quotes' in fields:
        amounts = [item.price_fen for item in fields['quotes']]
        products = tuple(item.model_copy(update={
            'purchase_price_fen_min': min(100, *amounts), 'purchase_price_fen_max': max(1000, *amounts)})
            if item.product_id == product_id else item for item in products)
    return replace(semantics, scenario=semantics.scenario.model_copy(
        update={'supplies': supplies, 'products': products}, deep=True))


def replace_location(semantics, location_id, **fields):
    """在指定地点设置可复现的访问与营业条件。"""
    locations = tuple(item.model_copy(update=fields, deep=True) if item.location_id == location_id else item
                      for item in semantics.scenario.locations)
    return replace(semantics, scenario=semantics.scenario.model_copy(update={'locations': locations}, deep=True))


def order_request(state, supplies, fulfillment='delivery', destination='home', quantity=1):
    """形成明确引用当前供给报价的购买请求。"""
    return PurchaseOrderRequest(items=tuple({'supply_id': item.supply_id, 'quantity': quantity,
                                            'recipient_id': 'agent:1',
                                            'price_version': state.supply_quotes[item.supply_id].price_version}
                                           for item in supplies),
                                fulfillment=fulfillment, destination_id=destination)


def assert_rejected_unchanged(state, semantics, request, message, agent_id=1):
    """拒绝必须包含准确原因且不能改变任何输入世界字段。"""
    before = state.model_dump(mode='json')
    with pytest.raises(BusinessRuleError, match=f'^{message}$'):
        purchase_order(state, semantics, agent_id, request)
    assert state.model_dump(mode='json') == before


def test_single_supply_changes_only_selected_shop_and_preserves_sources(storefront_case):
    semantics, state, supply, _, alternative = storefront_case
    request = order_request(state, [alternative], quantity=2)
    before = state.model_dump(mode='json')
    outcome = purchase_order(state, semantics, 1, request)
    price = state.supply_quotes[alternative.supply_id].price_fen * 2
    assert outcome.total_price_fen == price
    assert outcome.state.supply_stock[alternative.supply_id] == state.supply_stock[alternative.supply_id] - 2
    assert outcome.state.supply_stock[supply.supply_id] == state.supply_stock[supply.supply_id]
    assert outcome.state.accounts['agent:1'] == state.accounts['agent:1'] - price
    assert outcome.state.accounts[alternative.seller_account_id] == state.accounts[alternative.seller_account_id] + price
    assert outcome.state.accounts[supply.seller_account_id] == state.accounts[supply.seller_account_id]
    assert sum(outcome.state.accounts.values()) == sum(state.accounts.values())
    assert outcome.state.agents[1].inventory == {}
    order = outcome.orders[0]
    assert (order.supply_id, order.product_id, order.location_id, order.seller_account_id) == (
        alternative.supply_id, alternative.product_id, alternative.location_id, alternative.seller_account_id)
    assert order.price_version == state.supply_quotes[alternative.supply_id].price_version
    assert order.source['supply'] == alternative.source.model_dump(mode='json')
    assert order.source['quote'] == state.supply_quotes[alternative.supply_id].source
    assert order.source['route']['kind'] == 'fact'
    assert state.model_dump(mode='json') == before


def test_whole_basket_uses_maximum_preparation_and_departure_traffic_once(storefront_case):
    semantics, state, primary, second, _ = storefront_case
    state.current_time = epoch('2026-01-05T07:59:00+08:00')
    semantics = replace_supply(semantics, primary.supply_id, delivery_delay_seconds=60)
    semantics = replace_supply(semantics, second.supply_id, delivery_delay_seconds=300)
    request = order_request(state, [primary, second], quantity=2)
    outcome = purchase_order(state, semantics, 1, request)
    route = next(route for route in semantics.scenario.routes
                 if route.origin_id == 'market' and route.destination_id == 'home' and route.mode == 'driving')
    departure = state.current_time + 300
    duration = semantics.travel_planned_seconds('driving', route.duration_seconds, departure)
    assert duration != semantics.travel_planned_seconds('driving', route.duration_seconds, state.current_time)
    assert {order.expected_at for order in outcome.orders} == {departure + duration}
    assert {order.route_id for order in outcome.orders} == {route.route_id}
    assert len(outcome.scheduled) == 1
    event = outcome.scheduled[0]
    assert event.data == {'order_ids': [order.order_id for order in outcome.orders]}
    assert event.due_at == departure + duration
    assert outcome.state.pending_events == [event]
    assert outcome.total_price_fen == sum(state.supply_quotes[s.supply_id].price_fen * 2
                                         for s in [primary, second])


def test_delivery_is_due_atomic_and_idempotent_with_truthful_evidence(storefront_case):
    semantics, state, primary, second, _ = storefront_case
    outcome = purchase_order(state, semantics, 1, order_request(state, [primary, second], quantity=2))
    pending = outcome.state
    before = pending.model_dump(mode='json')
    with pytest.raises(BusinessRuleError, match='^DELIVERY_NOT_DUE$'):
        deliver_order(pending, outcome.orders[0].order_id)
    assert pending.model_dump(mode='json') == before
    pending.current_time = outcome.scheduled[0].due_at
    pending.agents[1].location_id = 'office'
    delivered = apply_scheduled_effect(pending, outcome.scheduled[0], semantics)
    assert len(delivered.data['orders']) == 2
    assert len(delivered.data['inventory_changes']) == 2
    assert delivered.state.agents[1].inventory['home'] == {primary.product_id: 2, second.product_id: 2}
    assert held_quantity(delivered.state.agents[1], primary.product_id) == 0
    assert all(order.status == 'delivered' for order in delivered.state.deliveries.values())
    assert delivered.state.accounts == pending.accounts
    assert delivered.state.supply_stock == pending.supply_stock
    replay = apply_scheduled_effect(delivered.state, outcome.scheduled[0], semantics)
    assert replay.state.model_dump(mode='json') == delivered.state.model_dump(mode='json')
    assert replay.data['inventory_changes'] == []


def test_delivery_with_invalid_later_item_does_not_partially_deliver(storefront_case):
    semantics, state, primary, second, _ = storefront_case
    outcome = purchase_order(state, semantics, 1, order_request(state, [primary, second]))
    state = outcome.state
    state.current_time = outcome.scheduled[0].due_at
    event = outcome.scheduled[0].model_copy(update={'data': {'order_ids': [outcome.orders[0].order_id, 'missing']}})
    before = state.model_dump(mode='json')
    with pytest.raises(BusinessRuleError, match='^ORDER_NOT_FOUND$'):
        apply_scheduled_effect(state, event, semantics)
    assert state.model_dump(mode='json') == before


def test_pickup_is_instant_and_never_needs_delivery_route(storefront_case):
    semantics, state, primary, second, _ = storefront_case
    state.agents[1].location_id = 'market'
    semantics = replace(semantics, scenario=semantics.scenario.model_copy(update={'routes': ()}))
    outcome = purchase_order(state, semantics, 1,
                             order_request(state, [primary, second], 'pickup', 'market', quantity=2))
    assert outcome.scheduled == ()
    assert {order.expected_at for order in outcome.orders} == {state.current_time}
    assert all(order.status == 'delivered' and order.route_id is None for order in outcome.orders)
    assert outcome.state.agents[1].inventory['market'] == {primary.product_id: 2, second.product_id: 2}


def test_same_product_from_different_stores_merges_only_after_delivery(storefront_case):
    semantics, state, primary, _, alternative = storefront_case
    first = purchase_order(state, semantics, 1, order_request(state, [primary]))
    second = purchase_order(first.state, semantics, 1, order_request(first.state, [alternative], quantity=2))
    state = second.state
    state.current_time = max(event.due_at for event in state.pending_events)
    for event in state.pending_events:
        state = apply_scheduled_effect(state, event, semantics).state
    assert state.agents[1].inventory['home'][primary.product_id] == 3
    assert {order.supply_id for order in state.deliveries.values()} == {primary.supply_id, alternative.supply_id}
    assert len(state.deliveries) == 2


@pytest.mark.parametrize('case,message', [
    ('empty', 'ORDER_ITEM_COUNT_OUT_OF_RANGE'),
    ('six', 'ORDER_ITEM_COUNT_OUT_OF_RANGE'),
    ('duplicate', 'ORDER_DUPLICATE_SUPPLY'),
    ('missing', 'SUPPLY_NOT_FOUND'),
    ('seller', 'ORDER_SELLER_MISMATCH'),
    ('location', 'ORDER_LOCATION_MISMATCH'),
    ('price', 'PRICE_VERSION_MISMATCH'),
    ('stock', 'OUT_OF_STOCK'),
    ('money', 'INSUFFICIENT_FUNDS'),
])
def test_whole_order_rejections_are_atomic(storefront_case, case, message):
    semantics, state, primary, second, alternative = storefront_case
    request = order_request(state, [primary, second])
    if case == 'empty':
        request = request.model_copy(update={'items': ()})
    elif case == 'six':
        supplies = [item for item in semantics.scenario.supplies if item.location_id == 'market'][:6]
        request = order_request(state, supplies)
    elif case == 'duplicate':
        request = order_request(state, [primary, primary])
    elif case == 'missing':
        item = request.items[1].model_copy(update={'supply_id': 'missing'})
        request = request.model_copy(update={'items': (request.items[0], item)})
    elif case in {'seller', 'location'}:
        if case == 'location':
            semantics = replace_supply(semantics, alternative.supply_id, seller_account_id=primary.seller_account_id)
        request = order_request(state, [primary, alternative])
    elif case == 'price':
        item = request.items[1].model_copy(update={'price_version': 'obsolete'})
        request = request.model_copy(update={'items': (request.items[0], item)})
    elif case == 'stock':
        state.supply_stock[second.supply_id] = 0
    else:
        state.accounts['agent:1'] = sum(state.supply_quotes[x.supply_id].price_fen for x in [primary, second]) - 1
    assert_rejected_unchanged(state, semantics, request, message)


@pytest.mark.parametrize('case', ['missing', 'expired', 'future'])
def test_purchase_never_invents_price_in_unknown_interval(storefront_case, case):
    semantics, state, primary, _, _ = storefront_case
    request = order_request(state, [primary])
    quote = state.supply_quotes[primary.supply_id]
    if case == 'missing':
        state.supply_quotes[primary.supply_id] = None
    elif case == 'expired':
        quote.effective_to = state.current_time
    else:
        quote.effective_from = state.current_time + 1
    assert_rejected_unchanged(state, semantics, request, 'SUPPLY_QUOTE_UNKNOWN')


@pytest.mark.parametrize('scope,message', [('supply', 'SUPPLY_QUALIFICATION_REQUIRED'),
                                           ('location', 'LOCATION_ACCESS_DENIED')])
def test_purchase_requires_declared_supply_and_location_qualification(storefront_case, scope, message):
    semantics, state, primary, _, _ = storefront_case
    if scope == 'supply':
        semantics = replace_supply(semantics, primary.supply_id, required_qualifications=('member',))
    else:
        semantics = replace_location(semantics, 'market', required_qualifications=('member',))
    request = order_request(state, [primary])
    assert_rejected_unchanged(state, semantics, request, message)
    state.agents[1].qualifications = ('member',)
    assert purchase_order(state, semantics, 1, request).orders


@pytest.mark.parametrize('case,message', [('closed', 'MERCHANT_CLOSED'),
                                          ('unknown', 'MERCHANT_OPENING_UNKNOWN')])
def test_purchase_rechecks_actual_opening_and_distinguishes_unknown(storefront_case, case, message):
    semantics, state, primary, _, _ = storefront_case
    request = order_request(state, [primary])
    if case == 'closed':
        state.opening_exceptions['market']['2026-01-05'] = []
    else:
        semantics = replace_location(semantics, 'market', opening_calendars=())
    assert_rejected_unchanged(state, semantics, request, message)


def test_purchase_uses_minute_boundary_and_world_opening_override(storefront_case):
    semantics, state, primary, _, _ = storefront_case
    state.opening_exceptions['market']['2026-01-05'] = [(12 * 60, 12 * 60 + 1)]
    request = order_request(state, [primary])
    state.current_time += 59
    assert purchase_order(state, semantics, 1, request).orders
    state.current_time += 1
    assert_rejected_unchanged(state, semantics, request, 'MERCHANT_CLOSED')


@pytest.mark.parametrize('case,message', [
    ('remote_pickup', 'PICKUP_LOCATION_REQUIRED'), ('wrong_pickup_destination', 'PICKUP_LOCATION_REQUIRED'),
    ('unsupported', 'FULFILLMENT_NOT_SUPPORTED'), ('unknown_destination', 'DESTINATION_NOT_FOUND'),
    ('range', 'DELIVERY_DESTINATION_NOT_SUPPORTED'), ('route_missing', 'DELIVERY_ROUTE_NOT_FOUND'),
    ('route_unknown', 'DELIVERY_ROUTE_INFORMATION_UNKNOWN'), ('route_unreachable', 'DELIVERY_ROUTE_UNREACHABLE'),
])
def test_fulfillment_rejections_are_atomic(storefront_case, case, message):
    semantics, state, primary, _, _ = storefront_case
    request = order_request(state, [primary])
    if case == 'remote_pickup':
        request = order_request(state, [primary], 'pickup', 'market')
    elif case == 'wrong_pickup_destination':
        state.agents[1].location_id = 'market'
        request = order_request(state, [primary], 'pickup', 'home')
    elif case == 'unsupported':
        semantics = replace_supply(semantics, primary.supply_id, fulfillment_modes=('pickup',),
                                   delivery_destination_ids=())
    elif case == 'unknown_destination':
        request = request.model_copy(update={'destination_id': 'missing'})
    elif case == 'range':
        semantics = replace_supply(semantics, primary.supply_id, delivery_destination_ids=('office',))
    else:
        routes = []
        for route in semantics.scenario.routes:
            selected = route.origin_id == 'market' and route.destination_id == 'home' and route.mode == 'driving'
            if selected and case == 'route_missing':
                continue
            if selected:
                route = route.model_copy(update={'status': case.removeprefix('route_'),
                                                  'distance_meters': None, 'duration_seconds': None, 'fare_fen': None})
            routes.append(route)
        semantics = replace(semantics, scenario=semantics.scenario.model_copy(update={'routes': tuple(routes)}))
    assert_rejected_unchanged(state, semantics, request, message)


def test_same_location_delivery_uses_processing_time_without_self_route(storefront_case):
    semantics, state, primary, _, _ = storefront_case
    outcome = purchase_order(state, semantics, 1, order_request(state, [primary], destination='market'))
    assert outcome.orders[0].route_id is None
    assert outcome.orders[0].expected_at == state.current_time + primary.delivery_delay_seconds
    assert outcome.orders[0].status == 'pending'


def test_zero_duration_delivery_at_same_location_is_immediate_with_delivery_evidence(storefront_case):
    semantics, state, supply, _, _ = storefront_case
    semantics = replace_supply(semantics, supply.supply_id, delivery_delay_seconds=0)
    outcome = purchase_order(state, semantics, 1, order_request(state, [supply], destination='market'))
    order = outcome.orders[0]
    assert order.fulfillment == 'delivery'
    assert order.status == 'delivered'
    assert order.expected_at == state.current_time
    assert order.route_id is None
    assert outcome.scheduled == () and outcome.state.pending_events == []
    assert outcome.state.agents[1].inventory['market'][supply.product_id] == 1
    assert outcome.inventory_changes == ({'order_id': order.order_id, 'supply_id': supply.supply_id,
                                           'location_id': 'market', 'product_id': supply.product_id,
                                           'quantity': 1},)
    replayed, replay_order = deliver_order(outcome.state, order.order_id)
    assert replay_order.status == 'delivered'
    assert replayed.model_dump(mode='json') == outcome.state.model_dump(mode='json')
