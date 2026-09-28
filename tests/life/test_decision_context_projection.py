"""完整请求 token 预算与进度层结构化浓缩（docs/dev/SPEC-persona-prompt.md 第四章）的单元与宿主请求测试。

达到上下文 85% 时依次：从旧到新逐条去掉未受保护记录的 self_note；把最旧一段连续的未受保护记录折成一条 condensed
记录（lines 为各记录的 settled 句），重复到阈值以下；每步重新计数。受保护记录与其他五层从不改变；仍超限时按层记录
token 用量并以容量故障停止。
"""

from __future__ import annotations

import copy
import json
import logging

import pytest

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import _context, _observations
from worldmock.life import prompt_capacity
from worldmock.life.narration import render_layer_json
from worldmock.life.prompt_capacity import ProgressBudget


def _call(minute: int, tool: str, note: str | None) -> dict:
    """一条本段停留中的调用记录（可带回复正文 self_note）。"""
    record = {"occurred_at": f"2026-01-05T08:{minute:02d}:00+08:00", "type": "tool_call", "tool": tool,
              "arguments": {}, "result": {"occurred_at": f"2026-01-05T08:{minute:02d}:00+08:00",
                                          "status": "completed", "error_code": None, "message": "ok",
                                          "details": {}}}
    return {**record, "self_note": note} if note is not None else record


def _records() -> tuple[list[dict], list[dict]]:
    """五条记录与逐条元数据：第 2、4 条受保护（在途订单、上次请求之后的新记录），其余可浓缩。"""
    records = [
        _call(0, "start_activity", "先点个外卖。"),
        {"occurred_at": "2026-01-05T08:05:00+08:00", "type": "message_arrival", "sender": "王磊",
         "kind": "direct", "text": "帮我也带一份。"},
        _call(10, "start_activity", "顺便给王磊点。"),
        _call(20, "report_status", "进度写好了。"),
        _call(30, "send_message", "回他一句。"),
    ]
    meta = [{"protected": False, "lines": ["08:00 远程下单牛肉面 1 份，付 32 元"]},
            {"protected": False, "lines": ["08:05 收到王磊的消息：帮我也带一份。"]},
            {"protected": True, "lines": ["08:10 远程下单牛肉面 1 份，付 32 元"]},
            {"protected": False, "lines": []},
            {"protected": True, "lines": ["08:30 给王磊发消息：好的"]}]
    return records, meta


def _budget(records: list[dict], meta: list[dict]) -> tuple[dict, ProgressBudget]:
    """已组装好的请求（system、六层 user 中的工具层与进度层、工具）与对应的可浓缩部分。"""
    layers = ({"layer": "工具层", "targets": []},
              {"layer": "进度层", "session_started_at": "2026-01-05T07:50:00+08:00", "events": records})
    request = {
        "model": prompt_capacity.MODEL_ALIAS,
        "messages": [{"role": "system", "content": "系统层"}, {"role": "user", "content": render_layer_json(layers)}],
        "tools": [{"type": "function", "function": {"name": "start_activity"}}],
        "max_tokens": 100,
    }
    return request, ProgressBudget(layers, tuple(meta))


def _events(candidate: dict) -> list[dict]:
    """请求 user 消息里进度层的记录。"""
    return json.loads(candidate["messages"][1]["content"])[1]["events"]


