"""WorldMock 修订合同的 AS2 vendor 钩子测试。

覆盖：观察单写、决策前决策自述钩子（默认关闭与启用）、reasoning_content
回传、空记忆列表、记忆失败传播、步尾不调用模型（已退役的巩固阈值不再触发）与相似合并。
"""

import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentsociety2.agent.base import AgentBase, ReactDecision, ReactLoopRequest, ReactToolResult
from agentsociety2.agent.memory import AgentMemoryStore
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig, PersonMemoryRuntime
from agentsociety2.agent.person import PersonAgent
from custom.agents.life_agent import LifeAgent
from worldmock.agents.memory.life_memory import WorldMockMemoryRuntime
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, bigram_jaccard
from agentsociety2.agent.person_prompt import build_react_messages
from agentsociety2.society import DecisionContext


class _DummyAgent(AgentBase):
    """提供钩子单元测试所需的最小人物。"""

    @classmethod
    def create(cls, workspace_path: Path, profile: dict, config: dict) -> None:
        raise NotImplementedError

    async def to_workspace(self, workspace_path: Path) -> None:
        raise NotImplementedError

    async def ask(self, message: str, readonly: bool = True, *, t=None) -> str:
        raise NotImplementedError

    async def step(self, tick: int, t: datetime) -> str:
        raise NotImplementedError


def _response(*calls: tuple[str, str], content: str = "", reasoning: str = "") -> Any:
    tool_calls = [
        SimpleNamespace(
            id=f"call-{index}",
            type="function",
            function=SimpleNamespace(name=name, arguments=args),
        )
        for index, (name, args) in enumerate(calls, start=1)
    ]
    message = SimpleNamespace(tool_calls=tool_calls, content=content)
    if reasoning:
        message.reasoning_content = reasoning

    def model_dump(*, mode: str) -> dict[str, Any]:
        serialized = [
            {
                "id": item.id,
                "type": item.type,
                "function": {
                    "name": item.function.name,
                    "arguments": item.function.arguments,
                },
            }
            for item in tool_calls
        ]
        payload: dict[str, Any] = {
            "choices": [{"message": {"content": content, "tool_calls": serialized}}]
        }
        if reasoning:
            payload["choices"][0]["message"]["reasoning_content"] = reasoning
        return payload

    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model_dump=model_dump,
    )


def _text_response(content: str) -> Any:
    return _response(content=content)


class _SequenceDispatcher:
    """按顺序返回模型响应或抛出明确错误。"""

    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.requests: list[dict[str, Any]] = []

    async def call(self, **request: Any) -> Any:
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _EvidenceEnvironment:
    """保存模型调用证据。"""

    def __init__(self) -> None:
        self.evidence: list[Any] = []

    async def record_model_exchange(self, exchange: Any) -> None:
        self.evidence.append(exchange)


def _configured_agent(
    items: list[Any],
    *,
    max_turns: int = 3,
    readonly: bool = True,
) -> tuple[_DummyAgent, _SequenceDispatcher, _EvidenceEnvironment]:
    agent = _DummyAgent()
    dispatcher = _SequenceDispatcher(items)
    environment = _EvidenceEnvironment()
    agent._model_name = "model"
    agent._dispatcher = dispatcher
    agent._env = environment
    agent._decision_context = DecisionContext(
        "run", "round", "decision", 1, datetime(2026, 1, 5, tzinfo=timezone.utc), 0
    )
    agent._max_react_turns = max_turns
    agent.build_react_messages = lambda request: [{"role": "user", "content": "act"}]
    agent.build_react_tool_schemas = lambda **kwargs: [
        {"type": "function", "function": {"name": "noop", "parameters": {}}},
        {"type": "function", "function": {"name": "finish", "parameters": {}}},
    ]
    agent.trace_span = lambda *args, **kwargs: nullcontext()  # type: ignore[method-assign]
    agent._readonly = readonly
    return agent, dispatcher, environment


