"""LifeEnv 生命周期接口的进程内直驱测试（C4a 覆盖率单元）。

经 AS2 环境接口在进程内直接驱动 ``custom/envs/life_env.py`` 的全部
EnvBase 生命周期方法（init/begin/observe/submit/commit/abort/
scheduling_state/record_*/checkpoint/step/fail_run/apply_counterfactual/
to_workspace/restore/from_workspace/close），并覆盖初始化校验、工具面
fail-closed、检查点身份校验与 close 双重故障分支。只新增测试，不改生产代码。
"""

from __future__ import annotations

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
from agentsociety2.agent.base import ModelCallEvidence, MemoryUpdateEvidence
from agentsociety2.society import DecisionContext, DirectToolCall, SocietyCheckpointRequest
from custom.envs.life_env import LifeEnv
from worldmock.errors import ProtocolError
from worldmock.life.models import (
    CounterfactualIntervention,
    ModelIdentity,
    RunIdentity,
    StartBasicActivityRequest,
)
from worldmock.life.scenario import load_scenario

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")


def _identity(scenario, tag: str) -> RunIdentity:
    """形成与 tests/life 工厂同口径的运行身份（隔离运行目录）。"""
    run_id = f"envcov-{tag}-{scenario.scenario_id}"
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        code_sha256="4" * 64,
        model_identity=ModelIdentity(
            provider="deepseek",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )


def _start(scenario) -> datetime:
    """场景起始时刻（带时区）。"""
    return datetime.fromtimestamp(scenario.start_at.timestamp(),
                                  ZoneInfo(scenario.timezone))


async def _new_env(tmp_path: Path, tag: str) -> LifeEnv:
    """建立并初始化一个全新运行的 LifeEnv。"""
    scenario, _ = load_scenario(_SCENARIO)
    env = LifeEnv(scenario_path=str(_SCENARIO),
                  run_dir=str(tmp_path / f"run-{tag}"),
                  run_identity=_identity(scenario, tag).model_dump(mode="json"))
    await env.init(_start(scenario))
    return env


def _context_for(env: LifeEnv, round_id: str, agent_id: int = 1) -> DecisionContext:
    """按引擎当前状态形成宿主决策上下文。"""
    timezone = ZoneInfo(env.engine.scenario.timezone)
    observed = datetime.fromtimestamp(env.engine.state.current_time, timezone)
    return DecisionContext(env.engine.state.run_id, round_id,
                           f"decision-{round_id}-{agent_id}", agent_id,
                           observed, env.engine.state.version)


# ---------------------------------------------------------------------------
# 一、init 校验与工具面 fail-closed（修订合同 L1606/L1624-1626）
# ---------------------------------------------------------------------------


async def test_init_requires_paths_identity_and_fresh_state(tmp_path) -> None:
    """缺路径、缺身份、重复初始化与起始时钟不符都拒绝。"""
    scenario, _ = load_scenario(_SCENARIO)
    with pytest.raises(ProtocolError, match="requires scenario_path and run_dir"):
        await LifeEnv().init(_start(scenario))

    with pytest.raises(ProtocolError, match="requires explicit run_identity"):
        env = LifeEnv(scenario_path=str(_SCENARIO),
                      run_dir=str(tmp_path / "no-identity"))
        await env.init(_start(scenario))

    env = await _new_env(tmp_path, "fresh")
    try:
        with pytest.raises(ProtocolError, match="already initialized"):
            await env.init(_start(scenario))
    finally:
        await env.close()

    disagree = LifeEnv(scenario_path=str(_SCENARIO),
                       run_dir=str(tmp_path / "disagree"),
                       run_identity=_identity(scenario, "disagree").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="start time disagrees"):
        await disagree.init(_start(scenario) + timedelta(seconds=60))
    assert disagree.engine is None  # 引擎已关闭并置空


async def test_open_existing_restores_prior_run(tmp_path) -> None:
    """open_existing=true 只能打开已有不可变运行目录。"""
    scenario, _ = load_scenario(_SCENARIO)
    first = await _new_env(tmp_path, "reopen")
    run_id = first.engine.state.run_id
    await first.close()

    reopened = LifeEnv(scenario_path=str(_SCENARIO),
                       run_dir=str(tmp_path / "run-reopen"), open_existing=True)
    await reopened.init(_start(scenario))
    try:
        assert reopened.engine.state.run_id == run_id
    finally:
        await reopened.close()


async def test_registered_tool_surface_fails_closed(tmp_path, monkeypatch) -> None:
    """环境或人物侧登记工具面漂移都在 init 时失败，不留给运行中 KeyError。"""
    scenario, _ = load_scenario(_SCENARIO)
    monkeypatch.setattr(life_env_module, "_REVISION_CONTRACT_TOOLS",
                        frozenset({"plan_day", "revise_day_plan", "send_message",
                                   "start_social_activity", "ghost_tool"}))
    drifted = LifeEnv(scenario_path=str(_SCENARIO),
                      run_dir=str(tmp_path / "drift-env"),
                      run_identity=_identity(scenario, "drift").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="missing revision tools"):
        await drifted.init(_start(scenario))
    if drifted.engine is not None:
        drifted.engine.close()

    monkeypatch.setattr(life_env_module, "_REVISION_CONTRACT_TOOLS",
                        frozenset({"plan_day", "revise_day_plan", "send_message",
                                   "start_social_activity"}))
    monkeypatch.setattr(life_agent_module, "life_action_tool_names",
                        lambda: frozenset({"start_basic_activity"}))
    mismatch = LifeEnv(scenario_path=str(_SCENARIO),
                       run_dir=str(tmp_path / "mismatch-env"),
                       run_identity=_identity(scenario, "mismatch").model_dump(mode="json"))
    with pytest.raises(ProtocolError, match="disagrees with the LifeAgent action tool set"):
        await mismatch.init(_start(scenario))
    if mismatch.engine is not None:
        mismatch.engine.close()


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


