"""运行宿主 RunHost 在真实引擎与真实 SQLite 上的屏障步序、身份、三种启动顺序、零点任务、故障登记与关闭。"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.engine_fixture import _mutated_environment
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.native_runtime_fixtures import CurrentDecisionModel
from tests.integration.runtime_fixtures import current_run_identity
from tests.life.conftest import (
    StubMemoryDigestClient,
    StubPlanDayClient,
    conclude_request,
    drive,
    report_request,
    sleep,
    stage_day_plan,
    stub_day_session,
    tool_of,
    travel,
)
from tests.life.environment_runtime import act
from worldmock.agents.life_agent import person_inputs
from worldmock.agents.memory.workspace import agent_workspace, create_workspaces, restore_workspaces, snapshot_workspaces
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.model.model_registry import ModelRegistry
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.time.readable import _datetime
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.runtime.framework.setup import creation_inputs, model_identity
from worldmock.runtime.lifecycle import midnight, run_host
from worldmock.runtime.lifecycle.runner import _intervention_sha256, habit_responses
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.interaction.requests import ReviseDayPlanRequest

_REVISE = ("revise_day_plan", ReviseDayPlanRequest(goals=[]))


class _ScriptedAgent:
    """替代 run_host.LifeAgent：实现 decide_wakeup(wakeup, sandbox, resolved)，按世界现状在沙盒调度一条动作。"""

    built: list = []
    seen: list = []
    requests: list = []
    raises: BaseException | None = None
    keep_ready: bool = False

    def __init__(self, inputs):
        self.inputs = inputs
        self.calls = 0
        type(self).built.append(self)

    async def decide_wakeup(self, wakeup, sandbox, resolved):
        self.calls += 1
        type(self).seen.append((wakeup, asyncio.get_running_loop(), threading.current_thread(), resolved))
        if type(self).raises is not None:
            raise type(self).raises
        if type(self).requests:
            tool, request = type(self).requests.pop(0)
        else:
            tool, request = _settling(sandbox._state, wakeup.agent_id, sandbox._context)
        req_dict = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
        sandbox.dispatch_world_tool(tool, req_dict, f"{wakeup.wakeup_id}:action:0001")
        return sandbox.finish_wakeup(None, stub_day_session(wakeup))


def _settling(state, agent_id: int, context) -> tuple[str, object]:
    """按世界现状选一条最简单合法动作。"""
    pending = [item.name for item in state.activities.values()
               if item.status not in {"active", "paused"} and item.concluded_at is None]
    if pending:
        return "conclude_activity", conclude_request(*pending)
    active = [item.name for item in state.activities.values() if item.status == "active"]
    if active:
        return "report_status", report_request(*active)
    home_id = context.inputs.home_location_ids[agent_id]
    request = sleep() if state.agents[agent_id].location_id == home_id else travel(
        context.catalog.locations[home_id].display_name)
    return tool_of(request), request


def _scripted(monkeypatch, requests=(), raises=None) -> type[_ScriptedAgent]:
    monkeypatch.setattr(_ScriptedAgent, "built", [])
    monkeypatch.setattr(_ScriptedAgent, "seen", [])
    monkeypatch.setattr(_ScriptedAgent, "requests", list(requests))
    monkeypatch.setattr(_ScriptedAgent, "raises", raises)
    monkeypatch.setattr(_ScriptedAgent, "keep_ready", False)
    monkeypatch.setattr(run_host, "LifeAgent", _ScriptedAgent)
    return _ScriptedAgent


def _force_ready_after_commit(monkeypatch, engine) -> list[bool]:
    """标记开启时，在 commit_barrier 之后把 1 号人物 next_decision_at 留在当前秒，用于测试同秒再决策分支。"""
    flag = [True]
    real_commit = engine.commit_barrier

    def wrapped(outcomes):
        receipt = real_commit(outcomes)
        if flag[0]:
            now = engine.context.state.current_time
            agent = engine.context.state.agents[1].model_copy(update={"next_decision_at": now}, deep=True)
            engine.context.state = engine.context.state.model_copy(
                update={"agents": {**engine.context.state.agents, 1: agent}}, deep=True,
            )
        return receipt

    monkeypatch.setattr(engine, "commit_barrier", wrapped)
    return flag


class _MidnightClient:
    """分派零点计划与回顾，并记录调用时的事件循环、线程与自定义 watch。"""

    def __init__(self, identity, *, digest_raises=None, plan_raises=None):
        self.digest = StubMemoryDigestClient(identity, raises=digest_raises)
        self.plan = StubPlanDayClient(identity, raises=plan_raises)
        self.order: list[str] = []
        self.seen: list = []
        self.watch = None
        self.closed = 0

    async def call(self, request: dict) -> dict:
        self.seen.append((asyncio.get_running_loop(), threading.current_thread(),
                          None if self.watch is None else self.watch()))
        name = request["tools"][0]["function"]["name"]
        if name == "submit_memory_digest":
            self.order.append("memory_digest")
            return await self.digest.call(request)
        if name == "submit_day_plan":
            self.order.append("plan_day")
            return await self.plan.call(request)
        raise AssertionError(f"Unexpected model request for tool {name}")

    async def close(self) -> None:
        self.closed += 1


def _late_times(persona, config) -> None:
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T08:00:00+08:00")


def _one_hour(persona, config) -> None:
    config["stop_time"] = datetime.fromisoformat("2026-01-05T09:00:00+08:00")


def _late_environment(tmp_path: Path) -> Path:
    return _mutated_environment(ENVIRONMENT_ROOT, _late_times, tmp_path / "late-environment")


def _model_dict(inputs_obj, stage: str = "plan_day") -> dict:
    p = inputs_obj.config.models[getattr(inputs_obj.config.model_routing, stage)]
    return {"requested_model": p.requested_model, "max_retries": p.max_retries,
            "transport_max_retries": p.transport_max_retries}


def _registry(inputs_obj, client) -> ModelRegistry:
    return ModelRegistry(inputs_obj.config, inputs_obj.model_metadata, client_factory=lambda _p: client)


def _fresh(tmp_path: Path, mutate=None, root: Path | None = None) -> SimpleNamespace:
    source = root or ENVIRONMENT_ROOT
    if mutate is not None:
        source = _mutated_environment(source, mutate, tmp_path / "environment")
    loaded = load_environment(source, metadata_loader=fixture_metadata)
    identity = current_run_identity(loaded, "host")
    specs, warnings = creation_inputs(loaded)
    options = {"input_warnings": warnings, "persona_habit_responses": habit_responses(specs)}
    return SimpleNamespace(
        run_dir=tmp_path / "run", inputs=loaded, identity=identity,
        m_identity=model_identity(loaded.config, loaded.model_metadata),
        stage=_model_dict(loaded), agents={1: person_inputs(loaded.persona_by_id[1])}, records={1: ()},
        options=options,
    )


@pytest.fixture
def opened():
    hosts: list = []
    yield hosts
    for host in hosts:
        if host.engine is not None:
            host.engine.close()


def _host(opened: list, fresh: SimpleNamespace, client) -> run_host.RunHost:
    reg = client if isinstance(client, ModelRegistry) else _registry(fresh.inputs, client)
    host = run_host.RunHost(fresh.run_dir, fresh.inputs, reg, fresh.agents, fresh.records)
    opened.append(host)
    return host


async def _started(opened: list, fresh: SimpleNamespace, client) -> run_host.RunHost:
    host = _host(opened, fresh, client)
    await host.start_new(fresh.identity, fresh.options)
    return host


def _events(engine, kind: str | None = None) -> list:
    store = engine.context.store
    rows = list(store.iter_events(store.last_event_seq))
    return rows if kind is None else [row for row in rows if row.event_type == kind]


def _environment_kinds(engine, kind: str) -> list:
    return [row for row in _events(engine, "environment_event") if row.data.get("kind") == kind]


def _stored_types(run_dir: Path) -> list[str]:
    connection = sqlite3.connect(f"{(Path(run_dir) / 'footprints.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(
            "SELECT json_extract(body,'$.event_type') FROM events ORDER BY event_seq")]
    finally:
        connection.close()


def _stored_world(run_dir: Path) -> dict:
    connection = sqlite3.connect(f"{(Path(run_dir) / 'footprints.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    try:
        return json.loads(connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0])
    finally:
        connection.close()


def _record_call(monkeypatch, owner, name: str, order: list) -> None:
    original = getattr(owner, name)

    def recorded(*arguments, **options):
        order.append(name)
        return original(*arguments, **options)

    monkeypatch.setattr(owner, name, recorded)


def _wrap_midnight_tasks(monkeypatch, order: list) -> None:
    for name in ("settle_pending_memory_digests", "settle_pending_day_plans"):
        original = getattr(run_host, name)

        async def wrapped(engine, registry, original=original, name=name):
            order.append(name)
            return await original(engine, registry)

        monkeypatch.setattr(run_host, name, wrapped)


def _raiser(error: BaseException):
    def fail(*arguments, **options):
        raise error
    return fail


async def test_decisions_and_midnight_tasks_run_in_the_callers_event_loop(tmp_path, monkeypatch, opened):
    """从 23:00 过零点：唤醒、零点计划与日回顾均在主线程与调用者事件循环内完成。"""
    agent = _scripted(monkeypatch)
    fresh = _fresh(tmp_path, root=_late_environment(tmp_path))
    client = _MidnightClient(fresh.stage)
    threads = set(threading.enumerate())
    host = _host(opened, fresh, client)
    client.watch = lambda: host.engine.context.state.version
    await host.start_new(fresh.identity, fresh.options)
    for _ in range(200):
        if host.current_time.date() != fresh.inputs.config.start_time.date():
            break
        await host.step()
    loop = asyncio.get_running_loop()
    assert host.current_time.hour == 0 and agent.seen and client.seen
    assert all(item[1] is loop and item[2] is threading.main_thread() for item in agent.seen)
    assert all(item[0] is loop and item[1] is threading.main_thread() for item in client.seen)
    assert set(threading.enumerate()) == threads
    assert {"memory_digest", "plan_day"} <= set(client.order)


async def test_a_failed_decision_is_never_called_again(tmp_path, monkeypatch, opened):
    """人物唤醒异常时 run() 原样抛出，不重跑。"""
    failure = RuntimeError("scripted")
    agent = _scripted(monkeypatch, raises=failure)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(RuntimeError) as raised:
        await host.run()
    assert raised.value is failure and len(agent.built) == 1 and agent.built[0].calls == 1


async def test_step_follows_the_barrier_order_then_advances_and_checkpoints(tmp_path, monkeypatch, opened):
    """一次有就绪人物且提交后无就绪人物的 step：查到期搜索、查调度、准备屏障、开沙盒、唤醒、提交屏障、再查调度、推进、写检查点。"""
    order: list[str] = []
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    engine = host.engine
    for name in ("scheduling_state", "prepare_barrier", "open_sandbox", "commit_barrier", "advance", "checkpoint"):
        _record_call(monkeypatch, engine, name, order)
    orig = _ScriptedAgent.decide_wakeup

    async def decided(self, wakeup, sandbox, resolved):
        order.append("decide_wakeup")
        return await orig(self, wakeup, sandbox, resolved)

    monkeypatch.setattr(_ScriptedAgent, "decide_wakeup", decided)
    await host.step()
    assert order == [
        "scheduling_state", "prepare_barrier", "open_sandbox", "decide_wakeup", "commit_barrier",
        "scheduling_state", "advance", "checkpoint",
    ]


async def test_same_second_redecision_checkpoints_without_advancing(tmp_path, monkeypatch, opened):
    """提交后同一秒仍有就绪人物时只写检查点不推进，下一步同秒再次唤醒。"""
    agent = _scripted(monkeypatch, requests=[_REVISE])
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    engine = host.engine
    advanced: list[str] = []
    engine.advance = lambda *a, **kw: advanced.append("advance")
    moment, checkpoints = engine.context.state.current_time, len(_events(engine, "checkpoint_created"))
    await host.step()
    vars(engine).pop("advance")
    assert engine.context.state.current_time == moment and advanced == []
    assert len(_events(engine, "checkpoint_created")) == checkpoints + 1
    assert engine.scheduling_state().ready_agent_ids == (1,)
    await host.step()
    first, second = (item[0].wakeup_id for item in agent.seen)
    assert first != second and f":wake:{moment}:" in first and f":wake:{moment}:" in second


async def test_every_step_writes_exactly_one_checkpoint(tmp_path, monkeypatch, opened):
    """一小时跑完检查点数为 step_count + 1。"""
    agent = _scripted(monkeypatch)
    fresh = _fresh(tmp_path, mutate=_one_hour)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    await host.run()
    assert len(_events(host.engine, "checkpoint_created")) == host.step_count + 1
    assert len(agent.built) < host.step_count


async def test_midnight_advance_runs_digests_then_day_plans_then_the_rhythm(tmp_path, monkeypatch, opened):
    """过零点时顺序为：memory_digest -> plan_day -> decide_midnight_rhythm。"""
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path, root=_late_environment(tmp_path))
    client = _MidnightClient(fresh.stage)
    host = await _started(opened, fresh, client)
    engine = host.engine
    act(engine, "night", sleep(480))
    client.order.clear()
    original, rhythm = engine.decide_midnight_rhythm, []

    def decided():
        client.order.append("rhythm")
        rhythm.append(engine.context.store.last_event_seq)
        original()

    monkeypatch.setattr(engine, "decide_midnight_rhythm", decided)
    while host.current_time.hour != 0:
        assert not engine.scheduling_state().ready_agent_ids
        await host.step()
    assert client.order == ["memory_digest", "plan_day", "rhythm"]
    assert _environment_kinds(engine, "memory_digest_generated")[-1].event_seq < _environment_kinds(
        engine, "day_plan_generated")[-1].event_seq <= rhythm[0]
    assert not engine.midnight_rhythm_pending


async def test_run_stops_exactly_at_the_end_time_on_the_engine_clock(tmp_path, monkeypatch, opened):
    """run() 精确停在配置的 stop_time。"""
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path, mutate=_one_hour)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    calls: list[int] = []
    step = host.step
    monkeypatch.setattr(host, "step", lambda: (calls.append(len(calls)), step())[1])
    await host.run()
    state = host.engine.context.state
    assert state.current_time == state.end_time and str(host.current_time) == str(fresh.inputs.config.stop_time)
    assert host.step_count == len(calls) > 0


async def test_wakeup_and_action_identities_keep_their_formats(engine_factory, host_factory):
    """真实 LifeAgent 跑一步：wakeup_id、action_id 与 call_id 格式精确保留。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    model = CurrentDecisionModel(None)
    model.engine = engine
    host = host_factory(engine, model)
    state = engine.scheduling_state()
    epoch = int(state.current_time.timestamp())
    await host.step()
    wakeup_id = f"{engine.context.state.run_id}:wake:{epoch}:v{state.world_state_version}:a1"
    (started,) = _events(engine, "decision_started")
    assert started.wakeup_id == wakeup_id
    assert [row.action_id for row in _events(engine, "action_requested")] == [f"{wakeup_id}:action:0001"]
    calls = [row.data["call_id"] for row in _events(engine, "model_request")]
    assert calls and all(re.fullmatch(rf"{re.escape(wakeup_id)}:model:\d{{4}}:attempt:1", c) for c in calls)
    (observed,) = _events(engine, "observation")
    assert observed.wakeup_id == wakeup_id and observed.observed_at == epoch


