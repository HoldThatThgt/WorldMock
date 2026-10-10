"""活动边界、固定槽位与同秒预算的真实状态反例。"""

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, same_moment, sessions
from worldmock.life.activities import (
    ActivityOrigin, ActivityStartParams, advance_activity_shared, change_activity, start_activity as create_activity,
    truncate_activity,
)
from worldmock.life.body import BodySettlement
from worldmock.life.models import (
    ActivityChange, ActivityStatus, ChangeActivityRequest, PendingAction, SameMomentStage, StoredAction, TravelMode,
)


def _activity(engine, action="interact"):
    data = {"exertion": "idle"} if action == "interact" else {}
    params = (ActivityStartParams(action, 600, "office", TravelMode.WALKING, data)
              if action == "walk" else ActivityStartParams(action, 600, data=data))
    return create_activity(engine.context.state.agents[1], params,
        engine.context.state.current_time, ActivityOrigin("origin", "decision", "request"), engine.context.body)[1]


def test_activity_creation_rejects_zero_duration_before_creating_state(engine_factory):
    engine = engine_factory()
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT"):
        create_activity(engine.context.state.agents[1], ActivityStartParams("sleep", 0),
                        engine.context.state.current_time, ActivityOrigin("a", "d", "r"), engine.context.body)
    assert not engine.context.state.activities


@pytest.mark.parametrize("case,code", [
    ("owner", "ACTIVITY_NOT_OWNED"), ("identity", "ACTIVITY_NOT_OWNED"),
    ("sleep", "ACTIVITY_PAUSE_UNAVAILABLE"), ("purchase", "ACTIVITY_PAUSE_UNAVAILABLE"),
    ("resume-active", "ACTIVITY_NOT_PAUSED"), ("unsettled", "ACTIVITY_FEEDBACK_NOT_DUE"),
    ("terminal", "ACTIVITY_ALREADY_TERMINAL"),
])
def test_control_rejects_an_invalid_boundary_without_changing_its_input(engine_factory, case, code):
    engine = engine_factory()
    state = engine.context.state
    agent = state.agents[1]
    activity = _activity(engine, "sleep" if case == "sleep" else "purchase" if case == "purchase" else "interact")
    operation = ActivityChange.RESUME if case == "resume-active" else ActivityChange.PAUSE
    identity = "foreign" if case == "identity" else activity.activity_id
    if case == "owner":
        activity = activity.model_copy(update={"agent_id": 999})
    if case == "terminal":
        activity = activity.model_copy(update={"status": ActivityStatus.COMPLETED})
    now = state.current_time + int(case == "unsettled")
    before = activity.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match=code):
        change_activity(agent, activity, ChangeActivityRequest(activity_id=identity, operation=operation), now, 60)
    assert activity.model_dump(mode="json") == before
    assert not state.activities


def test_shared_settlement_rejects_backward_time_and_terminal_cursor(engine_factory):
    engine = engine_factory()
    agent = engine.context.state.agents[1]
    activity = _activity(engine)
    settlement = BodySettlement(agent.body, agent.body, 0, 0.0, 0.0, 0.0, None)
    for candidate, now in ((activity, activity.last_settled_at - 1),
                           (activity.model_copy(update={"status": ActivityStatus.PAUSED}), activity.last_settled_at)):
        with pytest.raises(ValueError, match="settlement interval"):
            advance_activity_shared(candidate, agent, now, settlement)
    assert activity.elapsed_seconds == 0


@pytest.mark.parametrize("status", [ActivityStatus.ACTIVE, ActivityStatus.PAUSED, ActivityStatus.COMPLETED])
def test_window_truncation_preserves_actual_progress_and_completed_terminal(engine_factory, status):
    engine = engine_factory()
    agent = engine.context.state.agents[1]
    activity = _activity(engine).model_copy(update={"status": status, "elapsed_seconds": 60,
        "media_position_seconds": 60, "last_settled_at": engine.context.state.current_time + 60})
    changed, person = truncate_activity(activity, agent, engine.context.state.current_time + 60)
    assert changed.elapsed_seconds == changed.media_position_seconds == 60
    assert person.body == agent.body
    if status == ActivityStatus.COMPLETED:
        assert changed is activity and person is agent
    else:
        assert changed.status == ActivityStatus.TRUNCATED
        assert changed.ended_at == engine.context.state.current_time + 60
        assert changed.boundary_type == changed.interruption_reason == "experiment_end"


