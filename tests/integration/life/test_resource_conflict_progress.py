"""当前工作占用冲突在同一时刻反馈，边界结算不伪造拒绝反馈。"""

from tests.life.conftest import advance_until_ready, context, last_result, target_actions
from tests.life.work_runtime import start_assigned_work
from tests.integration.life.test_acs_amended_runtime import perform
from tests.integration.life.test_acs_runtime import request
from worldmock.life.models import active_activities
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs


def test_active_work_is_listed_with_note_and_conflict_redecides_in_same_second(engine_factory) -> None:
    """真实分配内容运行时，目录动作仍列出；重复启动以同刻资源冲突拒绝并保留原活动。"""
    engine = engine_factory()
    content, identity = start_assigned_work(engine, "resource-lock")
    assert advance_until_ready(engine)
    catalog = engine.context.semantics.catalog
    action = catalog.actions[content.action_id]
    observed = engine.observe(context(engine, "resource-preview"))
    listed = target_actions(observed, action.name)["进行"]
    assert listed["available"] is False and listed["reason"]
    now = engine.context.state.current_time
    rounds = engine.context.state.agents[1].same_moment_rounds
    result = perform(engine, "resource-repeat", request(action.name, None))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert result.data["reason"] == listed["reason"]
    assert result.data["next_step"] == f"Conclude or pause {engine.context.state.activities[identity].name} first."
    agent = engine.context.state.agents[1]
    assert engine.context.state.current_time == now and agent.next_decision_at == now
    assert agent.same_moment_rounds == rounds + 1
    assert agent.pending_wake_reasons == ["rejection_feedback"]
    assert [item.activity_id for item in active_activities(engine.context.state, 1)] == [identity]
    details = last_result(engine.observations._observation_data(agent))["details"]
    assert details["reason"] == result.data["reason"]
    assert details["next_step"] == result.data["next_step"]


def test_boundary_never_yields_rejection_feedback(engine_factory) -> None:
    """即使上一轮是拒绝，未来边界也只给真实到期原因，不重复同刻反馈。"""
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    state.current_time += 1
    agent = state.agents[1]
    state.agents[1] = agent.model_copy(update={
        "last_decision_time": state.current_time - 1,
        "next_decision_at": state.current_time + 900,
        "body_settled_at": state.current_time}, deep=True)
    _, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert "rejection_feedback" not in rhythms[1].wake_reasons
    state.current_time = state.agents[1].next_decision_at
    state.agents[1].body_settled_at = state.current_time
    _, rhythms = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
    assert "rejection_feedback" not in rhythms[1].wake_reasons
    assert "no_active_activity" in rhythms[1].wake_reasons
