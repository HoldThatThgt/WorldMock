"""@brief 多人物异步屏障、模型双维路由、search_world 无栈挂起恢复与反事实重放系统测试。"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import threading
from types import SimpleNamespace

import pytest
import tomlkit

from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime_fixtures import current_run_identity
from tests.life.conftest import main_arguments
from worldmock import cli
from worldmock.agents.life_agent import person_inputs
from worldmock.agents.prompt.life_prompt import user_layer
from worldmock.agents.prompt.tool_groups import expand_tool_layer
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.model.model_registry import ModelRegistry
from worldmock.foundation.errors import PreconditionError
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.runtime.lifecycle.run_host import RunHost
from worldmock.simulation_engine.actions.start_group import ToolInvocation
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PENDING
from worldmock.simulation_engine.decisions import merger as merger_module
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.scheduling.clock.time_advance import due_search_activities
from worldmock.state.interaction.requests import ConcludeActivityRequest
from worldmock.state.world.kinds import ActivityKind, ActivityStatus

MULTI_AGENT_ENV_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "multi-agent-environment"
_API_KEY = ("WORLDMOCK_TEST_API_KEY", "test-only-key")
_AGENT_1_CHAT = ("start_main_activity", main_arguments("interact", "家", "测试闲聊"))
_AGENT_2_REST = ("start_main_activity", main_arguments("interact", "家", "测试休息"))


def _prepare_multi_env(root: Path, port: int | None = None, *, hours: int = 1) -> Path:
    """@brief 复制多人物环境夹具，缩短运行窗口并可选重写全部模型档案端点。"""
    dest = root / "environment"
    if not dest.exists():
        shutil.copytree(MULTI_AGENT_ENV_ROOT, dest)
    path = dest / "config.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    doc["stop_time"] = doc["start_time"] + timedelta(hours=hours)
    if port is not None:
        for profile in doc["models"].values():
            profile["base_url"] = f"http://127.0.0.1:{port}/v1"
            profile["metadata_url"] = f"http://127.0.0.1:{port}/v1/models"
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
    return dest


def _tool_response(name: str, arguments: dict, call_id: str, *, model: str = "test-model") -> dict:
    """@brief 构造标准 OpenAI 聊天补全工具调用响应正文。"""
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1767571200,
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "分析当前状态后作出决定。",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }],
            },
        }],
        "usage": {"prompt_tokens": 80, "completion_tokens": 12, "total_tokens": 92},
    }


def _latest_observation_tail(request: dict) -> tuple[dict[str, str], list[dict]]:
    """@brief 按日会话顺序折算当前活动阶段映射，并返回最后一次边界结算（或用户观察）之后的消息尾巴。"""
    messages = request["messages"]
    last_idx = max(i for i, m in enumerate(messages) if m.get("role") == "user")
    targets = expand_tool_layer(user_layer(json.loads(messages[last_idx]["content"]), "工具层"))["targets"]
    phases = {row["name"]: row["phase"] for row in targets if row.get("kind") == "activity"}
    boundary_idx = last_idx
    for idx in range(last_idx + 1, len(messages)):
        msg = messages[idx]
        if msg.get("role") != "tool":
            continue
        payload = json.loads(msg["content"])
        deltas = payload.get("changes", {}).get("工具层", {}).get("group_deltas", {})
        for row in deltas.get("removed", ()):
            if row.get("kind") == "activity":
                phases.pop(row["name"], None)
        for key in ("added", "updated"):
            for row in deltas.get(key, ()):
                if row.get("kind") == "activity":
                    phases[row["name"]] = row["phase"]
        if payload.get("result", {}).get("status") == "settled":
            boundary_idx = idx
    return phases, messages[boundary_idx + 1:]


def _agent_id_from_request(request: dict) -> int:
    """@brief 从首个 user 消息的人设层姓名识别 林舟(1) 与 陈默(2)。"""
    user_content = next(m["content"] for m in request["messages"] if m["role"] == "user")
    return 1 if "林舟" in user_content else 2


def _activity_names_in_request(request: dict, phase: str) -> list[str]:
    """@brief 从本轮最新活动阶段映射中提取处于指定阶段的活动名。"""
    phases, _ = _latest_observation_tail(request)
    return [name for name, item_phase in phases.items() if item_phase == phase]


def _as_model_response(payload: dict) -> SimpleNamespace:
    """@brief 把测试构造的 OpenAI 字典包装为支持属性访问的响应对象（与 ModelClient 返回接口一致）。"""
    choices = []
    for ch in payload["choices"]:
        msg = ch["message"]
        calls = [
            SimpleNamespace(id=tc["id"], type=tc["type"],
                            function=SimpleNamespace(name=tc["function"]["name"], arguments=tc["function"]["arguments"]))
            for tc in msg.get("tool_calls", [])
        ]
        choices.append(SimpleNamespace(
            finish_reason=ch["finish_reason"],
            message=SimpleNamespace(role=msg["role"], content=msg["content"],
                                    reasoning_content=msg.get("reasoning_content"), tool_calls=calls),
        ))
    return SimpleNamespace(
        model=payload["model"],
        choices=choices,
        usage=payload["usage"],
        model_dump=lambda mode="json", raw=copy.deepcopy(payload): raw,
    )


class _DelayedAsyncClient:
    """@brief 支持按人物注入异步延迟并校验双维路由的异步模型客户端。"""

    def __init__(self, handler, delays: dict[int, float], completion_order: list[int]) -> None:
        self._handler = handler
        self._delays = delays
        self._completion_order = completion_order

    async def call(self, request: dict):
        tools = [tool["function"]["name"] for tool in request.get("tools", [])]
        if "submit_day_plan" in tools:
            return _as_model_response(_tool_response("submit_day_plan", {"goals": []},
                                                     f"plan-{len(self._completion_order)}", model=request["model"]))
        if "submit_search_results" in tools:
            assert request["model"] == "test-search-model"
            return _as_model_response(self._handler("search", 1, request))
        agent_id = _agent_id_from_request(request)
        delay = self._delays.get(agent_id, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)
        self._completion_order.append(agent_id)
        return _as_model_response(self._handler("action", agent_id, request))

    async def close(self) -> None:
        return None


def _mask_uuid_fields(value):
    """@brief 把跨异步响应次序的纯随机 32 位十六进制事件身份归一，保留全部确定性语义字段。"""
    if isinstance(value, str):
        return re.sub(r"\b[0-9a-f]{32}\b", "<uuid>", value)
    if isinstance(value, dict):
        return {key: _mask_uuid_fields(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_mask_uuid_fields(item) for item in value]
    return value


def _normalized_events(run_dir: Path) -> list[tuple]:
    """@brief 提取运行库全量确定性事件序列摘要，用于交叉比对并发响应顺序无关性。"""
    store = FootprintStore(run_dir)
    try:
        rows = []
        for ev in store.iter_events(store.last_event_seq):
            data = copy.deepcopy(ev.data)
            if ev.event_type in {"checkpoint_created", "decision_started", "observation",
                                 "model_request", "model_response"}:
                data = {"kind": ev.event_type, "purpose": data.get("purpose"),
                        "observation_event_id": bool(data.get("observation_event_id"))}
            else:
                if ev.event_type == "memory_updated":
                    for key in ("memory_before_sha256", "memory_sha256"):
                        if data.get(key) is not None:
                            data[key] = "<memory-digest>"
                data = _mask_uuid_fields(data)
            rows.append((ev.event_seq, ev.event_type, ev.agent_id, ev.wakeup_id, ev.action_id,
                         ev.activity_id, ev.occurred_at, ev.state_version_before, ev.state_version_after,
                         canonical_json(data).decode()))
        return rows
    finally:
        store.close()


async def _run_inverted_case(tmp_path: Path, name: str, delays: dict[int, float]) -> dict:
    """@brief 运行一次双人物多轮宿主机推进并返回规范事件、随机状态及检查点摘要。"""
    env_root = _prepare_multi_env(tmp_path / name, hours=1)
    inputs = load_environment(env_root, metadata_loader=fixture_metadata)
    run_dir = tmp_path / name / "run"
    agent_records: dict[str, list[str]] = {"a1": [], "a2": [], "search": []}
    order: list[int] = []

    def handle(stage: str, agent_id: int, request: dict) -> dict:
        if stage == "search":
            idx = len(agent_records["search"])
            agent_records["search"].append(request["model"])
            if idx == 0:
                return _tool_response("search_goods", {
                    "keywords": ["柠檬茶"], "category": None, "app": None, "store": None,
                    "max_price_yuan": None, "open_only": False, "limit": 10, "offset": 0,
                }, "sc-1", model="test-search-model")
            return _tool_response("submit_search_results", {
                "goods_ids": ["lemon-tea"], "place_ids": [], "summary": "找到柠檬茶",
            }, "sc-2", model="test-search-model")
        _, tail = _latest_observation_tail(request)
        pending = _activity_names_in_request(request, PHASE_PENDING)
        active = _activity_names_in_request(request, PHASE_ACTIVE)
        has_tool = any(m.get("role") == "tool" for m in tail)
        if agent_id == 1:
            assert request["model"] == "test-model"
            turn = len(agent_records["a1"])
            agent_records["a1"].append(request["model"])
            if turn == 0:
                return _tool_response("search_world", {"query": "柠檬茶", "prefer": None}, "call-a1-s",
                                      model=request["model"])
            if pending and not has_tool:
                return _tool_response("conclude_activity", {
                    "items": [{"activity_name": name, "summary": "结束了。", "memories": [], "views": []}
                              for name in pending],
                    "errands": [],
                }, f"call-a1-c-{turn}", model=request["model"])
            if turn == 1:
                return _tool_response("start_side_activity",
                                      {"action": "purchase", "target": ["柠檬茶"], "prefer": None},
                                      "call-a1-buy", model=request["model"])
            if any("测试闲聊" in name for name in active) and not has_tool:
                chat_name = next(name for name in active if "测试闲聊" in name)
                return _tool_response("report_status", {
                    "items": [{"activity_name": chat_name, "summary": "继续闲聊。"}],
                    "errands": [],
                }, f"call-a1-rep-{turn}", model=request["model"])
            return _tool_response(*_AGENT_1_CHAT, f"call-a1-{turn}", model=request["model"])
        assert request["model"] == "test-search-model"
        turn = len(agent_records["a2"])
        agent_records["a2"].append(request["model"])
        if pending and turn > 0 and not has_tool:
            return _tool_response("conclude_activity", {
                "items": [{"activity_name": name, "summary": "休息完成。", "memories": [], "views": []}
                          for name in pending],
                "errands": [],
            }, f"call-a2-c-{turn}", model=request["model"])
        if active and not has_tool:
            return _tool_response("report_status", {
                "items": [{"activity_name": active[0], "summary": "继续休息。"}],
                "errands": [],
            }, f"call-a2-rep-{turn}", model=request["model"])
        return _tool_response(*_AGENT_2_REST, f"call-a2-r-{turn}", model=request["model"])

    registry = ModelRegistry(
        inputs.config, inputs.model_metadata,
        client_factory=lambda _profile: _DelayedAsyncClient(handle, delays, order),
    )
    agents = {persona.agent_id: person_inputs(persona) for persona in inputs.personas}
    host = RunHost(run_dir, inputs, registry, agents, {1: (), 2: ()})
    await host.start_new(current_run_identity(inputs, "det-multi-run"),
                         {"input_warnings": [], "persona_habit_responses": {1: [], 2: []}})
    try:
        for _ in range(6):
            if host.engine.context.state.current_time >= host.engine.context.state.end_time:
                break
            await host.step()
        state = host.engine.context.state
        search_acts = [a for a in state.activities.values() if a.kind == ActivityKind.SEARCH]
        store = host.engine.context.store
        cp_ids = [ev.data["checkpoint_id"] for ev in store.iter_events(store.last_event_seq)
                  if ev.event_type == "checkpoint_created"]
        last_cp = store.get_checkpoint(cp_ids[-1])
        snapshot_digests = {
            path.relative_to(run_dir).as_posix(): hashlib.sha256(
                _mask_uuid_fields(path.read_text(encoding="utf-8")).encode("utf-8")
            ).hexdigest()
            for path in sorted((run_dir / "agents").rglob("*")) if path.is_file()
        }
        assert snapshot_digests and set(last_cp.snapshots) == {"agent:1", "agent:2", "life"}
        state_digest = hashlib.sha256(canonical_json(_mask_uuid_fields(last_cp.state))).hexdigest()
        return {
            "order": order,
            "models": agent_records,
            "events": _normalized_events(run_dir),
            "random_state": state.random_state,
            "search": [(a.origin_action_id, a.planned_duration_seconds, a.data.get("app_id")) for a in search_acts],
            "snapshot_digests": snapshot_digests,
            "state_digest": state_digest,
        }
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_multi_agent_routing_and_inverted_async_delays_produce_identical_world(tmp_path):
    """@brief agent_1 走 reasoner、agent_2 与 search_world 走 fast_search；反转网络响应顺序后事件序列、随机流与检查点摘要逐字节一致。"""
    res_b_first = await _run_inverted_case(tmp_path, "b-first", {1: 0.06, 2: 0.0})
    res_a_first = await _run_inverted_case(tmp_path, "a-first", {1: 0.0, 2: 0.06})
    assert res_b_first["order"][:2] == [2, 1]
    assert res_a_first["order"][:2] == [1, 2]
    assert set(res_b_first["models"]["a1"]) == {"test-model"}
    assert set(res_b_first["models"]["a2"]) == {"test-search-model"}
    assert set(res_b_first["models"]["search"]) == {"test-search-model"}
    assert res_b_first["events"] == res_a_first["events"]
    assert res_b_first["random_state"] == res_a_first["random_state"]
    assert res_b_first["search"] == res_a_first["search"] and len(res_b_first["search"]) >= 1
    assert res_b_first["snapshot_digests"] == res_a_first["snapshot_digests"]
    assert res_b_first["state_digest"] == res_a_first["state_digest"]


@pytest.mark.asyncio
async def test_cross_agent_conflict_rolls_back_second_agent_without_corrupting_memory_md(tmp_path, monkeypatch):
    """@brief BarrierMerger 跨人物冲突回滚 agent_2：重置 next_decision_at=now 即刻带着 rejection_feedback 重试，不损坏 MEMORY.md 或 agent_1 提交。"""
    env_root = _prepare_multi_env(tmp_path / "conflict", hours=1)
    inputs = load_environment(env_root, metadata_loader=fixture_metadata)
    run_dir = tmp_path / "conflict" / "run"
    seen_wakeups: list[int] = []
    calls_by_agent: dict[int, int] = {1: 0, 2: 0}

    def handle(stage: str, agent_id: int, request: dict) -> dict:
        del stage
        calls_by_agent[agent_id] += 1
        if agent_id == 1:
            return _tool_response(*_AGENT_1_CHAT, f"c-a1-{calls_by_agent[1]}", model=request["model"])
        seen_wakeups.append(calls_by_agent[2])
        return _tool_response(*_AGENT_2_REST, f"c-a2-{calls_by_agent[2]}", model=request["model"])

    orig_exec = merger_module.execute_invocation

    def inject_conflict(context, actions, state, inv, event_ids):
        if inv.agent_id == 2 and any(act.agent_id == 1 for act in state.activities.values()):
            raise PreconditionError("RESOURCE_CONFLICT", ["shared_quiet_slot"],
                                    public_reason="Conflict with agent 1 chat")
        return orig_exec(context, actions, state, inv, event_ids)

    registry = ModelRegistry(
        inputs.config, inputs.model_metadata,
        client_factory=lambda _profile: _DelayedAsyncClient(handle, {}, []),
    )
    agents = {persona.agent_id: person_inputs(persona) for persona in inputs.personas}
    host = RunHost(run_dir, inputs, registry, agents, {1: (), 2: ()})
    await host.start_new(current_run_identity(inputs, "conflict-run"),
                         {"input_warnings": [], "persona_habit_responses": {1: [], 2: []}})
    mem2_path = host.run_dir / "agents" / "agent_0002" / "MEMORY.md"
    assert not mem2_path.exists()
    monkeypatch.setattr(merger_module, "execute_invocation", inject_conflict)
    try:
        t0 = host.engine.context.state.current_time
        await host.step()
        state1 = host.engine.context.state
        assert any(act.agent_id == 1 for act in state1.activities.values())
        assert not any(act.agent_id == 2 for act in state1.activities.values())
        assert state1.current_time == t0
        agent2 = state1.agents[2]
        assert agent2.next_decision_at == t0
        assert "rejection_feedback" in agent2.pending_wake_reasons
        assert agent2.suspended_loop is None
        assert not mem2_path.exists()
        assert not host.engine.context.store.read_agent_events("memory_updated", 2)

        monkeypatch.undo()
        assert host.engine.scheduling_state().ready_agent_ids == (2,)
        await host.step()
        state2 = host.engine.context.state
        assert any(act.agent_id == 2 for act in state2.activities.values())
        assert mem2_path.is_file()
        assert seen_wakeups == [1, 2]
        retry_obs = host.engine.context.store.read_agent_events("observation", 2)[-1]
        assert "rejection_feedback" in retry_obs.data["wake_reasons"]
    finally:
        await host.close()


class _SearchLifecycleHttpModel:
    """@brief 真 HTTP 服务器路由：支撑父运行挂起、worldmock resume 到期搜索子会话与下单、worldmock replay 清空恢复。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.lock = threading.Lock()
        self.search_requests: list[dict] = []
        self.resumed_action_requests: list[dict] = []

    def answer(self, body: dict) -> tuple[int, dict]:
        tools = [t["function"]["name"] for t in body.get("tools", [])]
        with self.lock:
            if "submit_day_plan" in tools:
                return 200, _tool_response("submit_day_plan", {"goals": []}, "call-plan", model=body["model"])
            if "submit_search_results" in tools:
                self.search_requests.append(copy.deepcopy(body))
                idx = len(self.search_requests)
                if idx == 1:
                    return 200, _tool_response("search_goods", {
                        "keywords": ["柠檬茶"], "category": None, "app": None, "store": None,
                        "max_price_yuan": None, "open_only": False, "limit": 10, "offset": 0,
                    }, "search-step-1", model=body["model"])
                return 200, _tool_response("submit_search_results", {
                    "goods_ids": ["lemon-tea"], "place_ids": [], "summary": "柠檬茶有售",
                }, "search-step-2", model=body["model"])
            agent_id = _agent_id_from_request(body)
            _, tail = _latest_observation_tail(body)
            pending = _activity_names_in_request(body, PHASE_PENDING)
            active = _activity_names_in_request(body, PHASE_ACTIVE)
            last_msg = body["messages"][-1]
            called = {tc["function"]["name"] for m in tail if m.get("role") == "assistant"
                      for tc in m.get("tool_calls", ())}
            if agent_id == 1:
                self.resumed_action_requests.append(copy.deepcopy(body))
            if self.mode == "resume" and agent_id == 1 and last_msg.get("tool_call_id") == "pending-search-call":
                return 200, _tool_response("start_side_activity", {
                    "action": "purchase", "target": ["柠檬茶"], "prefer": None,
                }, "call-a1-after-search", model=body["model"])
            to_conclude = [*pending, *active]
            if to_conclude and "conclude_activity" not in called:
                return 200, _tool_response("conclude_activity", {
                    "items": [{"activity_name": name, "summary": "结束了。", "memories": [], "views": []}
                              for name in to_conclude],
                    "errands": [],
                }, f"call-conclude-{agent_id}", model=body["model"])
            if agent_id == 2:
                return 200, _tool_response(*_AGENT_2_REST, "call-a2", model=body["model"])
            return 200, _tool_response(*_AGENT_1_CHAT, "call-a1-chat", model=body["model"])


