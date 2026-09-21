"""分层活动菜单：activity_summary 摘要 + browse_activities 下钻的域内测试。"""

from __future__ import annotations

from worldmock.life.models import BrowseActivitiesRequest

from .conftest import (advance_next, browse_activities, browse_all, context,
                       submit_round, wake_by_mail)


def _browse_result(engine_factory, category: str, round_id: str = "menu"):
    """下钻一个品类并返回 (items, 结算结果)。"""
    engine = engine_factory()
    _, results = submit_round(engine, {1: ("browse_activities",
                                           BrowseActivitiesRequest(category=category, cursor=None))}, round_id)
    return engine, results[0]


def test_observation_carries_activity_summary_without_full_catalog(engine_factory) -> None:
    """观察只带品类计数：无 content_catalog，计数与场景装配一致。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "summary"))
    assert "content_catalog" not in observed
    summary = observed["activity_summary"]
    assert summary["feature_film"] == 90
    assert summary["drama_series"] == 10
    assert summary["podcast_episode"] == 1
    assert summary["webnovel_excerpt"] == 6
    assert summary["news_feed"] + summary["joke_feed"] == 5
    assert summary["subtitle_experience"] == 1
    # game_session 键同时覆盖内容侧的会话条目与场景声明的游戏身份（同一池、同一链路）
    assert summary["game_session"] == 80 + len(engine.scenario.games)
    assert summary["foods"] == len(engine.scenario.supplies)
    # 全局启用的生活活动按品类计数：sport=44、hobby=56（corpora/lifestyle）
    assert summary["sport"] == 44
    assert summary["hobby"] == 56
    # 观察不再逐条展开全部内容：内容品类之和等于场景内容总数
    assert sum(summary[kind] for kind in (
        "feature_film", "drama_series", "podcast_episode", "webnovel_excerpt", "news_feed",
        "joke_feed", "subtitle_experience")) + 80 == len(engine.scenario.contents)


def test_browse_content_kind_returns_full_items(engine_factory) -> None:
    """内容品类下钻经游标下推返回全量条目，形状与观看受理一致（分段/标题/进度）。"""
    engine = engine_factory()
    movies = browse_all(engine, "feature_film")
    assert len(movies) == 90
    first = next(item for item in movies if item["item_id"] == "movie-0001")
    assert first["kind"] == "feature_film"
    assert first["duration_seconds"] == 6780
    assert first["title"] == "一球定江山"
    assert first["segments_total"] == 1
    assert "progress" not in first


def test_browse_lifestyle_category_returns_catalog_entries(engine_factory) -> None:
    """生活活动品类下钻返回名称/时长/MET/一句话描述。"""
    engine = engine_factory()
    sport = browse_all(engine, "sport")
    assert len(sport) == 44
    yoga = next(item for item in sport if item["activity_id"] == "yoga")
    assert yoga["activity_type"] == "lifestyle:yoga"
    assert yoga["name"]
    assert yoga["category"] == "sport"
    assert yoga["typical_duration_minutes"] > 0
    assert yoga["met_value"] == engine.scenario.body.activity_met["lifestyle:yoga"]
    assert yoga["description"]


def test_browse_foods_returns_distinct_supply_facts_and_actual_visibility(engine_factory) -> None:
    """foods 逐页返回全部门店供给及其报价，凭据只来自有效期内实际返回的身份。"""
    from worldmock.life.projection import SUPPLY_VISIBILITY_TTL_SECONDS

    engine = engine_factory()
    foods = browse_all(engine, "foods")
    by_id = {item["supply_id"]: item for item in foods}
    assert len(foods) == len(by_id) == len(engine.scenario.supplies)
    assert set(by_id) == {item.supply_id for item in engine.scenario.supplies}
    for supply in engine.scenario.supplies:
        row = by_id[supply.supply_id]
        assert row["product_id"] == supply.product_id
        assert row["location_id"] == supply.location_id
        assert row["seller_account_id"] == supply.seller_account_id
        assert row["price_fen"] == row["quote"]["price_fen"]
        assert row["price_version"] == row["quote"]["price_version"]
    events = engine.store.read_agent_events("action_result", 1)
    expected = {row["supply_id"] for event in events
                if event.data.get("data", {}).get("tool") == "browse_activities"
                and engine.state.current_time - event.occurred_at <= SUPPLY_VISIBILITY_TTL_SECONDS
                for row in event.data["data"]["items"]}
    assert expected
    assert engine.state.agents[1].visible_supply_ids == expected


def test_browse_unknown_category_is_rejected(engine_factory) -> None:
    """不在 activity_summary 键闭集内的品类稳定拒绝。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="casino", cursor=None))}, "menu-bad")
    result = results[0]
    assert result.status.value == "rejected"
    assert result.error_code == "BROWSE_CATEGORY_NOT_FOUND"


