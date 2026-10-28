"""事后读取视图：只去掉表里的庞大载荷，事后读取会读的键一个不少，任何形状都不抛错。"""
from worldmock.state.model.exchange import is_action_request as _decision_start_is_action_request
from worldmock.state.model.exchange import request_tool_names as _decision_start_request_tool_names

import json

import pytest

from tools.analysis.behavior import metrics as behavior_metrics
from worldmock.recording_engine.reports import errand_metrics
from worldmock.recording_engine.reports import quality
from worldmock.recording_engine.events.models import Event
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.export.post_run import DROPPED_PAYLOADS, post_run_row, post_run_view
from worldmock.simulation_engine.decisions import decision_start

#: 观察在视图里去掉的八个键（逐字写出）。
EIGHT_KEYS = {"activity_bindings", "persona_views", "tool_layer", "interaction_bindings", "catalog_bindings",
              "memory_layer", "goal_bindings", "locations"}


def _event(event_type, data, sequence=1, **fields):
    """一条持久事件；data 原样交给事件模型。"""
    return Event(event_id=f"event-{sequence}", event_type=event_type, occurred_at=100, run_id="run",
                 event_seq=sequence, recorded_at="2026-09-15T07:00:00+00:00", wall_elapsed_ms=0,
                 state_version_before=0, state_version_after=0, data=data, **fields)


def _reduced(event_type, data):
    """同一规则下 data 的事后读取视图（经行视图，不校验事件模型，供任意形状使用）。"""
    return post_run_row({"event_type": event_type, "data": data})["data"]


def _request(*, purpose=None, thinking="enabled", tools=("start_main_activity", "start_side_activity"),
             messages=None):
    """一次模型请求：系统提示、人物输入与工具定义都带完整原文。"""
    body = {"model": "deepseek-flash", "max_tokens": 32000,
            "messages": messages or [{"role": "system", "content": "你是一个人。"},
                                     {"role": "user", "content": "{\"progress_layer\": {}}"}],
            "tools": [{"type": "function", "function": {"name": name, "description": f"{name} 的说明",
                                                        "parameters": {"type": "object", "properties": {}}}}
                      for name in tools]}
    if thinking is not None:
        body["extra_body"] = {"thinking": {"type": thinking}}
    if purpose is not None:
        body["purpose"] = purpose
    return body


def test_post_run_view_drops_only_the_listed_payloads():
    data = {key: [{"payload": key}] for key in sorted(EIGHT_KEYS)}
    data.update(todo_layer={"errands": [{"name": "买牛奶"}]}, verified_experiences=[{"kind": "played"}],
                progress_event_ids=["event-0"], errand_bindings=[{"product_id": "milk"}], kind="observation-kind",
                activity={"activity_id": "a"}, data={"nested": True}, future_key={"added": "later"})
    observation = _event("observation", data)
    view = post_run_view(observation)
    assert set(observation.data) - set(view.data) == EIGHT_KEYS
    assert all(view.data[key] is observation.data[key] for key in view.data)
    assert list(view.data) == [key for key in observation.data if key not in EIGHT_KEYS]
    assert view.model_dump(exclude={"data"}) == observation.model_dump(exclude={"data"})

    request = _request()
    request["messages"].append({"role": "assistant", "content": "好的", "reasoning_content": "想一想",
                                "tool_calls": [{"id": "call-1", "function": {"name": "start_main_activity"}}]})
    request["messages"].append({"role": "tool", "tool_call_id": "call-1", "content": "{\"status\": \"ok\"}"})
    model_request = _event("model_request", {"phase": "request", "call_id": "call", "purpose": None,
                                             "request": request})
    outline = post_run_view(model_request).data
    assert {key: value for key, value in outline.items() if key != "request"} == {
        "phase": "request", "call_id": "call", "purpose": None}
    assert set(outline["request"]) == set(request)
    assert outline["request"]["extra_body"] == request["extra_body"]
    assert outline["request"]["messages"] == [
        {"role": "system"}, {"role": "user"},
        {"role": "assistant", "tool_calls": [{"id": "call-1", "function": {"name": "start_main_activity"}}]},
        {"role": "tool", "tool_call_id": "call-1"}]
    assert outline["request"]["tools"] == [{"type": "function", "function": {"name": name}}
                                           for name in ("start_main_activity", "start_side_activity")]

    response = {"id": "resp-1", "model": "deepseek-flash", "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                "choices": [{"index": 0, "finish_reason": "tool_calls",
                             "message": {"role": "assistant", "content": "x", "reasoning_content": "y"}}]}
    model_response = _event("model_response", {"phase": "response", "call_id": "call", "request": None,
                                               "response": response})
    reduced = post_run_view(model_response).data
    assert reduced["request"] is None and reduced["phase"] == "response" and reduced["call_id"] == "call"
    assert {key: reduced["response"][key] for key in ("id", "model", "usage")} == {
        key: response[key] for key in ("id", "model", "usage")}
    assert reduced["response"]["choices"] == [{"index": 0, "finish_reason": "tool_calls"}]

    result = _event("action_result", {"status": "completed", "message": {"content": "kept"}})
    assert post_run_view(result) is result


