"""服务、证据和查询失败保留实际阶段与完整错误合同。

末两例经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent（docs/design/10-runtime.md  与验收台账「故障传播」）：
世界请求的工具服务失败与模型服务失败各跑一次醒来那一轮的人物步骤，核对日志、上抛的异常对象与类型、失败阶段码，
以及库里没有多出模型证据之外的任何事件。其余用例在替身环境上直调私有覆写点，是这两例的补充证据与变异杀手。

其后追加的两例同样经真实引擎（上面所说的「末两例」排在它们之前）：失败阶段码不挑异常类型——不是 OSError 的工具服务
失败照样先留日志再以 TOOL_SERVICE_FAILED 上抛；足迹库以 sqlite3 错误拒写请求证据或响应证据时以 EVIDENCE_WRITE_FAILED 上抛。

开始组（同一次回复里两个开始工具各一次）的第二条世界请求登记失败在替身环境上经真实 ReAct 循环核对：失败阶段码指向
第二条调用的真实位置，第一条已登记，不再请求模型。
"""

import json
import logging
import os
import sqlite3
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactDecision, ReactLoopRequest
from worldmock.agents.protocol.life_feedback import HostFeedback
from tests.integration.runtime.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _MultipleModelResponse,
    _WAIT_CHOICE,
    _WORK_CHOICE,
    _context,
    _observations,
    _prepared,
    _wire_agent,
)
from tests.integration.host_run_fixtures import waking_decision
from tests.engine_fixture import engine_factory  # noqa: F401
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL
from worldmock.agents.prompt import prompt_capacity


def _assert_report(report, code, path):
    """失败不伪造参数闭集和已执行结果。"""
    assert set(report) == {"error_code", "reason", "fix", "items", "unmet"}
    assert report["error_code"] == code and report["reason"] and report["fix"]
    assert report["unmet"] == []
    assert report["items"][0]["path"] == path
    assert report["items"][0]["allowed_values"] == []
    assert report["items"][0]["written"] is None


@pytest.mark.asyncio
async def test_model_service_failure_persists_stage_without_parsing_exception_text(monkeypatch):
    """真实模型传输入口保存明确的服务阶段，原始传输异常仍终止决策。"""
    person, environment = _prepared(), _Environment()

    async def fail(**request):
        raise OSError("NAME_NOT_FOUND: unrelated exception text")

    _wire_agent(person, environment, SimpleNamespace(call=fail), 4)
    monkeypatch.setattr(person, "_model_request", lambda messages, tools: {"model": "test", "messages": messages})
    with pytest.raises(OSError, match="unrelated exception"):
        await person._complete_react_once([], [], 1)
    assert [event.phase for event in environment.evidence] == ["request", "failure"]
    _assert_report(environment.evidence[-1].response["protocol_feedback"], "MODEL_SERVICE_FAILED", ["tool_calls"])


@pytest.mark.asyncio
async def test_evidence_write_failure_stops_with_structured_stage():
    """证据写入失败不继续模型调用或世界操作。"""
    person, environment = _prepared(), _Environment()

    async def fail(exchange):
        raise OSError("evidence unavailable")

    environment.record_model_exchange = fail
    person._env = environment
    with pytest.raises(RuntimeError) as failure:
        await person._record_model_evidence(ModelCallEvidence(_context(), "model-1", "request", 1, {}))
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "EVIDENCE_WRITE_FAILED", ["tool_calls"])
    assert environment.calls == []


@pytest.mark.asyncio
async def test_tool_service_failure_points_to_real_call_without_claiming_execution(monkeypatch, caplog):
    """第二个合法检索在环境 read_query 处服务异常，保留真实下标，不声称已取得数据；先留一条带异常链的英文 error 日志，
    再按失败阶段码上抛（docs/design/10-runtime.md ）。"""
    person, environment = _prepared(), _Environment()
    _wire_agent(person, environment, None, 4)
    arguments = {"category": "全部", "time": "今天", "keyword": None}
    decisions = [ReactDecision("", "search_memory", arguments, "", f"call-{index}") for index in range(2)]
    assert person.validate_react_decisions(decisions, False) == ""
    injected = OSError("query store unavailable")

    async def fail(context, tool, request):
        raise injected

    monkeypatch.setattr(environment, "read_query", fail)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await person._execute_react_tool(decisions[1])
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 1])
    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    assert environment.calls == []
    records = [record for record in caplog.records if record.getMessage().startswith("LifeAgent tool dispatch failed")]
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.ERROR, "LifeAgent tool dispatch failed: search_memory")]
    assert records[0].exc_info[1] is injected


def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _SecondSubmitFails(_Environment):
    """第一条世界请求照常登记并给出回执，第二条登记时环境服务以注入的异常失败。"""

    def __init__(self, injected: Exception) -> None:
        super().__init__()
        self.injected = injected

    async def submit_action(self, call):
        """已经登记过一条之后的登记一律失败。"""
        if self.calls:
            raise self.injected
        return await super().submit_action(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)])
