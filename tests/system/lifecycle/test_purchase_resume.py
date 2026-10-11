"""购买进行中写共同检查点，恢复或重放的分支沿用受理时冻结的付款方式、付款时长和购买时长。"""

import pytest

from tests.integration.runtime_fixtures import BRANCH_BALANCE, branch_of
from tests.life.conftest import purchase, stage_day_plan
from tests.life.environment_runtime import act, events, reach
from worldmock.life.purchase_phases import PAYMENT, activity_phases


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
