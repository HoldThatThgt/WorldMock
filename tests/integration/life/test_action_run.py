"""@brief 统一动作时长、真实进度、五类效果与静默接续的纯生产证据。"""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from tests.life.action_worlds import action_world, advance, begin, pin
from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.constants import CATEGORY_MET, ENTERTAINMENT_RELIEF_PER_HOUR
from worldmock.life.action_run import ActionStart, action_content_result, prepare_action, resume_action, settle_action
from worldmock.life.activities import activity_factors, change_activity
from worldmock.life.content_select import ContentRequest, eligible_contents
from worldmock.life.models import ActivityKind, ActivityStatus, ChangeActivityRequest, ContentProgress, WorldState
from worldmock.life.readable import duration_phrase
from worldmock.life.schedule import SLEEP_ALLOWED_REASONS, WakeReason
from worldmock.life.work import validate_work_start


def test_action_run_minimal_real_catalog_body_progress_and_silent_continuation():
    semantics, state = action_world()
    pin(state)
    original = state.model_dump_json()
    state, activity = begin(semantics, state)
    assert activity.kind == ActivityKind.ACTION and activity.planned_duration_seconds == 120
    assert activity.planned_end_at == activity.started_at + 120
    assert activity.next_interaction_at is None and activity.boundary_type is None
    assert state.agents[1].content_progress == {}
    assert state.random_state == json.loads(original)["random_state"]
    assert set(activity.data) == {"action_id", "category", "app_id", "manner", "communication_mode", "counterpart", "content_id",
                                  "start_position_seconds", "pinned_by_push_id", "draw_index", "played"}
    first, evidence = advance(semantics, state, activity, 120)
    progress = first.agents[1].content_progress["c-short"]
    following = first.activities[activity.activity_id]
    assert evidence == {"content_id": "c-short", "app_id": "douyin", "position_before_seconds": 0,
                        "position_after_seconds": 120, "completed": True, "completed_rounds_after": 1}
    assert progress.position_seconds == 0 and progress.completed_rounds == 1
    assert progress.last_consumed_at == first.current_time
    assert following.status == ActivityStatus.ACTIVE and following.ended_at is None
    assert following.boundary_type is None and following.data["draw_index"] == 1
    assert following.data["content_id"] != "c-short" and following.data["pinned_by_push_id"] is None
    assert following.data["played"] == [{"content_id": "c-short", "from_position": 0, "to_position": 120}]
    next_content = semantics.catalog.contents[following.data["content_id"]]
    assert following.planned_end_at == first.current_time + next_content.duration_seconds
    assert json.loads(original)["agents"]["1"]["content_progress"] == {}


@pytest.mark.parametrize("action,app,category", [
    ("quiet-rest", None, "daily_care"), ("run", "keep", "exercise"),
    ("study-course", "bilibili", "work_study"), ("office-work", None, "work_study"),
    ("voice-call", "wechat", "social"), ("small-talk", None, "social"),
])
def test_non_entertainment_completes_one_content_without_continuation(action, app, category):
    semantics, state = action_world("office" if action == "office-work" else "home")
    counterpart = "contact-colleague" if app == "wechat" else "agent:2" if category == "social" else None
    state, activity = begin(semantics, state, action, app, counterpart)
    item = semantics.catalog.contents[activity.data["content_id"]]
    changed, evidence = advance(semantics, state, activity, item.duration_seconds)
    ended = changed.activities[activity.activity_id]
    assert evidence["completed"] is True and ended.status == ActivityStatus.COMPLETED
    assert ended.boundary_type == "planned_end" and ended.ended_at == changed.current_time
    assert ended.data["draw_index"] == 0 and len(ended.data["played"]) == 1


@pytest.mark.parametrize("category,app,met", [
    ("entertainment", "douyin", 1), ("entertainment", None, 1), ("work_study", "bilibili", 1.5),
    ("social", None, 1.5), ("social", "wechat", 1.3), ("exercise", "keep", 5), ("daily_care", None, 2),
])
def test_category_effects_and_met_are_fixed_not_content_types(category, app, met):
    semantics, state = action_world()
    state, activity = begin(semantics, state)
    activity.data.update(category=category, app_id=app)
    factors = activity_factors(activity, state.agents[1], semantics.body)
    assert factors.met == met == CATEGORY_MET[category][app is not None]
    assert factors.entertainment_relief_per_hour == (ENTERTAINMENT_RELIEF_PER_HOUR if category == "entertainment" else 0)


