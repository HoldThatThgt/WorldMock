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
from worldmock.life.models import (
    ActionReceipt,
    REQUEST_MODELS,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)


_MEMORY_TOOL_NAMES = {
    "memory_recent",
    "memory_search",
    "memory_step_range",
    "memory_tick_range",
    "memory_time_range",
    "memory_read",
}

_BASE_ACTION_NAMES = {
    "browse_activities",
    "start_basic_activity",
    "start_targeted_activity",
    "start_travel",
    "purchase",
    "consume",
    "submit_work",
}

_BUDGET_MARKER = "\n\nAuthoritative ReAct model-turn budget:\n"


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


def _request_budget(request: dict) -> dict:
    """读取实际模型请求携带的权威回合预算。"""
    system = request["messages"][0]["content"]
    return json.loads(system.split(_BUDGET_MARKER, 1)[1])


def _observations(*result_ids: str) -> list[dict]:
    """构造含人物可见来源事件的真实结构化观察记录。"""
    data = {
        "observation_event_id": "observation-current",
        "recent_action_results": [{"event_id": item} for item in result_ids],
        "active_activity": None,
        "paused_activities": [],
        "energy_feasibility": {"maximum_duration_seconds_by_kind": {
            "work": 86_400, "watch": 86_400, "play_game": 86_400,
            "read_mail": 86_400, "browse": 86_400,
        }},
        "tasks": [{"task_id": "task-visible", "remaining_contract_seconds": 3600,
                   "material": {"end_char": 10, "total_chars": 10}}],
        "activity_summary": {"feature_film": 1, "sport": 44, "foods": 1},
        "content_progress": {"content-visible": {
            "item_id": "content-visible", "position_seconds": 0.0,
            "remaining_media_seconds": 630.0, "exposure_count": 0,
        }},
        "game_catalog": [{"game_id": "game-visible"}],
        "messages": [{"message_id": "message-visible"}],
        "browse_targets": ["product_catalog"],
        "products": [{"product_id": "product-visible", "price_version": "price-v1",
                      "current_stock": 2, "use_modes": ["eat"], "use_duration_seconds": 300,
                      "merchant_location_id": "home", "required_location_id": None}],
        "locations": [{"location_id": "home"}, {"location_id": "office"}],
        "location_id": "home",
        "available_inventory": {"product-visible": 1.0},
        "routes": [{"destination_id": "office", "mode": "driving", "duration_seconds": 216,
                    "minimum_energy_cost_kcal": 6.0, "energy_feasible": True}],
        "games": {},
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def test_life_agent_schema_is_closed_and_host_fields_are_hidden() -> None:
    """正式动作前 Schema 只含本轮可构造业务动作和六个记忆工具。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    assert set(functions) == _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES
    assert "finish" not in functions
    start_fields = functions["start_basic_activity"]["parameters"]["properties"]
    assert {"agent_id", "decision_id", "action_id"}.isdisjoint(start_fields)
    assert functions["start_basic_activity"]["parameters"]["additionalProperties"] is False
    assert all(function["strict"] is True for function in functions.values())
    assert len(life_prompt_sha256()) == 64
    assert life_tool_schema_sha256() == life_tool_schema_sha256()


def test_action_schema_requires_prepared_environment_catalogs() -> None:
    """非只读动作工具在首观察目录冻结前必须明确失败。"""
    with pytest.raises(RuntimeError, match="activity controls are not prepared"):
        LifeAgent().build_react_tool_schemas(readonly=False)


def test_feedback_boundary_exposes_direct_switch_tools() -> None:
    """活动反馈边界除继续与暂停外，同时暴露直接切换的启动工具。"""
    agent = LifeAgent()
    observation = dict(_observations()[0]["data"])
    observation["active_activity"] = {
        "activity_id": "activity-current", "kind": "wait", "status": "active"}
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at,
                         [{"turn": 0, "action": "observe", "ok": True,
                           "data": observation}])
    )
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    assert set(functions) == (
        {"continue_activity", "set_activity_state",
         "start_basic_activity", "start_targeted_activity", "start_travel"}
        | _MEMORY_TOOL_NAMES
    )
    state = functions["set_activity_state"]["parameters"]["properties"]
    assert state["activity_id"]["enum"] == ["activity-current"]
    assert state["operation"]["enum"] == ["pause", "cancel"]
    assert functions["continue_activity"]["parameters"]["properties"]["activity_id"][
        "enum"] == ["activity-current"]

    switch = ReactDecision("", "start_targeted_activity", {
        "activity_type": "watch", "target_id": "content-visible",
        "planned_duration_seconds": 300, "playback_speed": 1,
    }, "")
    assert agent.validate_react_decisions([switch], False) == ""
    blocked = ReactDecision("", "purchase", {
        "product_id": "product-visible", "price_version": "price-v1",
        "quantity": 1, "fulfillment": "delivery", "destination_id": "home",
    }, "")
    assert "is not available in the current activity state" in agent.validate_react_decisions(
        [blocked], False)


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
    assert "unknown LifeAgent tool: finish" == agent.validate_react_decisions([finish], False)


def test_finish_accepts_only_current_visible_environment_refs() -> None:
    """结束记忆可引用本次观察及其中的上轮结果，拒绝任意事件身份。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )
    agent._life_action_count = 1
    agent.build_react_tool_schemas(readonly=False)

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

    def __init__(self, call_id: str, name: str, arguments: str,
                 reasoning_content: str | None = None) -> None:
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(id=call_id, type="function", function=function)
        message = SimpleNamespace(
            tool_calls=[tool_call], content="", reasoning_content=reasoning_content)
        self.choices = [SimpleNamespace(message=message)]
        self._call_id = call_id
        self._name = name
        self._arguments = arguments
        self._reasoning_content = reasoning_content

    def model_dump(self, *, mode: str) -> dict:
        """返回最小 JSON 模型响应（reasoning_content 恒在，缺省为 None）。"""
        assert mode == "json"
        return {"choices": [{"message": {"tool_calls": [{
            "id": self._call_id,
            "type": "function",
            "function": {"name": self._name, "arguments": self._arguments},
        }], "reasoning_content": self._reasoning_content}}]}