async def test_unknown_ready_agent_stops_before_the_barrier_opens(engine_factory):
    """就绪人物不在宿主 agents 映射内时停在屏障之前。"""
    engine = engine_factory()
    reg = _registry(engine.context.inputs, _MidnightClient(_model_dict(engine.context.inputs)))
    host = run_host.RunHost(engine.context.store.run_dir, engine.context.inputs, reg,
                            {2: person_inputs(engine.context.inputs.persona_by_id[1])}, {1: ()})
    host.engine = engine
    with pytest.raises(RuntimeError, match=r"^Environment returned unknown ready agents: \[1\]$"):
        await host.run()
    assert _events(engine, "decision_started") == [] and len(_events(engine, "run_failed")) == 1


async def test_each_wakeup_builds_a_fresh_agent_on_the_shared_objects(tmp_path, monkeypatch, opened):
    """每次唤醒构造新无状态 LifeAgent 并传入同一份冻结输入及解析后绑定。"""
    agent = _scripted(monkeypatch, requests=[_REVISE])
    fresh = _fresh(tmp_path)
    client = _MidnightClient(fresh.stage)
    host = await _started(opened, fresh, client)
    await host.step()
    await host.step()
    first, second = agent.built
    assert first is not second and [b.calls for b in agent.built] == [1, 1]
    assert first.inputs is host._agents[1] and second.inputs is host._agents[1]
    assert agent.seen[0][3].client is client and agent.seen[1][3].client is client


