"""服务、证据和查询失败保留实际阶段与完整错误合同。

末两例经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent（docs/dev/SPEC-errands-workday.md 1.4 与验收台账「故障传播」）：
世界请求的工具服务失败与模型服务失败各跑一次醒来那一轮的人物步骤，核对日志、上抛的异常对象与类型、失败阶段码，
以及库里没有多出模型证据之外的任何事件。其余用例在替身环境上直调私有覆写点，是这两例的补充证据与变异杀手。
"""

import logging
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactDecision
from custom.agents.life_feedback import HostFeedback
from tests.integration.test_life_agent_protocol import _Environment, _context, _prepared, _wire_agent
from tests.integration.test_strict_reflection import _waking_decision
from tests.life.conftest import engine_factory
from worldmock.life import prompt_capacity


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
    """第二个合法查询服务异常保留真实下标，不声称已取得数据；先留一条带异常链的英文 error 日志，再按失败阶段码上抛
    （docs/dev/SPEC-errands-workday.md 1.4）。"""
    person, environment = _prepared(), _Environment()
    _wire_agent(person, environment, None, 4)
    decisions = [ReactDecision("", "memory_recent", {"limit": 1}, "", f"call-{index}") for index in range(2)]
    assert person.validate_react_decisions(decisions, False) == ""
    injected = OSError("query store unavailable")

    async def fail(action, args, *, readonly=False):
        raise injected

    monkeypatch.setattr(person, "dispatch_react_tool", fail)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as failure:
        await person._execute_react_tool(decisions[1])
    feedback = failure.value.args[0]
    assert isinstance(feedback, HostFeedback)
    _assert_report(feedback.report.payload(), "TOOL_SERVICE_FAILED", ["tool_calls", 1])
    assert type(failure.value) is RuntimeError and failure.value.__cause__ is injected
    assert environment.calls == []
    records = [record for record in caplog.records if record.getMessage().startswith("LifeAgent tool dispatch failed")]
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.ERROR, "LifeAgent tool dispatch failed: memory_recent")]
    assert records[0].exc_info[1] is injected


def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _agent_logs(caplog) -> list[tuple]:
    """人物适配器写下的日志：级别、英文原文与所带异常对象。"""
    return [(record.levelno, record.getMessage(), record.exc_info[1] if record.exc_info else None)
            for record in caplog.records if record.getMessage().startswith("LifeAgent ")]


def _events_after(run, sequence: int) -> list:
    """库里序号晚于给定序号的全部事件。"""
    return [event for event in run.engine.store.read_events(run.engine.store.last_event_seq)
            if event.event_seq > sequence]


@pytest.mark.asyncio
async def test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage(
        engine_factory, tmp_path, monkeypatch, caplog):
    """真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent：醒来那一轮的世界请求在 LifeEnv.submit_action 处服务失败。
    先写工具分发失败日志、再写决策失败日志，各一条且都带异常；step 上抛 RuntimeError，参数是指向那次工具调用的
    TOOL_SERVICE_FAILED 反馈，原因是注入的异常对象；库里只多出观察、decision_started 与一对模型请求与响应，没有
    action_requested，也没有 memory_updated。"""
    _fixed_context_window(monkeypatch)
    run = await _waking_decision(engine_factory, tmp_path)
    injected = OSError("environment actor unavailable")

    async def fail(call):
        """LifeEnv 提交世界请求的替身：服务不可用。"""
        raise injected

    monkeypatch.setattr(run.environment, "submit_action", fail)
    sequence = run.engine.store.last_event_seq
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
    run = await _waking_decision(engine_factory, tmp_path)
    injected = OSError("model transport unavailable")

    async def fail(**request):
        """远程模型传输的替身：连接失败。"""
        raise injected

    monkeypatch.setattr(run.model, "call", fail)
    sequence = run.engine.store.last_event_seq
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