def test_errand_item_bindings_keep_every_row_a_reader_can_reach():
    rows = [{"product_id": "milk", "name": "牛奶"}, {"product_id": "tea", "name": "茶"},
            {"product_id": "bread", "name": "面包"}, "not-a-row", {"name": "无身份"},
            {"product_id": 7, "name": "整数身份"}, {"product_id": "egg"}, {"product_id": "milk", "name": "牛奶二"}]
    data = {"errand_bindings": [{"product_id": "milk"}, {"product_id": "bread"}, "odd", {"product_id": 7}],
            "errand_item_bindings": rows}
    kept = _reduced("observation", data)["errand_item_bindings"]
    assert kept == [rows[0], rows[2], rows[3], rows[4], rows[5], rows[6], rows[7]]
    assert _reduced("observation", {"errand_item_bindings": {"milk": "牛奶"}})["errand_item_bindings"] == {
        "milk": "牛奶"}
    for bindings in (None, {"product_id": "milk"}, "milk"):
        reduced = _reduced("observation", {"errand_bindings": bindings, "errand_item_bindings": rows})
        assert reduced["errand_item_bindings"] == [rows[3], rows[4], rows[5], rows[6]]
    assert _reduced("observation", {"errand_item_bindings": rows})["errand_item_bindings"] == [
        rows[3], rows[4], rows[5], rows[6]]
    assert "errand_item_bindings" not in _reduced("observation", {"errand_bindings": []})


def _outcome(function, *args):
    """调用结果或抛出的异常（类型与文本），两种结局都能直接比较。"""
    try:
        return "value", function(*args)
    except Exception as error:  # noqa: BLE001 - 比较两侧抛出的同一异常
        return "error", type(error), str(error)


_TODO = {"errands": [{"name": "买牛奶"}, {"name": "取快递"}]}
_ITEMS = [{"product_id": "milk", "name": "牛奶"}, {"product_id": "tea", "name": "茶"}]


@pytest.mark.parametrize("observation", [
    {"todo_layer": _TODO, "errand_bindings": [{"product_id": "milk"}], "errand_item_bindings": _ITEMS},
    {"todo_layer": _TODO, "errand_bindings": [{"product_id": "milk"}],
     "errand_item_bindings": [*_ITEMS, {"product_id": "milk", "name": "牛奶（大）"}, {"product_id": "tea", "name": "绿茶"}]},
    {"errand_bindings": [{"product_id": "milk"}], "errand_item_bindings": [*_ITEMS, "not-a-row"]},
    {"errand_bindings": [{"product_id": "milk"}], "errand_item_bindings": [_ITEMS[1], {"name": "无身份"}, _ITEMS[0]]},
    {"errand_bindings": [{"product_id": 7}], "errand_item_bindings": [_ITEMS[1], {"product_id": 7, "name": "整数"}]},
    {"errand_bindings": [{"product_id": "milk"}], "errand_item_bindings": [_ITEMS[1], {"product_id": "egg"}, _ITEMS[0]]},
    {"errand_bindings": [{"product_id": "milk"}], "errand_item_bindings": {"milk": "牛奶"}},
    {"todo_layer": _TODO, "errand_bindings": None, "errand_item_bindings": _ITEMS},
    {"errand_bindings": {"product_id": "milk"}, "errand_item_bindings": _ITEMS},
    {"errand_bindings": "milk", "errand_item_bindings": _ITEMS},
    {"todo_layer": _TODO, "errand_item_bindings": _ITEMS},
    {"errand_bindings": [{"product_id": "bread"}], "errand_item_bindings": _ITEMS},
], ids=["well-formed", "duplicates", "non-dict-row", "no-product-id", "int-product-id", "no-name", "not-a-list",
        "bindings-none", "bindings-dict", "bindings-string", "bindings-missing", "unknown-product"])
def test_errand_texts_are_equal_or_fail_alike_from_full_and_post_run_observations(observation):
    full = _outcome(errand_metrics._errand_texts, observation)
    reduced = _outcome(errand_metrics._errand_texts, _reduced("observation", observation))
    assert reduced == full