@pytest.mark.parametrize("failing", ["none", "engine", "client", "both"])
async def test_close_keeps_engine_and_client_failures(engine_factory, host_factory, failing):
    """关闭顺序为先引擎后注册表；双重异常合入 ExceptionGroup。"""
    engine = engine_factory()
    engine_error, client_error = ValueError("disk full while closing"), OSError("client close failed")

    class _Client(_MidnightClient):
        async def close(self) -> None:
            await super().close()
            if failing in {"client", "both"}:
                raise client_error

    client = _Client(_model_dict(engine.context.inputs))
    host = host_factory(engine, client)
    if failing in {"engine", "both"}:
        engine.close = _raiser(engine_error)
    try:
        if failing == "none":
            await host.close()
        else:
            with pytest.raises(BaseException) as raised:
                await host.close()
            error = raised.value
    finally:
        vars(engine).pop("close", None)
    assert client.closed == 1
    if failing in {"engine", "both"}:
        assert host.engine is engine and _events(engine, "run_failed")[0].data["error"] == repr(engine_error)
    if failing == "both":
        assert isinstance(error, ExceptionGroup) and error.message == "Run host close failed"
        assert error.exceptions[0] is engine_error and error.exceptions[1] is client_error


async def test_engine_close_failure_records_run_failed_or_raises_a_group(engine_factory, host_factory):
    """引擎关闭失败时登记 run_failed；登记再失败时进异常组。"""
    engine = engine_factory()
    host = host_factory(engine, _MidnightClient(_model_dict(engine.context.inputs)))
    close_error, record_error = ValueError("disk full while closing"), OSError("journal unavailable")
    engine.close = _raiser(close_error)
    try:
        with pytest.raises(ValueError) as raised:
            await host.close()
        assert raised.value is close_error and _events(engine, "run_failed")[0].data["error"] == repr(close_error)
        engine.fail_run = _raiser(record_error)
        with pytest.raises(ExceptionGroup) as grouped:
            await host.close()
    finally:
        vars(engine).pop("close", None)
        vars(engine).pop("fail_run", None)
    assert grouped.value.message == "Run and failure recording both failed"


