"""输出截断与思考螺旋验收（docs/design/10-runtime.md，AgentLoop 截断重试合同）。

真实 LifeEngine 与 SQLite 上，脚本化模型经真实 LifeAgent.decide_wakeup 运行：
thinking 用尽输出上限的截断回合（finish_reason=length、正文为空、没有工具调用）不进入后续请求，纠错为
OUTPUT_TRUNCATED；紧随其后的一次重试关闭思考并 tool_choice=required、仅开放六个核心世界请求、max_tokens 不变；
正文写到上限、空 stop 回复、参数截断或空 choices 都按常规协议纠错而非截断重试；重试后正常世界请求或控制请求
恢复后续回合思考；质量报告 truncations 与记录的截断响应一致。
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
from worldmock.agents.protocol.life_truncation import TRUNCATION_CONTENT, TRUNCATION_DETAIL  # noqa: E402
from worldmock.configuration.model.model_client import REQUEST_KEYS  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.recording_engine.export.export import export_run  # noqa: E402
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402

#: 截断回合的思考文本。
_SPIRALS = ("先想想要不要回家，家里没有吃的，外卖要等，还是先去楼下……", "再算一遍：回家要四十分钟，楼下那家没有菜单……")
#: 在家点名测试休息的参数与修订计划参数。
_WAIT = main_arguments("interact", "家", "测试休息")
_PLAN = {"goals": [{"intent_kind": "休息", "goal": "下午在家休息一会儿", "remind_time": None, "around_time": None}]}
_FULL_TOOLS = set(life_action_tool_names()) | set(life_query_tool_names())
_CORE_TOOLS = set(life_action_tool_names()) - {SEARCH_TOOL, "recall_task_summary"}
_TRUNCATION_FEEDBACK = json.loads(TRUNCATION_CONTENT)


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


def _is_retry(request: dict) -> bool:
    assert request["max_tokens"] == 16000 and sorted(request) == sorted(REQUEST_KEYS)
    assert "reasoning_effort" not in request and "temperature" not in request
    assert all(item["function"]["strict"] is True for item in request["tools"])
    retry = (request["extra_body"], request["tool_choice"]) == ({"thinking": {"type": "disabled"}}, "required")
    assert retry or (request["extra_body"], request["tool_choice"]) == ({"thinking": {"type": "enabled"}}, "auto")
    return retry


def _tools(request: dict) -> set[str]:
    return {item["function"]["name"] for item in request["tools"]}


def _corrections(request: dict) -> list[str]:
    return [json.loads(m["content"])["error_code"]
            for m in request["messages"][2:] if m["role"] == "user"]


def _wakeup_responses(engine, wakeup_id: str) -> list:
    return [e for e in engine.context.store.read_agent_events("model_response", 1) if e.wakeup_id == wakeup_id]


@pytest.mark.asyncio
async def test_a_truncated_turn_is_dropped_and_retried_once_without_thinking(_engine_factory, caplog) -> None:
    """截断回合不进历史：重试仅保留首轮消息加一条截断纠错，关思考、required、仅开 6 个核心世界工具。"""
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
    assert [_is_retry(item) for item in requests] == [False, True]
    assert _tools(first) == _FULL_TOOLS and _tools(retry) == _CORE_TOOLS
    assert retry["messages"][:2] == first["messages"] and len(retry["messages"]) == 3
    feedback = retry["messages"][2]
    assert feedback["role"] == "user" and json.loads(feedback["content"]) == _TRUNCATION_FEEDBACK
    assert _SPIRALS[0] not in json.dumps(retry, ensure_ascii=False)
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    assert registered.data["tool_name"] == MAIN_TOOL and registered.data["self_note"] == "先歇一会儿。"
    truncated, answered = _wakeup_responses(engine, registered.wakeup_id)
    assert truncated.data["error"] == f"OUTPUT_TRUNCATED: {TRUNCATION_DETAIL}" and answered.data["error"] is None
    assert truncated.data["response"]["choices"][0]["message"]["reasoning_content"] == _SPIRALS[0]
    assert engine.context.store.read_agent_events("action_result", 1)[-1].data["error_code"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "code"), [
    pytest.param(_Finished("length", "我先想想要不要回家，家里没有吃的……"), "TOOL_CALL_REQUIRED", id="length_with_text"),
    pytest.param(_Finished("stop", ""), "TOOL_CALL_REQUIRED", id="empty_stop"),
    pytest.param(_Finished("length", "", '{"action": "interact", "target": "家", "detail": "测试'), "ARGUMENTS_NOT_JSON",
                 id="length_with_cut_tool_call"),
    pytest.param(_NoChoices(), "TOOL_CALL_REQUIRED", id="no_choices"),
])
async def test_only_a_reply_that_ran_out_while_thinking_is_a_truncation(_engine_factory, reply, code: str) -> None:
    """非「思考写满无调用无正文」形态按现行协议纠错、不发截断重试。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, _, _ = await decide(engine, "not-truncated", [reply, _reply("call-w", MAIN_TOOL, _WAIT)])
    assert [_is_retry(item) for item in requests] == [False, False]
    assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == code
    assert _tools(requests[1]) == _FULL_TOOLS
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    first, answered = _wakeup_responses(engine, registered.wakeup_id)
    assert not first.data["error"].startswith("OUTPUT_TRUNCATED") and answered.data["error"] is None


