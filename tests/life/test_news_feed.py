"""信息流语料冻结、目录准入与碎片化消费结算测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, conclude_pending, conclude_request, context,
                                 wake_by_mail)
from worldmock.corpora import load_manifest
from worldmock.life.content import load_subtitles
from worldmock.life.observe import _content_catalog
from worldmock.life.models import (
    active_activities,
    PauseActivityRequest,
    ReportStatusRequest,
    StartActivityRequest,
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
    observed = engine.observe(ctx)
    if tool_name == "start_activity":
        canonical_action, target_id = request
        choice = next((row, action) for row in observed["interaction_bindings"]
                      for action in row["actions"]
                      if row["object_id"] == target_id
                      and action["canonical_action"] == canonical_action)
        request = StartActivityRequest(targets=[choice[0]["object"]], actions=[choice[1]["label"]])
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _wait_awake(engine, rounds: int = 1, seconds: int = 900) -> None:
    """用完成的 wait 活动积累娱乐需要（边等边刷：净累积=清醒累积−被动缓解）。

    等待按计划结束即成待收尾，人物在同一秒给它收尾后再做别的（时间与能量规格第七章「活动一结束就要结束它」）。
    """
    for n in range(rounds):
        assert seconds == engine.scenario.activity_feedback_seconds
        _act(engine, f"wait-{n}", "start_activity", ("wait", "agent:1"))
        advance_next(engine)
        assert conclude_pending(engine, f"wait-{n}-end").status == "completed"


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
        # 信息流纳入内容品类准入（CONTENT_CORPORA）后逐条补齐元数据三字段。
        assert set(item) == {"item_id", "title", "body", "category",
                             "estimated_read_seconds", "genre", "language", "age_rating"}
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
        assert feed.kind == "news_feed"
        assert feed.timeline_path == "feed/news-feed.srt"
        assert feed.entertainment_relief_per_hour == FEED_RATE
        assert feed.experience_label == "casual_news_feed_scroll"
        # docs/dev/SPEC-life-realism.md「画像编译」：随机偏好浮点删除——场景人物不再携带任何偏好浮点键。
        doc = json.loads(Path(f"scenarios/{name}").read_text(encoding="utf-8"))
        assert all("entertainment_preferences" not in agent for agent in doc["agents"])
    engine = engine_factory()
    # 完整目录供引擎形成轮转展示窗口；这里锁定两个基线条目的分段元数据。
    agent = engine.state.agents[1]
    catalog = _content_catalog(engine.scenario, engine.rules.subtitles,
                               engine.rules.content_titles, agent.content_progress)
    feed_catalog = [row for row in catalog if row["kind"] == "news_feed"]
    sintel_catalog = [row for row in catalog if row["kind"] == "subtitle_experience"]
    expected_entries = [
        {"item_id": "sintel-en-subtitles", "kind": "subtitle_experience",
         "duration_seconds": 630, "entertainment_relief_per_hour": 0.3,
         "experience_label": "subtitle_experience", "title": "Sintel",
         "segments_total": 26, "typical_segment_seconds": 24.2},
        {"item_id": FEED_ID, "kind": "news_feed",
         "duration_seconds": 38032, "entertainment_relief_per_hour": FEED_RATE,
         "experience_label": "casual_news_feed_scroll", "title": "中文新闻流（1000 条标题+摘要）",
         "segments_total": 1000, "typical_segment_seconds": 38.0},
    ]
    assert expected_entries[1] in feed_catalog
    assert expected_entries[0] in sintel_catalog


def _seed_feed_taste(engine) -> None:
    """注入确定性口味权重（docs/dev/SPEC-life-realism.md「画像编译」：结算偏好源=taste_weights，卡片入库前测试注入）。

    经 staged 深拷贝 + 版本 +1 + 事件留痕提交，保持足迹库状态连续性。
    """
    from worldmock.footprints.models import EventDraft

    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"taste_weights": {FEED_ID: FEED_PREFERENCE}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_seed_feed_taste"})])
    engine.state = staged


def test_single_feed_watch_reveals_items_and_settles_by_contact_seconds(engine_factory) -> None:
    """一次短 watch 只揭示跨过的条目，能量按实际接触秒数结算。"""
    ests = [item["estimated_read_seconds"] for item in _items()]
    planned = ests[0] + ests[1]
    engine = engine_factory()
    _seed_feed_taste(engine)
    _wait_awake(engine)
    body = engine.semantics.body
    energy_before = engine.state.agents[1].body.energy_kcal
    need_before = engine.state.agents[1].body.entertainment_need
    # 纯等待只累计实际清醒时间的娱乐需要。
    wait_net = body.entertainment_need_per_awake_hour
    assert need_before == pytest.approx(wait_net * 900 / 3600)

    _act(engine, "scroll", "start_activity", ("watch", FEED_ID))
    activity_id = next(item.activity_id for item in active_activities(engine.state, 1) if item.kind.value == "watch")
    wake_by_mail(engine, offset=planned)
    advance_next(engine)
    _act(engine, "scroll-stop", "conclude_activity",
         conclude_request(activity_name(engine, activity_id), summary="阅读到当前段落。"))
    activity = engine.state.activities[activity_id]
    assert activity.status.name == "CANCELLED"
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
        (FEED_RATE * FEED_PREFERENCE - body.entertainment_need_per_awake_hour) * hours)


def test_repeated_short_feed_watches_keep_existing_repeat_dilution(engine_factory) -> None:
    """多次独立短 watch 沿用现行重复接触递减公式，不引入新动力学。"""
    ests = [item["estimated_read_seconds"] for item in _items()]
    durations = [ests[0] + ests[1], ests[2] + ests[3], ests[4] + ests[5]]
    engine = engine_factory()
    _seed_feed_taste(engine)
    _wait_awake(engine)
    need_before = engine.state.agents[1].body.entertainment_need
    relief = 0.0
    idle_growth = 0.0
    for n, duration in enumerate(durations, start=1):
        if n > 1:
            # 结束刷屏后没有进行中活动时同一秒再决策（时间与能量规格第一章规则 2）：两次刷屏之间以等待度过，
            # 下一次决策边界上先结束等待，刷屏仍单独结算。
            before_idle = engine.state.agents[1].body.entertainment_need
            waited = _act(engine, f"between-scrolls-{n}", "start_activity", ("wait", "agent:1"))
            advance_next(engine)
            _act(engine, f"between-scrolls-end-{n}", "conclude_activity",
                 conclude_request(activity_name(engine, waited.activity_id), summary="等够了。"))
            idle_growth += engine.state.agents[1].body.entertainment_need - before_idle
        need_at_step = engine.state.agents[1].body.entertainment_need
        _act(engine, f"scroll-{n}", "start_activity", ("watch", FEED_ID))
        activity_id = next(item.activity_id for item in active_activities(engine.state, 1)
                           if item.kind.value == "watch")
        wake_by_mail(engine, offset=duration)
        advance_next(engine)
        _act(engine, f"scroll-stop-{n}", "conclude_activity",
             conclude_request(activity_name(engine, activity_id), summary="阅读到当前段落。"))
        step = (FEED_RATE * FEED_PREFERENCE / n
                - engine.semantics.body.entertainment_need_per_awake_hour) * (duration / 3600)
        assert need_at_step - engine.state.agents[1].body.entertainment_need == pytest.approx(step)
        relief += step
    assert need_before - engine.state.agents[1].body.entertainment_need == pytest.approx(
        relief - idle_growth)
    flat = FEED_RATE * FEED_PREFERENCE * sum(durations) / 3600
    assert relief != pytest.approx(flat)
    progress = engine.state.agents[1].content_progress[FEED_ID]
    assert progress.position_seconds == sum(durations)
    assert progress.exposure_count == 3


def test_interrupted_feed_watch_accumulates_flat_rate_over_total_contact(engine_factory) -> None:
    """一次可中断的连续刷屏按总接触时长乘现行费率结算。"""
    engine = engine_factory()
    # 6 轮 wait（5400s）按清醒增长累积需求，高于 950s 刷屏的未钳制缓解量
    # 0.25×0.6×950/3600≈0.0396——保证断言观测的是未触底的总接触费率结算。
    _seed_feed_taste(engine)
    _wait_awake(engine, rounds=6)
    need_before = engine.state.agents[1].body.entertainment_need
    body = engine.semantics.body
    wait_net = body.entertainment_need_per_awake_hour
    assert need_before == pytest.approx(wait_net * 5400 / 3600)

    _act(engine, "long-scroll", "start_activity", ("watch", FEED_ID))
    activity_id = next(item.activity_id for item in active_activities(engine.state, 1) if item.kind.value == "watch")
    wake_by_mail(engine)  # 900 边界经 message_arrival 提供决策（节律合同）
    advance_next(engine)
    assert engine.state.current_time == int(engine.scenario.start_at.timestamp()) + 5400 + 900
    _act(engine, "pause-scroll", "pause_activity",
         PauseActivityRequest(task=[activity_name(engine, activity_id)], summary=["稍后继续阅读。"]))
    paused_at = engine.state.current_time
    # 暂停后没有进行中活动：同一秒开始等待，时钟才推进（时间与能量规格第一章规则 1、2）。
    _act(engine, "wait-while-paused", "start_activity", ("wait", "agent:1"))
    wake_by_mail(engine)
    advance_next(engine)
    assert engine.state.current_time == paused_at + 900
    # 等待在这一边界按计划结束：先当秒收尾，再恢复暂停的刷屏（第七章「活动一结束就要结束它」）。
    assert conclude_pending(engine, "wait-while-paused-end").status == "completed"
    _act(engine, "resume-scroll", "report_status",
         ReportStatusRequest(task=[activity_name(engine, activity_id)], summary=["继续阅读。"]))
    wake_by_mail(engine, offset=50)
    advance_next(engine)
    _act(engine, "finish-scroll", "conclude_activity",
         conclude_request(activity_name(engine, activity_id), summary="本次阅读结束。"))

    activity = engine.state.activities[activity_id]
    assert activity.status.name == "CANCELLED"
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
        FEED_RATE * FEED_PREFERENCE * 950 / 3600
        - body.entertainment_need_per_awake_hour * (950 + 900) / 3600)
