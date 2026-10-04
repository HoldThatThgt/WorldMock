"""@brief 记忆规则搬迁后，宿主行为和框架完整性分别由真实对象验证。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentsociety2.agent.memory import AgentMemoryStore
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig
from custom.agents.life_memory import WorldMockMemoryRuntime, _episode_snapshot
from custom.agents.life_memory_store import WorldMockMemoryStore, bigram_jaccard


_TIME = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)


def _memory(text="我想每天去图书馆读书", kind="goal"):
    return {"type": kind, "text": text, "importance": 0.2,
            "source": "activity_conclusion", "epistemic_status": "subjective"}


def _runtime():
    owner = SimpleNamespace(id=1, name="Person", _model_name="fixture", _dispatch_memory_llm=None,
                            get_profile=lambda: {}, _logger=None, trace_span=None)
    return WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())


@pytest.mark.parametrize("age,merged", [(timedelta(hours=24), True),
                                         (timedelta(hours=24, microseconds=1), False),
                                         (timedelta(seconds=-1), False)])
def test_host_merge_window_has_closed_twenty_four_hour_boundary(tmp_path, age, merged):
    store = WorldMockMemoryStore(tmp_path, agent_id=1)
    first = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)[0]
    second = store.append_episodes([_memory()], tick=60, t=_TIME + age, step_count=2)[0]
    assert (first["id"] == second["id"]) is merged
    assert store.read_state()["episode_counter"] == (1 if merged else 2)
    assert second["step_index"] == (1 if merged else 2)
    store.validate_existing()


@pytest.mark.parametrize("text,similarity,merged", [("abcd", 0.6, True), ("abc", 0.4, False)])
def test_host_merge_similarity_uses_the_frozen_threshold(tmp_path, text, similarity, merged):
    store = WorldMockMemoryStore(tmp_path, agent_id=1)
    store.append_episodes([_memory("abcdef")], tick=60, t=_TIME, step_count=1)
    assert bigram_jaccard("abcdef", text) == similarity
    store.append_episodes([_memory(text)], tick=60, t=_TIME, step_count=2)
    assert len(store.iter_episodes()) == (1 if merged else 2)


def test_generic_framework_does_not_apply_host_merge_business(tmp_path):
    store = AgentMemoryStore(tmp_path, agent_id=1)
    records = store.append_episodes([_memory(), _memory()], tick=60, t=_TIME, step_count=1)
    assert len(records) == 2 and records[0]["id"] != records[1]["id"]
    assert [row["merge_count"] for row in records] == [1, 1]
    store.validate_existing()


def test_host_restore_binds_dedicated_store_and_keeps_strict_checkpoint_validation(tmp_path):
    first = _runtime()
    store = first.ensure_store(tmp_path)
    assert isinstance(store, WorldMockMemoryStore)
    store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)
    assert isinstance(_runtime().restore_store(tmp_path), WorldMockMemoryStore)
    before = store.state_path.read_bytes()
    store.state_path.write_bytes(b'{}')
    with pytest.raises(ValueError, match="schema"):
        _runtime().restore_store(tmp_path)
    assert store.state_path.read_bytes() == b'{}' and before != b'{}'


def test_shared_snapshot_validator_guards_the_host_write_boundary():
    valid = {"id": "memory", "type": "goal", "text": "我想读书", "epistemic_status": "subjective"}
    assert _episode_snapshot(valid) == valid
    with pytest.raises(RuntimeError, match="snapshot is invalid"):
        _episode_snapshot({**valid, "type": "event"})
