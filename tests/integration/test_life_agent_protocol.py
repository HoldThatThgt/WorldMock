"""LifeAgent 闭集 Schema、宿主身份注入与原生 ReAct 链测试。"""

from __future__ import annotations

import os
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.agent.memory import AgentMemoryStore
from agentsociety2.agent.runner import _step_agent_batch_async
import agentsociety2.society.society as society_module
from agentsociety2.society import (
    AgentSociety,
    AgentSocietyConfig,
    DecisionContext,
    DirectToolCall,
    SchedulingState,
)
from custom.agents.life_agent import (
    LifeAgent,
    life_prompt_sha256,
    life_tool_schema_sha256,
)
from worldmock.life.models import ActionReceipt, REQUEST_MODELS, StartBasicActivityRequest


_MEMORY_TOOL_NAMES = {
    "memory_recent",
    "memory_search",
    "memory_step_range",
    "memory_tick_range",
    "memory_time_range",
    "memory_read",
}


def _context() -> DecisionContext:
    """构造稳定测试决策上下文。"""
    return DecisionContext(
        "run", "round", "decision", 7, datetime(2026, 1, 5, tzinfo=timezone.utc), 3
    )


def _finish_args(ref: str = "observation-current") -> dict:
    """构造合法正式结束记忆。"""
    return {
        "memories": [
            {
                "type": "observation",
                "importance": 0.5,
                "keywords": ["wait"],
                "text": "I chose to wait for one minute.",
                "source": "decision_observation",
                "refs": [ref],
            }
        ]
    }


def _observations(*result_ids: str) -> list[dict]:
    """构造含人物可见来源事件的真实结构化观察记录。"""
    data = {
        "observation_event_id": "observation-current",
        "recent_action_results": [{"event_id": item} for item in result_ids],
        "tasks": [{"task_id": "task-visible"}],
        "content_catalog": [{"item_id": "content-visible"}],
        "game_catalog": [{"game_id": "game-visible"}],
        "messages": [{"message_id": "message-visible"}],
        "browse_targets": ["product_catalog"],
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def test_life_agent_schema_is_closed_and_host_fields_are_hidden() -> None:
    """正式动作前 Schema 仅含十个业务和六个记忆工具。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    assert set(functions) == set(REQUEST_MODELS) | _MEMORY_TOOL_NAMES
    assert "finish" not in functions
    start_fields = functions["start_basic_activity"]["parameters"]["properties"]
    assert {"agent_id", "decision_id", "action_id"}.isdisjoint(start_fields)
    assert functions["start_basic_activity"]["parameters"]["additionalProperties"] is False
    assert all(function["strict"] is True for function in functions.values())
    assert len(life_prompt_sha256()) == 64
    assert life_tool_schema_sha256() == life_tool_schema_sha256()


def test_action_schema_requires_prepared_environment_catalogs() -> None:
    """非只读动作工具在首观察目录冻结前必须明确失败。"""
    with pytest.raises(RuntimeError, match="visible target catalogs are not prepared"):
        LifeAgent().build_react_tool_schemas(readonly=False)


def test_readonly_schema_uses_its_independent_finish_contract() -> None:
    """只读问答始终提供记忆查询和无需生活动作的结束工具。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, [], "How was today?", True)
    agent.prepare_react_loop(request)

    functions = {
        item["function"]["name"]: item["function"]
        for item in agent.build_react_tool_schemas(readonly=True)
    }

    assert set(functions) == _MEMORY_TOOL_NAMES | {"finish"}
    assert set(REQUEST_MODELS).isdisjoint(functions)
    assert set(functions["finish"]["parameters"]["properties"]) == {"answer"}