def _request(*, readonly: bool = True) -> ReactLoopRequest:
    return ReactLoopRequest(
        1,
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        [],
        readonly=readonly,
    )


# ----------------------------------------------------------------------
# 观察单写
# ----------------------------------------------------------------------


def test_structured_observation_appears_once_in_user_message() -> None:
    observation = {
        "scene": "kitchen-sink-a1",
        "items": [{"name": "pan", "count": 1}],
    }
    record = LifeAgent._observation_record(observation)

    assert "observation" not in record
    assert record["data"] == observation

    messages = build_react_messages(
        name="Alice",
        world_description="",
        skill_catalog=[],
        activated_skill_content="",
        observations=[record],
        agent_json={"id": 1, "name": "Alice"},
    )
    user_content = messages[1]["content"]
    assert user_content.count("kitchen-sink-a1") == 1


def test_string_observation_keeps_single_text_copy() -> None:
    record = LifeAgent._observation_record("plain observation")

    assert record["observation"] == "plain observation"
    assert "data" not in record


# ----------------------------------------------------------------------
# 决策前决策自述钩子
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prelude_decision_statement_disabled_by_default() -> None:
    agent, dispatcher, environment = _configured_agent([
        _response(("finish", '{"answer":"done"}')),
    ])

    assert await agent.run_react_loop(_request()) == "done"
    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert {item.attempt for item in environment.evidence} == {1}


@pytest.mark.asyncio
async def test_prelude_decision_statement_hook_runs_one_toolless_call() -> None:
    agent, dispatcher, environment = _configured_agent([
        _text_response("我现在有点饿，想先弄点吃的。"),
        _response(("finish", '{"answer":"acted"}')),
    ])
    received: list[str] = []

    def decision_statement_request(request: ReactLoopRequest) -> dict[str, Any]:
        del request
        return {"model": "model", "messages": [{"role": "user", "content": "决策自述"}]}

    agent.prelude_decision_statement_request = decision_statement_request  # type: ignore[method-assign]
    agent.receive_prelude_decision_statement = (  # type: ignore[method-assign]
        lambda request, decision_statement: received.append(decision_statement)
    )

    assert await agent.run_react_loop(_request()) == "acted"
    assert received == ["我现在有点饿，想先弄点吃的。"]
    assert "tools" not in dispatcher.requests[0]
    assert dispatcher.requests[0]["messages"][0]["content"] == "决策自述"
    assert [item.phase for item in environment.evidence] == [
        "request",
        "response",
        "request",
        "response",
    ]
    assert environment.evidence[0].attempt == 0
    assert environment.evidence[0].call_id == environment.evidence[1].call_id


@pytest.mark.asyncio
async def test_prelude_decision_statement_rejects_tools_and_empty_content() -> None:
    agent, dispatcher, _ = _configured_agent([])
    agent.prelude_decision_statement_request = lambda request: {  # type: ignore[method-assign, misc]
        "model": "model",
        "messages": [],
        "tools": [{"type": "function"}],
    }
    with pytest.raises(RuntimeError, match="must not expose tools"):
        await agent.run_react_loop(_request())
    assert dispatcher.requests == []

    agent2, _, _ = _configured_agent([_text_response("   ")])
    agent2.prelude_decision_statement_request = lambda request: {  # type: ignore[method-assign, misc]
        "model": "model",
        "messages": [],
    }
    with pytest.raises(RuntimeError, match="empty content"):
        await agent2.run_react_loop(_request())


@pytest.mark.asyncio
async def test_prelude_decision_statement_transport_failure_propagates() -> None:
    agent, dispatcher, environment = _configured_agent([TimeoutError("boom")])
    agent.prelude_decision_statement_request = lambda request: {  # type: ignore[method-assign, misc]
        "model": "model",
        "messages": [],
    }
    with pytest.raises(TimeoutError, match="boom"):
        await agent.run_react_loop(_request())
    assert len(dispatcher.requests) == 1
    assert [item.phase for item in environment.evidence] == ["request", "failure"]