async def test_full_round_drives_engine_interfaces(tmp_path) -> None:
    """一轮真实决策覆盖 begin/observe/submit/commit/abort/记录/检查点/推进。"""
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

        receipt = await env.submit_action(DirectToolCall(
            contexts[-1], "action-round-1", "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait",
                                      planned_duration_seconds=60)))
        assert receipt.action_id == "action-round-1"
        results = await env.commit_decision_round("round-1")
        assert [item.action_id for item in results] == ["action-round-1"]

        memory_context = _context_for(env, "round-memory")
        await env.record_memory_update(MemoryUpdateEvidence(
            memory_context, "append_episodes", ("m-round-1",),
            "a" * 64, "b" * 64, "c" * 64, False))

        workspaces = tuple(
            (agent_id, str(tmp_path / f"round-ws-{agent_id}"))
            for agent_id in engine.store.manifest["agent_ids"])
        for _agent_id, path in workspaces:
            workspace = Path(path)
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "MEMORY.md").write_text("# round memory\n", encoding="utf-8")
        now = datetime.fromtimestamp(engine.state.current_time,
                                     ZoneInfo(engine.scenario.timezone))
        checkpoint = await env.checkpoint(SocietyCheckpointRequest(
            run_id=engine.state.run_id, round_id="round-1", current_time=now,
            agent_workspaces=workspaces))
        assert checkpoint.checkpoint_id

        state = await env.scheduling_state()
        guard = 0
        while state.ready_agent_ids:
            round_id = f"round-drain-{guard}"
            contexts = [_context_for(env, round_id, agent_id)
                        for agent_id in state.ready_agent_ids]
            await env.begin_decision_round(tuple(contexts))
            for context in reversed(contexts):
                await env.submit_action(DirectToolCall(
                    context, f"action-{round_id}-{context.agent_id}",
                    "start_basic_activity",
                    StartBasicActivityRequest(activity_type="wait",
                                              planned_duration_seconds=60)))
            await env.commit_decision_round(round_id)
            state = await env.scheduling_state()
            guard += 1
            assert guard < 8, "drain loop did not converge"
        assert state.next_event_at is not None
        tick = int(state.next_event_at.timestamp()) - engine.state.current_time
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
            for agent_id in engine.store.manifest["agent_ids"])
        for _agent_id, path in workspaces:
            Path(path).mkdir(parents=True, exist_ok=True)
        now = datetime.fromtimestamp(engine.state.current_time,
                                     ZoneInfo(engine.scenario.timezone))
        stale = SocietyCheckpointRequest(
            run_id="other-run", round_id="round-ck", current_time=now,
            agent_workspaces=workspaces)
        with pytest.raises(ProtocolError, match="identity or time is stale"):
            await env.checkpoint(stale)

        incomplete = SocietyCheckpointRequest(
            run_id=engine.state.run_id, round_id="round-ck", current_time=now,
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
        events = env.engine.store.read_events(env.engine.store.last_event_seq)
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
        snapshots = {f"agent:{agent_id}": engine.store.assets.put_json(
            {"agent_id": agent_id}, {"kind": "envcov_counterfactual"})
            for agent_id in engine.store.manifest["agent_ids"]}
        checkpoint = engine.checkpoint(snapshots)
        payload = {"tool_name": "counterfactual_intervention",
                   "request": intervention.model_dump(mode="json")}
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        store = engine.store.restore(checkpoint.checkpoint_id, branch_dir, {
            "branch_kind": "counterfactual", "intervention_sha256": digest})
        store.close()
    finally:
        await parent.close()

    scenario, _ = load_scenario(_SCENARIO)
    branch = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(branch_dir),
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
    run_dir = Path(env.engine.store.run_dir)
    run_id = env.engine.state.run_id
    try:
        await env.to_workspace()
        assert env._workspace_root == run_dir
        await env.to_workspace(run_dir)
        with pytest.raises(ProtocolError, match="must be its footprint run directory"):
            await env.to_workspace(tmp_path / "elsewhere")
    finally:
        await env.close()

    with pytest.raises(ProtocolError, match="restore requires scenario_path"):
        await LifeEnv().restore(run_dir)

    first = await _new_env(tmp_path, "restore-first")
    first_run_dir = Path(first.engine.store.run_dir)
    first_run_id = first.engine.state.run_id
    second = await _new_env(tmp_path, "restore-second")
    assert await second.restore(first_run_dir) is True
    assert second.engine is not None
    assert second.engine.state.run_id == first_run_id
    await second.close()
    await first.close()

    restored = LifeEnv(scenario_path=str(_SCENARIO))
    assert await restored.restore(run_dir) is True
    try:
        assert restored.engine.state.run_id == run_id
        assert restored.t is not None
    finally:
        await restored.close()

    from_workspace = await LifeEnv.from_workspace(
        run_dir, scenario_path=str(_SCENARIO))
    try:
        assert from_workspace.engine.state.run_id == run_id
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
    events = engine.store.read_events(engine.store.last_event_seq)
    assert any(event.event_type == "run_failed" for event in events)

    def broken_fail_run(error: str) -> None:
        raise OSError("journal unavailable")

    monkeypatch.setattr(engine, "fail_run", broken_fail_run)
    with pytest.raises(ExceptionGroup, match="close and failure recording both failed"):
        await env.close()
    engine.store.close()
