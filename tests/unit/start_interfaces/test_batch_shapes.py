"""一次回复的批次形状：三种合法形状通过，其余组合以 TOOL_CALL_REQUIRED 整批拒绝，原因与改法逐字。"""

from __future__ import annotations

import pytest
from agentsociety2.agent.base import ReactDecision

from tests.integration.runtime.test_life_agent_protocol import (
    _WAIT,
    _WAIT_CHOICE,
    _WORK_CHOICE,
    _control_args,
    _observations,
    _prepared,
    _with_activities,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE

_REASON = ("Submit one world request, start_main_activity together with start_side_activity, "
           "or a batch containing only read queries.")
_FIX = "Submit one world request, the two start tools together, or only read queries."
_RECENT = ("memory_recent", {"limit": 1})
_SEARCH = ("memory_search", {"query": "休息", "limit": 1})
_REPORT = ("report_status", _control_args("report_status", _WAIT))
_MAIN = (MAIN_TOOL, _WAIT_CHOICE)
_SIDE = (SIDE_TOOL, _WORK_CHOICE)


def _feedback(*calls: tuple[str, dict]):
    """宿主对一次回复全部工具调用的整批校验结果（空串即通过）与本回合开放的工具。"""
    observations = _observations()
    _with_activities(observations[0]["data"], (_WAIT, PHASE_ACTIVE))
    agent = _prepared(observations)
    offered = [item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)]
    decisions = [ReactDecision("", name, arguments, "", f"call-{index}")
                 for index, (name, arguments) in enumerate(calls)]
    return agent.validate_react_decisions(decisions, False), offered


@pytest.mark.parametrize("calls", [
    (_RECENT,), (_RECENT, _SEARCH), (_MAIN,), (_SIDE,), (_REPORT,), (_MAIN, _SIDE), (_SIDE, _MAIN),
])
def test_three_legal_batch_shapes_pass(calls):
    """全部是只读查询；恰好一个世界请求；两个开始工具各一次，先后不限。"""
    feedback, offered = _feedback(*calls)
    assert feedback == ""
    assert offered[:2] == [MAIN_TOOL, SIDE_TOOL] and len(offered) == 10 and "start_activity" not in offered


@pytest.mark.parametrize("calls", [
    (), (_MAIN, _MAIN), (_SIDE, _SIDE), (_MAIN, _REPORT), (_REPORT, _SIDE), (_SIDE, _RECENT), (_RECENT, _MAIN),
    (_MAIN, _SIDE, _RECENT), (_MAIN, _SIDE, _REPORT), (_REPORT, _REPORT),
])
def test_other_combinations_are_refused_as_one_batch_with_verbatim_text(calls):
    """同一个开始工具两次、开始加控制、世界请求加只读查询、空批都整批拒绝，不登记任何世界请求。"""
    feedback, offered = _feedback(*calls)
    report = feedback.report
    assert report.error_code == "TOOL_CALL_REQUIRED" and report.reason == _REASON and report.fix == _FIX
    item = report.payload()["items"][0]
    assert item == {"path": ["tool_calls"], "written": [name for name, _ in calls], "error_code": "TOOL_CALL_REQUIRED",
                    "allowed_values": offered, "expected": _REASON,
                    "why": "The call combination violates atomic batch rules.", "candidates": []}


@pytest.mark.parametrize("position", [0, 1])
def test_removed_query_world_next_to_a_valid_call_fails_the_whole_batch(position):
    """已删除的 query_world 与一条合法的开始请求同批：整批以 TOOL_CALL_REQUIRED 纠错，它不是只读查询，批次形状不合法，
    另有一条明细指向 query_world 那一条的工具名；允许值是本回合开放的工具（不含 query_world），合法的那一条也不执行。"""
    removed = ("query_world", {"name": "抖音", "action": None, "category": None, "available_only": False, "offset": 0})
    calls = [_MAIN]
    calls.insert(position, removed)
    feedback, offered = _feedback(*calls)
    assert feedback != "" and "query_world" not in offered
    report = feedback.report.payload()
    assert report["error_code"] == "TOOL_CALL_REQUIRED"
    assert report["items"] == [{
        "path": ["tool_calls"], "written": [name for name, _ in calls], "error_code": "TOOL_CALL_REQUIRED",
        "allowed_values": offered, "expected": _REASON, "why": "The call combination violates atomic batch rules.",
        "candidates": []}, {
        "path": ["tool_calls", position, "function", "name"], "written": "query_world",
        "error_code": "TOOL_CALL_REQUIRED", "allowed_values": offered,
        "expected": "A tool offered in this round is required.", "why": "The tool is not offered in this round.",
        "candidates": []}]


def test_both_start_calls_are_validated_and_reported_at_their_real_positions():
    """两条开始调用各自校验，全部名称问题合并成一次纠错，明细路径以各自真实的调用位置开头。"""
    main = (MAIN_TOOL, main_arguments("travel", "火星", "步行"))
    side = (SIDE_TOOL, side_arguments("interact", "不存在的应用"))
    feedback, _ = _feedback(side, main)
    report = feedback.report.payload()
    assert report["error_code"] == "NAME_NOT_FOUND" and report["fix"] == "Use an exact known name or a candidate."
    assert [item["path"] for item in report["items"]] == [
        ["tool_calls", 0, "function", "arguments", "target", 0], ["tool_calls", 1, "function", "arguments", "target"]]
    assert [item["written"] for item in report["items"]] == ["不存在的应用", "火星"]
    assert report["reason"] == "; ".join(item["why"] for item in report["items"])


def test_request_model_errors_of_both_calls_join_one_correction():
    """detail 为 null 到不了名称解析：由请求模型拒绝，明细不带允许值与候选，改法是改正每个报告的参数；
    用内容 app 而名称数组为空同时违背两条交叉约束，两条都报。"""
    main = (MAIN_TOOL, {"action": "interact", "target": "自己家", "detail": None})
    side = (SIDE_TOOL, {"action": "interact", "target": [], "prefer": None})
    report = _feedback(main, side)[0].report.payload()
    assert report["error_code"] == "ARGUMENTS_INVALID" and report["fix"] == "Correct every reported parameter."
    target = ["tool_calls", 1, "function", "arguments", "target"]
    assert [(item["path"], item["why"], item["allowed_values"], item["candidates"]) for item in report["items"]] == [
        (["tool_calls", 0, "function", "arguments", "detail"], "detail is required when action is interact", [], []),
        (target, "target requires at least one name", [], []),
        (target, "target takes exactly one app name when action is interact", [], [])]


def test_retired_start_tool_is_refused_as_a_tool_not_offered():
    """模型调用已不存在的 start_activity：按本回合未提供的工具纠错。"""
    retired = ("start_activity", {"items": [{"action": "walk", "target": "办公室"}]})
    report = _feedback(retired)[0].report.payload()
    assert report["error_code"] == "TOOL_CALL_REQUIRED"
    named = [item for item in report["items"] if item["path"][-1] == "name"]
    assert named and named[0]["written"] == "start_activity"
    assert "start_activity" not in named[0]["allowed_values"]
    assert named[0]["why"] == "The tool is not offered in this round."
