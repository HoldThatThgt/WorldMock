"""输出截断与思考螺旋验收（docs/design/10-runtime.md，AgentLoop 截断重试与前缀恒定合同）。

真实 LifeEngine 与 SQLite 上，脚本化模型经真实 LifeAgent.decide_wakeup 运行：
thinking 用尽输出上限的截断回合（finish_reason=length、正文为空、没有工具调用）不进入后续请求，也不插入第二条
user 纠错消息，纠错为 OUTPUT_TRUNCATED；全回合保持 11 个原生工具、thinking 开启与 tool_choice=auto 恒定不变；
正文写到上限、空 stop 回复、空 choices 保持前缀不变并按 TOOL_CALL_REQUIRED 纠错，截断了工具 JSON 则按
ARGUMENTS_NOT_JSON 配对 tool 消息纠错；质量报告(v12) truncations 与记录的截断响应一致。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.life_host_fixtures import TruncatedResponse  # noqa: E402
from tests.integration.runtime.current_contract_fixtures import (  # noqa: E402
    ScriptedModel,
    decide,
    resolved_binding,
)
from tests.life.conftest import MAIN_TOOL, advance_next, main_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.life_agent import (  # noqa: E402
    LifeAgent,
    life_action_tool_names,
    life_query_tool_names,
    person_inputs,
)
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.protocol.agent_loop import TURNS_EXHAUSTED  # noqa: E402
from worldmock.agents.protocol.life_truncation import TRUNCATION_DETAIL  # noqa: E402
from worldmock.configuration.model.model_client import REQUEST_KEYS  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.recording_engine.export.export import export_run  # noqa: E402

#: 截断回合的思考文本。
_SPIRALS = ("先想想要不要回家，家里没有吃的，外卖要等，还是先去楼下……", "再算一遍：回家要四十分钟，楼下那家没有菜单……")
#: 在家点名测试休息的参数与修订计划参数。
_WAIT = main_arguments("interact", "家", "测试休息")
_PLAN = {"goals": [{"intent_kind": "休息", "goal": "下午在家休息一会儿", "remind_time": None, "around_time": None}]}
_FULL_TOOLS = set(life_action_tool_names()) | set(life_query_tool_names())


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _reply(call_id: str, name: str, arguments: dict, reasoning: str | None = "先想清楚再动手。",
           content: str = "") -> SimpleNamespace:
    tool_call = {"id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}
    doc = {"choices": [{"finish_reason": "tool_calls", "index": 0, "message": {
        "content": content, "reasoning_content": reasoning, "tool_calls": [tool_call]}}]}
    msg = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=[
        SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(**tool_call["function"]))])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", index=0, message=msg)],
                           model_dump=lambda *, mode: copy.deepcopy(doc))


class _TextOnly:
    """只有正文、没有工具调用的回复（finish_reason=stop）。"""

    def __init__(self, content: str = "我先想一想再说。") -> None:
        self._content = content
        message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
        self.choices = [SimpleNamespace(finish_reason="stop", index=0, message=message)]

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"model": "test-model", "choices": [{"finish_reason": "stop", "index": 0,
                                                    "message": {"role": "assistant", "content": self._content,
                                                                "tool_calls": None}}]}


class _Finished:
    """非截断回合形态（length 带正文、stop 为空或截断了工具 JSON）。"""

    _REASONING = "想一想再调用工具。"

    def __init__(self, finish_reason: str, content: str, arguments: str | None = None) -> None:
        self._finish, self._content, self._arguments = finish_reason, content, arguments
        calls = None if arguments is None else [SimpleNamespace(
            id="call-cut", type="function", function=SimpleNamespace(name=MAIN_TOOL, arguments=arguments))]
        self.choices = [SimpleNamespace(finish_reason=finish_reason, index=0,
                                        message=SimpleNamespace(content=content, tool_calls=calls,
                                                                reasoning_content=self._REASONING))]

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        calls = None if self._arguments is None else [{"id": "call-cut", "type": "function",
                                                       "function": {"name": MAIN_TOOL, "arguments": self._arguments}}]
        return {"model": "test-model", "choices": [{"finish_reason": self._finish, "index": 0, "message": {
            "role": "assistant", "content": self._content, "tool_calls": calls,
            "reasoning_content": self._REASONING}}]}


class _NoChoices:
    """没有选项的空回复。"""

    def __init__(self) -> None:
        self.choices: list = []

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"model": "test-model", "choices": []}


def _tools(request: dict) -> set[str]:
    return {item["function"]["name"] for item in request["tools"]}


def _assert_constant_wire(request: dict) -> None:
    """开启思考时全回合保持 11 个原生工具、thinking 开启、tool_choice=auto 且无第二条 user 消息。"""
    assert request["max_tokens"] == 16000 and sorted(request) == sorted(REQUEST_KEYS)
    assert "reasoning_effort" not in request and "temperature" not in request
    assert all(item["function"]["strict"] is True for item in request["tools"])
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["tool_choice"] == "auto"
    assert _tools(request) == _FULL_TOOLS and len(_tools(request)) == 11
    assert [m["role"] for m in request["messages"] if m["role"] == "user"] == ["user"]


def _wakeup_responses(engine, wakeup_id: str) -> list:
    return [e for e in engine.context.store.read_agent_events("model_response", 1) if e.wakeup_id == wakeup_id]


@pytest.mark.asyncio
async def test_a_truncated_turn_is_dropped_and_retried_once_without_thinking(_engine_factory, caplog) -> None:
    """截断回合不进历史也不插入第二条 user 纠错，前缀逐字节不变，工具与思考保持恒定。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    with caplog.at_level(logging.ERROR):
        _, requests, _, _ = await decide(engine, "spiral", [
            TruncatedResponse(_SPIRALS[0]),
            _reply("call-wait", MAIN_TOOL, _WAIT, None, "先歇一会儿。"),
        ])
    assert [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Life tool protocol rejected: OUTPUT_TRUNCATED")] == [
        f"Life tool protocol rejected: OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}"]
    first, retry = requests
    _assert_constant_wire(first)
    _assert_constant_wire(retry)
    assert retry["messages"] == first["messages"]
    assert _SPIRALS[0] not in json.dumps(retry, ensure_ascii=False)
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    assert registered.data["tool_name"] == MAIN_TOOL and registered.data["self_note"] == "先歇一会儿。"
    truncated, answered = _wakeup_responses(engine, registered.wakeup_id)
    assert truncated.data["error"] == f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}" and answered.data["error"] is None
    assert truncated.data["response"]["protocol_feedback"]["error_code"] == "OUTPUT_TRUNCATED"
    assert truncated.data["response"]["choices"][0]["message"]["reasoning_content"] == _SPIRALS[0]
    assert engine.context.store.read_agent_events("action_result", 1)[-1].data["error_code"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "code", "paired_tool"), [
    pytest.param(_Finished("length", "我先想想要不要回家，家里没有吃的……"), "TOOL_CALL_REQUIRED", False,
                 id="length_with_text"),
    pytest.param(_Finished("stop", ""), "TOOL_CALL_REQUIRED", False, id="empty_stop"),
    pytest.param(_Finished("length", "", '{"action": "interact", "target": "家", "detail": "测试'),
                 "ARGUMENTS_NOT_JSON", True, id="length_with_cut_tool_call"),
    pytest.param(_NoChoices(), "TOOL_CALL_REQUIRED", False, id="no_choices"),
])
async def test_only_a_reply_that_ran_out_while_thinking_is_a_truncation(
    _engine_factory, reply, code: str, paired_tool: bool,
) -> None:
    """非「思考写满无调用无正文」形态按现行协议纠错：带调用编号时配对 tool 纠错，无调用时保持前缀不变。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, _, _ = await decide(engine, "not-truncated", [reply, _reply("call-w", MAIN_TOOL, _WAIT)])
    _assert_constant_wire(requests[0])
    _assert_constant_wire(requests[1])
    if paired_tool:
        assert requests[1]["messages"][-1]["role"] == "tool"
        assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == code
    else:
        assert requests[1]["messages"] == requests[0]["messages"]
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    first, answered = _wakeup_responses(engine, registered.wakeup_id)
    assert not first.data["error"].startswith("OUTPUT_TRUNCATED") and answered.data["error"] is None
    assert first.data["response"]["protocol_feedback"]["error_code"] == code


@pytest.mark.asyncio
async def test_identical_corrections_replace_each_other_across_consecutive_truncations(_engine_factory) -> None:
    """连续截断与非法纯文本均不进消息历史也不追加 user 纠错，前缀缓存逐字节保持不变。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), _TextOnly(), TruncatedResponse(_SPIRALS[1]),
                 _reply("call-wait", MAIN_TOOL, _WAIT, None)]
    _, requests, _, _ = await decide(engine, "twice", responses)
    for req in requests:
        _assert_constant_wire(req)
        assert req["messages"] == requests[0]["messages"]
    assert all(item["role"] != "assistant" for request in requests for item in request["messages"])
    wire = json.dumps(requests[1:], ensure_ascii=False)
    assert _SPIRALS[0] not in wire and _SPIRALS[1] not in wire and "我先想一想再说。" not in wire
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    errors = [event.data["error"] for event in _wakeup_responses(engine, registered.wakeup_id)]
    assert errors == [
        f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}",
        "TOOL_CALL_REQUIRED: At least one complete native tool call is required.",
        f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}",
        None,
    ]


