"""运行宿主 RunHost 在真实引擎与真实 SQLite 上的步序、身份、三种启动顺序、零点任务触发点、故障登记与关闭。

宿主的步序与触发点不依赖回合循环，所以多数用例把人物换成按世界现状选一条请求的 _ScriptedAgent；身份格式一例用真实
LifeAgent。整次运行唯一的模型客户端由 _MidnightClient 替身，它按请求里唯一工具的名字把回顾与零点计划分派给两个现有桩。
"""

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
    context,
    drive,
    report_request,
    sleep,
    stage_day_plan,
    tool_of,
    travel,
)
from tests.life.environment_runtime import act
from worldmock.agents.life_agent import person_inputs
from worldmock.agents.memory.workspace import agent_workspace, create_workspaces, restore_workspaces, snapshot_workspaces
from worldmock.configuration.inputs.environment import load_environment
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.time.readable import _datetime
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.runtime.framework.setup import creation_inputs, stage_model_identity
from worldmock.runtime.lifecycle import midnight, run_host
from worldmock.runtime.lifecycle.runner import _intervention_sha256, habit_responses
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.decisions.round import DirectToolCall
from worldmock.state.interaction.requests import ReviseDayPlanRequest

#: 同一秒再决策用的请求：不开始活动，人物在同一秒仍就绪。
_REVISE = ("revise_day_plan", ReviseDayPlanRequest(goals=[]))


class _ScriptedAgent:
    """构造参数同 LifeAgent 的人物替身：记下自己；决定时先观察，再按世界现状只登记一条请求（有待收尾的活动就收尾，
    有进行中的活动就播报，否则在家睡、不在家就步行回家），不开轮、不提交。requests 非空时依次登记其中的请求，
    raises 非空时抛出它。"""

    built: list = []
    seen: list = []
    requests: list = []
    raises: BaseException | None = None

    def __init__(self, inputs, engine, model_client, memory):
        self.inputs, self.engine, self.model_client, self.memory = inputs, engine, model_client, memory
        self.calls = 0
        type(self).built.append(self)

    async def decide(self, context) -> None:
        self.calls += 1
        type(self).seen.append((context, asyncio.get_running_loop(), threading.current_thread(),
                                self.engine.decisions.round))
        if type(self).raises is not None:
            raise type(self).raises
        self.engine.observe(context)
        tool, request = type(self).requests.pop(0) if type(self).requests else _settling(self.engine, context.agent_id)
        self.engine.submit_action(DirectToolCall(context, f"{context.decision_id}:action:0001", tool, request))


def _settling(engine, agent_id: int) -> tuple[str, object]:
    """与 tests/life/environment_runtime.py 的 settle_decision 同一选法，只返回工具名与请求。"""
    state = engine.context.state
    pending = [item.name for item in state.activities.values()
               if item.status not in {"active", "paused"} and item.concluded_at is None]
    if pending:
        return "conclude_activity", conclude_request(*pending)
    active = [item.name for item in state.activities.values() if item.status == "active"]
    if active:
        return "report_status", report_request(*active)
    home_id = engine.context.inputs.home_location_id
    request = sleep() if state.agents[agent_id].location_id == home_id else travel(
        engine.context.catalog.locations[home_id].display_name)
    return tool_of(request), request


def _scripted(monkeypatch, requests=(), raises=None) -> type[_ScriptedAgent]:
    """把宿主模块里的 LifeAgent 换成 _ScriptedAgent，并清空它的记录。"""
    monkeypatch.setattr(_ScriptedAgent, "built", [])
    monkeypatch.setattr(_ScriptedAgent, "seen", [])
    monkeypatch.setattr(_ScriptedAgent, "requests", list(requests))
    monkeypatch.setattr(_ScriptedAgent, "raises", raises)
    monkeypatch.setattr(run_host, "LifeAgent", _ScriptedAgent)
    return _ScriptedAgent


