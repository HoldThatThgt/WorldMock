"""双对象逐项动作在正式场景中的组合准入和共同结算验收。"""

from __future__ import annotations

import pytest

from tests.life.conftest import stage_day_plan
from tests.life.test_parallel_activities import _advance_with_decisions, _start_pair


@pytest.mark.parametrize("partner", ["wait", "do", "travel_to"])
def test_watch_can_share_time_with_compatible_activity(engine_factory, partner: str) -> None:
    """观看与等待、生活方式或步行均可同轮启动，并保留独立身份。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result = _start_pair(engine, "watch", partner, f"watch-{partner}")
    assert result.status == "in_progress", result.error_code
    activities = [item for item in engine.state.activities.values()
                  if item.origin_action_id == result.action_id]
    assert len(activities) == 2
    assert len({item.activity_id for item in activities}) == 2
    assert all(item.status == "active" for item in activities)
    assert len(result.data["activities"]) == 2


@pytest.mark.parametrize("partner", ["sleep", "play"])
def test_incompatible_pair_is_rejected_without_partial_state(engine_factory, partner: str) -> None:
    """注意力或位置冲突拒绝整组，不留下首项活动；会话只剩运行开始的停留会话且未改动（第七章）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.state.model_dump(mode="json")
    result = _start_pair(engine, "watch", partner, f"watch-{partner}-conflict")
    assert result.status == "rejected"
    assert result.error_code == "RESOURCE_CONFLICT"
    assert engine.state.activities == {}
    sessions = list(engine.state.sessions.values())
    assert [(item.kind, item.status, item.activity_ids) for item in sessions] == [("stay", "active", [])]
    assert engine.state.model_dump(mode="json")["sessions"] == before["sessions"]
    assert engine.state.accounts == before["accounts"]


def test_parallel_progress_uses_one_shared_body_interval(engine_factory) -> None:
    """两个活动在同一时间段各写进度，身体只结算一笔。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _start_pair(engine, "watch", "wait", "shared-body")
    assert started.status == "in_progress", started.error_code
    before = engine.store.last_event_seq
    for index in range(40):
        _advance_with_decisions(engine, index)
        events = [item for item in engine.store.read_events(engine.store.last_event_seq)
                  if item.event_seq > before]
        progress = [item for item in events if item.event_type == "activity_progress"
                    and item.action_id == started.action_id]
        if len(progress) >= 2:
            break
    assert len(progress) >= 2
    body_ids = {item.data["body_settlement_event_id"] for item in progress[:2]}
    assert len(body_ids) == 1