async def test_checkpoint_snapshots_every_agent_workspace_in_agent_order(tmp_path, monkeypatch, opened):
    """检查点按键序快照全员工作区。"""
    calls: list = []
    original = run_host.snapshot_workspaces
    monkeypatch.setattr(run_host, "snapshot_workspaces",
                        lambda rd, aids, assets: (calls.append((rd, aids, assets)), original(rd, aids, assets))[1])
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    ((run_dir, agent_ids, assets),) = calls
    assert run_dir == host.run_dir and agent_ids == [1] and assets is host.engine.context.store.assets
    point = host.engine.context.store.get_checkpoint(_events(host.engine, "checkpoint_created")[0].data["checkpoint_id"])
    assert set(point.snapshots) == {"agent:1", "life"}


async def test_new_run_settles_digests_then_day_plans_after_the_initial_checkpoint(tmp_path, opened):
    fresh = _fresh(tmp_path)
    client = _MidnightClient(fresh.stage)
    host = await _started(opened, fresh, client)
    assert _events(host.engine, "checkpoint_created")[0].event_seq < _environment_kinds(
        host.engine, "day_plan_generated")[0].event_seq
    assert len(client.plan.calls) == 1 and client.order == ["plan_day"] and _events(host.engine, "decision_started") == []


async def test_every_checkpoint_settles_digests_before_day_plans(tmp_path, monkeypatch, opened):
    order: list[str] = []
    _scripted(monkeypatch)
    _wrap_midnight_tasks(monkeypatch, order)
    original = run_host.snapshot_workspaces
    monkeypatch.setattr(run_host, "snapshot_workspaces",
                        lambda rd, aids, assets: (order.append("checkpoint"), original(rd, aids, assets))[1])
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    await host.step()
    await host.step()
    assert order == ["checkpoint", "settle_pending_memory_digests", "settle_pending_day_plans"] * 3