class _MidnightClient:
    """整次运行唯一模型客户端的替身：按请求里唯一工具的名字把回顾交给回顾替身、零点计划交给计划替身，在 order 末尾记下
    这一路；digest_raises、plan_raises 让那一路抛出；每次调用记下事件循环、线程与 watch 给出的轮次；close 记次数。"""

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
    """2026-01-05 23:00 开始，01-08 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T08:00:00+08:00")


def _one_hour(persona, config) -> None:
    """夹具环境本身是 24 小时，这里只跑开头一小时。"""
    config["stop_time"] = datetime.fromisoformat("2026-01-05T09:00:00+08:00")


def _late_environment(tmp_path: Path) -> Path:
    """样例环境的副本：2026-01-05 23:00 开始，01-08 08:00 截止。"""
    return _mutated_environment(ENVIRONMENT_ROOT, _late_times, tmp_path / "late-environment")


def _fresh(tmp_path: Path, mutate=None, root: Path | None = None) -> SimpleNamespace:
    """装配新建宿主所需的全部值：运行目录、冻结输入、运行身份、阶段身份、人物冻结输入、空记忆与三键新建选项。"""
    source = root or ENVIRONMENT_ROOT
    if mutate is not None:
        source = _mutated_environment(source, mutate, tmp_path / "environment")
    inputs = load_environment(source, metadata_loader=fixture_metadata)
    stage = stage_model_identity(inputs.config.model, inputs.model_metadata)
    specs, warnings = creation_inputs(inputs)
    options = {"same_second_continuation": True, "input_warnings": warnings,
               "persona_habit_responses": habit_responses(specs)}
    return SimpleNamespace(run_dir=tmp_path / "run", inputs=inputs, identity=current_run_identity(inputs, "host"),
                           stage=stage, agents={1: person_inputs(inputs.persona, stage)}, records={1: ()},
                           options=options)


@pytest.fixture
def opened():
    """本文件新建或打开的宿主；用例结束时关掉仍开着的引擎。"""
    hosts: list = []
    yield hosts
    for host in hosts:
        if host.engine is not None:
            host.engine.close()


def _host(opened: list, fresh: SimpleNamespace, client) -> run_host.RunHost:
    """按 _fresh 的装配构造一个尚未启动的宿主。"""
    host = run_host.RunHost(fresh.run_dir, fresh.inputs, client, fresh.agents, fresh.records)
    opened.append(host)
    return host


async def _started(opened: list, fresh: SimpleNamespace, client) -> run_host.RunHost:
    """构造并以新建顺序启动一个宿主。"""
    host = _host(opened, fresh, client)
    await host.start_new(fresh.identity, fresh.stage, fresh.options)
    return host


def _events(engine, kind: str | None = None) -> list:
    """引擎库里已提交的事件，可按类型筛选。"""
    store = engine.context.store
    rows = list(store.iter_events(store.last_event_seq))
    return rows if kind is None else [row for row in rows if row.event_type == kind]


def _environment_kinds(engine, kind: str) -> list:
    """引擎库里某一种环境事件。"""
    return [row for row in _events(engine, "environment_event") if row.data.get("kind") == kind]


def _stored_types(run_dir: Path) -> list[str]:
    """用只读连接按序号读运行库里每条事件的类型（引擎可能已经关闭）。"""
    connection = sqlite3.connect(f"{(Path(run_dir) / 'footprints.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(
            "SELECT json_extract(body,'$.event_type') FROM events ORDER BY event_seq")]
    finally:
        connection.close()


def _stored_world(run_dir: Path) -> dict:
    """用只读连接读运行库里已提交的世界。"""
    connection = sqlite3.connect(f"{(Path(run_dir) / 'footprints.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    try:
        return json.loads(connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0])
    finally:
        connection.close()


def _record_call(monkeypatch, owner, name: str, order: list) -> None:
    """给对象上的一个方法包一层先记下方法名、再转调原方法的包装。"""
    original = getattr(owner, name)

    def recorded(*arguments, **options):
        order.append(name)
        return original(*arguments, **options)

    monkeypatch.setattr(owner, name, recorded)


def _wrap_midnight_tasks(monkeypatch, order: list) -> None:
    """把宿主模块里的两个零点任务换成先记下调用次序、再转调原函数的包装。"""
    for name in ("settle_pending_memory_digests", "settle_pending_day_plans"):
        original = getattr(run_host, name)

        async def wrapped(engine, client, original=original, name=name):
            order.append(name)
            return await original(engine, client)

        monkeypatch.setattr(run_host, name, wrapped)


def _raiser(error: BaseException):
    """被调用即抛出给定异常的替身。"""
    def fail(*arguments, **options):
        raise error
    return fail


async def test_decisions_and_midnight_tasks_run_in_the_callers_event_loop(tmp_path, monkeypatch, opened):
    """从 23:00 新建并逐步过第一个本地零点：人物决策与两条零点任务都在用例自己的事件循环、主线程里执行；零点调用时
    引擎没有开着的决策轮，决策时有；前后线程集合相同，回顾与计划两路都被调用过。"""
    agent = _scripted(monkeypatch)
    fresh = _fresh(tmp_path, root=_late_environment(tmp_path))
    client = _MidnightClient(fresh.stage)
    threads = set(threading.enumerate())
    host = _host(opened, fresh, client)
    client.watch = lambda: host.engine.decisions.round
    await host.start_new(fresh.identity, fresh.stage, fresh.options)
    for _ in range(200):
        if host.current_time.date() != fresh.inputs.config.start_time.date():
            break
        await host.step()
    loop = asyncio.get_running_loop()
    assert host.current_time.hour == 0
    assert agent.seen and client.seen
    assert all(item[1] is loop and item[2] is threading.main_thread() for item in agent.seen)
    assert all(item[0] is loop and item[1] is threading.main_thread() for item in client.seen)
    assert all(item[3] is not None for item in agent.seen)
    assert all(item[2] is None for item in client.seen)
    assert set(threading.enumerate()) == threads
    assert {"memory_digest", "plan_day"} <= set(client.order)


async def test_a_failed_decision_is_never_called_again(tmp_path, monkeypatch, opened):
    """人物决策抛出时 run() 抛出同一对象，这次决策只被调用一次，不重跑。"""
    failure = RuntimeError("scripted")
    agent = _scripted(monkeypatch, raises=failure)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(RuntimeError) as raised:
        await host.run()
    assert raised.value is failure
    assert len(agent.built) == 1 and agent.built[0].calls == 1


async def test_step_follows_the_round_order_then_advances_and_checkpoints(tmp_path, monkeypatch, opened):
    """一次有决策、提交后没人就绪的 step：取调度状态、开轮、决策、提交、再取调度状态、推进、写检查点；提交换成返回
    None 的包装照常通过，说明宿主不读提交的返回值。"""
    order: list[str] = []
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    engine = host.engine
    for name in ("scheduling_state", "begin_decision_round", "advance", "checkpoint"):
        _record_call(monkeypatch, engine, name, order)
    committed = engine.commit_decision_round

    def commit(round_id):
        order.append("commit_decision_round")
        committed(round_id)

    monkeypatch.setattr(engine, "commit_decision_round", commit)
    decide = _ScriptedAgent.decide

    async def decided(self, context):
        order.append("decide")
        await decide(self, context)

    monkeypatch.setattr(_ScriptedAgent, "decide", decided)
    await host.step()
    assert order == ["scheduling_state", "begin_decision_round", "decide", "commit_decision_round",
                     "scheduling_state", "advance", "checkpoint"]


async def test_same_second_redecision_checkpoints_without_advancing(tmp_path, monkeypatch, opened):
    """人物在提交之后同一秒仍就绪：这一步不推进、时钟不变、多一个检查点；下一步在同一秒开新轮，轮次身份只差世界版本。
    第一步里引擎的推进换成记下调用并以断言失败的替身，第二步换回真实方法。"""
    agent = _scripted(monkeypatch, requests=[_REVISE])
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    engine = host.engine
    advanced: list[str] = []

    def refused(*arguments, **options):
        advanced.append("advance")
        raise AssertionError("RunHost advanced while agent 1 was still ready")

    engine.advance = refused
    moment, checkpoints = engine.context.state.current_time, len(_events(engine, "checkpoint_created"))
    try:
        await host.step()
    finally:
        vars(engine).pop("advance")
    assert engine.context.state.current_time == moment and advanced == []
    assert len(_events(engine, "checkpoint_created")) == checkpoints + 1
    assert engine.scheduling_state().ready_agent_ids == (1,)
    await host.step()
    first, second = (item[0].round_id for item in agent.seen)
    assert first.rsplit(":", 1)[0] == second.rsplit(":", 1)[0]
    assert int(second.rsplit(":", 1)[1]) > int(first.rsplit(":", 1)[1])


async def test_every_step_writes_exactly_one_checkpoint(tmp_path, monkeypatch, opened):
    """一小时的新建运行跑完：检查点数恰为步数加一（加的是初始检查点），其中有只推进、不决策的步。"""
    agent = _scripted(monkeypatch)
    fresh = _fresh(tmp_path, mutate=_one_hour)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    await host.run()
    assert len(_events(host.engine, "checkpoint_created")) == host.step_count + 1
    assert len(agent.built) < host.step_count


async def test_midnight_advance_runs_digests_then_day_plans_then_the_rhythm(tmp_path, monkeypatch, opened):
    """人物 23:00 睡 480 分钟，经 step 过零点：回顾替身的调用早于计划替身；事件序号上回顾登记早于计划登记，计划登记不晚于
    零点节律那次提交；之后零点节律不再待判定。"""
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
    digest = _environment_kinds(engine, "memory_digest_generated")[-1].event_seq
    plan = _environment_kinds(engine, "day_plan_generated")[-1].event_seq
    assert digest < plan <= rhythm[0]
    assert engine.context.store.last_event_seq > rhythm[0] and not engine.midnight_rhythm_pending


async def test_run_stops_exactly_at_the_end_time_on_the_engine_clock(tmp_path, monkeypatch, opened):
    """一小时的新建运行：run() 停在引擎时钟的结束时刻；宿主时刻每次读引擎，字符串与配置的结束时刻相同；步数等于 step 的调用次数。"""
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path, mutate=_one_hour)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    calls: list[int] = []
    step = host.step

    async def counted():
        calls.append(len(calls))
        await step()

    monkeypatch.setattr(host, "step", counted)
    await host.run()
    state = host.engine.context.state
    assert state.current_time == state.end_time
    assert host.current_time == _datetime(state.end_time, fresh.inputs.config.timezone)
    assert str(host.current_time) == str(fresh.inputs.config.stop_time)
    assert host.step_count == len(calls) > 0


async def test_round_and_decision_identities_keep_their_formats(engine_factory, host_factory):
    """真实 LifeAgent 跑一步：轮次身份、决策身份、动作身份与调用身份的格式逐字保留，观察时刻是提交前的当前时刻。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    model = CurrentDecisionModel(None)
    model.engine = engine
    host = host_factory(engine, model)
    state = engine.scheduling_state()
    epoch, run_id = int(state.current_time.timestamp()), engine.context.state.run_id
    await host.step()
    round_id = f"{run_id}:round:{epoch}:{state.world_state_version}"
    decision_id = f"{round_id}:agent:1:order:0"
    (started,) = _events(engine, "decision_started")
    assert started.data["round_id"] == round_id and started.data["round_order"] == 0
    assert started.decision_id == decision_id
    assert [row.action_id for row in _events(engine, "action_requested")] == [f"{decision_id}:action:0001"]
    calls = [row.data["call_id"] for row in _events(engine, "model_request")]
    assert calls and all(re.fullmatch(rf"{re.escape(decision_id)}:model:\d{{4}}:attempt:1", call) for call in calls)
    assert calls == [f"{decision_id}:model:{number:04d}:attempt:1" for number in range(1, len(calls) + 1)]
    (observed,) = _events(engine, "observation")
    assert observed.decision_id == decision_id and observed.observed_at == epoch


