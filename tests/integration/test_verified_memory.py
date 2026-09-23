"""WorldMock 人物记忆的已核验事实和主观自述隔离。"""

import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")

from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig
from agentsociety2.agent.base.react import MemoryUpdateEvidence
from custom.agents.life_memory import WorldMockMemoryRuntime, render_memory_view
from worldmock.footprints.experiences import project_verified_experiences
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.engine import _memory_evidence_data


_TIME = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _runtime(tmp_path, dispatch=None):
    async def no_call(**_):
        pytest.fail("subjective consolidation was not due")

    owner = SimpleNamespace(
        id=1, name="Person", _model_name="fixture", _dispatch_memory_llm=dispatch or no_call,
        get_profile=lambda: {"name": "Person"}, _logger=SimpleNamespace(warning=lambda *a: None),
        trace_span=None, _persona_card_text=lambda: "人物画像里的身份",
        _life_verified_experiences=[], _life_observation_event_id="observation-1",
    )
    runtime = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    runtime.ensure_store(tmp_path)
    return runtime, owner


def _wait_fact(event_id="progress-1", seq=1):
    return {"kind": "waited", "agent_id": 1, "activity_id": "waiting", "source_event_id": event_id,
            "event_seq": seq, "occurred_at": 100, "interval_start": 90, "interval_end": 100,
            "duration_seconds": 10, "reason": None}


@pytest.mark.asyncio
async def test_memory_runtime_without_a_workspace_does_not_claim_a_persisted_update(tmp_path):
    runtime, owner = _runtime(tmp_path)
    cold = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    assert cold.build_context() == {}
    outcome = await cold.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[])
    assert outcome.created_episodes == () and outcome.merged_episodes == ()
    assert cold.store is None


def test_oversized_fact_view_fails_instead_of_truncating_verified_evidence():
    with pytest.raises(ValueError, match="budget"):
        render_memory_view("a" * 4000, [_wait_fact()], "我想休息")


@pytest.mark.asyncio
async def test_fact_view_only_uses_current_observation_and_does_not_reset_subjective_counter(tmp_path):
    runtime, owner = _runtime(tmp_path)
    owner._life_verified_experiences = [_wait_fact()]
    subjective = {"type": "goal", "importance": 0.7, "keywords": ["饭"],
                  "text": "我已经吃完三顿饭", "source": "decision_observation", "refs": ["observation-1"],
                  "epistemic_status": "subjective"}
    first = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[subjective])
    assert len(first.created_episodes) == 1 and first.merged_episodes == ()
    assert first.verified_event_ids == ("progress-1",)
    assert runtime.store.read_state()["pending_episode_count"] == 1
    view = runtime.store.memory_md_path.read_text(encoding='utf-8')
    assert view.count("## 已核验经历") == 1
    identity = view.split("## 身份\n", 1)[1].split("## 已核验经历", 1)[0].strip()
    assert identity == "> 人物画像里的身份"
    facts = view.split("## 已核验经历\n", 1)[1].split("## 人物自述", 1)[0]
    assert "progress-1" in facts and "吃完三顿饭" not in facts
    assert runtime.build_context()["recent_episodes"][0]["epistemic_status"] == "subjective"
    owner._life_verified_experiences = [_wait_fact("progress-2", 2)]
    owner._life_observation_event_id = "observation-2"
    second = await runtime.after_step(tick=60, t=_TIME, step_count=2, finish_memories=[])
    assert second.created_episodes == () and second.merged_episodes == ()
    assert second.verified_event_ids == ("progress-2",)
    assert runtime.store.read_state()["pending_episode_count"] == 1
    assert "progress-2" in runtime.store.memory_md_path.read_text(encoding='utf-8')
    assert "progress-1" not in runtime.build_context()["memory_md"]
    third = await runtime.after_step(tick=60, t=_TIME, step_count=3, finish_memories=[])
    assert third.created_episodes == () and third.merged_episodes == ()
    assert third.memory_before_sha256 is not None