class _MultipleModelResponse:
    """提供同一模型响应中的多个原生工具调用。"""

    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        self._calls = calls
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                type="function",
                function=SimpleNamespace(name=name, arguments=arguments),
            )
            for call_id, name, arguments in calls
        ]
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(tool_calls=tool_calls, content="")
        )]

    def model_dump(self, *, mode: str) -> dict:
        """返回含多个工具调用的最小 JSON 模型响应。"""
        assert mode == "json"
        tool_calls = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in self._calls
        ]
        return {"choices": [{"message": {"tool_calls": tool_calls}}]}


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
                reasoning_content="Tired person needs a short rest before work.",
            ),
            _ModelResponse(
                "call-memory", "memory_recent", '{"limit":1}'
            ),
            _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
        ]

    async def call(self, **request):
        """返回下一个原生工具响应。"""
        self.requests.append(request)
        assert request["tool_choice"] == "auto"
        assert request["max_retries"] == 0
        assert "reasoning_effort" not in request
        assert request["extra_body"] == {"thinking": {"type": "enabled"}}
        assert all(item["function"]["strict"] is True for item in request["tools"])
        return self.responses.pop(0)


class _MemoryRuntime:
    """返回一个固定历史记录并关闭步骤后记忆写入。"""

    store = None

    def __init__(self) -> None:
        self.calls = []

    def build_context(self):
        """返回稳定长期记忆提示上下文。"""
        return {"memory_md": "# Memory", "recent_episodes": []}

    def dispatch_tool(self, action, args):
        """返回模型可见的实际记忆工具结果。"""
        assert action == "memory_recent"
        assert args == {"limit": 1}
        self.calls.append((action, args))
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