async def test_unknown_ready_agent_stops_before_the_round_opens(engine_factory):
    """引擎给出的就绪人物宿主没有冻结输入：开轮之前以原文停止，没有 decision_started，运行级登记的 round_id 为空。"""
    engine = engine_factory()
    identity = engine.context.store.manifest["stage_model_identity"]
    host = run_host.RunHost(engine.context.store.run_dir, engine.context.inputs, _MidnightClient(identity),
                            {2: person_inputs(engine.context.inputs.persona, identity)}, {1: ()})
    host.engine = engine
    with pytest.raises(RuntimeError) as raised:
        await host.run()
    assert str(raised.value) == "Environment returned unknown ready agents: [1]"
    assert _events(engine, "decision_started") == []
    (failed,) = _events(engine, "run_failed")
    assert failed.data["round_id"] is None


async def test_each_decision_builds_a_fresh_agent_on_the_shared_objects(tmp_path, monkeypatch, opened):
    """两次决策构造两个人物对象，每个对象恰好决定一次；每次收到的冻结输入、引擎、模型客户端与记忆存储都是宿主持有的
    同一对象。"""
    agent = _scripted(monkeypatch, requests=[_REVISE])
    fresh = _fresh(tmp_path)
    client = _MidnightClient(fresh.stage)
    host = await _started(opened, fresh, client)
    await host.step()
    await host.step()
    first, second = agent.built
    assert first is not second
    assert [built.calls for built in agent.built] == [1, 1]
    for built in (first, second):
        assert built.inputs is host._agents[1] and built.engine is host.engine
        assert built.model_client is client and built.memory is host._memories[1]


