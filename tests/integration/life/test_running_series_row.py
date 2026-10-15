"""进行中的地点系列在工具层活动目标行写正在做第几集、哪一集供一餐：state 是「第 N 集，共 M 集；<供餐说法>；<客观进度>」；
独立的一件事、一次 app 使用、暂停与待收尾的系列只有客观进度；凭状态的系列做着时单元行仍不列出
（docs/design/06-agent.md「提示与工具」）。

都经真实请求、引擎与 SQLite：meal_environment 副本的「做晚饭」三集是《备菜做饭》1800 秒、《吃晚饭》1500 秒（供一餐）、
《洗碗收拾厨房》900 秒；运行从 2026 年 1 月 5 日（周一）08:00 开始，人在家，重估间隔 300 秒。
"""

from __future__ import annotations

import pytest

from tests.integration.life.test_grocery_cooking import _consumed_dinner, grocery_engine, home_units
from tests.life.conftest import do_at, pause_request, report_request, target_tools, use_app
from tests.life.environment_inputs import catalog_rows, meal_environment, write_catalog_rows
from tests.life.environment_runtime import act, every_five_minutes, observe_only, reach, until_ended
from worldmock.life import activity_control
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer

_DINNER = "进行做晚饭（1月5日 08:00 起）"
#: 规格算例：开始后的秒数、正在做的内容、活动目标行的 state、家下「做晚饭」一行的 note。
_RUNNING = [
    (300, "dinner-1-cook", "第 1 集，共 3 集；第 2 集供一餐；已做约 5 分钟，还剩约 25 分钟",
     "第 1 集，共 3 集；上次没做完，接着做；第 2 集供一餐"),
    (2100, "dinner-2-eat", "第 2 集，共 3 集；第 2 集供一餐；已做约 35 分钟，还剩约 20 分钟",
     "第 2 集，共 3 集；上次没做完，接着做；第 2 集供一餐"),
    (3600, "dinner-3-wash", "第 3 集，共 3 集；第 2 集供一餐；已做约 1 小时，还剩约 10 分钟",
     "第 3 集，共 3 集；上次没做完，接着做；第 2 集供一餐"),
]


def _target(observed: dict, name: str) -> dict:
    """观察工具层里的一个目标行。"""
    return next(row for row in observed["tool_layer"]["targets"] if row["name"] == name)


def _dinner_row(observed: dict) -> dict:
    """家下「做晚饭」那一行。"""
    return next(row for row in _target(observed, "家")["actions"] if row["detail"] == "做晚饭")


