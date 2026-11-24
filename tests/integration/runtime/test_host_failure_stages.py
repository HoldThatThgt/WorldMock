"""服务、证据和查询失败保留实际阶段与完整错误合同（真实 RunHost、SQLite、AgentSandbox 与 LifeAgent）。"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from types import SimpleNamespace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.host_run_fixtures import _evening
from tests.integration.runtime.current_contract_fixtures import resolved_binding
from tests.integration.runtime.test_life_agent_protocol import (
    _GroundedCorrectionDispatcher,
    _MultipleModelResponse,
    _SandboxStub,
    _WAIT_CHOICE,
    _WORK_CHOICE,
    _observations,
    _person_7,
    _wakeup,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, stage_day_plan
from worldmock.agents.life_agent import LifeAgent
from worldmock.agents.prompt import prompt_capacity
from worldmock.agents.protocol.agent_loop import AgentLoop, LoopCallbacks, LoopConfig, TURNS_EXHAUSTED
from worldmock.agents.protocol.life_feedback import ProtocolFeedback


def _assert_report(report, code, path):
    """失败不伪造参数闭集和已执行结果。"""
    assert set(report) == {"error_code", "reason", "fix", "items", "unmet"}
    assert report["error_code"] == code and report["reason"] and report["fix"]
    assert report["unmet"] == []
    assert report["items"][0]["path"] == path
    assert report["items"][0]["allowed_values"] == []
    assert report["items"][0]["written"] is None


@pytest.mark.asyncio
async def test_model_service_failure_persists_stage_without_parsing_exception_text():
    """真实模型传输入口保存明确的服务阶段 MODEL_SERVICE_FAILED，原始传输异常仍终止唤醒。"""
    sandbox = _SandboxStub()

    async def fail(request):
        raise OSError("NAME_NOT_FOUND: unrelated exception text")

    binding = resolved_binding(SimpleNamespace(call=fail), max_turns=4)
    with pytest.raises(OSError, match="unrelated exception"):
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, binding)
    assert [event.phase for event in sandbox.evidence] == ["request", "failure"]
    _assert_report(sandbox.evidence[-1].response["protocol_feedback"], "MODEL_SERVICE_FAILED", ["tool_calls"])


@pytest.mark.asyncio
async def test_evidence_write_failure_stops_with_structured_stage():
    """证据写入失败时以 EVIDENCE_WRITE_FAILED 立即停止，不调用模型也不调度世界动作。"""
    sandbox = _SandboxStub()
    called = []

    def fail(exchange):
        raise OSError("evidence unavailable")

    async def model_call(_req):
        called.append(True)
        raise AssertionError("Should not call model when request evidence fails")

    sandbox.record_model_exchange = fail
    binding = resolved_binding(SimpleNamespace(call=model_call), max_turns=4)
    with pytest.raises(RuntimeError) as failure:
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, binding)
    feedback = failure.value.feedback
    assert isinstance(feedback, ProtocolFeedback)
    _assert_report(feedback.report.payload(), "EVIDENCE_WRITE_FAILED", ["tool_calls"])
    assert sandbox.calls == [] and called == []


@pytest.mark.asyncio
async def test_tool_service_failure_points_to_real_call_without_claiming_execution(caplog):
    """同一批两个 search_memory 调用中第二个抛出异常：保留真实下标 1，先留带异常链的 error 日志再抛 TOOL_SERVICE_FAILED。"""
    sandbox = _SandboxStub()
    args = {"category": "全部", "time": "今天", "keyword": None}
    model = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([
            ("call-q0", "search_memory", json.dumps(args, ensure_ascii=False)),
            ("call-q1", "search_memory", json.dumps(args, ensure_ascii=False)),
        ]),
    ])
    injected = OSError("query store unavailable")
    real_search = sandbox.search_memory
    count = []

    def search_memory(req):
        count.append(req)
        if len(count) == 1:
            return real_search(req)
        raise injected

    sandbox.search_memory = search_memory
    binding = resolved_binding(model, max_turns=4)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, binding)
    feedback = failure.value.feedback
    assert isinstance(feedback, ProtocolFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 1])
    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    records = [r for r in caplog.records if r.getMessage().startswith("Tool service failed during")]
    assert [(r.levelno, r.getMessage()) for r in records] == [(logging.ERROR, "Tool service failed during search_memory")]
    assert records[0].exc_info[1] is injected


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)])
async def test_second_start_call_service_failure_points_to_its_own_call(caplog, order):
    """开始组两条世界工具第二条失败：先写日志再以 TOOL_SERVICE_FAILED(index=1) 上抛，原因保留注入异常，不再请求模型。"""
    arguments = {MAIN_TOOL: _WAIT_CHOICE, SIDE_TOOL: _WORK_CHOICE}
    injected = OSError("environment actor unavailable")
    sandbox = _SandboxStub()
    real_dispatch = sandbox.dispatch_world_tool

    def dispatch(tool_name, args, call_id, note=None):
        if sandbox.calls:
            raise injected
        return real_dispatch(tool_name, args, call_id, note)

    sandbox.dispatch_world_tool = dispatch
    dispatcher = _GroundedCorrectionDispatcher(responses=[_MultipleModelResponse([
        (f"call-{tool}", tool, json.dumps(arguments[tool], ensure_ascii=False)) for tool in order])])
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, resolved_binding(dispatcher, max_turns=4))

    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    _assert_report(failure.value.feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 1])
    assert [(call.tool_name,) for call in sandbox.calls] == [(order[0],)]
    assert len(dispatcher.requests) == 1
    records = [r for r in caplog.records if r.getMessage().startswith("Tool service failed during")]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.ERROR, f"Tool service failed during {order[1]}")]
    assert records[0].exc_info[1] is injected


def _agent_logs(caplog) -> list[tuple]:
    return [(r.levelno, r.getMessage(), r.exc_info[1] if r.exc_info else None)
            for r in caplog.records if r.getMessage().startswith(("Tool service failed", "LifeAgent "))]


def _events_after(engine, sequence: int) -> list:
    return [event for event in list(engine.context.store.iter_events(engine.context.store.last_event_seq))
            if event.event_seq > sequence]


class _InjectedServiceFailure(Exception):
    """非 OSError 的服务失败。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [_InjectedServiceFailure("actor died"), sqlite3.OperationalError("locked")],
                         ids=["private_exception", "sqlite_error"])