# ----------------------------------------------------------------------
# reasoning_content 回传
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_content_survives_into_followup_request() -> None:
    agent, dispatcher, _ = _configured_agent([
        _response(("noop", "{}"), reasoning="chain-of-thought-1"),
        _response(("finish", '{"answer":"done"}')),
    ])

    async def execute(decision, *, readonly=False):
        return ReactToolResult(True, "ok", {})

    agent._execute_react_tool = execute  # type: ignore[method-assign]

    assert await agent.run_react_loop(_request()) == "done"
    followup = dispatcher.requests[1]["messages"]
    assistant = next(item for item in followup if item["role"] == "assistant")
    assert assistant["reasoning_content"] == "chain-of-thought-1"
    assert assistant["tool_calls"][0]["function"]["name"] == "noop"


@pytest.mark.asyncio
async def test_reasoning_content_survives_protocol_feedback_request() -> None:
    agent, dispatcher, _ = _configured_agent([
        _response(("invented_tool", "{}"), reasoning="chain-of-thought-2"),
        _response(("finish", '{"answer":"fixed"}')),
    ])

    assert await agent.run_react_loop(_request()) == "fixed"
    followup = dispatcher.requests[1]["messages"]
    assistant = next(item for item in followup if item["role"] == "assistant")
    assert assistant["reasoning_content"] == "chain-of-thought-2"


def test_assistant_message_without_reasoning_omits_key() -> None:
    message = SimpleNamespace(content="text", tool_calls=[])
    assistant = _DummyAgent._assistant_tool_message(message, [])

    assert "reasoning_content" not in assistant


# ----------------------------------------------------------------------
# 空记忆列表合法
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_finish_with_empty_memories_is_legal() -> None:
    agent, dispatcher, _ = _configured_agent([
        _response(("finish", '{"memories": []}')),
    ])
    agent.build_react_tool_schemas = lambda **kwargs: [
        {"type": "function", "function": {"name": "finish", "parameters": {}}}
    ]

    result = await agent.run_react_loop(_request(readonly=False))

    assert result == ""
    assert agent._last_finish_memories == []


def test_person_finish_schema_allows_empty_memories() -> None:
    overrides = PersonAgent()._react_tool_overrides(readonly=False)
    memories = overrides["finish"]["function"]["parameters"]["properties"]["memories"]

    assert "minItems" not in memories
    assert memories["maxItems"] == 8
    assert "empty list" in memories["description"]


# ----------------------------------------------------------------------
# 记忆运行时：失败传播与空列表
# ----------------------------------------------------------------------


