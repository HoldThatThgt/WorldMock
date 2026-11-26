"""@brief AgentSandbox、BarrierMerger 多人物冲突仲裁、日会话回滚与 LifeEngine 公开接口测试。"""

from __future__ import annotations

import shutil

import pytest
import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.conftest import action_identity, advance_until_ready, do_at, search_world, stage_day_plan
from tests.life.environment_runtime import events
from worldmock.agents.life_agent import life_tool_schema_sha256
from worldmock.foundation.errors import IntegrityError, PreconditionError, ProtocolError
from worldmock.foundation.serialization import sha256_json
from worldmock.recording_engine.events.models import AssetRef
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.simulation_engine.decisions import merger as merger_mod
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.state.decisions.day_session import DaySessionState
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, SearchMemoryRequest
from worldmock.state.model.exchange import ModelCallEvidence


def _suspended_stub(wakeup, action_id: str, activity_id: str) -> SuspendedLoopState:
    """@brief 构造最小合法无栈挂起恢复载荷。"""
    return SuspendedLoopState(
        wakeup_id=wakeup.wakeup_id, action_id=action_id, activity_id=activity_id,
        pending_tool_call_id="call-search", turn_used=1, memory_search_count=0,
        accepted_action_ids=(action_id,), pending_memories=(), verified_experiences=(),
        local_rejection_counts={}, observation_event_id=wakeup.observation["observation_event_id"],
        observation_parts=dict(wakeup.observation_parts), suspended_at=wakeup.observed_at,
    )


def _day_session_stub(wakeup, *, local_date_override: str | None = None,
                      compaction_count: int = 0) -> DaySessionState:
    """@brief 构造最小合法日会话状态。"""
    return DaySessionState(
        local_date=local_date_override or wakeup.local_date,
        system_prompt='{"layer":"系统层"}',
        tools_digest=life_tool_schema_sha256(),
        messages=(
            {"role": "system", "content": '{"layer":"系统层"}'},
            {"role": "user", "content": "[]"},
        ),
        compaction_count=compaction_count,
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
    """AgentSandbox 只读检索限次、回看、模型证据配对、挂起、日会话提交与反事实清除。"""
    engine = engine_factory()
    stage_day_plan(engine)
    committed_before = engine.context.state.model_dump_json()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    assert not hasattr(sandbox, "can_finish_idle")
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
    assert revise.accepted and not revise.terminal_exit and "待办层" in revise.tool_message_payload["changes"]
    assert engine.context.state.model_dump_json() == committed_before
    drained = sandbox.drain_evidence()
    assert {draft.event_type for draft in drained} >= {"decision_started", "observation", "model_request",
                                                       "model_response"}
    with pytest.raises(ProtocolError):
        sandbox.finish_wakeup(_suspended_stub(wakeup, "a-3", "missing-activity"), _day_session_stub(wakeup))
    sandbox2 = engine.open_sandbox(wakeup)
    started = sandbox2.dispatch_world_tool("search_world", search_world("测试"), "a-3")
    assert started.accepted and started.suspended and started.terminal_exit
    assert "activity_id" not in started.tool_message_payload
    assert started.activity_id
    outcome = sandbox2.finish_wakeup(
        _suspended_stub(wakeup, "a-3", started.activity_id), _day_session_stub(wakeup),
    )
    assert outcome.status == "suspended" and engine.context.state.model_dump_json() == committed_before
    with pytest.raises(ProtocolError):
        sandbox2.search_memory(SearchMemoryRequest(category="全部", time="今天", keyword=None))
    receipt = engine.commit_barrier([outcome])
    assert receipt.committed_agent_ids == (1,) and engine.context.state.agents[1].suspended_loop is not None
    assert engine.context.state.agents[1].day_session is not None
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
        assert branch.context.state.agents[1].day_session is None
    finally:
        branch.close()


def test_barrier_merger_two_agents_conflict_rollback_and_engine_controls(engine_factory, tmp_path, monkeypatch):
    """同刻双人屏障正常提交、冲突分支回滚并保留唤醒前日会话、失败与检查点门。"""
    env_dir = _two_agent_env_dir(tmp_path / "env1")
    engine = engine_factory(environment_root=env_dir)
    assert set(engine.meal_windows_by_agent()) == {1, 2}
    stage_day_plan(engine, agent_id=1)
    stage_day_plan(engine, agent_id=2)
    w1, w2 = engine.prepare_barrier((1, 2))
    s1, s2 = engine.open_sandbox(w1), engine.open_sandbox(w2)
    assert s1.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("b1", 1, 1)).accepted
    first = engine.commit_barrier([
        s1.finish_wakeup(None, _day_session_stub(w1)),
        s2.finish_wakeup(None, _day_session_stub(w2)),
    ])
    assert first.committed_agent_ids == (1, 2) and first.conflict_requeued_agent_ids == ()
    act_name = next(iter(engine.context.state.activities.values())).name
    obs_after = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    assert act_name in str(engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=act_name), obs_after))
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

    engine2 = engine_factory(environment_root=env_dir)
    stage_day_plan(engine2, agent_id=1)
    stage_day_plan(engine2, agent_id=2)
    w1_a, w2_a = engine2.prepare_barrier((1, 2))
    pre_session2 = _day_session_stub(w2_a, compaction_count=2)
    staged2 = engine2.context.state.model_copy(
        update={"version": engine2.context.state.version + 1, "agents": {
            **engine2.context.state.agents,
            2: engine2.context.state.agents[2].model_copy(update={"day_session": pre_session2}, deep=True),
        }},
        deep=True,
    )
    from worldmock.recording_engine.events.models import EventDraft
    engine2.context.store.commit(
        engine2.context.state.model_dump(mode="json"),
        staged2.model_dump(mode="json"),
        [EventDraft(event_type="environment_event", occurred_at=staged2.current_time,
                    visibility="system", data={"kind": "test_day_session_staged"})],
    )
    engine2.context.state = staged2
    w1_b, w2_b = engine2.prepare_barrier((1, 2))
    s1_b, s2_b = engine2.open_sandbox(w1_b), engine2.open_sandbox(w2_b)
    assert s1_b.dispatch_world_tool("start_main_activity", do_at("家", "测试闲聊"), action_identity("b2", 1, 1)).accepted
    s2_b.lookup_targets({"kind": "location", "keyword": None, "limit": 1, "offset": 0})
    assert s2_b.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("b2", 1, 2)).accepted
    o1_b = s1_b.finish_wakeup(None, _day_session_stub(w1_b))
    o2_b = s2_b.finish_wakeup(None, _day_session_stub(w2_b, compaction_count=3))
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
    assert agent2.day_session is not None and agent2.day_session.compaction_count == 2
    assert agent2.day_session.loaded_target_keys == ()
    assert any(ev.agent_id == 2 for ev in events(engine2, "action_rejected"))