def _scripted(monkeypatch, values: list[int], seen: list[list[dict]]) -> None:
    """按顺序返回预设的 token 计数，并记下每次计数时进度层的记录。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)

    def measured(candidate):
        seen.append(_events(candidate))
        return values[len(seen) - 1]

    monkeypatch.setattr(prompt_capacity, "request_token_count", measured)


def test_token_counter_receives_complete_messages_tools_and_output_reserve(monkeypatch) -> None:
    """预检把最终消息、工具定义和预留输出计入同一个 token 总数。"""
    calls = []

    def count(*, model, messages, tools):
        """捕获真正交给计数器的模型请求组成。"""
        calls.append((model, messages, tools))
        return 73

    monkeypatch.setattr(prompt_capacity, "token_counter", count)
    request, _ = _budget(*_records())
    assert prompt_capacity.request_token_count(request) == 173
    assert calls == [(prompt_capacity.TOKENIZER_MODEL, request["messages"], request["tools"])]


def test_memory_model_usage_comparison_does_not_require_output_reserve(monkeypatch, caplog) -> None:
    """记忆总结没有输出上限时仍可核对供应商的输入 token 用量。"""
    request = {"model": prompt_capacity.MODEL_ALIAS,
               "messages": [{"role": "system", "content": "总结主观记忆"},
                            {"role": "user", "content": "当天感受"}]}
    monkeypatch.setattr(prompt_capacity, "token_counter", lambda **kwargs: 73)
    with caplog.at_level(logging.INFO):
        prompt_capacity.compare_usage(request, {"usage": {"prompt_tokens": 80}})
    assert any("estimated=73 actual=80 difference=7" in record.message
               for record in caplog.records)


def test_below_threshold_returns_the_request_untouched(monkeypatch) -> None:
    """整次请求低于阈值时原样返回同一请求对象。"""
    request, budget = _budget(*_records())
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 849)
    assert prompt_capacity.fit_request(request, budget) is request


def test_self_notes_are_removed_oldest_first_one_at_a_time_and_protected_notes_stay(monkeypatch) -> None:
    """第一步：未受保护记录的 self_note 从旧到新逐条去掉，每去一条重计；受保护记录的 self_note 不动。"""
    records, meta = _records()
    request, budget = _budget(records, meta)
    original = copy.deepcopy(request)
    seen: list[list[dict]] = []
    _scripted(monkeypatch, [900, 880, 800], seen)

    fitted = prompt_capacity.fit_request(request, budget)

    assert request == original and budget.layers[1]["events"] == records
    assert [[("self_note" in item) for item in events] for events in seen[1:]] == [
        [False, False, True, True, True], [False, False, True, False, True]]
    assert _events(fitted) == seen[-1]
    assert _events(fitted)[2] == records[2] and _events(fitted)[4] == records[4]
    assert fitted["messages"][0] == request["messages"][0] and fitted["tools"] == request["tools"]
    assert json.loads(fitted["messages"][1]["content"])[0] == budget.layers[0]


def test_oldest_unprotected_run_folds_into_one_condensed_record_then_the_next(monkeypatch) -> None:
    """第二步：最旧一段连续的未受保护记录折成一条 condensed（起止时刻与 settled 句），仍超限再折下一段。"""
    records, meta = _records()
    request, budget = _budget(records, meta)
    seen: list[list[dict]] = []
    _scripted(monkeypatch, [900, 890, 880, 870, 800], seen)

    fitted = prompt_capacity.fit_request(request, budget)

    first_fold = seen[3]
    assert first_fold[0] == {"occurred_at": records[0]["occurred_at"], "type": "condensed",
                             "until_at": records[1]["occurred_at"],
                             "lines": meta[0]["lines"] + meta[1]["lines"]}
    assert first_fold[1:] == [records[2], {key: value for key, value in records[3].items() if key != "self_note"},
                              records[4]]
    events = _events(fitted)
    assert [item["type"] for item in events] == ["condensed", "tool_call", "condensed", "tool_call"]
    assert events[2] == {"occurred_at": records[3]["occurred_at"], "type": "condensed",
                         "until_at": records[3]["occurred_at"], "lines": []}
    assert events[1] == records[2] and events[3] == records[4]


def test_an_adjacent_earlier_condensed_record_is_merged_into_the_next_fold(monkeypatch) -> None:
    """紧邻一段未受保护记录之前的 condensed 记录并入这次折叠，行按时间顺序相接。"""
    earlier = {"occurred_at": "2026-01-05T07:55:00+08:00", "type": "condensed",
               "until_at": "2026-01-05T07:58:00+08:00", "lines": ["07:55 到达办公室"]}
    records, meta = _records()
    request, budget = _budget([earlier, *records[:2]], [{"protected": False, "lines": earlier["lines"]}, *meta[:2]])
    seen: list[list[dict]] = []
    _scripted(monkeypatch, [900, 890, 800], seen)

    events = _events(prompt_capacity.fit_request(request, budget))

    assert events == [{"occurred_at": earlier["occurred_at"], "type": "condensed",
                       "until_at": records[1]["occurred_at"],
                       "lines": ["07:55 到达办公室", *meta[0]["lines"], *meta[1]["lines"]]}]


def test_exactly_85_percent_triggers_condensation(monkeypatch) -> None:
    """阈值本身触发浓缩，不等到真正撞满模型窗口。"""
    request, budget = _budget(*_records())
    seen: list[list[dict]] = []
    _scripted(monkeypatch, [850, 800], seen)
    assert "self_note" not in _events(prompt_capacity.fit_request(request, budget))[0]


def test_capacity_failure_keeps_protected_records_and_logs_every_part(monkeypatch, caplog) -> None:
    """浓缩到无可折叠仍超限即容量故障：受保护记录始终完整，日志按系统层、工具、各层、续接消息与预留输出分列。"""
    records, meta = _records()
    request, budget = _budget(records, meta)
    request["messages"].append({"role": "assistant", "content": None, "tool_calls": []})
    seen: list[list[dict]] = []
    _scripted(monkeypatch, [900] * 6, seen)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="model request capacity exceeded"):
        prompt_capacity.fit_request(request, budget)

    assert len(seen) == 5
    assert all(records[2] in events and records[4] in events for events in seen)
    message = next(record.message for record in caplog.records if "capacity exceeded" in record.message)
    for part in ("'system'", "'tools'", "'工具层'", "'进度层'", "'continuation_messages'", "'reserved_output': 100"):
        assert part in message


def test_requests_without_a_progress_layer_fail_without_condensing(monkeypatch, caplog) -> None:
    """只读问答没有进度层，超限直接按容量故障停止，并分层记日志。"""
    layers = ({"layer": "人设层", "habits": []}, {"layer": "记忆层", "earlier_days": [], "earlier_today": []})
    request = {"model": prompt_capacity.MODEL_ALIAS, "max_tokens": 100,
               "messages": [{"role": "system", "content": "只读系统层"},
                            {"role": "user", "content": render_layer_json(layers)}]}
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 950)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="capacity exceeded"):
        prompt_capacity.fit_request(request, ProgressBudget(layers, ()))
    assert "'人设层'" in caplog.text and "'记忆层'" in caplog.text


def test_misaligned_progress_metadata_is_a_corrupt_host_observation(monkeypatch) -> None:
    """进度层记录与私有元数据条数不一致时不猜测受保护集，明确失败。"""
    records, meta = _records()
    request, budget = _budget(records, meta[:-1])
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 900)
    with pytest.raises(RuntimeError, match="progress metadata does not align"):
        prompt_capacity.fit_request(request, budget)


def test_host_request_condenses_progress_from_observation_metadata(monkeypatch) -> None:
    """宿主实际请求：受保护标记与 settled 句取自观察私有证据 progress_meta，浓缩只改 user 中的进度层。"""
    records, meta = _records()
    observations = _observations()
    observations[0]["data"]["progress_layer"]["events"] = records
    observations[0]["data"]["progress_meta"] = meta
    agent = LifeAgent()
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._model_name = prompt_capacity.MODEL_ALIAS
    request = ReactLoopRequest(0, _context().observed_at, observations)
    agent.prepare_react_loop(request)
    messages = agent.build_react_messages(request)
    tools = agent.build_react_tool_schemas(readonly=False)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    counts = iter([900, 890, 880, 800])
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: next(counts))

    sent = agent._model_request(messages, tools)

    layers = json.loads(sent["messages"][1]["content"])
    original = json.loads(messages[1]["content"])
    assert layers[:5] == original[:5]
    assert [item["type"] for item in layers[5]["events"]] == ["condensed", "tool_call", "tool_call", "tool_call"]
    assert layers[5]["events"][1] == records[2] and layers[5]["events"][3] == records[4]
    assert "progress_meta" not in sent["messages"][1]["content"]
    assert sent["messages"][0] == messages[0] and sent["tools"] == tools
