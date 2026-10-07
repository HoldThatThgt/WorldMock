"""活动目录摘要与本轮可读对象窗口。"""

from __future__ import annotations


def test_observation_keeps_category_counts_outside_model_facts(engine_factory) -> None:
    """@brief 现行食品、主题与生活活动池的类别计数仅供宿主验证，不混入人物工具层。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    summary = observed["activity_summary"]
    assert summary == {"drink": 2, "meal": 2, "rest": 1, "snack": 2, "work_topic": 1}
    assert "content_catalog" not in observed["tool_layer"] and "activity_summary" not in observed["tool_layer"]
    assert observed["tool_layer"]["targets"]


def test_presented_apps_use_readable_names(engine_factory) -> None:
    """@brief 全部已登记 app 始终以可读名称供人物打开，内容与游戏不再作为直接目标。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    bindings = observed["interaction_bindings"]
    apps = [row for row in bindings if row["kind"] == "app"]
    assert apps and {row["object_id"] for row in apps} == set(engine.context.semantics.catalog.apps)
    assert all(row["object"] != row["object_id"] for row in apps)
    assert not {"content", "game"} & {row["kind"] for row in bindings}
    public = {row["name"]: row for row in observed["tool_layer"]["targets"]}
    assert all(row["object"] in public for row in apps)
    assert all(public[row["object"]]["kind"] == "app" for row in apps)
    assert all({(action["action"], action["way"]) for action in public[row["object"]]["actions"]}
               == {("interact", "打开")} for row in apps)