async def _resumable(engine_factory, tmp_path: Path) -> SimpleNamespace:
    parent = engine_factory(mutate=_late_times)
    stage_day_plan(parent, submitted=True)
    act(parent, "night", sleep(480))
    while not parent.midnight_rhythm_pending:
        state = parent.scheduling_state()
        parent.advance(int(state.next_event_at.timestamp()) - parent.context.state.current_time, state.next_event_at)
    stage = _model_dict(parent.context.inputs)
    await midnight.settle_pending_day_plans(parent, _registry(parent.context.inputs, StubPlanDayClient(stage)))
    parent.decide_midnight_rhythm()
    run_dir = parent.context.store.run_dir
    create_workspaces(run_dir, (1,))
    point = parent.checkpoint(snapshot_workspaces(run_dir, [1], parent.context.store.assets))
    branch = tmp_path / "resumed"
    child = restore_branch(parent.context.store, point.checkpoint_id, branch)
    try:
        records = restore_workspaces(point.snapshots, child.assets, branch, (1,))
    finally:
        child.close()
    return SimpleNamespace(parent=parent, branch=branch, inputs=parent.context.inputs, stage=stage, records=records,
                           agents={1: person_inputs(parent.context.inputs.persona_by_id[1])})


def _branch_host(opened: list, made: SimpleNamespace, client) -> run_host.RunHost:
    host = run_host.RunHost(made.branch, made.inputs, _registry(made.inputs, client), made.agents, made.records)
    opened.append(host)
    return host


