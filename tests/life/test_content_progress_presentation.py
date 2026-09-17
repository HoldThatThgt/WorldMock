"""内容品类的分段结构呈现与观看进度跨暂停守护测试（browse_activities 下钻）。"""

from __future__ import annotations

from pathlib import Path

from agentsociety2.society import DirectToolCall

from tests.life.conftest import wake_by_mail, advance_next, browse_activities, context
from worldmock.life.content import load_subtitles
from worldmock.life.models import SetActivityStateRequest, StartTargetedActivityRequest
from worldmock.life.scenario import load_scenario

NOVEL_ID = "novel-0001"
NOVEL_SEGMENTS = 169
NOVEL_SECONDS = 84_837
PROGRESS_FIELDS = {"position_seconds", "remaining_seconds", "segments_total",
                   "segments_completed", "segments_remaining", "current_segment"}


def _observed_action(engine, round_id: str, tool_name: str, request):
    """按真实观察、登记和结算顺序执行一个人物动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    action_id = f"action-{round_id}"
    engine.submit_action(DirectToolCall(ctx, action_id, tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _novel_cues():
    """独立于引擎规则层加载网文真实字幕时间轴。"""
    scenario, _ = load_scenario("scenarios/white-collar-12-weeks.json")
    item = next(entry for entry in scenario.contents if entry.item_id == NOVEL_ID)
    return load_subtitles(item, Path("corpora"))


def _browsed_entry(engine, category: str, item_id: str, round_id: str) -> dict:
    """下钻一个内容品类并取出指定条目（round_id 必须唯一：动作按身份幂等）。"""
    entries = browse_activities(engine, round_id, category)
    return next(entry for entry in entries if entry["item_id"] == item_id)


def test_unstarted_entries_expose_segment_structure_without_progress(engine_factory) -> None:
    """未开始条目呈现章数与单章典型时长，且不带 progress 键。"""
    engine = engine_factory()
    novel = _browsed_entry(engine, "webnovel_excerpt", NOVEL_ID, "browse-novel-fresh")
    # 网文单本：169 章、每章约 502 秒，模型据此知道"读一章"是小步消费
    assert novel["segments_total"] == NOVEL_SEGMENTS
    assert novel["typical_segment_seconds"] == 502.0
    assert "progress" not in novel
    advance_next(engine)
    feed = _browsed_entry(engine, "news_feed", "news-feed", "browse-feed-fresh")
    assert feed["segments_total"] == 1000
    assert feed["typical_segment_seconds"] == 38.0
    advance_next(engine)
    movies = browse_activities(engine, "browse-feature_film", "feature_film")
    movie = next(entry for entry in movies if entry["item_id"] == "movie-0001")
    # 电影单 cue；全品类条目都带分段结构
    assert movie["segments_total"] == 1
    assert movie["typical_segment_seconds"] == 6780.0
    assert all(entry["segments_total"] >= 1 and entry["typical_segment_seconds"] > 0
               for entry in movies)


def test_started_entry_merges_progress_aligned_with_real_timeline(engine_factory) -> None:
    """已开始条目并入 progress：位置、剩余与已完成段数对齐真实字幕时间轴。"""
    engine = engine_factory()
    cues = _novel_cues()
    started = _observed_action(
        engine, "watch", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="watch", target_id=NOVEL_ID,
                                     planned_duration_seconds=1800, playback_speed=1))
    assert started.status.value == "in_progress"
    activity_id = started.activity_id
    wake_by_mail(engine)  # t0+900 边界经 message_arrival 提供决策：媒体位置 0->900
    advance_next(engine)
    _observed_action(engine, "pause", "set_activity_state",
                     SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    wake_by_mail(engine)  # 暂停后的下一决策边界（邮件唤醒）
    advance_next(engine)

    novel = _browsed_entry(engine, "webnovel_excerpt", NOVEL_ID, "browse-novel-started")
    completed = sum(1 for cue in cues if cue.end_seconds <= 900)
    assert set(novel["progress"]) == PROGRESS_FIELDS
    assert novel["progress"]["position_seconds"] == 900
    assert novel["progress"]["remaining_seconds"] == NOVEL_SECONDS - 900
    assert novel["progress"]["segments_total"] == NOVEL_SEGMENTS
    assert novel["progress"]["segments_completed"] == completed
    assert novel["progress"]["segments_remaining"] == NOVEL_SEGMENTS - completed
    assert novel["progress"]["current_segment"] == completed + 1
    wake_by_mail(engine)  # 下一次决策再下钻相邻品类
    advance_next(engine)
    assert "progress" not in _browsed_entry(engine, "feature_film", "movie-0001", "browse-movie-unstarted")


def test_pause_resume_keeps_content_position(engine_factory) -> None:
    """娱乐活动暂停后恢复，内容位置不丢失：进度跨暂停累积而非重置。"""
    engine = engine_factory()
    activity_id = _observed_action(
        engine, "watch", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="watch", target_id=NOVEL_ID,
                                     planned_duration_seconds=1800, playback_speed=1)
    ).activity_id
    wake_by_mail(engine)  # t0+900：位置 0->900（邮件唤醒提供决策）
    advance_next(engine)
    _observed_action(engine, "pause", "set_activity_state",
                     SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    wake_by_mail(engine)  # 暂停期间下钻：位置冻结在 900，不丢失也不前进
    advance_next(engine)
    paused_entry = _browsed_entry(engine, "webnovel_excerpt", NOVEL_ID, "browse-novel-paused")
    assert paused_entry["progress"]["position_seconds"] == 900
    wake_by_mail(engine)  # 恢复决策边界
    advance_next(engine)
    _observed_action(engine, "resume", "set_activity_state",
                     SetActivityStateRequest(activity_id=activity_id, operation="resume"))
    advance_next(engine)  # 恢复后推进到新结束边界：位置 900->1800

    progress = engine.state.agents[1].content_progress[NOVEL_ID]
    assert progress.position_seconds == 1800 and progress.exposure_count == 1
    completed = sum(1 for cue in _novel_cues() if cue.end_seconds <= 1800)
    # 活动已完成、人物回到空闲：直接下钻核对最终进度呈现
    final_entry = _browsed_entry(engine, "webnovel_excerpt", NOVEL_ID, "browse-novel-final")
    assert final_entry["progress"]["position_seconds"] == 1800
    assert final_entry["progress"]["segments_completed"] == completed
    assert final_entry["progress"]["current_segment"] == completed + 1

