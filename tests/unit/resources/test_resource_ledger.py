"""@brief 资源账本不变量测试：每条错误码至少一正一反。"""

import pytest

from worldmock.resources import (
    ConvertEntry,
    FlowEntry,
    ResourceLedger,
    ResourceLedgerError,
    ResourceMeta,
    TransferEntry,
)

from tests.resources.conftest import AGENT, ANCHOR, EMPLOYER, ENERGY, MONEY, TRANSPORT


def seed(**owners) -> dict:
    """@brief 构造 (resource, owner) 键控的初值字典。"""
    return {(MONEY, owner): value for owner, value in owners.items()}


def transfer(amount, owner_from, owner_to, cause="test", at=100):
    """@brief 货币转账分录的便捷构造。"""
    return TransferEntry(MONEY, owner_from, owner_to, amount, cause, at)


def flow(amount, owner, direction="source", cause="test", at=100, resource=ENERGY):
    """@brief 流水分录的便捷构造。"""
    return FlowEntry(resource, owner, direction, amount, cause, at)


def test_transfer_moves_amount_between_closed_accounts(ledger):
    balances = ledger.apply([transfer(500, AGENT, TRANSPORT)], seed(**{AGENT: 1000}))
    assert balances == {(MONEY, AGENT): 500, (MONEY, TRANSPORT): 500}


def test_transfer_outside_closed_domain_is_rejected(ledger):
    entry = TransferEntry(ENERGY, AGENT, EMPLOYER, 10, "test", 100)
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([entry], {})
    assert exc.value.code == "TRANSFER_NOT_CLOSED_DOMAIN"


def test_transfer_pairing_is_structurally_enforced(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        transfer(10, AGENT, AGENT)
    assert exc.value.code == "TRANSFER_UNBALANCED"


def test_flow_sources_and_drains_within_bounds(ledger):
    balances = ledger.apply([flow(185.0, AGENT), flow(65.0, AGENT, "drain")], {})
    assert balances == {(ENERGY, AGENT): 120.0}


def test_flow_on_closed_domain_is_rejected(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([flow(10, AGENT, resource=MONEY)], {})
    assert exc.value.code == "FLOW_ON_CLOSED_DOMAIN"


def test_convert_atomically_exchanges_energy_for_paid_wages(ledger):
    entries = [
        ConvertEntry(inputs=[(ENERGY, AGENT, 120.0), (MONEY, EMPLOYER, 20000)],
                     outputs=[(MONEY, AGENT, 20000)], cause="work:contract", occurred_at=200),
    ]
    balances = ledger.apply(entries, {(ENERGY, AGENT): 150.0, (MONEY, EMPLOYER): 4000000})
    assert balances == {(ENERGY, AGENT): 30.0, (MONEY, EMPLOYER): 3980000, (MONEY, AGENT): 20000}


@pytest.mark.parametrize("inputs,outputs", [
    ([(ENERGY, AGENT, 100.0)], [(MONEY, AGENT, 20), (MONEY, EMPLOYER, 20)]),
    ([(MONEY, EMPLOYER, 20), (ENERGY, AGENT, 100.0)], [(MONEY, AGENT, 25)]),
])
def test_convert_must_not_create_closed_resource_out_of_thin_air(ledger, inputs, outputs):
    initial = {(ENERGY, AGENT): 200.0, (MONEY, EMPLOYER): 1000}
    entry = ConvertEntry(inputs=inputs, outputs=outputs, cause="work", occurred_at=100)
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([entry], initial)
    assert exc.value.code == "CONVERT_IMBALANCED"
    assert initial == {(ENERGY, AGENT): 200.0, (MONEY, EMPLOYER): 1000}


def test_overdrafting_an_account_is_rejected(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([transfer(1001, AGENT, TRANSPORT)], seed(**{AGENT: 1000}))
    assert exc.value.code == "NEGATIVE_BALANCE"


def test_negative_initial_balances_are_rejected(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([], seed(**{AGENT: -1}))
    assert exc.value.code == "NEGATIVE_BALANCE"


def test_anchor_owner_absorbs_injections_may_go_negative(ledger):
    balances = ledger.apply([transfer(3_560_000, ANCHOR, AGENT)], {})
    assert balances == {(MONEY, ANCHOR): -3_560_000, (MONEY, AGENT): 3_560_000}


def test_non_anchor_owners_never_go_negative_via_tolerance_clamp(ledger):
    balances = {(ENERGY, AGENT): 1e-10}
    drained = ledger.apply([flow(1e-10, AGENT, "drain")], balances)
    assert drained == {(ENERGY, AGENT): 0}


def test_source_above_bounds_max_is_rejected(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([flow(2400.5, AGENT)], {(ENERGY, AGENT): 100.0})
    assert exc.value.code == "OUT_OF_BOUNDS"


def test_bounds_below_minimum_are_rejected():
    gauge = ResourceMeta("gauge", "level", "open", 5, 10)
    ledger = ResourceLedger([gauge])
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([FlowEntry("gauge", AGENT, "drain", 2, "test", 100)], {("gauge", AGENT): 6})
    assert exc.value.code == "OUT_OF_BOUNDS"
    with pytest.raises(ResourceLedgerError) as above:
        ledger.apply([FlowEntry("gauge", AGENT, "source", 1, "test", 100)], {("gauge", AGENT): 10})
    assert above.value.code == "OUT_OF_BOUNDS"


def test_unknown_resource_is_rejected_for_entries_and_balances(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        ledger.apply([TransferEntry("gold_grams", AGENT, TRANSPORT, 1, "test", 100)], {})
    assert exc.value.code == "UNKNOWN_RESOURCE"
    with pytest.raises(ResourceLedgerError) as seeded:
        ledger.apply([], {("gold_grams", AGENT): 1})
    assert seeded.value.code == "UNKNOWN_RESOURCE"


def test_apply_is_pure_and_returns_a_new_dict(ledger):
    initial = seed(**{AGENT: 1000})
    balances = ledger.apply([transfer(400, AGENT, TRANSPORT)], initial)
    assert initial == seed(**{AGENT: 1000}) and balances is not initial
    assert balances == {(MONEY, AGENT): 600, (MONEY, TRANSPORT): 400}


def test_validate_history_replays_full_history_to_same_final_state(ledger):
    entries = [
        transfer(800_000, ANCHOR, AGENT, "scenario:init", at=1),
        transfer(555, AGENT, TRANSPORT, "travel:fare", at=2),
        flow(185.0, AGENT, "source", "meal:milk", at=3),
        flow(65.0, AGENT, "drain", "met:basal", at=4),
    ]
    replayed = ledger.validate_history({}, entries)
    stepwise = ledger.apply(entries[2:], ledger.apply(entries[:2], {}))
    assert replayed == stepwise == {
        (MONEY, ANCHOR): -800_000, (MONEY, AGENT): 799_445, (MONEY, TRANSPORT): 555,
        (ENERGY, AGENT): 120.0}


def test_ledger_rejects_duplicate_and_malformed_meta_definitions(metas):
    with pytest.raises(ValueError):
        ResourceLedger(list(metas) + [ResourceMeta(MONEY, "account", "closed", 0, None)])
    with pytest.raises(ValueError):
        ResourceLedger([ResourceMeta(MONEY, "account", "closed", 0, None), "money_fen"])


def test_apply_rejects_unknown_entry_objects(ledger):
    with pytest.raises(ValueError):
        ledger.apply([{"resource": MONEY}], {})
