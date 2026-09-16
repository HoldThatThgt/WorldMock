"""信息流语料冻结、目录准入与碎片化消费结算测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.corpora import load_manifest
from worldmock.life.content import load_subtitles
from worldmock.life.models import (
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)
from worldmock.life.scenario import load_scenario

FEED_ID = "news-feed"
FEED_RATE = 0.25
FEED_PREFERENCE = 0.6
SCENARIOS = ("multi-person.json", "white-collar-12-weeks.json")


def _items() -> list[dict]:
    """读取冻结的信息流条目。"""
    return json.loads(Path("corpora/feed/items.json").read_text(encoding="utf-8"))


def _act(engine, round_id: str, tool_name: str, request):
    """按真实观察、登记和结算顺序执行一个人物动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _wait_awake(engine, rounds: int = 1, seconds: int = 900) -> None:
    """用完成的 wait 活动积累娱乐需要。"""
    for n in range(rounds):
        _act(engine, f"wait-{n}", "start_basic_activity",
             StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=seconds))
        advance_next(engine)


def test_feed_corpus_freeze_matches_manifest_and_timeline() -> None:
    """冻结的 1000 条信息流与 manifest 哈希和时间轴逐条一致。"""
    manifest = load_manifest(Path("corpora"))
    paths = {entry.path for entry in manifest.entries}
    assert {"feed/items.json", "feed/news-feed.srt"} <= paths
    feed_entries = {entry.purpose for entry in manifest.entries if entry.path.startswith("feed/")}
    assert feed_entries == {"feed"}
    items = _items()
    assert len(items) == 1000
    assert [item["item_id"] for item in items] == [f"feed-{n:04d}" for n in range(1, 1001)]
    for item in items:
        chars = len(item["title"]) + len(item["body"])
        assert set(item) == {"item_id", "title", "body", "category", "estimated_read_seconds"}
        assert len(item["title"]) >= 8
        assert len(item["body"]) >= 30
        assert 150 <= chars <= 300
        assert item["estimated_read_seconds"] == round(chars / 5)
        assert 30 <= item["estimated_read_seconds"] <= 60
    scenario, _ = load_scenario("scenarios/white-collar-12-weeks.json")
    feed = next(item for item in scenario.contents if item.item_id == FEED_ID)
    cues = load_subtitles(feed, Path("corpora"))
    assert len(cues) == len(items)
    assert cues[-1].end_seconds == feed.duration_seconds
    assert feed.duration_seconds == sum(item["estimated_read_seconds"] for item in items)
    for cue, item in zip(cues, items):
        assert cue.text == f'{item["title"]}\n{item["body"]}'
        assert cue.end_seconds - cue.start_seconds == item["estimated_read_seconds"]


def test_both_scenarios_admit_feed_with_fragmentable_catalog_entry(engine_factory) -> None:
    """两份场景都把信息流接入 contents，人物目录可见碎片化条目。"""
    for name in SCENARIOS:
        scenario, _ = load_scenario(f"scenarios/{name}")
        feed = next(item for item in scenario.contents if item.item_id == FEED_ID)
        assert feed.kind == "subtitle_experience"
        assert feed.timeline_path == "feed/news-feed.srt"
        assert feed.entertainment_relief_per_hour == FEED_RATE
        assert feed.experience_label == "casual_news_feed_scroll"
        assert all(0 < agent.entertainment_preferences[FEED_ID] <= 1 for agent in scenario.agents)
    engine = engine_factory()
    catalog = engine.observe(context(engine, "catalog"))["content_catalog"]
    assert catalog == [
        {"item_id": "sintel-en-subtitles", "kind": "subtitle_experience",
         "duration_seconds": 630, "entertainment_relief_per_hour": 0.3,
         "experience_label": "subtitle_experience"},
        {"item_id": FEED_ID, "kind": "subtitle_experience",
         "duration_seconds": 38032, "entertainment_relief_per_hour": FEED_RATE,
         "experience_label": "casual_news_feed_scroll"},
    ]


