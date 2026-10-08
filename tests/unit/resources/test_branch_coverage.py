"""@brief C4f 分支覆盖补测：resources/models、ledger 与 audit。

分录模型拒绝形状、账本边界钳制与不变量守卫、域级审计的对账与拒付路径。
"""

from __future__ import annotations

import pytest

from worldmock.resources.audit import audit_closed_domain, audit_level_flows
from worldmock.resources.ledger import ResourceLedger
from worldmock.resources.models import (
    ConvertEntry,
    FlowEntry,
    ResourceLeg,
    ResourceLedgerError,
    ResourceMeta,
    TransferEntry,
)
from tests.resources.conftest import AGENT, ANCHOR, ENERGY, EMPLOYER, MONEY

MINT = "entertainment_need"


@pytest.fixture
def metas():
    return [
        ResourceMeta(MONEY, "account", "closed", 0, None),
        ResourceMeta(ENERGY, "level", "open", 0, 2400),
        ResourceMeta("score_pt", "level", "open", 5, 10),
    ]


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------


def test_transfer_entry_rejects_blank_owners():
    with pytest.raises(ValueError, match="from_owner must be a non-empty string"):
        TransferEntry(MONEY, "", "b", 5, "cause", 1)
    with pytest.raises(ValueError, match="to_owner must be a non-empty string"):
        TransferEntry(MONEY, "a", "", 5, "cause", 1)


def test_flow_entry_rejects_invalid_direction_and_blank_owner():
    with pytest.raises(ValueError, match="direction must be source or drain"):
        FlowEntry(ENERGY, AGENT, "sideways", 5, "cause", 1)
    with pytest.raises(ValueError, match="owner must be a non-empty string"):
        FlowEntry(ENERGY, "", "source", 5, "cause", 1)


def test_convert_entry_rejects_blank_leg_fields():
    with pytest.raises(ValueError, match="resource must be a non-empty string"):
        ConvertEntry(inputs=(ResourceLeg("", AGENT, 5),),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 5),), cause="c", occurred_at=1)
    with pytest.raises(ValueError, match="owner must be a non-empty string"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, "", 5),),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 5),), cause="c", occurred_at=1)


def test_convert_entry_rejects_non_positive_leg_amounts():
    with pytest.raises(ResourceLedgerError, match="AMOUNT_NOT_POSITIVE"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, AGENT, 0),),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 5),), cause="c", occurred_at=1)


def test_convert_entry_rejects_duplicate_and_overlapping_legs():
    with pytest.raises(ResourceLedgerError, match="duplicate leg"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, AGENT, 5), ResourceLeg(ENERGY, AGENT, 3)),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 8),), cause="c", occurred_at=1)
    with pytest.raises(ResourceLedgerError, match="overlap"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, AGENT, 5),),
                     outputs=(ResourceLeg(ENERGY, AGENT, 5),), cause="c", occurred_at=1)


def test_convert_entry_rejects_empty_sides_and_bad_cause_or_timestamp():
    with pytest.raises(ResourceLedgerError, match="must not be empty"):
        ConvertEntry(inputs=(), outputs=(ResourceLeg(MONEY, EMPLOYER, 5),),
                     cause="c", occurred_at=1)
    with pytest.raises(ResourceLedgerError, match="CAUSE_REQUIRED"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, AGENT, 5),),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 5),), cause="", occurred_at=1)
    with pytest.raises(ResourceLedgerError, match="TIMESTAMP_NOT_INTEGER"):
        ConvertEntry(inputs=(ResourceLeg(ENERGY, AGENT, 5),),
                     outputs=(ResourceLeg(MONEY, EMPLOYER, 5),), cause="c", occurred_at=1.5)


def test_resource_leg_coerce_accepts_tuples_and_rejects_others():
    leg = ResourceLeg.coerce((ENERGY, AGENT, 5))
    assert isinstance(leg, ResourceLeg) and leg.amount == 5
    assert ResourceLeg.coerce(leg) is leg
    with pytest.raises(ValueError, match="leg must be ResourceLeg"):
        ResourceLeg.coerce("bad")


def test_resource_meta_rejects_invalid_shapes():
    with pytest.raises(ValueError, match="resource_id must be a non-empty string"):
        ResourceMeta("", "account", "closed")
    with pytest.raises(ValueError, match="unknown resource kind"):
        ResourceMeta("x", "wallet", "closed")
    with pytest.raises(ValueError, match="unknown conservation domain"):
        ResourceMeta("x", "account", "sealed")
    with pytest.raises(ValueError, match="bounds_min must be a finite number"):
        ResourceMeta("x", "level", "open", float("nan"))
    with pytest.raises(ValueError, match="bounds_min cannot be negative"):
        ResourceMeta("x", "level", "open", -1)
    with pytest.raises(ValueError, match="bounds_max"):
        ResourceMeta("x", "level", "open", 5, 4)