def test_reopen_world_preserves_suspended_loop_day_session_and_failed_evidence(engine_factory):
    """suspended_loop 与 day_session 落盘检查点重开恢复及并发异常中止补写证据。"""
    engine = engine_factory()
    stage_day_plan(engine)
    run_dir = engine.context.store.run_dir
    [w] = engine.prepare_barrier((1,))
    s = engine.open_sandbox(w)
    r = s.dispatch_world_tool("search_world", search_world("咖啡"), "wake-search")
    out = s.finish_wakeup(_suspended_stub(w, "wake-search", r.activity_id), _day_session_stub(w))
    engine.commit_barrier([out])
    asset = engine.context.store.assets.put_json({"kind": "test"}, {"kind": "agent_snapshot"})
    engine.checkpoint({"agent:1": asset})
    inputs = engine.context.inputs
    engine.close()
    reopened = LifeEngine.open(inputs, run_dir)
    try:
        assert reopened.context.state.agents[1].suspended_loop is not None
        assert reopened.context.state.agents[1].suspended_loop.pending_tool_call_id == "call-search"
        assert reopened.context.state.agents[1].day_session is not None
        assert reopened.due_searches() == ()
    finally:
        reopened.close()
    fail_engine = engine_factory()
    [wf] = fail_engine.prepare_barrier((1,))
    sf = fail_engine.open_sandbox(wf)
    fail_engine.record_failed_evidence(1, wf.wakeup_id, sf.drain_evidence())
    assert any(ev.wakeup_id == wf.wakeup_id for ev in events(fail_engine, "decision_started"))


