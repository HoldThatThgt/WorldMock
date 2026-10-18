"""当前观察的结构、冻结身份、查询纠错和已核验经历投影。"""

from copy import deepcopy

import pytest

from tests.life.conftest import (
    MAIN_TOOL, SIDE_TOOL, advance_until_ready, conclude_request, context, do_at, errand_add, stage_day_plan, travel, use_app,
)
from tests.life.environment_runtime import act, act_group, reach
from worldmock.life import activity_control, tool_layer
from worldmock.life.action_contract import start_tool
from worldmock.life.activity_control import open_activities
from worldmock.errors import IntegrityError
from worldmock.life.continuation import continuation_enabled
from worldmock.life.control_items import control_errands, control_items
from worldmock.life.decision_start import is_action_request
from worldmock.life.error_contract import ErrorItem, ErrorReport, bind_report, host_defaults
from worldmock.life.name_resolution import lookup, name_book, resolution_data
from worldmock.life.progress_layer import order_label, session_bound
from worldmock.life.prompt_capacity import prompt_token_count, request_token_count
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.readable import clock_phrase
from worldmock.life.reminders import mark_reminded, reminder_due
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.settled_text import activity_sentence, experience_sentence, record_lines
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.life.tool_layer import Candidate, LayerContext, refusal_for
from worldmock.life.models import SameMomentStage


def _context(engine):
    names = {"activities": {key: row.name for key, row in engine.context.state.activities.items()}, "sessions": {}}
    return LayerContext(engine.context, 1, names, [], open_activities(engine.context.state, 1))


def test_continuation_requires_the_actual_frozen_stage_identity():
    with pytest.raises(IntegrityError, match="frozen action stage identity"):
        continuation_enabled({})


def test_control_errands_rejects_removed_or_unknown_fields():
    change = {**errand_add("买纸巾", "纸巾"), "legacy_quantity_unit": "piece"}
    with pytest.raises(IntegrityError, match="unknown fields"):
        control_errands("report_status", {"items": [], "errands": [change]})


def test_control_record_requires_the_current_items_shape():
    with pytest.raises(IntegrityError, match="request shape is not recognised"):
        control_items("report_status", {"errands": []})


def test_batch_feedback_rebinds_existing_tool_paths_without_losing_parameter_position():
    report = ErrorReport("ARGUMENTS_INVALID", "The actual tool arguments failed validation.", items=(
        ErrorItem(("tool_calls", 0, "function", "arguments", "offset"), -1, "ARGUMENTS_INVALID"),
        ErrorItem(("tool_calls",), [], "TOOL_CALL_REQUIRED")))
    rebound = bind_report(report, 3)
    assert rebound.items[0].path == ("tool_calls", 3, "function", "arguments", "offset")
    assert rebound.items[0].written == -1
    assert rebound.items[1].path == ("tool_calls",)
    assert report.items[0].path[1] == 0


def _thinking_request(*tools, **extra):
    """开了思考、带给定原生工具的模型请求体。"""
    return {"extra_body": {"thinking": {"type": "enabled"}},
            "tools": [{"type": "function", "function": {"name": name}} for name in tools], **extra}


@pytest.mark.parametrize("payload", [None, [], {"purpose": "plan_day"},
    {"extra_body": {"thinking": {"type": "enabled"}}, "tools": None},
    _thinking_request(SIDE_TOOL, "report_status"), _thinking_request("start_activity"),
    _thinking_request(MAIN_TOOL, SIDE_TOOL, purpose="plan_day"),
    {**_thinking_request(MAIN_TOOL), "extra_body": {"thinking": {"type": "disabled"}}}])
def test_only_actual_action_model_requests_start_decision_evidence(payload):
    assert is_action_request(payload) is False


def test_action_model_request_is_recognised_by_the_main_start_tool():
    """行动请求按原生 tools 里的 start_main_activity 识别，与存储侧的判定同义。"""
    assert is_action_request(_thinking_request("recall_task_summary", MAIN_TOOL, SIDE_TOOL)) is True
    assert is_action_request(_thinking_request(MAIN_TOOL)) is True


@pytest.mark.parametrize("field", ["persona_sha256", "config_sha256", "run_id"])
def test_context_rejects_identity_drift_before_any_runtime_projection(engine_factory, field):
    engine = engine_factory()
    before = engine.context.state
    changed = before.model_copy(deep=True)
    setattr(changed, field, "wrong-input" if field == "run_id" else "a" * 64)
    engine.context.state = changed
    try:
        with pytest.raises(IntegrityError, match="inputs or agent identity mismatch"):
            engine.context.validate()
    finally:
        engine.context.state = before


