"""@brief 固定图上车费经真实货币账本付给显式交通服务账户。"""

import pytest

from tests.life.test_travel_graph import travel_case
from worldmock.errors import BusinessRuleError
from worldmock.life.travel import pay_travel_fare


def test_route_quote_is_paid_once_into_the_declared_transport_account():
    _, scenario, state, trip = travel_case("driving")
    changed, receipt = pay_travel_fare(state, scenario, 1, trip)
    assert changed.accounts == {"agent:1": 9345, "transport:service": 655}
    assert state.accounts == {"agent:1": 10000, "transport:service": 0}
    assert sum(changed.accounts.values()) == sum(state.accounts.values())
    assert receipt["trip_id"] == "home->office:driving"
    assert receipt["fare_fen"] == 655 and receipt["payer_account_id"] == "agent:1"
    assert receipt["payee_account_id"] == scenario.transport_account_id
    assert receipt["resource_changes"] == {"account:agent:1": -655, "account:transport:service": 655}
    assert "route_id" not in receipt


def test_unaffordable_departure_changes_no_account():
    _, scenario, state, trip = travel_case("driving")
    state.accounts["agent:1"] = 654
    before = state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match="INSUFFICIENT_TRAVEL_FUNDS"):
        pay_travel_fare(state, scenario, 1, trip)
    assert state.model_dump(mode="json") == before


def test_walking_records_zero_fare_without_inventing_a_payment():
    _, scenario, state, trip = travel_case("walking")
    changed, receipt = pay_travel_fare(state, scenario, 1, trip)
    assert changed.accounts == state.accounts
    assert receipt["fare_fen"] == 0 and receipt["trip_id"] == "home->office:walking"
    assert not any(receipt["resource_changes"].values())