@pytest.mark.asyncio
async def test_identical_corrections_replace_each_other_across_consecutive_truncations(_engine_factory) -> None:
    """连续截断时后一次 OUTPUT_TRUNCATED 替换前一次而不叠加；截断思考与非法无调用文本均不进后续消息。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), _TextOnly(), TruncatedResponse(_SPIRALS[1]),
                 _reply("call-wait", MAIN_TOOL, _WAIT, None)]
    _, requests, _, _ = await decide(engine, "twice", responses)
    assert [_is_retry(item) for item in requests] == [False, True, False, True]
    assert [_corrections(item) for item in requests] == [
        [], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED"],
        ["TOOL_CALL_REQUIRED", "OUTPUT_TRUNCATED"]]
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
    """重试本身再次截断时不再连续发截断重试，下一请求回到开启思考与全量 9 工具。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    responses = [TruncatedResponse(_SPIRALS[0]), TruncatedResponse(_SPIRALS[1]), _reply("call-w", MAIN_TOOL, _WAIT)]
    _, requests, _, _ = await decide(engine, "again", responses)
    assert [_is_retry(item) for item in requests] == [False, True, False]
    assert [_corrections(item) for item in requests] == [[], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED"]]
    assert _tools(requests[2]) == _FULL_TOOLS
    assert requests[2]["messages"] == requests[1]["messages"]


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
    assert [_is_retry(item) for item in requests] == [False, True, False, False]
    assert [_corrections(item) for item in requests] == [
        [], ["OUTPUT_TRUNCATED"], ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED"],
        ["OUTPUT_TRUNCATED", "TOOL_CALL_REQUIRED", "TOOL_CALL_REQUIRED"]]
    assert _tools(requests[2]) == _FULL_TOOLS and _tools(requests[3]) == _CORE_TOOLS
    assert engine.context.store.read_agent_events("action_requested", 1) == []


@pytest.mark.asyncio
async def test_control_call_on_retry_keeps_thinking_enabled_for_next_turn_in_same_wakeup(_engine_factory) -> None:
    """同一连续唤醒中截断重试提交 revise_day_plan（关闭思考）后，下一回合立刻恢复思考并继续决策，截断思考不入历史。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, answer, _ = await decide(engine, "in-loop-retry", [
        TruncatedResponse(_SPIRALS[0]),
        _reply("call-plan", "revise_day_plan", _PLAN, None),
        _reply("call-wait", MAIN_TOOL, _WAIT, "计划调完了，开始休息。"),
    ])
    assert answer == ["revise_day_plan", MAIN_TOOL]
    assert [_is_retry(item) for item in requests] == [False, True, False]
    assert _tools(requests[1]) == _CORE_TOOLS and _tools(requests[2]) == _FULL_TOOLS
    third_msgs = requests[2]["messages"]
    assert [m["role"] for m in third_msgs] == ["system", "user", "user", "assistant", "tool"]
    assert third_msgs[3]["tool_calls"][0]["id"] == "call-plan"
    assert "reasoning_content" not in third_msgs[3]
    assert _SPIRALS[0] not in json.dumps(third_msgs, ensure_ascii=False)


@pytest.mark.asyncio
async def test_rejected_retry_turns_are_dropped_while_valid_assistant_thoughts_remain(_engine_factory) -> None:
    """先 revise_day_plan 保留思考，再截断并尝试在重试调用不在工具面内的 search_memory：被拒重试不留助手消息。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _, requests, _, _ = await decide(engine, "rejected-retry", [
        _reply("call-plan", "revise_day_plan", _PLAN, "先把计划写好。"),
        TruncatedResponse(_SPIRALS[0]),
        _reply("call-mem", "search_memory", {"category": "全部", "time": "今天", "keyword": None}, None),
        _reply("call-wait", MAIN_TOOL, _WAIT, "接着开始休息。"),
    ])
    assert [_is_retry(item) for item in requests] == [False, False, True, False]
    last_msgs = requests[3]["messages"]
    assistants = [m for m in last_msgs if m["role"] == "assistant"]
    assert len(assistants) == 1 and assistants[0]["reasoning_content"] == "先把计划写好。"
    assert "call-mem" not in json.dumps(last_msgs, ensure_ascii=False)
    assert _SPIRALS[0] not in json.dumps(last_msgs, ensure_ascii=False)
    assert [m for m in last_msgs if m["role"] == "user" and m["content"].startswith('{"error_code"')]


@pytest.mark.asyncio
async def test_the_exported_quality_report_counts_the_truncations_the_run_recorded(_engine_factory, tmp_path) -> None:
    """两次独立唤醒共三次截断：导出质量报告 quality.json(v11) 与事件逐项一致。"""
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
    assert quality["schema_version"] == 11


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