def test_single_feed_watch_reveals_items_and_settles_by_contact_seconds(engine_factory) -> None:
    """一次短 watch 只揭示跨过的条目，能量按实际接触秒数结算。"""
    ests = [item["estimated_read_seconds"] for item in _items()]
    planned = ests[0] + ests[1]
    engine = engine_factory()
    _wait_awake(engine)
    body = engine.semantics.body
    energy_before = engine.state.agents[1].body.energy_kcal
    need_before = engine.state.agents[1].body.entertainment_need
    assert need_before == pytest.approx(0.08 * 900 / 3600)

    _act(engine, "scroll", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="watch", target_id=FEED_ID,
                                      planned_duration_seconds=planned, playback_speed=1))
    activity_id = engine.state.agents[1].current_activity_id
    advance_next(engine)
    activity = engine.state.activities[activity_id]
    assert activity.status.name == "COMPLETED"
    revealed = activity.data["revealed"]
    assert [cue["cue_id"] for cue in revealed["cues"]] == [1, 2]
    assert revealed["cues"][0]["text"] == f'{_items()[0]["title"]}\n{_items()[0]["body"]}'
    assert revealed["position_after_seconds"] == planned
    progress = engine.state.agents[1].content_progress[FEED_ID]
    assert progress.position_seconds == planned
    assert progress.exposure_count == 1
    hours = planned / 3600
    met = body.activity_met["watch"]
    expected_cost = (body.basal_kcal_per_hour + max(met - 1, 0) * body.mass_kg) * hours
    assert energy_before - engine.state.agents[1].body.energy_kcal == pytest.approx(expected_cost)
    assert need_before - engine.state.agents[1].body.entertainment_need == pytest.approx(
        FEED_RATE * FEED_PREFERENCE * hours)


def test_repeated_short_feed_watches_keep_existing_repeat_dilution(engine_factory) -> None:
    """多次独立短 watch 沿用现行重复接触递减公式，不引入新动力学。"""
    ests = [item["estimated_read_seconds"] for item in _items()]
    durations = [ests[0] + ests[1], ests[2] + ests[3], ests[4] + ests[5]]
    engine = engine_factory()
    _wait_awake(engine)
    need_before = engine.state.agents[1].body.entertainment_need
    relief = 0.0
    for n, duration in enumerate(durations, start=1):
        need_at_step = engine.state.agents[1].body.entertainment_need
        _act(engine, f"scroll-{n}", "start_targeted_activity",
             StartTargetedActivityRequest(activity_type="watch", target_id=FEED_ID,
                                          planned_duration_seconds=duration, playback_speed=1))
        advance_next(engine)
        step = FEED_RATE * FEED_PREFERENCE * (duration / 3600) / n
        assert need_at_step - engine.state.agents[1].body.entertainment_need == pytest.approx(step)
        relief += step
    assert need_before - engine.state.agents[1].body.entertainment_need == pytest.approx(relief)
    flat = FEED_RATE * FEED_PREFERENCE * sum(durations) / 3600
    assert relief != pytest.approx(flat)
    progress = engine.state.agents[1].content_progress[FEED_ID]
    assert progress.position_seconds == sum(durations)
    assert progress.exposure_count == 3


def test_interrupted_feed_watch_accumulates_flat_rate_over_total_contact(engine_factory) -> None:
    """一次可中断的连续刷屏按总接触时长乘现行费率结算。"""
    engine = engine_factory()
    _wait_awake(engine, rounds=3)
    need_before = engine.state.agents[1].body.entertainment_need
    assert need_before == pytest.approx(0.08 * 2700 / 3600)

    _act(engine, "long-scroll", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="watch", target_id=FEED_ID,
                                      planned_duration_seconds=950, playback_speed=1))
    activity_id = engine.state.agents[1].current_activity_id
    advance_next(engine)
    assert engine.state.current_time == int(engine.scenario.start_at.timestamp()) + 2700 + 900
    _act(engine, "pause-scroll", "set_activity_state",
         SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    paused_at = engine.state.current_time
    advance_next(engine)
    assert engine.state.current_time == paused_at + 1
    _act(engine, "resume-scroll", "set_activity_state",
         SetActivityStateRequest(activity_id=activity_id, operation="resume"))
    advance_next(engine)

    activity = engine.state.activities[activity_id]
    assert activity.status.name == "COMPLETED"
    assert activity.elapsed_seconds == 950
    progress = engine.state.agents[1].content_progress[FEED_ID]
    assert progress.exposure_count == 1
    assert progress.position_seconds == 950
    cues = [cue for event in engine.store.read_agent_events("activity_progress", 1)
            if event.activity_id == activity_id
            for cue in event.data["activity"]["data"]["revealed"]["cues"]]
    ids = [cue["cue_id"] for cue in cues]
    assert sorted(set(ids)) == list(range(1, len(set(ids)) + 1))
    assert 20 <= len(set(ids)) <= 30
    assert len(cues) - len(set(ids)) <= 1
    assert need_before - engine.state.agents[1].body.entertainment_need == pytest.approx(
        FEED_RATE * FEED_PREFERENCE * 950 / 3600)
