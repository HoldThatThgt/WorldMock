"""@brief 日会话连续上下文、gate 紧凑分组、prompt_cache_key、lookup_targets/continue_activity 与日内 Compaction 单元测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import tomllib

import pytest
from pydantic import ValidationError

from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.prompt import prompt_capacity
from worldmock.agents.prompt.prompt_capacity import (
    CompactionInput,
    compact_day_session,
    select_compaction_tail,
)
from worldmock.agents.prompt.prompt_schemas import BODY_LEVELS, validate_layer
from worldmock.agents.prompt.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.agents.search.search_session import SearchSessionRunner
from worldmock.configuration.inputs.models import ModelMetadata, ModelProfileConfig, PersonaConfig
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.state.decisions.barrier import (
    AgentWakeupInput,
    SandboxOutcome,
    SearchTaskSpec,
    ToolDispatchResult,
)
from worldmock.state.decisions.day_session import DaySessionState
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.state.world.world import WORLD_SCHEMA_VERSION, WorldState

_PERSONA_TOML = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment/personas/persona_1.toml"


def _persona() -> PersonaConfig:
    """@brief 读取单人测试人设夹具。"""
    return PersonaConfig.model_validate(tomllib.loads(_PERSONA_TOML.read_text(encoding="utf-8")))


def _tool_call(cid: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    """@brief 构造原生工具调用响应替身。"""
    return SimpleNamespace(
        id=cid, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _reply(calls: list[SimpleNamespace], *, content: str | None = None,
           reasoning: str | None = None, finish_reason: str = "tool_calls",
           prompt_tokens: int = 120) -> SimpleNamespace:
    """@brief 构造异步模型响应替身。"""
    msg = SimpleNamespace(role="assistant", content=content, tool_calls=calls, reasoning_content=reasoning)
    choice = SimpleNamespace(finish_reason=finish_reason, message=msg)
    return SimpleNamespace(model="deepseek-reasoner", choices=[choice],
                           usage={"prompt_tokens": prompt_tokens, "completion_tokens": 16})


class _ScriptedClient:
    """@brief 记录请求并按脚本返回响应。"""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def call(self, request: dict[str, Any]) -> Any:
        self.requests.append(copy.deepcopy(request))
        return self._script.pop(0)


def _binding(client: Any, *, thinking: bool = True, max_turns: int = 4,
             prompt_cache_key: bool = False, compact_trigger_tokens: int = 80000) -> ResolvedModelBinding:
    """@brief 构造内存模型绑定。"""
    profile = ModelProfileConfig(
        provider="openai_compatible", base_url="https://api.example.invalid/v1",
        requested_model="deepseek-reasoner", api_key_env="WORLDMOCK_API_KEY",
        metadata_url="https://api.example.invalid/v1/models", context_window=262144,
        thinking=thinking, max_output_tokens=4096, max_react_turns=max_turns,
        prompt_cache_key=prompt_cache_key, compact_trigger_tokens=compact_trigger_tokens,
    )
    meta = ModelMetadata(
        requested_model="deepseek-reasoner", metadata_url="https://api.example.invalid/v1/models",
        context_window=262144, max_output_tokens=4096,
    )
    return ResolvedModelBinding("action-prof", profile, meta, client, 4096)


def _gate() -> dict[str, Any]:
    """@brief 构造最小合法顶层门禁。"""
    return {
        "main_slot_busy": False,
        "side_slot_busy": False,
        "sleeping": False,
        "in_transit": False,
        "pending_conclusions": [],
    }


def _observation(clock: str = "08:00", today_entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """@brief 构造满足七层拼装与 Schema 校验的观察。"""
    return {
        "current_time": 1000,
        "observation_event_id": "obs-evt-1",
        "verified_experiences": [],
        "wake_reasons": ["planned_end"],
        "memory_layer": {"layer": "记忆层", "可查": {"最早日期": "2026-01-04"}, "近三天": {},
                         "今天": today_entries or []},
        "todo_layer": {"layer": "待办层", "paused_activities": [], "errands": [], "orders": [], "day_plan": None},
        "tool_layer": {"layer": "工具层", "gate": _gate(), "targets": [],
                       "callable_index": {"goods": [], "prefer_apps": []}},
        "environment_layer": {
            "layer": "环境层", "observed_at": f"2026-01-05T{clock}:00+08:00",
            "body": {k: v[0] for k, v in BODY_LEVELS.items()},
        },
        "progress_layer": {"layer": "进度层", "session_started_at": "2026-01-05T08:00:00+08:00",
                           "rejected_today": [], "events": []},
        "progress_meta": [],
    }


class _SandboxStub:
    """@brief 支持 lookup_targets、compact_snapshot 与 DaySessionState 补全的内存沙盒替身。"""

    def __init__(self, world_results: list[ToolDispatchResult], *,
                 snapshot: dict[str, Any] | None = None,
                 lookup_results: list[dict[str, Any]] | None = None) -> None:
        self._world_results = list(world_results)
        self._snapshot = snapshot
        self._lookup_results = list(lookup_results or [])
        self.compacted_calls = 0
        self.loaded_target_keys: list[tuple[str, str]] = []

    def search_memory(self, request) -> dict[str, Any]:
        return {"items": []}

    def recall_task_summary(self, request) -> dict[str, Any]:
        return {"summary": "进行中"}

    def lookup_targets(self, request) -> dict[str, Any]:
        self.loaded_target_keys.append(("location", "loc-cafe"))
        return self._lookup_results.pop(0) if self._lookup_results else {
            "ok": True, "result": {"total": 1, "has_more": False, "next_offset": 1, "targets": [], "loaded_count": 1}}

    def dispatch_world_tool(self, tool_name, arguments, call_id, note=None) -> ToolDispatchResult:
        return self._world_results.pop(0)

    def record_model_exchange(self, exchange) -> None:
        return None

    def compact_snapshot(self) -> dict[str, Any]:
        self.compacted_calls += 1
        self.loaded_target_keys.clear()
        obs = self._snapshot or _observation(
            "14:30",
            [{"时刻": "10:15", "类别": "生活", "核验": ["到咖啡馆", "午休小睡结束"]}],
        )
        return {key: obs[key] for key in (
            "memory_layer", "todo_layer", "tool_layer", "environment_layer", "progress_layer", "progress_meta")}

    def finish_wakeup(
        self,
        suspended: SuspendedLoopState | None,
        day_session: DaySessionState,
        compaction_records: tuple[dict[str, Any], ...] = (),
    ) -> SandboxOutcome:
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
        updated = day_session.model_copy(update={"loaded_target_keys": tuple(self.loaded_target_keys)})
        return SandboxOutcome(
            wakeup_id="w1", agent_id=1,
            status="suspended" if suspended is not None else "completed",
            state=dummy_state, day_session=updated, compaction_records=compaction_records,
            observation_event_id="obs-evt-1",
        )


def test_tool_groups_and_schemas_enforce_gate_and_reject_count_line():
    """@brief compact_tool_layer/expand_tool_layer 保留 gate 与键序；Schema 要求 gate 并拒绝记忆计数行。"""
    layer = {
        "layer": "工具层",
        "gate": {**_gate(), "main_slot_busy": True, "pending_conclusions": ["工作"]},
        "targets": [{
            "name": "家", "kind": "location", "phase": None, "state": None,
            "actions": [{"action": "sleep", "detail": None, "available": True, "reason": None,
                         "price": None, "duration": None, "quantity": None, "note": None}],
            "tools": [],
        }],
        "callable_index": {"goods": [], "prefer_apps": []},
    }
    validate_layer(layer)
    compact = compact_tool_layer(layer)
    assert list(compact) == ["layer", "gate", "groups", "callable_index"]
    assert compact["gate"] == layer["gate"] and compact["gate"] is not layer["gate"]
    validate_layer(compact)
    expanded = expand_tool_layer(compact)
    assert list(expanded) == ["layer", "gate", "targets", "callable_index"]
    assert expanded == layer

    missing_gate = {k: v for k, v in layer.items() if k != "gate"}
    with pytest.raises(ValueError, match="violates its schema"):
        validate_layer(missing_gate)

    retired_mem = {
        "layer": "记忆层", "可查": {}, "近三天": {},
        "今天": [{"时刻": "08:00–09:00", "类别": "生活", "条数": 3}],
    }
    with pytest.raises(ValueError, match="violates its schema"):
        validate_layer(retired_mem)

    compacted_mem = {
        "layer": "记忆层", "可查": {}, "近三天": {}, "今天": [],
        "折叠摘要": {
            "folded_through_seq": 12, "folded_until_clock": "11:30",
            "completed_activities": ["10:15 散步结束"], "places_visited": ["公园"],
            "purchases": [], "body_and_balance_notes": ["当前体感：不饿"], "open_commitments": [],
        },
    }
    validate_layer(compacted_mem)


@pytest.mark.asyncio
async def test_day_session_prefix_continuity_paired_delta_cache_key_and_lookup_continue():
    """@brief 跨唤醒延续严格保持前缀一致，成组边界只末条带完整差量，prompt_cache_key 按需注入。"""
    agent = LifeAgent(person_inputs(_persona()))
    client1 = _ScriptedClient([
        _reply([_tool_call("q1", "lookup_targets", {"kind": "location", "keyword": "咖啡", "limit": 5, "offset": 0})]),
        _reply([
            _tool_call("s1", "start_main_activity", {"action": "sleep", "target": "120", "detail": None}),
            _tool_call("s2", "start_side_activity", {"action": "interact", "target": ["微博"], "prefer": None}),
        ]),
    ])
    sb1 = _SandboxStub([
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
    ])
    w1 = AgentWakeupInput(
        wakeup_id="run:wake:1000:v1:a1", run_id="run-42", agent_id=1, observed_at=1000,
        local_date="2026-01-05", world_state_version=1,
        mode="regular", observation=_observation(), observation_parts={}, action_seq_start=1,
    )
    out1 = await agent.decide_wakeup(w1, sb1, _binding(client1, prompt_cache_key=True))
    assert client1.requests[0]["extra_body"] == {"thinking": {"type": "enabled"},
                                                 "prompt_cache_key": "run-42:a1:2026-01-05"}
    assert out1.day_session.pending_boundary_calls == ("s1", "s2")
    assert out1.day_session.loaded_target_keys == (("location", "loc-cafe"),)
    assert out1.day_session.last_prompt_tokens == 120

    delta = {
        "ok": True,
        "result": {"status": "settled", "observed_at": "2026-01-05T10:00:00+08:00"},
        "boundary": {"observed_at": "2026-01-05T10:00:00+08:00", "wake_reasons": ["planned_end"], "events": ["小睡结束"]},
        "changes": {"环境层": {"observed_at": "2026-01-05T10:00:00+08:00"}},
    }
    client2 = _ScriptedClient([
        _reply([_tool_call("cont-1", "continue_activity", {"reason": "继续刷一会"})], prompt_tokens=300),
    ])
    sb2 = _SandboxStub([
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
    ])
    w2 = AgentWakeupInput(
        wakeup_id="run:wake:2000:v2:a1", run_id="run-42", agent_id=1, observed_at=2000,
        local_date="2026-01-05", world_state_version=2,
        mode="regular", observation=_observation("10:00"), observation_parts={}, action_seq_start=3,
        day_session=out1.day_session, boundary_delta_payload=delta,
    )
    out2 = await agent.decide_wakeup(w2, sb2, _binding(client2, prompt_cache_key=False))
    assert "prompt_cache_key" not in client2.requests[0]["extra_body"]
    sent_msgs = client2.requests[0]["messages"]
    assert sent_msgs[:len(out1.day_session.messages)] == list(out1.day_session.messages)
    assert [m["role"] for m in sent_msgs] == ["system", "user", "assistant", "tool", "assistant", "tool", "tool"]
    assert json.loads(sent_msgs[-2]["content"]) == {
        "ok": True, "result": {"status": "settled", "observed_at": "2026-01-05T10:00:00+08:00"}}
    assert json.loads(sent_msgs[-1]["content"]) == delta
    assert out2.day_session.pending_boundary_calls == ("cont-1",)
    assert out2.day_session.last_prompt_tokens == 300


@pytest.mark.asyncio
async def test_cross_midnight_resume_search_starts_fresh_day_session_and_search_keeps_four_tools():
    """@brief 跨零点恢复搜索时 day_session=None 不追加孤立 tool；SearchSessionRunner 末轮保持四个工具且无缓存键。"""
    agent = LifeAgent(person_inputs(_persona()))
    client = _ScriptedClient([
        _reply([_tool_call("c1", "continue_activity", {"reason": None})]),
    ])
    sb = _SandboxStub([
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
    ])
    suspended = SuspendedLoopState(
        wakeup_id="w-prev", action_id="w-prev:action:0001", activity_id="act-s",
        pending_tool_call_id="sw-old", turn_used=1, observation_event_id="obs-0", suspended_at=900,
    )
    w_midnight = AgentWakeupInput(
        wakeup_id="w-prev", run_id="run", agent_id=1, observed_at=1000,
        local_date="2026-01-06", world_state_version=2,
        mode="resume_search", observation=_observation("00:05"), observation_parts={}, action_seq_start=2,
        day_session=None, suspended_loop=suspended, search_result_payload={"goods_rows": []},
    )
    out = await agent.decide_wakeup(w_midnight, sb, _binding(client))
    assert [m["role"] for m in client.requests[0]["messages"]] == ["system", "user"]
    assert out.day_session.local_date == "2026-01-06"

    search_client = _ScriptedClient([
        _reply([_tool_call("sr1", "submit_search_results", {"goods_ids": [], "place_ids": [], "summary": "无结果"})]),
    ])
    catalog = SimpleNamespace(validate_submission=lambda req: {"valid": True})
    task = SearchTaskSpec(
        action_id="a1", agent_id=1, activity_id="act1", origin_wakeup_id="w1",
        query="咖啡", prefer=None, current_location_id="home", location_name="家",
        opened_at_clock="09:00", local_date="2026-01-05", weather_label="晴", turn_budget=1,
    )
    await SearchSessionRunner().run(task, catalog, _binding(search_client, prompt_cache_key=True, max_turns=1))
    assert len(search_client.requests[0]["tools"]) == 4
    assert search_client.requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_in_day_compaction_tail_splitting_and_wakeup_hook(monkeypatch):
    """@brief 水位触发日内 Compaction 保留<=6组<=16k尾部并刷新 ProgressBudget；仅单组时跳过。"""
    monkeypatch.setattr(prompt_capacity, "token_counter",
                        lambda *, model, messages=None, tools=None, text=None: len(
                            text if text is not None else json.dumps(
                                {"messages": messages, "tools": tools}, ensure_ascii=False)))
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 262144)

    agent = LifeAgent(person_inputs(_persona()))
    base_history = [
        {"role": "system", "content": '{"layer":"系统层"}'},
        {"role": "user", "content": json.dumps([
            {"layer": "人设层", "identity": {"name": "林舟"}},
            {"layer": "记忆层", "可查": {"最早日期": "2026-01-04"}, "近三天": {}, "今天": []},
            {"layer": "待办层"}, {"layer": "工具层", "gate": _gate(), "targets": [], "callable_index": {"goods": [], "prefer_apps": []}},
            {"layer": "环境层"}, {"layer": "进度层", "events": []},
        ], ensure_ascii=False)},
    ]
    # 构造 1 组时不可折叠（k == 0），select_compaction_tail 返回 None
    assert select_compaction_tail("deepseek-reasoner", base_history) is None
    one_group = [*base_history, {"role": "assistant", "content": None, "tool_calls": [{"id": "c0"}]}]
    assert select_compaction_tail("deepseek-reasoner", one_group) is None
    two_small = [
        *one_group, {"role": "tool", "tool_call_id": "c0", "content": "{}"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
    ]
    assert select_compaction_tail("deepseek-reasoner", two_small) is None
    oversized_first = [
        *base_history,
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c0"}]},
        {"role": "tool", "tool_call_id": "c0", "content": "甲" * 17000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
    ]
    cut = select_compaction_tail("deepseek-reasoner", oversized_first)
    assert cut is not None and cut[0] == 1 and len(cut[1]) == 1

    with pytest.raises(ValidationError, match="canonical ISO date"):
        DaySessionState(local_date="2026-02-30", system_prompt="sys", tools_digest="0" * 64,
                        messages=tuple(base_history))
    with pytest.raises(ValidationError, match="tool_calls"):
        DaySessionState(
            local_date="2026-01-05", system_prompt="sys", tools_digest="0" * 64,
            messages=(*base_history, {"role": "assistant", "content": "hi"}),
            pending_boundary_calls=("c0",),
        )

    # 构造 8 组交互：每组约 2000 字符，总长 > 16384 触发阈值压缩，保留尾部 6 组（<=16000）
    many_msgs = list(base_history)
    pad = "记" * 1800
    for idx in range(7):
        many_msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{idx}", "type": "function", "function": {"name": "continue_activity", "arguments": '{"reason":null}'}}]})
        many_msgs.append({"role": "tool", "tool_call_id": f"c{idx}", "content": json.dumps({"ok": True, "pad": pad}, ensure_ascii=False)})
    many_msgs.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c7", "type": "function", "function": {"name": "continue_activity", "arguments": '{"reason":null}'}}]})

    prev_session = DaySessionState(
        local_date="2026-01-05", system_prompt=base_history[0]["content"],
        tools_digest="0" * 64, messages=tuple(many_msgs), pending_boundary_calls=("c7",),
    )
    # 确认 compact_day_session 单独调用可折叠前 2 组、保留后 6 组（含新注入 tool 后的 c7），不可折叠时返回 None
    snap = _SandboxStub([]).compact_snapshot()
    assert compact_day_session(CompactionInput(
        model="deepseek-reasoner", tools=[], messages=one_group,
        day_session=prev_session, snapshot=snap, settled_day_events=(),
    )) is None
    direct = compact_day_session(CompactionInput(
        model="deepseek-reasoner", tools=[], messages=[*many_msgs, {"role": "tool", "tool_call_id": "c7", "content": "{}"}],
        day_session=prev_session, snapshot=snap,
        settled_day_events=({"event_seq": 42, "clock": "14:20", "place": "咖啡馆"},),
    ))
    assert direct is not None
    assert direct.record["folded_groups"] == 2 and direct.record["kept_tail_groups"] == 6
    assert direct.compacted_through_seq == 42 and direct.compaction_count == 1
    assert direct.messages[0]["content"] == prev_session.system_prompt
    rebuilt_layers = json.loads(direct.messages[1]["content"])
    mem_layer = next(layer for layer in rebuilt_layers if layer["layer"] == "记忆层")
    assert mem_layer["折叠摘要"]["folded_through_seq"] == 42
    assert "咖啡馆" in mem_layer["折叠摘要"]["places_visited"]
    validate_layer(mem_layer)

    # 通过 _WakeupRunner 端到端触发阈值 Compaction
    from worldmock.agents.life_agent import life_tool_schema_sha256
    valid_session = prev_session.model_copy(update={"tools_digest": life_tool_schema_sha256()})
    client = _ScriptedClient([
        _reply([_tool_call("final-c", "continue_activity", {"reason": None})]),
    ])
    sb = _SandboxStub([
        ToolDispatchResult(accepted=True, terminal_exit=True, suspended=False, tool_message_payload={"ok": True}),
    ])
    sb.loaded_target_keys.append(("location", "loc-stale"))
    wakeup = AgentWakeupInput(
        wakeup_id="w-compact", run_id="run", agent_id=1, observed_at=5000,
        local_date="2026-01-05", world_state_version=5,
        mode="regular", observation=_observation("14:30"), observation_parts={}, action_seq_start=10,
        day_session=valid_session,
        boundary_delta_payload={"ok": True, "result": {"status": "settled"}},
        settled_day_events=({"event_seq": 88, "clock": "14:25", "completed_activity": "工作结束"},),
    )
    out = await agent.decide_wakeup(wakeup, sb, _binding(client, compact_trigger_tokens=16384))
    assert sb.compacted_calls == 1
    assert len(out.compaction_records) == 1
    assert out.compaction_records[0]["compacted_through_seq"] == 88
    assert out.day_session.compaction_count == 1
    assert out.day_session.compacted_through_seq == 88
    assert out.day_session.loaded_target_keys == ()
    assert len(client.requests[0]["messages"]) == 14