def test_exercise_has_only_fixed_hourly_mood_effect():
    semantics, state = action_world()
    state.agents[1].body.mood_valence = 0
    state, activity = begin(semantics, state, "run", "keep")
    changed, evidence = advance(semantics, state, activity, 600)
    assert evidence["position_after_seconds"] == 600
    assert changed.agents[1].body.mood_valence == pytest.approx(0.1 * 600 / 3600)
    assert changed.agents[1].body.social_need == state.agents[1].body.social_need


def test_assigned_content_is_finite_and_completion_pulse_is_applied_once():
    semantics, state = action_world("office")
    state, activity = begin(semantics, state, "office-work", None)
    changed, evidence = advance(semantics, state, activity, 32400)
    assert evidence["completed"] is True
    actual = changed.activities[activity.activity_id]
    assert actual.status == ActivityStatus.COMPLETED
    assert actual.ended_at == changed.current_time and actual.boundary_type == "planned_end"
    assert changed.agents[1].body.mood_valence == pytest.approx(0.05)
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        begin(semantics, changed, "office-work", None, suffix="again")
    same, empty = settle_action(changed, changed.activities[activity.activity_id], semantics.catalog,
                                semantics.scenario, 32400)
    assert empty["completed"] is False and same.agents[1].body.mood_valence == changed.agents[1].body.mood_valence
    assert same.agents[1].content_progress["topic-sales"].completed_rounds == 1


def test_unassigned_study_repeats_without_completion_pulse():
    semantics, state = action_world()
    for ordinal in ("first", "second"):
        query = ContentRequest(1, "study-course", "bilibili")
        assert "c-course" in {item.content_id for item in eligible_contents(
            semantics.catalog, semantics.scenario, state, query)}
        state, activity = begin(semantics, state, "study-course", "bilibili", suffix=ordinal)
        state, evidence = advance(semantics, state, activity, 1800)
        assert evidence["completed"] is True and state.agents[1].body.mood_valence == 0
    assert state.agents[1].content_progress["c-course"].completed_rounds == 2


@pytest.mark.parametrize("location", ["home", "park", "cafe"])
def test_assigned_work_requires_actual_workplace_and_unassigned_study_does_not(location):
    semantics, state = action_world(location)
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as rejected:
        begin(semantics, state, "office-work", None)
    action_name = semantics.catalog.actions["office-work"].name
    place_name = semantics.catalog.locations["office"].display_name
    assert rejected.value.public_reason == f"{action_name} requires presence at {place_name}."
    assert rejected.value.suggested == {"next_step": f"Go to {place_name}."}
    assert begin(semantics, state, "study-course", "bilibili")[1].data["content_id"] == "c-course"
    config = semantics.scenario.agents[0]
    validate_work_start(config, semantics.catalog.contents["c-course"], location, action_name, place_name)
    with pytest.raises(PreconditionError):
        validate_work_start(config, semantics.catalog.contents["topic-sales"], location, action_name, place_name)


@pytest.mark.parametrize("location,allowed", [("home", True), ("park", True), ("office", False), ("cafe", False)])
def test_exercise_location_rule_is_shared_with_rule_engine(location, allowed):
    from worldmock.life.rules import LifeRuleEngine
    semantics, state = action_world(location)
    refusal = LifeRuleEngine(semantics.scenario, semantics.catalog).action_location_refusal("exercise", location)
    if allowed:
        assert refusal is None and begin(semantics, state, "run", "keep")[1].data["category"] == "exercise"
    else:
        with pytest.raises(BusinessRuleError, match="ELIGIBILITY_LOCATION_KIND_MISMATCH") as rejected:
            begin(semantics, state, "run", "keep")
        assert rejected.value.public_reason == refusal.reason


