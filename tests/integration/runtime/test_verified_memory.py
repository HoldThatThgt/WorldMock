"""WorldMock 人物记忆的已核验事实和主观自述隔离。

主观条目只经 conclude_activity 逐项的记忆与看法写入：宿主按项原样交出存储形状，记忆存储固定来源与认知状态
（docs/design/04-simulation-engine.md 第七章）；步尾保存经真实工作区文件写出视图与条目。
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.life.conftest import advance_until_ready, conclude_request, stage_day_plan, submit_round, use_app
from worldmock.agents.life_agent import _conclusion_memories
from worldmock.agents.memory.life_memory import MemoryStep, render_memory_view, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.recording_engine.projections.activities.experiences import project_verified_experiences
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.recording_engine.events.memory_evidence import (
    MEMORY_UPDATE_KEYS,
    MemoryUpdateEvidence,
    _memory_evidence_data,
)


_TIME = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)


def _store(workspace) -> WorldMockMemoryStore:
    """人物 1 的空记忆存储（首次决策之前）。"""
    return WorldMockMemoryStore(workspace, 1, ())


def _save(store, facts=(), memories=(), observation="observation-1", now=_TIME):
    """一次步尾保存：身份段是人物卡片文本，事实与观察取本次决策的冻结观察。"""
    return save_step(store, MemoryStep("人物画像里的身份", tuple(facts), observation, tuple(memories), now))


def _concluded(text: str, kind: str = "goal", importance: float = 0.5,
               keywords: tuple[str, ...] = (), about: str | None = None) -> list[dict]:
    """结束一项活动时写下的一条记忆（about 非空时是写在 views 的看法），经宿主按项原样交出存储形状。"""
    entry = {"type": kind, "text": text, "importance": importance, "keywords": list(keywords)}
    memories, views = ([entry], []) if about is None else ([], [{"type": kind, "about": about, **entry}])
    return _conclusion_memories("conclude_activity", {"items": [
        {"activity_name": "等待（9月21日 16:00 起）", "summary": "收尾。", "memories": memories, "views": views}],
        "errands": []})


def _wait_fact(event_id="progress-1", seq=1):
    return {"kind": "waited", "agent_id": 1, "activity_id": "waiting", "source_event_id": event_id,
            "event_seq": seq, "occurred_at": 100, "interval_start": 90, "interval_end": 100,
            "duration_seconds": 10, "reason": None}


def test_oversized_fact_view_fails_instead_of_truncating_verified_evidence():
    with pytest.raises(ValueError, match="budget"):
        render_memory_view("a" * 4000, [_wait_fact()])


def test_fact_view_only_uses_the_current_observation(tmp_path):
    """视图的已核验经历只取本次决策冻结观察的事实，主观条目不进事实段；下一次决策的视图换成它自己的事实。"""
    store = _store(tmp_path)
    first = _save(store, [_wait_fact()], _concluded("我已经吃完三顿饭", importance=0.7, keywords=("饭",)))
    assert len(first.created_episodes) == 1 and first.merged_episodes == ()
    assert first.created_episodes[0]["source"] == "activity_conclusion"
    assert first.created_episodes[0]["epistemic_status"] == "subjective"
    assert first.verified_event_ids == ("progress-1",)
    view = store.memory_md_path.read_text(encoding='utf-8')
    assert view.count("## 已核验经历") == 1
    identity = view.split("## 身份\n", 1)[1].split("## 已核验经历", 1)[0].strip()
    assert identity == "> 人物画像里的身份"
    facts = view.split("## 已核验经历\n", 1)[1].split("## 人物自述", 1)[0]
    assert "progress-1" in facts and "吃完三顿饭" not in facts
    second = _save(store, [_wait_fact("progress-2", 2)], observation="observation-2")
    assert second.created_episodes == () and second.merged_episodes == ()
    assert second.verified_event_ids == ("progress-2",) and second.observation_event_id == "observation-2"
    view = store.memory_md_path.read_text(encoding='utf-8')
    assert "progress-2" in view and "progress-1" not in view
    third = _save(store, [_wait_fact("progress-2", 2)], observation="observation-3")
    assert third.created_episodes == () and third.merged_episodes == ()
    assert third.memory_before_sha256 is not None


def test_multiple_receipts_keep_goods_and_their_source_references(tmp_path):
    footprint = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
        {"version": 0, "current_time": 100},
    )
    try:
        receipts = footprint.append_evidence([EventDraft(
            event_type="environment_event", occurred_at=100, agent_id=1,
            data={"kind": "goods_received", "order_id": f"order-{goods_id}",
                  "goods_id": goods_id, "category": "meal", "quantity": 1, "store_id": "shop",
                  "quality": "好" if goods_id == "rice" else "坏"},
        ) for goods_id in ("rice", "soup")])
        facts = project_verified_experiences(receipts, agent_id=1, cutoff_seq=receipts[-1].event_seq)
        store = _store(tmp_path / "memory")
        outcome = _save(store, facts, _concluded("想再来一碗汤"))
        assert [item["goods_id"] for item in facts] == ["rice", "soup"]
        assert outcome.verified_event_ids == tuple(receipt.event_id for receipt in receipts)
        view = store.memory_md_path.read_text(encoding='utf-8')
        assert all(view.count(receipt.event_id) == 1 for receipt in receipts)
        assert "rice 1 份，商铺 shop，这份品质好" in view and "soup 1 份，商铺 shop，这份品质坏" in view
        episode = outcome.created_episodes[0]
        evidence = MemoryUpdateEvidence(
            context=None, operation="append_episodes", created_episode_ids=(episode["id"],),
            merged_episode_ids=(), episodes=({key: episode[key] for key in
                                              ("id", "type", "text", "epistemic_status")},),
            memory_before_sha256=outcome.memory_before_sha256,
            memory_sha256=_sha256(store.memory_md_path),
            episodes_sha256=_sha256(store.episodes_path),
            observation_event_id=outcome.observation_event_id,
            verified_event_ids=outcome.verified_event_ids,
        )
        data = _memory_evidence_data(evidence)
        assert list(data) == list(MEMORY_UPDATE_KEYS[:9])
        assert data["memory_before_sha256"] is None
        assert data["verified_event_ids"] == [receipt.event_id for receipt in receipts]
    finally:
        footprint.close()


def test_actual_app_progress_is_written_to_verified_memory_with_source_and_cursor(engine_factory, tmp_path):
    """真实 app 足迹经步尾保存写进工作区的 MEMORY.md；应用、内容与实际区间位置不靠人物自述补齐。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_side_activity", use_app("抖音"))},
                              "memory-app")
    started = results[0]
    assert started.status == "in_progress"
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    _, results = submit_round(engine, {1: ("conclude_activity", conclude_request(activity.name))},
                              "memory-app-end")
    assert results[0].status == "completed"
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=engine.context.store.last_event_seq)[-5:]
    assert facts and all(fact["kind"] == "played" for fact in facts)
    store = _store(tmp_path / "app-memory")
    result = _save(store, facts)
    view = store.memory_md_path.read_text(encoding="utf-8")
    assert result.verified_event_ids == tuple(fact["source_event_id"] for fact in facts)
    for fact in facts:
        assert f"使用应用 {fact['app_id']}，内容 {fact['content_id']}" in view
        assert f"{fact['position_before_seconds']:g} 至 {fact['position_after_seconds']:g} 秒" in view
        assert fact["source_event_id"] in view
    assert store.episodes_path.read_bytes() == b"" and result.created_episodes == ()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_restore_preserves_subjective_identity_and_rejects_status_tampering(tmp_path):
    """严格读回得到写出的条目身份，读回不改任何文件；条目的认知状态被改成 objective 时读回拒绝。"""
    store = _store(tmp_path)
    outcome = _save(store, [_wait_fact()], _concluded("想休息"))
    identity = outcome.created_episodes[0]["id"]
    persisted_view = store.memory_md_path.read_bytes()
    (episode,) = read_episodes(store.workspace, 1)
    assert episode["id"] == identity
    assert store.memory_md_path.read_bytes() == persisted_view
    episode["epistemic_status"] = "objective"
    store.episodes_path.write_text(json.dumps(episode, ensure_ascii=False) + "\n", encoding='utf-8')
    with pytest.raises(ValueError, match="has an invalid epistemic status"):
        read_episodes(store.workspace, 1)


def test_repeated_subjective_memory_reports_merge_identity_without_duplicate(tmp_path):
    """相似记忆合并沿用旧身份，并在本次更新结果明确报告合并。"""
    store = _store(tmp_path)
    memory = _concluded("想去附近散步")
    first = _save(store, memories=memory)
    identity = first.created_episodes[0]["id"]
    second = _save(store, memories=memory, now=_TIME + timedelta(minutes=1))
    assert second.created_episodes == ()
    assert [row["id"] for row in second.merged_episodes] == [identity]
    assert second.merged_episodes[0]["epistemic_status"] == "subjective"
    stored = read_episodes(store.workspace, 1)
    assert len(stored) == 1 and stored[0]["id"] == identity
    assert stored[0]["merge_count"] == 2
