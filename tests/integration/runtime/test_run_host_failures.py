"""运行宿主的故障路径：真实引擎、真实 SQLite、真实 RunHost 与真实 LifeAgent，只把模型客户端换成按脚本作答的替身。

原异常直接到达宿主；失败前补写未落盘的模型与沙盒证据草稿，运行级登记写原异常的 repr；证据补写或故障登记本身失败时
两项异常都保留在一个异常组里；不是 Exception 的 BaseException 不登记。
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
from litellm import ModelResponse

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.host_run_fixtures import _evening
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments, stage_day_plan
from worldmock.agents.protocol.agent_loop import TURNS_EXHAUSTED
from worldmock.configuration.model.model_client import LLMDispatchError
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle.run_host import RunHost
from worldmock.runtime.monitoring.status import failure_message

#: 人物一侧模型调用最终失败时模型客户端抛出的异常。
MODEL_FAILURE = LLMDispatchError("Failed to get valid response after 3 attempts in 12.0 s. Last error type: "
                                 "InternalServerError. Error chain: InternalServerError <- ConnectError")
#: 两种阶段码的固定文字（人物一侧带调用位置的故障）。
MODEL_SERVICE_FAILED = "MODEL_SERVICE_FAILED: The model service failed before a response was available."
TOOL_SERVICE_FAILED = "TOOL_SERVICE_FAILED: The tool service failed before a result was available."
#: 替身每个回复都带的思考（常规唤醒开启思考）。
THINKING = "先想一想眼下该做什么。"


class _Stop(BaseException):
    """不是 Exception 的 BaseException：宿主不登记，原样传出。"""


def _reply(label: int, calls: list[tuple[str, dict]], content: str) -> ModelResponse:
    """一次决策请求的完整聊天补全：带思考与正文，按给定次序带工具调用（没有调用时是纯文字回复）。"""
    tool_calls = [{"id": f"call-{label}-{index}", "type": "function",
                   "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}
                  for index, (name, arguments) in enumerate(calls, start=1)]
    message = {"role": "assistant", "content": content, "reasoning_content": THINKING}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ModelResponse(id=f"chatcmpl-{label}", object="chat.completion", created=1767571200, model="test-model",
                         choices=[{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop",
                                   "message": message}],
                         usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})


class _Model:
    """模型客户端替身：决策请求（工具里有 start_main_activity）按脚本依次作答或抛出；其余请求是零点回顾或日计划，
    给了 midnight 时抛出它，否则当场失败。"""

    def __init__(self, script: list, midnight: BaseException | None = None) -> None:
        self.script, self.midnight, self.decisions = list(script), midnight, 0

    async def call(self, request: dict) -> ModelResponse:
        """按请求种类作答。"""
        names = [tool["function"]["name"] for tool in request.get("tools") or []]
        if MAIN_TOOL not in names:
            if self.midnight is None:
                raise AssertionError(f"Unexpected non-decision model request: {names}")
            raise self.midnight
        self.decisions += 1
        answer = self.script.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        calls, content = answer
        return _reply(self.decisions, calls, content)


def _attach(host_factory, engine, script: list, midnight: BaseException | None = None) -> tuple[RunHost, _Model]:
    """预置当日计划并把真实运行宿主挂到引擎上。"""
    stage_day_plan(engine, submitted=True)
    model = _Model(script, midnight)
    host = host_factory(engine, model)
    assert isinstance(host, RunHost)
    return host, model


def _two_turns(persona, config) -> None:
    """一次唤醒至多两个模型回合。"""
    config["models"]["reasoner"]["max_react_turns"] = 2


def _ops(engine) -> list[dict]:
    """读取当前运行已落盘的全部运维日志记录。"""
    logs = engine.context.store.run_dir / "logs"
    return [json.loads(line) for path in sorted(logs.glob("ops-*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def _ops_details(engine, code: str) -> list[str]:
    """某个运维日志码的全部说明，按写入先后。"""
    return [record["detail"] for record in _ops(engine) if record["code"] == code]


def _bodies(engine) -> list[str]:
    """库里每条事件正文的 JSON 文本，按事件序号。"""
    rows = engine.context.store.connection.execute("SELECT body FROM events ORDER BY event_seq")
    return [row[0] for row in rows]


def _events(engine, event_type: str) -> list[dict]:
    """库里该类型的全部事件，按事件序号。"""
    return [event for event in map(json.loads, _bodies(engine)) if event["event_type"] == event_type]


def _logged_prefixes(caplog, prefix: str) -> list[logging.LogRecord]:
    """正文以 prefix 开头的 ERROR 日志记录。"""
    return [record for record in caplog.records
            if record.getMessage().startswith(prefix) and record.levelno == logging.ERROR]


def _host_records(caplog) -> list[logging.LogRecord]:
    """运行宿主模块日志器写下的全部记录。"""
    return [record for record in caplog.records if record.name == "worldmock.runtime.lifecycle.run_host"]


async def test_agent_failure_registers_run_failed_with_the_original_repr(engine_factory, host_factory, caplog):
    """人物一侧的模型调用最终失败：宿主补写失败前模型证据草稿并写入 run_failed 与 RUN_FAILED，原样传播异常。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [MODEL_FAILURE])
    with caplog.at_level(logging.ERROR), pytest.raises(LLMDispatchError) as caught:
        await host.run()
    assert caught.value is MODEL_FAILURE
    assert _events(engine, "action_result") == []
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": repr(MODEL_FAILURE)}]
    failures = [event["data"] for event in _events(engine, "model_response") if event["data"].get("phase") == "failure"]
    assert [data["error"] for data in failures] == [MODEL_SERVICE_FAILED]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {repr(MODEL_FAILURE)}"]
    assert len(_logged_prefixes(caplog, "Model service failed during model call")) == 1
    assert len(_logged_prefixes(caplog, "LifeAgent wakeup failed for agent 1 (")) == 1
    assert _host_records(caplog) == []
    assert failure_message(MODEL_FAILURE) == (
        "LLMDispatchError: Failed to get valid response after 3 attempts in 12.0 s. Last error type: "
        "InternalServerError. Error chain: InternalServerError <- ConnectError")
    texts = [*_bodies(engine), *(json.dumps(record, ensure_ascii=False) for record in _ops(engine))]
    assert not any("Agent decision failed: " in text for text in texts)