def test_life_agent_rejects_invalid_or_non_atomic_decisions() -> None:
    """参数错误、多动作和先结束都在执行前失败。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)
    valid = ReactDecision("", "start_basic_activity", {
        "activity_type": "wait", "planned_duration_seconds": 60,
    }, "")

    assert "multiple" in agent.validate_react_decisions([valid, valid], False)
    memory = ReactDecision("", "memory_recent", {"limit": 1}, "")
    assert "only tool" in agent.validate_react_decisions([memory, valid], False)
    invalid = ReactDecision("", "start_basic_activity", {
        "activity_type": "wait", "planned_duration_seconds": 0,
    }, "")
    assert "invalid" in agent.validate_react_decisions([invalid], False)
    finish = ReactDecision("", "finish", _finish_args(), "")
    assert "submitted life action" in agent.validate_react_decisions([finish], False)


def test_finish_accepts_only_current_visible_environment_refs() -> None:
    """结束记忆可引用本次观察及其中的上轮结果，拒绝任意事件身份。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )
    agent._life_action_count = 1

    visible = ReactDecision("", "finish", _finish_args("result-prior"), "")
    unknown = ReactDecision("", "finish", _finish_args("unseen-event"), "")
    mixed = _finish_args()
    mixed["memories"][0]["refs"].append("decision:action:0001")
    missing = _finish_args()
    missing["memories"][0]["refs"] = []

    assert agent.validate_react_decisions([visible], False) == ""
    assert "unknown memory reference" in agent.validate_react_decisions([unknown], False)
    assert "unknown memory reference" in agent.validate_react_decisions(
        [ReactDecision("", "finish", mixed, "")], False
    )
    assert "invalid finish" in agent.validate_react_decisions(
        [ReactDecision("", "finish", missing, "")], False
    )


def test_finish_schema_contains_only_sources_shown_this_decision() -> None:
    """动态 Schema 包含初始记忆和环境来源，排除未展示的持久记忆。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._memory_runtime = SimpleNamespace(
        store=SimpleNamespace(read_ids=lambda _ids: [{"id": "m_hidden"}])
    )
    agent._build_memory_context = lambda: {  # type: ignore[method-assign]
        "memory_md": "# Memory",
        "recent_episodes": [{"id": "m_initial", "text": "Visible memory."}],
    }
    request = ReactLoopRequest(
        0,
        _context().observed_at,
        _observations("result-prior"),
    )
    agent.prepare_react_loop(request)

    agent.build_react_messages(request)
    agent._life_action_count = 1
    finish = next(
        item
        for item in agent.build_react_tool_schemas(readonly=False)
        if item["function"]["name"] == "finish"
    )
    refs = finish["function"]["parameters"]["properties"]["memories"]["items"]
    allowed = refs["properties"]["refs"]["items"]["enum"]

    assert allowed == ["m_initial", "observation-current", "result-prior"]
    assert "m_hidden" not in allowed
    hidden = ReactDecision("", "finish", _finish_args("m_hidden"), "")
    assert "unknown memory reference" in agent.validate_react_decisions(
        [hidden], False
    )


def test_life_prompt_does_not_advertise_hidden_person_tools() -> None:
    """LifeAgent 正文与发送 Schema 使用同一闭集。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    request = ReactLoopRequest(0, _context().observed_at, [{"action": "observe"}])

    messages = agent.build_react_messages(request)
    text = "\n".join(item["content"] for item in messages)

    assert "activate_skill" not in text
    assert "execute_skill_script" not in text
    assert "ask_env" not in text
    assert "workspace_write" not in text
    assert "exactly one life action" in text
    assert "Every ref in every memory" in text
    assert "Never use action_id" in text


class _ModelResponse:
    """提供原生工具属性及可持久化响应。"""

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(id=call_id, function=function)
        message = SimpleNamespace(tool_calls=[tool_call], content="")
        self.choices = [SimpleNamespace(message=message)]
        self._call_id = call_id
        self._name = name
        self._arguments = arguments

    def model_dump(self, *, mode: str) -> dict:
        """返回最小 JSON 模型响应。"""
        assert mode == "json"
        return {"choices": [{"message": {"tool_calls": [{
            "id": self._call_id,
            "type": "function",
            "function": {"name": self._name, "arguments": self._arguments},
        }]}}]}


