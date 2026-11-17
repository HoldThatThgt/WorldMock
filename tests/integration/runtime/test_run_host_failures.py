"""运行宿主的故障路径：真实引擎、真实 SQLite、真实 RunHost 与真实 LifeAgent，只把模型客户端换成按脚本作答的替身。

原异常直接到达宿主；只在引擎的决策轮仍开着时中止，中止原因与运行级登记都写原异常的 repr；中止或登记本身失败时
两项异常都保留在一个异常组里；不是 Exception 的 BaseException 不中止、不登记。
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
from worldmock.configuration.model.model_client import LLMDispatchError
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle.run_host import RunHost
from worldmock.runtime.monitoring.status import failure_message

#: 人物一侧模型调用最终失败时模型客户端抛出的异常。
MODEL_FAILURE = LLMDispatchError("Failed to get valid response after 3 attempts in 12.0 s. Last error type: "
                                 "InternalServerError. Error chain: InternalServerError <- ConnectError")
#: 三种阶段码的固定文字（人物一侧带调用位置的故障）。
MODEL_SERVICE_FAILED = "MODEL_SERVICE_FAILED: The model service failed before a response was available."
TOOL_SERVICE_FAILED = "TOOL_SERVICE_FAILED: The tool service failed before a result was available."
#: 回合上限个模型回合都没有登记世界请求时的异常文字。
TURNS_EXHAUSTED = "ReAct decision budget exhausted without a world request"
#: 替身每个回复都带的思考（同秒续接要求本决策的 assistant 回合都有思考）。
THINKING = "先想一想眼下该做什么。"


class _Stop(BaseException):
    """不是 Exception 的 BaseException：宿主既不中止也不登记，原样传出。"""


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
        """脚本每项是要抛出的异常，或（工具调用列表，正文）。"""
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
    """先预置当日计划（零点计划不在开头触发），再把真实运行宿主挂到这台引擎上，模型客户端是按脚本作答的替身。"""
    stage_day_plan(engine, submitted=True)
    model = _Model(script, midnight)
    host = host_factory(engine, model)
    assert isinstance(host, RunHost)
    return host, model


def _two_turns(persona, config) -> None:
    """一次决策至多两个模型回合。"""
    config["model"]["max_react_turns"] = 2


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


def _aborts(engine) -> list[dict]:
    """库里全部 decision_round_aborted 环境事件。"""
    return [event for event in _events(engine, "environment_event")
            if event["data"].get("kind") == "decision_round_aborted"]


def _round_id(engine) -> str:
    """最后一轮的 round_id（取自它的 decision_started）。"""
    return _events(engine, "decision_started")[-1]["data"]["round_id"]


def _logged(caplog, message: str) -> list[logging.LogRecord]:
    """正文恰为 message 的 ERROR 日志记录。"""
    return [record for record in caplog.records
            if record.getMessage() == message and record.levelno == logging.ERROR]


def _messages(caplog) -> list[str]:
    """捕获的全部日志正文。"""
    return [record.getMessage() for record in caplog.records]


def _host_records(caplog) -> list[logging.LogRecord]:
    """运行宿主模块日志器写下的全部记录（人物一侧的故障宿主不另写日志，这里应为空）。"""
    return [record for record in caplog.records if record.name == "worldmock.runtime.lifecycle.run_host"]


async def test_agent_failure_aborts_the_round_with_the_original_repr(engine_factory, host_factory, caplog):
    """人物一侧的模型调用最终失败：宿主捕获的就是模型客户端抛出的那个对象；中止原因、运行失败与运维日志都是它的
    repr，失败证据记模型服务的阶段码，宿主不另写日志，任何记录都没有包装文字。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [MODEL_FAILURE])
    with caplog.at_level(logging.ERROR), pytest.raises(LLMDispatchError) as caught:
        await host.run()
    assert caught.value is MODEL_FAILURE
    round_id = _round_id(engine)
    assert _events(engine, "action_result") == []
    assert [event["data"] for event in _aborts(engine)] == [
        {"kind": "decision_round_aborted", "round_id": round_id, "error": repr(MODEL_FAILURE)}]
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": repr(MODEL_FAILURE), "round_id": round_id}]
    failures = [event["data"] for event in _events(engine, "model_response") if event["data"].get("phase") == "failure"]
    assert [data["error"] for data in failures] == [MODEL_SERVICE_FAILED]
    assert _ops_details(engine, "DECISION_ROUND_ABORTED") == [
        f"Decision round aborted after 0 actions: {repr(MODEL_FAILURE)}"]
    assert _ops_details(engine, "RUN_FAILED") == []
    assert len(_logged(caplog, "LifeAgent decision failed for agent 1")) == 1
    assert _host_records(caplog) == []
    assert not any("Decision round abort failed" in message for message in _messages(caplog))
    assert failure_message(MODEL_FAILURE) == (
        "LLMDispatchError: Failed to get valid response after 3 attempts in 12.0 s. Last error type: "
        "InternalServerError. Error chain: InternalServerError <- ConnectError")
    texts = [*_bodies(engine), *(json.dumps(record, ensure_ascii=False) for record in _ops(engine))]
    assert not any("Agent decision failed: " in text for text in texts)


