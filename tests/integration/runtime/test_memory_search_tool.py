"""search_memory 与九工具清单测试（docs/design/06-agent.md「提示与工具」「检索记忆」）。

生活决策开放九个原生工具（6 核心世界 + search_world + 2 只读查询）；search_memory 经沙盒及 LifeEngine.search_memory
在冻结事件上界内只读检索，单次唤醒最多三次，第 4 次由宿主直接返回 MEMORY_SEARCH_LIMIT；失败明细路径绑定实际下标；
已退役的三个 memory_* 工具及 life_continuation 模块彻底删除。
"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.agents.life_agent as life_agent  # noqa: E402
from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, person, resolved_binding  # noqa: E402
from tests.life.conftest import MAIN_TOOL, advance_until_ready, main_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.state.interaction.errors import HOST_ERROR_CODES  # noqa: E402
from worldmock.state.interaction.requests import SearchMemoryRequest  # noqa: E402

#: 生活唤醒全量十一个原生工具名及工具列表顺序（与 life_agent._FULL_MODELS 一致）。
_REQUEST_ORDER = [
    "start_main_activity", "start_side_activity", "search_world", "continue_activity",
    "recall_task_summary", "report_status", "pause_activity", "conclude_activity",
    "revise_day_plan", "search_memory", "lookup_targets",
]
_TODAY = {"category": "全部", "time": "今天", "keyword": None}
_LIMIT = {
    "error_code": "MEMORY_SEARCH_LIMIT",
    "reason": "This decision has already searched memory three times.",
    "fix": "本次决策已查过三次记忆，请依据现有信息决定",
    "items": [],
    "unmet": [],
}
_REST = main_arguments("interact", "家", "测试休息")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _Reply:
    """带思考的一次响应，含一个或多个原生工具调用。"""

    def __init__(self, *calls: tuple[str, str, dict]) -> None:
        tool_calls = [{"id": cid, "type": "function",
                       "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                      for cid, name, args in calls]
        self.document = {"choices": [{"message": {"content": "", "reasoning_content": "我先查一查。",
                                                  "tool_calls": tool_calls}}]}
        message = SimpleNamespace(content="", reasoning_content="我先查一查。", tool_calls=[
            SimpleNamespace(id=item["id"], type="function", function=SimpleNamespace(**item["function"]))
            for item in tool_calls])
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode):
        assert mode == "json"
        return copy.deepcopy(self.document)


def _search(call_id: str, arguments: dict | None = None) -> _Reply:
    return _Reply((call_id, "search_memory", arguments or _TODAY))


def _rest(call_id: str = "call-rest") -> _Reply:
    return _Reply((call_id, MAIN_TOOL, _REST))


def _recording(engine) -> tuple:
    """在 open_sandbox 上包一层记录沙盒每次 search_memory 的调用与返回。"""
    calls: list = []
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sb = real_open(wakeup)
        real_search = sb.search_memory

        def search_memory(req):
            ans = real_search(req)
            calls.append(("search_memory", req, ans))
            return ans

        sb.search_memory = search_memory
        return sb

    engine.open_sandbox = wrapped_open
    return engine, calls


async def _decide(engine, env, responses: list) -> SimpleNamespace:
    [wakeup] = engine.prepare_barrier((1,))
    sb = env.open_sandbox(wakeup)
    model = ScriptedModel(responses)
    binding = resolved_binding(model, frozen=engine.context.inputs, max_turns=max(len(responses) + 2, 16))
    outcome = await person(1, engine.context.inputs).decide_wakeup(wakeup, sb, binding)
    env.commit_barrier([outcome])
    return SimpleNamespace(requests=model.requests, outcome=outcome, wakeup=wakeup)


def _tools(request: dict) -> list[str]:
    return [item["function"]["name"] for item in request["tools"]]


def _tool_messages(request: dict) -> list[dict]:
    return [json.loads(item["content"]) for item in request["messages"] if item["role"] == "tool"]


async def test_life_tool_surface_has_nine_tools_in_order(engine_factory):
    """每次常规回合请求严格暴露九个工具并保持全量顺序。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, _ = _recording(engine)
    run = await _decide(engine, env, [_rest()])
    assert list(life_agent._FULL_MODELS) == _REQUEST_ORDER
    assert [_tools(req) for req in run.requests] == [_REQUEST_ORDER]
    assert life_agent.life_query_tool_names() == {"recall_task_summary", "search_memory", "lookup_targets"}
    assert life_agent.life_action_tool_names() == set(_REQUEST_ORDER) - {"search_memory", "lookup_targets"}


