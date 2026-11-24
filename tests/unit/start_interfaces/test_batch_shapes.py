"""@brief 一次回复的批次形状与工具面门禁：四种合法形状通过，其余组合以 TOOL_CALL_REQUIRED 整批拒绝，末回合或重试收敛至六项核心工具。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from worldmock.agents.life_agent import (
    LifeAgent,
    PersonInputs,
    life_action_tool_names,
    life_query_tool_names,
    life_tool_schema_sha256,
    validate_life_batch,
)
from worldmock.agents.protocol.agent_loop import (
    LoopToolCall,
    combine_reports,
    parse_tool_calls,
    validate_single_tool_call,
)
from worldmock.state.interaction.actions import SEARCH_TOOL, START_TOOLS
from worldmock.state.interaction.errors import bind_report
from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS

MAIN_TOOL, SIDE_TOOL = START_TOOLS
_FULL_MODELS = {**REQUEST_MODELS, **QUERY_MODELS}
_REASON = ("Submit one world request, start_main_activity together with start_side_activity, "
           "or a batch containing only read queries.")
_FIX = "Submit one world request, the two start tools together, or only read queries."

_RECENT = ("search_memory", {"category": "全部", "time": "今天", "keyword": None})
_SEARCH_MEM = ("search_memory", {"category": "生活", "time": "全部", "keyword": "休息"})
_RECALL = ("recall_task_summary", {"task_name": "买牛奶"})
_REPORT = ("report_status", {
    "items": [{"activity_name": "在办公室工作", "summary": "写完了两页。"}],
    "errands": [],
})
_MAIN = (MAIN_TOOL, {"action": "sleep", "target": "420", "detail": None})
_SIDE = (SIDE_TOOL, {"action": "interact", "target": ["微博"], "prefer": None})
_WORLD_SEARCH = (SEARCH_TOOL, {"query": "热美式咖啡", "prefer": None})


def _agent() -> LifeAgent:
    """@brief 不含任何 I/O 的最小冻结人设输入人物适配器。"""
    return LifeAgent(PersonInputs(1, "林舟", "林舟，设计师。", {}))


def _evaluate(*calls: tuple[str, dict], remaining: int = 16, retry: bool = False):
    """@brief 用通用解析与 LifeAgent 批次规则校验整组原始工具调用。"""
    offered_schemas = _agent().turn_tools(remaining, retry)
    offered_names = [item["function"]["name"] for item in offered_schemas]
    models = {name: _FULL_MODELS[name] for name in offered_names}
    raw_calls = [
        SimpleNamespace(id=f"call-{index}", type="function",
                        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))
        for index, (name, args) in enumerate(calls)
    ]
    indexed, parse_reports = parse_tool_calls(raw_calls, models)
    reports = []
    if calls:
        stub = [LoopToolCall(name, {}, f"call-{index}") for index, (name, _) in enumerate(calls)]
        batch_fb = validate_life_batch(stub, models)
        if batch_fb is not None:
            reports.append(batch_fb.report)
    reports.extend(parse_reports)
    for index, call in indexed:
        call_fb = validate_single_tool_call(call, models)
        if call_fb is not None:
            reports.append(bind_report(call_fb.report, index))
    feedback = combine_reports(reports) if reports else None
    return feedback, offered_names


def test_tool_surface_exposes_nine_tools_normally_and_six_on_forced_final_turn():
    """@brief 常规回合开放 9 个工具（含 search_world），末回合与截断重试仅保留 6 个核心世界工具。"""
    agent = _agent()
    normal = [item["function"]["name"] for item in agent.turn_tools(2, False)]
    forced = [item["function"]["name"] for item in agent.turn_tools(1, False)]
    retried = [item["function"]["name"] for item in agent.turn_tools(4, True)]
    assert len(normal) == 9 and normal[:2] == [MAIN_TOOL, SIDE_TOOL]
    assert set(normal) == set(_FULL_MODELS)
    assert forced == retried
    assert len(forced) == 6 and SEARCH_TOOL not in forced
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    assert life_query_tool_names() == frozenset({"recall_task_summary", "search_memory"})
    assert len(life_tool_schema_sha256()) == 64


@pytest.mark.parametrize("calls", [
    (_RECENT,),
    (_RECENT, _SEARCH_MEM),
    (_RECENT, _RECALL),
    (_MAIN,),
    (_SIDE,),
    (_WORLD_SEARCH,),
    (_REPORT,),
    (_MAIN, _SIDE),
    (_SIDE, _MAIN),
])
def test_legal_batch_shapes_pass(calls):
    """@brief 全为只读查询、单个世界请求、单独 search_world 或两个开始工具成组都合法通过。"""
    feedback, offered = _evaluate(*calls)
    assert feedback is None
    assert offered[:2] == [MAIN_TOOL, SIDE_TOOL] and len(offered) == 9 and "start_activity" not in offered


@pytest.mark.parametrize("calls", [
    (),
    (_MAIN, _MAIN),
    (_SIDE, _SIDE),
    (_WORLD_SEARCH, _WORLD_SEARCH),
    (_MAIN, _WORLD_SEARCH),
    (_WORLD_SEARCH, _SIDE),
    (_WORLD_SEARCH, _RECENT),
    (_MAIN, _REPORT),
    (_REPORT, _SIDE),
    (_SIDE, _RECENT),
    (_RECENT, _MAIN),
    (_MAIN, _SIDE, _RECENT),
    (_MAIN, _SIDE, _REPORT),
    (_REPORT, _REPORT),
])
def test_illegal_combinations_are_refused_as_one_batch(calls):
    """@brief 重复开始工具、多次 search_world、混发 search_world 或混合查询与世界调用均整批拒绝。"""
    feedback, offered = _evaluate(*calls)
    assert feedback is not None
    report = feedback.report
    assert report.error_code == "TOOL_CALL_REQUIRED"
    if calls:
        assert report.reason == _REASON and report.fix == _FIX
        assert report.payload()["items"][0] == {
            "path": ["tool_calls"], "written": [name for name, _ in calls], "error_code": "TOOL_CALL_REQUIRED",
            "allowed_values": offered, "expected": _REASON,
            "why": "The call combination violates atomic batch rules.", "candidates": [],
        }
    else:
        assert report.payload()["items"][0]["path"] == ["tool_calls"]


@pytest.mark.parametrize("position", [0, 1])
def test_removed_query_world_next_to_valid_call_reports_batch_and_position(position):
    """@brief 已删除的 query_world 与合法开始调用混批时同时报告整批违法与真实的未开放工具位置。"""
    calls = [_MAIN]
    calls.insert(position, ("query_world", {"name": "抖音"}))
    feedback, offered = _evaluate(*calls)
    assert feedback is not None and "query_world" not in offered
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


def test_request_model_errors_of_both_start_calls_join_one_correction():
    """@brief 开始组两条参数均违反请求模型时保留各自真实位置、允许值与错误合并。"""
    main = (MAIN_TOOL, {"action": "interact", "target": "自己家", "detail": None})
    side = (SIDE_TOOL, {"action": "interact", "target": [], "prefer": None})
    feedback, _ = _evaluate(main, side)
    report = feedback.report.payload()
    assert report["error_code"] == "ARGUMENTS_INVALID" and report["fix"] == "Correct every reported parameter."
    target = ["tool_calls", 1, "function", "arguments", "target"]
    assert [(item["path"], item["why"], item["allowed_values"], item["candidates"]) for item in report["items"]] == [
        (["tool_calls", 0, "function", "arguments", "detail"], "detail is required when action is interact", [], []),
        (target, "target requires at least one name", [], []),
        (target, "target takes exactly one app name when action is interact", [], [])]


def test_retired_start_tool_is_refused_as_not_offered():
    """@brief 已退场的 start_activity 工具按未开放工具报错。"""
    feedback, offered = _evaluate(("start_activity", {"items": [{"action": "walk", "target": "办公室"}]}))
    report = feedback.report.payload()
    assert report["error_code"] == "TOOL_CALL_REQUIRED"
    named = [item for item in report["items"] if item["path"][-1] == "name"]
    assert named and named[0]["written"] == "start_activity"
    assert named[0]["allowed_values"] == offered
    assert named[0]["why"] == "The tool is not offered in this round."
