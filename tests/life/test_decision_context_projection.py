"""完整请求 token 预算与仅进度历史浓缩的纯单元测试。

浓缩单位是会话（docs/dev/SPEC-time-and-energy.md 第七章「会话与活动的字段」）：超过窗口 85% 时先按会话合并较早会话里
已收尾的活动，仍超限再合并全部已收尾活动；进行中、暂停与刚结束（待收尾）的活动、新结果与提醒完整保留。
"""

from __future__ import annotations

import copy
import logging

import pytest

from worldmock.life import prompt_capacity
from worldmock.life.activity_control import SELF_REPORT_MARK
from worldmock.life.narration import render_facts


def _activity(name: str, phase: str, outcome: str) -> dict:
    """构造符合公开进度层合同的可读活动行。"""
    return {
        "name": name,
        "phase": phase,
        "objective_progress": outcome,
        "subjective_progress": "我会继续检查。",
        "subjective_status": SELF_REPORT_MARK,
    }


def _history(count: int, outcome: str) -> dict:
    """按会话或全部历史合并出的一行摘要：条数与最近一条的客观进度及注明来源的自述。"""
    return {"name": f"较早收尾的{count}项活动", "phase": "历史摘要", "activity_count": count,
            "latest_objective_progress": outcome, "latest_subjective_progress": "我会继续检查。",
            "subjective_status": SELF_REPORT_MARK}


def _facts() -> dict:
    """三段会话（办公室、去饭馆的路上、饭馆）的活动行与两条本轮新结果。

    较早两段各有已收尾的活动，办公室还留着一项暂停的活动；最近一段有进行中、刚结束与已收尾的活动。
    """
    return {
        "location": "菜饭骨头汤",
        "progress": {
            "sessions": [
                {"name": "张江办公室（1月5日 09:00 起）", "activities": [
                    _activity("工作周会纪要（1月5日 09:00 起）", "已收尾", "已做约1小时，有效约50分钟"),
                    _activity("阅读邮件（1月5日 10:00 起）", "已暂停", "已做约10分钟，还剩约20分钟"),
                    _activity("工作周会纪要（1月5日 10:30 起）", "已收尾", "已做约2小时，有效约1.5小时"),
                ]},
                {"name": "去菜饭骨头汤的路上（1月5日 12:30 起）", "activities": [
                    _activity("步行菜饭骨头汤（1月5日 12:30 起）", "已收尾", "已做约10分钟"),
                    _activity("观看Sintel（1月5日 12:30 起）", "已收尾", "已做约10分钟"),
                ]},
                {"name": "菜饭骨头汤（1月5日 12:40 起）", "activities": [
                    _activity("浏览菜单（1月5日 12:40 起）", "已收尾", "已做约2分钟"),
                    _activity("吃饭番茄炒蛋（1月5日 12:42 起）", "进行中", "已做约5分钟，还剩约15分钟"),
                    _activity("观看Space Habitat（1月5日 12:42 起）", "刚结束", "已做约5分钟"),
                ]},
            ],
            "results": [
                {"occurred_at": 1, "status": "rejected", "error_code": "OUT_OF_STOCK",
                 "message": "刚才购买失败：已售罄"},
                {"occurred_at": 2, "status": "completed", "error_code": None,
                 "message": "刚才到达菜饭骨头汤"},
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


def test_compaction_merges_concluded_activities_per_older_session_and_preserves_source() -> None:
    """第一阶段按会话合并较早会话里已收尾的活动；暂停与最近一段会话的活动、新结果和提醒原样保留，输入不变。"""
    progress = _facts()["progress"]
    original = copy.deepcopy(progress)
    compacted = prompt_capacity.compact_by_session(progress)
    assert progress == original
    office, road, restaurant = compacted["sessions"]
    assert [row["name"] for row in compacted["sessions"]] == [row["name"] for row in original["sessions"]]
    assert office["activities"] == [original["sessions"][0]["activities"][1], _history(2, "已做约2小时，有效约1.5小时")]
    assert road["activities"] == [_history(2, "已做约10分钟")]
    assert restaurant == original["sessions"][2]
    assert compacted["results"] == original["results"]
    assert compacted["alarms"] == original["alarms"]


def test_second_compaction_collapses_history_but_keeps_live_activities_and_fresh_results() -> None:
    """第二阶段把全部已收尾活动（含第一阶段的摘要行）并成一行「历史活动」；进行中、暂停与刚结束的活动、
    最近一段会话、本轮新结果和提醒都保留，没有活动剩下的较早会话不再列出。"""
    progress = _facts()["progress"]
    aggregate = prompt_capacity.compact_all_history(prompt_capacity.compact_by_session(progress))
    office, _, restaurant = progress["sessions"]
    assert aggregate["sessions"] == [
        {"name": "历史活动", "activities": [_history(5, "已做约2分钟")]},
        {"name": office["name"], "activities": [office["activities"][1]]},
        {"name": restaurant["name"], "activities": restaurant["activities"][1:]},
    ]
    assert [row["phase"] for row in aggregate["sessions"][2]["activities"]] == ["进行中", "刚结束"]
    assert aggregate["results"] == progress["results"]
    assert aggregate["alarms"] == progress["alarms"]


def _history_rows(candidate: dict) -> int:
    """请求 user 消息中历史摘要行的条数（原始 0，按会话浓缩后 2，全部浓缩后 1）。"""
    return candidate["messages"][1]["content"].count('"phase":"历史摘要"')


def test_85_percent_trigger_recounts_and_only_replaces_progress(monkeypatch) -> None:
    """完整请求触阈值后做两段浓缩，每段重计完整请求；其他消息和事实保持字节一致。"""
    facts = _facts()
    request = _request(facts)
    original_facts, original_request = copy.deepcopy(facts), copy.deepcopy(request)
    counts = []

    def measured(candidate):
        """模拟原始、按会话浓缩和全历史浓缩三个不同 token 占用。"""
        value = {0: 900, 2: 870, 1: 700}[_history_rows(candidate)]
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
    assert '"location":"菜饭骨头汤"' in user
    assert "吃饭番茄炒蛋" in user and "阅读邮件" in user and "观看Space Habitat" in user
    assert "周会纪要即将截止" in user and '"phase":"已收尾"' not in user
    assert user.index("刚才购买失败") < user.index("刚才到达菜饭骨头汤")
    assert user.count('"phase":"历史摘要"') == 1


def test_below_threshold_does_not_touch_progress(monkeypatch) -> None:
    """整次请求低于阈值时，原始进度投影和请求对象保持不变。"""
    facts = _facts()
    request = _request(facts)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 849)
    assert prompt_capacity.fit_request(request, facts) is request
    assert request["messages"][1]["content"].count('"phase":"已收尾"') == 5
    assert _history_rows(request) == 0


def test_exactly_85_percent_triggers_compaction(monkeypatch) -> None:
    """阈值本身触发按会话浓缩，不等到真正撞满模型窗口。"""
    facts = _facts()
    request = _request(facts)
    counts = []

    def measured(candidate):
        """原始请求恰好 85%，按会话浓缩后低于阈值。"""
        value = 850 if _history_rows(candidate) == 0 else 800
        counts.append(value)
        return value

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", measured)
    fitted = prompt_capacity.fit_request(request, facts)
    assert counts == [850, 800]
    assert _history_rows(fitted) == 2
    assert '"phase":"已暂停"' in fitted["messages"][1]["content"]


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