async def test_tool_dispatch_failure_preserves_cause_and_records_evidence(
    engine_factory, host_factory, caplog, monkeypatch,
):
    """成对开始工具第二条调度时沙盒服务失败：人物抛出 TOOL_SERVICE_FAILED（根因是注入异常），已登记草稿补写，
    运行故障登记这个异常的 repr。"""
    engine = engine_factory()
    start = [(MAIN_TOOL, main_arguments("interact", "家", "测试休息")),
             (SIDE_TOOL, side_arguments("interact", "哔哩哔哩"))]
    host, _ = _attach(host_factory, engine, [(start, "")])
    injected, calls = sqlite3.OperationalError("database is locked"), []
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sandbox = real_open(wakeup)
        real_dispatch = sandbox.dispatch_world_tool

        def dispatch(tool_name, request, action_id, note=None):
            calls.append(tool_name)
            if len(calls) == 1:
                return real_dispatch(tool_name, request, action_id, note)
            raise injected

        sandbox.dispatch_world_tool = dispatch
        return sandbox

    monkeypatch.setattr(engine, "open_sandbox", wrapped_open)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as caught:
        await host.run()
    assert type(caught.value) is RuntimeError
    assert str(caught.value) == TOOL_SERVICE_FAILED
    assert caught.value.__cause__ is injected
    assert len(_logged_prefixes(caplog, "Tool service failed during start_side_activity")) == 1
    assert _host_records(caplog) == []
    reason = f"RuntimeError('{TOOL_SERVICE_FAILED}')"
    assert len(_events(engine, "model_request")) == 1
    assert len(_events(engine, "model_response")) == 1
    assert _events(engine, "action_requested") == []
    assert _events(engine, "action_result") == []
    assert [event["data"]["error"] for event in _events(engine, "run_failed")] == [reason]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {reason}"]


async def test_turn_exhaustion_fails_without_finish(engine_factory, host_factory, caplog):
    """回合上限个模型回合都没有结束唤醒：通用循环写错误日志并抛出 ProtocolError，宿主登记它的 repr。"""
    engine = engine_factory(mutate=_two_turns)
    thinking = ([], "我再想想。")
    host, model = _attach(host_factory, engine, [thinking, thinking])
    with caplog.at_level(logging.ERROR), pytest.raises(ProtocolError) as caught:
        await host.run()
    error = caught.value
    assert type(error) is ProtocolError and str(error) == TURNS_EXHAUSTED
    assert len(_logged_prefixes(caplog, TURNS_EXHAUSTED)) == 1
    assert _host_records(caplog) == []
    assert model.decisions == 2
    reason = f"ProtocolError('{TURNS_EXHAUSTED}')"
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": reason}]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {reason}"]
    assert failure_message(error) == f"ProtocolError: {TURNS_EXHAUSTED}"
    assert "finish" not in failure_message(error)


