"""进行中的地点系列在活动目标行的说法：只对进行中、不经 app、当前内容是系列一集的交互活动写「第 N 集，共 M 集」，有集供餐时
再接与单元行 note 同一写法的供餐说法（docs/design/06-agent.md「提示与工具」）。

目录是 meal_environment 副本经生产加载器读出的真实目录；活动是手工造的，只改种类、状态与活动私有数据。
"""

from __future__ import annotations

import pytest

from tests.life.environment_inputs import catalog_rows, meal_environment, write_catalog_rows
from tests.unit.test_environment_engine_admission import running
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.simulation_engine.activities.content.location_activities import running_series_note
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState


def _without_food(root):
    """副本里《吃晚饭》不再供餐：删去那一行的 completion_effects 并刷新清单。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    del next(row for row in rows if row["content_id"] == "dinner-2-eat")["completion_effects"]
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def _catalog(tmp_path, fed: bool = True):
    """meal_environment 副本的真实目录；fed 为假时《吃晚饭》不供餐。"""
    root = meal_environment(tmp_path / "meal")
    if not fed:
        _without_food(root)
    return load_catalog(root / "catalog", 7)


def _activity(content_id: str, app_id: str | None = None, **update) -> ActivityState:
    """一项进行中的手工交互活动，当前内容是给定内容；app_id 为空时是在地点做的事。"""
    base = running("interact", ActivityKind.INTERACT, "first" if app_id is None else "second",
                   presence=app_id is None)
    return base.model_copy(update={"data": {**base.data, "app_id": app_id, "content_id": content_id}, **update})


@pytest.mark.parametrize("fed,content_id,expected", [
    (True, "dinner-1-cook", "第 1 集，共 3 集；第 2 集供一餐"),
    (True, "dinner-2-eat", "第 2 集，共 3 集；第 2 集供一餐"),
    (True, "dinner-3-wash", "第 3 集，共 3 集；第 2 集供一餐"),
    (False, "dinner-1-cook", "第 1 集，共 3 集"),
], ids=["episode-one", "episode-two", "episode-three", "series-without-food"])
def test_running_location_series_names_its_episode_then_its_meal_episodes(tmp_path, fed, content_id, expected):
    """进行中的「做晚饭」写当前是第几集、共几集，再写第 2 集供一餐，不随做到第几集而变；没有集供餐的副本只写第几集。"""
    assert running_series_note(_catalog(tmp_path, fed), _activity(content_id)) == expected


def test_only_a_running_location_series_gets_a_note(tmp_path):
    """暂停的、已完成的系列活动，正在放 app 里系列一集的一次 app 使用，供一餐的独立的一件事，没有 content_id 的出行
    活动：都没有说法。"""
    catalog = _catalog(tmp_path)
    assert running_series_note(catalog, _activity("dinner-1-cook")) is not None
    trip = running("travel", ActivityKind.TRAVEL, "first")
    assert "content_id" not in trip.data
    for activity in (_activity("dinner-1-cook", status=ActivityStatus.PAUSED),
                     _activity("dinner-2-eat", status=ActivityStatus.COMPLETED),
                     _activity("c-drama-1", "bilibili"), _activity("c-rest"), trip):
        assert running_series_note(catalog, activity) is None
    assert catalog.contents["c-drama-1"].series is not None
    assert catalog.contents["c-rest"].completion_effects.food == "meal"