class _GroundedCorrectionDispatcher:
    """复现记忆查询后捏造旧游戏工具并自行纠正的响应序列。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list[_ModelResponse] | None = None) -> None:
        self.requests = []
        defaults = [
            _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
            _ModelResponse(
                "call-invalid",
                "play_game",
                '{"game_id":"game-visible","planned_duration_seconds":600}',
            ),
            _ModelResponse(
                "call-action",
                "start_targeted_activity",
                json.dumps({
                    "activity_type": "play_game",
                    "target_id": "game-visible",
                    "planned_duration_seconds": 600,
                    "playback_speed": 1,
                }),
            ),
            _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
        ]
        self.responses = list(responses) if responses is not None else defaults

    async def call(self, **request):
        """记录真实 wire 并返回下一个协议响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


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
        return dict(_observations()[0]["data"])

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
    assert second["messages"][1] == first["messages"][1]
    assert third["messages"][1] == first["messages"][1]
    assert "observation-current" in first["messages"][1]["content"]
    first_names = {item["function"]["name"] for item in first["tools"]}
    second_names = {item["function"]["name"] for item in second["tools"]}
    third_names = {item["function"]["name"] for item in third["tools"]}
    assert first_names == _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES
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
    assert second_names == _MEMORY_TOOL_NAMES | {"finish"}
    assert third_names == {"finish"}
    assert set(REQUEST_MODELS).isdisjoint(second_names | third_names)
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in dispatcher.requests] == [3, 2, 1]
    assert [_request_budget(item)["phase"] for item in dispatcher.requests] == [
        "before_action", "after_action", "after_action"]
    finish = next(
        item["function"]["parameters"]
        for item in third["tools"]
        if item["function"]["name"] == "finish"
    )
    refs = finish["properties"]["memories"]["items"]["properties"]["refs"]
    assert refs["items"]["enum"] == [
        "m_prior", "observation-current", "result-prior",
    ]


@pytest.mark.asyncio
async def test_reasoning_content_is_persisted_in_model_response_evidence() -> None:
    """思考开启后模型推理随现有序列化进入 model_response 足迹证据。"""
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

    await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )

    requests = [item for item in environment.evidence if item.phase == "request"]
    responses = [item for item in environment.evidence if item.phase == "response"]
    # 请求证据钉死思考开启与 DeepSeek thinking 兼容的 tool_choice。
    for item in requests:
        assert item.request["extra_body"] == {"thinking": {"type": "enabled"}}
        assert item.request["tool_choice"] == "auto"
    # 正例：带 reasoning_content 的响应按原样进入足迹事件 data.response。
    first_message = responses[0].response["choices"][0]["message"]
    assert first_message["reasoning_content"] == (
        "Tired person needs a short rest before work."
    )
    assert first_message["tool_calls"][0]["function"]["name"] == "start_basic_activity"
    # 反例：不带推理的响应序列化后 reasoning_content 为 None，不伪造内容。
    assert responses[1].response["choices"][0]["message"]["reasoning_content"] is None