def _framework_runtime(tmp_path: Path) -> tuple[PersonMemoryRuntime, AgentMemoryStore]:
    async def noop_dispatch(**request: Any) -> Any:
        return _text_response("# Memory\n\n## 关于我\n- 暂无记录。\n")

    store = AgentMemoryStore(tmp_path, agent_id=1)
    store.ensure()
    runtime = PersonMemoryRuntime(
        agent_id=1,
        agent_name="Alice",
        config=MemoryRuntimeConfig(),
        get_model_name=lambda: "m",
        dispatch_llm=noop_dispatch,
        get_profile=lambda: {"name": "Alice", "language": "zh-CN"},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    runtime._store = store
    return runtime, store


def _runtime(tmp_path: Path):
    from tests.integration.runtime.current_contract_fixtures import profile as current_profile

    profile = current_profile()
    owner = SimpleNamespace(
        id=1, name="Alice", _model_name="m",
        get_profile=lambda: profile, _logger=SimpleNamespace(warning=lambda *a: None), trace_span=None,
        _persona_card_text=lambda: json.dumps(profile, ensure_ascii=False),
        _life_verified_experiences=[], _life_observation_event_id="observation-1")
    runtime = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    return runtime, runtime.ensure_store(tmp_path)


def _memory(text: str, *, type_: str = "goal", importance: float = 0.7) -> dict:
    return {"type": type_, "importance": importance, "text": text,
            "epistemic_status": "subjective", "source": "activity_conclusion"}


_T0 = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_after_step_accepts_empty_memory_list(tmp_path: Path) -> None:
    runtime, store = _framework_runtime(tmp_path)

    written = await runtime.after_step(
        tick=60,
        t=_T0,
        step_count=1,
        finish_memories=[],
    )

    assert written.created_episodes == ()
    assert written.merged_episodes == ()
    assert written.consolidated is False
    assert store.iter_episodes() == []


def test_validate_finish_memories_bounds(tmp_path) -> None:
    runtime, _ = _framework_runtime(tmp_path)
    assert runtime._validate_finish_memories(None) == []
    assert runtime._validate_finish_memories([]) == []
    with pytest.raises(ValueError):
        runtime._validate_finish_memories("not-a-list")
    with pytest.raises(Exception):
        runtime._validate_finish_memories([_memory("x" * 100)] * 9)


@pytest.mark.asyncio
async def test_memory_validation_failure_propagates(tmp_path: Path) -> None:
    runtime, _ = _framework_runtime(tmp_path)

    with pytest.raises(Exception, match="memories"):
        await runtime.after_step(
            tick=60,
            t=_T0,
            step_count=1,
            finish_memories=[{"type": "bogus", "importance": 0.5, "text": "bad"}],
        )


@pytest.mark.asyncio
async def test_memory_save_failure_propagates(tmp_path: Path) -> None:
    runtime, store = _framework_runtime(tmp_path)

    def explode(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise OSError("disk unavailable")

    store.append_episodes = explode  # type: ignore[method-assign]

    with pytest.raises(OSError, match="disk unavailable"):
        await runtime.after_step(
            tick=60,
            t=_T0,
            step_count=1,
            finish_memories=[_memory("保存失败样例")],
        )


@pytest.mark.asyncio
async def test_silent_zero_persist_is_a_save_failure(tmp_path: Path) -> None:
    runtime, store = _framework_runtime(tmp_path)

    def silent_drop(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    store.append_episodes = silent_drop  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="did not persist finish memories"):
        await runtime.after_step(
            tick=60,
            t=_T0,
            step_count=1,
            finish_memories=[_memory("被静默丢弃的记忆")],
        )


# ----------------------------------------------------------------------
# 主观条目的类型
# ----------------------------------------------------------------------


def _trigger_store(tmp_path: Path) -> WorldMockMemoryStore:
    store = WorldMockMemoryStore(tmp_path, agent_id=1)
    store.ensure()
    return store


def test_host_rejects_objective_event_and_observation_memories(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    for kind in ("event", "observation"):
        with pytest.raises(ValueError, match="subjective"):
            runtime._validate_subjective_memories([_memory("普通的一天", type_=kind)])


# ----------------------------------------------------------------------
# bigram 合并
# ----------------------------------------------------------------------


def test_bigram_jaccard_bounds() -> None:
    assert bigram_jaccard("同一句话", "同一句话") == 1.0
    assert bigram_jaccard("完全不同", "毫无关系") == 0.0


def test_similar_same_type_memory_merges_within_window(tmp_path: Path) -> None:
    store = _trigger_store(tmp_path)
    first = store.append_episodes(
        [_memory("我在楼下的面馆吃了一碗牛肉面，味道很好")],
        tick=60,
        t=_T0,
        step_count=1,
    )

    second = store.append_episodes(
        [_memory("我在楼下的面馆吃了一碗牛肉面，味道不错")],
        tick=60,
        t=_T0 + timedelta(hours=1),
        step_count=2,
    )

    records = store.iter_episodes()
    assert first and second == [records[0]]
    assert len(records) == 1
    assert records[0]["merge_count"] == 2
    assert records[0]["time"] == (_T0 + timedelta(hours=1)).isoformat()
    assert records[0]["text"] == "我在楼下的面馆吃了一碗牛肉面，味道很好"
    state = store.read_state()
    assert state["episode_counter"] == len(records)
    store.validate_existing()


def test_dissimilar_memory_is_kept_separate(tmp_path: Path) -> None:
    store = _trigger_store(tmp_path)
    store.append_episodes(
        [_memory("今天下午开了两小时项目评审会")],
        tick=60,
        t=_T0,
        step_count=1,
    )
    store.append_episodes(
        [_memory("晚上去超市买了牛奶和鸡蛋")],
        tick=60,
        t=_T0 + timedelta(hours=1),
        step_count=2,
    )

    assert len(store.iter_episodes()) == 2


def test_similar_memory_outside_window_or_type_is_kept(tmp_path: Path) -> None:
    store = _trigger_store(tmp_path)
    text = "我在楼下的面馆吃了一碗牛肉面，味道很好"
    store.append_episodes([_memory(text)], tick=60, t=_T0, step_count=1)
    store.append_episodes(
        [_memory(text)],
        tick=60,
        t=_T0 + timedelta(hours=25),
        step_count=2,
    )
    store.append_episodes(
        [_memory(text, type_="observation")],
        tick=60,
        t=_T0 + timedelta(hours=26),
        step_count=3,
    )

    assert len(store.iter_episodes()) == 3


# ----------------------------------------------------------------------
# 步尾的读取与模型入口
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_end_reads_memory_without_copying_it(tmp_path, monkeypatch):
    runtime, store = _runtime(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("copying memory read")

    monkeypatch.setattr(AgentMemoryStore, "iter_episodes", boom)
    monkeypatch.setattr(AgentMemoryStore, "unconsolidated", boom)
    outcome = await runtime.after_step(tick=60, t=_T0, step_count=1,
        finish_memories=[_memory("我喜欢午后的阳光", importance=0.2), _memory("明天给朋友打电话", importance=0.2)])
    assert {item["id"] for item in outcome.created_episodes} == store.episode_ids()
    assert len(outcome.created_episodes) == 2
    assert outcome.consolidated is False


@pytest.mark.asyncio
async def test_step_end_past_the_retired_thresholds_calls_no_model(tmp_path) -> None:
    """两次步尾各写入 6 条互不相似、重要度 0.7 的收尾记忆（合计 12 条、重要度 8.4，超过已退役的十条与重要度三两道
    阈值）：步尾不巩固、不调用模型（模型入口一旦被调用就会抛错），主观摘要始终为空。"""
    runtime, store = _runtime(tmp_path)
    texts = ("早上跑了五公里", "给妈妈回了电话", "把书架整理好了", "午饭吃了一碗面", "下午读完一章小说", "晚上去超市买菜",
             "周末想去爬山", "答应同事周五交初稿", "楼下新开了花店", "学会了做蛋饼", "阳台的花开了", "地铁上听了播客")
    for step, chunk in enumerate((texts[:6], texts[6:]), start=1):
        outcome = await runtime.after_step(tick=60 * step, t=_T0 + timedelta(minutes=step), step_count=step,
                                           finish_memories=[_memory(text, importance=0.7) for text in chunk])
        assert outcome.consolidated is False and len(outcome.created_episodes) == 6
    assert len(store.iter_episodes()) == 12
    assert store.read_subjective_summary() == ""


@pytest.mark.asyncio
async def test_memory_runtime_refuses_every_model_call(tmp_path, caplog) -> None:
    """记忆运行时的模型入口只会失败：先记一条英文错误日志，再抛 RuntimeError。"""
    runtime, _ = _runtime(tmp_path)
    with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="^WorldMock memory runtime does not call the model$"):
        await runtime._dispatch_llm(model="m", messages=[])
    assert [record.getMessage() for record in caplog.records if record.levelname == "ERROR"] == [
        "WorldMock memory runtime does not call the model"]