# ---------------------------------------------------------------------------
# ledger.py
# ---------------------------------------------------------------------------


def test_ledger_exposes_registered_resource_ids(metas):
    ledger = ResourceLedger(metas)
    assert ledger.resource_ids == {MONEY, ENERGY, "score_pt"}
    assert ledger.anchor_owners == frozenset()


def test_ledger_rejects_duplicate_metas_and_non_meta_items():
    with pytest.raises(ValueError, match="duplicate resource meta"):
        ResourceLedger([ResourceMeta(MONEY, "account", "closed"),
                        ResourceMeta(MONEY, "account", "closed")])
    with pytest.raises(ValueError, match="meta must be a ResourceMeta"):
        ResourceLedger([(MONEY, "account", "closed")])


def test_ledger_rejects_invalid_initial_balances(metas):
    ledger = ResourceLedger(metas)
    with pytest.raises(ValueError, match="balance key must be"):
        ledger.apply([], {(MONEY, AGENT, "extra"): 5})
    with pytest.raises(ValueError, match="must be a finite int or float"):
        ledger.apply([], {(MONEY, AGENT): "rich"})
    with pytest.raises(ResourceLedgerError, match="UNKNOWN_RESOURCE"):
        ledger.apply([], {("ghost", AGENT): 5})


def test_ledger_clamps_tiny_float_overshoots_onto_bounds(metas):
    ledger = ResourceLedger(metas)
    balances = {(MONEY, AGENT): 100.0, (ENERGY, AGENT): 0.0, ("score_pt", AGENT): 5.0}
    entries = [
        # 小额负漂移被钳回零（NEGATIVE_BALANCE 容差分支）。
        FlowEntry(ENERGY, AGENT, "drain", 5e-10, "burn", 1),
        # 低于 bounds_min 的浮点漂移被钳回下界。
        FlowEntry("score_pt", AGENT, "drain", 5e-10, "spend", 1),
    ]
    final = ledger.apply(entries, balances)
    assert final[(ENERGY, AGENT)] == 0
    assert final[("score_pt", AGENT)] == 5.0
    # transfer 超额 5e-10 同样钳回零而不是拒绝。
    drained = ledger.apply([TransferEntry(MONEY, AGENT, EMPLOYER, 100.0 + 5e-10, "buy", 1)],
                           {(MONEY, AGENT): 100.0})
    assert drained[(MONEY, AGENT)] == 0


def test_ledger_clamps_to_bounds_max_within_tolerance(metas):
    ledger = ResourceLedger(metas)
    final = ledger.apply([FlowEntry("score_pt", AGENT, "source", 5.0 + 5e-10, "earn", 1)],
                         {("score_pt", AGENT): 5.0})
    assert final[("score_pt", AGENT)] == 10.0


def test_ledger_rejects_out_of_bounds_beyond_tolerance(metas):
    ledger = ResourceLedger(metas)
    with pytest.raises(ResourceLedgerError, match="NEGATIVE_BALANCE"):
        ledger.apply([TransferEntry(MONEY, AGENT, EMPLOYER, 200, "over", 1)],
                     {(MONEY, AGENT): 100})
    with pytest.raises(ResourceLedgerError, match="OUT_OF_BOUNDS"):
        ledger.apply([FlowEntry("score_pt", AGENT, "source", 100, "earn", 1)],
                     {("score_pt", AGENT): 5})


def test_ledger_rejects_domain_violations(metas):
    ledger = ResourceLedger(metas)
    with pytest.raises(ResourceLedgerError, match="TRANSFER_NOT_CLOSED_DOMAIN"):
        ledger.apply([TransferEntry(ENERGY, AGENT, EMPLOYER, 5, "wrong", 1)], {})
    with pytest.raises(ResourceLedgerError, match="FLOW_ON_CLOSED_DOMAIN"):
        ledger.apply([FlowEntry(MONEY, AGENT, "source", 5, "mint", 1)],
                     {(MONEY, AGENT): 0})
    with pytest.raises(ResourceLedgerError, match="UNKNOWN_RESOURCE"):
        ledger.apply([FlowEntry("ghost", AGENT, "source", 5, "x", 1)], {})