def _history_messages():
    """一轮带历史的决策：上一轮的思考、工具调用与工具结果都在消息里。"""
    return [{"role": "system", "content": "你是一个人。"}, {"role": "user", "content": "上一轮"},
            {"role": "assistant", "content": "", "reasoning_content": "想一想",
             "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "start_main_activity"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "{\"status\": \"accepted\"}"},
            {"role": "user", "content": "这一轮"}]


@pytest.mark.parametrize("request_body", [
    _request(),
    _request(messages=_history_messages()),
    _request(messages=[*_history_messages(), {"role": "user", "content": "上次回复被截断，请重新回答。"}]),
    _request(tools=(), thinking="disabled"),
    _request(tools=("submit_reflection",)),
    _request(purpose="plan_day", tools=("submit_day_plan",)),
    "not-a-request",
    {"messages": [{"role": "user", "content": "x"}], "tools": {"function": {"name": "start_main_activity"}},
     "extra_body": {"thinking": {"type": "enabled"}}},
    {"messages": [{"role": "user"}], "extra_body": {"thinking": {"type": "enabled"}},
     "tools": [{"type": "function"}, {"type": "function", "function": {"name": "start_main_activity"}}]},
    {"messages": ["not-a-message", {"role": "system", "content": "x"}], "tools": ["not-a-tool"]},
], ids=["fresh-turn", "turn-with-history", "truncation-retry", "consolidation", "reflection", "plan-day",
        "non-dict-request", "non-list-tools", "tool-without-function", "non-dict-message"])
def test_request_outline_keeps_every_call_class(request_body):
    for event_type in ("model_request", "model_response"):
        outline = _reduced(event_type, {"phase": "request", "request": request_body})["request"]
        assert _decision_start_is_action_request(outline) == _decision_start_is_action_request(request_body)
        assert _decision_start_request_tool_names(outline) == _decision_start_request_tool_names(request_body)
        assert _roles(outline) == _roles(request_body)
        assert quality._call_class({"request": outline}) == quality._call_class({"request": request_body})
        if isinstance(request_body, dict):
            assert {key: value for key, value in outline.items() if key not in {"messages", "tools"}} == {
                key: value for key, value in request_body.items() if key not in {"messages", "tools"}}
    assert _decision_start_request_tool_names(_request()) == ["start_main_activity", "start_side_activity"]


def _roles(request):
    """请求里各条消息的角色（不是对象的消息原样列出）。"""
    messages = request.get("messages") if isinstance(request, dict) else None
    if not isinstance(messages, list):
        return None
    return [message.get("role") if isinstance(message, dict) else message for message in messages]


@pytest.mark.parametrize("response", [
    {"id": "r", "model": "deepseek-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17,
                                                     "completion_tokens_details": {"reasoning_tokens": 5}},
     "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "x"}}]},
    {"model": "deepseek-pro", "usage": {"completion_tokens": 3}, "choices": ["not-a-choice", {"message": {}}]},
    {"model": 5, "usage": None, "choices": {"index": 0, "finish_reason": "stop"}},
    {"usage": {"completion_tokens": -1}, "choices": []},
], ids=["complete", "non-dict-first-choice", "non-list-choices", "empty-choices"])
def test_finish_reason_usage_and_model_survive_the_response_reduction(response):
    full = _event("model_response", {"phase": "response", "call_id": "call", "purpose": None, "error": None,
                                     "request": None, "response": response})
    view = post_run_view(full)
    reduced = view.data["response"]
    assert quality._finish_reason(reduced) == quality._finish_reason(response)
    for path in (("completion_tokens",), ("completion_tokens_details", "reasoning_tokens"), ("prompt_tokens",)):
        assert quality._usage_count(reduced, path) == quality._usage_count(response, path)
    assert quality._usage_entry(view, "2026-09-15T07:00:00+00:00", "reflection") == quality._usage_entry(
        full, "2026-09-15T07:00:00+00:00", "reflection")
    assert not any(isinstance(choice, dict) and "message" in choice for choice in reduced.get("choices") or [])


def test_post_run_row_matches_the_view_of_the_validated_event():
    events = [
        _event("observation", {key: [key] for key in EIGHT_KEYS} | {
            "errand_bindings": [{"product_id": "milk"}],
            "errand_item_bindings": [{"product_id": "milk", "name": "牛奶"}, {"product_id": "tea", "name": "茶"}],
            "todo_layer": {"errands": []}}, agent_id=1),
        _event("model_request", {"phase": "request", "call_id": "c", "request": _request()}, 2,
               source_kind="model", visibility="system"),
        _event("model_response", {"phase": "response", "call_id": "c", "request": None, "response": {
            "model": "m", "choices": [{"index": 0, "finish_reason": "stop", "message": {"content": "x"}}]}}, 3,
               source_kind="model", visibility="system"),
    ]
    for event in events:
        row = json.loads(canonical_json(event.model_dump()))
        assert post_run_row(row)["data"] == post_run_view(Event.model_validate(row, strict=True)).data
        assert post_run_row(row)["data"] != row["data"]
    action = json.loads(canonical_json(_event("action_result", {"status": "completed"}).model_dump()))
    assert post_run_row(action) is action


def test_no_reduced_key_is_a_key_a_list_level_check_reads():
    read = {"kind", "activity", "data", "content", "status", "checkpoint_id", "ended_at", "interval_start",
            "interval_end", "reused", "verified_experiences", "progress_event_ids", "todo_layer", "errand_bindings",
            "errand_item_bindings", "call_id", "purpose", "phase", "error", "protocol_feedback", "response"}
    assert read.isdisjoint(DROPPED_PAYLOADS["observation"])
    assert DROPPED_PAYLOADS["observation"] == EIGHT_KEYS