@pytest.mark.asyncio
async def test_a_retry_that_is_truncated_again_is_not_retried_again(_engine_factory) -> None:
    """连续两次截断均保持恒定 11 工具、恒定思考与干净前缀不变。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), TruncatedResponse(_SPIRALS[1]), _reply("call-w", MAIN_TOOL, _WAIT)]
    _, requests, _, _ = await decide(engine, "again", responses)
    for req in requests:
        _assert_constant_wire(req)
    assert requests[2]["messages"] == requests[1]["messages"] == requests[0]["messages"]


@pytest.mark.asyncio
async def test_a_retry_still_without_a_tool_call_follows_the_turn_budget(_engine_factory) -> None:
    """四个回合全部耗尽且包含截断与无工具调用：抛出 ProtocolError(TURNS_EXHAUSTED)，不登记任何动作。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    model = ScriptedModel([TruncatedResponse(_SPIRALS[0]), _TextOnly(), _TextOnly("还是再想想。"), _TextOnly("再等等。")])
    binding = resolved_binding(model, frozen=engine.context.inputs, max_turns=4)
    with pytest.raises(ProtocolError, match=f"^{TURNS_EXHAUSTED}$"):
        await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(wakeup, sb, binding)
    engine.record_failed_evidence(1, wakeup.wakeup_id, sb.drain_evidence())
    requests = model.requests
    assert len(requests) == 4
    for req in requests:
        _assert_constant_wire(req)
        assert req["messages"] == requests[0]["messages"]
    assert engine.context.store.read_agent_events("action_requested", 1) == []