def _without_food(root):
    """副本里《吃晚饭》不再供餐：删去那一行的 completion_effects 并刷新清单。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    del next(row for row in rows if row["content_id"] == "dinner-2-eat")["completion_effects"]
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def _started(engine, request):
    """08:00 提交开始请求，返回活动身份与活动名。"""
    _, result = act(engine, "start", request)
    assert result.status == "in_progress", result.error_code
    return result.activity_id, engine.context.state.activities[result.activity_id].name


def _round_trips(layer: dict) -> None:
    """工具层通过层校验，紧凑渲染也通过并能还原成完整目标行。"""
    validate_layer(layer)
    compact = compact_tool_layer(layer)
    validate_layer(compact)
    assert expand_tool_layer(compact)["targets"] == layer["targets"]


def test_running_series_row_names_the_episode_and_the_meal_episode(engine_factory, tmp_path):
    """08:05、08:35、09:00 三次观察：活动目标行的 state 先写第几集、共几集与第 2 集供一餐，再写客观进度；家下「做晚饭」
    一行照旧不可用，note 与单元行同一写法；08:35 那一行的原因是槽位 1 被占，时长是这一集的余量。"""
    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=every_five_minutes)
    start = engine.context.state.current_time
    activity_id, name = _started(engine, do_at("家", "做晚饭"))
    assert name == _DINNER
    for offset, content_id, state, note in _RUNNING:
        reach(engine, start + offset)
        assert engine.context.state.current_time == start + offset
        assert engine.context.state.activities[activity_id].data["content_id"] == content_id
        observed = observe_only(engine, "dinner")
        target = _target(observed, _DINNER)
        assert (target["kind"], target["phase"], target["state"]) == ("activity", "进行中", state)
        row = _dinner_row(observed)
        assert (row["available"], row["note"]) == (False, note)
        if offset == 2100:
            assert row["reason"] == f"The first activity slot is occupied by {_DINNER}."
            assert row["duration"] == "约 20 分钟"
        _round_trips(observed["tool_layer"])


@pytest.mark.parametrize("variant", ["series-without-food", "standalone-unit", "app-use"])
def test_only_a_running_location_series_gets_the_episode_prefix(engine_factory, tmp_path, variant):
    """开始后 300 秒：没有集供餐的系列只写第几集与客观进度；供一餐的独立的「测试休息」只有客观进度；打开 Keep 的那一行
    就是客观进度，不写第几集。"""
    root = meal_environment(tmp_path / "meal")
    if variant == "series-without-food":
        _without_food(root)
    engine = engine_factory(root, mutate=every_five_minutes)
    start = engine.context.state.current_time
    request = {"series-without-food": do_at("家", "做晚饭"), "standalone-unit": do_at("家", "测试休息"),
               "app-use": use_app("Keep")}[variant]
    activity_id, name = _started(engine, request)
    reach(engine, start + 300)
    assert engine.context.state.current_time == start + 300
    state = _target(observe_only(engine, "running"), name)["state"]
    activity = engine.context.state.activities[activity_id]
    if variant == "series-without-food":
        assert state == "第 1 集，共 3 集；已做约 5 分钟，还剩约 25 分钟"
    elif variant == "standalone-unit":
        assert engine.context.catalog.contents["c-rest"].completion_effects.food == "meal"
        assert state == "已做约 5 分钟，还剩约 10 分钟"
    else:
        assert activity.data["app_id"] == "keep"
        assert state == activity_control.objective_progress(activity) and "集" not in state


def test_gated_series_row_stays_hidden_while_the_activity_row_names_the_episode(engine_factory, tmp_path):
    """凭 groceries 的同一个系列做着时：08:05 与 08:35 家下只有「测试休息」「测试闲聊」两件事（「做晚饭」与「做饭」都
    凭的状态已减到 0），活动目标行的 state 与 meal_environment 同一时刻的逐字相同。"""
    engine = grocery_engine(engine_factory, tmp_path, mutate=every_five_minutes, change=_consumed_dinner, initial=1)
    start = engine.context.state.current_time
    activity_id, name = _started(engine, do_at("家", "做晚饭"))
    assert name == _DINNER
    for offset, content_id, state, _ in _RUNNING[:2]:
        reach(engine, start + offset)
        assert engine.context.state.current_time == start + offset
        assert engine.context.state.activities[activity_id].data["content_id"] == content_id
        assert set(home_units(engine, "home")) == {"测试休息", "测试闲聊"}
        assert _target(observe_only(engine, "dinner"), _DINNER)["state"] == state


def test_paused_or_finished_series_keeps_only_its_progress(engine_factory, tmp_path):
    """08:05 暂停「做晚饭」：它不在工具层的目标里，待办层的 progress 只有客观进度；同一刻恢复后推进到三集做完，待收尾的
    活动目标行 state 只有「已做约 1 小时」，阶段是「刚结束」。"""
    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=every_five_minutes)
    start = engine.context.state.current_time
    activity_id, name = _started(engine, do_at("家", "做晚饭"))
    reach(engine, start + 300)
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    observed = observe_only(engine, "paused")
    assert name not in {row["name"] for row in observed["tool_layer"]["targets"]}
    [row] = observed["todo_layer"]["paused_activities"]
    assert (row["name"], row["progress"]) == (name, "已做约 5 分钟，还剩约 25 分钟")
    assert target_tools(observed, name)["report_status"]["available"] is True
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    until_ended(engine, activity_id)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "completed" and activity.data["content_id"] == "dinner-3-wash"
    target = _target(observe_only(engine, "finished"), name)
    assert (target["phase"], target["state"]) == ("刚结束", "已做约 1 小时")