async def test_plain_resume_settles_digests_then_day_plans_without_a_checkpoint(
    engine_factory, tmp_path, monkeypatch, opened,
):
    made = await _resumable(engine_factory, tmp_path)
    order: list[str] = []
    _wrap_midnight_tasks(monkeypatch, order)
    client = _MidnightClient(made.stage)
    host = _branch_host(opened, made, client)
    checkpoints = _stored_types(made.branch).count("checkpoint_created")
    await host.start_resume()
    assert order == ["settle_pending_memory_digests", "settle_pending_day_plans"]
    assert client.order == ["memory_digest"]
    assert [row.data["period"] for row in _environment_kinds(host.engine, "memory_digest_generated")] == ["2026-01-05"]
    assert len(_events(host.engine, "checkpoint_created")) == checkpoints


async def test_plain_resume_midnight_failure_records_run_failed_and_propagates(engine_factory, tmp_path, opened):
    made = await _resumable(engine_factory, tmp_path)
    failure = ConnectionError("network is down")
    client = _MidnightClient(made.stage, digest_raises=failure)
    host = _branch_host(opened, made, client)
    with pytest.raises(ConnectionError) as raised:
        await host.start_resume()
    assert raised.value is failure and host.engine.context.store.has_event("run_failed")
    assert client.plan.calls == []


async def test_midnight_tasks_without_pending_work_build_no_business_client(engine_factory, monkeypatch):
    class _Refused:
        def __init__(self, *arguments):
            raise AssertionError("A business client was built without pending work")

    monkeypatch.setattr(midnight, "PlanDayClient", _Refused)
    monkeypatch.setattr(midnight, "MemoryDigestClient", _Refused)
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    client = _MidnightClient(_model_dict(engine.context.inputs))
    seq = engine.context.store.last_event_seq
    assert await midnight.settle_pending_memory_digests(engine, _registry(engine.context.inputs, client)) == []
    assert await midnight.settle_pending_day_plans(engine, _registry(engine.context.inputs, client)) == []
    assert client.order == [] and engine.context.store.last_event_seq == seq


