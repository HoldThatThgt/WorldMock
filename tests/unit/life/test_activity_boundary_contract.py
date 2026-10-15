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
    ActionReceipt, ActivityChange, ActivityStatus, ChangeActivityRequest, PendingAction, SameMomentStage, StoredAction,
)

MAIN_TOOL, SIDE_TOOL = "start_main_activity", "start_side_activity"


def _activity(engine, action="interact"):
    data = {"exertion": "idle", "app_id": None} if action == "interact" else {}
    params = (ActivityStartParams(action, 600, "office", "walking", {"exertion": "walking"})
              if action == "travel" else ActivityStartParams(action, 600, data=data))
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
    travel = _activity(engine, "travel").model_copy(update={"activity_id": "travel", "name": "步行"})
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

    def rejected(decision: str, action: str, tool: str = MAIN_TOOL) -> None:
        """登记一条本秒已结算的名称类拒绝；同一决策的两条开始动作属于同一组。"""
        receipt = ActionReceipt(action_id=action, round_id="round", decision_id=decision, request_sha256="a" * 64)
        state.processed_actions[action] = StoredAction(action_id=action, tool_name=tool, request_sha256="a" * 64,
            receipt=receipt.model_dump(mode="json"), result={"agent_id": 1, "occurred_at": state.current_time,
                "status": "rejected", "error_code": "NAME_NOT_FOUND"})

    rejected("decision-0", "0")
    rejected("decision-1", "1-main")
    rejected("decision-1", "1-side", SIDE_TOOL)
    assert same_moment.latest_rejection_code(state, 1) is None
    assert same_moment.rejection_pulse(state, 1, "NAME_NOT_FOUND") == same_moment.SERVICE_REFUSED
    rejected("decision-2", "2")
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

    def pending(action_id: str, tool: str, request: dict) -> PendingAction:
        """同一决策里登记的一条开始动作。"""
        return PendingAction(run_id=state.run_id, round_id="invalid", decision_id="invalid", action_id=action_id,
            agent_id=1, observed_at=state.current_time, world_state_version=state.version, order=0,
            tool_name=tool, request=request, request_sha256="a" * 64, requested_event_id="request")

    main = {"action": "interact", "target": "家", "detail": "测试休息"}
    side = {"action": "interact", "target": ["哔哩哔哩"], "prefer": None}
    groups = ([pending("main", MAIN_TOOL, {**main, "way": "work"})],
              [pending("retired", MAIN_TOOL, {"items": [{"action": "interact", "target": "家"}]})],
              [pending("main", MAIN_TOOL, main), pending("side", SIDE_TOOL, {**side, "way": "work"})],
              [pending("side", SIDE_TOOL, {**side, "way": "work"}), pending("main", MAIN_TOOL, main)],
              [pending("retired-tool", "start_activity", main)])
    for group in groups:
        with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
            engine.actions.dispatch(state, group)
        assert state.model_dump(mode="json") == before
        assert not state.activities


@pytest.mark.parametrize("action,seconds,accepted", [
    ("interact", 1, True), ("interact", 30, True), ("interact", 0, False),
    ("sleep", 59, False), ("sleep", 60, True), ("purchase", 59, False), ("travel", 1, True), ("travel", 0, False)])
def test_shortest_planned_duration_depends_on_how_the_duration_is_derived(engine_factory, action, seconds, accepted):
    """出行与内容活动的时长由路线、内容时长与共享进度派生，至少一秒即可开始（没做完的内容只剩几十秒时照样接着做）；
    睡眠与购买至少一分钟。"""
    engine = engine_factory()
    data = {"exertion": "idle", "app_id": None} if action == "interact" else {}
    params = (ActivityStartParams(action, seconds, "office", "walking", {"exertion": "walking"})
              if action == "travel" else ActivityStartParams(action, seconds, data=data))
    arguments = (engine.context.state.agents[1], params, engine.context.state.current_time,
                 ActivityOrigin("origin", "decision", "request"), engine.context.body)
    if accepted:
        assert create_activity(*arguments)[1].planned_duration_seconds == seconds
    else:
        with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT"):
            create_activity(*arguments)


def test_paused_activity_is_cancelled_at_any_later_time_while_an_active_one_needs_its_boundary(engine_factory):
    """暂停中的活动没有进行中的结算区间，之后任何时刻都可以结束；进行中的活动仍须结算到当前边界才能结束。"""
    engine = engine_factory()
    state = engine.context.state
    agent, activity = state.agents[1], _activity(engine)
    later = state.current_time + 600
    paused = activity.model_copy(update={"status": ActivityStatus.PAUSED, "paused_at": state.current_time})
    cancel = ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.CANCEL)
    _, ended = change_activity(agent, paused, cancel, later, 60)
    assert (ended.status, ended.ended_at, ended.interruption_reason) == (
        ActivityStatus.CANCELLED, later, "agent_cancelled")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        change_activity(agent, activity, cancel, later, 60)
    pause = ChangeActivityRequest(activity_id=activity.activity_id, operation=ActivityChange.PAUSE)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_FEEDBACK_NOT_DUE"):
        change_activity(agent, paused, pause, later, 60)
