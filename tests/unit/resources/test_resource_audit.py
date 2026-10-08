"""@brief 域级审计测试：closed 域守恒与 level 域逐主体流水对账。"""

import pytest

from worldmock.resources import (
    ConvertEntry,
    FlowEntry,
    ResourceLedgerError,
    TransferEntry,
    audit_closed_domain,
    audit_level_flows,
)

from tests.resources.conftest import AGENT, ANCHOR, EMPLOYER, ENERGY, MONEY, TRANSPORT


def transfers_for_a_day():
    """@brief 一天的货币事件：锚点注入、工资、两笔车费。"""
    return [
        TransferEntry(MONEY, ANCHOR, AGENT, 800_000, "scenario:init", 1),
        TransferEntry(MONEY, ANCHOR, EMPLOYER, 4_000_000, "scenario:init", 1),
        TransferEntry(MONEY, EMPLOYER, AGENT, 60_000, "payroll:wage", 2),
        TransferEntry(MONEY, AGENT, TRANSPORT, 555, "travel:fare", 3),
        TransferEntry(MONEY, AGENT, TRANSPORT, 706, "travel:fare", 4),
    ]


def flows_for_a_day():
    """@brief 一天的身体事件：三餐进食与活动消耗。"""
    return [
        FlowEntry(ENERGY, AGENT, "source", 185.0, "meal:milk", 1),
        FlowEntry(ENERGY, AGENT, "drain", 65.0, "met:basal", 2),
        FlowEntry(ENERGY, AGENT, "source", 800.0, "meal:lunch", 3),
    ]


def test_closed_domain_total_equals_injected_amount(ledger):
    report = audit_closed_domain(ledger, MONEY, {}, transfers_for_a_day())
    assert report.injection_total == report.real_final_total == 4_800_000
    assert report.final_total == report.initial_total == 0
    assert report.anchor_owners == frozenset({ANCHOR})


def test_closed_domain_matches_exported_final_accounts(ledger):
    expected = {AGENT: 860_239, EMPLOYER: 3_938_500, TRANSPORT: 1_261}
    entries = transfers_for_a_day() + [
        TransferEntry(MONEY, EMPLOYER, AGENT, 1_500, "bonus", 5),
    ]
    audit_closed_domain(ledger, MONEY, {}, entries, expected_final=expected)


def test_closed_domain_rejects_tampered_export(ledger):
    tampered = {AGENT: 860_239 + 1, EMPLOYER: 3_938_500, TRANSPORT: 1_261}
    entries = transfers_for_a_day() + [TransferEntry(MONEY, EMPLOYER, AGENT, 1_500, "bonus", 5)]
    with pytest.raises(ResourceLedgerError) as exc:
        audit_closed_domain(ledger, MONEY, {}, entries, expected_final=tampered)
    assert exc.value.code == "CONSERVATION_VIOLATED"


def test_closed_domain_rejects_missing_exported_owner(ledger):
    with pytest.raises(ResourceLedgerError) as exc:
        audit_closed_domain(ledger, MONEY, {}, transfers_for_a_day(),
                            expected_final={AGENT: 858_739})
    assert exc.value.code == "CONSERVATION_VIOLATED"


def test_level_flow_rows_reconcile_initial_plus_sources_minus_drains(ledger):
    initial = {(ENERGY, AGENT): 1400.0}
    rows = audit_level_flows(ledger, ENERGY, initial, flows_for_a_day())
    assert len(rows) == 1
    row = rows[0]
    assert (row.initial, row.total_source, row.total_drain) == (1400.0, 985.0, 65.0)
    assert row.expected_final == row.replayed_final == 2320.0


def test_level_audit_folds_convert_legs_into_sources_and_drains(ledger):
    entries = flows_for_a_day() + [
        ConvertEntry(inputs=[(ENERGY, AGENT, 120.0), (MONEY, EMPLOYER, 20_000)],
                     outputs=[(MONEY, AGENT, 20_000)], cause="work:contract", occurred_at=9),
    ]
    initial = {(ENERGY, AGENT): 1400.0, (MONEY, EMPLOYER): 4_000_000}
    rows = audit_level_flows(ledger, ENERGY, initial, entries)
    assert (rows[0].total_source, rows[0].total_drain, rows[0].replayed_final) == (985.0, 185.0, 2200.0)


def test_level_audit_rejects_mismatched_export(ledger):
    initial = {(ENERGY, AGENT): 1400.0}
    with pytest.raises(ResourceLedgerError) as exc:
        audit_level_flows(ledger, ENERGY, initial, flows_for_a_day(),
                          expected_final={AGENT: 2320.5})
    assert exc.value.code == "FLOW_RECONCILIATION_MISMATCH"


def test_mixed_resource_history_feeds_both_domain_audits(ledger):
    entries = transfers_for_a_day() + flows_for_a_day()
    initial = {(ENERGY, AGENT): 1400.0}
    money_report = audit_closed_domain(ledger, MONEY, initial, entries)
    energy_rows = audit_level_flows(ledger, ENERGY, initial, entries)
    assert money_report.real_final_total == 4_800_000
    assert energy_rows[0].replayed_final == 2320.0
