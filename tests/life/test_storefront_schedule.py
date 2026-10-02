"""门店报价边界、调价补货和持久化恢复的行为验收。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.basket import purchase_order
from worldmock.life.models import WorldState
from worldmock.life.scenario import PriceChangeConfig, ProductReleaseConfig
from worldmock.life.storefront_state import current_quote, initial_supply_state
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind
from tests.life.test_storefront_commerce import order_request, replace_supply, storefront_case  # noqa: F401


def at(epoch):
    """转换为场景时区的明确时刻。"""
    return datetime.fromtimestamp(epoch, ZoneInfo('Asia/Shanghai'))


def quote_interval(base, start, end, version, amount):
    """构造有独立报价版本的合法声明。"""
    return base.model_copy(update={'effective_from': at(start), 'effective_to': at(end) if end else None,
                                   'price_version': version, 'price_fen': amount}, deep=True)


def supply_event(kind, supply, due, **values):
    """构造生产事件结构，显式提供目标供给与来源。"""
    return ScheduledEvent(scheduled_id=f'test-{kind}-{due}', kind=kind, due_at=due, sequence=1,
                          data={'supply_id': supply.supply_id, 'source': supply.source.model_dump(mode='json'), **values})


def test_quote_boundaries_compile_gaps_adjacent_intervals_and_exact_end(storefront_case):
    semantics, state, supply, _, _ = storefront_case
    now = state.current_time
    base = supply.quotes[0]
    quotes = (quote_interval(base, now - 60, now + 60, 'first', 200),
              quote_interval(base, now + 120, now + 180, 'second', 300),
              quote_interval(base, now + 180, now + 240, 'third', 400),
              quote_interval(base, now + 400, None, 'after-run', 500))
    semantics = replace_supply(semantics, supply.supply_id, quotes=quotes)
    state.supply_quotes = initial_supply_state(semantics.scenario, now)['supply_quotes']
    events = [event for event in compile_schedule(semantics.scenario, now, now + 240)
              if event.kind == ScheduledKind.SUPPLY_QUOTE_CHANGE and event.data['supply_id'] == supply.supply_id]
    assert [event.due_at - now for event in events] == [60, 120, 180, 240]
    assert len({event.scheduled_id for event in events}) == 4
    assert current_quote(state, supply.supply_id).price_version == 'first'
    for event, expected in zip(events, [None, 'second', 'third', None], strict=True):
        state.current_time = event.due_at
        outcome = apply_scheduled_effect(state, event, semantics)
        actual = current_quote(outcome.state, supply.supply_id)
        assert (actual.price_version if actual else None) == expected
        assert outcome.state.supply_stock == state.supply_stock
        assert outcome.data['quote_after'] == (actual.model_dump(mode='json') if actual else None)
        state = outcome.state


def test_first_quote_starts_as_unknown_and_entire_declaration_replaces_it(storefront_case):
    semantics, state, supply, _, _ = storefront_case
    due = state.current_time + 60
    quote = quote_interval(supply.quotes[0], due, due + 600, 'future', 999)
    source = quote.source.model_copy(update={'url': 'scenario://future-quote', 'applicability': 'Future price assumption'})
    quote = quote.model_copy(update={'source': source})
    semantics = replace_supply(semantics, supply.supply_id, quotes=(quote,))
    state.supply_quotes = initial_supply_state(semantics.scenario, state.current_time)['supply_quotes']
    assert state.supply_quotes[supply.supply_id] is None
    event = next(event for event in compile_schedule(semantics.scenario, state.current_time, due + 600)
                 if event.kind == ScheduledKind.SUPPLY_QUOTE_CHANGE and event.due_at == due)
    state.current_time = due
    outcome = apply_scheduled_effect(state, event, semantics)
    actual = outcome.state.supply_quotes[supply.supply_id]
    assert actual.price_fen == 999
    assert actual.effective_from == due and actual.effective_to == due + 600
    assert actual.source == source.model_dump(mode='json')
    assert outcome.data['quote_before'] is None


def test_explicit_price_change_persists_only_selected_supply_with_own_source(storefront_case):
    semantics, state, supply, _, alternative = storefront_case
    now = state.current_time
    quote = quote_interval(supply.quotes[0], now - 60, now + 600, 'declared', 300)
    semantics = replace_supply(semantics, supply.supply_id, quotes=(quote,))
    state.supply_quotes = initial_supply_state(semantics.scenario, now)['supply_quotes']
    source = supply.source.model_copy(update={'url': 'scenario://assumed-discount', 'applicability': 'New discount assumption'})
    change = PriceChangeConfig(change_id='discount', supply_id=supply.supply_id, amount_fen=199,
                               price_version='discount-v1', due_at=at(now + 60), source=source)
    scenario = semantics.scenario.model_copy(update={'price_changes': (change,)})
    event = next(event for event in compile_schedule(scenario, now, now + 1000)
                 if event.kind == ScheduledKind.PRICE_CHANGE)
    assert event.data == {'supply_id': supply.supply_id, 'amount_fen': 199,
                          'price_version': 'discount-v1', 'source': source.model_dump(mode='json')}
    state.current_time = event.due_at
    before = state.model_dump(mode='json')
    outcome = apply_scheduled_effect(state, event, semantics)
    active = current_quote(outcome.state, supply.supply_id)
    assert (active.price_fen, active.price_version, active.effective_from, active.effective_to) == (
        199, 'discount-v1', now + 60, now + 600)
    assert active.source == source.model_dump(mode='json')
    assert active.source != quote.source.model_dump(mode='json')
    assert outcome.data['quote_before']['price_fen'] == 300
    assert outcome.state.supply_quotes[alternative.supply_id] == state.supply_quotes[alternative.supply_id]
    assert state.model_dump(mode='json') == before
    bought = purchase_order(outcome.state, semantics, 1, order_request(outcome.state, [supply]))
    assert bought.total_price_fen == 199
    assert bought.orders[0].price_version == 'discount-v1'
    assert bought.orders[0].source['quote'] == source.model_dump(mode='json')


def test_declaration_boundary_precedes_same_time_explicit_price_change(storefront_case):
    from dataclasses import replace

    semantics, state, supply, _, _ = storefront_case
    now = state.current_time
    quotes = (quote_interval(supply.quotes[0], now - 60, now + 60, 'before', 100),
              quote_interval(supply.quotes[0], now + 60, now + 120, 'after', 200))
    semantics = replace_supply(semantics, supply.supply_id, quotes=quotes)
    change = PriceChangeConfig(change_id='same-time', supply_id=supply.supply_id, amount_fen=150,
                               price_version='override', due_at=at(now + 60), source=supply.source)
    semantics = replace(semantics, scenario=semantics.scenario.model_copy(update={'price_changes': (change,)}))
    state.supply_quotes = initial_supply_state(semantics.scenario, now)['supply_quotes']
    events = [event for event in compile_schedule(semantics.scenario, now, now + 120)
              if event.due_at == now + 60 and event.kind in {
                  ScheduledKind.PRICE_CHANGE, ScheduledKind.SUPPLY_QUOTE_CHANGE}]
    assert [event.kind for event in events] == [ScheduledKind.SUPPLY_QUOTE_CHANGE, ScheduledKind.PRICE_CHANGE]
    state.current_time = now + 60
    for event in events:
        state = apply_scheduled_effect(state, event, semantics).state
    assert current_quote(state, supply.supply_id).price_fen == 150
    assert current_quote(state, supply.supply_id).effective_to == now + 120
    assert current_quote(state, supply.supply_id).price_version == 'override'


@pytest.mark.parametrize('condition,error', [
    ('unknown_supply', 'unknown supply'), ('unknown_quote', 'effective quote declaration'),
    ('expired_quote', 'effective quote declaration'), ('same_version', 'new quote version'),
    ('fact_source', 'scenario source'),
])
def test_invalid_price_change_rejects_without_world_side_effects(storefront_case, condition, error):
    semantics, state, supply, _, _ = storefront_case
    event = supply_event(ScheduledKind.PRICE_CHANGE, supply, state.current_time,
                         amount_fen=state.supply_quotes[supply.supply_id].price_fen, price_version='new')
    if condition == 'unknown_supply':
        event.data['supply_id'] = 'missing'
    elif condition == 'unknown_quote':
        state.supply_quotes[supply.supply_id] = None
    elif condition == 'expired_quote':
        state.supply_quotes[supply.supply_id].effective_to = state.current_time
    elif condition == 'same_version':
        event.data['price_version'] = state.supply_quotes[supply.supply_id].price_version
    else:
        event.data['source']['kind'] = 'fact'
    before = state.model_dump(mode='json')
    with pytest.raises(IntegrityError, match=error):
        apply_scheduled_effect(state, event, semantics)
    assert state.model_dump(mode='json') == before


def test_release_is_supply_specific_and_can_explicitly_clear_stock(storefront_case):
    semantics, state, supply, _, alternative = storefront_case
    due = state.current_time + 60
    release = ProductReleaseConfig(release_id='restock', supply_id=supply.supply_id, stock=17,
                                   due_at=at(due), source=supply.source)
    scenario = semantics.scenario.model_copy(update={'product_releases': (release,)})
    event = next(event for event in compile_schedule(scenario, state.current_time, due + 1)
                 if event.kind == ScheduledKind.PRODUCT_RELEASE)
    assert event.data == {'supply_id': supply.supply_id, 'stock': 17,
                          'source': supply.source.model_dump(mode='json')}
    outcome = apply_scheduled_effect(state, event, semantics)
    assert outcome.state.supply_stock[supply.supply_id] == 17
    assert outcome.state.supply_stock[alternative.supply_id] == state.supply_stock[alternative.supply_id]
    assert outcome.state.supply_quotes == state.supply_quotes
    assert outcome.data['stock_before'] == state.supply_stock[supply.supply_id]
    assert outcome.data['stock_after'] == 17
    event.data['stock'] = 0
    assert apply_scheduled_effect(outcome.state, event, semantics).state.supply_stock[supply.supply_id] == 0


def test_price_change_bounds_are_atomic_and_preserve_orders(storefront_case):
    """真实商品范围限定显式调价，已成交订单不会被新报价改写。"""
    semantics, state, supply, _, _ = storefront_case
    product = next(item for item in semantics.scenario.products if item.product_id == supply.product_id)
    state.agents[1].location_id = supply.location_id
    request = order_request(state, [supply], fulfillment='pickup', destination=supply.location_id)
    bought = purchase_order(state, semantics, 1, request)
    orders = {key: item.model_dump(mode='json') for key, item in bought.state.deliveries.items()}
    event = supply_event(ScheduledKind.PRICE_CHANGE, supply, state.current_time,
                         amount_fen=product.purchase_price_fen_min, price_version='bounded-change')
    changed = apply_scheduled_effect(bought.state, event, semantics).state
    assert changed.supply_quotes[supply.supply_id].price_fen == product.purchase_price_fen_min
    assert {key: item.model_dump(mode='json') for key, item in changed.deliveries.items()} == orders
    before = bought.state.model_dump(mode='json')
    for amount in (product.purchase_price_fen_min - 1, product.purchase_price_fen_max + 1):
        event.data['amount_fen'] = amount
        with pytest.raises(IntegrityError, match='outside its product price bounds'):
            apply_scheduled_effect(bought.state, event, semantics)
        assert bought.state.model_dump(mode='json') == before


@pytest.mark.parametrize('kind', [ScheduledKind.PRODUCT_RELEASE, ScheduledKind.SUPPLY_QUOTE_CHANGE])
def test_supply_event_cannot_create_undeclared_identity(storefront_case, kind):
    semantics, state, supply, _, _ = storefront_case
    event = supply_event(kind, supply, state.current_time, stock=10)
    event.data['supply_id'] = 'missing'
    before = state.model_dump(mode='json')
    with pytest.raises(IntegrityError, match='unknown supply'):
        apply_scheduled_effect(state, event, semantics)
    assert state.model_dump(mode='json') == before


def test_sqlite_reopen_keeps_price_release_opening_and_visibility_world_facts(storefront_case, tmp_path):
    semantics, initial, supply, _, _ = storefront_case
    state = initial.model_copy(deep=True)
    event = supply_event(ScheduledKind.PRICE_CHANGE, supply, state.current_time,
                         amount_fen=700, price_version='persisted-discount')
    state = apply_scheduled_effect(state, event, semantics).state
    event = supply_event(ScheduledKind.PRODUCT_RELEASE, supply, state.current_time, stock=7)
    state = apply_scheduled_effect(state, event, semantics).state
    state.opening_exceptions['market']['2026-01-05'] = [(0, 1440)]
    state.agents[1].visible_supply_ids = {supply.supply_id}
    state.agents[1].supply_browsed_at = {supply.supply_id: state.current_time}
    state.agents[1].supply_browsed_location = {supply.supply_id: 'home'}
    state.version += 1
    store = FootprintStore.create(tmp_path / 'run', {'run_id': initial.run_id}, initial.model_dump(mode='json'))
    store.commit(state.model_dump(mode='json'), [EventDraft(
        event_type='environment_event', occurred_at=state.current_time, visibility='system',
        data={'kind': 'supply_changes', 'supply_id': supply.supply_id})])
    store.close()
    reopened = FootprintStore(tmp_path / 'run')
    restored = WorldState.model_validate(reopened.state)
    reopened.close()
    assert restored.model_dump(mode='json') == state.model_dump(mode='json')
    assert current_quote(restored, supply.supply_id).price_fen == 700
    outcome = purchase_order(restored, semantics, 1, order_request(restored, [supply], quantity=2))
    assert outcome.total_price_fen == 1400
    assert outcome.state.supply_stock[supply.supply_id] == 5
    assert outcome.orders[0].price_version == 'persisted-discount'