async def test_midnight_tasks_wrap_the_resolved_binding_only_when_work_is_pending(engine_factory, monkeypatch):
    built: list = []
    drafts: list = []

    class _Recorded(midnight.PlanDayClient):
        def __init__(self, model_client, model_id):
            super().__init__(model_client, model_id)
            built.append((model_client, model_id))

        async def call(self, request):
            drafts.append(request)
            return await super().call(request)

    monkeypatch.setattr(midnight, "PlanDayClient", _Recorded)
    engine = engine_factory()
    stub = StubPlanDayClient(_model_dict(engine.context.inputs))
    registered = await midnight.settle_pending_day_plans(engine, _registry(engine.context.inputs, stub))
    assert [item["agent_id"] for item in registered] == [1]
    assert built[0][0] is stub and len(drafts) == 1
    assert len(_environment_kinds(engine, "day_plan_generated")) == 1


async def test_new_run_creates_workspaces_then_the_engine_then_the_initial_checkpoint(tmp_path, monkeypatch, opened):
    order: list[str] = []
    orig_engine, orig_ws = run_host.LifeEngine, run_host.create_workspaces
    monkeypatch.setattr(run_host, "create_workspaces",
                        lambda rd, aids: (order.append("workspaces"), orig_ws(rd, aids))[1])
    monkeypatch.setattr(run_host, "LifeEngine", SimpleNamespace(
        create=lambda *a: (order.append("engine"), orig_engine.create(*a))[1], open=orig_engine.open))
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    assert order == ["workspaces", "engine"]
    assert agent_workspace(fresh.run_dir, 1).is_dir() and (fresh.run_dir / "footprints.sqlite").is_file()
    assert len(_events(host.engine, "checkpoint_created")) == 1
    assert json.loads((fresh.run_dir / "status.json").read_text(encoding="utf-8"))["phase"] == "checkpointing"


async def test_existing_agent_workspace_stops_before_the_engine_is_created(tmp_path, opened):
    fresh = _fresh(tmp_path)
    agent_workspace(fresh.run_dir, 1).mkdir(parents=True)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(FileExistsError):
        await host.start_new(fresh.identity, fresh.options)
    assert not (fresh.run_dir / "footprints.sqlite").exists() and host.engine is None


async def test_start_time_disagreement_closes_the_engine_without_recording_a_failure(tmp_path, monkeypatch, opened):
    original = run_host.LifeEngine
    monkeypatch.setattr(run_host, "LifeEngine", SimpleNamespace(
        create=lambda *a: (e := original.create(*a), setattr(
            e.context, "state", e.context.state.model_copy(update={"current_time": e.context.state.current_time + 3600})
        ))[0],
        open=original.open,
    ))
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(ProtocolError, match="Run host start time disagrees with the engine"):
        await host.start_new(fresh.identity, fresh.options)
    assert host.engine is None and "run_failed" not in _stored_types(fresh.run_dir)


def _counterfactual_branch(engine_factory, tmp_path: Path) -> SimpleNamespace:
    parent = engine_factory()
    [wakeup] = parent.prepare_barrier((1,))
    parent.commit_barrier([parent.open_sandbox(wakeup).finish_wakeup(None, stub_day_session(wakeup))])
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=654_321,
        hypothesis="Higher liquidity changes purchase choices.",
        source_event_ids=(wakeup.observation["observation_event_id"],),
    )
    digest = _intervention_sha256(intervention)
    run_dir = parent.context.store.run_dir
    create_workspaces(run_dir, (1,))
    point = parent.checkpoint(snapshot_workspaces(run_dir, [1], parent.context.store.assets))
    branch = tmp_path / "branch"
    child = restore_branch(parent.context.store, point.checkpoint_id, branch,
                           {"branch_kind": "counterfactual", "intervention_sha256": digest})
    try:
        records = restore_workspaces(point.snapshots, child.assets, branch, (1,))
    finally:
        child.close()
    return SimpleNamespace(
        parent=parent, branch=branch, inputs=parent.context.inputs,
        intervention=intervention, digest=digest, stage=_model_dict(parent.context.inputs), records=records,
        agents={1: person_inputs(parent.context.inputs.persona_by_id[1])},
    )


