"""候选窗口接入正式观察与曝光留痕。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, start_activity


def test_observation_commits_presented_object_stamps(engine_factory) -> None:
    """@brief 本轮看过的对象随决策提交写入本秒暂存，时钟推进时才并入轮转凭据（时间与能量规格第一章「同一秒内的呈现」）。"""
    engine = engine_factory()
    decision = context(engine, "candidate-wiring")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    presented = {row["object_id"] for row in observed["interaction_bindings"]
                 if row["kind"] in {"content", "game", "lifestyle"}}
    assert presented
    assert engine.state.presentation_state == {}

    request = start_activity("等待", "自己")
    engine.submit_action(DirectToolCall(decision, "candidate-wait", "start_activity", request))
    assert engine.commit_decision_round("candidate-wiring")[0].status == "in_progress"
    assert engine.state.presentation_state == {}
    staged = engine.state.same_moment_presentation[1].stamps
    assert presented <= set(staged)
    advance_next(engine)
    assert engine.state.same_moment_presentation == {}
    recorded = engine.state.presentation_state[1]
    assert presented <= set(recorded)
    assert all(recorded[item] == decision.observed_at.timestamp() for item in presented)
