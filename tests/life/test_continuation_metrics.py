"""@brief 续接对比子命令（scripts/inspect_run.py continuation，docs/dev/SPEC-persona-prompt.md 第七章「续接对比」）。

逐决策给出链位置、请求数、reasoning_tokens（缺失时报字符数，两者不混算）、协议错误与传输失败（400 单列）、缓存命中与
计价成本（与质量报告同一价目）、话题命中、工具是否与链首相同、按订单去重后的重复下单与链完整性；只读连接；--baseline
核对两份清单，除续接开关外有任何不同即拒绝比较。真实链路用例经真实 LifeAgent、LifeEngine 与 SQLite 产生运行库，
手工用例用存储写入事件，覆盖真实链路走不到的取值（协议错误、400、超时、断链、重复下单）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_life_agent_protocol import _ModelResponse
from tests.integration.test_same_second_continuation import (_PLAN, _WAIT, _environment, _single,
                                                             _fixed_context_window)  # noqa: F401
from tests.life.conftest import advance_until_ready, pending_names, stage_day_plan
from worldmock.footprints.models import EventDraft
from worldmock.footprints.quality import _estimate_cost, build_quality_report
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import DeliveryState

REPO_ROOT = Path(__file__).resolve().parents[2]
#: 子进程以 UTF-8 输出（报告含中文话题）。
_UTF8 = {**os.environ, "PYTHONIOENCODING": "utf-8"}
_TOOLS = [{"type": "function", "function": {"name": "start_activity"}}]
_THINKING = {"thinking": {"type": "enabled"}}


def _script():
    """@brief 按文件加载只读统计脚本。"""
    spec = importlib.util.spec_from_file_location("inspect_run", REPO_ROOT / "scripts" / "inspect_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(run_dir: Path) -> dict:
    """@brief 以只读连接计算续接对比报告。"""
    module = _script()
    conn = module._connect(run_dir)
    try:
        return module.continuation_report(conn)
    finally:
        conn.close()


class _PricedResponse(_ModelResponse):
    """@brief 带实际模型名与提供商用量的模型响应。"""

    def __init__(self, call_id: str, name: str, arguments: dict, reasoning: str, usage: dict) -> None:
        super().__init__(call_id, name, json.dumps(arguments, ensure_ascii=False), reasoning_content=reasoning)
        self._usage = usage

    def model_dump(self, *, mode: str) -> dict:
        """@brief 最小 JSON 响应另带 model 与 usage。"""
        return {**super().model_dump(mode=mode), "model": "deepseek-flash", "usage": self._usage}


def _usage(prompt: int, hit: int, completion: int, reasoning_tokens: int | None) -> dict:
    """@brief 提供商用量：输入（缓存命中与未命中）、输出与可选的 reasoning_tokens。"""
    usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
             "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": prompt - hit}
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return usage


_THINK_A = "有点困，想下午喝杯咖啡，先写进计划。"
_THINK_B = "计划写好了，先歇一会儿。"
_THINK_C = "等完了，收尾。"


async def _live_run(engine) -> None:
    """@brief 真实三次决策：同一秒修订计划、续接开始等待，时间推进后收尾（最后一次响应不报 reasoning_tokens）。"""
    stage_day_plan(engine)
    environment = _environment(engine)
    await _single(environment, "plan", [_PricedResponse("call-a", "revise_day_plan", _PLAN, _THINK_A,
                                                        _usage(1000, 0, 200, 150))])
    await _single(environment, "wait", [_PricedResponse("call-b", "start_activity", _WAIT, _THINK_B,
                                                        _usage(1300, 1000, 90, 60))])
    assert advance_until_ready(engine)

    def conclude(observation: dict) -> list:
        names = pending_names(observation)
        arguments = {"task": names, "summary": ["歇好了。" for _ in names], "memories": [[] for _ in names]}
        return [_PricedResponse("call-c", "conclude_activity", arguments, _THINK_C, _usage(1100, 0, 80, None))]

    await _single(environment, "later", conclude)


@pytest.mark.asyncio
async def test_a_real_run_reports_chain_positions_reasoning_cache_cost_and_topics(engine_factory) -> None:
    """真实运行：链首位置 0、同秒续接位置 1 且链完整、工具与链首相同；token 与字符分列不混算；缓存命中如实合计；
    成本与质量报告的冻结价目逐次一致；话题按决策计。"""
    engine = engine_factory()
    await _live_run(engine)
    report = _report(engine.store.run_dir)
    first, second, third = report["decisions"]
    assert (first["chain_position"], first["chain_intact"], first["same_second_follow_up"]) == (0, None, False)
    assert (second["chain_position"], second["chain_intact"], second["same_second_follow_up"]) == (1, True, True)
    assert second["tools_same_as_head"] is True and first["tools_same_as_head"] is None
    assert (third["chain_position"], third["same_second_follow_up"]) == (0, False)
    assert [row["requests"] for row in report["decisions"]] == [1, 1, 1]
    assert [(row["reasoning_tokens"], row["reasoning_chars"]) for row in report["decisions"]] == [
        (150, None), (60, None), (None, len(_THINK_C))]
    assert [row["cache_hit_tokens"] for row in report["decisions"]] == [0, 1000, 0]
    assert [row["topics"] for row in report["decisions"]] == [{"咖啡": True}, {"咖啡": False}, {"咖啡": False}]
    summary = report["summary"]
    assert summary["follow_up_reasoning"] == {"reasoning_tokens": 60, "decisions_with_tokens": 1,
                                              "reasoning_chars": 0, "decisions_with_chars": 0}
    assert summary["other_reasoning"] == {"reasoning_tokens": 150, "decisions_with_tokens": 1,
                                          "reasoning_chars": len(_THINK_C), "decisions_with_chars": 1}
    assert (summary["continued"], summary["longest_chain"], summary["broken_chains"]) == (1, 2, 0)
    assert summary["topic_hits"] == {"咖啡": 1} and summary["tools_same_as_head"] == 1
    events = engine.store.read_events(engine.store.last_event_seq)
    quality = build_quality_report(events, [], engine.store.manifest)["model_usage"]["estimated_cost"]
    assert summary["unpriced_responses"] == 0 and summary["known_cost_usd"] == quality["known_estimate_usd"]
    responses = [event for event in events if event.event_type == "model_response"]
    requests = {event.event_id: event for event in events if event.event_type == "model_request"}
    expected = [_estimate_cost("deepseek-flash", event.data["response"]["usage"],
                               requests[event.caused_by[0]].recorded_at)[0] for event in responses]
    assert [row["cost_usd"] for row in report["decisions"]] == expected


def test_the_subcommand_opens_the_run_read_only(engine_factory) -> None:
    """只读连接拒绝写入；子命令运行前后足迹库字节不变，并输出 JSON 报告。"""
    engine = engine_factory()
    stage_day_plan(engine)
    run_dir = engine.store.run_dir
    engine.close()
    database = run_dir / "footprints.sqlite"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    conn = _script()._connect(run_dir)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM events")
    finally:
        conn.close()
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "inspect_run.py"), str(run_dir),
                             "continuation"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                            env=_UTF8)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decisions"] == []
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def _request(at: int, decision: str, messages: list[dict]) -> EventDraft:
    """@brief 一次行动请求（没有 purpose、带 start_activity、思考开启）。"""
    return EventDraft(event_type="model_request", occurred_at=at, agent_id=1, decision_id=decision,
                      visibility="system", source_kind="model",
                      data={"phase": "request", "call_id": f"{decision}:{len(messages)}", "attempt": 1,
                            "request": {"model": "deepseek-flash", "messages": messages, "tools": _TOOLS,
                                        "extra_body": _THINKING}, "response": None, "error": None})


def _terminal(request: EventDraft, response: dict | None, error: str | None) -> EventDraft:
    """@brief 请求的终态：有响应时为 response（error 为协议错误），没有响应时为传输失败。"""
    return EventDraft(event_type="model_response", occurred_at=request.occurred_at, agent_id=1,
                      decision_id=request.decision_id, caused_by=[request.event_id], visibility="system",
                      source_kind="model", data={"phase": "response" if response else "failure",
                                                 "call_id": request.data["call_id"], "attempt": 1,
                                                 "request": request.data["request"], "response": response,
                                                 "error": error})


def _response(reasoning: str, reasoning_tokens: int | None) -> dict:
    """@brief 一次提供商响应：思考原文、一个世界请求与可选的 reasoning_tokens。"""
    call = {"id": "call", "type": "function", "function": {"name": "start_activity", "arguments": "{}"}}
    usage = {} if reasoning_tokens is None else {"completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}
    return {"choices": [{"message": {"content": "", "reasoning_content": reasoning, "tool_calls": [call]}}],
            "usage": usage}


def _order(order_id: str, product_id: str, created_at: int) -> dict:
    """@brief 一个订单的状态快照（动作结果 data.orders 的元素）。"""
    return DeliveryState(
        order_id=order_id, agent_id=1, product_id=product_id, supply_id=f"supply-{product_id}",
        location_id="shop", seller_account_id="seller:shop", price_version="v1", route_id=None,
        source={"kind": "fixture"}, quantity=1, destination_id="office", fulfillment="delivery",
        created_at=created_at, expected_at=created_at + 900, status="pending",
        total_price_fen=2800, recipient_id="agent:1").model_dump(mode="json")


def _purchase(at: int, decision: str, action: str, orders: list[dict]) -> list[EventDraft]:
    """@brief 一次下单请求与结果（结果顶层与逐项结果都可能带同一订单）。"""
    requested = EventDraft(event_type="action_requested", occurred_at=at, agent_id=1, decision_id=decision,
                           action_id=action, source_kind="model", data={
                               "tool_name": "start_activity", "request_sha256": "0" * 64, "reused": False,
                               "request": {"targets": ["店"], "actions": ["obtain"], "ways": ["远程下单"]}})
    result = EventDraft(event_type="action_result", occurred_at=at, agent_id=1, decision_id=decision,
                        action_id=action, caused_by=[requested.event_id], data={
                            "action_id": action, "agent_id": 1, "status": "completed", "occurred_at": at,
                            "message": "Purchase accepted", "error_code": None,
                            "data": {"orders": orders[:1], "activity_results": [{"orders": orders}]}})
    return [requested, result]


def _synthetic_run(run_dir: Path, manifest: dict | None = None) -> Path:
    """@brief 手工运行库：协议错误后成功、链首之后前缀不符的续接（400 与超时）、同一商品同日两单与次日一单。"""
    head = [{"role": "system", "content": "系统层"}, {"role": "user", "content": "六层"}]
    first, retry = _request(100, "d1", head), _request(100, "d1", [*head, {"role": "user", "content": "纠错"}])
    wrong = [{"role": "system", "content": "系统层"}, {"role": "user", "content": "别的六层"},
             {"role": "assistant", "content": "", "reasoning_content": "想想", "tool_calls": []},
             {"role": "tool", "tool_call_id": "call", "content": "{}"}]
    broken, timeout = _request(100, "d2", wrong), _request(100, "d2", [*wrong, {"role": "user", "content": "再试"}])
    later = _request(200, "d3", head)
    drafts = [first, _terminal(first, _response("想点咖啡", None), "invalid start_activity arguments"),
              retry, _terminal(retry, _response("还是咖啡吧", 40), None),
              *_purchase(100, "d1", "buy-1", [_order("o1", "coffee", 100)]),
              broken, _terminal(broken, None, "litellm.BadRequestError: DeepseekException - 400 missing reasoning"),
              timeout, _terminal(timeout, None, "TimeoutError()"),
              later, _terminal(later, _response("再来一杯", 7), None),
              *_purchase(200, "d3", "buy-2", [_order("o2", "coffee", 200), _order("o1", "coffee", 100)]),
              *_purchase(90000, "d3", "buy-3", [_order("o3", "coffee", 90000)])]
    store = FootprintStore.create(run_dir, manifest or {"run_id": "synthetic", "agent_ids": [1]},
                                  {"version": 0, "current_time": 0, "timezone": "Asia/Shanghai"})
    store.commit({"version": 1, "current_time": 90000, "timezone": "Asia/Shanghai"}, drafts)
    store.close()
    return run_dir


def test_errors_are_classified_with_400_separate_and_partial_tokens_fall_back_to_chars(tmp_path) -> None:
    """协议错误、传输失败与 400 分列；一次决策中有响应缺 reasoning_tokens 时整次报字符数；前缀不符的续接记为断链；
    按订单去重后同一商品同日再下单才算重复。"""
    report = _report(_synthetic_run(tmp_path / "run"))
    first, second, third = report["decisions"]
    assert (first["requests"], first["protocol_errors"], first["transport_failures"], first["bad_request_400"]) == (
        2, 1, 0, 0)
    assert (first["reasoning_tokens"], first["reasoning_chars"]) == (None, len("想点咖啡") + len("还是咖啡吧"))
    assert (second["requests"], second["transport_failures"], second["bad_request_400"]) == (2, 1, 1)
    assert (second["chain_position"], second["chain_intact"], second["tools_same_as_head"]) == (None, False, False)
    assert second["same_second_follow_up"] is True and second["cost_usd"] is None
    assert (third["reasoning_tokens"], third["reasoning_chars"]) == (7, None)
    assert [row["repeat_orders"] for row in report["decisions"]] == [0, 0, 1]
    summary = report["summary"]
    assert (summary["protocol_errors"], summary["transport_failures"], summary["bad_request_400"]) == (1, 1, 1)
    assert (summary["broken_chains"], summary["repeat_orders"]) == (1, 1)
    assert summary["topic_hits"] == {"咖啡": 1}
    assert summary["unpriced_responses"] == 3 and summary["known_cost_usd"] is None


def _manifest(switch: bool, **changes) -> dict:
    """@brief 可比运行清单：同一提交、场景与模型身份，按需改动字段。"""
    from worldmock.runtime import stage_model_identity

    manifest = {"run_id": f"arm-{switch}", "source_group_id": f"arm-{switch}", "agent_ids": [1],
                "git_commit": "a" * 40, "git_dirty": False, "code_sha256": "c" * 64, "scenario_sha256": "s" * 64,
                "model_identity": {"requested_model": "deepseek-flash"},
                "stage_model_identity": stage_model_identity(switch)}
    manifest.update(changes)
    return manifest


def test_the_baseline_comparison_refuses_runs_that_are_not_comparable(tmp_path) -> None:
    """--baseline 核对两份清单：只有续接开关不同才比较；开关相同或提交、场景、模型身份任一不同即拒绝。"""
    module = _script()
    on, off = _manifest(True), _manifest(False)
    assert module.comparison_problems(on, off) == []
    assert module.comparison_problems(on, _manifest(True)) == ["same_second_continuation is the same in both runs"]
    assert module.comparison_problems(on, _manifest(False, scenario_sha256="t" * 64)) == [
        "manifest field scenario_sha256 differs"]
    assert module.comparison_problems(on, _manifest(False, git_commit="b" * 40)) == [
        "manifest field git_commit differs"]
    changed = _manifest(False)
    changed["stage_model_identity"]["stages"]["action"]["max_tokens"] = 4000
    assert module.comparison_problems(on, changed) == ["manifest field stage_model_identity differs"]
    arm, baseline = _synthetic_run(tmp_path / "on", on), _synthetic_run(tmp_path / "off", off)
    script = [sys.executable, str(REPO_ROOT / "scripts" / "inspect_run.py"), str(arm), "continuation"]
    compared = subprocess.run([*script, "--baseline", str(baseline)], cwd=REPO_ROOT, capture_output=True,
                              text=True, encoding="utf-8", env=_UTF8)
    assert compared.returncode == 0, compared.stderr
    document = json.loads(compared.stdout)
    assert (document["arm"]["same_second_continuation"], document["baseline"]["same_second_continuation"]) == (
        True, False)
    assert document["arm"]["summary"] == document["baseline"]["summary"]
    other = _synthetic_run(tmp_path / "other", _manifest(False, code_sha256="d" * 64))
    refused = subprocess.run([*script, "--baseline", str(other)], cwd=REPO_ROOT, capture_output=True,
                             text=True, encoding="utf-8", env=_UTF8)
    assert refused.returncode != 0 and refused.stdout == ""
    assert "Runs are not comparable: manifest field code_sha256 differs" in refused.stderr
