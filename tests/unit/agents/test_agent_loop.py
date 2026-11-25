"""@brief 通用 AgentLoop、LifeAgent 单次唤醒执行、search_world 无栈挂起恢复与错误配对纯单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import tomllib

import pytest

from worldmock.agents.life_agent import (
    LifeAgent,
    PersonInputs,
    life_action_tool_names,
    life_query_tool_names,
    life_tool_schema_sha256,
    person_inputs,
    validate_life_batch,
)
from worldmock.agents.prompt.prompt_schemas import BODY_LEVELS, USER_LAYER_NAMES
from worldmock.agents.protocol.agent_loop import (
    AgentLoop,
    LoopCallbacks,
    LoopConfig,
    LoopStepOutcome,
    LoopToolCall,
    build_tool_schema,
    combine_reports,
    dispatch_tool_safely,
    model_call_id,
    parse_tool_calls,
    validate_model_request,
    validate_single_tool_call,
)
from worldmock.agents.protocol.life_truncation import TRUNCATION_CONTENT
from worldmock.configuration.inputs.models import ModelMetadata, ModelProfileConfig, PersonaConfig
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.barrier import AgentWakeupInput, SandboxOutcome, ToolDispatchResult
from worldmock.state.interaction.requests import SearchMemoryRequest
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.state.world.world import WORLD_SCHEMA_VERSION, WorldState

_PERSONA_TOML = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment/personas/persona_1.toml"


def _tool_call(cid: str, name: str, arguments: dict[str, Any] | str, call_type: str = "function") -> SimpleNamespace:
    """@brief 构造 LiteLLM 样式的原生 tool_call 响应项。"""
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=cid, type=call_type, function=SimpleNamespace(name=name, arguments=encoded))


def _reply(calls: list[SimpleNamespace], *, content: str | None = None,
           reasoning: str | None = None, finish_reason: str = "tool_calls") -> SimpleNamespace:
    """@brief 构造可消费的假模型响应对象。"""
    msg = SimpleNamespace(role="assistant", content=content, tool_calls=calls, reasoning_content=reasoning)
    choice = SimpleNamespace(finish_reason=finish_reason, message=msg)
    return SimpleNamespace(model="deepseek-reasoner", choices=[choice],
                           usage={"prompt_tokens": 100, "completion_tokens": 12})


class _ScriptedClient:
    """@brief 按脚本序列返回响应或抛出预定异常的异步模型客户端替身。"""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def call(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _binding(client: Any, *, thinking: bool = True, max_turns: int = 4) -> ResolvedModelBinding:
    """@brief 构造纯内存 ResolvedModelBinding。"""
    profile = ModelProfileConfig(
        provider="openai_compatible", base_url="https://api.example.invalid/v1",
        requested_model="deepseek-reasoner", api_key_env="WORLDMOCK_API_KEY",
        metadata_url="https://api.example.invalid/v1/models", context_window=262144,
        thinking=thinking, max_output_tokens=4096, max_react_turns=max_turns,
    )
    meta = ModelMetadata(
        requested_model="deepseek-reasoner", metadata_url="https://api.example.invalid/v1/models",
        context_window=262144, max_output_tokens=4096,
    )
    return ResolvedModelBinding("action-prof", profile, meta, client, 4096)


def _observation() -> dict[str, Any]:
    """@brief 构造满足七层拼装与校验的最小有效观察载荷。"""
    return {
        "current_time": 1000,
        "observation_event_id": "obs-evt-1",
        "verified_experiences": [],
        "wake_reasons": ["planned_end"],
        "memory_layer": {"layer": "记忆层", "可查": {}, "近三天": {}, "今天": []},
        "todo_layer": {"layer": "待办层", "paused_activities": [], "errands": [], "orders": [], "day_plan": None},
        "tool_layer": {"layer": "工具层", "targets": [], "callable_index": {"goods": [], "prefer_apps": []}},
        "environment_layer": {
            "layer": "环境层", "observed_at": "2026-01-05T08:00:00+08:00",
            "body": {k: v[0] for k, v in BODY_LEVELS.items()},
        },
        "progress_layer": {"layer": "进度层", "session_started_at": "2026-01-05T08:00:00+08:00",
                           "rejected_today": [], "events": []},
        "progress_meta": [],
    }


class _FakeSandbox:
    """@brief 纯内存 SandboxHandle 替身：脚本化调度与记录全部模型交换与调用。"""

    def __init__(self, world_results: list[ToolDispatchResult], *,
                 query_results: dict[str, list[dict[str, Any]]] | None = None,
                 fail_world_on: str | None = None, fail_exchange: bool = False) -> None:
        self._world_results = list(world_results)
        self._queries = {k: list(v) for k, v in (query_results or {}).items()}
        self._fail_world_on = fail_world_on
        self._fail_exchange = fail_exchange
        self.dispatched: list[tuple[str, Any, str, str | None]] = []
        self.exchanges: list[Any] = []
        self.finished: Any = None

    def search_memory(self, request) -> dict[str, Any]:
        return self._queries["search_memory"].pop(0)

    def recall_task_summary(self, request) -> dict[str, Any]:
        return self._queries["recall_task_summary"].pop(0)

    def dispatch_world_tool(self, tool_name, arguments, call_id, note=None) -> ToolDispatchResult:
        if tool_name == self._fail_world_on:
            raise RuntimeError("boom in sandbox")
        self.dispatched.append((tool_name, arguments, call_id, note))
        return self._world_results.pop(0)

    def record_model_exchange(self, exchange) -> None:
        if self._fail_exchange:
            raise OSError("disk full")
        self.exchanges.append(exchange)

    def finish_wakeup(self, suspended) -> SandboxOutcome:
        self.finished = suspended
        status = "suspended" if suspended is not None else "completed"
        agent = AgentState(
            agent_id=1, name="林舟", location_id="home", balance_account_id="agent:1",
            body=BodyState(energy_kcal=1800.0, sleep_pressure=0.2, sleep_deficit_hours=0.0,
                           sleep_need_hours=8.0, chronotype_midsleep=4.0, social_need=0.2,
                           relaxation_need=0.2, social_need_growth_per_hour=0.05),
            body_settled_at=1000, next_decision_at=1000,
        )
        dummy_state = WorldState(
            schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=1000, end_time=10000,
            run_id="run", timezone="UTC", persona_sha256s={1: "a" * 64}, config_sha256="b" * 64,
            agents={1: agent}, accounts={"agent:1": 0, "external:scenario": 0},
            random_seed=1, random_state="frozen",
        )
        return SandboxOutcome(
            wakeup_id="run:wake:1000:v1:a1", agent_id=1, status=status,
            state=dummy_state,
            observation_event_id="obs-evt-1",
        )

    def can_finish_idle(self) -> bool:
        return False


def _persona() -> PersonaConfig:
    """@brief 从夹具解析真实单人 PersonaConfig。"""
    return PersonaConfig.model_validate(tomllib.loads(_PERSONA_TOML.read_text(encoding="utf-8")))


def test_agent_loop_public_helpers_cover_parsing_validation_and_dispatch():
    """@brief 直接测通 model_call_id、build_tool_schema、parse_tool_calls、validate_single_tool_call 等公共函数。"""
    assert model_call_id("wake-1", "model", 2) == "wake-1:model:0002:attempt:1"
    schema = build_tool_schema("search_memory", "查记忆。", SearchMemoryRequest)
    assert schema["function"]["strict"] is True
    models = {"search_memory": SearchMemoryRequest}
    empty_parsed, empty_reports = parse_tool_calls([], models)
    assert empty_parsed == [] and empty_reports[0].error_code == "TOOL_CALL_REQUIRED"
    bad_calls = [
        _tool_call("", "unknown_tool", "{not-json"),
        _tool_call("dup", "search_memory", "123"),
        _tool_call("dup", "search_memory", {"category": "坏值", "time": "今天", "keyword": None}),
    ]
    indexed, reports = parse_tool_calls(bad_calls, models)
    assert len(indexed) == 1 and len(reports) >= 4
    fb_unknown = validate_single_tool_call(LoopToolCall("nope", {}, "c1"), models)
    fb_invalid = validate_single_tool_call(indexed[0][1], models)
    assert fb_unknown is not None and fb_invalid is not None
    assert isinstance(validate_model_request(SearchMemoryRequest, {
        "category": "饮食", "time": "今天", "keyword": None}), SearchMemoryRequest)
    merged = combine_reports([*reports, fb_invalid.report])
    assert merged.report.error_code == "TOOL_CALL_REQUIRED"
    assert dispatch_tool_safely("ok", 0, lambda: 42) == 42
    with pytest.raises(RuntimeError, match="TOOL_SERVICE_FAILED"):
        dispatch_tool_safely("bad", 1, lambda: (_ for _ in ()).throw(ValueError("fail")))


@pytest.mark.asyncio
async def test_life_agent_regular_wakeup_runs_queries_control_limit_truncation_and_completion():
    """@brief 完整覆盖常规唤醒：记忆查询上限、控制动作沙盒回传、思考配对纠错、输出截断关闭思考重试与开始组完成。"""
    inputs = person_inputs(_persona())
    agent = LifeAgent(inputs)
    assert USER_LAYER_NAMES[-1] == "进度层"
    assert "search_world" in life_action_tool_names()
    assert life_query_tool_names() == frozenset({"recall_task_summary", "search_memory"})
    assert len(life_tool_schema_sha256()) == 64

    sandbox = _FakeSandbox(
        world_results=[
            ToolDispatchResult(accepted=True, terminal_exit=False, suspended=False,
                               tool_message_payload={"result": {"ok": True}, "changes": {}, "events": []}),
            ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False,
                               tool_message_payload={"result": {"started": True}}),
            ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False,
                               tool_message_payload={"result": {"started": True}}),
        ],
        query_results={
            "search_memory": [
                {"items": [{"text": "昨天的饭"}]},
                {"items": []},
                {"error_code": "MEMORY_TIME_INVALID", "items": [{"path": ["tool_calls", 0, "time"]}]},
            ],
            "recall_task_summary": [{"summary": "买菜进度"}],
        },
    )
    search_arg = {"category": "饮食", "time": "今天", "keyword": None}
    client = _ScriptedClient([
        _reply([_tool_call("q1", "search_memory", search_arg), _tool_call("q2", "search_memory", search_arg),
                _tool_call("q3", "search_memory", search_arg), _tool_call("q4", "search_memory", search_arg),
                _tool_call("q5", "recall_task_summary", {"task_name": "买菜"})], reasoning="先查记忆"),
        _reply([_tool_call("bad1", "report_status", {"items": [], "errands": []})], reasoning="误提交空控制"),
        _reply([], content=" ", finish_reason="length"),
        _reply([_tool_call("c1", "report_status", {
            "items": [{"activity_name": "休息", "summary": "继续休息。"}],
            "errands": []})]),
        _reply([
            _tool_call("s1", "start_main_activity", {"action": "sleep", "target": "420", "detail": None}),
            _tool_call("s2", "start_side_activity", {"action": "interact", "target": ["微博"], "prefer": None}),
        ], content="开始睡觉并刷会手机"),
    ])
    wakeup = AgentWakeupInput(
        wakeup_id="run:wake:1000:v1:a1", agent_id=1, observed_at=1000, world_state_version=1,
        mode="regular", observation=_observation(), observation_parts={}, pending_presentation={},
        action_seq_start=1,
    )
    outcome = await agent.decide_wakeup(wakeup, sandbox, _binding(client, thinking=True, max_turns=6))
    assert outcome.status == "completed" and sandbox.finished is None
    assert client.requests[3]["tool_choice"] == "required"
    assert client.requests[3]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert len(client.requests[3]["tools"]) == 6
    assert sandbox.dispatched[0][2] == "run:wake:1000:v1:a1:action:0001"
    assert sandbox.dispatched[1][2] == "run:wake:1000:v1:a1:action:0002"
    assert sandbox.dispatched[1][3] == "开始睡觉并刷会手机"
    assert sandbox.dispatched[2][2] == "run:wake:1000:v1:a1:action:0003" and sandbox.dispatched[2][3] is None
    assert "MEMORY_SEARCH_LIMIT" in client.requests[1]["messages"][-2]["content"]
    assert TRUNCATION_CONTENT == client.requests[3]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_life_agent_search_world_suspend_and_resume_grants_forced_final_turn():
    """@brief search_world 挂起保留 pending_tool_call_id 与上下文；最后一回合挂起恢复时授予恰 1 次非挂起终结机会。"""
    agent = LifeAgent(person_inputs(_persona()))
    suspend_sandbox = _FakeSandbox(
        world_results=[
            ToolDispatchResult(accepted=False, terminal_exit=False, suspended=False,
                               tool_message_payload={"result": {"error_code": "ACTION_NOT_OFFERED"},
                                                     "changes": {}, "events": []}),
            ToolDispatchResult(accepted=True, terminal_exit=False, suspended=True,
                               tool_message_payload={"result": {"activity_id": "act-search-9"}},
                               pending_memories=({"type": "routine", "text": "记住先比价"},)),
        ],
    )
    suspend_client = _ScriptedClient([
        _reply([_tool_call("w0", "revise_day_plan", {"goals": []})]),
        _reply([_tool_call("sw-call-1", "search_world", {"query": "买水果", "prefer": "盒马"})],
               reasoning="先搜一下"),
    ])
    wakeup_1 = AgentWakeupInput(
        wakeup_id="run:wake:1000:v1:a1", agent_id=1, observed_at=1000, world_state_version=1,
        mode="regular", observation=_observation(), observation_parts={"p": "sha"}, pending_presentation={},
        action_seq_start=1,
    )
    suspended_outcome = await agent.decide_wakeup(
        wakeup_1, suspend_sandbox, _binding(suspend_client, thinking=True, max_turns=3),
    )
    assert suspended_outcome.status == "suspended"
    saved = suspend_sandbox.finished
    assert saved is not None and saved.pending_tool_call_id == "sw-call-1"
    assert saved.action_id == "run:wake:1000:v1:a1:action:0002"
    assert saved.activity_id == "act-search-9" and saved.turn_used == 2
    assert saved.local_rejection_counts == {"ACTION_NOT_OFFERED": 1}

    resume_sandbox = _FakeSandbox(world_results=[
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
    ])
    resume_client = _ScriptedClient([
        _reply([_tool_call("final-go", "start_side_activity", {
            "action": "purchase", "target": ["苹果"], "prefer": "盒马"})]),
    ])
    wakeup_2 = AgentWakeupInput(
        wakeup_id=saved.wakeup_id, agent_id=1, observed_at=1300, world_state_version=2,
        mode="resume_search", observation={**_observation(), "current_time": 1300},
        observation_parts={"p": "sha"}, pending_presentation={}, action_seq_start=3,
        suspended_loop=saved, search_result_payload={"goods_rows": [{"product": "苹果"}]},
    )
    resumed_outcome = await agent.decide_wakeup(
        wakeup_2, resume_sandbox, _binding(resume_client, thinking=False, max_turns=2),
    )
    assert resumed_outcome.status == "completed" and resume_sandbox.finished is None
    assert len(resume_client.requests[0]["tools"]) == 6
    injected_msg = resume_client.requests[0]["messages"][-1]
    assert injected_msg["role"] == "tool" and injected_msg["tool_call_id"] == "sw-call-1"
    assert "苹果" in injected_msg["content"]
    assert resume_sandbox.dispatched[0][2] == "run:wake:1000:v1:a1:action:0003"


@pytest.mark.asyncio
async def test_agent_loop_error_paths_failures_cancellations_and_budget_exhaustion():
    """@brief 核验模型失败写入 failure 证据、任务取消不写 failure、证据写库失败与预算耗尽抛 ProtocolError。"""
    agent = LifeAgent(person_inputs(_persona()))
    fail_sandbox = _FakeSandbox([])
    fail_wakeup = AgentWakeupInput(
        wakeup_id="w-fail", agent_id=1, observed_at=100, world_state_version=1,
        mode="regular", observation=_observation(), observation_parts={}, pending_presentation={}, action_seq_start=1,
    )
    with pytest.raises(RuntimeError, match="network down"):
        await agent.decide_wakeup(fail_wakeup, fail_sandbox, _binding(_ScriptedClient([RuntimeError("network down")])))
    assert fail_sandbox.exchanges[-1].phase == "failure" and "MODEL_SERVICE_FAILED" in fail_sandbox.exchanges[-1].error

    cancel_sandbox = _FakeSandbox([])
    with pytest.raises(asyncio.CancelledError):
        await agent.decide_wakeup(fail_wakeup, cancel_sandbox, _binding(_ScriptedClient([asyncio.CancelledError()])))
    assert [ex.phase for ex in cancel_sandbox.exchanges] == ["request"]

    bad_ev_sandbox = _FakeSandbox([], fail_exchange=True)
    with pytest.raises(RuntimeError, match="EVIDENCE_WRITE_FAILED"):
        await agent.decide_wakeup(fail_wakeup, bad_ev_sandbox, _binding(_ScriptedClient([_reply([])])))

    exhaust_sandbox = _FakeSandbox([])
    with pytest.raises(ProtocolError, match="Model turn limit exhausted"):
        await agent.decide_wakeup(fail_wakeup, exhaust_sandbox,
                                  _binding(_ScriptedClient([_reply([])]), thinking=False, max_turns=1))
    loop = AgentLoop(
        LoopConfig("s", "w", 1, "model", _binding(_ScriptedClient([_reply([])]), max_turns=1), allow_thinking=False),
        LoopCallbacks(
            turn_tools=lambda rem, r: ([], {"search_memory": SearchMemoryRequest}),
            validate_call=lambda c, m: None,
            validate_batch=lambda c, m: None,
            execute_calls=lambda c, n: LoopStepOutcome("completed"),
            record_exchange=lambda e: None,
        ),
    )
    with pytest.raises(ProtocolError, match="Model turn limit exhausted"):
        await loop.run([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])

    for bad_patch in (
        {"current_time": True},
        {"observation_event_id": ""},
        {"wake_reasons": [""]},
        {"progress_meta": [{"protected": True, "lines": []}]},
    ):
        bad_obs = {**_observation(), **bad_patch}
        bad_wake = AgentWakeupInput(
            wakeup_id="w-bad", agent_id=1, observed_at=100, world_state_version=1,
            mode="regular", observation=bad_obs, observation_parts={}, pending_presentation={}, action_seq_start=1,
        )
        with pytest.raises(RuntimeError):
            await agent.decide_wakeup(bad_wake, _FakeSandbox([]), _binding(_ScriptedClient([])))

    bad_suspend = _FakeSandbox([
        ToolDispatchResult(accepted=True, terminal_exit=False, suspended=True, tool_message_payload={"ok": True}),
    ])
    bad_sw_client = _ScriptedClient([
        _reply([_tool_call("sw-bad", "search_world", {"query": "咖啡", "prefer": None})]),
    ])
    with pytest.raises(RuntimeError, match="Suspended search_world result lacks activity_id"):
        await agent.decide_wakeup(fail_wakeup, bad_suspend, _binding(bad_sw_client, thinking=False))
