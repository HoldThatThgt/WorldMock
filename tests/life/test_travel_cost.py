import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.models import TravelMode
from worldmock.life.travel import pay_travel_fare


def payment_state(engine):
    accounts = dict(engine.context.state.accounts)
    accounts[engine.context.scenario.transport_account_id] = engine.context.semantics.accounts[
        engine.context.scenario.transport_account_id]
    return engine.context.state.model_copy(update={'accounts': accounts}, deep=True)


def test_route_quote_is_paid_once_into_the_declared_transport_account(engine_factory):
    engine = engine_factory()
    state = payment_state(engine)
    route = next(item for item in engine.context.scenario.routes if item.mode == TravelMode.DRIVING)
    payer = state.agents[1].balance_account_id
    payee = engine.context.scenario.transport_account_id
    original = state.model_dump(mode='json')
    changed, receipt = pay_travel_fare(state, engine.context.scenario, 1, route)
    assert state.model_dump(mode='json') == original
    assert changed.accounts[payer] == state.accounts[payer] - route.fare_fen
    assert changed.accounts[payee] == state.accounts[payee] + route.fare_fen
    assert sum(changed.accounts.values()) == sum(state.accounts.values())
    assert sum(receipt['resource_changes'].values()) == 0
    assert receipt['payment_timing'] == 'accepted_departure'
    assert receipt['cancellation_refund_fen'] == 0 and receipt['fare_source_kind'] == 'scenario'


def test_unaffordable_departure_changes_no_account(engine_factory):
    engine = engine_factory()
    state = payment_state(engine)
    route = next(item for item in engine.context.scenario.routes if item.mode == TravelMode.DRIVING)
    state.accounts[state.agents[1].balance_account_id] = route.fare_fen - 1
    original = state.model_dump(mode='json')
    with pytest.raises(BusinessRuleError, match='INSUFFICIENT_TRAVEL_FUNDS'):
        pay_travel_fare(state, engine.context.scenario, 1, route)
    assert state.model_dump(mode='json') == original


def test_walking_records_zero_fare_without_inventing_a_payment(engine_factory):
    engine = engine_factory()
    state = payment_state(engine)
    route = next(item for item in engine.context.scenario.routes if item.mode == TravelMode.WALKING)
    changed, receipt = pay_travel_fare(state, engine.context.scenario, 1, route)
    assert changed.accounts == state.accounts
    assert receipt['fare_fen'] == 0
