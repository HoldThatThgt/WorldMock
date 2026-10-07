"""逐项工具、完整错误和原子只读批次的行为测试。"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent, MemoryTimeRangeRequest, _TOOL_MODELS, _validate_request
from worldmock.agents.life_feedback import argument_feedback, feedback_report
from worldmock.agents.life_truncation import TRUNCATION_CONTENT
from tests.life.conftest import context, stage_day_plan
from tests.life.action_contract_world import contract_world
from worldmock.life.world_query import attach_world_query
from worldmock.life.control_items import control_items
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.life.error_contract import Candidate, ErrorItem, activity_candidate
from worldmock.life.vocabulary import WAYS
from worldmock.life.interaction_menu import binding_action
from worldmock.life.models import (ConcludeActivityRequest, REQUEST_MODELS, ReviseDayPlanRequest,
                                   StartActivityRequest, deepseek_strict_schema)
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.request_errors import argument_report
from worldmock.life.routing import combination_issues, routed_selection, routing_names


@pytest.fixture
def observation():
    """@brief 宿主读取与环境同形的真实目录、动作及应用事实。"""
    layer, value = contract_world()
    attach_world_query(value, layer)
    value.update({"goal_bindings": [], "activity_bindings": [],
                  "errand_bindings": [], "errand_item_bindings": []})
    return value


def request(target="自己", way="等待", **fields):
    """@brief 五个逐项字段与消息关联全部明示，没有默认应用选择。"""
    item = {"target": target, "way": way, "app": None, "to": None, "pay_app": None}
    item.update({key: fields.pop(key) for key in tuple(fields) if key in item})
    return {"items": [item], "answering": None, **fields}


def agent_for(observation):
    agent = LifeAgent()
    agent._life_readonly = False
    agent._life_action_count = 0
    agent._model_call_sequence = 0
    agent._last_model_request = {}
    agent._react_messages = []
    agent._life_request_models = _TOOL_MODELS
    agent._life_observation = observation
    agent._life_names = {"book": name_book(observation), "routing": routing_names(observation),
                         "known_persons": frozenset({"同事"}), "activity_names": (),
                         "recall_names": ("会议总结",), "task_names": ("会议总结",),
                         "recipient_names": ("人物：同事",), "progressed_intents": (),
                         "errand_bindings": (), "errand_item_bindings": ()}
    agent._life_interaction_menu = SimpleNamespace(allowed_actions=tuple({value[1] for value in WAYS.values()}))
    return agent


def decision(tool, arguments):
    return ReactDecision("", tool, arguments, "", f"call-{tool}")


def test_new_schema_and_candidates_are_complete_and_strict():
    schema = deepseek_strict_schema(StartActivityRequest)
    assert set(schema["properties"]) == {"items", "answering"}
    assert schema["additionalProperties"] is False
    row = activity_candidate("苹果", "购买").payload()
    assert set(row) == {"tool", "arguments", "note"}
    assert row["note"] is None
    assert row["arguments"] == request("苹果", "购买")
    assert set(StartActivityRequest.model_fields) == {"items", "answering"}
    _validate_request(StartActivityRequest, row["arguments"])
    for tool, model in _TOOL_MODELS.items():
        assert deepseek_strict_schema(model)["additionalProperties"] is False, tool


@pytest.mark.parametrize("extra", ["actions", "targets", "ways", "from", "to", "prefer", "source"])
def test_deleted_activity_fields_are_rejected(extra):
    payload = {**request(), extra: []}
    with pytest.raises(ValidationError) as caught:
        _validate_request(StartActivityRequest, payload)
    error = argument_feedback("start_activity", caught.value, StartActivityRequest, payload)
    detail = error.report.payload()["items"]
    assert any(item["path"] == ["tool_calls", 0, "function", "arguments", extra] for item in detail)


@pytest.mark.parametrize("value", [None, "最快拿到", "到手价最低", "fastest", 1, True])
def test_retired_purchase_preference_is_rejected_for_every_value(value):
    """@brief 删除偏好意味着连空值也不得保留为兼容入口。"""
    payload = request("牛肉面", "购买", prefer=value)
    with pytest.raises(ValidationError) as caught:
        _validate_request(StartActivityRequest, payload)
    [item] = argument_feedback("start_activity", caught.value, StartActivityRequest, payload).report.payload()["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "prefer"]
    assert item["written"] == value and item["allowed_values"] == []


def test_unused_route_fields_preserve_json_null_in_feedback(observation):
    """@brief 等待不接受任何路由值，完整反馈保留真正 JSON null。"""
    payload = request(app="微信", to="王磊", pay_app="支付宝")
    error = agent_for(observation).validate_react_decisions([decision("start_activity", payload)], False)
    rows = feedback_report(error).payload()["items"]
    assert [row["path"][-1] for row in rows] == ["app", "to", "pay_app"]
    assert all(json.loads(json.dumps(row))["allowed_values"] == [None] for row in rows)


def test_strict_nested_type_positions_are_preserved():
    payload = {"items": [{"activity_name": "等待", "summary": "结束", "views": [], "memories": [
        {"type": "invalid", "text": "记忆", "importance": "0.5", "keywords": [False]}]}], "errands": []}
    with pytest.raises(ValidationError) as caught:
        _validate_request(ConcludeActivityRequest, payload)
    rows = argument_feedback("conclude_activity", caught.value, ConcludeActivityRequest, payload).report.payload()["items"]
    root = ["tool_calls", 0, "function", "arguments", "items", 0, "memories", 0]
    assert {tuple(row["path"]) for row in rows} == {
        tuple([*root, "type"]), tuple([*root, "importance"]), tuple([*root, "keywords", 0])}
    assert rows[0]["allowed_values"] == ["commitment", "goal", "conflict", "resolution", "identity", "routine", "reflection"]
    assert rows[1]["written"] == "0.5"
    assert rows[2]["written"] is False


def test_way_derives_action_and_null_means_caller(observation):
    payload = StartActivityRequest.model_validate(request())
    outcome = resolve_start(name_book(observation), payload)
    assert not outcome.failures and outcome.request.items[0].action == "interact"
    names = routing_names(observation)
    assert combination_issues(outcome.request, names) == []
    selected = routed_selection(list(outcome.rows), outcome.request, names)[0]
    assert selected["recipient_id"] == "agent:1" and selected["to_self"] is True


@pytest.mark.parametrize("way", ["购买", "买", "下单", "下单购买", "点单", "订购"])
def test_native_purchase_routing_derives_way_without_action_field(observation, way):
    payload = request("牛肉面", way, app="美团", to="Second Test Person", pay_app="微信")
    model = StartActivityRequest.model_validate(payload)
    assert not hasattr(model.items[0], "action")
    outcome = resolve_start(name_book(observation), model)
    assert not outcome.failures and outcome.request.items[0].way == "购买"
    names = routing_names(observation)
    assert combination_issues(outcome.request, names) == []
    [routed] = routed_selection(list(outcome.rows), outcome.request, names)
    assert (routed["app_id"], routed["payment_app_id"], routed["recipient_id"], routed["to_self"]) == (
        "meituan", "wechat", "agent:2", False)


@pytest.mark.parametrize("way", ["制作", "做饭", "给予", "赠送", "到店", "远程下单", "食用", "打开", "工作"])
def test_retired_ways_return_complete_current_closed_set(observation, way):
    payload = request("自己", way)
    error = agent_for(observation).validate_react_decisions([decision("start_activity", payload)], False)
    [row] = feedback_report(error).payload()["items"]
    assert row["path"][-1] == "way" and row["allowed_values"] == list(WAYS)
    assert row["error_code"] == "WAY_NOT_OFFERED"


def test_route_errors_return_null_only_and_all_independent_fields(observation):
    payload = request("安静休息", "进行", to="王磊", pay_app="支付宝")
    error = agent_for(observation).validate_react_decisions([decision("start_activity", payload)], False)
    rows = feedback_report(error).payload()["items"]
    assert {row["path"][-1] for row in rows} == {"to", "pay_app"}
    assert all(row["allowed_values"] == [None] and row["error_code"] == "ROUTE_FIELDS_INVALID" for row in rows)


def test_unknown_way_returns_entire_closed_set_and_no_target_guess(observation):
    error = agent_for(observation).validate_react_decisions([decision("start_activity", request("不存在", "乱动"))], False)
    rows = feedback_report(error).payload()["items"]
    assert len(rows) == 1
    assert rows[0]["path"][-1] == "way"
    assert rows[0]["allowed_values"] == list(WAYS)


def test_read_batch_reports_every_error_with_real_call_indices(observation):
    calls = [decision("memory_recent", {"limit": True}), decision("memory_search", {"query": " ", "limit": "3"})]
    error = agent_for(observation).validate_react_decisions(calls, False)
    rows = feedback_report(error).payload()["items"]
    assert len(rows) == 3
    assert [row["path"][1] for row in rows] == [0, 1, 1]
    assert rows[0]["written"] is True
    assert all(row["expected"] and row["why"] for row in rows)


def test_world_query_mixed_batches_are_atomic(observation):
    agent = agent_for(observation)
    query = {"name": None, "way": "等待", "category": None, "app": None, "available_only": False, "offset": 0}
    calls = [decision("start_activity", request()), decision("query_world", query)]
    error = agent.validate_react_decisions(calls, False)
    assert feedback_report(error).payload()["items"][0]["path"] == ["tool_calls"]
    assert agent._life_action_count == 0
    assert not agent.validate_react_decisions([decision("memory_recent", {"limit": 3}),
                                             decision("recall_task_summary", {"task_name": "会议总结"})], False)


def test_final_turn_and_truncation_retry_disable_every_query(observation):
    agent = agent_for(observation)
    agent._max_react_turns = 16
    agent._model_call_sequence = 15
    assert not (set(agent._request_tool_models(False)) & {"query_world", "recall_task_summary", "memory_recent"})
    assert set(agent._request_tool_models(True)) == {"finish"}
    agent._model_call_sequence = 0
    agent._react_messages = [{"role": "user", "content": TRUNCATION_CONTENT}]
    agent._last_model_request = {"extra_body": {"thinking": {"type": "enabled"}}}
    assert not (set(agent._request_tool_models(False)) & {"query_world", "recall_task_summary", "memory_recent"})


def test_parser_reports_malformed_json_and_unknown_tool_complete_locations(observation):
    agent = agent_for(observation)
    calls = [SimpleNamespace(id="call-a", function=SimpleNamespace(name="memory_recent", arguments='{')),
             SimpleNamespace(id="call-b", function=SimpleNamespace(name="old_discover_places", arguments='{}'))]
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=calls), finish_reason="stop")])
    parsed, error = agent._parse_react_responses(response)
    assert parsed == []
    rows = feedback_report(error).payload()["items"]
    assert rows[0]["path"] == ["tool_calls", 0, "function", "arguments"]
    assert "line 1, column 2" in rows[0]["expected"]
    assert rows[1]["path"] == ["tool_calls", 1, "function", "name"]
    assert rows[1]["allowed_values"] == list(agent._life_request_models)


def test_old_control_records_are_rejected():
    from worldmock.errors import IntegrityError

    with pytest.raises(IntegrityError):
        control_items("pause_activity", {"task": ["等待"], "summary": ["暂停"]})


def test_query_dispatch_has_no_world_side_effect(observation):
    agent = agent_for(observation)
    before = copy.deepcopy(observation)
    query = {"name": None, "way": "等待", "category": None, "app": None, "available_only": False, "offset": 0}
    result = asyncio.run(agent.dispatch_react_tool("query_world", query))
    assert result.ok
    assert set(result.data) == {"matches", "total_count", "next_offset"}
    assert agent._life_action_count == 0
    assert observation == before


def test_explicit_apps_are_preserved_without_source_notes_or_default_guess(observation):
    payload = request("牛肉面", "购买", app="美团", pay_app="微信")
    outcome = resolve_start(name_book(observation), _validate_request(StartActivityRequest, payload))
    assert not outcome.failures and outcome.notes == ()
    names = routing_names(observation)
    assert combination_issues(outcome.request, names) == []
    [routed] = routed_selection(list(outcome.rows), outcome.request, names)
    assert routed["app_id"] == "meituan" and routed["payment_app_id"] == "wechat"
    assert "from" not in routed and "prefer" not in routed
    payload["items"][0]["from"] = "一心面馆"
    with pytest.raises(ValidationError) as caught:
        _validate_request(StartActivityRequest, payload)
    [error] = argument_feedback("start_activity", caught.value, StartActivityRequest, payload).report.payload()["items"]
    assert error["path"][-3:] == ["items", 0, "from"]


@pytest.mark.parametrize("payload", [None, [], True, "invalid"])
def test_root_validator_keeps_argument_root_and_original_json(payload):
    with pytest.raises(ValidationError) as caught:
        StartActivityRequest.model_validate(payload)
    assert caught.value.errors()[0]["loc"] == ()
    [item] = argument_report("start_activity", caught.value, StartActivityRequest, payload).payload()["items"]
    assert item == {"path": ["tool_calls", 0, "function", "arguments"], "written": payload,
                    "error_code": "ARGUMENTS_INVALID", "allowed_values": [], "expected": "JSON type: object.",
                    "why": "A JSON object containing the declared fields is required.", "candidates": []}
    assert feedback_report(argument_feedback("start_activity", caught.value, StartActivityRequest, payload)
                           ).payload()["items"] == [item]


def test_cross_field_interval_and_goal_bounds_keep_explicit_locations():
    interval = {"start_time": "2026-01-05T11:00:00+08:00", "end_time": "2026-01-05T10:00:00+08:00", "limit": 3}
    with pytest.raises(ValidationError) as caught:
        _validate_request(MemoryTimeRangeRequest, interval)
    rows = argument_report("memory_time_range", caught.value, MemoryTimeRangeRequest, interval).payload()["items"]
    root = ["tool_calls", 0, "function", "arguments"]
    assert [(row["path"], row["written"], row["why"]) for row in rows] == [
        ([*root, field], interval[field], "memory time range is reversed") for field in ("start_time", "end_time")]
    from worldmock.life.models import PLAN_GOAL_MAX_CHARS
    goal = {"intent_kind": "工作", "goal": "做" * (PLAN_GOAL_MAX_CHARS + 1), "remind_time": None, "around_time": None}
    plan = {"goals": [goal]}
    with pytest.raises(ValidationError) as caught:
        _validate_request(ReviseDayPlanRequest, plan)
    [item] = argument_report("revise_day_plan", caught.value, ReviseDayPlanRequest, plan).payload()["items"]
    assert (item["path"], item["written"], item["why"]) == (
        [*root, "goals", 0, "goal"], goal["goal"], f"day goal exceeds {PLAN_GOAL_MAX_CHARS} characters")


@pytest.mark.parametrize("payload, expected", [
    pytest.param({"answering": None},
                 [(('items',), None, "This required field was omitted.")], id="missing_items"),
    pytest.param({"items": [{"target": True, "way": "等待", "app": False, "to": 1, "pay_app": 2}],
                  "answering": None},
                 [(('items', 0, 'target'), True, "A JSON string is required."),
                  (('items', 0, 'app'), False, "A JSON string is required."),
                  (('items', 0, 'to'), 1, "A JSON string is required."),
                  (('items', 0, 'pay_app'), 2, "A JSON string is required.")], id="nested_bad_types"),
])
def test_engine_revalidates_constructed_bad_requests_with_complete_fields(engine_factory, payload, expected):
    engine = engine_factory()
    stage_day_plan(engine)
    before = (copy.deepcopy(engine.context.state.activities), dict(engine.context.state.accounts), copy.deepcopy(engine.context.state.deliveries),
              engine.context.state.current_time)
    ctx = context(engine, "bad-typed")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, "bad-typed", "start_activity", StartActivityRequest.model_construct(**payload)))
    [rejected] = engine.commit_decision_round("bad-typed")
    assert (rejected.status, rejected.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    root = ["tool_calls", 0, "function", "arguments"]
    expected_items = []
    for path, written, why in expected:
        condition = ("This field is required; JSON type: array." if path == ('items',) else
                     r"JSON type: string; pattern: \S." if path[-1] == 'target' else
                     "A value satisfying one of the declared JSON types is required.")
        expected_items.append({"path": [*root, *path], "written": written, "error_code": "ACTION_REQUEST_INVALID",
                               "allowed_values": [], "expected": condition, "why": why, "candidates": []})
    assert rejected.data["items"] == expected_items
    assert (engine.context.state.activities, engine.context.state.accounts, engine.context.state.deliveries, engine.context.state.current_time) == before
    events = [event.model_dump(mode="json") for event in engine.context.store.read_events(engine.context.store.last_event_seq)]
    assert project_purchase_facts(events) == []
    next_ctx = context(engine, "valid-after-bad")
    engine.begin_decision_round([next_ctx])
    engine.observe(next_ctx)
    engine.submit_action(DirectToolCall(next_ctx, "valid-after-bad", "start_activity",
                                       StartActivityRequest.model_validate(request())))
    [accepted] = engine.commit_decision_round("valid-after-bad")
    assert (accepted.status, accepted.error_code) == ("in_progress", None)
    assert accepted.activity_id in engine.context.state.activities