@pytest.mark.parametrize("failing", ["none", "engine", "client", "both"])
async def test_close_keeps_engine_and_client_failures(engine_factory, host_factory, failing):
    """关闭先关引擎、后关客户端：引擎关闭失败时登记运行故障且引擎属性不变，客户端仍关闭一次；两项都失败时两项都保留。"""
    engine = engine_factory()
    identity = engine.context.store.manifest["stage_model_identity"]
    engine_error, client_error = ValueError("disk full while closing"), OSError("client close failed")

    class _Client(_MidnightClient):
        async def close(self) -> None:
            await super().close()
            if failing in {"client", "both"}:
                raise client_error

    client = _Client(identity)
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
    if failing in {"none", "client"}:
        assert host.engine is None
    if failing == "client":
        assert error is client_error
    if failing in {"engine", "both"}:
        assert host.engine is engine
        (failed,) = _events(engine, "run_failed")
        assert failed.data["error"] == repr(engine_error)
    if failing == "engine":
        assert error is engine_error
    if failing == "both":
        assert isinstance(error, ExceptionGroup) and error.message == "Run host close failed"
        assert [type(item) for item in error.exceptions] == [ValueError, OSError]
        assert error.exceptions[0] is engine_error and error.exceptions[1] is client_error


async def test_engine_close_failure_records_run_failed_or_raises_a_group(engine_factory, host_factory):
    """引擎关闭失败时库里有 run_failed，原因是关闭错误的 repr；登记本身也失败时两项都在一个异常组里。"""
    engine = engine_factory()
    host = host_factory(engine, _MidnightClient(engine.context.store.manifest["stage_model_identity"]))
    close_error, record_error = ValueError("disk full while closing"), OSError("journal unavailable")
    engine.close = _raiser(close_error)
    try:
        with pytest.raises(ValueError) as raised:
            await host.close()
        assert raised.value is close_error and host.engine is engine
        (failed,) = _events(engine, "run_failed")
        assert failed.data["error"] == repr(close_error)
        engine.fail_run = _raiser(record_error)
        with pytest.raises(ExceptionGroup) as grouped:
            await host.close()
    finally:
        vars(engine).pop("close", None)
        vars(engine).pop("fail_run", None)
    assert grouped.value.message == "Run and failure recording both failed"
    assert grouped.value.exceptions[0] is close_error and grouped.value.exceptions[1] is record_error


