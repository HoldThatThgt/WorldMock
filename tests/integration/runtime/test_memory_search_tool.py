"""search_memory 的工具面与宿主派发（docs/design/06-agent.md「提示与工具」「检索记忆」）：生活决策八个原生工具、只读问答只开放
finish；检索经 LifeEnv.read_query 当场回答，不写模型证据以外的事件；每次决策至多派发三次，第 4 次起宿主当场回答
MEMORY_SEARCH_LIMIT；参数错误不计数、环境拒绝照样计数；失败结果的明细路径改写到实际调用位置；检索不能当世界请求提交，它的
tool 消息不是续接消息；三个 memory_* 工具已删除。

真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent，只替换外部模型传输。
"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402
import custom.agents.life_agent as life_agent  # noqa: E402
from tests.engine_fixture import engine_factory  # noqa: E402,F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, environment, person  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import _prepared  # noqa: E402
from tests.life.conftest import MAIN_TOOL, advance_until_ready, context, main_arguments  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.protocol.life_continuation import _earlier_changes  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.state.interaction.errors import HOST_ERROR_CODES  # noqa: E402
from worldmock.state.interaction.requests import SearchMemoryRequest  # noqa: E402

#: 生活决策的八个原生工具，按工具指纹的次序；以及每次模型请求的 tools 次序。
_FINGERPRINT_ORDER = ["start_main_activity", "start_side_activity", "recall_task_summary", "report_status",
                      "pause_activity", "conclude_activity", "revise_day_plan", "search_memory"]
_REQUEST_ORDER = ["start_main_activity", "start_side_activity", "report_status", "pause_activity",
                  "conclude_activity", "revise_day_plan", "recall_task_summary", "search_memory"]
_TODAY = {"category": "全部", "time": "今天", "keyword": None}
_LIMIT = {"error_code": "MEMORY_SEARCH_LIMIT", "reason": "This decision has already searched memory three times.",
          "fix": "本次决策已查过三次记忆，请依据现有信息决定", "items": [], "unmet": []}
_REST = main_arguments("interact", "家", "测试休息")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _Reply:
    """一次带思考的回复，含一个或几个原生工具调用（调用为（身份，工具名，参数对象））。"""

    def __init__(self, *calls: tuple[str, str, dict]) -> None:
        tool_calls = [{"id": call_id, "type": "function",
                       "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}
                      for call_id, name, arguments in calls]
        self.document = {"choices": [{"message": {"content": "", "reasoning_content": "我先查一查。",
                                                  "tool_calls": tool_calls}}]}
        message = SimpleNamespace(content="", reasoning_content="我先查一查。", tool_calls=[
            SimpleNamespace(id=item["id"], type="function", function=SimpleNamespace(**item["function"]))
            for item in tool_calls])
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode):
        """提供商形状的回复。"""
        assert mode == "json"
        return copy.deepcopy(self.document)


def _search(call_id: str, arguments: dict | None = None) -> _Reply:
    """只有一次检索的回复。"""
    return _Reply((call_id, "search_memory", arguments or _TODAY))


def _rest(call_id: str = "call-rest") -> _Reply:
    """开始测试休息的回复（世界请求登记即结束本次决策）。"""
    return _Reply((call_id, MAIN_TOOL, _REST))


def _recording(engine) -> tuple:
    """真实 LifeEnv，包一层记录 read_query 的每次调用与回答。"""
    env, calls = environment(engine), []
    original = env.read_query

    async def read_query(decision, tool, request):
        """记录后转交真实 LifeEnv。"""
        answer = await original(decision, tool, request)
        calls.append((tool, request, answer))
        return answer

    env.read_query = read_query
    return env, calls


async def _decide(engine, env, label: str, responses: list, readonly: bool = False) -> SimpleNamespace:
    """开一轮、走一次真实 ReAct 循环；生活决策随后结算。返回模型请求、循环结果与观察记录。"""
    frozen = context(engine, label)
    engine.begin_decision_round([frozen])
    model = ScriptedModel(responses)
    agent = person(env, frozen, model)
    if readonly:
        request = ReactLoopRequest(0, frozen.observed_at, [], "今天过得怎么样？", True)
    else:
        observed = await env.observe(frozen)
        request = ReactLoopRequest(0, frozen.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                            "data": observed}])
    result = await agent.run_react_loop(request)
    if not readonly:
        engine.commit_decision_round(label)
    return SimpleNamespace(requests=model.requests, result=result, observations=request.observations, agent=agent)


def _tools(request: dict) -> list[str]:
    """一次模型请求开放的工具名，按 tools 的次序。"""
    return [item["function"]["name"] for item in request["tools"]]


def _tool_messages(request: dict) -> list[dict]:
    """一次模型请求消息历史里全部 tool 消息的正文。"""
    return [json.loads(item["content"]) for item in request["messages"] if item["role"] == "tool"]


async def test_life_tool_surface_has_eight_tools_in_fingerprint_and_request_order(engine_factory):
    """工具指纹按 REQUEST_MODELS 加 QUERY_MODELS 的次序取八个原生工具；每次请求的 tools 是六个世界请求、回看任务、检索记忆。"""
    engine = engine_factory()
    env, _ = _recording(engine)
    run = await _decide(engine, env, "surface", [_rest()])
    assert list(life_agent._TOOL_MODELS) == _FINGERPRINT_ORDER
    assert [_tools(request) for request in run.requests] == [_REQUEST_ORDER]
    assert life_agent.life_query_tool_names() == {"recall_task_summary", "search_memory"}
    assert life_agent.life_action_tool_names() == set(_FINGERPRINT_ORDER) - {"search_memory"}


async def test_readonly_questions_offer_only_finish(engine_factory):
    """只读问答每个回合只开放 finish；只读回复调用 search_memory 按未开放的工具纠错，环境没有收到调用。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    run = await _decide(engine, env, "readonly", [_search("call-readonly"),
                                                  _Reply(("call-finish", "finish", {"answer": "还不错。"}))],
                        readonly=True)
    assert run.result == "还不错。"
    assert [_tools(request) for request in run.requests] == [["finish"], ["finish"]]
    assert [item["error_code"] for item in _tool_messages(run.requests[1])] == ["TOOL_CALL_REQUIRED"]
    assert calls == []