async def test_prepare_barrier_failure_is_registered_directly(engine_factory, host_factory, caplog, monkeypatch):
    """prepare_barrier 抛出异常：宿主只做一次运行级登记，原样传出同一对象。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [])
    error = ProtocolError("Scheduled agents do not match current barrier")

    def prepare(agent_ids=None):
        raise error

    monkeypatch.setattr(engine, "prepare_barrier", prepare)
    with caplog.at_level(logging.ERROR), pytest.raises(BaseException) as caught:
        await host.run()
    assert caught.value is error
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": repr(error)}]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {repr(error)}"]


async def test_failed_evidence_recording_failure_keeps_both_errors_in_one_group(
    engine_factory, host_factory, caplog, monkeypatch,
):
    """补写失败草稿也失败：宿主写带回溯的错误日志，抛出保留两项异常的异常组，运行级登记写异常组 repr。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [MODEL_FAILURE])
    injected = sqlite3.OperationalError("database is locked")

    def record_failed(agent_id, wakeup_id, drafts):
        raise injected

    monkeypatch.setattr(engine, "record_failed_evidence", record_failed)
    with caplog.at_level(logging.ERROR), pytest.raises(ExceptionGroup) as caught:
        await host.run()
    group = caught.value
    assert group.message == "Run and failure recording both failed"
    assert len(group.exceptions) == 2
    assert group.exceptions[0] is MODEL_FAILURE and group.exceptions[1] is injected
    assert group.__cause__ is MODEL_FAILURE
    records = _logged_prefixes(caplog, "Failed evidence recording failed for agent 1")
    assert len(records) == 1 and records[0].exc_info is not None
    failed = _events(engine, "run_failed")
    assert len(failed) == 1 and failed[0]["data"]["error"] == repr(group)
    assert failure_message(group) == failure_message(MODEL_FAILURE) + " | OperationalError: database is locked"


async def test_failure_recording_failure_keeps_both_errors_in_one_group(
    engine_factory, host_factory, caplog, monkeypatch,
):
    """运行级登记本身也失败：宿主写错误日志，抛出保留原始异常与登记异常的异常组，库里没有 run_failed。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [])
    error = ProtocolError("Barrier prepare failed")
    injected = sqlite3.OperationalError("database is locked")

    def prepare(agent_ids=None):
        raise error

    def fail_run(reason):
        raise injected

    monkeypatch.setattr(engine, "prepare_barrier", prepare)
    monkeypatch.setattr(engine, "fail_run", fail_run)
    with caplog.at_level(logging.ERROR), pytest.raises(ExceptionGroup) as caught:
        await host.run()
    group = caught.value
    assert group.message == "Run and failure recording both failed"
    assert len(group.exceptions) == 2
    assert group.exceptions[0] is error and group.exceptions[1] is injected
    assert group.__cause__ is error
    assert len(_logged_prefixes(caplog, "Run failure recording failed")) == 1
    assert _events(engine, "run_failed") == []


async def test_midnight_failure_outside_a_barrier_records_the_original_repr(engine_factory, host_factory):
    """周一 20:00 睡下后第一个零点调用失败：运行级登记写原异常的 repr；失败证据照常写下。"""
    engine = engine_factory(mutate=_evening)
    error = LLMDispatchError("Failed to get valid response after 1 attempts in 0.0 s. Last error type: "
                             "AuthenticationError. Error chain: AuthenticationError")
    host, _ = _attach(host_factory, engine, [([(MAIN_TOOL, main_arguments("sleep", "480"))], "")], midnight=error)
    with pytest.raises(LLMDispatchError) as caught:
        await host.run()
    assert caught.value is error
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": repr(error)}]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {repr(error)}"]
    failures = [event["data"] for event in _events(engine, "model_response")
                if event["data"].get("phase") == "failure"]
    assert len(failures) == 1
    assert failures[0]["purpose"] in {"memory_digest", "plan_day"} and failures[0]["error"] == repr(error)
    texts = [*_bodies(engine), *(json.dumps(record, ensure_ascii=False) for record in _ops(engine)),
             repr(error), failure_message(error)]
    assert not any("RayTaskError" in text for text in texts)


async def test_base_exception_is_neither_aborted_nor_registered(engine_factory, host_factory):
    """人物唤醒抛出不是 Exception 的 BaseException：宿主不登记，原样传出同一对象。"""
    engine = engine_factory()
    stop = _Stop()
    host, _ = _attach(host_factory, engine, [stop])
    with pytest.raises(_Stop) as caught:
        await host.run()
    assert caught.value is stop
    assert _events(engine, "run_failed") == []
    assert _ops_details(engine, "RUN_FAILED") == []
