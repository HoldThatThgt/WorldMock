"""完整请求 token 预算与仅进度历史浓缩的纯单元测试。"""

from __future__ import annotations

import copy
import logging

import pytest

from worldmock.life import prompt_capacity
from worldmock.life.narration import render_facts


def _session(name: str, phase: str, outcome: str) -> dict:
    """构造符合公开进度层合同的可读会话行。"""
    return {
        "task_name": name,
        "session_name": f"{name}：上午九点开始的工作",
        "phase": phase,
        "objective_progress": outcome,
        "subjective_progress": "我会继续检查。",
        "subjective_status": "未经核验",
    }


def _facts() -> dict:
    """提供两个任务的终态历史、一个当前会话及两条本轮新结果。"""
    return {
        "location": "办公室",
        "progress": {
            "sessions": [
                _session("当前工作", "进行中", "已经投入半小时"),
                _session("周会纪要", "已结束", "完成第二稿"),
                _session("周会纪要", "已结束", "完成第一稿"),
                _session("采购午餐", "已结束", "已完成购买"),
            ],
            "results": [
                {"occurred_at": 1, "status": "rejected", "error_code": "OUT_OF_STOCK",
                 "message": "刚才购买失败：已售罄"},
                {"occurred_at": 2, "status": "completed", "error_code": None,
                 "message": "刚才到达办公室"},
            ],
            "alarms": ["周会纪要即将截止"],
        },
    }


def _request(facts: dict) -> dict:
    """模拟已经完成 system、user 和工具组装的模型请求。"""
    return {
        "model": prompt_capacity.MODEL_ALIAS,
        "messages": [
            {"role": "system", "content": "系统说明"},
            {"role": "user", "content": "人物卡\n\n场景\n\n" + render_facts(facts)},
        ],
        "tools": [{"type": "function", "function": {"name": "start_activity"}}],
        "max_tokens": 100,
    }


def test_token_counter_receives_complete_messages_tools_and_output_reserve(monkeypatch) -> None:
    """预检把最终消息、工具定义和预留输出计入同一个 token 总数。"""
    calls = []

    def count(*, model, messages, tools):
        """捕获真正交给计数器的模型请求组成。"""
        calls.append((model, messages, tools))
        return 73

    monkeypatch.setattr(prompt_capacity, "token_counter", count)
    request = _request(_facts())
    assert prompt_capacity.request_token_count(request) == 173
    assert calls == [(prompt_capacity.TOKENIZER_MODEL, request["messages"], request["tools"])]


def test_compaction_groups_only_terminal_sessions_and_preserves_source() -> None:
    """按任务合并历史，当前进度、新结果和提醒均保持原样，输入不变。"""
    progress = _facts()["progress"]
    original = copy.deepcopy(progress)
    compacted = prompt_capacity.compact_by_task(progress)
    assert progress == original
    assert compacted["sessions"][0] == original["sessions"][0]
    assert len(compacted["sessions"]) == 3
    assert compacted["sessions"][1]["task_name"] == "周会纪要"
    assert compacted["sessions"][1]["session_count"] == 2
    assert compacted["sessions"][1]["latest_objective_progress"] == "完成第二稿"
    assert compacted["results"] == original["results"]
    assert compacted["alarms"] == original["alarms"]


def test_second_compaction_collapses_history_but_not_fresh_results() -> None:
    """第二阶段只进一步浓缩终态历史，不抹掉当前会话或本轮执行反馈。"""
    progress = _facts()["progress"]
    grouped = prompt_capacity.compact_by_task(progress)
    aggregate = prompt_capacity.compact_all_history(grouped)
    assert len(aggregate["sessions"]) == 2
    assert aggregate["sessions"][0] == progress["sessions"][0]
    assert aggregate["sessions"][1]["session_count"] == 3
    assert aggregate["results"] == progress["results"]
    assert aggregate["alarms"] == progress["alarms"]


def test_85_percent_trigger_recounts_and_only_replaces_progress(monkeypatch) -> None:
    """完整请求触阈值后做两段浓缩，其他消息和事实保持字节一致。"""
    facts = _facts()
    request = _request(facts)
    original_facts, original_request = copy.deepcopy(facts), copy.deepcopy(request)
    counts = []

    def measured(candidate):
        """模拟原始、按任务浓缩和全历史浓缩三个不同 token 占用。"""
        content = candidate["messages"][1]["content"]
        if '"phase":"已结束"' in content:
            value = 900
        elif content.count('"phase":"历史摘要"') > 1:
            value = 870
        else:
            value = 700
        counts.append(value)
        return value

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", measured)
    fitted = prompt_capacity.fit_request(request, facts, {"scene": "场景"})
    assert counts == [900, 870, 700]
    assert facts == original_facts and request == original_request
    assert fitted["messages"][0] == request["messages"][0]
    assert fitted["tools"] == request["tools"]
    assert fitted["max_tokens"] == request["max_tokens"]
    user = fitted["messages"][1]["content"]
    assert '"location":"办公室"' in user
    assert "当前工作" in user and "周会纪要即将截止" in user
    assert user.index("刚才购买失败") < user.index("刚才到达办公室")
    assert user.count('"phase":"历史摘要"') == 1


def test_below_threshold_does_not_touch_progress(monkeypatch) -> None:
    """整次请求低于阈值时，原始会话投影和请求对象保持不变。"""
    facts = _facts()
    request = _request(facts)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 849)
    assert prompt_capacity.fit_request(request, facts) is request
    assert request["messages"][1]["content"].count('"phase":"已结束"') == 3


def test_exactly_85_percent_triggers_compaction(monkeypatch) -> None:
    """阈值本身触发按任务压缩，不等到真正撞满模型窗口。"""
    facts = _facts()
    request = _request(facts)
    counts = []

    def measured(candidate):
        """原始请求恰好 85%，按任务浓缩后低于阈值。"""
        value = 850 if '"phase":"已结束"' in candidate["messages"][1]["content"] else 800
        counts.append(value)
        return value

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", measured)
    fitted = prompt_capacity.fit_request(request, facts)
    assert counts == [850, 800]
    assert '"phase":"历史摘要"' in fitted["messages"][1]["content"]


def test_capacity_failure_retains_all_fresh_results_and_reports_layers(monkeypatch, caplog) -> None:
    """即使终态历史全部合并后仍达阈值，也不能删新反馈或改输入。"""
    facts = _facts()
    request = _request(facts)
    original = copy.deepcopy(request)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 900)
    monkeypatch.setattr(prompt_capacity, "_layer_counts", lambda layers, candidate: {
        "scene": 120, "progress": 400, "reserved_output": 100,
    })
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="capacity exceeded"):
        prompt_capacity.fit_request(request, facts, {"scene": "场景"})
    assert request == original
    assert len(facts["progress"]["results"]) == 2
    assert any("progress" in record.message and "reserved_output" in record.message
               for record in caplog.records)