async def test_search_memory_goes_through_read_query_and_leaves_only_model_events(engine_factory):
    """先检索再开始活动：环境收到 ("search_memory", SearchMemoryRequest)，下一次请求里的 tool 消息就是 LifeEnv.read_query
    的回答；这次观察与 action_requested 之间只有决策开始与模型往来的事件。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    before = engine.context.store.last_event_seq
    run = await _decide(engine, env, "searched", [_search("call-search"), _rest()])
    [(tool, request, answer)] = calls
    assert tool == "search_memory" and request == SearchMemoryRequest(**_TODAY)
    tool_message = run.requests[1]["messages"][-1]
    assert tool_message["tool_call_id"] == "call-search"
    assert tool_message["content"] == json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    store = engine.context.store
    added = [event.event_type for event in store.iter_events(store.last_event_seq) if event.event_seq > before]
    start, end = added.index("observation"), added.index("action_requested")
    assert added[start + 1:end] == ["decision_started", "model_request", "model_response", "model_request",
                                    "model_response"]


async def test_fourth_search_in_a_decision_is_answered_by_the_host(engine_factory):
    """分四个回合各检索一次：环境只收到三次，第 4 条 tool 消息逐字等于 MEMORY_SEARCH_LIMIT 的五字段，工具面不变；下一次
    决策重新计数。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    run = await _decide(engine, env, "four", [*(_search(f"call-{index}") for index in range(4)), _rest()])
    assert len(calls) == 3
    assert _tool_messages(run.requests[4])[3] == _LIMIT
    assert len({json.dumps(request["tools"]) for request in run.requests}) == 1
    assert advance_until_ready(engine)
    await _decide(engine, env, "next", [_search("call-again"), _Reply(("call-end", "conclude_activity", {
        "items": [{"activity_name": next(iter(engine.context.state.activities.values())).name, "summary": "歇好了。",
                   "memories": [], "views": []}], "errands": []}))])
    assert len(calls) == 4


async def test_four_searches_in_one_reply_get_three_answers_and_one_limit(engine_factory):
    """同一次回复里四个检索按先后依次派发：前三个各得环境的回答，第 4 个得 MEMORY_SEARCH_LIMIT。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    run = await _decide(engine, env, "batch", [_Reply(*(
        (f"call-{index}", "search_memory", _TODAY) for index in range(4))), _rest()])
    messages = _tool_messages(run.requests[1])
    assert len(calls) == 3 and messages[:3] == [answer for _, _, answer in calls]
    assert messages[3] == _LIMIT


async def test_environment_rejected_searches_count_toward_the_limit(engine_factory):
    """前三次检索被环境以 MEMORY_TIME_INVALID 拒绝，第 4 次合法的检索得 MEMORY_SEARCH_LIMIT；环境只收到三次。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    invalid = {"category": "饮食", "time": "2026-02-30", "keyword": None}
    run = await _decide(engine, env, "invalid", [*(_search(f"call-{index}", invalid) for index in range(3)),
                                                 _search("call-valid"), _rest()])
    assert [answer["error_code"] for _, _, answer in calls] == ["MEMORY_TIME_INVALID"] * 3
    assert _tool_messages(run.requests[4])[3] == _LIMIT


