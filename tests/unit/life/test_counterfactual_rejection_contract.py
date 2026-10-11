"""真实父检查点与反事实分支的来源、时序和唯一干预校验。"""

from types import SimpleNamespace

import pytest

from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import context, do_at, stage_day_plan
from tests.life.environment_runtime import act, events
from worldmock.errors import ProtocolError
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention
from worldmock.life.world_ops import _request_sha


def _branch(parent, tmp_path, case):
    stage_day_plan(parent)
    observed = parent.observe(context(parent, "parent-source"))
    source = observed["observation_event_id"]
    if case == "hidden":
        source = next(event.event_id for event in events(parent) if event.visibility == "system")
    elif case == "missing":
        source = "missing-parent-source"
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=999 if case == "agent" else 1,
        balance_fen=123456, hypothesis="资金改变可选购买。", source_event_ids=(source,))
    workspace = write_agent_workspace(tmp_path / "agent")
    checkpoint = parent.checkpoint({"agent:1": snapshot_directory(workspace, parent.context.store.assets)})
    root = tmp_path / "branch"
    store = restore_branch(parent.context.store, checkpoint.checkpoint_id, root, {
        "branch_kind": "counterfactual", "intervention_sha256": _request_sha("counterfactual_intervention", intervention)})
    store.close()
    return LifeEngine.open(parent.context.inputs, root), intervention


@pytest.mark.parametrize("case,message", [
    ("hidden", "not visible"), ("missing", "not visible"), ("agent", "target agent"),
    ("round", "precede the first decision"), ("decision", "precede the first decision"),
])
def test_invalid_counterfactual_sources_and_timing_leave_balances_and_evidence_unchanged(
        engine_factory, tmp_path, case, message):
    parent = engine_factory()
    branch, intervention = _branch(parent, tmp_path, case)
    try:
        if case == "round":
            branch.begin_decision_round([context(branch, "child-round")])
        elif case == "decision":
            _, started = act(branch, "child-start", do_at("家", "测试休息"))
            assert started.status == "in_progress"
        before = branch.context.state.model_dump(mode="json")
        sequence = branch.context.store.last_event_seq
        with pytest.raises(ProtocolError, match=message):
            branch.apply_counterfactual(intervention)
        assert branch.context.state.model_dump(mode="json") == before
        assert branch.context.store.last_event_seq == sequence
        assert not [event for event in events(branch, "environment_event")
                    if event.data.get("kind") == "counterfactual_intervention"]
    finally:
        branch.close()


def test_counterfactual_rejects_foreign_type_and_an_ordinary_run(engine_factory):
    engine = engine_factory()
    with pytest.raises(ProtocolError, match="strict life model"):
        engine.apply_counterfactual(SimpleNamespace())
    observed = engine.observe(context(engine, "source"))
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=123456,
        hypothesis="资金改变可选购买。", source_event_ids=(observed["observation_event_id"],))
    before = dict(engine.context.state.accounts)
    with pytest.raises(ProtocolError, match="branch manifest"):
        engine.apply_counterfactual(intervention)
    assert engine.context.state.accounts == before


def test_valid_counterfactual_transfers_equal_and_opposite_amount_once(engine_factory, tmp_path):
    parent = engine_factory()
    branch, intervention = _branch(parent, tmp_path, "valid")
    try:
        before = dict(branch.context.state.accounts)
        receipt = branch.apply_counterfactual(intervention)
        changed = branch.context.state.accounts
        assert receipt.before_balance_fen == before["agent:1"]
        assert receipt.after_balance_fen == changed["agent:1"] == 123456
        assert sum(changed.values()) == sum(before.values()) == 0
        event = next(row for row in events(branch, "environment_event")
                     if row.data.get("kind") == "counterfactual_intervention")
        assert event.caused_by == list(intervention.source_event_ids)
        assert sum(event.resource_changes.values()) == 0
        with pytest.raises(ProtocolError, match="occur once"):
            branch.apply_counterfactual(intervention)
        assert branch.context.state.accounts == changed
    finally:
        branch.close()
