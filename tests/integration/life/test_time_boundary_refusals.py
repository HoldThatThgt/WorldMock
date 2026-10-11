"""@brief 真实运行公开时钟拒绝原子性与活动反馈节律边界。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import advance_next, context, sleep, use_app
from tests.life.environment_runtime import act, events
from worldmock.errors import ProtocolError
from worldmock.life.rhythm import RhythmBoundary, _wake_snapshot, decide_engine_rhythm
from worldmock.life.schedule import NextDecisionQuery, next_decision_time, reconsideration_interval
from worldmock.life.settlement import inject_initial_funds


def assert_unchanged(engine, before, sequence):
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == sequence


@pytest.mark.parametrize("open_round", [True, False], ids=["open-round", "decision-due"])
def test_advance_rejects_an_unfinished_decision_without_settling_or_writing(
    engine_factory, caplog, open_round
):
    engine = engine_factory()
    if open_round:
        engine.begin_decision_round([context(engine, "still-open")])
    before = engine.context.state.model_dump(mode="json")
    sequence = engine.context.store.last_event_seq
    target = datetime.fromtimestamp(before["current_time"] + 1, ZoneInfo(engine.context.config.timezone))
    expected = "open decision round" if open_round else "agent decision is due"
    with pytest.raises(ProtocolError, match=expected):
        engine.advance(1, target)
    assert expected in caplog.text
    assert_unchanged(engine, before, sequence)


@pytest.mark.parametrize("error", ["zero-tick", "wrong-tick", "wrong-target"])
def test_advance_rejects_an_inexact_boundary_without_touching_sleep(
    engine_factory, caplog, error
):
    engine = engine_factory()
    _, started = act(engine, "sleep-before-invalid-advance", sleep())
    assert started.status == "in_progress"
    scheduling = engine.scheduling_state()
    assert scheduling.ready_agent_ids == ()
    target = scheduling.next_event_at
    assert target is not None
    tick = int(target.timestamp()) - engine.context.state.current_time
    if error == "zero-tick":
        tick = 0
    elif error == "wrong-tick":
        tick += 1
    else:
        target += timedelta(seconds=1)
        tick += 1
    before = engine.context.state.model_dump(mode="json")
    sequence = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="must match the next scheduling boundary"):
        engine.advance(tick, target)
    assert "World advance must match the next scheduling boundary" in caplog.text
    assert_unchanged(engine, before, sequence)
    assert engine.context.state.activities[started.activity_id].status == "active"


def test_midnight_rhythm_cannot_be_committed_without_a_real_pending_boundary(engine_factory, caplog):
    engine = engine_factory()
    assert not engine.midnight_rhythm_pending
    before = engine.context.state.model_dump(mode="json")
    sequence = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="No midnight rhythm is pending"):
        engine.decide_midnight_rhythm()
    assert "No midnight rhythm is pending" in caplog.text
    assert_unchanged(engine, before, sequence)
    assert not engine.midnight_rhythm_pending


def test_rhythm_moves_due_feedback_forward_without_restarting_a_real_activity(engine_factory):
    def feedback_interval(persona, config):
        config["activity_feedback_seconds"] = 60

    engine = engine_factory(mutate=feedback_interval)
    _, started = act(engine, "feedback-source", use_app("抖音"))
    assert started.status == "in_progress"
    original = engine.context.state.activities[started.activity_id]
    feedback_at = original.next_interaction_at
    assert feedback_at is not None and feedback_at < original.planned_end_at
    before = engine.context.state.model_dump(mode="json")
    sequence = engine.context.store.last_event_seq
    staged = engine.context.state.model_copy(update={"current_time": feedback_at}, deep=True)
    boundary = RhythmBoundary(feedback_at, feedback_at - original.started_at, {}, [])
    changed = decide_engine_rhythm(engine.context, staged, boundary)
    actual = changed.activities[started.activity_id]
    candidate = feedback_at + engine.context.config.activity_feedback_seconds
    assert actual.next_interaction_at == (candidate if candidate < original.planned_end_at else None)
    assert actual.boundary_type is None
    assert actual.started_at == original.started_at
    assert actual.elapsed_seconds == original.elapsed_seconds
    assert actual.planned_end_at == original.planned_end_at
    assert actual.origin_action_id == original.origin_action_id
    assert actual.status == "active"
    assert staged.activities[started.activity_id] == original
    assert_unchanged(engine, before, sequence)


@pytest.mark.parametrize("initial_balance", [0, 200000], ids=["empty", "funded"])
def test_initial_funds_match_the_real_two_account_injection_and_remain_conserved(
    engine_factory, initial_balance
):
    def balance(persona, config):
        persona["initial_balance_fen"] = initial_balance

    engine = engine_factory(mutate=balance)
    state = engine.context.state
    before = state.model_dump(mode="json")
    sequence = engine.context.store.last_event_seq
    calculated = inject_initial_funds(state.accounts, state.current_time)
    assert calculated == state.accounts == {"agent:1": initial_balance, "external:scenario": -initial_balance}
    assert sum(calculated.values()) == 0
    assert all(type(value) is int for value in calculated.values())
    injections = [row for row in events(engine, "environment_event")
                  if row.data.get("kind") == "initial_funds_injected"]
    assert len(injections) == 1
    assert injections[0].source_kind == "scenario"
    assert injections[0].occurred_at == int(engine.context.config.start_time.timestamp())
    assert injections[0].resource_changes == {f"account:{name}": value for name, value in calculated.items()}
    assert_unchanged(engine, before, sequence)


@pytest.mark.parametrize("interval", [17, 37])
def test_configured_reconsideration_matches_the_real_next_boundary_and_wake(engine_factory, interval):
    def reconsideration(persona, config):
        config["reconsideration_interval_seconds"] = interval

    engine = engine_factory(mutate=reconsideration)
    _, started = act(engine, "reconsideration-source", use_app("抖音"))
    assert started.status == "in_progress"
    agent = engine.context.state.agents[1]
    activity = engine.context.state.activities[started.activity_id]
    now = engine.context.state.current_time
    query = NextDecisionQuery(now=now, planned_end=activity.planned_end_at,
        last_decision_time=agent.last_decision_time,
        reconsideration_interval_seconds=engine.context.config.reconsideration_interval_seconds)
    next_at = next_decision_time(query)
    assert next_at == agent.last_decision_time + reconsideration_interval(query)
    assert next_at == int(engine.scheduling_state().next_event_at.timestamp())
    advance_next(engine)
    actual = engine.context.state
    assert actual.current_time == next_at
    snapshot = _wake_snapshot(engine.context, actual, actual.agents[1], RhythmBoundary(next_at, interval, {}, []))
    assert reconsideration_interval(snapshot) == actual.current_time - agent.last_decision_time == interval
    assert engine.scheduling_state().ready_agent_ids == (1,)
    settlement = [row for row in events(engine, "environment_event")
                  if row.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["decision_offered"] is True
    assert "reconsideration_interval" in settlement.data["wake_reasons"]
    assert actual.activities[started.activity_id].status == "active"
    assert actual.activities[started.activity_id].elapsed_seconds == interval
