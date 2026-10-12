"""真实进度层通过层校验：每个参数是一种情形，经真实请求、引擎与 SQLite 走到那一刻，观察里的进度层交给
validate_layer。"""

from __future__ import annotations

import pytest

from tests.life.conftest import (
    SIDE_TOOL, advance_until_ready, conclude_request, context, do_at, progress_calls, travel, use_app,
)
from tests.life.environment_runtime import act, act_group, every_five_minutes, until_ended
from worldmock.life.prompt_schemas import validate_layer


def _location_activity_in_progress(engine_factory) -> dict:
    """在地点做事进行中：点名「测试休息」，第一次重估时观察；这条请求的结果是带 content 的第一条。"""
    engine = engine_factory(mutate=every_five_minutes)
    act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "in_progress" and "content" in result["details"]
    return observed


def _location_activity_finished(engine_factory) -> dict:
    """在地点做事做完：「测试休息」放完后观察；这条请求的结果是带 contents 的第二条。"""
    engine = engine_factory()
    _, rest = act(engine, "named", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    observed = engine.observe(context(engine, "done"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "completed" and "contents" in result["details"]
    return observed


def _app_use_finished(engine_factory) -> dict:
    """用内容 app 做完：Keep 放完后观察；这条请求的结果是带 contents 的第二条。"""
    engine = engine_factory()
    _, used = act(engine, "keep", use_app("Keep"))
    until_ended(engine, used.activity_id)
    observed = engine.observe(context(engine, "done"))
    result = progress_calls(observed, SIDE_TOOL)[0]["result"]
    assert result["status"] == "completed" and "contents" in result["details"]
    return observed


def _location_activity_ended_early(engine_factory) -> dict:
    """在地点做事被提前结束：「测试休息」做 5 分钟后用 conclude_activity 结束，观察里有 cancelled 的第二条结果与收尾记录。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, rest = act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    act(engine, "stop", conclude_request(name), "conclude_activity")
    observed = engine.observe(context(engine, "ended"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "cancelled" and "contents" in result["details"]
    assert progress_calls(observed, "conclude_activity")
    return observed


def _carried_record_finished(engine_factory) -> dict:
    """带入的记录（活动已做完）：出发步行去公司并打开 Keep，Keep 在路上放完后观察；路上带入的记录是带 contents 的
    第二条。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, (_, used) = act_group(engine, "depart", travel("公司", "步行"), use_app("Keep"))
    until_ended(engine, used.activity_id)
    observed = engine.observe(context(engine, "road"))
    (record,) = progress_calls(observed, SIDE_TOOL)
    assert record["result"]["status"] == "completed" and "contents" in record["result"]["details"]
    assert record["occurred_at"] <= observed["progress_layer"]["session_started_at"]
    return observed


@pytest.mark.parametrize("situation", [
    pytest.param(_location_activity_in_progress, id="location-activity-in-progress"),
    pytest.param(_location_activity_finished, id="location-activity-finished"),
    pytest.param(_app_use_finished, id="app-use-finished"),
    pytest.param(_location_activity_ended_early, id="location-activity-ended-early"),
    pytest.param(_carried_record_finished, id="carried-record-finished"),
])
def test_real_progress_layer_passes_layer_validation(engine_factory, situation):
    """走真实引擎到给定情形后，观察里的进度层通过层校验。"""
    validate_layer(situation(engine_factory)["progress_layer"])