async def test_checkpoint_snapshots_every_agent_workspace_in_agent_order(tmp_path, monkeypatch, opened):
    """共同检查点按人物编号次序为每个人物工作区取快照，资产库是引擎的；提交的检查点参与者恰为人物与 life。"""
    calls: list = []
    original = run_host.snapshot_workspaces

    def snapshot(run_dir, agent_ids, assets):
        calls.append((run_dir, agent_ids, assets))
        return original(run_dir, agent_ids, assets)

    monkeypatch.setattr(run_host, "snapshot_workspaces", snapshot)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    ((run_dir, agent_ids, assets),) = calls
    assert run_dir == host.run_dir and agent_ids == [1] and assets is host.engine.context.store.assets
    (created,) = _events(host.engine, "checkpoint_created")
    point = host.engine.context.store.get_checkpoint(created.data["checkpoint_id"])
    assert set(point.snapshots) == {"agent:1", "life"}


async def test_new_run_settles_digests_then_day_plans_after_the_initial_checkpoint(tmp_path, opened):
    """新建运行的初始检查点之后才补零点计划：检查点事件早于计划登记，计划替身恰被调用一次，还没有任何决策。"""
    fresh = _fresh(tmp_path)
    client = _MidnightClient(fresh.stage)
    host = await _started(opened, fresh, client)
    (created,) = _events(host.engine, "checkpoint_created")
    (planned,) = _environment_kinds(host.engine, "day_plan_generated")
    assert created.event_seq < planned.event_seq
    assert len(client.plan.calls) == 1 and client.order == ["plan_day"]
    assert _events(host.engine, "decision_started") == []


async def test_every_checkpoint_settles_digests_before_day_plans(tmp_path, monkeypatch, opened):
    """每次写共同检查点之后恰好先跑回顾任务、再跑零点计划任务各一次（初始检查点与每一步的检查点）。"""
    order: list[str] = []
    _scripted(monkeypatch)
    _wrap_midnight_tasks(monkeypatch, order)
    original = run_host.snapshot_workspaces

    def snapshot(run_dir, agent_ids, assets):
        order.append("checkpoint")
        return original(run_dir, agent_ids, assets)

    monkeypatch.setattr(run_host, "snapshot_workspaces", snapshot)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    await host.step()
    await host.step()
    assert order == ["checkpoint", "settle_pending_memory_digests", "settle_pending_day_plans"] * 3