async def test_tool_service_failure_on_real_engine_is_flushed_to_sqlite(
    engine_factory, host_factory, monkeypatch, caplog, injected,
):
    """真实引擎、SQLite、RunHost 与 LifeAgent：dispatch_world_tool 抛出非 OSError 异常，RunHost 补写纯模型与唤醒证据。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    call_resp = _MultipleModelResponse([
        ("call-rest", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    host = host_factory(engine, _GroundedCorrectionDispatcher(responses=[call_resp]))
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sb = real_open(wakeup)
        sb.dispatch_world_tool = lambda *a, **kw: (_ for _ in ()).throw(injected)
        return sb

    monkeypatch.setattr(engine, "open_sandbox", wrapped_open)
    seq = engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await host.run()
    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    _assert_report(failure.value.feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 0])
    added = _events_after(engine, seq)
    assert [e.event_type for e in added] == [
        "decision_started", "observation", "model_request", "model_response", "run_failed"]
    assert added[-2].data["phase"] == "response" and added[-2].data["error"] is None
    assert ("locked" in str(injected) or "actor" in str(injected)) and caplog.records


@pytest.mark.asyncio
async def test_model_service_failure_on_real_engine_flushes_failure_evidence(
    engine_factory, host_factory, caplog,
):
    """真实引擎上模型调用失败：failure 阶段记入 model_response，run_failed 记录原始异常 repr。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    injected = OSError("model transport unavailable")

    async def fail(_req):
        raise injected

    host = host_factory(engine, SimpleNamespace(call=fail))
    seq = engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(OSError) as failure:
        await host.run()
    assert failure.value is injected
    added = _events_after(engine, seq)
    assert [e.event_type for e in added] == [
        "decision_started", "observation", "model_request", "model_response", "run_failed"]
    assert added[-2].data["phase"] == "failure"
    _assert_report(added[-2].data["protocol_feedback"], "MODEL_SERVICE_FAILED", ["tool_calls"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase,kept", [
    ("request", ["decision_started", "observation", "run_failed"]),
    ("response", ["decision_started", "observation", "model_request", "run_failed"]),
])
async def test_evidence_write_failure_on_real_engine_stops_before_model_or_action(
    engine_factory, host_factory, monkeypatch, failure_phase, kept,
):
    """足迹草稿记录在指定阶段以 sqlite3 错误失败时上抛 EVIDENCE_WRITE_FAILED，库里仅保留已建立的纯唤醒/请求草稿。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    model_resp = _MultipleModelResponse([
        ("call-rest", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    host = host_factory(engine, _GroundedCorrectionDispatcher(responses=[model_resp]))
    injected = sqlite3.OperationalError("database is locked")
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sb = real_open(wakeup)
        real_rec = sb.record_model_exchange

        def rec(exchange):
            if exchange.phase == failure_phase:
                raise injected
            real_rec(exchange)

        sb.record_model_exchange = rec
        return sb

    monkeypatch.setattr(engine, "open_sandbox", wrapped_open)
    seq = engine.context.store.last_event_seq
    with pytest.raises(RuntimeError) as failure:
        await host.run()
    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    _assert_report(failure.value.feedback.report.payload(), "EVIDENCE_WRITE_FAILED", ["tool_calls"])
    added = _events_after(engine, seq)
    assert [e.event_type for e in added] == kept