def test_ledger_rejects_unknown_entry_types(metas):
    ledger = ResourceLedger(metas)
    with pytest.raises(ValueError, match="unknown ledger entry type"):
        ledger._apply_entry({}, object())


def test_convert_rejects_imbalanced_closed_amounts(metas):
    ledger = ResourceLedger(metas, anchor_owners={ANCHOR})
    entry = ConvertEntry(inputs=(ResourceLeg(MONEY, AGENT, 100),),
                         outputs=(ResourceLeg(MONEY, ANCHOR, 90),), cause="bad", occurred_at=1)
    with pytest.raises(ResourceLedgerError, match="CONVERT_IMBALANCED"):
        ledger.apply([entry], {(MONEY, AGENT): 100, (MONEY, ANCHOR): 0})


# ---------------------------------------------------------------------------
# audit.py
# ---------------------------------------------------------------------------


def test_audit_closed_domain_rejects_total_drift_on_open_resource(metas):
    ledger = ResourceLedger(metas, anchor_owners={ANCHOR})
    initial = {(ENERGY, AGENT): 100.0}
    entries = [FlowEntry(ENERGY, AGENT, "source", 50, "meal", 1)]
    with pytest.raises(ResourceLedgerError, match="CONSERVATION_VIOLATED"):
        audit_closed_domain(ledger, ENERGY, initial, entries)


def test_audit_closed_domain_reports_injection_and_expected_final(metas):
    ledger = ResourceLedger(metas, anchor_owners={ANCHOR})
    initial = {(MONEY, AGENT): 100, (MONEY, EMPLOYER): 100}
    entries = [TransferEntry(MONEY, AGENT, EMPLOYER, 40, "buy", 1)]
    report = audit_closed_domain(ledger, MONEY, initial, entries,
                                 expected_final={AGENT: 60, EMPLOYER: 140})
    assert report.real_final_total == 200
    assert report.injection_total == 0
    with pytest.raises(ResourceLedgerError, match="CONSERVATION_VIOLATED"):
        audit_closed_domain(ledger, MONEY, initial, entries,
                            expected_final={AGENT: 999, EMPLOYER: 1})
    with pytest.raises(ResourceLedgerError, match="differ from replay"):
        audit_closed_domain(ledger, MONEY, initial, entries, expected_final={AGENT: 60})


def test_audit_level_flows_reconciles_convert_legs(metas):
    ledger = ResourceLedger(metas)
    other = "agent:2"
    initial = {(ENERGY, AGENT): 1000.0, (ENERGY, other): 1000.0, (MONEY, AGENT): 500}
    entries = [
        FlowEntry(ENERGY, AGENT, "drain", 300, "burn", 1),
        ConvertEntry(inputs=(ResourceLeg(ENERGY, other, 200),),
                     outputs=(ResourceLeg(ENERGY, AGENT, 200),), cause="refuel", occurred_at=2),
    ]
    rows = audit_level_flows(ledger, ENERGY, initial, entries)
    by_owner = {row.owner: row for row in rows}
    assert by_owner[AGENT].total_source == 200
    assert by_owner[AGENT].total_drain == 300
    assert by_owner[AGENT].expected_final == 900
    assert by_owner[AGENT].replayed_final == 900


def test_audit_level_flows_rejects_replay_disagreement(metas):
    """@brief 对 closed 资源误用 level 对账：transfer 移动无 source/drain 记录即失配。"""
    ledger = ResourceLedger(metas)
    initial = {(MONEY, AGENT): 100, (MONEY, EMPLOYER): 100}
    entries = [TransferEntry(MONEY, AGENT, EMPLOYER, 30, "move", 1)]
    with pytest.raises(ResourceLedgerError, match="FLOW_RECONCILIATION_MISMATCH"):
        audit_level_flows(ledger, MONEY, initial, entries)


def test_audit_level_flows_expected_final_mismatch_code(metas):
    ledger = ResourceLedger(metas)
    initial = {(ENERGY, AGENT): 1000.0}
    entries = [FlowEntry(ENERGY, AGENT, "drain", 100, "burn", 1)]
    rows = audit_level_flows(ledger, ENERGY, initial, entries,
                             expected_final={AGENT: 900.0})
    assert rows[0].expected_final == 900.0
    with pytest.raises(ResourceLedgerError, match="FLOW_RECONCILIATION_MISMATCH"):
        audit_level_flows(ledger, ENERGY, initial, entries, expected_final={AGENT: 1.0})