def _resumable(engine_factory, tmp_path: Path) -> SimpleNamespace:
    """父运行推进过零点、只跑零点计划与节律、不跑回顾，再取检查点并恢复出一个普通恢复分支（库与人物工作区）。"""
    parent = engine_factory(mutate=_late_times)
    stage_day_plan(parent, submitted=True)
    act(parent, "night", sleep(480))
    while not parent.midnight_rhythm_pending:
        state = parent.scheduling_state()
        parent.advance(int(state.next_event_at.timestamp()) - parent.context.state.current_time, state.next_event_at)
    identity = parent.context.store.manifest["stage_model_identity"]
    drive(midnight.settle_pending_day_plans(parent, StubPlanDayClient(identity)))
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
    return SimpleNamespace(parent=parent, branch=branch, identity=identity, records=records,
                           agents={1: person_inputs(parent.context.inputs.persona, identity)})


def _branch_host(opened: list, made: SimpleNamespace, client) -> run_host.RunHost:
    """在恢复出的分支目录上构造一个尚未启动的宿主。"""
    host = run_host.RunHost(made.branch, made.parent.context.inputs, client, made.agents, made.records)
    opened.append(host)
    return host


async def test_plain_resume_settles_digests_then_day_plans_without_a_checkpoint(
        engine_factory, tmp_path, monkeypatch, opened):
    """普通恢复启动时、首次决策之前先跑回顾再跑零点计划：登记了 2026-01-05 的日回顾，不写检查点；打开的是分支自己的运行。"""
    made = _resumable(engine_factory, tmp_path)
    order: list[str] = []
    _wrap_midnight_tasks(monkeypatch, order)
    client = _MidnightClient(made.identity)
    host = _branch_host(opened, made, client)
    checkpoints = _stored_types(made.branch).count("checkpoint_created")
    await host.start_resume()
    assert order == ["settle_pending_memory_digests", "settle_pending_day_plans"]
    assert client.order == ["memory_digest"]
    assert [row.data["period"] for row in _environment_kinds(host.engine, "memory_digest_generated")] == ["2026-01-05"]
    assert len(_events(host.engine, "checkpoint_created")) == checkpoints
    assert host.engine.context.state.run_id == host.engine.context.store.manifest["run_id"]


async def test_plain_resume_midnight_failure_records_run_failed_and_propagates(engine_factory, tmp_path, opened):
    """普通恢复启动时回顾失败：抛出同一对象，库里有 run_failed，零点计划没有被调用。"""
    made = _resumable(engine_factory, tmp_path)
    failure = ConnectionError("network is down")
    client = _MidnightClient(made.identity, digest_raises=failure)
    host = _branch_host(opened, made, client)
    with pytest.raises(ConnectionError) as raised:
        await host.start_resume()
    assert raised.value is failure
    assert host.engine.context.store.has_event("run_failed")
    assert client.plan.calls == []


async def test_midnight_tasks_without_pending_work_build_no_business_client(engine_factory, monkeypatch):
    """已登记当日计划、没有待生成的回顾：两个零点任务都返回空表，不构造业务客户端、不调用模型客户端、不写任何事件。"""
    class _Refused:
        def __init__(self, *arguments):
            raise AssertionError("A business client was built without pending work")

    monkeypatch.setattr(midnight, "PlanDayClient", _Refused)
    monkeypatch.setattr(midnight, "MemoryDigestClient", _Refused)
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    client = _MidnightClient(engine.context.store.manifest["stage_model_identity"])
    sequence = engine.context.store.last_event_seq
    assert await midnight.settle_pending_memory_digests(engine, client) == []
    assert await midnight.settle_pending_day_plans(engine, client) == []
    assert client.order == [] and client.seen == []
    assert engine.context.store.last_event_seq == sequence


async def test_midnight_tasks_wrap_the_given_client_only_when_work_is_pending(engine_factory, monkeypatch):
    """有待办时零点计划以 PlanDayClient(给的客户端, 清单的阶段身份) 发送：桩收到的请求与底稿逐值相同而不是同一对象，
    登记一份计划；调用写法与数据集一侧相同。"""
    built: list = []
    drafts: list = []

    class _Recorded(midnight.PlanDayClient):
        def __init__(self, model_client, model_identity):
            super().__init__(model_client, model_identity)
            built.append((model_client, model_identity))

        async def call(self, request):
            drafts.append(request)
            return await super().call(request)

    monkeypatch.setattr(midnight, "PlanDayClient", _Recorded)
    engine = engine_factory()
    manifest = engine.context.store.manifest
    stub = StubPlanDayClient(manifest["stage_model_identity"])
    registered = await midnight.settle_pending_day_plans(engine, client=stub)
    assert [item["agent_id"] for item in registered] == [1]
    ((model_client, model_identity),) = built
    assert model_client is stub and model_identity == manifest["stage_model_identity"]
    (draft,) = drafts
    (stored,) = [row for row in _events(engine, "model_request") if row.data.get("purpose") == "plan_day"]
    assert stub.calls == [draft] and stub.calls[0] is not draft and stored.data["request"] == draft
    assert len(_environment_kinds(engine, "day_plan_generated")) == 1


