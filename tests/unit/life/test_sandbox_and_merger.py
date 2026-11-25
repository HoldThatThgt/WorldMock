"""@brief AgentSandbox、BarrierMerger 多人物冲突仲裁、反事实清除挂起与 LifeEngine 全部公开接口测试。"""

from __future__ import annotations

import shutil

import pytest
import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.conftest import action_identity, do_at, search_world, stage_day_plan
from tests.life.environment_runtime import events
from worldmock.foundation.errors import IntegrityError, PreconditionError, ProtocolError
from worldmock.foundation.serialization import sha256_json
from worldmock.recording_engine.events.models import AssetRef
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.simulation_engine.decisions import merger as merger_mod
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, SearchMemoryRequest
from worldmock.state.model.exchange import ModelCallEvidence


def _suspended_stub(wakeup, action_id: str, activity_id: str) -> SuspendedLoopState:
    """@brief 构造最小合法无栈挂起恢复载荷。"""
    return SuspendedLoopState(
        wakeup_id=wakeup.wakeup_id, action_id=action_id, activity_id=activity_id,
        pending_tool_call_id="call-search", messages=({"role": "assistant", "content": None},),
        turn_used=1, memory_search_count=0, accepted_action_ids=(action_id,),
        pending_memories=(), verified_experiences=(), local_rejection_counts={},
        observation_event_id=wakeup.observation["observation_event_id"],
        observation_parts=dict(wakeup.observation_parts), suspended_at=wakeup.observed_at,
        base_layers=(),
    )


def _two_agent_env_dir(tmp_path):
    """@brief 复制样例环境并加入第二名同住处人物以构造并发屏障与冲突仲裁。"""
    root = tmp_path / "two-agents"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    first_path = sorted((root / "personas").glob("*.toml"))[0]
    doc = tomlkit.parse(first_path.read_text(encoding="utf-8"))
    doc["agent_id"] = 2
    doc["name"] = "顾言"
    doc["initial_location"] = doc["home_location"]
    (root / "personas" / "persona_2.toml").write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
    return root