@pytest.mark.asyncio
async def test_control_call_on_retry_keeps_thinking_enabled_for_next_turn_in_same_wakeup(_engine_factory) -> None:
    """同一唤醒中截断重试提交 revise_day_plan 后，消息序列严格为 [system, user, assistant, tool]，截断思考不入历史。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, answer, _ = await decide(engine, "in-loop-retry", [
        TruncatedResponse(_SPIRALS[0]),
        _reply("call-plan", "revise_day_plan", _PLAN, None),
        _reply("call-wait", MAIN_TOOL, _WAIT, "计划调完了，开始休息。"),
    ])
    assert answer == ["revise_day_plan", MAIN_TOOL]
    for req in requests:
        _assert_constant_wire(req)
    third_msgs = requests[2]["messages"]
    assert [m["role"] for m in third_msgs] == ["system", "user", "assistant", "tool"]
    assert third_msgs[2]["tool_calls"][0]["id"] == "call-plan"
    assert "reasoning_content" not in third_msgs[2]
    assert _SPIRALS[0] not in json.dumps(third_msgs, ensure_ascii=False)


@pytest.mark.asyncio
async def test_rejected_retry_turns_are_dropped_while_valid_assistant_thoughts_remain(_engine_factory) -> None:
    """先 revise_day_plan 保留思考，再截断并无调用回复：无效截断与纯文本重试不入历史，先前合法思考保留。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, _, _ = await decide(engine, "rejected-retry", [
        _reply("call-plan", "revise_day_plan", _PLAN, "先把计划写好。"),
        TruncatedResponse(_SPIRALS[0]),
        _TextOnly("还是不确定该做什么。"),
        _reply("call-wait", MAIN_TOOL, _WAIT, "接着开始休息。"),
    ])
    for req in requests:
        _assert_constant_wire(req)
    last_msgs = requests[3]["messages"]
    assistants = [m for m in last_msgs if m["role"] == "assistant"]
    assert len(assistants) == 1 and assistants[0]["reasoning_content"] == "先把计划写好。"
    assert [m["role"] for m in last_msgs] == ["system", "user", "assistant", "tool"]
    assert _SPIRALS[0] not in json.dumps(last_msgs, ensure_ascii=False)
    assert "还是不确定该做什么。" not in json.dumps(last_msgs, ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_exported_quality_report_counts_the_truncations_the_run_recorded(_engine_factory, tmp_path) -> None:
    """两次独立唤醒共三次截断：导出质量报告 quality.json(v12) 与事件逐项一致。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    await decide(engine, "plan", [TruncatedResponse(_SPIRALS[0], 8001),
                                  _reply("call-plan", "revise_day_plan", _PLAN, None),
                                  _reply("call-wait1", MAIN_TOOL, _WAIT, None)])
    advance_next(engine)
    # 结束前次睡眠后唤醒再产生两次截断
    name = next(iter(engine.context.state.activities.values())).name
    await decide(engine, "wait", [
        TruncatedResponse(_SPIRALS[1]), _TextOnly(), TruncatedResponse(_SPIRALS[0]),
        _reply("call-conclude", "conclude_activity", {
            "items": [{"activity_name": name, "summary": "好了。", "memories": [], "views": []}], "errands": []}, None),
        _reply("call-wait2", MAIN_TOOL, _WAIT, None),
    ])
    advance_next(engine)
    export = tmp_path / "export"
    export_run(engine.context.store, engine.context.store.last_event_seq, export)
    quality = json.loads((export / "quality.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (export / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    recorded = [row["data"]["response"] for row in rows if row["event_type"] == "model_response"
                and row["data"]["response"]["choices"][0].get("finish_reason") == "length"]
    assert len(recorded) == 3
    assert quality["truncations"] == {
        "truncated_responses": 3, "purpose_counts": {"decision": 3},
        "wasted_output_tokens": sum(item["usage"]["completion_tokens"] for item in recorded),
        "reasoning_tokens": sum(item["usage"]["completion_tokens_details"]["reasoning_tokens"] for item in recorded),
        "responses_missing_output_tokens": 0, "responses_missing_reasoning_tokens": 0,
        "method": quality["truncations"]["method"],
    }
    assert quality["truncations"]["wasted_output_tokens"] == 24001 == quality["truncations"]["reasoning_tokens"]
    assert quality["schema_version"] == 12


@pytest.mark.asyncio
async def test_export_with_model_calls_is_byte_identical_with_and_without_the_post_run_view(
    _engine_factory, tmp_path, monkeypatch,
) -> None:
    """运行后提纲视图投影不影响完整导出报告或事件的逐字节结果。"""
    from worldmock.recording_engine.export import export as export_module

    engine = _engine_factory()
    stage_day_plan(engine)
    await decide(engine, "plan", [TruncatedResponse(_SPIRALS[0], 8001),
                                  _reply("call-wait", MAIN_TOOL, _WAIT, None)])
    advance_next(engine)
    store = engine.context.store
    captured, original = [], export_module.build_quality_report

    def capture(evts, *args):
        captured.extend(evts)
        return original(evts, *args)

    with monkeypatch.context() as patched:
        patched.setattr(export_module, "build_quality_report", capture)
        export_run(store, store.last_event_seq, tmp_path / "views")
    with monkeypatch.context() as patched:
        patched.setattr(export_module, "post_run_view", lambda event: event)
        export_run(store, store.last_event_seq, tmp_path / "full")
    views = {p.relative_to(tmp_path / "views").as_posix(): p.read_bytes() for p in sorted((tmp_path / "views").rglob("*")) if p.is_file()}
    full = {p.relative_to(tmp_path / "full").as_posix(): p.read_bytes() for p in sorted((tmp_path / "full").rglob("*")) if p.is_file()}
    assert views == full
    outlines = [e.data["request"] for e in captured if e.event_type in {"model_request", "model_response"}
                and isinstance(e.data.get("request"), dict)]
    assert outlines and not any({"content", "reasoning_content"} & set(m) for r in outlines for m in r["messages"])
