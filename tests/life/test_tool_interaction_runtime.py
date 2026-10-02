"""工具交互规格的任务通知、操作备忘与无损呈现行为验收。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import IntegrityError, PreconditionError
from worldmock.footprints.models import EventDraft
from worldmock.life import rhythm
from worldmock.life.models import AgentState, BodyState, WorldState
from worldmock.life.progress_layer import rejected_today
from worldmock.life.scenario import LifeScenario, RuntimeWorkTask, WorkTaskConfig, runtime_tasks
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.life.work import validate_work_start
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind
from tests.life.conftest import context, stage_day_plan


def _document():
    return json.loads(Path("scenarios/multi-person.json").read_text(encoding="utf-8"))


def _agent(agent_id=1):
    return AgentState(agent_id=agent_id, name=f"Person {agent_id}", location_id="home",
                      balance_account_id=f"agent:{agent_id}", skill_levels={"office": 1},
                      body=BodyState(energy_kcal=1000, sleep_pressure=0, entertainment_need=0), body_settled_at=0, next_decision_at=0)


def _task_document():
    return deepcopy(_document()["tasks"][0])


def _notifications(task, starts, recipient=1):
    from worldmock.life.scenario import MessageConfig

    return tuple(MessageConfig(message_id=f"mail-{index}", thread_id="thread", recipient_agent_id=recipient,
                               sent_at=moment, subject=task.title, body="Private actual body",
                               related_task_id=task.task_id) for index, moment in enumerate(starts))


def test_ordinary_work_opens_at_first_notification_and_wrong_place_is_explicit():
    raw = _task_document()
    raw["required_skill"] = "office"
    task = WorkTaskConfig.model_validate(raw)
    first = task.due_at.replace(hour=8, minute=0)
    runtime = runtime_tasks((task,), _notifications(task, (first, first + timedelta(hours=2))))[0]
    assert runtime.available_at == first
    agent = _agent().model_copy(update={"visible_task_ids": {task.task_id}})
    validate_work_start(agent, runtime, int(first.replace(hour=8, minute=55).timestamp()), runtime.required_location_id)
    with pytest.raises(PreconditionError) as error:
        validate_work_start(agent, runtime, int(first.replace(hour=8, minute=55).timestamp()), "home")
    assert error.value.unmet == ["required_location_id"]


def test_removed_input_opening_is_rejected_and_appointment_is_explicit():
    raw = _task_document()
    raw["available_at"] = raw["due_at"]
    with pytest.raises(ValidationError):
        WorkTaskConfig.model_validate(raw)
    raw.pop("available_at")
    raw.pop("appointment")
    with pytest.raises(ValidationError):
        WorkTaskConfig.model_validate(raw)
    raw["appointment"] = {"starts_at": "2026-01-05T10:00:00+08:00", "reason": "Scheduled interview"}
    task = WorkTaskConfig.model_validate(raw)
    notices = _notifications(task, (task.due_at.replace(hour=8), task.due_at.replace(hour=11)))
    runtime = runtime_tasks((task,), notices)[0]
    assert runtime.available_at == task.appointment.starts_at
    with pytest.raises(PreconditionError, match="WORK_TASK_NOT_AVAILABLE") as error:
        validate_work_start(_agent().model_copy(update={"visible_task_ids": {task.task_id}}), runtime,
                            int(task.due_at.replace(hour=9).timestamp()), runtime.required_location_id)
    assert "10:00:00+08:00" in error.value.public_reason


@pytest.mark.parametrize("fault", ["missing", "wrong_recipient", "unknown_task", "wrong_title", "late_first"])
def test_notification_coverage_checks_every_notice(fault):
    raw = _document()
    task = raw["tasks"][0]
    related = [row for row in raw["messages"] if row.get("related_task_id") == task["task_id"]]
    if fault == "missing":
        raw["messages"] = [row for row in raw["messages"] if row not in related]
    elif fault == "wrong_recipient":
        related[-1]["recipient_agent_id"] = 2
    elif fault == "unknown_task":
        related[-1]["related_task_id"] = "unknown"
    elif fault == "wrong_title":
        related[-1]["subject"] = "Other task"
    else:
        task["appointment"] = {"starts_at": "2026-01-05T07:00:00+08:00", "reason": "Scheduled meeting"}
    with pytest.raises(ValidationError):
        LifeScenario.model_validate(raw)


def test_appointment_mail_reveals_only_assignee_without_false_opening():
    raw = _document()
    task = raw["tasks"][0]
    task["appointment"] = {"starts_at": "2026-01-05T10:00:00+08:00", "reason": "Scheduled interview"}
    document = LifeScenario.model_validate(raw)
    tasks = runtime_tasks(document.tasks, document.messages)
    scenario = SimpleNamespace(**{key: getattr(document, key) for key in
                               ("messages", "timezone", "calendar", "bills", "payrolls", "price_changes",
                                "product_releases")}, tasks=tasks, supplies=())
    first = next(row for row in document.messages if row.related_task_id == task["task_id"])
    start, end = int(document.start_at.timestamp()), int(document.end_at.timestamp())
    scheduled = compile_schedule(scenario, start, end)
    openings = [row for row in scheduled if row.kind == ScheduledKind.WORK_AVAILABLE
                and row.data["task_id"] == task["task_id"]]
    assert len(openings) == 1 and openings[0].due_at == int(tasks[0].available_at.timestamp())
    mail = next(row for row in scheduled if row.kind == ScheduledKind.MAIL_ARRIVAL
                and row.data["message_id"] == first.message_id)
    state = WorldState.model_construct(agents={1: _agent(), 2: _agent(2)})
    changed = apply_scheduled_effect(state, mail, SimpleNamespace(scenario=scenario)).state
    assert task["task_id"] in changed.agents[1].visible_task_ids
    assert task["task_id"] not in changed.agents[2].visible_task_ids
    assert changed.agents[2].visible_message_ids == set()
    assert openings[0].due_at > mail.due_at
    assert not any(row.kind == ScheduledKind.WORK_AVAILABLE and row.data["task_id"] == task["task_id"]
                   for row in scheduled if row.due_at == mail.due_at)


def test_initial_observation_sees_already_delivered_appointment_and_contract(engine_factory):
    def mutate(document):
        task = document["tasks"][0]
        document["start_at"] = "2026-01-05T08:30:00+08:00"
        task["appointment"] = {"starts_at": "2026-01-05T10:00:00+08:00", "reason": "Scheduled interview"}
    engine = engine_factory(mutate=mutate)
    stage_day_plan(engine)
    task = engine.scenario.tasks[0]
    assert task.task_id in engine.state.agents[1].visible_task_ids
    observed = engine.observe(context(engine, "initial-appointment"))
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == f"工作：{task.title}")
    assert set(target["contract"]) == {"opens", "deadline", "place", "remaining", "outlook"}
    assert target["contract"]["opens"].endswith("10:00:00+08:00")
    assert target["contract"]["place"] == engine.rules.locations[task.required_location_id].name
    assert target["actions"][0]["available"] is False
    assert "10:00:00+08:00" in target["actions"][0]["reason"]
    notice = next(row for row in engine.scenario.messages if row.message_id in engine.state.agents[1].visible_message_ids)
    public = [observed[key] for key in ("tool_layer", "progress_layer", "memory_layer", "todo_layer")]
    assert notice.body not in json.dumps(public, ensure_ascii=False)


def test_work_material_is_validated_before_first_world_save(engine_factory):
    with pytest.raises(IntegrityError, match="Work input"):
        engine_factory(mutate=lambda document: document["tasks"][0].update(input_path="ami/missing-material.txt"))


def _layer():
    action = {"action": "interact", "way": "工作", "available": False, "reason": "Opens later",
              "price": 0, "duration": "0 minutes", "quantity": None, "note": None}
    return {"layer": "工具层", "callable_index": {}, "targets": [
        {"name": "Task", "kind": "task", "phase": None, "state": None,
         "contract": {"opens": "soon", "deadline": "later", "place": "Office", "remaining": "0", "outlook": None},
         "actions": [action], "tools": [{"tool": "submit_work", "available": False, "reason": "Not complete"}]},
        {"name": "Active", "kind": "activity", "phase": "进行中", "state": "Waiting", "actions": [],
         "tools": [{"tool": "report_status", "available": True, "reason": None}]},
    ]}


def test_compact_layer_preserves_false_zero_contract_and_control_tools():
    layer = _layer()
    original = deepcopy(layer)
    compact = compact_tool_layer(layer)
    assert expand_tool_layer(compact) == original
    assert layer == original
    assert compact["groups"][1]["action"] is None and compact["groups"][1]["way"] is None
    assert "tools" in compact["groups"][1]["shared"]
    assert compact["groups"][0]["shared"]["available"] is False
    assert compact["groups"][0]["shared"]["price"] == 0
    conflict = deepcopy(compact)
    conflict["groups"].append(deepcopy(compact["groups"][0]))
    conflict["groups"][-1]["shared"]["state"] = "Conflicting"
    with pytest.raises(IntegrityError, match="conflicting"):
        expand_tool_layer(conflict)


def test_memo_is_derived_across_sessions_with_acceptance_and_clears_next_day(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    now = engine.state.current_time
    written = "wrong"
    report = {"error_code": "ARGUMENTS_INVALID", "reason": "Invalid value", "items": [
        {"path": ["tool_calls", 0, "function", "arguments", "prefer"], "written": written,
         "why": "The preference must match the declared enum."}]}
    response = {"choices": [{"message": {"tool_calls": [{"function": {"name": "start_activity"}}]}}]}
    drafts = [EventDraft(event_type="model_response", occurred_at=now, agent_id=1,
                         data={"protocol_feedback": report, "response": response}) for _ in range(2)]
    staged = engine.state.model_copy(deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), drafts)
    engine.state = staged
    view = SimpleNamespace(state=engine.state, scenario=engine.scenario, agent_id=1)
    memo = rejected_today(view, engine.store)
    assert len(memo) == 1 and memo[0]["written"] == written and memo[0]["times"] == 2
    assert len(memo[0]["why"]) <= 60
    staged.current_time += 60
    assert rejected_today(view, engine.store) == memo
    drafts = [EventDraft(event_type="action_requested", occurred_at=staged.current_time, agent_id=1,
                         action_id="corrected", data={"tool_name": "start_activity", "request": {"prefer": "最快拿到"}}),
              EventDraft(event_type="action_accepted", occurred_at=staged.current_time, agent_id=1,
                         action_id="corrected", data={})]
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), drafts)
    assert rejected_today(view, engine.store)[0]["accepted_as"] == "最快拿到"
    staged.current_time += 86400
    assert rejected_today(view, engine.store) == []
    assert engine.store.read_agent_events("model_response", 1)



def test_appointment_wakes_only_at_real_opening_and_never_wakes_sleep(engine_factory):
    from worldmock.life.schedule import WakeReason, evaluate_wake_reasons

    def mutate(document):
        task = document["tasks"][0]
        task["appointment"] = {"starts_at": "2026-01-05T10:00:00+08:00", "reason": "Scheduled interview"}
    engine = engine_factory(mutate=mutate)
    task = engine.scenario.tasks[0]
    mail = next(row for row in engine.state.pending_events if row.kind == ScheduledKind.MAIL_ARRIVAL
                and engine.rules.messages[row.data["message_id"]].related_task_id == task.task_id)
    state = engine.state.model_copy(update={"current_time": mail.due_at}, deep=True)
    state = apply_scheduled_effect(state, mail, engine.semantics).state
    state.agents[1].last_decision_time = mail.due_at
    inputs = rhythm.engine_rhythm_inputs(engine, state, arrival_flags={1: {"message_arrival"}})
    facts = {"task": set(), "closing": set()}
    snapshot = rhythm._wake_snapshot(state.agents[1], inputs, facts, set())
    reasons = evaluate_wake_reasons(snapshot).wake_reasons
    assert WakeReason.MESSAGE_ARRIVAL in reasons and WakeReason.TASK_AVAILABLE not in reasons
    assert WakeReason.MESSAGE_ARRIVAL not in evaluate_wake_reasons(snapshot.model_copy(update={"is_asleep": True})).wake_reasons
    opening = next(row for row in state.pending_events if row.kind == ScheduledKind.WORK_AVAILABLE
                   and row.data["task_id"] == task.task_id)
    state.current_time = opening.due_at
    state = apply_scheduled_effect(state, opening, engine.semantics).state
    inputs = rhythm.engine_rhythm_inputs(engine, state, arrival_flags={1: {"task_available"}})
    snapshot = rhythm._wake_snapshot(state.agents[1], inputs, facts, set())
    assert WakeReason.TASK_AVAILABLE in evaluate_wake_reasons(snapshot).wake_reasons
    assert WakeReason.TASK_AVAILABLE not in evaluate_wake_reasons(snapshot.model_copy(update={"is_asleep": True})).wake_reasons


def test_mail_reading_keeps_actual_body_and_only_full_task_name(engine_factory):
    from worldmock.life.content import reveal_mail
    from worldmock.life.result_projection import public_result

    engine = engine_factory()
    task = engine.scenario.tasks[0]
    notice = next(row for row in engine.scenario.messages if row.related_task_id == task.task_id)
    agent = engine.state.agents[1].model_copy(update={"visible_message_ids": {notice.message_id}})
    activity = SimpleNamespace(target_id=notice.message_id)
    changed, revealed = reveal_mail(activity, agent, engine.rules.messages)
    result = {"occurred_at": int(notice.sent_at.timestamp()), "status": "completed", "error_code": None,
              "message": "Mail reading completed", "data": {"activity": {"kind": "read_mail",
                                                                         "data": {"revealed": revealed}}}}
    mail = public_result(result, engine.scenario)["details"]["mail"]
    assert mail == {"subject": notice.subject, "body": notice.body, "task": f"工作：{task.title}"}
    assert changed.read_message_ids == {notice.message_id}
    assert not {"opens", "deadline", "place", "oracle", "expected"}.intersection(mail)


def test_memo_and_appointment_survive_persistent_reopen(engine_factory, tmp_path):
    from worldmock.life.engine import LifeEngine

    def mutate(document):
        document["tasks"][0]["appointment"] = {
            "starts_at": "2026-01-05T10:00:00+08:00", "reason": "Scheduled interview"}
    engine = engine_factory(mutate=mutate)
    stage_day_plan(engine)
    now = engine.state.current_time
    report = {"error_code": "NAME_NOT_FOUND", "reason": "Unknown object", "items": [
        {"path": ["tool_calls", 0, "function", "arguments", "items", 0, "target"],
         "written": "Missing object", "why": "This object does not exist."}]}
    staged = engine.state.model_copy(deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="model_response", occurred_at=now, agent_id=1, data={"protocol_feedback": report})])
    engine.state = staged
    expected = rejected_today(SimpleNamespace(state=engine.state, scenario=engine.scenario, agent_id=1), engine.store)
    run_directory = engine.store.run_dir
    engine.close()
    scenario_path = tmp_path / "scenario-0" / "scenarios" / "white-collar-12-weeks.json"
    reopened = LifeEngine.open(scenario_path, run_directory)
    try:
        observed = reopened.observe(context(reopened, "reopened-memo"))
        assert observed["progress_layer"]["rejected_today"] == expected
        assert reopened.state.agents[1].visible_task_ids == staged.agents[1].visible_task_ids
        opening = next(row for row in reopened.state.pending_events if row.kind == ScheduledKind.WORK_AVAILABLE
                       and row.data["task_id"] == reopened.scenario.tasks[0].task_id)
        assert opening.due_at == int(reopened.scenario.tasks[0].appointment.starts_at.timestamp())
    finally:
        reopened.close()



def test_actual_frozen_tool_layer_and_prompt_groups_restore_identically(engine_factory):
    from worldmock.life.narration import render_layer_json
    from worldmock.life.prompt_schemas import validate_layer

    engine = engine_factory()
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "compact-real-observation"))
    original = deepcopy(observed["tool_layer"])
    rendered = render_layer_json(original)
    compact = json.loads(rendered)
    assert "groups" in compact and "targets" not in compact
    validate_layer(compact)
    assert expand_tool_layer(compact) == original
    assert observed["tool_layer"] == original
    assert render_layer_json(original) == rendered
    public_names = {row["name"] for row in original["targets"]}
    grouped_names = {row["name"] for group in compact["groups"] for row in group["targets"]}
    assert grouped_names == public_names



def test_memo_caps_recent_entries_and_reason_without_truncating_audit(engine_factory):
    engine = engine_factory()
    now = engine.state.current_time
    drafts = []
    for index in range(10):
        item = {"path": ["tool_calls", 0, "function", "arguments", "name"],
                "written": f"Unknown {index}", "why": "The object name is unavailable. " * 5}
        drafts.append(EventDraft(event_type="model_response", occurred_at=now, agent_id=1,
                                 data={"protocol_feedback": {"error_code": "NAME_NOT_FOUND", "items": [item]}}))
    staged = engine.state.model_copy(deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), drafts)
    engine.state = staged
    memo = rejected_today(SimpleNamespace(state=staged, scenario=engine.scenario, agent_id=1), engine.store)
    assert len(memo) == 8 and memo[0]["written"] == "Unknown 9" and memo[-1]["written"] == "Unknown 2"
    assert all(len(row["why"]) == 60 for row in memo)
    stored = engine.store.read_agent_events("model_response", 1)
    assert len(stored) == 10 and len(stored[0].data["protocol_feedback"]["items"][0]["why"]) > 60


def test_read_queries_keep_actual_model_exchange_without_progress_events(engine_factory):
    """五种同批只读查询的真实模型证据保留完整结果，生活状态与进度记录保持不变。"""
    import asyncio

    from agentsociety2.agent.base import ModelCallEvidence
    from custom.envs.life_env import LifeEnv
    from worldmock.life.models import RecallTaskSummaryRequest
    from worldmock.life.world_query import QueryWorldRequest

    engine = engine_factory()
    stage_day_plan(engine)
    decision = context(engine, "read-query-evidence")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    before = engine.state.model_dump(mode="json")
    environment = LifeEnv()
    environment.engine = engine
    category = observed["tool_layer"]["callable_index"]["query_categories"][0]["category"]
    requests = {
        "memory_recent": {"limit": 2}, "memory_search": {"query": "咖啡"},
        "memory_time_range": {"start_time": "2026-01-05T00:00:00+08:00", "end_time": "2026-01-05T08:30:00+08:00"},
        "query_world": {"name": None, "way": None, "category": category, "offset": 0},
        "recall_task_summary": {"task_name": "尚未结束的活动"}}
    results = {name: {"epistemic_status": "subjective", "episodes": []} for name in requests}
    results["query_world"] = asyncio.run(environment.read_query(
        decision, "query_world", QueryWorldRequest(**requests["query_world"])))
    results["recall_task_summary"] = asyncio.run(environment.read_query(
        decision, "recall_task_summary", RecallTaskSummaryRequest(**requests["recall_task_summary"])))
    calls = [{"id": f"call-{name}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}} for name, arguments in requests.items()]
    wire = {"model": "test-model", "messages": []}
    response = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}]}
    engine.record_model_exchange(ModelCallEvidence(decision, "query-batch", "request", 1, wire))
    engine.record_model_exchange(ModelCallEvidence(decision, "query-batch", "response", 1, wire, response=response))
    messages = [{"role": "tool", "tool_call_id": f"call-{name}", "content": json.dumps(result, ensure_ascii=False)}
                for name, result in results.items()]
    engine.record_model_exchange(ModelCallEvidence(decision, "query-results", "request", 1,
                                                   {"model": "test-model", "messages": messages}))
    after = engine.observe(decision)
    assert after["progress_layer"]["events"] == observed["progress_layer"]["events"]
    assert engine.state.model_dump(mode="json") == before
    evidence = engine.store.read_agent_events("model_request", 1)[-1]
    assert evidence.data["request"]["messages"] == messages
    assert not engine.store.read_agent_events("action_requested", 1)


def test_compact_prompt_continuation_preserves_head_and_exact_target_changes(engine_factory):
    """真实紧凑人物提示续写保持头消息字节，完整冻结层的变化保留不可用状态与控制工具。"""
    from agentsociety2.agent.base import ReactLoopRequest
    from custom.agents.life_agent import LifeAgent
    from worldmock.life.continuation import CONTINUATION_KEY

    engine = engine_factory()
    stage_day_plan(engine)
    decision = context(engine, "compact-continuation")
    observed = engine.observe(decision)
    agent = LifeAgent()
    agent._name = engine.scenario.agents[0].name
    agent._profile = {"id": 1, "name": agent._name}
    initial = ReactLoopRequest(0, decision.observed_at, [{"data": observed}])
    agent.prepare_react_loop(initial)
    head = agent.build_react_messages(initial)
    public = next(layer for layer in json.loads(head[1]["content"]) if layer["layer"] == "工具层")
    assert "groups" in public and "targets" not in public
    current = deepcopy(observed)
    target = next(row for row in current["tool_layer"]["targets"] if row["actions"])
    target["actions"][0]["available"] = False
    target["actions"][0]["reason"] = "This action is unavailable in the continued observation."
    current[CONTINUATION_KEY] = {
        "messages": head, "assistant": {"role": "assistant", "content": None, "reasoning_content": "Plan the next step.",
        "tool_calls": [{"id": "continued-plan", "type": "function", "function": {
            "name": "revise_day_plan", "arguments": '{"goals":[]}'}}]},
        "tool_call_id": "continued-plan", "result": {"status": "completed", "details": {}}, "events": []}
    agent.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, [{"data": current}]))
    continued = agent._react_messages
    assert continued[:len(head)] == head
    payload = json.loads(continued[-1]["content"])
    assert payload["changes"]["targets"]["updated"] == [target]
    assert payload["changes"]["targets"]["updated"][0]["actions"][0]["available"] is False
    assert engine.state.version == decision.world_state_version


def test_plan_times_display_exact_declared_local_format(engine_factory):
    """实际修订计划后的提醒与大致时刻均按 HH:MM 呈现，六层提示校验通过。"""
    from worldmock.life.models import PlanGoalInput, ReviseDayPlanRequest
    from worldmock.life.prompt_schemas import validate_layer
    from tests.life.conftest import submit_round

    engine = engine_factory()
    stage_day_plan(engine)
    request = ReviseDayPlanRequest(goals=[PlanGoalInput(
        intent_kind="休息", goal="午前休息", remind_time="10:00", around_time="10:05")])
    _, results = submit_round(engine, {1: ("revise_day_plan", request)}, "exact-plan-times")
    assert results[0].status == "completed"
    observed = engine.observe(context(engine, "exact-plan-times-seen"))
    [goal] = observed["todo_layer"]["day_plan"]["goals"]
    assert (goal["remind_time"], goal["around_time"]) == ("10:00", "10:05")
    validate_layer(observed["todo_layer"])