def test_continue_activity_compact_snapshot_and_boundary_delta_lifecycle(engine_factory):
    """continue_activity 前置条件、compact_snapshot、Compaction 证据落盘、跨唤醒差量与过期日会话清理。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [w1] = engine.prepare_barrier((1,))
    s1 = engine.open_sandbox(w1)
    # 无进行中活动时拒绝 continue_activity
    rejected = s1.dispatch_world_tool("continue_activity", {"reason": None}, "c-empty")
    assert not rejected.accepted
    assert rejected.tool_message_payload["result"]["error_code"] == "ACTIVITY_PRECONDITION_UNMET"
    # 开启进行中活动后正常提交，再测 compact_snapshot 与 Compaction 记录
    started = s1.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("c1", 1, 1))
    assert started.accepted and started.terminal_exit
    s1.lookup_targets({"kind": "location", "keyword": None, "limit": 2, "offset": 0})
    snap = s1.compact_snapshot()
    assert "tool_layer" in snap and "progress_meta" in snap
    comp_rec = {
        "compaction_count": 1, "compacted_through_seq": engine.context.store.last_event_seq,
        "before_tokens": 90000, "after_tokens": 20000, "folded_groups": 3, "kept_tail_groups": 2,
    }
    engine.commit_barrier([s1.finish_wakeup(None, _day_session_stub(w1, compaction_count=1), (comp_rec,))])
    assert engine.context.state.agents[1].day_session.loaded_target_keys == ()
    compacted_evts = [ev for ev in events(engine, "environment_event")
                      if ev.data.get("kind") == "day_session_compacted"]
    assert len(compacted_evts) == 1 and compacted_evts[0].data["before_tokens"] == 90000
    import json
    ops_entries = [
        json.loads(line)
        for path in sorted((engine.context.store.run_dir / "logs").glob("ops-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(row.get("code") == "DAY_SESSION_COMPACTED" for row in ops_entries)
    # 推进至活动结束产生待收尾，验证 continue_activity 拒绝与 boundary_delta_payload
    assert advance_until_ready(engine)
    [w2] = engine.prepare_barrier((1,))
    assert w2.day_session is not None and w2.boundary_delta_payload is not None
    assert w2.boundary_delta_payload["ok"] is True
    assert w2.boundary_delta_payload["result"]["status"] == "settled"
    assert w2.boundary_delta_payload["boundary"]["observed_at"] == w2.observation["environment_layer"]["observed_at"]
    assert w2.settled_day_events
    s2 = engine.open_sandbox(w2)
    blocked = s2.dispatch_world_tool("continue_activity", {"reason": "先继续"}, "c-wait")
    assert not blocked.accepted and blocked.tool_message_payload["result"]["error_code"] == "ACTIVITY_CONCLUSION_REQUIRED"
    # 防御性过期日会话在 _build_wakeup 被丢弃
    expired = _day_session_stub(w2, local_date_override="2000-01-01")
    engine.context.state = engine.context.state.model_copy(
        update={"agents": {1: engine.context.state.agents[1].model_copy(update={"day_session": expired}, deep=True)}},
        deep=True,
    )
    [w_expired] = engine.prepare_barrier((1,))
    assert w_expired.day_session is None and w_expired.boundary_delta_payload is None


def test_continue_activity_accepts_without_mutating_slots_and_midnight_clears_day_session(engine_factory):
    """存在进行中活动时 continue_activity 受理并维持状态，跨午夜重置清空 day_session。"""
    engine = engine_factory(mutate=lambda _p, cfg: cfg.__setitem__("reconsideration_interval_seconds", 300))
    stage_day_plan(engine)
    [w1] = engine.prepare_barrier((1,))
    s1 = engine.open_sandbox(w1)
    assert s1.dispatch_world_tool("start_main_activity", do_at("家", "测试休息"), action_identity("m1", 1, 1)).accepted
    engine.commit_barrier([s1.finish_wakeup(None, _day_session_stub(w1))])
    # 推进到下一次清醒/播报边界，测试 continue_activity 受理
    state = engine.scheduling_state()
    engine.advance(int((state.next_event_at - state.current_time).total_seconds()), state.next_event_at)
    while not engine.scheduling_state().ready_agent_ids:
        if engine.midnight_rhythm_pending:
            break
        state = engine.scheduling_state()
        engine.advance(int((state.next_event_at - state.current_time).total_seconds()), state.next_event_at)
    if engine.scheduling_state().ready_agent_ids:
        [w2] = engine.prepare_barrier((1,))
        s2 = engine.open_sandbox(w2)
        cont = s2.dispatch_world_tool("continue_activity", {"reason": "再看一会儿"}, "m-cont")
        assert cont.accepted and cont.terminal_exit and not cont.suspended
        assert cont.tool_message_payload["result"]["details"]["reason"] == "再看一会儿"
        engine.commit_barrier([s2.finish_wakeup(None, _day_session_stub(w2))])
    assert engine.context.state.agents[1].day_session is not None
    # 推进跨过本地午夜并执行 decide_midnight_rhythm，验证 day_session 置为 None
    while not engine.midnight_rhythm_pending:
        st = engine.scheduling_state()
        if st.ready_agent_ids:
            [w_loop] = engine.prepare_barrier(st.ready_agent_ids)
            sb = engine.open_sandbox(w_loop)
            engine.commit_barrier([sb.finish_wakeup(None, _day_session_stub(w_loop))])
            continue
        engine.advance(int((st.next_event_at - st.current_time).total_seconds()), st.next_event_at)
    engine.decide_midnight_rhythm()
    assert engine.context.state.agents[1].day_session is None