async def test_registered_start_is_aborted_with_the_tool_stage_repr(engine_factory, host_factory, caplog,
                                                                    monkeypatch):
    """开始组的第二条登记时工具服务失败：人物抛出带工具阶段码的 RuntimeError（根因是注入的异常）；已登记的第一条
    动作被中止，它的结果、中止事件、运行失败与运维日志都写这个异常的 repr。"""
    engine = engine_factory()
    start = [(MAIN_TOOL, main_arguments("interact", "家", "测试休息")),
             (SIDE_TOOL, side_arguments("interact", "哔哩哔哩"))]
    host, _ = _attach(host_factory, engine, [(start, "")])
    injected, real, calls = sqlite3.OperationalError("database is locked"), engine.submit_action, []

    def submit(call):
        """第一次交给真实方法，第二次抛出注入的异常。"""
        calls.append(call)
        if len(calls) == 1:
            return real(call)
        raise injected

    monkeypatch.setattr(engine, "submit_action", submit)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as caught:
        await host.run()
    assert type(caught.value) is RuntimeError
    assert str(caught.value) == TOOL_SERVICE_FAILED
    assert caught.value.__cause__ is injected
    assert len(_logged(caplog, "LifeAgent tool dispatch failed: start_side_activity")) == 1
    assert _host_records(caplog) == []
    reason = f"RuntimeError('{TOOL_SERVICE_FAILED}')"
    requested = _events(engine, "action_requested")
    results = _events(engine, "action_result")
    assert len(requested) == 1 and len(results) == 1
    assert results[0]["action_id"] == requested[0]["action_id"]
    assert (results[0]["data"]["status"], results[0]["data"]["error_code"], results[0]["data"]["message"]) == (
        "failed", "DECISION_ROUND_ABORTED", reason)
    assert [event["data"]["error"] for event in _aborts(engine)] == [reason]
    assert [event["data"]["error"] for event in _events(engine, "run_failed")] == [reason]
    assert _ops_details(engine, "DECISION_ROUND_ABORTED") == [f"Decision round aborted after 1 actions: {reason}"]


async def test_turn_exhaustion_aborts_with_the_text_without_finish(engine_factory, host_factory, caplog):
    """回合上限个模型回合都没有登记世界请求：回合循环写一条错误日志后抛出不再提 finish 的文字，宿主以它的 repr 中止。"""
    engine = engine_factory(mutate=_two_turns)
    thinking = ([], "我再想想。")
    host, model = _attach(host_factory, engine, [thinking, thinking])
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as caught:
        await host.run()
    error = caught.value
    assert type(error) is RuntimeError and str(error) == TURNS_EXHAUSTED
    assert len(_logged(caplog, "LifeAgent 1 used all 2 model turns without a world request")) == 1
    assert _host_records(caplog) == []
    assert model.decisions == 2
    reason = f"RuntimeError('{TURNS_EXHAUSTED}')"
    assert [event["data"]["error"] for event in _aborts(engine)] == [reason]
    assert [event["data"]["error"] for event in _events(engine, "run_failed")] == [reason]
    assert failure_message(error) == f"RuntimeError: {TURNS_EXHAUSTED}"
    assert "finish" not in failure_message(error)