class _Dispatcher:
    """按顺序返回生活动作、动作后记忆读取和结束调用。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse(
                "call-action",
                "start_basic_activity",
                '{"activity_type":"wait","planned_duration_seconds":60}',
            ),
            _ModelResponse(
                "call-memory", "memory_recent", '{"limit":1}'
            ),
            _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
        ]

    async def call(self, **request):
        """返回下一个原生工具响应。"""
        self.requests.append(request)
        assert request["tool_choice"] == "required"
        assert request["max_retries"] == 0
        assert "reasoning_effort" not in request
        assert request["extra_body"] == {"thinking": {"type": "disabled"}}
        assert all(item["function"]["strict"] is True for item in request["tools"])
        return self.responses.pop(0)


class _MemoryRuntime:
    """返回一个固定历史记录并关闭步骤后记忆写入。"""

    store = None

    def build_context(self):
        """返回稳定长期记忆提示上下文。"""
        return {"memory_md": "# Memory", "recent_episodes": []}

    def dispatch_tool(self, action, args):
        """返回模型可见的实际记忆工具结果。"""
        assert action == "memory_recent"
        assert args == {"limit": 1}
        data = {"episodes": [{"id": "m_prior", "text": "I waited yesterday."}]}
        return True, json.dumps(data), data


class _Environment:
    """捕获 LifeAgent 注入的直接调用和模型证据。"""

    def __init__(self) -> None:
        self.calls: list[DirectToolCall] = []
        self.evidence = []
        self.memory_updates = []

    async def submit_action(self, call: DirectToolCall) -> ActionReceipt:
        """返回与宿主身份一致的提交收据。"""
        self.calls.append(call)
        return ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256="a" * 64,
        )

    async def record_model_exchange(self, exchange) -> None:
        """保存模型请求与响应证据。"""
        self.evidence.append(exchange)

    async def record_memory_update(self, evidence) -> None:
        """保存人物记忆文件内容身份。"""
        self.memory_updates.append(evidence)


class _StepDispatcher:
    """返回一个生活动作和原生 finish。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse(
                "call-action",
                "start_basic_activity",
                '{"activity_type":"wait","planned_duration_seconds":60}',
            ),
            _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
        ]

    async def call(self, **request):
        """返回下一个严格工具响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


class _PrematureFinishDispatcher:
    """在人物提交生活动作前注入未开放的 finish。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []

    async def call(self, **request):
        """返回协议非法的提前结束调用。"""
        self.requests.append(request)
        return _ModelResponse("call-finish", "finish", json.dumps(_finish_args()))


class _StepEnvironment(_Environment):
    """提供实际人物步骤与 Society 故障边界所需环境合同。"""

    def __init__(self) -> None:
        super().__init__()
        self.current_time = _context().observed_at
        self.version = _context().world_state_version
        self.ready = (7,)
        self.rounds = []
        self.commits = []
        self.aborts = []
        self.failures = []
        self.checkpoints = []

    async def observe(self, context: DecisionContext) -> dict:
        """返回带稳定来源事件身份的当前人物观察。"""
        assert context.agent_id == 7
        return {
            "observation_event_id": "observation-current",
            "recent_action_results": [],
            "tasks": [{"task_id": "task-visible"}],
            "content_catalog": [{"item_id": "content-visible"}],
            "game_catalog": [{"game_id": "game-visible"}],
            "messages": [{"message_id": "message-visible"}],
            "browse_targets": ["product_catalog"],
        }

    async def scheduling_state(self) -> SchedulingState:
        """返回当前共同决策边界。"""
        return SchedulingState(
            self.current_time,
            self.ready,
            self.current_time + timedelta(seconds=60),
            self.version,
        )

    async def begin_decision_round(self, contexts) -> str:
        """记录 Society 冻结的决策顺序。"""
        self.rounds.append(contexts)
        return contexts[0].round_id

    async def commit_decision_round(self, round_id: str) -> list[dict]:
        """记录不应在记忆保存失败后发生的结算。"""
        self.commits.append(round_id)
        return [{"agent_id": 7, "status": "completed"}]

    async def abort_decision_round(self, round_id: str, error: str) -> None:
        """保存失败轮身份与错误证据。"""
        self.aborts.append((round_id, error))

    async def fail_run(self, error: str) -> None:
        """保存 Society 传播的运行失败证据。"""
        self.failures.append(error)

    async def checkpoint(self, request) -> None:
        """记录不应在失败轮后创建的共同检查点。"""
        self.checkpoints.append(request)