def test_browse_is_pure_query_and_feeds_next_observation(engine_factory) -> None:
    """下钻不消耗内容、不开活动；结果进入下一观察的 recent_action_results。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="drama_series", cursor=None))},
        "menu-dramas")
    result = results[0]
    assert result.status.value == "completed"
    assert result.activity_id is None
    assert result.data["total"] == 10  # 分区总数（筛选后的候选规模），不是本次条数
    assert result.data["returned"] == len(result.data["items"])
    assert "count" not in result.data  # count 不再是返回体字段
    assert engine.state.agents[1].content_progress == {}  # 不进 content_progress

    observed = engine.observe(context(engine, "after-menu"))
    browsed = [item for item in observed["recent_action_results"]
               if item["result"].get("data", {}).get("tool") == "browse_activities"]
    assert len(browsed) == 1
    assert browsed[0]["result"]["data"]["category"] == "drama_series"
    assert {item["item_id"] for item in browsed[0]["result"]["data"]["items"]} == {
        entry.item_id for entry in engine.scenario.contents if entry.kind == "drama_series"}


def test_browse_while_active_activity_preserves_playback(engine_factory) -> None:
    """播放中的真实决策允许下钻，查询不停止或改写播放进度。"""
    import pytest

    from worldmock.errors import ProtocolError
    from worldmock.life.models import StartTargetedActivityRequest

    engine, result = _browse_result(engine_factory, "feature_film", "menu-first")
    assert result.status.value == "completed"
    # 紧接同一时刻再下钻：决策位已被占用，宿主协议层不再受理（轮未开）
    ctx = context(engine, "menu-second")
    with pytest.raises(ProtocolError):
        engine.begin_decision_round([ctx])
    # 推进到下一决策位启动 watch，邮件到达后形成播放中的新决策
    advance_next(engine)
    _, watch_results = submit_round(
        engine, {1: ("start_targeted_activity",
                     StartTargetedActivityRequest(activity_type="watch",
                                                  target_id="novel-0001",
                                                  planned_duration_seconds=1800,
                                                  playback_speed=1))},
        "menu-watch")
    assert watch_results[0].status.value == "in_progress"
    watch_id = watch_results[0].activity_id
    wake_by_mail(engine)  # 首个 900 边界经 message_arrival 提供决策（节律合同）
    advance_next(engine)
    before = engine.state.activities[watch_id].media_position_seconds
    _, browse_results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="sport", cursor=None))}, "menu-active")
    assert browse_results[0].status.value == "completed"
    assert engine.state.activities[watch_id].status == "active"
    assert engine.state.activities[watch_id].media_position_seconds == before


def test_motivation_internal_remain_top5_under_layered_menu(engine_factory) -> None:
    """分层菜单架构下动机提示仍产出 top-5 娱乐建议（采样上下文）。"""
    engine = engine_factory()
    hints = engine._motivation_internal(engine.state.agents[1])
    assert hints is not None
    candidates = hints["candidates"]
    assert len(candidates) == 5
    assert [item["rank"] for item in candidates] == [1, 2, 3, 4, 5]
    assert all(item["reason_text"] and 0 <= item["softmax_probability"] <= 1
               for item in candidates)
    # 求值上下文是品类摘要 + 每品类 top-2 采样，而不是全量 192 项
    profile = next(item.motivation for item in engine.scenario.agents
                   if item.motivation is not None)
    context_value = engine._motivation_context(engine.state.agents[1], profile)
    assert len(context_value.contents) <= 14
    assert sum(context_value.content_category_counts.values()) == len(engine.scenario.contents)
    sampled_kinds: dict[str, int] = {}
    for item in context_value.contents:
        sampled_kinds[item.kind] = sampled_kinds.get(item.kind, 0) + 1
    assert all(count <= 2 for count in sampled_kinds.values())