def test_unknown_host_code_cannot_be_rendered_as_an_invented_contract():
    with pytest.raises(ValueError, match="unknown host error"):
        host_defaults("REMOVED_WORK_TOOL")


def test_full_catalog_names_remain_callable_when_outside_the_current_window(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "hidden-catalog"))
    observed["interaction_bindings"] = []
    observed["tool_layer"]["targets"] = []
    book = name_book(observed)
    assert book.targets and all(not target.listed for target in book.targets)
    home = next(target for target in book.targets if target.label == "家")
    assert lookup(" 家 ", book.targets) == (home,)
    data = {"resolved": [{"written": "旧写法", "as": "完整名称"}]}
    combined = resolution_data(data, ({"written": " 家 ", "as": "家"},), ("实际说明",))
    assert len(combined["resolved"]) == 2 and combined["notes"] == ["实际说明"]
    assert data == {"resolved": [{"written": "旧写法", "as": "完整名称"}]}


def test_progress_layer_requires_an_active_real_session(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(update={"sessions": {}}, deep=True)
    with pytest.raises(IntegrityError, match="no active session"):
        session_bound(engine.context.store, state, 1)


def test_token_measurement_failures_and_missing_output_reserve_stop_capacity_validation(monkeypatch):
    def failed_counter(**kwargs):
        raise ValueError("tokenizer unavailable")

    monkeypatch.setattr("worldmock.life.prompt_capacity.token_counter", failed_counter)
    with pytest.raises(RuntimeError, match="token count failed"):
        prompt_token_count({"model": "test-model", "messages": []})
    for value in (None, 0, -1, "100"):
        with pytest.raises(RuntimeError, match="output reserve is missing"):
            request_token_count({"model": "test-model", "messages": [], "max_tokens": value})


@pytest.mark.parametrize("layer", [{"layer": "退役层"}, {"layer": "进度层", "events": "invalid"}])
def test_unknown_and_malformed_prompt_layers_cannot_reach_model_rendering(layer):
    with pytest.raises(ValueError, match="Unknown prompt layer|violates its schema"):
        validate_layer(layer)


def test_goal_reminder_marks_only_due_goals_and_never_delivers_twice(engine_factory):
    engine = engine_factory()
    now = engine.context.state.current_time
    goals = (PlanGoal(intent_kind="工作", goal="开始工作", registered_at=now - 60, remind_at=now),
             PlanGoal(intent_kind="休息", goal="稍后休息", registered_at=now - 60, remind_at=now + 60))
    person = engine.context.state.agents[1].model_copy(update={"day_plan": DayPlan(day_end=now + 3600, goals=goals)})
    changed = mark_reminded(person, now)
    assert changed.day_plan.goals[0].reminded_at == now
    assert changed.day_plan.goals[1].reminded_at is None
    assert not reminder_due(changed, now)
    assert mark_reminded(changed, now) is changed
    assert person.day_plan.goals[0].reminded_at is None


def test_delivery_and_actual_experience_sentences_retain_goods_times_and_travel_mode(engine_factory):
    engine = engine_factory()
    _, trip = act(engine, "trip", travel("公司"))
    reach(engine, engine.context.state.activities[trip.activity_id].planned_end_at)
    layer = _context(engine)
    now = engine.context.state.current_time
    actual = {"kind": "arrived", "activity_id": trip.activity_id, "location_id": "office",
              "access_status": "denied", "occurred_at": now}
    assert "步行到了公司，但没能进去" in experience_sentence(layer, actual)
    receipt = {"kind": "received", "goods_id": "tissue", "quantity": 1, "occurred_at": now}
    assert "收到纸巾 1 份" in experience_sentence(layer, receipt)
    played = {"kind": "played", "content_id": "c-article", "app_id": "bilibili", "activity_id": trip.activity_id,
              "interval_start": now, "interval_end": now + 60}
    zone = engine.context.config.timezone
    span = f"{clock_phrase(now, now, zone)}–{clock_phrase(now + 60, now, zone)}"
    assert experience_sentence(layer, played) == f"{span} 使用哔哩哔哩《测试文章》"
    resting = {**played, "content_id": "c-rest", "app_id": None}
    assert experience_sentence(layer, resting) == f"{span} 在家：测试休息"
    with pytest.raises(IntegrityError, match="Unsupported verified experience"):
        experience_sentence(layer, {"kind": "invented"})
    label = order_label({"goods_id": "tissue", "quantity": 1, "created_at": now}, {"tissue": "纸巾"},
                        (engine.context.config.timezone, now))
    assert "纸巾 1 份" in label and "购买" in label
    record = {"type": "delivery", "occurred_at": engine.context.config.start_time.isoformat(),
              "order": label, "message": "已送达"}
    assert "已送达" in record_lines(layer, record, [])[0]


def test_content_sentence_requires_authenticated_progress_and_stage_feedback_uses_same_contract(engine_factory):
    engine = engine_factory()
    _, started = act(engine, "content", do_at("家", "测试休息"))
    layer = _context(engine)
    assert layer.body is engine.context.body
    with pytest.raises(IntegrityError, match="authenticated progress evidence"):
        activity_sentence(layer, engine.context.state.activities[started.activity_id])
    agent = engine.context.state.agents[1]
    engine.context.state.agents[1] = agent.model_copy(update={"same_moment_stage": SameMomentStage.CONCLUDE_PENDING})
    for tool in (MAIN_TOOL, SIDE_TOOL):
        refusal = refusal_for(layer, Candidate(tool, action={"action": "interact"}))
        assert refusal.code == "SAME_MOMENT_ACTION_RESTRICTED"


def test_compact_groups_reject_conflicting_metadata_for_the_same_full_name(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "compact"))
    compact = compact_tool_layer(observed["tool_layer"])
    group = deepcopy(compact["groups"][0])
    group["kind"] = "product" if group["kind"] != "product" else "location"
    compact["groups"].append(group)
    with pytest.raises(IntegrityError, match="conflicting target metadata"):
        expand_tool_layer(compact)


