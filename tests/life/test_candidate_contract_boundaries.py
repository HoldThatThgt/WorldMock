"""本轮候选窗口与人物可见对象的边界。"""

from __future__ import annotations


def test_presentation_windows_obey_frozen_limit(engine_factory) -> None:
    """@brief 内容、游戏与生活活动共用正式场景的窗口上限。"""
    engine = engine_factory()
    windows = engine._menu_windows(engine.state.agents[1])
    limit = engine.scenario.candidate_presentation_limit
    assert set(windows) == {"content", "game", "lifestyle"}
    for window in windows.values():
        assert 0 < len(window.items) <= limit
        assert window.total >= len(window.items)


def test_public_window_has_only_currently_bound_objects(engine_factory) -> None:
    """@brief 人物动作对象与引擎私有身份绑定逐项对应。"""
    engine = engine_factory()
    observed = engine._observation_data(engine.state.agents[1])
    public = observed["facts"]["interactive_objects"]
    bindings = observed["interaction_bindings"]
    assert [row["object"] for row in public] == [row["object"] for row in bindings]
    assert all("object_id" not in row for row in public)
    assert all("object_id" in row for row in bindings)