async def test_second_start_call_service_failure_points_to_its_own_call(monkeypatch, caplog, order):
    """开始组的两条世界请求按回复中的先后依次登记；第二条登记时环境抛出异常是运行故障：先留一条带异常链的英文 error
    日志，再以 TOOL_SERVICE_FAILED 上抛，明细指向第二条调用的真实位置，原因是注入的异常对象。第一条已经登记（它不结算
    由决策轮中止保证），宿主不声称第二条已登记，也不再请求模型。"""
    _fixed_context_window(monkeypatch)
    arguments = {MAIN_TOOL: _WAIT_CHOICE, SIDE_TOOL: _WORK_CHOICE}
    injected = OSError("environment actor unavailable")
    person, environment = _prepared(), _SecondSubmitFails(injected)
    dispatcher = _GroundedCorrectionDispatcher(responses=[_MultipleModelResponse([
        (f"call-{tool}", tool, json.dumps(arguments[tool], ensure_ascii=False)) for tool in order])])
    _wire_agent(person, environment, dispatcher, 4, memory=False)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await person.run_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))

    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 1])
    assert [(call.action_id, call.tool_name) for call in environment.calls] == [("decision:action:0001", order[0])]
    assert len(dispatcher.requests) == 1
    records = [record for record in caplog.records if record.getMessage().startswith("LifeAgent tool dispatch failed")]
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.ERROR, f"LifeAgent tool dispatch failed: {order[1]}")]
    assert records[0].exc_info[1] is injected


def _agent_logs(caplog) -> list[tuple]:
    """人物适配器写下的日志：级别、英文原文与所带异常对象。"""
    return [(record.levelno, record.getMessage(), record.exc_info[1] if record.exc_info else None)
            for record in caplog.records if record.getMessage().startswith("LifeAgent ")]


def _events_after(run, sequence: int) -> list:
    """库里序号晚于给定序号的全部事件。"""
    return [event for event in list(run.engine.context.store.iter_events(run.engine.context.store.last_event_seq))
            if event.event_seq > sequence]


@pytest.mark.asyncio
async def test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage(
        engine_factory, tmp_path, monkeypatch, caplog):
    """真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent：醒来那一轮的世界请求在 LifeEnv.submit_action 处服务失败。
    先写工具分发失败日志、再写决策失败日志，各一条且都带异常；step 上抛 RuntimeError，参数是指向那次工具调用的
    TOOL_SERVICE_FAILED 反馈，原因是注入的异常对象；库里只多出观察、decision_started 与一对模型请求与响应，没有
    action_requested，也没有 memory_updated。"""
    _fixed_context_window(monkeypatch)
    run = await waking_decision(engine_factory, tmp_path, note=None)
    injected = OSError("environment actor unavailable")

    async def fail(call):
        """LifeEnv 提交世界请求的替身：服务不可用。"""
        raise injected

    monkeypatch.setattr(run.environment, "submit_action", fail)
    sequence = run.engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await run.agent.step(0, run.decision.observed_at)

    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 0])
    assert _agent_logs(caplog) == [
        (logging.ERROR, "LifeAgent tool dispatch failed: conclude_activity", injected),
        (logging.ERROR, "LifeAgent decision failed for agent 1", failure.value)]
    added = _events_after(run, sequence)
    assert [event.event_type for event in added] == [
        "observation", "decision_started", "model_request", "model_response"]
    assert {event.decision_id for event in added} == {run.decision.decision_id}
    assert added[-1].data["phase"] == "response" and added[-1].data["error"] is None


@pytest.mark.asyncio
async def test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged(
        engine_factory, tmp_path, monkeypatch, caplog):
    """同一真实路径上醒来那一轮的模型调用失败：决策失败日志恰好一条、带原异常，step 把同一个异常对象原样上抛；
    该次模型证据以 failure 阶段记下 MODEL_SERVICE_FAILED；库里只多出观察、decision_started、模型请求与这条失败证据，
    没有世界请求，也没有 memory_updated。"""
    _fixed_context_window(monkeypatch)
    run = await waking_decision(engine_factory, tmp_path, note=None)
    injected = OSError("model transport unavailable")

    async def fail(**request):
        """远程模型传输的替身：连接失败。"""
        raise injected

    monkeypatch.setattr(run.model, "call", fail)
    sequence = run.engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(OSError) as failure:
        await run.agent.step(0, run.decision.observed_at)

    assert failure.value is injected and type(failure.value) is OSError
    assert _agent_logs(caplog) == [(logging.ERROR, "LifeAgent decision failed for agent 1", injected)]
    added = _events_after(run, sequence)
    assert [event.event_type for event in added] == [
        "observation", "decision_started", "model_request", "model_response"]
    assert {event.decision_id for event in added} == {run.decision.decision_id}
    assert added[-1].data["phase"] == "failure"
    _assert_report(added[-1].data["protocol_feedback"], "MODEL_SERVICE_FAILED", ["tool_calls"])


