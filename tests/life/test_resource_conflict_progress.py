"""正在执行的工作占用资源时，提示与决策节律保持一致。"""

from __future__ import annotations

from types import SimpleNamespace

from tests.life.test_work_contracts import _shared_round, _stage_shared_work
from worldmock.errors import BusinessRuleError
from worldmock.life.models import StartActivityRequest
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs


def test_active_work_is_marked_unavailable_and_conflict_redecides_in_same_second(engine_factory) -> None:
    """同一工作仍在进行时不列入交互对象；绕过后的资源冲突拒绝未达同刻上限时同一秒再决策，不按最早结束延后。"""
    engine = engine_factory("multi-person.json")
    _stage_shared_work(engine)
    task = engine.scenario.tasks[0]

    def work_request(observed):
        """取本轮冻结的可读任务名启动工作。"""
        binding = next(row for row in observed["interaction_bindings"]
                       if row["kind"] == "work_task" and row["object_id"] == task.task_id)
        return StartActivityRequest(targets=[binding["object"]], actions=["工作"])

    _shared_round(engine, "resource-lock-start", {1: ("start_activity", work_request),
                                                  2: ("start_activity", work_request)})
    engine.commit_decision_round("resource-lock-start")
    observed = engine._observation_data(engine.state.agents[1])
    assert all(row["object"] != f"工作：{task.title}"
               for row in observed["facts"]["interactive_objects"])
    assert all(row["object_id"] != task.task_id for row in observed["interaction_bindings"])

    assert any(item.agent_id == 1 and item.status == "active" for item in engine.state.activities.values())
    rounds = engine.state.agents[1].same_moment_rounds
    assert rounds < engine.scenario.max_same_moment_decisions - 1
    pending = SimpleNamespace(agent_id=1, action_id="repeat-work")
    changed, result = engine._after_rejection(
        engine.state, pending, BusinessRuleError("RESOURCE_CONFLICT"))
    assert result.error_code == "RESOURCE_CONFLICT"
    assert changed.agents[1].next_decision_at == engine.state.current_time
    assert changed.agents[1].same_moment_rounds == rounds + 1
    assert changed.agents[1].pending_wake_reasons == ["rejection_feedback"]


def test_boundary_never_yields_rejection_feedback(engine_factory) -> None:
    """rejection_feedback 只由同刻规则写入：边界判定即使在拒绝后的决策到期时也不产生它（第一章规则 6）。"""
    engine = engine_factory()
    state = engine.state.model_copy(deep=True)
    state.current_time += 1
    agent = state.agents[1]
    state.agents[1] = agent.model_copy(update={
        "last_decision_time": state.current_time - 1,
        "next_decision_at": state.current_time + 900,
        "body_settled_at": state.current_time,
        "consecutive_rejections": 1,
    }, deep=True)
    _, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
    assert "rejection_feedback" not in rhythms[1].wake_reasons

    due = state.model_copy(deep=True)
    due.current_time = state.agents[1].next_decision_at
    due.agents[1].body_settled_at = due.current_time
    _, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine, due))
    assert "rejection_feedback" not in rhythms[1].wake_reasons
    assert "no_active_activity" in rhythms[1].wake_reasons
