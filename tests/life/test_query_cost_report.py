"""查询成本子命令（scripts/inspect_run.py queries，docs/dev/SPEC-errands-workday.md 6.3，只报告）。

按运行打印规格 6.3 的八个键：查询调用数、失败数、返回字符数、带筛选的调用数、筛选后为空的次数、从首次查询到购买结算的
模型调用数与反思校验重试次数；口径只取已落库的模型工具交换证据（model_request、model_response），购买结算经
worldmock.footprints.purchases 的同一投影。真实链路用例在真实 LifeEngine 与 SQLite 上留证一次带两个查询（其一带筛选）
的决策，结果页由直接导入的 LifeAgent 在同一冻结观察上派发，购买经真实结算；手工用例用存储写入事件，覆盖真实链路走不到
的取值（整批被拒、从未回答、筛选后为空、旧四键调用、他人购买、就寝反思的校验重试）；子进程用例守打印的键序与只读。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ModelCallEvidence, ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402
from custom.agents.life_agent import ACTION_THINKING, LifeAgent  # noqa: E402
from custom.agents.life_feedback import argument_feedback, feedback_content  # noqa: E402
from custom.agents.life_reflection import (REFLECTION_PROMPT, ReflectionOutputError,  # noqa: E402
                                           reflection_correction, reflection_insights, reflection_tool)
from tests.integration.test_life_agent_protocol import _ModelResponse, _TextResponse  # noqa: E402
from tests.life.conftest import context, stage_day_plan  # noqa: E402
from tests.life.purchase_worlds import at_clock  # noqa: E402
from worldmock.footprints.models import EventDraft  # noqa: E402
from worldmock.footprints.store import FootprintStore  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, DeliveryState  # noqa: E402
from worldmock.life.world_query import QueryWorldRequest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "docs" / "dev" / "SPEC-errands-workday.md"
#: 子进程以 UTF-8 输出。
_UTF8 = {**os.environ, "PYTHONIOENCODING": "utf-8"}
#: 手工运行里决策循环请求的头两条消息与开放的工具（只看工具名）。
_HEAD = [{"role": "system", "content": "系统层"}, {"role": "user", "content": "六层"}]
_DECISION_TOOLS = [{"type": "function", "function": {"name": name}} for name in ("start_activity", "query_world")]
#: 两种取得方式；真实用例从返回项抄一个可用的取得方式。
_ACQUIRE = frozenset({"到店", "远程下单"})
_MILK = "纯牛奶"
_EMPTY_PAGE = {"matches": [], "total_count": 0, "next_offset": None}


def _script():
    """按文件加载只读统计脚本。"""
    spec = importlib.util.spec_from_file_location("inspect_run", REPO_ROOT / "scripts" / "inspect_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(run_dir: Path) -> dict:
    """以只读连接计算查询成本；运行身份与子命令一样取运行清单。"""
    module = _script()
    conn = module._connect(run_dir)
    try:
        return module.query_cost_report(conn, module._manifest(run_dir).get("run_id"))
    finally:
        conn.close()


def _query(**filters) -> dict:
    """六键查询参数：未写的筛选为 null，available_only 缺省 false，offset 缺省 0；键 from 用 source= 写。"""
    source = filters.pop("source", None)
    return {"name": None, "way": None, "category": None, "from": source, "available_only": False, "offset": 0,
            **filters}


def _call(call_id: str, name: str, arguments: dict) -> dict:
    """一个原生工具调用（参数是 JSON 文本）。"""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _reply(calls: list[dict]) -> dict:
    """提供商响应：一条 assistant 消息带这些原生工具调用（带思考、正文为空）。"""
    return {"choices": [{"message": {"role": "assistant", "content": None, "reasoning_content": "想一想。",
                                     "tool_calls": calls}}]}


def _assistant(calls: list[dict]) -> dict:
    """下一请求里回放的 assistant 消息（同一组工具调用）。"""
    return {"role": "assistant", "content": None, "reasoning_content": "想一想。", "tool_calls": calls}


def _tool(call_id: str, content: str) -> dict:
    """回答一个工具调用的 tool 消息。"""
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _compact(page: dict) -> str:
    """宿主写进 tool 消息的结果页正文（紧凑 JSON，与 LifeAgent 追加查询结果同一序列化）。"""
    return json.dumps(page, ensure_ascii=False, separators=(",", ":"))


def test_query_cost_on_a_real_engine_exchange(engine_factory) -> None:
    """真实引擎与 SQLite：10:00 在家的一次决策，第一次请求（开放 start_activity 与 query_world，思考开启）的响应带两个
    查询——纯牛奶不带筛选、纯牛奶「购买」只看此刻能做的；直接导入的 LifeAgent 在同一冻结观察上整批校验并派发，两页以
    紧凑 JSON 作为 tool 消息进入第二次请求；第二次响应照抄一条结果的完整名称与可用方式下单，经宿主校验与真实结算受理。
    子命令报告两个调用、无失败、返回字符数等于两页正文之和、一个带筛选、筛选后为空按真实页（此处非空，为 0）、从首次
    查询到购买结算两次模型调用、没有反思重试，运行身份取运行清单。"""
    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    decision = context(engine, "query-cost")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    host = LifeAgent()
    host.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                                       "data": observed}]))
    tools = host.build_react_tool_schemas(readonly=False)
    assert {"start_activity", "query_world"} <= {tool["function"]["name"] for tool in tools}
    queries = [_call("call-milk", "query_world", _query(name=_MILK)),
               _call("call-milk-now", "query_world", _query(name=_MILK, way="购买", available_only=True))]
    asked = [ReactDecision("", "query_world", json.loads(call["function"]["arguments"]), "", call["id"])
             for call in queries]
    assert host.validate_react_decisions(asked, False) == ""
    pages = [asyncio.run(host.dispatch_react_tool("query_world", item.args)).data for item in asked]
    contents = [_compact(page) for page in pages]
    match = pages[1]["matches"][0]
    way = next(action["way"] for action in match["actions"] if action["available"] and action["way"] in _ACQUIRE)
    purchase = {"items": [{"target": match["name"], "way": way, "from": None, "to": None}], "answering": None,
                "prefer": "最快拿到"}
    first = {"model": "deepseek-flash", "messages": _HEAD, "tools": tools, "extra_body": ACTION_THINKING}
    second = {**first, "messages": [*_HEAD, _assistant(queries),
                                    *(_tool(call["id"], text) for call, text in zip(queries, contents))]}
    for call_id, request, calls in (("turn-1", first, queries),
                                    ("turn-2", second, [_call("call-buy", "start_activity", purchase)])):
        engine.record_model_exchange(ModelCallEvidence(decision, call_id, "request", 1, request))
        engine.record_model_exchange(ModelCallEvidence(decision, call_id, "response", 1, request,
                                                       response=_reply(calls)))
    assert host.validate_react_decisions([ReactDecision("", "start_activity", purchase, "", "call-buy")], False) == ""
    engine.submit_action(DirectToolCall(decision, f"{decision.decision_id}:action:0001", "start_activity",
                                        REQUEST_MODELS["start_activity"].model_validate(purchase)))
    [result] = engine.commit_decision_round("query-cost")
    assert (result.status, result.error_code) == ("completed", None) and len(result.data["orders"]) == 1
    assert pages[0]["total_count"] > 0 and pages[1]["total_count"] > 0
    assert _report(engine.store.run_dir) == {
        "run_id": engine.store.manifest["run_id"], "calls": 2, "failures": 0,
        "returned_chars": sum(len(text) for text in contents), "filtered_calls": 1,
        "empty_after_filter": int(pages[1]["total_count"] == 0), "model_calls_to_purchase": 2,
        "reflection_retries": 0}


def _event(event_type: str, at: int, agent: int, decision: str, **fields) -> EventDraft:
    """一条手工事件（模型证据由系统可见、来源为模型；动作事件保留缺省）。"""
    return EventDraft(event_type=event_type, occurred_at=at, agent_id=agent, decision_id=decision, **fields)


def _request(at: int, agent: int, decision: str, messages: list[dict], body: dict | None = None) -> EventDraft:
    """一次模型请求证据；缺省是决策循环的请求（开放 start_activity 与 query_world，思考开启）。"""
    request = {"model": "deepseek-flash", "messages": messages,
               **(body or {"tools": _DECISION_TOOLS, "extra_body": ACTION_THINKING})}
    return _event("model_request", at, agent, decision, visibility="system", source_kind="model",
                  data={"phase": "request", "call_id": f"{decision}:{len(messages)}", "attempt": 1,
                        "request": request, "response": None, "error": None})


def _response(request: EventDraft, response: dict, error: str | None = None) -> EventDraft:
    """请求的响应证据（phase=response）；error 是宿主协议错误（整批被拒）。"""
    return _event("model_response", request.occurred_at, request.agent_id, request.decision_id,
                  caused_by=[request.event_id], visibility="system", source_kind="model",
                  data={"phase": "response", "call_id": request.data["call_id"], "attempt": 1,
                        "request": request.data["request"], "response": response, "error": error})


def _turn(request: EventDraft, calls: list[dict], error: str | None = None) -> list[EventDraft]:
    """一次决策循环模型调用：请求与带这些工具调用的响应。"""
    return [request, _response(request, _reply(calls), error)]


def _purchase(at: int, agent: int, decision: str, order_id: str) -> list[EventDraft]:
    """一次远程下单请求与结果；结果顶层带一笔该人物的订单。"""
    order = DeliveryState(
        order_id=order_id, agent_id=agent, product_id="milk", supply_id="supply-milk", location_id="shop",
        seller_account_id="seller:shop", price_version="v1", route_id=None, source={"kind": "fixture"}, quantity=1,
        destination_id="home", fulfillment="delivery", created_at=at, expected_at=at + 900, status="pending",
        total_price_fen=1200, recipient_id=f"agent:{agent}").model_dump(mode="json")
    action = f"{decision}:action:0001"
    requested = _event("action_requested", at, agent, decision, action_id=action, source_kind="model", data={
        "tool_name": "start_activity", "request_sha256": "0" * 64, "reused": False,
        "request": {"items": [{"target": "店·纯牛奶", "way": "远程下单", "from": None, "to": None}],
                    "answering": None, "prefer": "最快拿到"}})
    result = _event("action_result", at, agent, decision, action_id=action, caused_by=[requested.event_id], data={
        "action_id": action, "agent_id": agent, "status": "completed", "occurred_at": at,
        "message": "Purchase accepted", "error_code": None, "data": {"orders": [order]}})
    return [requested, result]


def _reflection(at: int, agent: int) -> list[EventDraft]:
    """就寝反思的三次模型调用：原始请求（system 与 user 两条）答成自由文本，第一次校验重试引用了输入之外的记忆，第二次
    改正。重试消息由阶段 1 的 reflection_correction 照记忆运行时的同一循环生成（总是基于原始消息）。"""
    episodes = [{"id": "m_00000180_000001"}]
    original = [{"role": "system", "content": REFLECTION_PROMPT},
                {"role": "user", "content": json.dumps(episodes, ensure_ascii=False)}]
    attempts = [_TextResponse("今天挺累的。"),
                _ModelResponse("call-r1", "submit_reflection", json.dumps(
                    {"insights": [{"insight": "我想早点睡。", "refs": ["m_unknown"]}]}, ensure_ascii=False)),
                _ModelResponse("call-r2", "submit_reflection", json.dumps(
                    {"insights": [{"insight": "我想早点睡。", "refs": [episodes[0]["id"]]}]}, ensure_ascii=False))]
    body = {"stream": False, "tools": [reflection_tool()], "tool_choice": "required", "max_retries": 0,
            "extra_body": {"thinking": {"type": "disabled"}}}
    drafts, messages = [], original
    for response in attempts:
        request = _request(at, agent, f"sleep-{agent}", messages, body)
        drafts += [request, _response(request, response.model_dump(mode="json"))]
        try:
            reflection_insights(response, episodes)
        except ReflectionOutputError as error:
            messages = reflection_correction(original, response, error)
    assert len(reflection_insights(attempts[-1], episodes)) == 1
    assert [len(draft.data["request"]["messages"]) for draft in drafts[::2]] == [2, 3, 3]
    assert [draft.data["request"]["messages"][:2] for draft in drafts[::2]] == [original] * 3
    return drafts


def _store(run_dir: Path, run_id: str, drafts: list[EventDraft]) -> Path:
    """用存储写入一次手工运行（事件按给出的顺序，发生时刻不减）。"""
    store = FootprintStore.create(run_dir, {"run_id": run_id, "agent_ids": [1, 2]},
                                  {"version": 0, "current_time": 0, "timezone": "Asia/Shanghai"})
    store.commit({"version": 1, "current_time": max(draft.occurred_at for draft in drafts),
                  "timezone": "Asia/Shanghai"}, drafts)
    store.close()
    return run_dir


def _rejected_batch_correction(arguments: dict) -> str:
    """旧四键查询所在整批被拒时宿主写进每个 tool 消息的协议纠错（真实的参数错误报告加固定指示，没有 matches）。"""
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(arguments)
    content = feedback_content(argument_feedback("query_world", caught.value, QueryWorldRequest, arguments))
    assert "matches" not in json.loads(content)
    return content


def test_query_cost_counts_failures_empty_filtered_pages_and_reflection_retries(tmp_path) -> None:
    """手工运行：人物 1 先买过一次（在首次查询之前，不计）；首次查询所在的响应带旧四键调用与只写 from 的调用，整批被拒，
    下一请求里两条都是协议纠错（两次失败，后者算带筛选）；再一次响应带 way=到店加 available_only 的调用（筛选后为空页）
    与只写 name 的调用（有一条结果），都被回答；人物 2 的购买与请求不计；就寝反思三次调用中两次是带附回消息的校验重试；
    人物 1 下一次决策下单结算之后的请求不计；人物 2 最后一次响应里的查询从未被回答（运行停在这里）。"""
    old = {"name": "牛奶", "way": None, "category": None, "offset": 0}
    correction = _rejected_batch_correction(old)
    milk = _compact({"matches": [{"name": "好德·纯牛奶", "kind": "product", "actions": [
        {"way": "远程下单", "available": True}]}], "total_count": 1, "next_offset": None})
    empty = _compact(_EMPTY_PAGE)
    rejected = [_call("call-old", "query_world", old), _call("call-haode", "query_world", _query(source="好德"))]
    answered = [_call("call-shop-now", "query_world", _query(way="到店", available_only=True)),
                _call("call-milk", "query_world", _query(name=_MILK))]
    second = [*_HEAD, _assistant(rejected), *(_tool(call["id"], correction) for call in rejected)]
    third = [*second, _assistant(answered), _tool("call-shop-now", empty), _tool("call-milk", milk)]
    buy = _call("call-buy", "start_activity", {})
    drafts = [*_turn(_request(50, 1, "d0", _HEAD), [buy]), *_purchase(50, 1, "d0", "o-early"),
              *_turn(_request(100, 1, "d1", _HEAD), rejected, "ARGUMENTS_INVALID: Invalid query_world arguments."),
              *_turn(_request(100, 1, "d1", second), answered),
              *_turn(_request(100, 1, "d1", third), [_call("call-wait", "start_activity", {})]),
              *_turn(_request(150, 2, "d-other", _HEAD), [buy]), *_purchase(150, 2, "d-other", "o-other"),
              *_reflection(180, 1),
              *_turn(_request(200, 1, "d2", _HEAD), [buy]), *_purchase(200, 1, "d2", "o-milk"),
              *_turn(_request(200, 1, "d3", _HEAD), [_call("call-rest", "start_activity", {})]),
              *_turn(_request(300, 2, "d9", _HEAD), [_call("call-eggs", "query_world", _query(name="鸡蛋"))])]
    report = _report(_store(tmp_path / "run", "synthetic", drafts))
    assert report == {"run_id": "synthetic", "calls": 5, "failures": 3, "returned_chars": len(empty) + len(milk),
                      "filtered_calls": 2, "empty_after_filter": 1, "model_calls_to_purchase": 4,
                      "reflection_retries": 2}


def _files(run_dir: Path) -> dict[str, str]:
    """运行目录下每个文件的相对路径与内容摘要。"""
    return {path.relative_to(run_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(run_dir.rglob("*")) if path.is_file()}


def test_query_cost_of_a_run_without_queries_and_the_printed_keys(tmp_path) -> None:
    """没有查询的运行各计数为 0、从首次查询到购买结算为 null；子进程运行 `queries` 打印一个 JSON 对象，键序恰是规格
    6.3 列出的八个键（QUERY_REPORT_KEYS 与规格原文逐一相同），运行目录的文件逐字节不变。有查询而其后没有本人购买
    结算的运行同样为 null。"""
    keys = re.search(r"子命令 `queries` 按运行打印 `\{([^}`]*)\}`", SPEC.read_text(encoding="utf-8")).group(1)
    assert list(_script().QUERY_REPORT_KEYS) == keys.split(", ")
    quiet = _store(tmp_path / "quiet", "quiet", [*_turn(_request(100, 1, "d1", _HEAD),
                                                        [_call("call-buy", "start_activity", {})]),
                                                 *_purchase(100, 1, "d1", "o-1")])
    before = _files(quiet)
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "inspect_run.py"), str(quiet), "queries"],
                            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", env=_UTF8)
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert list(printed) == keys.split(", ")
    assert printed == {"run_id": "quiet", "calls": 0, "failures": 0, "returned_chars": 0, "filtered_calls": 0,
                       "empty_after_filter": 0, "model_calls_to_purchase": None, "reflection_retries": 0}
    assert _files(quiet) == before
    asked = [_call("call-milk", "query_world", _query(name=_MILK))]
    late = _store(tmp_path / "late", "late", [
        *_purchase(100, 1, "d1", "o-1"), *_turn(_request(200, 1, "d2", _HEAD), asked),
        *_turn(_request(200, 1, "d2", [*_HEAD, _assistant(asked), _tool("call-milk", _compact(_EMPTY_PAGE))]),
               [_call("call-wait", "start_activity", {})])])
    assert _report(late) == {"run_id": "late", "calls": 1, "failures": 0, "returned_chars": len(_compact(_EMPTY_PAGE)),
                             "filtered_calls": 0, "empty_after_filter": 0, "model_calls_to_purchase": None,
                             "reflection_retries": 0}


#: 手工运行里一条有结果的查询页正文，与一次不带查询的等待调用。
_ONE_MATCH = _compact({"matches": [{"name": "好德出售的纯牛奶", "kind": "product"}], "total_count": 1,
                       "next_offset": None})
_WAIT = _call("call-wait", "start_activity", {})


def test_a_tail_page_of_a_filtered_query_is_not_empty_after_filter(tmp_path) -> None:
    """@brief 筛选后为空只计成功且 total_count 为 0 的带筛选调用（规格 6.3，口径见记录引擎设计「调试工具」）：offset 等于
    筛选后总数的合法空末页——matches 为空而 total_count 为 5——是成功的带筛选调用，不算筛选后为空；同一响应里另一个
    total_count 为 0 的带筛选调用才计一次。两页正文都计入返回字符数，没有失败。"""
    tail = _compact({"matches": [], "total_count": 5, "next_offset": None})
    empty = _compact(_EMPTY_PAGE)
    asked = [_call("call-tail", "query_world", _query(source="好德", offset=5)),
             _call("call-none", "query_world", _query(source="好德", name="并不存在的东西"))]
    answered = [*_HEAD, _assistant(asked), _tool("call-tail", tail), _tool("call-none", empty)]
    run = _store(tmp_path / "tail", "tail", [*_turn(_request(100, 1, "d1", _HEAD), asked),
                                             *_turn(_request(100, 1, "d1", answered), [_WAIT])])
    assert _report(run) == {"run_id": "tail", "calls": 2, "failures": 0, "returned_chars": len(tail) + len(empty),
                            "filtered_calls": 2, "empty_after_filter": 1, "model_calls_to_purchase": None,
                            "reflection_retries": 0}


def test_model_calls_to_purchase_stop_at_the_first_settlement_after_the_first_query(tmp_path) -> None:
    """@brief 从首次查询到购买结算的模型调用数只数到其后的第一笔购买结算（规格 6.3；记录引擎设计：计该人物从产生首次
    查询的请求（含）到其后第一笔购买结算（不含）之间的决策请求）：人物 1 查询后在同一决策下单结算，之后两次决策又各买
    一次——结果是首笔之前的两次请求，后面两笔购买及其请求都不计。"""
    asked = [_call("call-milk", "query_world", _query(name=_MILK))]
    buy = _call("call-buy", "start_activity", {})
    answered = [*_HEAD, _assistant(asked), _tool("call-milk", _ONE_MATCH)]
    run = _store(tmp_path / "thrice", "thrice", [
        *_turn(_request(100, 1, "d1", _HEAD), asked), *_turn(_request(100, 1, "d1", answered), [buy]),
        *_purchase(100, 1, "d1", "o-first"),
        *_turn(_request(200, 1, "d2", _HEAD), [buy]), *_purchase(200, 1, "d2", "o-second"),
        *_turn(_request(300, 1, "d3", _HEAD), [buy]), *_purchase(300, 1, "d3", "o-third")])
    assert _report(run) == {"run_id": "thrice", "calls": 1, "failures": 0, "returned_chars": len(_ONE_MATCH),
                            "filtered_calls": 0, "empty_after_filter": 0, "model_calls_to_purchase": 2,
                            "reflection_retries": 0}


def test_a_call_id_reused_by_a_later_decision_does_not_answer_an_earlier_query(tmp_path) -> None:
    """@brief 查询是否得到回答只看同一决策里的 tool 消息（规格 6.3；记录引擎设计：同一决策其后最先带同一调用身份的 tool
    消息）：调用身份只在一次响应内唯一，后一次决策重用了 call_0——前一次决策里从未得到回答的查询仍算失败，后一次决策的
    结果页只算给它自己那次调用，返回字符数只计一次。"""
    first = [_call("call_0", "query_world", _query(name="鸡蛋"))]
    second = [_call("call_0", "query_world", _query(name=_MILK))]
    answered = [*_HEAD, _assistant(second), _tool("call_0", _ONE_MATCH)]
    run = _store(tmp_path / "reused", "reused", [
        *_turn(_request(100, 1, "d1", _HEAD), first), *_turn(_request(200, 1, "d2", _HEAD), second),
        *_turn(_request(200, 1, "d2", answered), [_WAIT])])
    assert _report(run) == {"run_id": "reused", "calls": 2, "failures": 1, "returned_chars": len(_ONE_MATCH),
                            "filtered_calls": 0, "empty_after_filter": 0, "model_calls_to_purchase": None,
                            "reflection_retries": 0}
