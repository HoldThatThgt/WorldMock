"""真实进度层通过层校验：每个参数是一种情形，经真实请求、引擎与 SQLite 走到那一刻，观察里的进度层交给
validate_layer。"""

from __future__ import annotations

import pytest

from tests.life.conftest import (
    SIDE_TOOL, advance_next, advance_until_ready, conclude_request, context, do_at, progress_calls, use_app,
)
from tests.life.environment_runtime import act, settle_decision
from worldmock.life.prompt_schemas import validate_layer


def _attentive(persona, config):
    """每五分钟到一次重估间隔：人物在一件事做到一半时就有决策机会。"""
    config["reconsideration_interval_seconds"] = 300


def _location_activity_in_progress(engine_factory) -> dict:
    """在地点做事进行中：点名「测试休息」，第一次重估时观察；这条请求的结果是带 content 的第一条。"""
    engine = engine_factory(mutate=_attentive)
    act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "in_progress" and "content" in result["details"]
    return observed


def _until_ended(engine, activity_id: str) -> None:
    """推进到这项活动终结，其间的决策机会只播报进行中的活动；不替这项活动收尾。"""
    while engine.context.state.activities[activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"settle-{engine.context.state.current_time}")
        else:
            advance_next(engine)


def _location_activity_finished(engine_factory) -> dict:
    """在地点做事做完：「测试休息」放完后观察；这条请求的结果是带 contents 的第二条。"""
    engine = engine_factory()
    _, rest = act(engine, "named", do_at("家", "测试休息"))
    _until_ended(engine, rest.activity_id)
    observed = engine.observe(context(engine, "done"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "completed" and "contents" in result["details"]
    return observed


def _app_use_finished(engine_factory) -> dict:
    """用内容 app 做完：Keep 放完后观察；这条请求的结果是带 contents 的第二条。"""
    engine = engine_factory()
    _, used = act(engine, "keep", use_app("Keep"))
    _until_ended(engine, used.activity_id)
    observed = engine.observe(context(engine, "done"))
    result = progress_calls(observed, SIDE_TOOL)[0]["result"]
    assert result["status"] == "completed" and "contents" in result["details"]
    return observed


def _location_activity_ended_early(engine_factory) -> dict:
    """在地点做事被提前结束：「测试休息」做 5 分钟后用 conclude_activity 结束，观察里有 cancelled 的第二条结果与收尾记录。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    act(engine, "stop", conclude_request(name), "conclude_activity")
    observed = engine.observe(context(engine, "ended"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "cancelled" and "contents" in result["details"]
    assert progress_calls(observed, "conclude_activity")
    return observed


@pytest.mark.parametrize("situation", [
    pytest.param(_location_activity_in_progress, id="location-activity-in-progress"),
    pytest.param(_location_activity_finished, id="location-activity-finished"),
    pytest.param(_app_use_finished, id="app-use-finished"),
    pytest.param(_location_activity_ended_early, id="location-activity-ended-early"),
])
def test_real_progress_layer_passes_layer_validation(engine_factory, situation):
    """走真实引擎到给定情形后，观察里的进度层通过层校验。"""
    validate_layer(situation(engine_factory)["progress_layer"])
