"""分层活动菜单：activity_summary 摘要 + browse_activities 下钻的域内测试。"""

from __future__ import annotations

from worldmock.life.models import BrowseActivitiesRequest

from .conftest import advance_next, browse_activities, context, submit_round


def _browse_result(engine_factory, category: str, round_id: str = "menu"):
    """下钻一个品类并返回 (items, 结算结果)。"""
    engine = engine_factory()
    _, results = submit_round(engine, {1: ("browse_activities",
                                           BrowseActivitiesRequest(category=category))}, round_id)
    return engine, results[0]


def test_observation_carries_activity_summary_without_full_catalog(engine_factory) -> None:
    """观察只带品类计数：无 content_catalog，计数与场景装配一致。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "summary"))
    assert "content_catalog" not in observed
    summary = observed["activity_summary"]
    assert summary["feature_film"] == 90
    assert summary["drama_series"] == 10
    assert summary["webnovel_excerpt"] == 6
    assert summary["news_feed"] + summary["joke_feed"] == 5
    assert summary["subtitle_experience"] == 1
    assert summary["game_session"] == 80
    assert summary["foods"] == len(engine.scenario.products)
    # 全局启用的生活活动按品类计数：sport=44、hobby=56（corpora/lifestyle）
    assert summary["sport"] == 44
    assert summary["hobby"] == 56
    # 观察不再逐条展开 192 项内容
    assert sum(summary[kind] for kind in (
        "feature_film", "drama_series", "webnovel_excerpt", "news_feed",
        "joke_feed", "subtitle_experience", "game_session")) == len(engine.scenario.contents)


def test_browse_content_kind_returns_full_items(engine_factory) -> None:
    """内容品类下钻返回与观看受理一致的条目形状（分段/标题/进度）。"""
    engine = engine_factory()
    movies = browse_activities(engine, "menu-movies", "feature_film")
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
    sport = browse_activities(engine, "menu-sport", "sport")
    assert len(sport) == 44
    yoga = next(item for item in sport if item["activity_id"] == "yoga")
    assert yoga["activity_type"] == "lifestyle:yoga"
    assert yoga["name"]
    assert yoga["category"] == "sport"
    assert yoga["typical_duration_minutes"] > 0
    assert yoga["met_value"] == engine.scenario.body.activity_met["lifestyle:yoga"]
    assert yoga["description"]


def test_browse_foods_returns_menu_level_products(engine_factory) -> None:
    """foods 下钻返回菜单级商品行，购买合同仍走 product_catalog 限时 browse。"""
    engine = engine_factory()
    foods = browse_activities(engine, "menu-foods", "foods")
    assert len(foods) == len(engine.scenario.products)
    by_id = {item["product_id"]: item for item in foods}
    product = engine.scenario.products[0].product_id
    assert by_id[product]["price_fen"] == engine.rules.products[product].price_fen
    assert "price_version" not in by_id[product]  # 合同字段不随菜单泄露
    assert engine.state.agents[1].visible_product_ids == set()  # 可见性门控未被动


def test_browse_unknown_category_is_rejected(engine_factory) -> None:
    """不在 activity_summary 键闭集内的品类稳定拒绝。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="casino"))}, "menu-bad")
    result = results[0]
    assert result.status.value == "rejected"
    assert result.error_code == "BROWSE_CATEGORY_NOT_FOUND"


def test_browse_is_pure_query_and_feeds_next_observation(engine_factory) -> None:
    """下钻不消耗内容、不开活动；结果进入下一观察的 recent_action_results。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="drama_series"))},
        "menu-dramas")
    result = results[0]
    assert result.status.value == "completed"
    assert result.activity_id is None
    assert result.data["count"] == 10
    assert engine.state.agents[1].content_progress == {}  # 不进 content_progress

    observed = engine.observe(context(engine, "after-menu"))
    browsed = [item for item in observed["recent_action_results"]
               if item["result"].get("data", {}).get("tool") == "browse_activities"]
    assert len(browsed) == 1
    assert browsed[0]["result"]["data"]["category"] == "drama_series"
    assert {item["item_id"] for item in browsed[0]["result"]["data"]["items"]} == {
        entry.item_id for entry in engine.scenario.contents if entry.kind == "drama_series"}


def test_browse_while_active_activity_is_boundary_gated(engine_factory) -> None:
    """活动进行中下钻被反馈边界合同拒绝：菜单查询只在空闲决策位可用。"""
    import pytest

    from worldmock.errors import ProtocolError
    from worldmock.life.models import StartTargetedActivityRequest

    engine, result = _browse_result(engine_factory, "feature_film", "menu-first")
    assert result.status.value == "completed"
    # 紧接同一时刻再下钻：决策位已被占用，宿主协议层不再受理（轮未开）
    ctx = context(engine, "menu-second")
    with pytest.raises(ProtocolError):
        engine.begin_decision_round([ctx])
    # 推进到下一决策位启动 watch，反馈边界上的菜单查询被边界合同拒绝
    advance_next(engine)
    _, watch_results = submit_round(
        engine, {1: ("start_targeted_activity",
                     StartTargetedActivityRequest(activity_type="watch",
                                                  target_id="novel-0001",
                                                  planned_duration_seconds=1800,
                                                  playback_speed=1))},
        "menu-watch")
    assert watch_results[0].status.value == "in_progress"
    advance_next(engine)  # watch 的首个反馈边界
    _, browse_results = submit_round(
        engine, {1: ("browse_activities", BrowseActivitiesRequest(category="sport"))}, "menu-active")
    assert browse_results[0].status.value == "rejected"
    assert browse_results[0].error_code == "ACTIVITY_FEEDBACK_ACTION_REQUIRED"


def test_motivation_hints_remain_top5_under_layered_menu(engine_factory) -> None:
    """分层菜单架构下动机提示仍产出 top-5 娱乐建议（采样上下文）。"""
    engine = engine_factory()
    hints = engine._motivation_hints(engine.state.agents[1])
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