async def test_counterfactual_start_checks_identity_time_and_version_before_its_checkpoint(
    engine_factory, tmp_path, opened,
):
    made = _counterfactual_branch(engine_factory, tmp_path)
    client = _MidnightClient(made.stage)
    host = _branch_host(opened, made, client)
    world = _stored_world(made.branch)
    receipt = await host.start_counterfactual(made.intervention, made.digest)
    assert receipt.intervention_sha256 == made.digest and receipt.after_balance_fen == 654_321
    (applied,) = _environment_kinds(host.engine, "counterfactual_intervention")
    assert applied.state_version_after == world["version"] + 1 and applied.occurred_at == world["current_time"]
    rows = _events(host.engine)
    (created,) = [row for row in rows if row.event_type == "checkpoint_created" and row.event_seq > applied.event_seq]
    assert created.state_version_after == world["version"] + 1 and client.order == ["plan_day"]
    with pytest.raises(ProtocolError):
        host.engine.apply_counterfactual(made.intervention)


def _skew_second_state(monkeypatch, check: str) -> None:
    original = run_host.LifeEngine

    def open_engine(inputs, run_dir):
        engine = original.open(inputs, run_dir)
        real, first = engine.scheduling_state, []

        def skewed():
            state = real()
            if not first:
                first.append(state)
                return state
            if check == "time":
                return replace(state, current_time=first[0].current_time + timedelta(seconds=1))
            return replace(state, world_state_version=first[0].world_state_version + 2)

        engine.scheduling_state = skewed
        return engine

    monkeypatch.setattr(run_host, "LifeEngine", SimpleNamespace(create=original.create, open=open_engine))


@pytest.mark.parametrize("check", ["identity", "time", "version"])
async def test_counterfactual_check_failures_record_run_failed(engine_factory, tmp_path, monkeypatch, opened, check):
    texts = {"identity": "Environment confirmed a different counterfactual identity",
             "time": "Counterfactual intervention changed simulation time",
             "version": "Counterfactual intervention did not advance one world version"}
    made = _counterfactual_branch(engine_factory, tmp_path)
    if check != "identity":
        _skew_second_state(monkeypatch, check)
    host = _branch_host(opened, made, _MidnightClient(made.stage))
    checkpoints = _stored_types(made.branch).count("checkpoint_created")
    with pytest.raises(RuntimeError, match=texts[check]) as raised:
        await host.start_counterfactual(made.intervention, "0" * 64 if check == "identity" else made.digest)
    assert _events(host.engine, "run_failed")[0].data["error"] == repr(raised.value)
    assert len(_events(host.engine, "checkpoint_created")) == checkpoints


async def test_new_run_failure_after_the_engine_starts_records_run_failed(tmp_path, opened):
    failure = ConnectionError("network is down")
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage, plan_raises=failure))
    with pytest.raises(ConnectionError) as raised:
        await host.start_new(fresh.identity, fresh.options)
    assert raised.value is failure
    assert _events(host.engine, "run_failed")[0].data["error"] == repr(failure)


@pytest.mark.parametrize("moment", ["initial", "step"])
async def test_checkpoint_failure_records_run_failed(tmp_path, monkeypatch, opened, moment):
    failure = OSError("checkpoint failed")
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    if moment == "initial":
        monkeypatch.setattr(run_host, "snapshot_workspaces", _raiser(failure))
        with pytest.raises(OSError) as raised:
            await host.start_new(fresh.identity, fresh.options)
    else:
        await host.start_new(fresh.identity, fresh.options)
        monkeypatch.setattr(run_host, "snapshot_workspaces", _raiser(failure))
        with pytest.raises(OSError) as raised:
            await host.run()
    assert raised.value is failure and _events(host.engine, "run_failed")[0].data["error"] == repr(failure)


async def test_status_phases_follow_the_barrier_advance_and_checkpoint(tmp_path, monkeypatch, opened):
    _scripted(monkeypatch, requests=[_REVISE])
    written: list = []
    original = run_host.write_status
    monkeypatch.setattr(run_host, "write_status", lambda rd, s, p, *r: (written.append((s, p)), original(rd, s, p, *r)))
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    assert written == [("running", "checkpointing")]
    written.clear()
    await host.step()
    assert written == [("running", p) for p in ("deciding", "resolving", "checkpointing")]
    written.clear()
    await host.step()
    assert written == [("running", p) for p in ("deciding", "resolving", "advancing", "checkpointing")]