async def test_new_run_creates_workspaces_then_the_engine_then_the_initial_checkpoint(tmp_path, monkeypatch, opened):
    """新建先建人物工作区、再建引擎；返回时工作区与运行库都在，恰有一个检查点，状态阶段最后一次是 checkpointing。"""
    order: list[str] = []
    original_engine, original_workspaces = run_host.LifeEngine, run_host.create_workspaces

    def workspaces(run_dir, agent_ids):
        order.append("workspaces")
        return original_workspaces(run_dir, agent_ids)

    def create(*arguments):
        order.append("engine")
        return original_engine.create(*arguments)

    monkeypatch.setattr(run_host, "create_workspaces", workspaces)
    monkeypatch.setattr(run_host, "LifeEngine", SimpleNamespace(create=create, open=original_engine.open))
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    assert order == ["workspaces", "engine"]
    assert agent_workspace(fresh.run_dir, 1).is_dir() and (fresh.run_dir / "footprints.sqlite").is_file()
    assert len(_events(host.engine, "checkpoint_created")) == 1
    assert json.loads((fresh.run_dir / "status.json").read_text(encoding="utf-8"))["phase"] == "checkpointing"


async def test_existing_agent_workspace_stops_before_the_engine_is_created(tmp_path, opened):
    """人物工作区已存在：新建在建引擎之前失败，没有运行库，引擎属性仍为空。"""
    fresh = _fresh(tmp_path)
    agent_workspace(fresh.run_dir, 1).mkdir(parents=True)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(FileExistsError):
        await host.start_new(fresh.identity, fresh.stage, fresh.options)
    assert not (fresh.run_dir / "footprints.sqlite").exists() and host.engine is None


async def test_start_time_disagreement_closes_the_engine_without_recording_a_failure(tmp_path, monkeypatch, opened):
    """引擎时钟与配置的起始时刻不符：抛协议错误、关闭并置空引擎，库里没有 run_failed。"""
    original = run_host.LifeEngine

    def create(*arguments):
        engine = original.create(*arguments)
        state = engine.context.state
        engine.context.state = state.model_copy(update={"current_time": state.current_time + 3600})
        return engine

    monkeypatch.setattr(run_host, "LifeEngine", SimpleNamespace(create=create, open=original.open))
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    with pytest.raises(ProtocolError) as raised:
        await host.start_new(fresh.identity, fresh.stage, fresh.options)
    assert str(raised.value) == "Run host start time disagrees with the engine"
    assert host.engine is None
    assert "run_failed" not in _stored_types(fresh.run_dir)


def _counterfactual_branch(engine_factory, tmp_path: Path) -> SimpleNamespace:
    """父运行观察一次、取检查点，按反事实元数据恢复出分支（库与人物工作区），返回干预与它的规范身份。"""
    parent = engine_factory()
    observed = parent.observe(context(parent, "cf-source"))
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=654_321,
        hypothesis="Higher liquidity changes purchase choices.", source_event_ids=(observed["observation_event_id"],))
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
    identity = parent.context.store.manifest["stage_model_identity"]
    return SimpleNamespace(parent=parent, branch=branch, intervention=intervention, digest=digest, identity=identity,
                           records=records, agents={1: person_inputs(parent.context.inputs.persona, identity)})


async def test_counterfactual_start_checks_identity_time_and_version_before_its_checkpoint(
        engine_factory, tmp_path, opened):
    """反事实启动：收据摘要等于给的身份、余额等于干预值；干预让世界版本恰好加一、时刻不变；干预检查点在首轮之前，
    随后补了零点计划；同一分支再施加一次干预被引擎拒绝。"""
    made = _counterfactual_branch(engine_factory, tmp_path)
    client = _MidnightClient(made.identity)
    host = _branch_host(opened, made, client)
    world = _stored_world(made.branch)
    receipt = await host.start_counterfactual(made.intervention, made.digest)
    assert receipt.intervention_sha256 == made.digest and receipt.after_balance_fen == 654_321
    (applied,) = _environment_kinds(host.engine, "counterfactual_intervention")
    assert applied.state_version_after == world["version"] + 1 and applied.occurred_at == world["current_time"]
    rows = _events(host.engine)
    (created,) = [row for row in rows if row.event_type == "checkpoint_created" and row.event_seq > applied.event_seq]
    assert created.state_version_after == world["version"] + 1 and created.occurred_at == world["current_time"]
    assert [row.event_seq > created.event_seq for row in _environment_kinds(host.engine, "day_plan_generated")] == [
        True]
    assert client.order == ["plan_day"]
    assert not any(row.event_type == "decision_started" and row.event_seq > applied.event_seq for row in rows)
    with pytest.raises(ProtocolError):
        host.engine.apply_counterfactual(made.intervention)


