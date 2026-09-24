"""正在执行的工作占用资源时，提示与决策节律保持一致。"""

from __future__ import annotations

from types import SimpleNamespace

from tests.life.test_work_contracts import _shared_round, _stage_shared_work
from worldmock.errors import BusinessRuleError
from worldmock.life.models import StartActivityRequest
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs


def test_active_work_is_marked_unavailable_and_conflict_waits_for_progress(engine_factory) -> None:
    """同一工作仍在进行时，重启请求不会导致逐秒模型重试。"""
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
    task_row = next(row for row in observed["facts"]["interactive_objects"]
                    if row["object"] == f"工作：{task.title}")
    assert task_row["preconditions_met"] is False

    pending = SimpleNamespace(agent_id=1, action_id="repeat-work")
    changed, result = engine._after_rejection(
        engine.state, pending, BusinessRuleError("RESOURCE_CONFLICT"))
    activity = next(item for item in engine.state.activities.values() if item.agent_id == 1)
    expected = min(engine.scenario.activity_feedback_seconds,
                   activity.planned_end_at - engine.state.current_time)
    assert result.error_code == "RESOURCE_CONFLICT"
    assert changed.agents[1].next_decision_at == engine.state.current_time + expected
    assert expected > 1


def test_rejection_feedback_waits_until_next_decision_boundary(engine_factory) -> None:
    """后台身体边界不得抢在已安排的拒绝反馈决策之前唤醒人物。"""
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
    assert "rejection_feedback" in rhythms[1].wake_reasons