@pytest.mark.asyncio
async def test_multi_item_meal_keeps_both_facts_but_references_source_once(tmp_path):
    footprint = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
        {"version": 0, "current_time": 100},
    )
    try:
        progress = footprint.commit(footprint.state, [EventDraft(
            event_type="activity_progress", occurred_at=100, agent_id=1, activity_id="meal",
            data={"interval_start": 90, "interval_end": 100,
                  "activity": {"activity_id": "meal", "kind": "consume"},
                  "consumption": {"use_mode": "eat", "location_id": "restaurant", "items": [
                      {"product_id": "rice", "interval_quantity": 0.5, "cumulative_quantity": 0.5},
                      {"product_id": "soup", "interval_quantity": 1.0, "cumulative_quantity": 1.0},
                  ]}},
        )])[0]
        facts = project_verified_experiences([progress], agent_id=1, cutoff_seq=progress.event_seq)
        runtime, owner = _runtime(tmp_path / "memory")
        owner._life_verified_experiences = facts
        memory = {"type": "goal", "importance": 0.5, "text": "想再来一碗汤",
                  "source": "decision_observation", "refs": ["observation-1"],
                  "epistemic_status": "subjective"}
        outcome = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[memory])
        assert [item["product_id"] for item in facts] == ["rice", "soup"]
        assert outcome.verified_event_ids == (progress.event_id,)
        assert runtime.store.memory_md_path.read_text(encoding='utf-8').count(progress.event_id) == 2
        episode = outcome.created_episodes[0]
        evidence = MemoryUpdateEvidence(
            context=None, operation="append_episodes", created_episode_ids=(episode["id"],),
            merged_episode_ids=(), episodes=({key: episode[key] for key in
                                              ("id", "type", "text", "epistemic_status")},),
            memory_before_sha256=outcome.memory_before_sha256,
            memory_sha256=_sha256(runtime.store.memory_md_path),
            episodes_sha256=_sha256(runtime.store.episodes_path),
            state_sha256=_sha256(runtime.store.state_path), consolidated=False,
            observation_event_id=outcome.observation_event_id,
            verified_event_ids=outcome.verified_event_ids,
        )
        assert _memory_evidence_data(evidence)["verified_event_ids"] == [progress.event_id]
    finally:
        footprint.close()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_sleep_reflection_is_subjective_and_its_id_is_reported(tmp_path):
    holder = {}

    async def dispatch(**request):
        if len(request["messages"]) != 2:
            pytest.fail("unexpected memory request")
        holder["calls"] = holder.get("calls", 0) + 1
        if holder["calls"] == 1:
            return _response("我想和朋友保持联系。")
        identity = holder["runtime"].store.iter_episodes()[0]["id"]
        return _response(json.dumps([{"insight": "我更珍惜这段关系", "refs": [identity]}]))

    runtime, owner = _runtime(tmp_path, dispatch)
    holder["runtime"] = runtime
    memory = {"type": "relationship", "importance": 0.6, "text": "想多联系朋友",
              "source": "decision_observation", "refs": ["observation-1"],
              "epistemic_status": "subjective"}
    outcome = await runtime.after_step(
        tick=60, t=_TIME, step_count=1, finish_memories=[memory], sleep_boundary=True)
    assert holder["calls"] == 2
    assert len(outcome.created_episodes) == 2
    reflection = next(item for item in outcome.created_episodes if item["type"] == "reflection")
    assert reflection["epistemic_status"] == "subjective"
    assert reflection["source"] == "reflection"
    assert outcome.consolidated is True
    assert runtime.store.read_subjective_summary() == "我想和朋友保持联系。"


def test_memory_tool_returns_explicit_subjective_label_even_when_empty(tmp_path):
    runtime, _ = _runtime(tmp_path)
    ok, _, data = runtime.dispatch_tool("memory_recent", {"limit": 3})
    assert ok and data == {"epistemic_status": "subjective", "episodes": []}


def test_memory_store_results_cannot_mutate_persisted_nested_episode_data(tmp_path):
    runtime, _ = _runtime(tmp_path)
    store = runtime.store
    store.append_episodes([{"type": "goal", "importance": 0.5, "text": "愿意去散步",
                            "keywords": ["散步"], "refs": ["observation-1"],
                            "epistemic_status": "subjective"}], tick=60, t=_TIME, step_count=1)
    retrieved = store.iter_episodes()
    retrieved[0]["refs"].append("fabricated")
    assert store.iter_episodes()[0]["refs"] == ["observation-1"]


@pytest.mark.asyncio
async def test_restore_preserves_subjective_identity_and_rejects_status_tampering(tmp_path):
    runtime, owner = _runtime(tmp_path)
    owner._life_verified_experiences = [_wait_fact()]
    memory = {"type": "goal", "importance": 0.5, "text": "想休息",
              "source": "decision_observation", "refs": ["observation-1"],
              "epistemic_status": "subjective"}
    outcome = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[memory])
    identity = outcome.created_episodes[0]["id"]
    persisted_view = runtime.store.memory_md_path.read_bytes()
    resumed = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    resumed.restore_store(tmp_path)
    assert resumed.store.iter_episodes()[0]["id"] == identity
    assert resumed.store.memory_md_path.read_bytes() == persisted_view
    episode = resumed.store.iter_episodes()[0]
    episode["epistemic_status"] = "objective"
    resumed.store.episodes_path.write_text(json.dumps(episode, ensure_ascii=False) + "\n", encoding='utf-8')
    with pytest.raises(ValueError, match="epistemic status"):
        WorldMockMemoryRuntime(owner, MemoryRuntimeConfig()).restore_store(tmp_path)


@pytest.mark.asyncio
async def test_repeated_subjective_memory_reports_merge_identity_without_duplicate(tmp_path):
    """相似记忆合并沿用旧身份，并在本次更新结果明确报告合并。"""
    runtime, _ = _runtime(tmp_path)
    memory = {"type": "goal", "importance": 0.5, "text": "想去附近散步",
              "source": "decision_observation", "refs": ["observation-1"],
              "epistemic_status": "subjective"}
    first = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[memory])
    identity = first.created_episodes[0]["id"]
    second = await runtime.after_step(tick=120, t=_TIME, step_count=2, finish_memories=[memory])
    assert second.created_episodes == ()
    assert [row["id"] for row in second.merged_episodes] == [identity]
    assert second.merged_episodes[0]["epistemic_status"] == "subjective"
    stored = runtime.store.iter_episodes()
    assert len(stored) == 1 and stored[0]["id"] == identity
    assert stored[0]["merge_count"] == 2