@pytest.mark.parametrize("action,app,counterpart,code", [
    ("browse-feed", None, None, "APP_REQUIRED"), ("browse-feed", "keep", None, "APP_NOT_OFFERED"),
    ("quiet-rest", "douyin", None, "APP_NOT_OFFERED"), ("small-talk", None, None, "COUNTERPART_REQUIRED"),
    ("run", "keep", "agent:2", "ROUTE_FIELDS_INVALID"),
])
def test_person_named_fields_are_never_filled_by_executor(action, app, counterpart, code):
    semantics, state = action_world()
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match=code):
        begin(semantics, state, action, app, counterpart)
    assert state.model_dump_json() == before


def test_actual_remaining_under_one_minute_starts_and_resume_never_selects(monkeypatch):
    semantics, state = action_world()
    state.agents[1].content_progress["c-long"] = ContentProgress(content_id="c-long", position_seconds=2399,
                                                               completed_rounds=0, last_consumed_at=state.current_time)
    state, activity = begin(semantics, state)
    assert activity.planned_duration_seconds == 1 and activity.data["content_id"] == "c-long"
    _, paused = change_activity(state.agents[1], activity, ChangeActivityRequest(activity_id=activity.activity_id,
                               operation="pause"), state.current_time, 900)
    state.activities[activity.activity_id] = paused
    state.current_time += 100
    _, resumed = change_activity(state.agents[1], paused, ChangeActivityRequest(activity_id=activity.activity_id,
                                 operation="resume"), state.current_time, 900)
    selections = []
    monkeypatch.setattr("worldmock.life.action_run.select_content",
                        lambda *args: selections.append(args) or (semantics.catalog.contents["c-long"], None))
    changed = resume_action(state, resumed, semantics.catalog, semantics.scenario)
    assert selections == []
    actual = changed.activities[activity.activity_id]
    assert actual.data["content_id"] == "c-long" and actual.media_position_seconds == 2399
    assert actual.planned_end_at == state.current_time + 1
    assert actual.elapsed_seconds == 0 and actual.data["draw_index"] == 0
    assert changed.agents[1].content_progress == state.agents[1].content_progress


def test_action_result_has_exact_six_real_content_fields():
    semantics, state = action_world()
    state, activity = begin(semantics, state, "quiet-rest", None)
    item = semantics.catalog.contents[activity.data["content_id"]]
    assert action_content_result(activity, semantics.catalog) == {
        "action": "安静休息", "app": None, "title": item.title, "summary": item.summary,
        "duration": duration_phrase(item.duration_seconds), "pushed": False}
    assert set(ActivityKind) == {ActivityKind.WAIT, ActivityKind.SLEEP, ActivityKind.TRAVEL, ActivityKind.ACTION}
    assert len(WakeReason) == 23 and len(SLEEP_ALLOWED_REASONS) == 5
    assert "content_completed" not in {item.value for item in WakeReason}


def test_content_pool_is_exact_action_app_intersection_and_empty_bindings_never_borrow():
    from random import Random
    from worldmock.life.content_select import ContentRequest, eligible_contents, select_content

    semantics, state = action_world()
    catalog = semantics.catalog
    for app_id in catalog.actions["browse-feed"].app_ids:
        query = ContentRequest(1, "browse-feed", app_id, "activity-selection")
        actual = eligible_contents(catalog, semantics.scenario, state, query)
        assert {item.content_id for item in actual} == set(catalog.contents_by_action_app[("browse-feed", app_id)])
        assert all(item.action_id == "browse-feed" and app_id in item.app_ids for item in actual)
    query = ContentRequest(1, "browse-feed", "wechat", "activity-empty")
    assert eligible_contents(catalog, semantics.scenario, state, query) == ()
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        select_content(catalog, semantics.scenario, state, query, Random(7))


def test_report_opportunity_is_consumed_for_all_due_active_activities_without_a_report():
    from worldmock.life.rhythm import consume_progress_report_opportunity
    semantics, state = action_world()
    state, first = begin(semantics, state)
    state, second = begin(semantics, state, "quiet-rest", None, suffix="second")
    paused = first.model_copy(update={"status": ActivityStatus.PAUSED, "paused_at": state.current_time}, deep=True)
    state.activities[first.activity_id] = paused
    state.current_time += 1800
    changed = consume_progress_report_opportunity(state, 1, 1800)
    assert changed.activities[second.activity_id].report_anchor_at == state.current_time
    assert changed.activities[first.activity_id].report_anchor_at == first.started_at
    assert state.activities[second.activity_id].report_anchor_at == second.started_at
    same = consume_progress_report_opportunity(changed, 1, 1800)
    assert same.model_dump(mode="json") == changed.model_dump(mode="json")


