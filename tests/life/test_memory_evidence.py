"""记忆更新只引用本轮真实观察的完整事实窗口。"""

from __future__ import annotations

import pytest

from agentsociety2.agent.base import MemoryUpdateEvidence

from tests.life.conftest import advance_next, context, submit_round
from worldmock.errors import ProtocolError
from worldmock.life.models import StartBasicActivityRequest


@pytest.mark.parametrize("wrong_observation", [False, True])
def test_memory_update_rejects_omitted_observed_source(engine_factory, wrong_observation) -> None:
    """等待经历已被当前观察交付时，不能在记忆证据中漏报该来源。"""
    engine = engine_factory()
    submit_round(engine, {1: ("start_basic_activity", StartBasicActivityRequest(
        activity_type="wait", planned_duration_seconds=60))}, "observed-wait")
    advance_next(engine)
    ctx = context(engine, "memory-source")
    observation = engine.observe(ctx)
    assert observation["verified_experiences"]
    evidence = MemoryUpdateEvidence(
        context=ctx, operation="refresh_summary", created_episode_ids=(), merged_episode_ids=(),
        episodes=(), memory_before_sha256="a" * 64, memory_sha256="b" * 64,
        episodes_sha256="c" * 64, state_sha256="d" * 64, consolidated=False,
        observation_event_id=("unseen-observation" if wrong_observation else observation["observation_event_id"]),
        verified_event_ids=())
    with pytest.raises(ProtocolError, match="Memory update evidence is invalid"):
        engine.record_memory_update(evidence)
    assert not engine.store.read_agent_events("memory_updated", 1)