def _start_http(handler_model: _SearchLifecycleHttpModel) -> ThreadingHTTPServer:
    """@brief 启动只服务 /v1/models 与 /v1/chat/completions 的本地 HTTP 服务。"""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            self._send(200, {"object": "list", "data": [
                {"id": "test-model", "object": "model", "context_window": 524288, "max_output_tokens": 32768},
                {"id": "test-search-model", "object": "model", "context_window": 524288, "max_output_tokens": 32768},
            ]})

        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            self._send(*handler_model.answer(json.loads(self.rfile.read(length))))

        def log_message(self, *args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_suspended_loop_resume_replay_and_cancel_search_lifecycle(tmp_path, monkeypatch):
    """@brief 无栈 suspended_loop 全生命周期：挂起落盘检查点 -> worldmock resume 到期检索后立即下单、worldmock replay 清空挂起、conclude 取消清除。"""
    monkeypatch.setenv(*_API_KEY)
    model_srv = _SearchLifecycleHttpModel("resume")
    server = _start_http(model_srv)
    try:
        env_root = _prepare_multi_env(tmp_path / "case", server.server_port, hours=1)
        inputs = load_environment(env_root)
        parent_dir = tmp_path / "case" / "parent"

        async def _build_parent():
            def _handle(stage: str, aid: int, req: dict) -> dict:
                del stage
                if aid == 1:
                    return _tool_response("search_world", {"query": "柠檬茶", "prefer": None},
                                          "pending-search-call", model=req["model"])
                return _tool_response(*_AGENT_2_REST, "parent-a2", model=req["model"])

            reg = ModelRegistry(inputs.config, inputs.model_metadata,
                                client_factory=lambda _p: _DelayedAsyncClient(_handle, {}, []))
            agents = {persona.agent_id: person_inputs(persona) for persona in inputs.personas}
            host = RunHost(parent_dir, inputs, reg, agents, {1: (), 2: ()})
            await host.start_new(current_run_identity(inputs, "parent-suspended"),
                                 {"input_warnings": [], "persona_habit_responses": {1: [], 2: []}})
            try:
                await host.step()
                store = host.engine.context.store
                cps = [ev.data["checkpoint_id"] for ev in store.iter_events(store.last_event_seq)
                       if ev.event_type == "checkpoint_created"]
                cp = store.get_checkpoint(cps[-1])
                obs_id = store.read_agent_events("observation", 1)[-1].event_id
                return cp, obs_id
            finally:
                await host.close()

        parent_cp, obs_event_id = asyncio.run(_build_parent())
        susp = parent_cp.state["agents"]["1"]["suspended_loop"]
        assert susp is not None and susp["pending_tool_call_id"] == "pending-search-call"
        assert susp["turn_used"] == 1 and susp["action_id"] and susp["activity_id"]

        resumed_dir = tmp_path / "case" / "resumed"
        rc = cli.main(["resume", "--environment", str(env_root), "--parent", str(parent_dir),
                       "--checkpoint", parent_cp.checkpoint_id, "--output", str(resumed_dir)])
        assert rc == 0
        r_store = FootprintStore(resumed_dir)
        try:
            search_reqs = [ev for ev in r_store.iter_events(r_store.last_event_seq)
                           if ev.event_type == "model_request" and ev.data.get("purpose") == "search_session"]
            assert len(search_reqs) == 2
            assert search_reqs[0].data["call_id"] == f"{susp['action_id']}:search:0001:attempt:1"
            assert search_reqs[1].data["call_id"] == f"{susp['action_id']}:search:0002:attempt:1"
            assert r_store.state["agents"]["1"]["suspended_loop"] is None
            assert any(order["goods_id"] == "lemon-tea" for order in r_store.state["deliveries"].values())
        finally:
            r_store.close()
        assert any(
            any(m.get("role") == "tool" and m.get("tool_call_id") == "pending-search-call" for m in req["messages"])
            for req in model_srv.resumed_action_requests
        )

        model_srv.mode = "replay"
        model_srv.resumed_action_requests.clear()
        model_srv.search_requests.clear()
        intervention_file = tmp_path / "case" / "intervention.json"
        intervention_file.write_text(json.dumps({
            "kind": "set_balance_fen", "agent_id": 1, "balance_fen": 100000,
            "hypothesis": "余额减半后清除旧搜索栈并按新观察决策",
            "source_event_ids": [obs_event_id],
        }, ensure_ascii=False), encoding="utf-8", newline="\n")
        replay_dir = tmp_path / "case" / "replayed"
        rc_rep = cli.main(["replay", "--environment", str(env_root), "--parent", str(parent_dir),
                           "--checkpoint", parent_cp.checkpoint_id,
                           "--intervention", str(intervention_file), "--output", str(replay_dir)])
        assert rc_rep == 0
        assert model_srv.resumed_action_requests
        first_replay_msgs = model_srv.resumed_action_requests[0]["messages"]
        assert not any(m.get("tool_call_id") == "pending-search-call" for m in first_replay_msgs)
        assert [m["role"] for m in first_replay_msgs[:2]] == ["system", "user"]

        cancel_engine = LifeEngine.open(inputs, parent_dir)
        try:
            search_act = next(a for a in cancel_engine.context.state.activities.values()
                              if a.agent_id == 1 and a.kind == ActivityKind.SEARCH)
            assert cancel_engine.context.state.agents[1].suspended_loop is not None
            obs_data = cancel_engine.observations._observation_data(cancel_engine.context.state.agents[1])
            req = ConcludeActivityRequest(
                items=[{"activity_name": search_act.name, "summary": "放弃继续检索。", "memories": [], "views": []}],
                errands=[],
            )
            inv = ToolInvocation(1, susp["wakeup_id"], f"{susp['wakeup_id']}:action:0099", "conclude_activity",
                                 req.model_dump(mode="json"), "a" * 64, "ev-req", obs_data, None)
            cancelled_state, [res] = cancel_engine.actions.dispatch(cancel_engine.context.state, [inv])
            assert res.status.value == "completed"
            assert cancelled_state.activities[search_act.activity_id].status == ActivityStatus.CANCELLED
            assert cancelled_state.agents[1].suspended_loop is None
            assert due_search_activities(cancelled_state) == ()
        finally:
            cancel_engine.close()
    finally:
        server.shutdown()
        server.server_close()