async def test_budget_exhaustion_after_commit_stops_with_one_protocol_error(engine_factory, host_factory, caplog):
    """同一秒的第六轮提交写下 decision_budget_exhausted 与 run_failed 并关闭本轮后才抛出：宿主不中止，没有假的
    「决策轮没有开着」，运行以那个 ProtocolError 停下，提交写下的 run_failed 是库里最后一条事件。"""
    engine = engine_factory()
    revise = ([("revise_day_plan", {"goals": []})], "")
    host, _ = _attach(host_factory, engine, [revise] * 6)
    # 接住任何异常，由下面的精确类型断言判定：错误的异常（如中止失败的异常组）以断言失败报出，而不是逃出 raises。
    with caplog.at_level(logging.ERROR), pytest.raises(BaseException) as caught:
        await host.run()
    error = caught.value
    assert type(error) is ProtocolError and str(error) == "decision_budget_exhausted"
    failed = _events(engine, "run_failed")
    assert [event["data"] for event in failed] == [
        {"error_type": "RuntimeError", "error": "decision_budget_exhausted", "round_id": _round_id(engine)}]
    assert failed[0]["event_seq"] == max(json.loads(body)["event_seq"] for body in _bodies(engine))
    assert _aborts(engine) == []
    assert _ops_details(engine, "DECISION_ROUND_ABORTED") == [] and _ops_details(engine, "RUN_FAILED") == []
    messages = _messages(caplog)
    assert not any("Decision round is not open" in message for message in messages)
    assert not any("Decision round abort failed" in message for message in messages)
    assert failure_message(error) == "ProtocolError: decision_budget_exhausted"
    assert type(error).__name__ == "ProtocolError"


async def test_begin_failure_is_registered_without_an_abort(engine_factory, host_factory, caplog, monkeypatch):
    """开轮调用在打开本轮之前抛出：宿主不中止，只做一次运行级登记（round_id 为 null），原样传出同一对象。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [])
    error = ProtocolError("Decision round contexts must match the scheduled agent order")

    def begin(contexts):
        """不打开本轮就抛出。"""
        raise error

    monkeypatch.setattr(engine, "begin_decision_round", begin)
    # 接住任何异常，由下面的同一对象断言判定：错误的异常（如中止失败的异常组）以断言失败报出，而不是逃出 raises。
    with caplog.at_level(logging.ERROR), pytest.raises(BaseException) as caught:
        await host.run()
    assert caught.value is error
    assert _aborts(engine) == []
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError",
         "error": "ProtocolError('Decision round contexts must match the scheduled agent order')", "round_id": None}]
    assert _ops_details(engine, "RUN_FAILED") == [
        "Run failed: ProtocolError('Decision round contexts must match the scheduled agent order')"]
    assert not any("Decision round is not open" in message for message in _messages(caplog))


async def test_abort_failure_keeps_both_errors_in_one_group(engine_factory, host_factory, caplog, monkeypatch):
    """中止本身也失败：宿主写一条带回溯的错误日志，抛出保留两项异常的异常组；运行级登记写这个异常组的 repr。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [MODEL_FAILURE])
    injected = sqlite3.OperationalError("database is locked")

    def abort(round_id, reason):
        """中止时抛出注入的异常。"""
        raise injected

    monkeypatch.setattr(engine, "abort_decision_round", abort)
    with caplog.at_level(logging.ERROR), pytest.raises(ExceptionGroup) as caught:
        await host.run()
    group = caught.value
    assert group.message == "Decision round and abort both failed"
    assert len(group.exceptions) == 2
    assert group.exceptions[0] is MODEL_FAILURE and group.exceptions[1] is injected
    assert group.__cause__ is MODEL_FAILURE
    records = _logged(caplog, "Decision round abort failed")
    assert len(records) == 1 and records[0].exc_info is not None
    assert _aborts(engine) == []
    failed = _events(engine, "run_failed")
    assert len(failed) == 1
    assert failed[0]["data"]["error"] == repr(group) and failed[0]["data"]["round_id"] is None
    assert failure_message(group) == failure_message(MODEL_FAILURE) + " | OperationalError: database is locked"


