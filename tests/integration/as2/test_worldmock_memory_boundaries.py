"""@brief 记忆规则搬迁后，宿主行为和框架完整性分别由真实对象验证。"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentsociety2.agent.memory import AgentMemoryStore
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig
from worldmock.agents.memory.life_memory import WorldMockMemoryRuntime, _episode_snapshot
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, bigram_jaccard
from worldmock.state.model.memory import valid_subjective_snapshot


_TIME = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)


def _memory(text="我想每天去图书馆读书", kind="goal"):
    return {"type": kind, "text": text, "importance": 0.2,
            "source": "activity_conclusion", "epistemic_status": "subjective"}


def _runtime(*, enabled=True):
    owner = SimpleNamespace(id=1, name="Person", _model_name="fixture", _dispatch_memory_llm=None,
                            get_profile=lambda: {}, _logger=None, trace_span=None)
    return WorldMockMemoryRuntime(owner, MemoryRuntimeConfig(enabled=enabled))


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


def test_disabled_host_memory_never_creates_or_restores_a_workspace(tmp_path):
    runtime = _runtime(enabled=False)
    workspace = tmp_path / "disabled"
    assert runtime.ensure_store(workspace) is None
    assert runtime.restore_store(workspace) is None
    assert runtime.store is None and not workspace.exists()


def test_repeated_host_store_binding_preserves_written_identity_and_state(tmp_path):
    runtime = _runtime()
    store = runtime.ensure_store(tmp_path)
    written = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)
    before = store.episodes_path.read_bytes(), store.state_path.read_bytes()
    assert runtime.ensure_store(tmp_path) is store
    assert store.iter_episodes() == written
    assert (store.episodes_path.read_bytes(), store.state_path.read_bytes()) == before


def test_host_restore_rejects_an_already_bound_store_without_overwriting_it(tmp_path, caplog):
    runtime = _runtime()
    store = runtime.ensure_store(tmp_path)
    store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)
    before = store.episodes_path.read_bytes(), store.state_path.read_bytes()
    with pytest.raises(RuntimeError, match="already bound"):
        runtime.restore_store(tmp_path)
    assert runtime.store is store
    assert (store.episodes_path.read_bytes(), store.state_path.read_bytes()) == before
    assert "Memory store was already bound" in caplog.text


@pytest.mark.parametrize("corruption,error", [({"epistemic_status": "objective"}, "epistemic status"),
                                             ({"text": ""}, "empty text")])
def test_host_restore_refuses_corrupt_episode_snapshot_without_repair(tmp_path, corruption, error):
    store = _runtime().ensure_store(tmp_path)
    written = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)[0]
    store.episodes_path.write_text(json.dumps({**written, **corruption}) + "\n", encoding="utf-8")
    before = store.episodes_path.read_bytes(), store.state_path.read_bytes()
    restored = _runtime()
    with pytest.raises(ValueError, match=error):
        restored.restore_store(tmp_path)
    assert restored.store is None
    assert (store.episodes_path.read_bytes(), store.state_path.read_bytes()) == before


@pytest.mark.parametrize("left,right", [("", "文字"), ("文字", ""), ("a", "a"), (" \t\n", "文字")])
def test_short_or_empty_text_cannot_count_as_similar_subjective_memory(left, right):
    assert bigram_jaccard(left, right) == 0.0


@pytest.mark.parametrize("text", ["", " \t\n", None])
def test_host_store_refuses_empty_text_without_changing_written_memory(tmp_path, text, caplog):
    store = _runtime().ensure_store(tmp_path)
    written = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)
    before = store.episodes_path.read_bytes(), store.state_path.read_bytes()
    with pytest.raises(RuntimeError, match="empty memory text"):
        store.append_episodes([_memory(text)], tick=60, t=_TIME, step_count=2)
    assert store.iter_episodes() == written
    assert (store.episodes_path.read_bytes(), store.state_path.read_bytes()) == before
    assert "Memory store received an empty memory text" in caplog.text


def test_empty_host_write_leaves_the_memory_counter_and_records_unchanged(tmp_path):
    store = _runtime().ensure_store(tmp_path)
    written = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=1)
    before = store.episodes_path.read_bytes(), store.state_path.read_bytes()
    assert store.append_episodes([], tick=60, t=_TIME, step_count=2) == []
    assert store.iter_episodes() == written
    assert (store.episodes_path.read_bytes(), store.state_path.read_bytes()) == before
    store.validate_existing()


def test_host_does_not_merge_memories_with_incomparable_timezones(tmp_path):
    store = _runtime().ensure_store(tmp_path)
    first = store.append_episodes([_memory()], tick=60, t=_TIME.replace(tzinfo=None), step_count=1)[0]
    second = store.append_episodes([_memory()], tick=60, t=_TIME, step_count=2)[0]
    assert first["id"] != second["id"]
    assert len(store.iter_episodes()) == 2
    store.validate_existing()


@pytest.mark.parametrize("snapshot", [None, [], "subjective", 1])
def test_shared_snapshot_validator_refuses_non_object_memory_receipts(snapshot):
    assert valid_subjective_snapshot(snapshot) is False


def test_shared_snapshot_validator_guards_the_host_write_boundary():
    valid = {"id": "memory", "type": "goal", "text": "我想读书", "epistemic_status": "subjective"}
    assert _episode_snapshot(valid) == valid
    with pytest.raises(RuntimeError, match="snapshot is invalid"):
        _episode_snapshot({**valid, "type": "event"})



def _populated_cache(tmp_path):
    store = WorldMockMemoryStore(tmp_path, agent_id=1)
    store.append_episodes([_memory(), _memory("我想沿着河岸散步", "reflection")], tick=60, t=_TIME, step_count=1)
    store.append_episodes([_memory(), _memory("下周给妈妈打电话", "plan")],
                          tick=120, t=_TIME + timedelta(hours=1), step_count=2)
    store.append_episodes([_memory("今天的鱼汤很好喝", "observation")],
                          tick=180, t=_TIME + timedelta(hours=2), step_count=3)
    return store


def test_cache_reads_equal_the_copying_reads_at_every_cursor(tmp_path):
    store = _populated_cache(tmp_path)
    assert len(store.iter_episodes()) == 4
    assert store.episode_ids() == {str(item["id"]) for item in store.iter_episodes()}
    assert store.unconsolidated_view() == store.unconsolidated()
    store.mark_subjective_consolidated("记住了", step_count=3, tick=180)
    assert store.unconsolidated_view() == store.unconsolidated() == []
    written = store.append_episodes([_memory("我喜欢木吉他", "preference"), _memory("明天整理电脑桌", "plan")],
                                    tick=240, t=_TIME + timedelta(days=2), step_count=4)
    assert store.episode_ids() == {str(item["id"]) for item in store.iter_episodes()}
    assert store.unconsolidated_view() == store.unconsolidated() == written


def test_a_dangling_consolidation_cursor_is_refused(tmp_path, caplog):
    store = _populated_cache(tmp_path)
    state = store.read_state()
    state["last_consolidated_episode_id"] = "missing"
    store.write_state(state)
    with pytest.raises(ValueError, match="Last consolidated memory episode does not exist"):
        store.unconsolidated_view()
    assert "Last consolidated memory episode does not exist" in caplog.text


def test_cache_reads_return_the_cached_records_without_copying(tmp_path):
    store = _populated_cache(tmp_path)
    cached = store._load_episodes_cache()
    viewed = store.unconsolidated_view()
    assert viewed is not cached
    assert all(left is right for left, right in zip(viewed, cached, strict=True))
    state = store.read_state()
    state["last_consolidated_episode_id"] = cached[1]["id"]
    store.write_state(state)
    assert store.unconsolidated_view()[0] is cached[2]