def test_slot_feedback_describes_real_occupants_and_distinguishes_pauseability(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    app = _activity(engine).model_copy(update={"name": "内容活动"})
    travel = _activity(engine, "walk").model_copy(update={"activity_id": "travel", "name": "步行"})
    state.activities = {app.activity_id: app}
    one = activity_control.slot_refusal(state, 1, 2, {})
    assert one.code == "SESSION_LIMIT_EXCEEDED" and "Only one" in one.reason
    assert "pause" in one.next_step
    state.activities[travel.activity_id] = travel
    two = activity_control.slot_refusal(state, 1, 1, {})
    assert "Both" in two.reason and "内容活动" in two.next_step and "步行" in two.next_step
    state.activities = {travel.activity_id: travel}
    travel_only = activity_control.slot_refusal(state, 1, 2, {})
    assert "pause" not in travel_only.next_step
    assert activity_control.ending_phrase(travel.model_copy(update={"status": ActivityStatus.FAILED})) == "失败"


def test_colliding_current_names_stay_unique_in_recall_and_activity_controls(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    original = _activity(engine).model_copy(update={"name": "读书"})
    state.activities = {str(index): original.model_copy(update={"activity_id": str(index)}) for index in range(12)}
    names = activity_control.visible_names(state, 1, ["读书"])["activities"]
    assert len(set(names.values())) == 12
    assert "读书（第十次）" in names.values() and "读书（第十三次）" in names.values()
    refusal = activity_control.activity_name_refusal(["不存在"], list(names.values()))
    assert refusal.items[0].error_code == "NAME_NOT_FOUND"
    assert activity_control.activity_name_refusal([next(iter(names.values()))], list(names.values())) is None


@pytest.mark.parametrize("stage,allowed", [
    (SameMomentStage.OPEN, True), (SameMomentStage.CONCLUDE_PENDING, False),
    (SameMomentStage.START_OR_RESUME, True), (SameMomentStage.CONCLUDE_ONLY, False),
])
def test_stage_permission_accepts_only_the_current_request_kind(engine_factory, stage, allowed):
    if allowed:
        same_moment.require_stage_action(stage, "start")
    else:
        with pytest.raises(BusinessRuleError, match="SAME_MOMENT_ACTION_RESTRICTED"):
            same_moment.require_stage_action(stage, "start")
    assert bool(same_moment.stage_refusal(stage, "start")) is not allowed


def test_same_second_name_feedback_is_free_twice_and_service_pulse_is_not_repeated(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    assert same_moment.rejection_pulse(state, 1, "NAME_NOT_FOUND") is None
    for index in range(3):
        state.processed_actions[str(index)] = StoredAction(action_id=str(index), tool_name="start_activity",
            request_sha256="a" * 64, receipt={}, result={"agent_id": 1, "occurred_at": state.current_time,
                "status": "rejected", "error_code": "NAME_NOT_FOUND"})
    assert same_moment.latest_rejection_code(state, 1) == "NAME_NOT_FOUND"
    assert same_moment.rejection_pulse(state, 1, "ACTION_REQUEST_INVALID") is None
    agent = state.agents[1].model_copy(update={"same_moment_rounds": 3})
    assert same_moment.next_stage(same_moment.RoundOutcome(state.current_time, agent, True, False, True)) \
        == SameMomentStage.CONCLUDE_PENDING


def test_session_switch_and_recall_labels_preserve_mobile_second_slot(engine_factory):
    engine = engine_factory()
    state = engine.context.state
    names = {key: item.display_name for key, item in engine.context.catalog.locations.items()}
    stay = sessions.current_session(state, 1)
    assert sessions.place_label(stay, names) == "家"
    assert sessions.end_transit(state, 1, names) is state
    assert not sessions.on_road(state, _activity(engine))
    transit = sessions.begin_transit(state, 1, "office", names, opened_by_action_id="departure")
    assert sessions.place_label(sessions.current_session(transit, 1), names) == "去公司的路上"
    changed = sessions.end_transit(transit, 1, names, "cancel-trip")
    assert sessions.current_session(changed, 1).kind == "stay"
    assert sessions.current_session(changed, 1).opened_by_action_id == "cancel-trip"


def test_malformed_wire_mutation_is_rejected_without_partial_dispatch(engine_factory):
    engine = engine_factory()
    state = engine.context.state
    before = state.model_dump(mode="json")
    pending = PendingAction(run_id=state.run_id, round_id="invalid", decision_id="invalid", action_id="invalid",
        agent_id=1, observed_at=state.current_time, world_state_version=state.version, order=0,
        tool_name="start_activity", request={"items": [{"action": "interact", "target": "家", "way": "work"}]},
        request_sha256="a" * 64, requested_event_id="request")
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        engine.actions.dispatch(state, pending)
    assert state.model_dump(mode="json") == before
    assert not state.activities