def _skew_second_state(monkeypatch, check: str) -> None:
    """让宿主打开的引擎第二次给出的调度状态比第一次晚一秒（time），或世界版本比第一次多二（version）。"""
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
    """收据摘要不符、施加前后时刻不同、世界版本不是恰好加一：各以原文抛 RuntimeError，库里有原因为它的 repr 的
    run_failed，没有干预检查点。"""
    texts = {"identity": "Environment confirmed a different counterfactual identity",
             "time": "Counterfactual intervention changed simulation time",
             "version": "Counterfactual intervention did not advance one world version"}
    made = _counterfactual_branch(engine_factory, tmp_path)
    if check != "identity":
        _skew_second_state(monkeypatch, check)
    host = _branch_host(opened, made, _MidnightClient(made.identity))
    checkpoints = _stored_types(made.branch).count("checkpoint_created")
    with pytest.raises(RuntimeError) as raised:
        await host.start_counterfactual(made.intervention, "0" * 64 if check == "identity" else made.digest)
    assert str(raised.value) == texts[check]
    (failed,) = _events(host.engine, "run_failed")
    assert failed.data["error"] == repr(raised.value)
    assert len(_events(host.engine, "checkpoint_created")) == checkpoints


async def test_new_run_failure_after_the_engine_starts_records_run_failed(tmp_path, opened):
    """新建运行的初始检查点之后零点计划失败：start_new 抛出同一对象，run_failed 的原因是它的 repr、round_id 为空。"""
    failure = ConnectionError("network is down")
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage, plan_raises=failure))
    with pytest.raises(ConnectionError) as raised:
        await host.start_new(fresh.identity, fresh.stage, fresh.options)
    assert raised.value is failure
    (failed,) = _events(host.engine, "run_failed")
    assert failed.data["error"] == repr(failure) and failed.data["round_id"] is None


@pytest.mark.parametrize("moment", ["initial", "step"])
async def test_checkpoint_failure_records_run_failed(tmp_path, monkeypatch, opened, moment):
    """取快照失败：初始检查点时 start_new、步进时 run() 抛出同一对象；库里有原因为它的 repr 的 run_failed，没有多出检查点。"""
    failure = OSError("checkpoint failed")
    _scripted(monkeypatch)
    fresh = _fresh(tmp_path)
    host = _host(opened, fresh, _MidnightClient(fresh.stage))
    if moment == "initial":
        monkeypatch.setattr(run_host, "snapshot_workspaces", _raiser(failure))
        with pytest.raises(OSError) as raised:
            await host.start_new(fresh.identity, fresh.stage, fresh.options)
    else:
        await host.start_new(fresh.identity, fresh.stage, fresh.options)
        monkeypatch.setattr(run_host, "snapshot_workspaces", _raiser(failure))
        with pytest.raises(OSError) as raised:
            await host.run()
    assert raised.value is failure
    (failed,) = _events(host.engine, "run_failed")
    assert failed.data["error"] == repr(failure)
    assert len(_events(host.engine, "checkpoint_created")) == (0 if moment == "initial" else 1)


async def test_status_phases_follow_the_round_advance_and_checkpoint(tmp_path, monkeypatch, opened):
    """状态阶段由宿主写：新建得 checkpointing；同秒再决策的一步得 deciding、resolving、checkpointing；有决策并推进的
    一步得 deciding、resolving、advancing、checkpointing；状态都是 running。"""
    _scripted(monkeypatch, requests=[_REVISE])
    written: list = []
    original = run_host.write_status

    def recorded(run_dir, status, phase, *rest):
        written.append((status, phase))
        original(run_dir, status, phase, *rest)

    monkeypatch.setattr(run_host, "write_status", recorded)
    fresh = _fresh(tmp_path)
    host = await _started(opened, fresh, _MidnightClient(fresh.stage))
    assert written == [("running", "checkpointing")]
    written.clear()
    await host.step()
    assert written == [("running", phase) for phase in ("deciding", "resolving", "checkpointing")]
    written.clear()
    await host.step()
    assert written == [("running", phase) for phase in ("deciding", "resolving", "advancing", "checkpointing")]
