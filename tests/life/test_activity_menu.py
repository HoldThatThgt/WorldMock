"""活动目录摘要与本轮可读对象窗口。"""

from __future__ import annotations


def test_observation_keeps_category_counts_outside_model_facts(engine_factory) -> None:
    """@brief 商品候选池的类别计数仅供宿主验证，不混入人物工具层。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    summary = observed["activity_summary"]
    assert summary == {"drink": 1, "meal": 1, "snack": 1}
    assert "content_catalog" not in observed["tool_layer"] and "activity_summary" not in observed["tool_layer"]
    assert observed["tool_layer"]["targets"]


def test_presented_apps_use_readable_names(engine_factory) -> None:
    """@brief 目录动作的绑定应用提供可读名称，应用与内容均不作为直接活动目标。"""
    engine = engine_factory()
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    bindings = observed["interaction_bindings"]
    actions = [row for row in bindings if row["kind"] == "action"]
    assert actions and {row["object_id"] for row in actions} == set(engine.context.semantics.catalog.actions)
    assert all(row["object"] != row["object_id"] for row in actions)
    assert not {"content", "game", "app"} & {row["kind"] for row in bindings}
    public = {row["name"]: row for row in observed["tool_layer"]["targets"]}
    catalog = engine.context.semantics.catalog
    for row in actions:
        current = public[row["object"]]
        assert current["kind"] == "action"
        assert {(item["action"], item["way"]) for item in current["actions"]} == {("interact", "进行")}
        assert {item["app"] for item in current["actions"]} == (
            {catalog.apps[app_id].name for app_id in catalog.actions[row["object_id"]].app_ids} or {None})