async def test_search_memory_goes_through_sandbox_and_leaves_only_model_events(engine_factory):
    """先检索再开始休息：沙盒收到真实 SearchMemoryRequest，随后仅在观察与 action_requested 间记录模型证据。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    before = engine.context.store.last_event_seq
    run = await _decide(engine, env, [_search("call-search"), _rest()])
    [(tool, request, answer)] = calls
    assert tool == "search_memory" and request == SearchMemoryRequest(**_TODAY)
    msg = run.requests[1]["messages"][-1]
    assert msg["tool_call_id"] == "call-search"
    assert msg["content"] == json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    store = engine.context.store
    added = [e.event_type for e in store.iter_events(store.last_event_seq) if e.event_seq > before]
    start, end = added.index("observation"), added.index("action_requested")
    assert added[start + 1:end] == ["model_request", "model_response", "model_request", "model_response"]


async def test_fourth_search_in_a_wakeup_is_answered_with_memory_search_limit(engine_factory):
    """分四个回合各检索一次：沙盒仅收三次，第 4 次由宿主直接答 MEMORY_SEARCH_LIMIT；下一唤醒清零。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    run = await _decide(engine, env, [*(_search(f"call-{i}") for i in range(4)), _rest()])
    assert len(calls) == 3
    assert _tool_messages(run.requests[4])[3] == _LIMIT
    assert len({json.dumps(req["tools"]) for req in run.requests}) == 1
    assert advance_until_ready(engine)
    await _decide(engine, env, [_search("call-again"), _Reply(("call-end", "conclude_activity", {
        "items": [{"activity_name": next(iter(engine.context.state.activities.values())).name, "summary": "歇好了。",
                   "memories": [], "views": []}], "errands": []})), _rest("c-next")])
    assert len(calls) == 4


async def test_four_searches_in_one_reply_get_three_answers_and_one_limit(engine_factory):
    """同批四个检索前三个到沙盒，第四个得 MEMORY_SEARCH_LIMIT。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    run = await _decide(engine, env, [_Reply(*((f"call-{i}", "search_memory", _TODAY) for i in range(4))), _rest()])
    messages = _tool_messages(run.requests[1])
    assert len(calls) == 3 and messages[:3] == [ans for _, _, ans in calls]
    assert messages[3] == _LIMIT


async def test_environment_rejected_searches_count_toward_the_limit(engine_factory):
    """沙盒拒绝的前三次非法时间检索照样计数，第四次合法检索得 MEMORY_SEARCH_LIMIT。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    invalid = {"category": "饮食", "time": "2026-02-30", "keyword": None}
    run = await _decide(engine, env, [*(_search(f"call-{i}", invalid) for i in range(3)),
                                      _search("call-valid"), _rest()])
    assert [ans["error_code"] for _, _, ans in calls] == ["MEMORY_TIME_INVALID"] * 3
    assert _tool_messages(run.requests[4])[3] == _LIMIT


def test_sandbox_itself_rejects_fourth_search_past_limit(engine_factory):
    """直接经 AgentSandbox 连调第 4 次 search_memory：沙盒自身返回 MEMORY_SEARCH_LIMIT 拒信。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    for _ in range(3):
        assert "error_code" not in sb.search_memory(SearchMemoryRequest(**_TODAY))
    fourth = sb.search_memory(SearchMemoryRequest(**_TODAY))
    assert fourth["error_code"] == _LIMIT["error_code"]


async def test_argument_errors_do_not_count_as_searches(engine_factory):
    """参数结构校验失败不消费检索次数，随后的三次合法检索均可执行。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    wrong = {"category": "饮食", "time": "今天", "keyword": "早饭 午饭 晚饭 夜宵 零食"}
    run = await _decide(engine, env, [*(_search(f"call-bad-{i}", wrong) for i in range(3)),
                                      *(_search(f"call-{i}") for i in range(3)), _rest()])
    assert len(calls) == 3
    assert all("error_code" not in msg for msg in _tool_messages(run.requests[6])[-3:])


async def test_rejected_search_paths_point_to_the_real_call(engine_factory):
    """同批第二个检索时间非法：明细路径改写为 ["tool_calls", 1, ...]。"""
    engine = engine_factory()
    stage_day_plan(engine)
    env, calls = _recording(engine)
    run = await _decide(engine, env, [_Reply(
        ("call-ok", "search_memory", _TODAY),
        ("call-bad", "search_memory", {"category": "饮食", "time": "2026-02-30", "keyword": None})), _rest()])
    refused = _tool_messages(run.requests[1])[1]
    assert refused["error_code"] == "MEMORY_TIME_INVALID"
    assert refused["items"][0]["path"] == ["tool_calls", 1, "function", "arguments", "time"]
    assert ["error_code" not in ans for _, _, ans in calls] == [True, False]


def test_search_memory_cannot_be_dispatched_as_world_tool(engine_factory):
    """试图把 search_memory 当世界工具经 sandbox.dispatch_world_tool 提交抛出 ProtocolError。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    with pytest.raises(ProtocolError, match="^Unsupported sandbox world tool: search_memory$"):
        sb.dispatch_world_tool("search_memory", SearchMemoryRequest(**_TODAY), f"{wakeup.wakeup_id}:action:0001")


def test_retired_memory_tools_and_continuation_module_are_gone():
    """旧三个 memory_* 工具、life_continuation 模块及 MEMORY_QUERY_FAILED 均已清理删除。"""
    import importlib.util

    for name in ("MemoryRecentRequest", "MemorySearchRequest", "MemoryTimeRangeRequest", "_parse_memory_time",
                 "_MEMORY_TOOL_MODELS", "_MEMORY_RUNTIME_ACTIONS", "_MEMORY_RESULT_FIELDS"):
        assert not hasattr(life_agent, name)
    assert importlib.util.find_spec("worldmock.agents.protocol.life_continuation") is None
    assert "MEMORY_QUERY_FAILED" not in HOST_ERROR_CODES
