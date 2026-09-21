"""活动事件流内嵌内容标题的端到端查看器路径测试。

查看器只读 activity_progress / action_result 事件里的 activity 对象；
本文件按真实引擎驱动一段观看并直接从 footprints.sqlite 的查看器读取
路径断言 title 字段存在、值与语料 items.json 一致，且无标题活动不带该键。
"""

from __future__ import annotations

import json
from pathlib import Path

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.life.models import (
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)

DRAMA_ID = "drama-0002"


def _corpus_title(item_id: str) -> str:
    """以语料 items.json 为独立事实源读取真实标题。"""
    rows = json.loads(Path("corpora/drama/items.json").read_text(encoding="utf-8"))
    return next(row["title"] for row in rows if row["item_id"] == item_id)


def _observed_action(engine, round_id: str, tool_name: str, request):
    """按真实观察、登记和结算顺序执行一个人物动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    action_id = f"action-{round_id}"
    engine.submit_action(DirectToolCall(ctx, action_id, tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def test_activity_progress_and_action_result_embed_content_title(engine_factory) -> None:
    """观看活动的事件流携带与语料一致的 title，查看器读取路径直接可用。"""
    engine = engine_factory()
    started = _observed_action(
        engine, "watch-drama", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="watch", target_id=DRAMA_ID,
                                     planned_duration_seconds=1800, playback_speed=1))
    assert started.status.value == "in_progress", started.error_code
    advance_next(engine)  # 首个反馈边界：产出 activity_progress 结算段

    expected = _corpus_title(DRAMA_ID)
    # 查看器读取路径：从 footprints.sqlite 按 activity_progress 事件重建活动列表
    progress_events = engine.store.read_agent_events("activity_progress", 1)
    assert progress_events, "activity_progress events are missing from footprints"
    watched = [event for event in progress_events
               if event.data["activity"]["target_id"] == DRAMA_ID]
    assert watched, "drama activity is missing from activity_progress events"
    activity = watched[-1].data["activity"]
    assert activity["title"] == expected

    result_events = engine.store.read_agent_events("action_result", 1)
    started_results = [event for event in result_events
                       if event.action_id == "action-watch-drama"
                       and event.data["data"]["activity"]["target_id"] == DRAMA_ID]
    assert started_results, "start action_result event is missing"
    assert started_results[-1].data["data"]["activity"]["title"] == expected


def test_lifestyle_activity_embeds_name_zh_title(engine_factory) -> None:
    """lifestyle 活动以目录 name_zh 作为 title，等待类活动不带该键。"""
    engine = engine_factory()
    scenario = engine.scenario
    lifestyle_id = sorted(key.removeprefix("lifestyle:")
                          for key in scenario.body.activity_met
                          if key.startswith("lifestyle:"))[0]
    rows = json.loads(Path("corpora/lifestyle/items.json").read_text(encoding="utf-8"))
    expected = next(row["name_zh"] for row in rows["items"]
                    if row["activity_id"] == lifestyle_id)

    started = _observed_action(
        engine, "start-lifestyle", "start_basic_activity",
        StartBasicActivityRequest(activity_type=f"lifestyle:{lifestyle_id}",
                                  planned_duration_seconds=1800))
    assert started.status.value == "in_progress", started.error_code
    result_events = engine.store.read_agent_events("action_result", 1)
    lifestyle_results = [event for event in result_events
                         if event.action_id == "action-start-lifestyle"]
    assert lifestyle_results, "lifestyle action_result event is missing"
    assert lifestyle_results[-1].data["data"]["activity"]["title"] == expected
    category = next(row["category"] for row in rows["items"] if row["activity_id"] == lifestyle_id)
    assert lifestyle_results[-1].data["data"]["activity"]["category"] == category
    advance_next(engine)
    progress = engine.store.read_agent_events("activity_progress", 1)[-1]
    assert progress.data["activity"]["category"] == category
    assert progress.data["activity"]["location_id"] == engine.state.agents[1].location_id


def test_wait_activity_carries_no_title(engine_factory) -> None:
    """无内容目标的 wait 活动事件不带 title 键。"""
    engine = engine_factory()
    started = _observed_action(
        engine, "start-wait", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=600))
    assert started.status.value == "in_progress", started.error_code
    advance_next(engine)
    wait_events = [event for event in engine.store.read_agent_events("activity_progress", 1)
                   if event.data["activity"]["kind"] == "wait"]
    assert wait_events, "wait activity_progress events are missing"
    assert all("title" not in event.data["activity"] for event in wait_events)
    result = engine.store.read_agent_events("action_result", 1)[-1]
    assert "title" not in result.data["data"]["activity"]
