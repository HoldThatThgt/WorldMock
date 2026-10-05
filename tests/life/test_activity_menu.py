"""活动目录摘要与本轮可读对象窗口。"""

from __future__ import annotations


def test_observation_keeps_category_counts_outside_model_facts(engine_factory) -> None:
    """@brief 完整目录仅供宿主验证，人物看到本轮窗口和品类摘要。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    summary = observed["activity_summary"]
    assert summary["feature_film"] == 90
    assert summary["drama_series"] == 10
    assert summary["game_session"] == 80 + len(engine.context.scenario.games)
    assert summary["foods"] == len(engine.context.scenario.supplies)
    assert "content_catalog" not in observed["tool_layer"] and "activity_summary" not in observed["tool_layer"]
    assert observed["tool_layer"]["targets"]


def test_presented_content_and_games_use_readable_names(engine_factory) -> None:
    """@brief 每个呈现的媒体与游戏对象通过语义名称选择。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    bindings = observed["interaction_bindings"]
    media = [row for row in bindings if row["kind"] in {"content", "game"}]
    assert media
    assert all(row["object"] != row["object_id"] for row in media)
    public = {row["name"]: row for row in observed["tool_layer"]["targets"]}
    assert all(row["object"] in public for row in media)