@pytest.mark.asyncio
async def test_budgeted_memory_search_preserves_action_and_finish_turns() -> None:
    """有限记忆读取后保留一个自主生活动作和一个独立结束回合。"""
    dispatcher = _GroundedCorrectionDispatcher([
        _ModelResponse("call-memory-1", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-memory-2", "memory_recent", '{"limit":1}'),
        _ModelResponse(
            "call-action",
            "start_basic_activity",
            '{"activity_type":"wait","planned_duration_seconds":60}',
        ),
        _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 4
    agent._memory_runtime = _MemoryRuntime()
    agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert "wait" in result
    assert len(environment.calls) == 1
    names = [
        {item["function"]["name"] for item in request["tools"]}
        for request in dispatcher.requests
    ]
    assert names[0] == names[1] == _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES
    assert names[2] == _BASE_ACTION_NAMES
    assert names[3] == {"finish"}
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in dispatcher.requests] == [4, 3, 2, 1]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0003", "0004"]


@pytest.mark.asyncio
async def test_multiple_memory_calls_share_one_model_turn() -> None:
    """同一响应的多个记忆调用只消耗一个模型回合。"""
    dispatcher = _GroundedCorrectionDispatcher([
        _MultipleModelResponse([
            ("call-memory-1", "memory_recent", '{"limit":1}'),
            ("call-memory-2", "memory_recent", '{"limit":1}'),
        ]),
        _ModelResponse(
            "call-action",
            "start_basic_activity",
            '{"activity_type":"wait","planned_duration_seconds":60}',
        ),
        _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 3
    memory_runtime = _MemoryRuntime()
    agent._memory_runtime = memory_runtime
    agent._build_memory_context = memory_runtime.build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert "wait" in result
    assert len(environment.calls) == 1
    assert len(memory_runtime.calls) == 2
    names = [
        {item["function"]["name"] for item in request["tools"]}
        for request in dispatcher.requests
    ]
    assert names == [
        _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES,
        _BASE_ACTION_NAMES,
        {"finish"},
    ]
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in dispatcher.requests] == [3, 2, 1]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0003"]


@pytest.mark.asyncio
async def test_reserved_action_boundary_rejects_memory_without_extra_request() -> None:
    """只剩动作与结束回合时非法记忆调用零执行并在下次请求前失败。"""
    dispatcher = _GroundedCorrectionDispatcher([
        _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 2
    agent._memory_runtime = _MemoryRuntime()
    agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    with pytest.raises(RuntimeError, match="requires 2 model turns but only 1 remain"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations())
        )

    assert len(dispatcher.requests) == 1
    assert {item["function"]["name"] for item in dispatcher.requests[0]["tools"]} == _BASE_ACTION_NAMES
    assert environment.calls == []
    assert agent._memory_runtime.calls == []
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert "unknown native tool calls: ['memory_recent']" == environment.evidence[-1].error


@pytest.mark.asyncio
async def test_readonly_budget_reserves_finish_and_resets_between_loops() -> None:
    """只读循环末回合仅开放结束，并在下一次循环重置实际调用序号。"""
    dispatcher = _GroundedCorrectionDispatcher([
        _ModelResponse("call-memory-1", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish-1", "finish", '{"answer":"first"}'),
        _ModelResponse("call-memory-2", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish-2", "finish", '{"answer":"second"}'),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 2
    agent._memory_runtime = _MemoryRuntime()
    agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )
    request = ReactLoopRequest(0, _context().observed_at, [], "What happened?", True)

    assert await agent.run_react_loop(request) == "first"
    assert await agent.run_react_loop(request) == "second"

    names = [
        {item["function"]["name"] for item in current["tools"]}
        for current in dispatcher.requests
    ]
    assert names == [
        _MEMORY_TOOL_NAMES | {"finish"}, {"finish"},
        _MEMORY_TOOL_NAMES | {"finish"}, {"finish"},
    ]
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in dispatcher.requests] == [2, 1, 2, 1]
    assert [_request_budget(item)["phase"] for item in dispatcher.requests] == ["readonly"] * 4
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0001", "0002"]


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
async def test_native_react_rejects_impossible_one_turn_life_decision() -> None:
    """动作前不足动作加结束两个回合时不发送注定失败的请求。"""
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

    with pytest.raises(RuntimeError, match="requires 2 model turns but only 1 remain"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations())
        )

    assert dispatcher.requests == []
    assert environment.calls == []
    assert environment.evidence == []


@pytest.mark.asyncio
async def test_grounded_unknown_game_tool_is_corrected_without_alias() -> None:
    """真实失败形状在同一有限循环内改用当前目标工具且仅提交一次。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._decision_context = _context()
    agent._max_react_turns = 4
    agent._memory_runtime = _MemoryRuntime()
    agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert "wait" in result
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartTargetedActivityRequest)
    assert environment.calls[0].request.activity_type == "play_game"
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response",
        "request", "response", "request", "response",
    ]
    assert "unknown native tool calls" in environment.evidence[3].error
    correction = dispatcher.requests[2]
    assert correction["messages"][-1]["tool_call_id"] == "call-invalid"
    feedback = json.loads(correction["messages"][-1]["content"])
    assert feedback["executed"] is False
    assert correction["messages"][-2]["tool_calls"][0]["function"]["name"] == "play_game"
    third_names = {item["function"]["name"] for item in correction["tools"]}
    assert "start_targeted_activity" in third_names
    assert "play_game" not in third_names
    fourth_names = {item["function"]["name"] for item in dispatcher.requests[3]["tools"]}
    assert fourth_names == {"finish"}


def test_readonly_state_mismatch_is_an_internal_failure() -> None:
    """宿主只读状态不一致必须抛出，不能作为模型可纠正错误。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)

    with pytest.raises(RuntimeError, match="readonly decision state is inconsistent"):
        agent.validate_react_decisions([], True)


@pytest.mark.asyncio
async def test_life_schema_and_target_errors_are_corrected_before_execution() -> None:
    """Pydantic参数与目标配对错误各耗一回合且只有最终合法动作执行。"""
    arguments = {
        "activity_type": "play_game",
        "target_id": "game-visible",
        "planned_duration_seconds": 600,
        "playback_speed": 1,
    }
    missing = dict(arguments)
    missing.pop("playback_speed")
    wrong_pair = {**arguments, "activity_type": "watch"}
    dispatcher = _GroundedCorrectionDispatcher([
        _ModelResponse("call-schema", "start_targeted_activity", json.dumps(missing)),
        _ModelResponse("call-pair", "start_targeted_activity", json.dumps(wrong_pair)),
        _ModelResponse("call-action", "start_targeted_activity", json.dumps(arguments)),
        _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._decision_context = _context()
    agent._max_react_turns = 4
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))

    assert len(environment.calls) == 1
    assert environment.calls[0].request.activity_type == "play_game"
    assert "invalid start_targeted_activity arguments" in environment.evidence[1].error
    assert "not visible for activity_type" in environment.evidence[3].error
    assert all(json.loads(request["messages"][-1]["content"])["executed"] is False
               for request in dispatcher.requests[1:3])


@pytest.mark.asyncio
async def test_invalid_finish_is_corrected_with_post_action_tools_only() -> None:
    """动作完成后非法记忆引用可纠正且后续请求不重新开放生活动作。"""
    invalid_finish = _finish_args("unknown-event")
    dispatcher = _GroundedCorrectionDispatcher([
        _ModelResponse(
            "call-action",
            "start_basic_activity",
            '{"activity_type":"wait","planned_duration_seconds":60}',
        ),
        _ModelResponse("call-invalid-finish", "finish", json.dumps(invalid_finish)),
        _ModelResponse("call-finish", "finish", json.dumps(_finish_args())),
    ])
    environment = _Environment()
    agent = LifeAgent()
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._decision_context = _context()
    agent._max_react_turns = 3
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )

    await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))

    assert len(environment.calls) == 1
    assert "unknown memory reference" in environment.evidence[3].error
    second_names = {item["function"]["name"] for item in dispatcher.requests[1]["tools"]}
    third_names = {item["function"]["name"] for item in dispatcher.requests[2]["tools"]}
    assert second_names == _MEMORY_TOOL_NAMES | {"finish"}
    assert third_names == {"finish"}
    feedback = json.loads(dispatcher.requests[2]["messages"][-1]["content"])
    assert "unknown memory reference" in feedback["error"]


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
