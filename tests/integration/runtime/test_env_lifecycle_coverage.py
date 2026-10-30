"""LifeEnv 生命周期接口的进程内直驱测试（C4a 覆盖率单元）。

经 AS2 环境接口在进程内直接驱动 ``custom/envs/life_env.py`` 的全部
EnvBase 生命周期方法（init/begin/observe/submit/commit/abort/
scheduling_state/record_*/checkpoint/step/fail_run/apply_counterfactual/
to_workspace/restore/from_workspace/close），并覆盖初始化校验、工具面
fail-closed、检查点身份校验与 close 双重故障分支。只新增测试，不改生产代码。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.recording_engine.storage.checkpoint import restore_branch

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import custom.agents.life_agent as life_agent_module
import custom.envs.life_env as life_env_module
import worldmock.runtime.lifecycle.runner as runtime_module
from agentsociety2.agent.base import ModelCallEvidence, MemoryUpdateEvidence
from agentsociety2.society import DecisionContext, DirectToolCall, SocietyCheckpointRequest
from custom.envs.life_env import LifeEnv
from tests.life.conftest import MAIN_TOOL, StubMemoryDigestClient, StubPlanDayClient, do_at
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.identity import ModelIdentity, RunIdentity
from worldmock.state.interaction.requests import REQUEST_MODELS
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.integration.runtime_fixtures import current_run_identity

_SCENARIO = ENVIRONMENT_ROOT


@pytest.fixture(autouse=True)
def actual_service_metadata(monkeypatch):
    metadata_service(monkeypatch)


def _identity(frozen, tag: str) -> RunIdentity:
    """当前完整环境身份。"""
    return current_run_identity(frozen, f"envcov-{tag}")


def _start(frozen) -> datetime:
    """完整环境的明确起始时刻。"""
    return frozen.config.start_time


async def _new_env(tmp_path: Path, tag: str) -> LifeEnv:
    """建立并初始化一个全新运行的 LifeEnv。"""
    scenario = inputs()
    env = LifeEnv(environment_root=str(_SCENARIO),
                  run_dir=str(tmp_path / f"run-{tag}"),
                  run_identity=_identity(scenario, tag).model_dump(mode="json"))
    await env.init(_start(scenario))
    return env


def _context_for(env: LifeEnv, round_id: str, agent_id: int = 1) -> DecisionContext:
    """按引擎当前状态形成宿主决策上下文。"""
    timezone = ZoneInfo(env.engine.context.config.timezone)
    observed = datetime.fromtimestamp(env.engine.context.state.current_time, timezone)
    return DecisionContext(env.engine.context.state.run_id, round_id,
                           f"decision-{round_id}-{agent_id}", agent_id,
                           observed, env.engine.context.state.version)


def _inject_plan_day_client(monkeypatch, order: list[str] | None = None) -> list[StubPlanDayClient]:
    """在 worldmock.runtime.lifecycle.runner 上替换零点计划客户端类（同替换 ContactReplyClient），返回按构造顺序记录的桩。

    LifeEnv 的检查点（启动路径）与零点推进经它生成确定性空清单，不调用真实模型（时间与能量规格第四章）；给出 order 时每次
    调用在它末尾记一个 plan_day。
    """
    built: list[StubPlanDayClient] = []

    class PlanDayStub(StubPlanDayClient):
        def __init__(self, model_identity):
            super().__init__(model_identity)
            built.append(self)

        async def call(self, request):
            if order is not None:
                order.append("plan_day")
            return await super().call(request)

    monkeypatch.setattr(runtime_module, "PlanDayClient", PlanDayStub)
    return built


def _inject_memory_digest_client(monkeypatch, order: list[str], raises: BaseException | None = None) -> list:
    """与 _inject_plan_day_client 并列，替换回顾客户端类：每次调用在共用的 order 末尾记一个 memory_digest；raises 非空时
    每次调用都抛出它。"""
    built: list[StubMemoryDigestClient] = []

    class MemoryDigestStub(StubMemoryDigestClient):
        def __init__(self, model_identity):
            super().__init__(model_identity, raises=raises)
            built.append(self)

        async def call(self, request):
            order.append("memory_digest")
            return await super().call(request)

    monkeypatch.setattr(runtime_module, "MemoryDigestClient", MemoryDigestStub)
    return built


def _wrap_midnight_tasks(monkeypatch, order: list[str]) -> None:
    """把 custom.envs.life_env 的两个零点任务换成先记下调用次序、再转调原函数的包装。"""
    for name in ("settle_pending_memory_digests", "settle_pending_day_plans"):
        original = getattr(life_env_module, name)

        async def wrapped(engine, original=original, name=name):
            order.append(name)
            return await original(engine)

        monkeypatch.setattr(life_env_module, name, wrapped)


def _late_environment(tmp_path: Path) -> Path:
    """样例环境的副本：2026-01-05 23:00 开始，01-08 08:00 截止。"""
    import shutil
    import tomlkit

    root = tmp_path / "late-environment"
    shutil.copytree(_SCENARIO, root)
    config = tomlkit.parse((root / "config.toml").read_text(encoding="utf-8"))
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T08:00:00+08:00")
    (root / "config.toml").write_text(tomlkit.dumps(config), encoding="utf-8", newline="\n")
    return root


async def _late_env(tmp_path: Path, tag: str) -> LifeEnv:
    """在 _late_environment 上新建并初始化一个运行，人物 23:00 开始睡 480 分钟。"""
    from tests.life.conftest import sleep, stage_day_plan
    from tests.life.environment_runtime import act
    from worldmock.configuration.inputs.environment import load_environment

    root = _late_environment(tmp_path)
    frozen = load_environment(root)
    env = LifeEnv(environment_root=str(root), run_dir=str(tmp_path / f"run-{tag}"),
                  run_identity=_identity(frozen, tag).model_dump(mode="json"))
    await env.init(frozen.config.start_time)
    stage_day_plan(env.engine, submitted=True)
    act(env.engine, "night", sleep(480))
    return env


async def _step_to_midnight(env: LifeEnv) -> None:
    """经 LifeEnv.step 逐边界推进，直到时钟到达下一个本地零点。"""
    engine = env.engine
    while datetime.fromtimestamp(engine.context.state.current_time,
                                 ZoneInfo(engine.context.config.timezone)).hour != 0:
        state = await env.scheduling_state()
        assert not state.ready_agent_ids
        await env.step(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)


# ---------------------------------------------------------------------------
# 一、init 校验与工具面 fail-closed（docs/design/06-agent.md「日程骨架」、docs/design/04-simulation-engine.md「社交最小可行版」）
# ---------------------------------------------------------------------------


async def test_init_requires_paths_identity_and_fresh_state(tmp_path) -> None:
    """缺路径、缺身份、重复初始化与起始时钟不符都拒绝。"""
    scenario = inputs()
    with pytest.raises(ProtocolError, match="requires environment_root and run_dir"):
        await LifeEnv().init(_start(scenario))

    with pytest.raises(ProtocolError, match="requires explicit run_identity"):
        env = LifeEnv(environment_root=str(_SCENARIO),
                      run_dir=str(tmp_path / "no-identity"))
        await env.init(_start(scenario))

    env = await _new_env(tmp_path, "fresh")
    try:
        with pytest.raises(ProtocolError, match="already initialized"):
            await env.init(_start(scenario))
    finally:
        await env.close()

    disagree = LifeEnv(environment_root=str(_SCENARIO),
                       run_dir=str(tmp_path / "disagree"),
                       run_identity=_identity(scenario, "disagree").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="start time disagrees"):
        await disagree.init(_start(scenario) + timedelta(seconds=60))
    assert disagree.engine is None  # 引擎已关闭并置空


async def test_open_existing_restores_prior_run(tmp_path) -> None:
    """open_existing=true 只能打开已有不可变运行目录。"""
    scenario = inputs()
    first = await _new_env(tmp_path, "reopen")
    run_id = first.engine.context.state.run_id
    await first.close()

    reopened = LifeEnv(environment_root=str(_SCENARIO),
                       run_dir=str(tmp_path / "run-reopen"), open_existing=True)
    await reopened.init(_start(scenario))
    try:
        assert reopened.engine.context.state.run_id == run_id
    finally:
        await reopened.close()


async def test_registered_tool_surface_fails_closed(tmp_path, monkeypatch) -> None:
    """两侧工具面恰为七个请求工具，只读查询恰为回看任务与检索记忆；环境一侧多出一个名字、人物一侧多出已删除的
    query_world、人物一侧的只读查询多出一个名字，都在 init 时失败，不留给运行中 KeyError。"""
    surface = life_env_module._STABLE_TOOL_SURFACE
    assert surface == frozenset(REQUEST_MODELS) == life_agent_module.life_action_tool_names()
    assert len(surface) == 7
    scenario = inputs()
    monkeypatch.setattr(life_env_module, "_STABLE_TOOL_SURFACE",
                        life_env_module._STABLE_TOOL_SURFACE | {"ghost_tool"})
    drifted = LifeEnv(environment_root=str(_SCENARIO),
                      run_dir=str(tmp_path / "drift-env"),
                      run_identity=_identity(scenario, "drift").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match=r"^LifeEnv registered tool surface disagrees with the stable tool surface: "
                                            r"\['ghost_tool'\]$"):
        await drifted.init(_start(scenario))
    if drifted.engine is not None:
        drifted.engine.close()

    monkeypatch.undo()
    metadata_service(monkeypatch)
    monkeypatch.setattr(life_env_module, "life_action_tool_names",
                        lambda: life_env_module._STABLE_TOOL_SURFACE | {"query_world"})
    mismatch = LifeEnv(environment_root=str(_SCENARIO),
                       run_dir=str(tmp_path / "mismatch-env"),
                       run_identity=_identity(scenario, "mismatch").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="^LifeEnv tool surface disagrees with the LifeAgent action tool set$"):
        await mismatch.init(_start(scenario))
    if mismatch.engine is not None:
        mismatch.engine.close()

    monkeypatch.undo()
    metadata_service(monkeypatch)
    assert life_agent_module.life_query_tool_names() == frozenset(life_env_module._READ_QUERY_MODELS) == {
        "recall_task_summary", "search_memory"}
    monkeypatch.setattr(life_env_module, "life_query_tool_names",
                        lambda: frozenset(life_env_module._READ_QUERY_MODELS) | {"memory_recent"})
    queries = LifeEnv(environment_root=str(_SCENARIO),
                      run_dir=str(tmp_path / "query-env"),
                      run_identity=_identity(scenario, "query").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="^LifeEnv read query surface disagrees with the LifeAgent query tool set$"):
        await queries.init(_start(scenario))
    if queries.engine is not None:
        queries.engine.close()


async def test_life_interfaces_require_initialized_engine() -> None:
    """init/restore 之前调用生活接口一律拒绝。"""
    env = LifeEnv()
    with pytest.raises(ProtocolError, match="LifeEnv is not initialized"):
        await env.observe(SimpleNamespace())
    with pytest.raises(ProtocolError, match="LifeEnv is not initialized"):
        await env.begin_decision_round((SimpleNamespace(),))
    with pytest.raises(ProtocolError, match="LifeEnv is not initialized"):
        await env.checkpoint(SimpleNamespace())


# ---------------------------------------------------------------------------
# 二、决策轮、证据、检查点与推进的进程内直驱
# ---------------------------------------------------------------------------


async def test_full_round_drives_engine_interfaces(tmp_path, monkeypatch) -> None:
    """一轮真实决策覆盖 begin/observe/submit/commit/abort/记录/检查点/推进；检查点之后执行零点计划后台任务。"""
    plan_clients = _inject_plan_day_client(monkeypatch)
    env = await _new_env(tmp_path, "round")
    engine = env.engine
    try:
        state = await env.scheduling_state()
        assert state.ready_agent_ids
        ready = list(state.ready_agent_ids)
        contexts = [_context_for(env, "round-1", agent_id) for agent_id in ready]
        assert await env.begin_decision_round(tuple(contexts)) == "round-1"

        data = await env.observe(contexts[-1])
        assert data["observation_event_id"]

        evidence = ModelCallEvidence(
            contexts[-1], f"{contexts[-1].decision_id}:model:0001:attempt:1",
            "request", 1, {"model": "deepseek-flash", "messages": []})
        await env.record_model_exchange(evidence)

        identity = f"{contexts[-1].decision_id}:action:0001"
        receipt = await env.submit_action(DirectToolCall(
            contexts[-1], identity, MAIN_TOOL, do_at("家", "测试休息")))
        assert receipt.action_id == identity
        results = await env.commit_decision_round("round-1")
        assert [item.action_id for item in results] == [identity]

        from dataclasses import replace
        memory_context = replace(contexts[-1], world_state_version=engine.context.state.version)
        files = {"memory_before_sha256": "a" * 64, "memory_sha256": "b" * 64, "episodes_sha256": "c" * 64,
                 "state_sha256": "d" * 64, "consolidated": False,
                 "observation_event_id": data["observation_event_id"], "verified_event_ids": ()}
        # 开始在家休息不是记忆写入点（只在结束活动时写记忆）：写入条目按协议违约拒绝，
        # 只刷新摘要照常留证，写入点列表为空。
        with pytest.raises(ProtocolError, match="Memory update evidence is invalid"):
            await env.record_memory_update(MemoryUpdateEvidence(
                context=memory_context, operation="append_episodes", created_episode_ids=("m-round-1",),
                merged_episode_ids=(), episodes=({"id": "m-round-1", "type": "reflection",
                                               "text": "我想休息一下。", "epistemic_status": "subjective"},),
                **files))
        await env.record_memory_update(MemoryUpdateEvidence(
            context=memory_context, operation="refresh_summary", created_episode_ids=(),
            merged_episode_ids=(), episodes=(), **files))
        updated = engine.context.store.read_agent_events("memory_updated", memory_context.agent_id)
        assert [(event.data["operation"], event.data["trigger"], event.data["decision_id"])
                for event in updated] == [("refresh_summary", [], memory_context.decision_id)]

        workspaces = tuple(
            (agent_id, str(tmp_path / f"round-ws-{agent_id}"))
            for agent_id in engine.context.store.manifest["agent_ids"])
        for _agent_id, path in workspaces:
            workspace = Path(path)
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "MEMORY.md").write_text("# round memory\n", encoding="utf-8")
        now = datetime.fromtimestamp(engine.context.state.current_time,
                                     ZoneInfo(engine.context.config.timezone))
        assert plan_clients == []  # init 与决策轮都不执行零点计划
        checkpoint = await env.checkpoint(SocietyCheckpointRequest(
            run_id=engine.context.state.run_id, round_id="round-1", current_time=now,
            agent_workspaces=workspaces))
        assert checkpoint.checkpoint_id
        # 新建运行的检查点提交之后，零点计划为缺当日计划的每个人物生成一次（新计划不进本次检查点）。
        assert [len(client.calls) for client in plan_clients] == [len(engine.context.state.agents)]
        assert all(agent.day_plan is not None for agent in engine.context.state.agents.values())
        assert all(agent["day_plan"] is None for agent in checkpoint.state["agents"].values())

        state = await env.scheduling_state()
        guard = 0
        while state.ready_agent_ids:
            round_id = f"round-drain-{guard}"
            contexts = [_context_for(env, round_id, agent_id)
                        for agent_id in state.ready_agent_ids]
            await env.begin_decision_round(tuple(contexts))
            for context in reversed(contexts):
                await env.submit_action(DirectToolCall(
                    context, f"{context.decision_id}:action:0001", MAIN_TOOL, do_at("家", "测试休息")))
            await env.commit_decision_round(round_id)
            state = await env.scheduling_state()
            guard += 1
            assert guard < 8, "drain loop did not converge"
        assert state.next_event_at is not None
        tick = int(state.next_event_at.timestamp()) - engine.context.state.current_time
        await env.step(tick, state.next_event_at)
        assert env.t == state.next_event_at

        abort_round = "round-abort"
        contexts = [_context_for(env, abort_round, agent_id)
                    for agent_id in (await env.scheduling_state()).ready_agent_ids]
        if contexts:
            await env.begin_decision_round(tuple(contexts))
            await env.abort_decision_round(abort_round, "injected abort")
            assert (await env.scheduling_state()).ready_agent_ids
    finally:
        await env.close()


async def test_checkpoint_identity_is_validated(tmp_path) -> None:
    """检查点请求类型、身份/时钟与人物集合不完整都拒绝。"""
    env = await _new_env(tmp_path, "checkpoint")
    engine = env.engine
    try:
        with pytest.raises(ProtocolError, match="must use SocietyCheckpointRequest"):
            await env.checkpoint(SimpleNamespace())

        workspaces = tuple(
            (agent_id, str(tmp_path / f"ck-ws-{agent_id}"))
            for agent_id in engine.context.store.manifest["agent_ids"])
        for _agent_id, path in workspaces:
            Path(path).mkdir(parents=True, exist_ok=True)
        now = datetime.fromtimestamp(engine.context.state.current_time,
                                     ZoneInfo(engine.context.config.timezone))
        stale = SocietyCheckpointRequest(
            run_id="other-run", round_id="round-ck", current_time=now,
            agent_workspaces=workspaces)
        with pytest.raises(ProtocolError, match="identity or time is stale"):
            await env.checkpoint(stale)

        incomplete = SocietyCheckpointRequest(
            run_id=engine.context.state.run_id, round_id="round-ck", current_time=now,
            agent_workspaces=((999, str(tmp_path / "ck-ws-999")),))
        with pytest.raises(ProtocolError, match="agent set is incomplete"):
            await env.checkpoint(incomplete)
    finally:
        await env.close()


async def test_fail_run_persists_the_failure_evidence(tmp_path) -> None:
    """Society 致命通知落成唯一 run_failed 证据。"""
    env = await _new_env(tmp_path, "fail")
    try:
        await env.fail_run("injected fatal notification")
        events = list(env.engine.context.store.iter_events(env.engine.context.store.last_event_seq))
        assert any(event.event_type == "run_failed" for event in events)
    finally:
        await env.close()


async def test_counterfactual_applies_once_on_fresh_branch(tmp_path) -> None:
    """反事实封闭干预经环境接口在真实分支上提交一次并返回收据。"""
    intervention: CounterfactualIntervention | None = None
    branch_dir = tmp_path / "run-cf-branch"
    parent = await _new_env(tmp_path, "cf-parent")
    try:
        context = _context_for(parent, "cf-source")
        data = await parent.observe(context)
        intervention = CounterfactualIntervention(
            kind="set_balance_fen", agent_id=context.agent_id, balance_fen=654_321,
            hypothesis="Higher liquidity changes purchase choices.",
            source_event_ids=(data["observation_event_id"],))
        engine = parent.engine
        snapshots = {f"agent:{agent_id}": engine.context.store.assets.put_json(
            {"agent_id": agent_id}, {"kind": "envcov_counterfactual"})
            for agent_id in engine.context.store.manifest["agent_ids"]}
        checkpoint = engine.checkpoint(snapshots)
        payload = {"tool_name": "counterfactual_intervention",
                   "request": intervention.model_dump(mode="json")}
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        store = restore_branch(engine.context.store, checkpoint.checkpoint_id, branch_dir, {'branch_kind': 'counterfactual', 'intervention_sha256': digest})
        store.close()
    finally:
        await parent.close()

    scenario = inputs()
    branch = LifeEnv(environment_root=str(_SCENARIO), run_dir=str(branch_dir),
                     open_existing=True)
    await branch.init(_start(scenario))
    try:
        receipt = await branch.apply_counterfactual(intervention)
        assert receipt.after_balance_fen == 654_321
        assert len(receipt.intervention_sha256) == 64
        with pytest.raises(ProtocolError):  # 同一分支只允许一次封闭干预
            await branch.apply_counterfactual(intervention)
    finally:
        await branch.close()


async def test_workspace_binding_and_restore_round_trip(tmp_path) -> None:
    """to_workspace 只接受自身运行目录；restore/from_workspace 恢复已有运行。"""
    env = await _new_env(tmp_path, "workspace")
    run_dir = Path(env.engine.context.store.run_dir)
    run_id = env.engine.context.state.run_id
    try:
        await env.to_workspace()
        assert env._workspace_root == run_dir
        await env.to_workspace(run_dir)
        with pytest.raises(ProtocolError, match="must be its footprint run directory"):
            await env.to_workspace(tmp_path / "elsewhere")
    finally:
        await env.close()

    with pytest.raises(ProtocolError, match="restore requires environment_root"):
        await LifeEnv().restore(run_dir)

    first = await _new_env(tmp_path, "restore-first")
    first_run_dir = Path(first.engine.context.store.run_dir)
    first_run_id = first.engine.context.state.run_id
    second = await _new_env(tmp_path, "restore-second")
    assert await second.restore(first_run_dir) is True
    assert second.engine is not None
    assert second.engine.context.state.run_id == first_run_id
    await second.close()
    await first.close()

    restored = LifeEnv(environment_root=str(_SCENARIO))
    assert await restored.restore(run_dir) is True
    try:
        assert restored.engine.context.state.run_id == run_id
        assert restored.t is not None
    finally:
        await restored.close()

    from_workspace = await LifeEnv.from_workspace(
        run_dir, environment_root=str(_SCENARIO))
    try:
        assert from_workspace.engine.context.state.run_id == run_id
    finally:
        await from_workspace.close()


async def test_close_failure_records_run_failure_or_raises_group(tmp_path,
                                                                 monkeypatch) -> None:
    """close 失败记录 run_failed；记录再失败保留两项异常。"""
    await LifeEnv().close()  # 未初始化时 close 是无副作用出口

    env = await _new_env(tmp_path, "close-fail")
    engine = env.engine

    def broken_close() -> None:
        raise ValueError("disk full while closing")

    monkeypatch.setattr(engine, "close", broken_close)
    with pytest.raises(ValueError, match="disk full while closing"):
        await env.close()
    assert env.engine is not None  # 失败路径不置空引擎
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    assert any(event.event_type == "run_failed" for event in events)

    def broken_fail_run(error: str) -> None:
        raise OSError("journal unavailable")

    monkeypatch.setattr(engine, "fail_run", broken_fail_run)
    with pytest.raises(ExceptionGroup, match="close and failure recording both failed"):
        await env.close()
    engine.context.store.close()



async def test_midnight_step_runs_digests_then_plans_then_rhythm(tmp_path, monkeypatch) -> None:
    """经 LifeEnv.step 过零点：回顾桩的调用早于计划桩；事件序号上回顾登记早于计划登记，计划登记早于零点节律那次提交。"""
    order: list[str] = []
    _inject_memory_digest_client(monkeypatch, order)
    _inject_plan_day_client(monkeypatch, order)
    env = await _late_env(tmp_path, "midnight")
    engine = env.engine
    original = engine.decide_midnight_rhythm
    rhythm: list[int] = []

    def decided():
        order.append("rhythm")
        rhythm.append(engine.context.store.last_event_seq)
        original()

    monkeypatch.setattr(engine, "decide_midnight_rhythm", decided)
    try:
        await _step_to_midnight(env)
        assert order == ["memory_digest", "plan_day", "rhythm"]
        rows = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
        kinds = {row.data.get("kind"): row.event_seq for row in rows if row.event_type == "environment_event"}
        assert kinds["memory_digest_generated"] < kinds["day_plan_generated"] <= rhythm[0]
        assert engine.context.store.last_event_seq > rhythm[0] and not engine.midnight_rhythm_pending
    finally:
        await env.close()


async def test_checkpoint_runs_digests_before_day_plans(tmp_path, monkeypatch) -> None:
    """LifeEnv.checkpoint 在共同检查点之后先调回顾任务、再调零点计划任务，各一次。"""
    order: list[str] = []
    _inject_plan_day_client(monkeypatch)
    _wrap_midnight_tasks(monkeypatch, order)
    env = await _new_env(tmp_path, "checkpoint-order")
    engine = env.engine
    try:
        workspace = tmp_path / "order-ws"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
        now = datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))
        await env.checkpoint(SocietyCheckpointRequest(run_id=engine.context.state.run_id, round_id="order",
                                                      current_time=now, agent_workspaces=((1, str(workspace)),)))
        assert order == ["settle_pending_memory_digests", "settle_pending_day_plans"]
    finally:
        await env.close()


@pytest.mark.parametrize("fails", [False, True])
async def test_resume_init_runs_digests_before_day_plans_and_records_failures(tmp_path, monkeypatch, fails) -> None:
    """父运行推进过零点、只跑零点计划与节律、不跑回顾，再取检查点；从它普通恢复时 init 先调回顾、后调计划；回顾桩抛出时
    库里有 run_failed，异常原样抛出。"""
    from tests.integration.runtime_fixtures import write_agent_workspace
    from tests.life.conftest import drive
    from worldmock.runtime.lifecycle.runner import settle_pending_day_plans

    parent = await _late_env(tmp_path, "parent")
    engine = parent.engine
    while not engine.midnight_rhythm_pending:
        state = engine.scheduling_state()
        engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    drive(settle_pending_day_plans(engine, StubPlanDayClient({"requested_model": "test-model"})))
    engine.decide_midnight_rhythm()
    from worldmock.recording_engine.storage.checkpoint import snapshot_directory

    workspace = write_agent_workspace(tmp_path / "resume-ws")
    point = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    restore_branch(engine.context.store, point.checkpoint_id, tmp_path / "resumed").close()
    moment = datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))
    root = engine.context.inputs.root
    await parent.close()
    order: list[str] = []
    failure = ConnectionError("network is down")
    _inject_memory_digest_client(monkeypatch, [], raises=failure if fails else None)
    _inject_plan_day_client(monkeypatch)
    _wrap_midnight_tasks(monkeypatch, order)
    resumed = LifeEnv(environment_root=str(root), run_dir=str(tmp_path / "resumed"), open_existing=True)
    try:
        if fails:
            with pytest.raises(ConnectionError) as raised:
                await resumed.init(moment)
            assert raised.value is failure and order == ["settle_pending_memory_digests"]
            assert resumed.engine.context.store.has_event("run_failed")
        else:
            await resumed.init(moment)
            assert order == ["settle_pending_memory_digests", "settle_pending_day_plans"]
            rows = list(resumed.engine.context.store.iter_events(resumed.engine.context.store.last_event_seq))
            assert [row.data["period"] for row in rows if row.data.get("kind") == "memory_digest_generated"] == [
                "2026-01-05"]
    finally:
        await resumed.close()
