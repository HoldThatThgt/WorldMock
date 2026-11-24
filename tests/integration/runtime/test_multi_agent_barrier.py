"""多人物屏障协同、双档位异步模型路由、乱序响应确定性与 BarrierMerger 冲突回滚测试。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.frozen_inputs import fixture_metadata  # noqa: E402
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.integration.runtime_fixtures import current_run_identity  # noqa: E402
from tests.life.conftest import MAIN_TOOL, StubPlanDayClient, main_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, person_inputs  # noqa: E402
from worldmock.agents.memory.workspace import agent_workspace  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.configuration.inputs.environment import load_environment  # noqa: E402
from worldmock.configuration.model.model_registry import ModelRegistry  # noqa: E402
from worldmock.foundation.errors import BusinessRuleError  # noqa: E402
from worldmock.runtime.framework.setup import creation_inputs  # noqa: E402
from worldmock.runtime.lifecycle.run_host import RunHost  # noqa: E402
from worldmock.simulation_engine.engine import LifeEngine  # noqa: E402
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402

_MULTI_ENV = Path(__file__).resolve().parents[2] / "fixtures" / "multi-agent-environment"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def test_life_engine_public_methods_are_synchronous_and_committed_world_is_immutable(engine_factory) -> None:
    """LifeEngine 全部公开方法均为同步 def；prepare_barrier 与 open_sandbox 绝不原地修改共享 state。"""
    public_methods = [getattr(LifeEngine, name) for name in dir(LifeEngine)
                      if not name.startswith("_") and callable(getattr(LifeEngine, name))]
    assert public_methods and not any(inspect.iscoroutinefunction(fn) for fn in public_methods)

    engine = engine_factory(environment_root=_MULTI_ENV)
    before = engine.context.state.model_copy(deep=True)
    wakeups = engine.prepare_barrier((1, 2))
    assert engine.context.state == before
    sb1 = engine.open_sandbox(wakeups[0])
    sb2 = engine.open_sandbox(wakeups[1])
    sb1.dispatch_world_tool(MAIN_TOOL, main_arguments("interact", "家", "测试休息"), f"{wakeups[0].wakeup_id}:action:0001")
    sb2.dispatch_world_tool(MAIN_TOOL, main_arguments("interact", "家", "测试休息"), f"{wakeups[1].wakeup_id}:action:0001")
    assert engine.context.state == before


def test_model_registry_routes_distinct_models_per_agent_and_stage() -> None:
    """双人物环境中 agent 1 用 reasoner、agent 2 覆写为 fast_search、search 阶段用 fast_search。"""
    frozen = inputs(_MULTI_ENV)
    clients: dict[str, object] = {}
    registry = ModelRegistry(frozen.config, frozen.model_metadata,
                             client_factory=lambda prof: clients.setdefault(prof.requested_model, object()))
    b1 = registry.resolve("action", 1)
    b2 = registry.resolve("action", 2)
    b_search = registry.resolve("search", 1)
    assert (b1.profile_id, b1.profile.requested_model, b1.profile.thinking) == ("reasoner", "test-model", True)
    assert (b2.profile_id, b2.profile.requested_model, b2.profile.thinking) == ("fast_search", "test-search-model", False)
    assert b_search.profile_id == "fast_search" and b_search.client is b2.client and b1.client is b2.client


async def _run_with_delays(tmp_path: Path, name: str, delays: dict[str, float],
                           monkeypatch) -> tuple[list[dict], str, str]:
    """按给定各模型请求人工网络延迟执行真实多人物 RunHost，返回规范事件序列、random_state 与快照哈希。"""
    frozen = load_environment(_MULTI_ENV, metadata_loader=fixture_metadata)
    stop_early = frozen.config.start_time + timedelta(hours=1)
    env_copy = tmp_path / f"env-{name}"
    import shutil
    shutil.copytree(_MULTI_ENV, env_copy)
    cfg_path = env_copy / "config.toml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace("2026-01-06T08:00:00+08:00", stop_early.isoformat()),
        encoding="utf-8", newline="\n",
    )
    loaded = load_environment(env_copy, metadata_loader=fixture_metadata)
    metadata_service(monkeypatch, loaded)
    plan_client = StubPlanDayClient({"requested_model": "test-model"})
    counters = {"test-model": 0, "test-search-model": 0, "search_done": False}

    def client_for(profile):
        class _DelayedClient:
            async def call(self, request: dict):
                tools = [t["function"]["name"] for t in request.get("tools") or []]
                if "submit_day_plan" in tools:
                    return await plan_client.call(request)
                if "submit_search_results" in tools:
                    await asyncio.sleep(delays["search"])
                    counters["search_done"] = True
                    return _ModelResponse("s-ok", "submit_search_results", json.dumps({
                        "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "推荐三杯咖啡柠檬茶。",
                    }, ensure_ascii=False))
                await asyncio.sleep(delays[profile.requested_model])
                idx = counters[profile.requested_model]
                counters[profile.requested_model] += 1
                if profile.requested_model == "test-model" and idx == 0:
                    return _ModelResponse("a1-sw", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": "美团"},
                                                                           ensure_ascii=False))
                return _ModelResponse(f"m-{profile.requested_model}-{idx}", MAIN_TOOL,
                                      json.dumps(main_arguments("sleep", "60"), ensure_ascii=False))

            async def close(self) -> None:
                return None

        return _DelayedClient()

    reg = ModelRegistry(loaded.config, loaded.model_metadata, client_factory=client_for)
    run_dir = tmp_path / f"run-{name}"
    specs, warnings = creation_inputs(loaded)
    agents = {p.agent_id: person_inputs(p) for p in loaded.personas}
    host = RunHost(run_dir, loaded, reg, agents, {1: (), 2: ()})
    ident = current_run_identity(loaded, "det")
    await host.start_new(ident, {"input_warnings": warnings, "persona_habit_responses": {}})
    await host.run()
    assert counters["search_done"]
    engine = host.engine
    norm = [
        {"seq": e.event_seq, "type": e.event_type, "agent": e.agent_id,
         "wakeup": e.wakeup_id, "action": e.action_id, "occ": e.occurred_at}
        for e in engine.context.store.iter_events(engine.context.store.last_event_seq)
    ]
    rnd = engine.context.state.random_state
    state_json = re.sub(
        r'"[0-9a-f]{32}"',
        '"<uuid>"',
        json.dumps(engine.context.store.state, sort_keys=True, ensure_ascii=False),
    )
    ck_hash = hashlib.sha256(state_json.encode()).hexdigest()
    await host.close()
    return norm, rnd, ck_hash


@pytest.mark.asyncio
async def test_inverted_async_network_delays_produce_byte_identical_barrier_commits(tmp_path, monkeypatch) -> None:
    """倒置并发人物网络完成顺序（agent_2 先于 agent_1 vs agent_1 先于 agent_2），event_seq、随机流与快照完全一致。"""
    ev_a, rnd_a, ck_a = await _run_with_delays(
        tmp_path, "a", {"test-model": 0.03, "test-search-model": 0.002, "search": 0.005}, monkeypatch,
    )
    ev_b, rnd_b, ck_b = await _run_with_delays(
        tmp_path, "b", {"test-model": 0.002, "test-search-model": 0.03, "search": 0.005}, monkeypatch,
    )
    assert ev_a == ev_b and rnd_a == rnd_b and ck_a == ck_b


@pytest.mark.asyncio
async def test_cross_agent_conflict_rolls_back_agent_2_without_corrupting_memory_and_retries_same_second(
    engine_factory, host_factory, monkeypatch,
):
    """BarrierMerger 重放时 2 号人物与 1 号人物发生资源冲突：1 号保留提交并写 MEMORY.md；
    2 号仅本人回滚（清 suspended_loop、next_decision_at=now、带 rejection_feedback、不写 MEMORY.md）并在同秒立即重入。"""
    engine = engine_factory(environment_root=_MULTI_ENV)
    stage_day_plan(engine, agent_id=1, submitted=True)
    stage_day_plan(engine, agent_id=2, submitted=True)
    real_dispatch = engine.actions.dispatch
    conflict_fired = [False]

    def dispatch_with_cross_conflict(state, invs):
        # 仅当 1 号人物已进入 staged（屏障合并 2 号阶段）时，对 2 号动作抛出跨人物冲突
        if not conflict_fired[0] and invs[0].agent_id == 2 and any(a.agent_id == 1 for a in state.activities.values()):
            conflict_fired[0] = True
            raise BusinessRuleError("RESOURCE_CONFLICT")
        return real_dispatch(state, invs)

    monkeypatch.setattr(engine.actions, "dispatch", dispatch_with_cross_conflict)
    conclude_1 = _ModelResponse("c-1", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"),
                                                            ensure_ascii=False))
    sleep_2_initial = _ModelResponse("c-2-bad", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"),
                                                                       ensure_ascii=False))
    sleep_2_retry = _ModelResponse("c-2-retry", MAIN_TOOL, json.dumps(main_arguments("sleep", "60"),
                                                                      ensure_ascii=False))

    class _RouterModel:
        def __init__(self, routes):
            self.routes = {k: list(v) for k, v in routes.items()}

        async def call(self, req):
            return self.routes[req["model"]].pop(0)

    router = _RouterModel({"test-model": [conclude_1], "test-search-model": [sleep_2_initial, sleep_2_retry]})
    reg = ModelRegistry(engine.context.inputs.config, engine.context.inputs.model_metadata,
                        client_factory=lambda _prof: router)
    host = host_factory(engine, reg)
    t_now = engine.context.state.current_time
    await host.step()

    assert conflict_fired[0] is True
    assert engine.context.state.current_time == t_now
    assert (agent_workspace(engine.context.store.run_dir, 1) / "MEMORY.md").is_file()
    assert not (agent_workspace(engine.context.store.run_dir, 2) / "MEMORY.md").exists()
    assert any(a.agent_id == 1 for a in engine.context.state.activities.values())
    assert not any(a.agent_id == 2 for a in engine.context.state.activities.values())
    a2 = engine.context.state.agents[2]
    assert a2.suspended_loop is None and a2.next_decision_at == t_now and "rejection_feedback" in a2.pending_wake_reasons
    assert engine.scheduling_state().ready_agent_ids == (2,)
    rejected_events = [e for e in engine.context.store.read_agent_events("action_rejected", 2)
                       if e.data.get("error_code") == "RESOURCE_CONFLICT"]
    assert len(rejected_events) == 1

    # 下一步同秒立即只唤醒 2 号重试，观察里带回拒绝反馈
    await host.step()
    assert any(a.agent_id == 2 for a in engine.context.state.activities.values())
    assert (agent_workspace(engine.context.store.run_dir, 2) / "MEMORY.md").is_file()
