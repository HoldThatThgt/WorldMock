"""@brief NASA 播客经真实播放活动推进的段落揭示回归。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_until_ready, context, stage_day_plan, wake_by_mail
from worldmock.life.models import (
    BrowseActivitiesRequest, SetActivityStateRequest,
    StartActivityRequest,
)

ITEM_ID = "podcast-nasa-space-habitat-excerpt"


def _action(engine, name: str, tool: str, request):
    """@brief 通过正式决策轮执行一次人物工具。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return result


def _advance_to(engine, expected: int) -> None:
    """@brief 只通过真实世界事件到达指定唤醒时刻。"""
    assert advance_until_ready(engine)
    assert engine.state.current_time == expected


def _revealed(engine, activity_id: str) -> list[dict]:
    """@brief 读取当前播放活动在本结算区间实际揭示的段落。"""
    return engine.state.activities[activity_id].data["revealed"]["cues"]


def test_podcast_pause_and_speed_follow_real_media_position(engine_factory) -> None:
    """@brief 暂停不前进，恢复后到达官方锚点时揭示段落。"""
    engine = engine_factory()
    stage_day_plan(engine)
    start = engine.state.current_time
    started = _action(engine, "podcast-start", "start_activity",
                      StartActivityRequest(activity_type="watch", target_id=ITEM_ID,
                                                   planned_duration_seconds=786, playback_speed=1))
    activity_id = started.activity_id
    for offset in (1, 10, 11, 12):
        wake_by_mail(engine, offset=offset)
    _advance_to(engine, start + 1)
    assert engine.state.activities[activity_id].media_position_seconds == 1
    assert _revealed(engine, activity_id) == []
    _action(engine, "podcast-pause", "set_activity_state",
            SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    _advance_to(engine, start + 10)
    assert engine.state.activities[activity_id].media_position_seconds == 1
    _action(engine, "podcast-resume", "set_activity_state",
            SetActivityStateRequest(activity_id=activity_id, operation="resume"))
    _advance_to(engine, start + 11)
    assert engine.state.activities[activity_id].media_position_seconds == 2
    assert _revealed(engine, activity_id) == []
    _action(engine, "podcast-browse", "browse_activities",
            BrowseActivitiesRequest(category="podcast_episode", cursor=None))
    _advance_to(engine, start + 12)
    assert engine.state.activities[activity_id].media_position_seconds == 3
    assert [cue["cue_id"] for cue in _revealed(engine, activity_id)] == [1], (
        engine.state.activities[activity_id].data["revealed"])


def test_podcast_full_speed_playback_reaches_final_official_anchor(engine_factory) -> None:
    """@brief 完整音轨播放到 786 秒时才完成最后的官方段落。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _action(engine, "podcast-full", "start_activity",
                      StartActivityRequest(activity_type="watch", target_id=ITEM_ID,
                                                   planned_duration_seconds=393, playback_speed=2))
    activity_id = started.activity_id
    for _ in range(64):
        if engine.state.activities[activity_id].status == "completed":
            break
        assert advance_until_ready(engine)
    finished = engine.state.activities[activity_id]
    assert finished.status == "completed"
    assert finished.media_position_seconds == 786
    assert engine.state.agents[1].content_progress[ITEM_ID].position_seconds == 786
    assert _revealed(engine, activity_id)[-1]["end_seconds"] == 786


def test_watch_and_wait_start_separately_and_control_independently(engine_factory) -> None:
    """@brief 单项工具可建立相容组合；暂停点名播放不停止占位等待。"""
    engine = engine_factory()
    stage_day_plan(engine)
    start = engine.state.current_time
    watching = _action(engine, "listen-with-wait", "start_activity",
                       StartActivityRequest(activity_type="watch", target_id=ITEM_ID,
                                                    planned_duration_seconds=786, playback_speed=1))
    wake_by_mail(engine, offset=1)
    _advance_to(engine, start + 1)
    waiting = _action(engine, "wait-while-listening", "start_activity",
                      StartActivityRequest(activity_type="wait", planned_duration_seconds=120))
    assert engine.state.activities[watching.activity_id].status == "active"
    assert engine.state.activities[waiting.activity_id].status == "active"
    wake_by_mail(engine, offset=1)
    _advance_to(engine, start + 2)
    _action(engine, "pause-listening-only", "set_activity_state",
            SetActivityStateRequest(activity_id=watching.activity_id, operation="pause"))
    assert engine.state.activities[watching.activity_id].status == "paused"
    assert engine.state.activities[waiting.activity_id].status == "active"
    media = engine.state.activities[watching.activity_id].media_position_seconds
    for _ in range(64):
        if engine.state.activities[waiting.activity_id].status == "completed":
            break
        assert advance_until_ready(engine)
    assert engine.state.activities[waiting.activity_id].status == "completed"
    assert engine.state.activities[watching.activity_id].media_position_seconds == media
