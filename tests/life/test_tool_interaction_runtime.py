"""工具交互的真实邮件、操作备忘、只读证据与无损呈现行为验收。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.progress_layer import rejected_today
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from tests.life.conftest import context, stage_day_plan


def _layer():
    action = {"action": "interact", "way": "工作", "available": False, "reason": "Opens later",
              "price": 0, "duration": "0 minutes", "quantity": None, "note": None}
    return {"layer": "工具层", "callable_index": {}, "targets": [
        {"name": "Task", "kind": "task", "phase": None, "state": None,
         "contract": {"topic": "Quarter report", "target": "约 8 小时", "done": "约 8 小时", "remaining": "约 0 小时"},
         "actions": [action], "tools": []},
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
    now = engine.context.state.current_time
    written = "wrong"
    report = {"error_code": "ARGUMENTS_INVALID", "reason": "Invalid value", "items": [
        {"path": ["tool_calls", 0, "function", "arguments", "prefer"], "written": written,
         "why": "The preference must match the declared enum."}]}
    response = {"choices": [{"message": {"tool_calls": [{"function": {"name": "start_activity"}}]}}]}
    drafts = [EventDraft(event_type="model_response", occurred_at=now, agent_id=1,
                         data={"protocol_feedback": report, "response": response}) for _ in range(2)]
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), drafts)
    engine.context.state = staged
    view = SimpleNamespace(state=engine.context.state, scenario=engine.context.scenario, agent_id=1)
    memo = rejected_today(view, engine.context.store)
    assert len(memo) == 1 and memo[0]["written"] == written and memo[0]["times"] == 2
    assert len(memo[0]["why"]) <= 60
    staged.current_time += 60
    assert rejected_today(view, engine.context.store) == memo
    drafts = [EventDraft(event_type="action_requested", occurred_at=staged.current_time, agent_id=1,
                         action_id="corrected", data={"tool_name": "start_activity", "request": {"prefer": "最快拿到"}}),
              EventDraft(event_type="action_accepted", occurred_at=staged.current_time, agent_id=1,
                         action_id="corrected", data={})]
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), drafts)
    assert rejected_today(view, engine.context.store)[0]["accepted_as"] == "最快拿到"
    staged.current_time += 86400
    assert rejected_today(view, engine.context.store) == []
    assert engine.context.store.read_agent_events("model_response", 1)


def test_mail_reading_keeps_actual_subject_and_body_only(engine_factory):
    from worldmock.life.content import reveal_mail
    from worldmock.life.result_projection import public_result

    engine = engine_factory()
    notice = engine.context.scenario.messages[0]
    agent = engine.context.state.agents[1].model_copy(update={"visible_message_ids": {notice.message_id}})
    activity = SimpleNamespace(target_id=notice.message_id)
    changed, revealed = reveal_mail(activity, agent, engine.context.rules.messages)
    result = {"occurred_at": int(notice.sent_at.timestamp()), "status": "completed", "error_code": None,
              "message": "Mail reading completed", "data": {"activity": {"kind": "read_mail",
                                                                         "data": {"revealed": revealed}}}}
    mail = public_result(result, engine.context.scenario)["details"]["mail"]
    assert mail == {"subject": notice.subject, "body": notice.body}
    assert changed.read_message_ids == {notice.message_id}
    assert not {"opens", "deadline", "place", "oracle", "expected"}.intersection(mail)


def test_memo_survives_persistent_reopen(engine_factory, tmp_path):
    from worldmock.life.engine import LifeEngine

    engine = engine_factory()
    stage_day_plan(engine)
    now = engine.context.state.current_time
    report = {"error_code": "NAME_NOT_FOUND", "reason": "Unknown object", "items": [
        {"path": ["tool_calls", 0, "function", "arguments", "items", 0, "target"],
         "written": "Missing object", "why": "This object does not exist."}]}
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="model_response", occurred_at=now, agent_id=1, data={"protocol_feedback": report})])
    engine.context.state = staged
    expected = rejected_today(SimpleNamespace(state=engine.context.state, scenario=engine.context.scenario, agent_id=1), engine.context.store)
    run_directory = engine.context.store.run_dir
    engine.close()
    scenario_path = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"
    reopened = LifeEngine.open(scenario_path, run_directory)
    try:
        observed = reopened.observe(context(reopened, "reopened-memo"))
        assert observed["progress_layer"]["rejected_today"] == expected
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
    now = engine.context.state.current_time
    drafts = []
    for index in range(10):
        item = {"path": ["tool_calls", 0, "function", "arguments", "name"],
                "written": f"Unknown {index}", "why": "The object name is unavailable. " * 5}
        drafts.append(EventDraft(event_type="model_response", occurred_at=now, agent_id=1,
                                 data={"protocol_feedback": {"error_code": "NAME_NOT_FOUND", "items": [item]}}))
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), drafts)
    engine.context.state = staged
    memo = rejected_today(SimpleNamespace(state=staged, scenario=engine.context.scenario, agent_id=1), engine.context.store)
    assert len(memo) == 8 and memo[0]["written"] == "Unknown 9" and memo[-1]["written"] == "Unknown 2"
    assert all(len(row["why"]) == 60 for row in memo)
    stored = engine.context.store.read_agent_events("model_response", 1)
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
    before = engine.context.state.model_dump(mode="json")
    environment = LifeEnv()
    environment.engine = engine
    category = observed["tool_layer"]["callable_index"]["query_categories"][0]["category"]
    requests = {
        "memory_recent": {"limit": 2}, "memory_search": {"query": "咖啡"},
        "memory_time_range": {"start_time": "2026-01-05T00:00:00+08:00", "end_time": "2026-01-05T08:30:00+08:00"},
        "query_world": {"name": None, "way": None, "category": category, "from": None, "available_only": False,
                        "offset": 0},
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
    assert engine.context.state.model_dump(mode="json") == before
    evidence = engine.context.store.read_agent_events("model_request", 1)[-1]
    assert evidence.data["request"]["messages"] == messages
    assert not engine.context.store.read_agent_events("action_requested", 1)


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
    agent._name = engine.context.scenario.agents[0].name
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
    assert engine.context.state.version == decision.world_state_version


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