def test_actual_conclusion_views_update_persona_without_duplicate_memory_prose(engine_factory):
    engine = engine_factory()
    _, started = act(engine, "view-content", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    views = [[{"type": "preference", "about": "纸巾", "text": "喜欢柔软的纸巾。", "importance": 0.5,
               "keywords": ["纸巾"]},
              {"type": "relationship", "about": "林舟", "text": "提醒自己做事认真。", "importance": 0.5,
               "keywords": ["认真"]}]]
    _, concluded = act(engine, "conclusion-views", conclude_request(activity.name, views=views), "conclude_activity")
    assert concluded.status == "completed"
    observed = engine.observe(context(engine, "after-views"))
    assert observed["persona_views"]["relationships"] == [{"person": "林舟", "view": "提醒自己做事认真。"}]
    assert observed["persona_views"]["preferences"][0]["preference"] == "喜欢柔软的纸巾。"
    assert "喜欢柔软的纸巾。" not in str(observed["memory_layer"])



def test_observation_computes_open_activities_once(engine_factory, monkeypatch):
    engine = engine_factory()
    stage_day_plan(engine)
    act(engine, "app", use_app("哔哩哔哩"))
    calls, original = [], activity_control.open_activities

    def count(state, agent_id):
        calls.append(agent_id)
        return original(state, agent_id)

    monkeypatch.setattr(activity_control, "open_activities", count)
    engine.observations._observation_data(engine.context.state.agents[1])
    assert calls == [1]


def test_tool_layer_rows_take_availability_from_the_private_binding_note(engine_factory, monkeypatch):
    engine = engine_factory()
    stage_day_plan(engine)
    act_group(engine, "both-slots", use_app("Keep"), travel("公司"))
    calls, original = [], tool_layer.availability

    def count(layer, candidate):
        calls.append(candidate)
        return original(layer, candidate)

    monkeypatch.setattr(tool_layer, "availability", count)
    observed = engine.observe(context(engine, "slots-full"))
    bindings = observed["interaction_bindings"]
    targets = observed["tool_layer"]["targets"]
    expected = sum(len(row["actions"]) for row in bindings)
    expected += sum(len(target["tools"]) for target in targets if target["kind"] == "activity")
    assert len(calls) == expected
    layer = engine.observations._layer_context(engine.context.state.agents[1], bindings)
    for binding, target in zip(bindings, targets):
        actions = [action for action in binding["actions"] if not action.get("locked")]
        for action, row in zip(actions, target["actions"], strict=True):
            assert row["available"] is (action["note"] is None)
            assert row["reason"] == action["note"]
            candidate = Candidate(start_tool(binding["kind"], action["action"]), binding, action)
            fresh = original(layer, candidate)
            assert fresh == {"available": row["available"], "reason": row["reason"]}
