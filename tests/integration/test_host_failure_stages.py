"""服务、证据和查询失败保留实际阶段与完整错误合同。"""

import logging
from types import SimpleNamespace

import pytest

from agentsociety2.agent.base import ModelCallEvidence, ReactDecision
from custom.agents.life_feedback import HostFeedback
from tests.integration.test_life_agent_protocol import _Environment, _context, _prepared, _wire_agent


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