async def test_search_dispatch_without_an_environment_fails_closed():
    """没有接上环境时合法的检索在派发处明确失败，计数不变；已达上限时同一调用不需要环境，ok 为假的 MEMORY_SEARCH_LIMIT。"""
    agent = _prepared()
    assert agent._env is None and agent._life_memory_searches == 0
    with pytest.raises(RuntimeError, match="^Environment is not initialized$"):
        await agent.dispatch_react_tool("search_memory", _TODAY)
    assert agent._life_memory_searches == 0
    agent._life_memory_searches = life_agent.MEMORY_SEARCHES_PER_DECISION
    result = await agent.dispatch_react_tool("search_memory", _TODAY)
    assert result.ok is False and result.data == _LIMIT and json.loads(result.observation) == _LIMIT


async def test_argument_errors_do_not_count_as_searches(engine_factory):
    """三次参数错误（五个词的关键词）没有派发、不计数；随后三次合法检索都到达环境。"""
    engine = engine_factory()
    env, calls = _recording(engine)
    wrong = {"category": "饮食", "time": "今天", "keyword": "早饭 午饭 晚饭 夜宵 零食"}
    run = await _decide(engine, env, "arguments", [*(_search(f"call-bad-{index}", wrong) for index in range(3)),
                                                   *(_search(f"call-{index}") for index in range(3)), _rest()])
    assert len(calls) == 3
    assert all("error_code" not in message for message in _tool_messages(run.requests[6])[-3:])


async def test_rejected_search_paths_point_to_the_real_call(engine_factory):
    """同一回复里第二个检索写 2026-02-30：它的 tool 消息明细路径改写到 tool_calls[1]，结果 ok 为假。"""
    engine = engine_factory()
    env, _ = _recording(engine)
    run = await _decide(engine, env, "paths", [_Reply(
        ("call-ok", "search_memory", _TODAY),
        ("call-bad", "search_memory", {"category": "饮食", "time": "2026-02-30", "keyword": None})), _rest()])
    refused = _tool_messages(run.requests[1])[1]
    assert refused["error_code"] == "MEMORY_TIME_INVALID"
    assert refused["items"][0]["path"] == ["tool_calls", 1, "function", "arguments", "time"]
    searched = [item for item in run.observations if item["action"] == "search_memory"]
    assert [item["ok"] for item in searched] == [True, False]


def test_search_memory_cannot_be_submitted_as_a_world_action(engine_factory):
    """把 search_memory 当世界请求经 submit_action 送达：请求类型与工具不符的协议错误。"""
    engine = engine_factory()
    frozen = context(engine, "direct")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    with pytest.raises(ProtocolError, match="^Direct tool call request type does not match its tool$"):
        engine.submit_action(DirectToolCall(frozen, f"{frozen.decision_id}:action:0001", "search_memory",
                                            SearchMemoryRequest(**_TODAY)))


async def test_search_tool_messages_are_not_continuation_messages(engine_factory):
    """检索的 tool 消息没有 result 键，续接链把它认作非续接消息。"""
    engine = engine_factory()
    env, _ = _recording(engine)
    run = await _decide(engine, env, "chain", [_search("call-search"), _rest()])
    message = run.requests[1]["messages"][-1]
    assert message["role"] == "tool" and _earlier_changes(message) is None


def test_retired_memory_tools_are_gone():
    """三个 memory_* 工具的请求模型、时间解析、工具表、动作映射、结果字段与派发都已删除；MEMORY_QUERY_FAILED 不再是宿主
    错误码。"""
    for name in ("MemoryRecentRequest", "MemorySearchRequest", "MemoryTimeRangeRequest", "_parse_memory_time",
                 "_MEMORY_TOOL_MODELS", "_MEMORY_RUNTIME_ACTIONS", "_MEMORY_RESULT_FIELDS"):
        assert not hasattr(life_agent, name)
    assert not hasattr(life_agent.LifeAgent, "_dispatch_strict_memory")
    assert "MEMORY_QUERY_FAILED" not in HOST_ERROR_CODES