def test_sandbox_queries_exchanges_suspend_and_counterfactual(engine_factory, tmp_path):
    """AgentSandbox 只读检索限次、回看、模型证据配对与孤立异常草稿、挂起与反事实清除循环。"""
    engine = engine_factory()
    stage_day_plan(engine)
    committed_before = engine.context.state.model_dump_json()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    assert not sandbox.can_finish_idle()
    sandbox.record_model_exchange(ModelCallEvidence(
        agent_id=1, wakeup_id=wakeup.wakeup_id, call_id="c1", phase="request", attempt=1,
        request={"messages": []}, response=None, error=None, purpose="agent",
    ))
    with pytest.raises(ProtocolError):
        sandbox.record_model_exchange(ModelCallEvidence(
            agent_id=1, wakeup_id=wakeup.wakeup_id, call_id="c1", phase="response", attempt=1,
            request={"messages": ["tampered"]}, response={}, error=None, purpose="agent",
        ))
    sandbox.record_model_exchange(ModelCallEvidence(
        agent_id=1, wakeup_id=wakeup.wakeup_id, call_id="c1", phase="response", attempt=1,
        request={"messages": []}, response={"ok": True}, error=None, purpose="agent",
    ))
    for _ in range(3):
        ans = sandbox.search_memory({"category": "全部", "time": "今天", "keyword": None})
        assert "结果" in ans
    assert sandbox.search_memory(SearchMemoryRequest(category="全部", time="今天", keyword=None))[
        "error_code"] == "MEMORY_SEARCH_LIMIT"
    assert sandbox.recall_task_summary(RecallTaskSummaryRequest(task_name="不存在"))["error_code"] == "TASK_NOT_FOUND"
    bad_tool = sandbox.dispatch_world_tool("start_main_activity", {"action": "interact"}, "a-1")
    assert not bad_tool.accepted and bad_tool.tool_message_payload["result"]["error_code"] == "ACTION_REQUEST_INVALID"
    revise = sandbox.dispatch_world_tool(
        "revise_day_plan",
        {"goals": [{"intent_kind": "休息", "goal": "傍晚散步放松。", "remind_time": None, "around_time": "18:00"}]},
        "a-2",
    )
    assert revise.accepted and not revise.terminal_exit and "todo_layer" in revise.tool_message_payload["changes"]
    assert engine.context.state.model_dump_json() == committed_before
    # 并发失败补写证据与挂起搜索校验
    drained = sandbox.drain_evidence()
    assert {draft.event_type for draft in drained} >= {"decision_started", "observation", "model_request",
                                                       "model_response"}
    with pytest.raises(ProtocolError):
        sandbox.finish_wakeup(_suspended_stub(wakeup, "a-3", "missing-activity"))
    # finish_wakeup 已封口后再次调用被拒；另起沙盒验证真实挂起与反事实清除
    sandbox2 = engine.open_sandbox(wakeup)
    started = sandbox2.dispatch_world_tool("search_world", search_world("测试"), "a-3")
    assert started.accepted and started.suspended and started.terminal_exit
    outcome = sandbox2.finish_wakeup(_suspended_stub(wakeup, "a-3", started.tool_message_payload["activity_id"]))
    assert outcome.status == "suspended" and engine.context.state.model_dump_json() == committed_before
    with pytest.raises(ProtocolError):
        sandbox2.search_memory(SearchMemoryRequest(category="全部", time="今天", keyword=None))
    receipt = engine.commit_barrier([outcome])
    assert receipt.committed_agent_ids == (1,) and engine.context.state.agents[1].suspended_loop is not None
    assert "结果" in engine.search_memory(1, SearchMemoryRequest(category="饮食", time="今天", keyword=None))
    asset = engine.context.store.assets.put_json({"kind": "test"}, {"kind": "agent_snapshot"})
    cp = engine.checkpoint({"agent:1": asset})
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=654_321,
        hypothesis="Higher liquidity unlocks more choices.",
        source_event_ids=(wakeup.observation["observation_event_id"],),
    )
    digest = sha256_json({"tool_name": "counterfactual_intervention",
                         "request": intervention.model_dump(mode="json")})
    branch_dir = tmp_path / "cf-branch"
    child = restore_branch(engine.context.store, cp.checkpoint_id, branch_dir,
                           {"branch_kind": "counterfactual", "intervention_sha256": digest})
    child.close()
    branch = LifeEngine.open(engine.context.inputs, branch_dir)
    try:
        cf = branch.apply_counterfactual(intervention)
        assert cf.intervention_sha256 == digest and cf.after_balance_fen == 654_321
        assert branch.context.state.agents[1].suspended_loop is None
    finally:
        branch.close()