async def test_failure_recording_failure_keeps_both_errors_in_one_group(engine_factory, host_factory, caplog,
                                                                        monkeypatch):
    """运行级登记本身也失败：宿主写一条错误日志，抛出保留开轮异常与登记异常的异常组，库里没有 run_failed。"""
    engine = engine_factory()
    host, _ = _attach(host_factory, engine, [])
    error = ProtocolError("Decision round contexts must match the scheduled agent order")
    injected = sqlite3.OperationalError("database is locked")

    def begin(contexts):
        """不打开本轮就抛出。"""
        raise error

    def fail_run(reason):
        """登记运行故障时抛出注入的异常。"""
        raise injected

    monkeypatch.setattr(engine, "begin_decision_round", begin)
    monkeypatch.setattr(engine, "fail_run", fail_run)
    with caplog.at_level(logging.ERROR), pytest.raises(ExceptionGroup) as caught:
        await host.run()
    group = caught.value
    assert group.message == "Run and failure recording both failed"
    assert len(group.exceptions) == 2
    assert group.exceptions[0] is error and group.exceptions[1] is injected
    assert group.__cause__ is error
    assert len(_logged(caplog, "Run failure recording failed")) == 1
    assert _events(engine, "run_failed") == []


async def test_midnight_failure_outside_a_round_records_the_original_repr(engine_factory, host_factory):
    """周一 20:00 睡下后第一个零点调用失败（决策轮之外）：运行级登记写原异常的 repr，round_id 为 null，不中止；
    这次零点调用的失败证据照常写下，任何文本都没有远端任务错误的类名。"""
    engine = engine_factory(mutate=_evening)
    error = LLMDispatchError("Failed to get valid response after 1 attempts in 0.0 s. Last error type: "
                             "AuthenticationError. Error chain: AuthenticationError")
    host, _ = _attach(host_factory, engine, [([(MAIN_TOOL, main_arguments("sleep", "480"))], "")], midnight=error)
    with pytest.raises(LLMDispatchError) as caught:
        await host.run()
    assert caught.value is error
    assert [event["data"] for event in _events(engine, "run_failed")] == [
        {"error_type": "RuntimeError", "error": repr(error), "round_id": None}]
    assert _ops_details(engine, "RUN_FAILED") == [f"Run failed: {repr(error)}"]
    assert _aborts(engine) == []
    failures = [event["data"] for event in _events(engine, "model_response")
                if event["data"].get("phase") == "failure"]
    assert len(failures) == 1
    assert failures[0]["purpose"] in {"memory_digest", "plan_day"} and failures[0]["error"] == repr(error)
    texts = [*_bodies(engine), *(json.dumps(record, ensure_ascii=False) for record in _ops(engine)),
             repr(error), failure_message(error)]
    assert not any("RayTaskError" in text for text in texts)


async def test_base_exception_is_neither_aborted_nor_registered(engine_factory, host_factory):
    """人物决策抛出不是 Exception 的 BaseException：宿主不中止、不登记，原样传出同一对象。"""
    engine = engine_factory()
    stop = _Stop()
    host, _ = _attach(host_factory, engine, [stop])
    with pytest.raises(_Stop) as caught:
        await host.run()
    assert caught.value is stop
    assert _aborts(engine) == [] and _events(engine, "run_failed") == []
    assert _ops_details(engine, "DECISION_ROUND_ABORTED") == [] and _ops_details(engine, "RUN_FAILED") == []
