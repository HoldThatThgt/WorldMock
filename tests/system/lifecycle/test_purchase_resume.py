"""购买进行中写共同检查点，恢复或重放的分支沿用受理时冻结的付款方式、付款时长和购买时长。"""

import pytest

from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import purchase, stage_day_plan
from tests.life.environment_runtime import act, events, reach
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention
from worldmock.life.purchase_phases import PAYMENT, activity_phases
from worldmock.life.world_ops import _request_sha

#: 分支里人物的余额改成这个数；它不改变已经冻结的付款抽取。
BRANCH_BALANCE = 123456


def at_store(persona, config):
    persona["initial_location"] = "四季便利"


def payment_events(engine):
    return [(row.occurred_at, row.data["order_ids"], row.data["amount_fen"])
            for row in events(engine, "environment_event") if row.data.get("kind") == "purchase_payment"]


def frozen_fields(engine, activity):
    """一次购买的记录上受理时冻结的付款抽取与到目前为止写下的付款时刻。"""
    return {key: (order.app_id, order.payment_app_id, order.payment_duration_seconds, order.payment_started_at,
                  order.payment_ended_at, order.paid_at, order.expected_at)
            for key in activity.data["order_ids"] for order in (engine.context.state.deliveries[key],)}


def branch_of(parent, tmp_path, kind, source):
    """从父运行的共同检查点开出恢复分支，或带一项余额干预的重放分支。"""
    workspace = write_agent_workspace(tmp_path / "agent")
    checkpoint = parent.checkpoint({"agent:1": snapshot_directory(workspace, parent.context.store.assets)})
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=BRANCH_BALANCE,
                                              hypothesis="余额改变不影响已经冻结的付款。", source_event_ids=(source,))
    metadata = None if kind == "resume" else {
        "branch_kind": "counterfactual",
        "intervention_sha256": _request_sha("counterfactual_intervention", intervention)}
    restore_branch(parent.context.store, checkpoint.checkpoint_id, tmp_path / "branch",
                   *(() if metadata is None else (metadata,))).close()
    child = LifeEngine.open(parent.context.inputs, tmp_path / "branch")
    if kind == "replay":
        child.apply_counterfactual(intervention)
    return child


@pytest.mark.parametrize("kind", ["resume", "replay"])
def test_branch_from_a_checkpoint_inside_the_first_payment_phase_keeps_the_frozen_payments(
        engine_factory, tmp_path, kind):
    parent = engine_factory(mutate=at_store)
    stage_day_plan(parent)
    observed, result = act(parent, "three", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    activity = parent.context.state.activities[result.activity_id]
    phases = activity_phases(parent.context.state, activity)
    paying = [phase for phase in phases if phase.step == PAYMENT]
    assert len(paying) >= 2 and paying[0].started_at > activity.started_at
    reach(parent, paying[0].started_at)
    at_checkpoint = frozen_fields(parent, activity)
    assert [fields[3] for fields in at_checkpoint.values()].count(paying[0].started_at) == 1
    assert all(fields[4] is None and fields[5] is None for fields in at_checkpoint.values())
    child = branch_of(parent, tmp_path, kind, observed["observation_event_id"])
    try:
        restored = child.context.state.activities[result.activity_id]
        assert restored == parent.context.state.activities[result.activity_id]
        assert (restored.status, restored.last_settled_at) == ("active", paying[0].started_at)
        assert (restored.started_at, restored.planned_end_at) == (activity.started_at, activity.planned_end_at)
        assert frozen_fields(child, restored) == at_checkpoint
        assert activity_phases(child.context.state, restored) == phases
        reach(parent, activity.planned_end_at)
        reach(child, activity.planned_end_at)
        assert parent.context.state.activities[result.activity_id].status == "completed"
        assert child.context.state.activities[result.activity_id].status == "completed"
        finished = frozen_fields(parent, activity)
        assert frozen_fields(child, restored) == finished
        assert {fields[5] for fields in finished.values()} == {activity.planned_end_at}
        for phase in paying:
            (fields,) = [row for row in finished.values() if (row[3], row[4]) == (phase.started_at, phase.ended_at)]
            assert fields[2] == phase.ended_at - phase.started_at
        assert payment_events(child) == payment_events(parent) and len(payment_events(parent)) == 3
        spent = sum(amount for _, _, amount in payment_events(parent))
        assert parent.context.state.accounts["agent:1"] == 200000 - spent
        assert child.context.state.accounts["agent:1"] == (BRANCH_BALANCE if kind == "replay" else 200000) - spent
    finally:
        child.close()