def _create_step_workspace(workspace: Path) -> None:
    """创建启用严格记忆且关闭技能待办的人物工作区。"""
    LifeAgent.create(
        workspace,
        {"id": 7, "name": "Alice", "occupation": "engineer"},
        {
            "enable_memory": True,
            "enable_todo_list": False,
            "max_react_turns": 2,
            "memory_consolidation_pending_threshold": 100,
            "memory_consolidation_interval_steps": 100,
        },
    )


def _service(environment: _StepEnvironment, dispatcher: _StepDispatcher):
    """构造测试使用的最小共享服务容器。"""
    return SimpleNamespace(
        env=environment,
        llm=SimpleNamespace(default=dispatcher),
        trace=None,
    )


@pytest.mark.asyncio
async def test_native_react_submits_one_host_bound_life_action() -> None:
    """原生 ReAct 完成动作、收据观察与后续结束调用。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _Dispatcher()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice", "occupation": "engineer"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 3
    agent._memory_runtime = _MemoryRuntime()
    agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    span = SimpleNamespace(attributes={})
    agent.trace_span = lambda *args, **kwargs: nullcontext(span)  # type: ignore[method-assign]

    summary = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )

    assert "wait" in summary
    assert len(environment.calls) == 1
    call = environment.calls[0]
    assert call.context == _context()
    assert call.action_id == "decision:action:0001"
    assert isinstance(call.request, StartBasicActivityRequest)
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response", "request", "response",
    ]
    first, second, third = dispatcher.requests
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert [item["role"] for item in second["messages"]] == [
        "system", "user", "assistant", "tool",
    ]
    assert [item["role"] for item in third["messages"]] == [
        "system", "user", "assistant", "tool", "assistant", "tool",
    ]
    assert second["messages"][-1]["tool_call_id"] == "call-action"
    assert third["messages"][-1]["tool_call_id"] == "call-memory"
    assert "decision:action:0001" in second["messages"][-1]["content"]
    assert "m_prior" in third["messages"][-1]["content"]
    assert second["messages"][:2] == first["messages"]
    assert third["messages"][:2] == first["messages"]
    assert "observation-current" in first["messages"][1]["content"]
    first_names = {item["function"]["name"] for item in first["tools"]}
    second_names = {item["function"]["name"] for item in second["tools"]}
    third_names = {item["function"]["name"] for item in third["tools"]}
    assert first_names == set(REQUEST_MODELS) | _MEMORY_TOOL_NAMES
    assert "finish" not in first_names
    targeted = next(
        item["function"]["parameters"]["properties"]
        for item in first["tools"]
        if item["function"]["name"] == "start_targeted_activity"
    )
    assert targeted["activity_type"]["enum"] == [
        "work", "watch", "play_game", "read_mail", "browse",
    ]
    assert targeted["target_id"]["enum"] == [
        "content-visible", "game-visible", "message-visible", "product_catalog", "task-visible",
    ]
    assert second_names == third_names == _MEMORY_TOOL_NAMES | {"finish"}
    assert set(REQUEST_MODELS).isdisjoint(second_names | third_names)
    finish = next(
        item["function"]["parameters"]
        for item in third["tools"]
        if item["function"]["name"] == "finish"
    )
    refs = finish["properties"]["memories"]["items"]["properties"]["refs"]
    assert refs["items"]["enum"] == [
        "m_prior", "observation-current", "result-prior",
    ]


def test_life_actions_are_removed_after_one_submission() -> None:
    """一次主动作提交后仅保留记忆查询和 finish。"""
    agent = LifeAgent()
    agent._life_action_count = 1

    names = {
        item["function"]["name"]
        for item in agent.build_react_tool_schemas(readonly=False)
    }

    assert names == _MEMORY_TOOL_NAMES | {"finish"}
    assert set(REQUEST_MODELS).isdisjoint(names)


@pytest.mark.asyncio
async def test_native_react_rejects_finish_before_life_action() -> None:
    """原生请求不开放 finish，模型非法注入时在执行前终止。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _PrematureFinishDispatcher()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    agent._decision_context = _context()
    agent._max_react_turns = 1
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    with pytest.raises(RuntimeError, match="unknown native tool calls.*finish"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations())
        )

    names = {
        item["function"]["name"]
        for item in dispatcher.requests[0]["tools"]
    }
    assert names == set(REQUEST_MODELS) | _MEMORY_TOOL_NAMES
    assert environment.calls == []
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert "unknown native tool calls" in environment.evidence[-1].error


