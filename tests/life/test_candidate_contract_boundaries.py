"""本轮候选窗口与人物可见对象的边界。"""

from __future__ import annotations


def test_presentation_windows_obey_frozen_limit(engine_factory) -> None:
    """@brief 生活活动窗口遵守当前场景上限，app 直接列出而不进入旧内容和游戏窗口。"""
    engine = engine_factory()
    windows = engine.observations._menu_windows(engine.context.state.agents[1])
    limit = engine.context.scenario.candidate_presentation_limit
    assert set(windows) == {"lifestyle"}
    for window in windows.values():
        assert 0 < len(window.items) <= limit
        assert window.total >= len(window.items)
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    apps = [row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "app"]
    assert set(apps) == set(engine.context.semantics.catalog.apps)


def test_public_window_has_only_currently_bound_objects(engine_factory) -> None:
    """@brief 人物动作对象与引擎私有身份绑定逐项对应。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    public = [row for row in observed["tool_layer"]["targets"] if row["kind"] != "activity"]
    bindings = observed["interaction_bindings"]
    assert [row["name"] for row in public] == [row["object"] for row in bindings]
    assert all("object_id" not in row for row in public)
    assert all("object_id" in row for row in bindings)