def test_content_duration_day_cap_leaves_partial_progress_and_ceil_uses_actual_position(tmp_path):
    import shutil
    from tests.catalog.conftest import read_payload, write_payload
    from tests.life.purchase_worlds import ENVIRONMENT
    from worldmock.catalog.loader import load_catalog
    from worldmock.life.semantics import assemble_semantics
    semantics, state = action_world()
    directory = tmp_path / "catalog"
    shutil.copytree(ENVIRONMENT / "catalog", directory)
    contents = read_payload(directory, "contents.json")
    rest = next(item for item in contents["contents"] if item["content_id"] == "c-rest")
    rest["duration_seconds"] = 86405
    write_payload(directory, "contents.json", contents)
    semantics = assemble_semantics(semantics.document, load_catalog(directory))
    state, activity = begin(semantics, state, "quiet-rest", None)
    assert activity.planned_duration_seconds == 86400
    changed, evidence = advance(semantics, state, activity, 86400)
    assert changed.activities[activity.activity_id].status == "completed" and evidence["completed"] is False
    assert changed.agents[1].content_progress["c-rest"].position_seconds == 86400
    changed, continuation = begin(semantics, changed, "quiet-rest", None, suffix="remaining")
    assert continuation.planned_duration_seconds == 5
    changed, evidence = advance(semantics, changed, continuation, 5)
    assert evidence["completed"] is True and changed.agents[1].content_progress["c-rest"].completed_rounds == 1


def test_mail_body_is_visible_at_real_arrival_without_reading_activity():
    from worldmock.life.content import visible_mail
    from worldmock.scheduling.compile import compile_schedule
    from worldmock.scheduling.effects import apply_scheduled_effect
    semantics, state = action_world()
    assert visible_mail(state.agents[1], {item.message_id: item for item in semantics.scenario.messages}) == []
    arrival = next(item for item in compile_schedule(semantics.scenario, state.current_time, state.end_time)
                   if item.kind == "mail_arrival")
    state.current_time = arrival.due_at
    delivered = apply_scheduled_effect(state, arrival, semantics)
    message = next(item for item in semantics.scenario.messages if item.message_id == arrival.data["message_id"])
    rows = visible_mail(delivered.state.agents[1], {item.message_id: item for item in semantics.scenario.messages})
    assert rows == [{"message_id": message.message_id, "thread_id": message.thread_id,
                     "subject": message.subject, "body": message.body}]
    assert delivered.wake == "message_arrival" and delivered.state.activities == {}
    assert "read_message_ids" not in type(delivered.state.agents[1]).model_fields


def test_work_completion_event_is_owner_visible_and_never_for_unassigned_study():
    from worldmock.life.work import work_completion_draft
    semantics, state = action_world("office")
    state, before = begin(semantics, state, "office-work", None)
    changed, _ = advance(semantics, state, before, 32400)
    draft = work_completion_draft(changed, before, semantics.catalog.contents["topic-sales"],
                                  "actual-progress", semantics.scenario.agents[0].work_content_ids)
    assert draft.data == {"kind": "work_content_completed", "content_id": "topic-sales"}
    assert draft.agent_id == 1 and draft.visibility == "agent" and draft.caused_by == ["actual-progress"]
    assert work_completion_draft(changed, before, semantics.catalog.contents["topic-sales"],
                                  "actual-progress", ()) is None
    state, study = begin(semantics, state, "study-course", "bilibili", suffix="course")
    completed, _ = advance(semantics, state, study, 1800)
    assert work_completion_draft(completed, study, semantics.catalog.contents["c-course"],
                                  "actual-progress", semantics.scenario.agents[0].work_content_ids) is None


def test_alarm_reason_is_real_allowed_sleep_wake_without_content_reason():
    from worldmock.life.schedule import WakeSnapshot, evaluate_wake_reasons
    verdict = evaluate_wake_reasons(WakeSnapshot(now=100, alarm_rang=True, is_asleep=True))
    assert verdict.wake_reasons == [WakeReason.ALARM_RING] and verdict.decision_offered is True
