"""通过真实决策轮核验原子并行启动、共同身体时间及独立生命周期。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.activity_evidence import dedupe_revealed_cues
from worldmock.life.models import (StartBasicActivityRequest, StartParallelActivitiesRequest,
                                   StartTargetedActivityRequest)


def _request(duration: int = 120) -> StartParallelActivitiesRequest:
    """构造资源兼容且结束时刻不同的等待与播放请求。"""
    return StartParallelActivitiesRequest(activities=(
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=duration),
        StartTargetedActivityRequest(activity_type="watch", target_id="sintel-en-subtitles",
                                     planned_duration_seconds=60, playback_speed=1),
    ))


def test_parallel_start_and_first_terminal_share_one_body_interval(engine_factory) -> None:
    """组合只有一笔共同身体账，先结束的播放不终结等待和原动作。"""
    engine = engine_factory()
    ctx = context(engine, "parallel")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, "parallel-one", "start_parallel_activities", _request()))
    result = engine.commit_decision_round("parallel")[0]
    assert result.status == "in_progress" and result.activity_id is None
    assert len(result.data["activities"]) == 2
    assert {item.kind for item in engine.state.activities.values()} == {"wait", "watch"}
    watch = next(item for item in engine.state.activities.values() if item.kind == "watch")
    while engine.state.current_time < watch.planned_end_at:
        advance_next(engine)
    events = engine.store.read_events(engine.store.last_event_seq)
    body = [event for event in events if event.event_type == "environment_event"
            and event.data.get("kind") == "body_settlement"]
    progress = [event for event in events if event.event_type == "activity_progress"]
    assert body and len(progress) == 2 * len(body)
    assert {row.data["body_settlement_event_id"] for row in progress} == {
        event.event_id for event in body}
    assert engine.state.activities[watch.activity_id].status == "completed"
    assert any(item.status == "active" for item in engine.state.activities.values())
    assert engine.state.processed_actions["parallel-one"].result["status"] == "in_progress"


def test_parallel_resource_conflict_rejects_whole_group(engine_factory) -> None:
    """组内两个位置声明冲突时没有部分活动或付款。"""
    engine = engine_factory()
    ctx = context(engine, "conflict")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = StartParallelActivitiesRequest(activities=(
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120),
        StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=120),
    ))
    accounts = dict(engine.state.accounts)
    engine.submit_action(DirectToolCall(ctx, "parallel-conflict", "start_parallel_activities", request))
    result = engine.commit_decision_round("conflict")[0]
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert engine.state.activities == {} and engine.state.accounts == accounts


def test_recovery_activity_cannot_sponsor_unaffordable_watch(engine_factory) -> None:
    """等待的能量下界豁免只属于等待，零能量组合不能偷渡播放。"""
    from tests.life.test_energy_feasibility import _set_energy

    engine = engine_factory()
    _set_energy(engine, 0)
    ctx = context(engine, "zero-mixed-energy")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, "zero-mixed-energy", "start_parallel_activities", _request()))
    result = engine.commit_decision_round("zero-mixed-energy")[0]
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_ENERGY"
    assert not engine.state.activities


def test_ordinary_long_cue_does_not_repeat_at_next_settlement() -> None:
    """旧媒体的长字幕跨边界后只在首次相交区间交付。"""
    cue = {"cue_id": 1, "start_seconds": 0, "end_seconds": 10, "text": "caption"}
    first = SimpleNamespace(data={"revealed": {"position_before_seconds": 0, "cues": [cue]}})
    later = SimpleNamespace(data={"revealed": {"position_before_seconds": 5, "cues": [cue]}})
    dedupe_revealed_cues(first)
    dedupe_revealed_cues(later)
    assert first.data["revealed"]["cues"] == [cue]
    assert later.data["revealed"]["cues"] == []


@pytest.mark.parametrize("field,value", [
    ("resource_claims", []),
    ("target_id", "not-selected-content"),
    ("planned_end_at", "before-start"),
    ("energy_kcal", 0),
])
def test_reopen_rejects_tampered_parallel_activity(engine_factory, field, value) -> None:
    """真实落盘的资源、目标或时间被篡改后，恢复入口必须拒绝。"""
    engine = engine_factory()
    ctx = context(engine, f"tamper-{field}")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, f"action-{field}", "start_parallel_activities", _request()))
    result = engine.commit_decision_round(f"tamper-{field}")[0]
    assert result.status == "in_progress"
    snapshot = engine.state.model_dump(mode="json")
    watch = next(item for item in snapshot["activities"].values() if item["kind"] == "watch")
    if field == "energy_kcal":
        snapshot["agents"][str(watch["agent_id"])]["body"]["energy_kcal"] = value
    else:
        watch[field] = watch["started_at"] - 1 if value == "before-start" else value
    snapshot["version"] += 1
    engine.store.commit(snapshot, [EventDraft(
        event_type="environment_event", occurred_at=snapshot["current_time"],
        visibility="system", data={"kind": "test_tampered_activity"})])
    run_dir = engine.store.run_dir
    engine.close()
    with pytest.raises((ValueError, IntegrityError), match="invalid|disagree|exceed"):
        LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
