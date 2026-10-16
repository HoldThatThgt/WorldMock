"""按索引的有界读取：会话下界、购买受理、零点计划输入与反事实核对读到的，与以前读整段历史时相同。"""

from tests.footprints.recording import recorded_engine, short_window
from tests.life.conftest import advance_until_ready, conclude_request, purchase, travel
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, events, finish_activity
from tests.life.phone_records import moment, phone_environment, phone_window
from tests.unit.life.test_counterfactual_rejection_contract import _branch
from worldmock.life import day_plan_inputs, session_bounds
from worldmock.life.day_plan_inputs import PlanInputs, plan_inputs
from worldmock.life.storefront_runtime import StorefrontRuntime

#: 通勤例子的时刻：08:20:00 出发，08:31:00 中途折返并随即再次出发。
DEPART, TURN = moment(8, 20), moment(8, 31)


def _full_scan_lower(store, state, session):
    """以前的会话下界：读本人在开始那一秒及以后的全部 action_result，取开启动作的第一条。"""
    if session.opened_by_action_id is not None:
        after = store.last_event_seq_before(session.started_at)
        reference = [event.event_seq for event in store.read_agent_events("action_result", session.agent_id, after)
                     if event.action_id == session.opened_by_action_id][0]
    elif session_bounds._first_session(state, session):
        return 0
    else:
        reference = store.last_event_seq_before(session.started_at) + 1
    return store.commit_last_seq(reference)


def _conclude(engine, activity_id, label):
    """给一项活动收尾（进行中的出行即中途结束）。"""
    name = engine.context.state.activities[activity_id].name
    _, result = act(engine, label, conclude_request(name), "conclude_activity")
    assert result.status == "completed", result.error_code
    return result


def test_session_lower_bounds_equal_the_full_result_scan(engine_factory, tmp_path):
    engine = engine_factory(phone_environment(tmp_path / "commute", 540),
                            mutate=phone_window((8, 0), 2, progress_report_interval_seconds=660))
    stage_moment(engine, DEPART)
    _, first = act(engine, "commute", travel("公司", "驾车"))
    assert first.status == "in_progress", first.error_code
    assert advance_until_ready(engine) and engine.context.state.current_time == TURN
    _conclude(engine, first.activity_id, "turn-back")
    _, second = act(engine, "again", travel("公司", "驾车"))
    assert second.status == "in_progress", second.error_code
    finish_activity(engine, second, "again")
    assert engine.context.state.activities[second.activity_id].status == "completed"
    store, state = engine.context.store, engine.context.state
    decided = [session for session in state.sessions.values() if session.opened_by_action_id is not None]
    assert len(decided) >= 3
    results = [event.action_id for event in events(engine, "action_result")]
    assert results.count(second.action_id) >= 2
    for session in state.sessions.values():
        assert session_bounds.session_lower(store, state, session) == _full_scan_lower(store, state, session)


def test_purchase_acceptance_lookup_starts_at_the_activity_start(engine_factory, monkeypatch):
    engine = engine_factory(mutate=short_window)
    stage_moment(engine, engine.context.state.current_time + 600)
    _, accepted = act(engine, "basket", purchase("柠檬茶", "纸巾"))
    assert accepted.status == "in_progress", accepted.error_code
    store = engine.context.store
    calls, inside, original_cause = [], [], StorefrontRuntime._accepted_cause
    original_read = store.read_agent_events

    def cause(self, activity):
        inside.append(activity)
        try:
            return original_cause(self, activity)
        finally:
            inside.pop()

    def read(event_type, agent_id, after=0):
        if inside:
            calls.append((inside[-1], event_type, agent_id, after))
        return original_read(event_type, agent_id, after)

    monkeypatch.setattr(StorefrontRuntime, "_accepted_cause", cause)
    monkeypatch.setattr(store, "read_agent_events", read)
    finish_activity(engine, accepted, "basket")
    assert calls
    for activity, event_type, agent_id, after in calls:
        assert (event_type, agent_id) == ("action_result", activity.agent_id)
        assert after == store.last_event_seq_before(activity.started_at) > 0
    acceptance = next(event for event in events(engine, "action_result")
                      if event.action_id == accepted.action_id and event.data.get("status") == "in_progress")
    payments = [event for event in events(engine, "environment_event") if event.data.get("kind") == "purchase_payment"]
    assert payments and all(event.caused_by == [acceptance.event_id] for event in payments)


def test_plan_inputs_read_each_event_type_once(engine_factory, monkeypatch):
    engine, _ = recorded_engine(engine_factory)
    store = engine.context.store
    calls, original = [], store.read_agent_events

    def read(event_type, agent_id, after=0):
        calls.append((event_type, agent_id, after))
        return original(event_type, agent_id, after)

    monkeypatch.setattr(store, "read_agent_events", read)
    plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)
    assert sorted(calls) == sorted((event_type, 1, 0) for event_type in (
        "activity_progress", "environment_event", "action_result", "action_requested"))


def test_plan_inputs_equal_parts_built_from_fresh_reads(engine_factory):
    engine, _ = recorded_engine(engine_factory)
    context, observations, pending = engine.context, engine.observations, engine.midnight_rhythm_pending
    planned = plan_inputs(context, observations, pending, 1)
    window = day_plan_inputs._review_window(context.state.current_time, context.config.timezone, pending)

    def fresh():
        return day_plan_inputs._agent_history(context.store, 1)

    experiences = day_plan_inputs._experience_sentences(context, observations, 1, fresh(), window)
    results, result_sources = day_plan_inputs._result_sentences(context, observations, 1, fresh(), window)
    conclusions, conclusion_sources = day_plan_inputs._conclusion_records(fresh(), window)
    goals, goal_sources = day_plan_inputs._previous_goals(fresh(), window)
    payload = {**planned.payload, "动作结果": results, "收尾记录": conclusions, "上一天的目标": goals}
    expected = PlanInputs(payload, experiences, (*result_sources, *conclusion_sources, *goal_sources))
    assert planned == expected
    assert planned.experiences and planned.payload["动作结果"] and planned.payload["收尾记录"]


def test_counterfactual_reads_only_the_branch_suffix(engine_factory, tmp_path, monkeypatch):
    parent = engine_factory()
    branch, intervention = _branch(parent, tmp_path, "valid")
    try:
        store = branch.context.store
        created = [event for event in store.iter_events(store.last_event_seq)
                   if event.event_type == "environment_event" and event.data.get("kind") == "branch_created"]
        assert len(created) == 1 and store.first_own_event_seq() == created[0].event_seq
        calls, original = [], store.iter_events

        def spy(cutoff, after=0):
            calls.append((cutoff, after))
            return original(cutoff, after)

        monkeypatch.setattr(store, "iter_events", spy)
        last = store.last_event_seq
        branch.apply_counterfactual(intervention)
        assert calls == [(last, created[0].event_seq - 1)]
    finally:
        branch.close()