def test_barrier_merger_two_agents_conflict_rollback_and_engine_controls(engine_factory, tmp_path, monkeypatch):
    """同刻双人屏障正常提交、零操作直接提交、后序冲突整分支回滚原刻重排与失败/检查点门。"""
    env_dir = _two_agent_env_dir(tmp_path / "env1")
    engine = engine_factory(environment_root=env_dir)
    assert set(engine.meal_windows_by_agent()) == {1, 2}
    stage_day_plan(engine, agent_id=1)
    stage_day_plan(engine, agent_id=2)
    # 屏障 1：1 号发请求、2 号零世界操作直接封口，均无冲突提交
    w1, w2 = engine.prepare_barrier((1, 2))
    s1, s2 = engine.open_sandbox(w1), engine.open_sandbox(w2)
    assert s1.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("b1", 1, 1)).accepted
    assert s1.can_finish_idle() and not s2.can_finish_idle()
    first = engine.commit_barrier([s1.finish_wakeup(None), s2.finish_wakeup(None)])
    assert first.committed_agent_ids == (1, 2) and first.conflict_requeued_agent_ids == ()
    act_name = next(iter(engine.context.state.activities.values())).name
    obs_after = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    assert act_name in str(engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=act_name), obs_after))
    # checkpoint 快照集完整校验与 fail_run 接口
    asset = engine.context.store.assets.put_json({"kind": "test"}, {"kind": "agent_snapshot"})
    with pytest.raises(IntegrityError):
        engine.checkpoint({"agent:1": asset})
    cp = engine.checkpoint({"agent:1": asset, "agent:2": asset})
    assert cp.last_event_seq == engine.context.store.last_event_seq
    with pytest.raises(ProtocolError):
        engine.fail_run("")
    engine.fail_run("unit test termination")
    engine.fail_run("duplicate ignored")
    assert len(events(engine, "run_failed")) == 1
    # 屏障 2：新运行上并发启动在重放阶段让 2 号遭遇跨人物规则冲突，1 号保留、2 号回滚并原刻带 rejection_feedback 重入
    engine2 = engine_factory(environment_root=env_dir)
    stage_day_plan(engine2, agent_id=1)
    stage_day_plan(engine2, agent_id=2)
    w1_b, w2_b = engine2.prepare_barrier((1, 2))
    s1_b, s2_b = engine2.open_sandbox(w1_b), engine2.open_sandbox(w2_b)
    assert s1_b.dispatch_world_tool("start_main_activity", do_at("家", "测试闲聊"), action_identity("b2", 1, 1)).accepted
    assert s2_b.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("b2", 1, 2)).accepted
    o1_b, o2_b = s1_b.finish_wakeup(None), s2_b.finish_wakeup(None)
    orig_exec = merger_mod.execute_invocation

    def _conflict_on_second(context, actions, state, inv, event_ids):
        if inv.agent_id == 2 and any(act.agent_id == 1 and "测试闲聊" in act.name for act in state.activities.values()):
            raise PreconditionError("RESOURCE_CONFLICT", ["resource"])
        return orig_exec(context, actions, state, inv, event_ids)

    monkeypatch.setattr(merger_mod, "execute_invocation", _conflict_on_second)
    second = engine2.commit_barrier([o2_b, o1_b])
    assert second.committed_agent_ids == (1,) and second.conflict_requeued_agent_ids == (2,)
    monkeypatch.undo()
    assert any("测试闲聊" in act.name for act in engine2.context.state.activities.values())
    assert not any(act.agent_id == 2 for act in engine2.context.state.activities.values())
    agent2 = engine2.context.state.agents[2]
    assert agent2.next_decision_at == engine2.context.state.current_time
    assert "rejection_feedback" in agent2.pending_wake_reasons and agent2.suspended_loop is None
    assert any(ev.agent_id == 2 for ev in events(engine2, "action_rejected"))


def test_reopen_world_preserves_suspended_loop_and_record_failed_evidence(engine_factory):
    """suspended_loop 落盘检查点重开恢复及并发异常中止时补写纯模型与唤醒证据。"""
    engine = engine_factory()
    stage_day_plan(engine)
    run_dir = engine.context.store.run_dir
    [w] = engine.prepare_barrier((1,))
    s = engine.open_sandbox(w)
    r = s.dispatch_world_tool("search_world", search_world("咖啡"), "wake-search")
    out = s.finish_wakeup(_suspended_stub(w, "wake-search", r.tool_message_payload["activity_id"]))
    engine.commit_barrier([out])
    asset = engine.context.store.assets.put_json({"kind": "test"}, {"kind": "agent_snapshot"})
    engine.checkpoint({"agent:1": asset})
    inputs = engine.context.inputs
    engine.close()
    reopened = LifeEngine.open(inputs, run_dir)
    try:
        assert reopened.context.state.agents[1].suspended_loop is not None
        assert reopened.context.state.agents[1].suspended_loop.pending_tool_call_id == "call-search"
        assert reopened.due_searches() == ()
    finally:
        reopened.close()
    fail_engine = engine_factory()
    [wf] = fail_engine.prepare_barrier((1,))
    sf = fail_engine.open_sandbox(wf)
    fail_engine.record_failed_evidence(1, wf.wakeup_id, sf.drain_evidence())
    assert any(ev.wakeup_id == wf.wakeup_id for ev in events(fail_engine, "decision_started"))
