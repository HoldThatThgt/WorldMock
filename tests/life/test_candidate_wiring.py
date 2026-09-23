"""候选窗口接入正式观察与曝光留痕。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import context
from worldmock.life.models import StartActivityRequest


def test_observation_commits_presented_object_stamps(engine_factory) -> None:
    """@brief 本轮看过的对象在决策提交后写入轮转凭据。"""
    engine = engine_factory()
    decision = context(engine, "candidate-wiring")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    presented = {row["object_id"] for row in observed["interaction_bindings"]
                 if row["kind"] in {"content", "game", "lifestyle"}}
    assert presented
    assert engine.state.presentation_state == {}

    request = StartActivityRequest(targets=["自己"], actions=["等待"])
    engine.submit_action(DirectToolCall(decision, "candidate-wait", "start_activity", request))
    engine.commit_decision_round("candidate-wiring")
    recorded = engine.state.presentation_state[1]
    assert presented <= set(recorded)
    assert all(recorded[item] == decision.observed_at.timestamp() for item in presented)