@pytest.mark.asyncio
async def test_native_memory_update_hashes_real_workspace_files(tmp_path) -> None:
    """真实记忆文件落盘后通过 PersonAgent 原生路径登记内容身份。"""
    store = AgentMemoryStore(tmp_path, agent_id=7)
    written = store.append_episodes(
        _finish_args()["memories"],
        tick=60,
        t=_context().observed_at,
        step_count=1,
    )
    agent = LifeAgent()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    agent._step_count = 1
    agent._memory_runtime = SimpleNamespace(store=store)

    await agent._record_memory_update(written)

    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    assert evidence.context == _context()
    assert evidence.episode_ids == (written[0]["id"],)
    assert evidence.consolidated is False
    for digest in (
        evidence.memory_sha256,
        evidence.episodes_sha256,
        evidence.state_sha256,
    ):
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


@pytest.mark.asyncio
async def test_person_step_records_native_finish_memory_update(tmp_path) -> None:
    """真实人物步骤把原生 finish 写盘并登记同一工作区身份。"""
    workspace = tmp_path / "agent_0007"
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    _create_step_workspace(workspace)
    agent = await LifeAgent.from_workspace(
        workspace,
        _service(environment, dispatcher),
    )
    agent.bind_decision_context(_context())

    summary = await agent.step(0, _context().observed_at)

    assert summary == "I chose to wait for one minute."
    assert len(environment.calls) == 1
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response",
    ]
    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    episodes = [
        json.loads(line)
        for line in (workspace / "memory" / "episodes.jsonl").read_text().splitlines()
    ]
    assert evidence.context == _context()
    assert evidence.episode_ids == (episodes[0]["id"],)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["exception", "empty"])
async def test_memory_persistence_failure_aborts_society_before_commit(
    monkeypatch,
    tmp_path,
    failure_mode,
) -> None:
    """finish 后记忆未落盘时 runner 失败且 Society 保存故障并中止。"""
    workspace = tmp_path / "as2" / "agents" / "agent_0007"
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    service = _service(environment, dispatcher)
    _create_step_workspace(workspace)
    marker = "injected append failure"
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("WORKSPACE_PATH", str(project_root))

    def fail_append(*_args, **_kwargs):
        if failure_mode == "exception":
            raise OSError(marker)
        return []

    monkeypatch.setattr(AgentMemoryStore, "append_episodes", fail_append)

    def run_actual_batch(request, proxy):
        async def done():
            return {
                "results": await _step_agent_batch_async(request, proxy),
                "token_stats": {},
            }

        return done()

    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(run_actual_batch)})(),
    )
    config = AgentSocietyConfig(
        run_id="run",
        agent_specs=({"id": 7, "profile": {}, "config": {}},),
        agent_class_name="LifeAgent",
        env_router=environment,
        service_proxy=service,
        start_time=environment.current_time,
        end_time=environment.current_time + timedelta(hours=1),
        run_dir=tmp_path,
        custom_root=project_root,
        workspace_mode="restored",
    )
    society = AgentSociety(config)
    society._initialized = True
    society._environment_started = True

    with pytest.raises(RuntimeError, match="Agent decision failed"):
        await society.run()

    expected = marker if failure_mode == "exception" else "did not persist finish memories"
    assert len(environment.calls) == 1
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response",
    ]
    assert environment.memory_updates == []
    assert environment.commits == []
    assert environment.checkpoints == []
    assert len(environment.aborts) == 1
    assert expected in environment.aborts[0][1]
    assert len(environment.failures) == 1
    assert expected in environment.failures[0]