class _InjectedServiceFailure(Exception):
    """不是 OSError 的服务失败：环境 actor 死亡、协议故障这类异常都不在 OSError 之下。"""


def _service_failures() -> list:
    """真实服务失败会有的两种非 OSError 形态：自定义异常，与足迹库（SQLite）自己的错误。"""
    return [_InjectedServiceFailure("environment actor died"), sqlite3.OperationalError("database is locked")]


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", _service_failures(), ids=["private_exception", "sqlite_error"])
async def test_tool_service_failure_of_any_exception_type_is_logged_and_raised_with_its_stage(
        engine_factory, tmp_path, monkeypatch, caplog, injected):
    """工具分发失败不挑异常类型（docs/design/10-runtime.md  与验收台账「故障传播」）：真实引擎、SQLite、
    LifeEnv 与直接导入的 LifeAgent 上，醒来那一轮的世界请求在 LifeEnv.submit_action 处以不是 OSError 的异常失败。照样
    先写工具分发失败日志、再写决策失败日志，各一条且都带异常；step 上抛的恰是 RuntimeError，参数是指向那次工具调用的
    TOOL_SERVICE_FAILED 反馈，原因是注入的异常对象（没有把原异常原样漏出去）；库里只多出观察、decision_started 与一对
    模型请求与响应，没有 action_requested，也没有 memory_updated。"""
    _fixed_context_window(monkeypatch)
    run = await waking_decision(engine_factory, tmp_path, note=None)
    assert not isinstance(injected, (OSError, RuntimeError))

    async def fail(call):
        """LifeEnv 提交世界请求的替身：服务以注入的异常失败。"""
        raise injected

    monkeypatch.setattr(run.environment, "submit_action", fail)
    sequence = run.engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await run.agent.step(0, run.decision.observed_at)

    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 0])
    assert _agent_logs(caplog) == [
        (logging.ERROR, "LifeAgent tool dispatch failed: conclude_activity", injected),
        (logging.ERROR, "LifeAgent decision failed for agent 1", failure.value)]
    added = _events_after(run, sequence)
    assert [event.event_type for event in added] == [
        "observation", "decision_started", "model_request", "model_response"]
    assert {event.decision_id for event in added} == {run.decision.decision_id}
    assert added[-1].data["phase"] == "response" and added[-1].data["error"] is None


#: 证据写入失败的阶段 →（失败之前模型被调用的次数, 这次决策在库里已写入的事件）。
_EVIDENCE_FAILURES = {
    "request": (0, ["observation"]),
    "response": (1, ["observation", "decision_started", "model_request"]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", sorted(_EVIDENCE_FAILURES))
async def test_evidence_write_failure_of_the_footprint_store_is_raised_with_its_stage(
        engine_factory, tmp_path, monkeypatch, caplog, failure_phase):
    """证据写入失败以运行故障上抛，带失败阶段码 EVIDENCE_WRITE_FAILED、定位在 tool_calls（docs/design/10-runtime.md  第二段与接口合同宿主错误码表），同样不挑异常类型：真实写入方是环境后面的 SQLite 足迹库，它的失败是 sqlite3 的
    错误而不是 OSError。真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent 上，醒来那一轮行动请求的请求证据或响应证据写入
    以 sqlite3.OperationalError 失败：step 上抛的恰是 RuntimeError，参数是该阶段码的反馈，原因是注入的异常对象；决策失败
    日志恰好一条；不重试（请求证据失败时模型一次都没被调用，响应证据失败时恰好一次）；世界请求没有登记，库里只留下失败
    之前已写入的事件。"""
    _fixed_context_window(monkeypatch)
    model_calls, kept = _EVIDENCE_FAILURES[failure_phase]
    run = await waking_decision(engine_factory, tmp_path, note=None)
    injected = sqlite3.OperationalError("database is locked")
    record, call, requests = run.environment.record_model_exchange, run.model.call, []

    async def failing_record(exchange):
        """LifeEnv 保存模型证据的替身：指定阶段的写入以足迹库的错误失败，其余照常落库。"""
        if exchange.phase == failure_phase:
            raise injected
        await record(exchange)

    async def counted_call(**request):
        """远程模型传输照常应答，只多记下被调用的次数。"""
        requests.append(request)
        return await call(**request)

    monkeypatch.setattr(run.environment, "record_model_exchange", failing_record)
    monkeypatch.setattr(run.model, "call", counted_call)
    sequence = run.engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await run.agent.step(0, run.decision.observed_at)

    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "EVIDENCE_WRITE_FAILED", ["tool_calls"])
    assert _agent_logs(caplog) == [(logging.ERROR, "LifeAgent decision failed for agent 1", failure.value)]
    assert len(requests) == model_calls
    added = _events_after(run, sequence)
    assert [event.event_type for event in added] == kept
    assert {event.decision_id for event in added} == {run.decision.decision_id}
