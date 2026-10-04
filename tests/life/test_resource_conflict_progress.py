"""正在执行的工作占用资源时，提示与决策节律保持一致。"""

from __future__ import annotations

from tests.life.conftest import activity_name, advance_until_ready, last_result, start_activity, target_actions
from tests.life.test_work_contracts import _shared_round, _stage_shared_work
from worldmock.life.models import active_activities
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs


def test_active_work_is_listed_with_note_and_conflict_redecides_in_same_second(engine_factory) -> None:
    """同一工作仍在进行时照常列出并带说明；选了就以 RESOURCE_CONFLICT 返回原因与下一步，未达同刻上限时同一秒再决策，
    不按最早结束延后（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「动作照常列出，做错了再说明」）。"""
    engine = engine_factory()
    _stage_shared_work(engine)
    task = engine.context.scenario.tasks[0]

    def work_request(observed):
        """取本轮冻结的可读任务名启动工作。"""
        binding = next(row for row in observed["interaction_bindings"]
                       if row["kind"] == "work_task" and row["object_id"] == task.task_id)
        return start_activity("工作", binding["object"])

    _shared_round(engine, "resource-lock-start", {1: ("start_activity", work_request)})
    engine.commit_decision_round("resource-lock-start")
    running = next(item for item in active_activities(engine.context.state, 1))
    name = activity_name(engine, running.activity_id)
    reason = f"工作{task.title} cannot run together with {name}."
    assert advance_until_ready(engine)
    ready = engine.scheduling_state().ready_agent_ids
    assert 1 in ready and running.activity_id in {item.activity_id for item in active_activities(engine.context.state, 1)}
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    listed = target_actions(observed, f"工作：{task.title}")["工作"]
    assert (listed["available"], listed["reason"]) == (False, reason)
    binding = next(row for row in observed["interaction_bindings"] if row["object_id"] == task.task_id)
    assert [item["note"] for item in binding["actions"]] == [reason]
    rounds = engine.context.state.agents[1].same_moment_rounds
    assert rounds < engine.context.scenario.max_same_moment_decisions - 1
    now = engine.context.state.current_time
    _shared_round(engine, "resource-lock-repeat", {agent_id: ("start_activity", work_request) for agent_id in ready})
    result = next(item for item in engine.commit_decision_round("resource-lock-repeat") if item.agent_id == 1)
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert (result.data["reason"], result.data["next_step"]) == (reason, f"Conclude or pause {name} first.")
    agent = engine.context.state.agents[1]
    assert engine.context.state.current_time == now
    assert agent.next_decision_at == now
    assert agent.same_moment_rounds == rounds + 1
    assert agent.pending_wake_reasons == ["rejection_feedback"]
    assert [item.activity_id for item in active_activities(engine.context.state, 1)] == [running.activity_id]
    details = last_result(engine.observations._observation_data(agent))["details"]
    assert (details["reason"], details["next_step"]) == (reason, f"Conclude or pause {name} first.")


def test_boundary_never_yields_rejection_feedback(engine_factory) -> None:
    """rejection_feedback 只由同刻规则写入：边界判定即使在拒绝后的决策到期时也不产生它（第一章规则 6）。"""
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    state.current_time += 1
    agent = state.agents[1]
    state.agents[1] = agent.model_copy(update={
        "last_decision_time": state.current_time - 1,
        "next_decision_at": state.current_time + 900,
        "body_settled_at": state.current_time,
        "consecutive_rejections": 1,
    }, deep=True)
    _, rhythms = apply_decision_rhythm(engine.context_rhythm_inputs(engine, state))
    assert "rejection_feedback" not in rhythms[1].wake_reasons

    due = state.model_copy(deep=True)
    due.current_time = state.agents[1].next_decision_at
    due.agents[1].body_settled_at = due.current_time
    _, rhythms = apply_decision_rhythm(engine.context_rhythm_inputs(engine, due))
    assert "rejection_feedback" not in rhythms[1].wake_reasons
    assert "no_active_activity" in rhythms[1].wake_reasons
