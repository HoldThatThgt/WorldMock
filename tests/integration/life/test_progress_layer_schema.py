"""真实进度层通过层校验：每个参数是一种情形，经真实请求、引擎与 SQLite 走到那一刻，观察里的进度层交给
validate_layer。"""

from __future__ import annotations

import pytest

from tests.life.conftest import advance_until_ready, context, do_at, progress_calls, purchase, travel, use_app
from tests.life.environment_runtime import act
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


def _purchase_in_progress(engine_factory) -> dict:
    """经饿了么买柠檬茶，购买进行中：人在家写饿了么，订单从三杯咖啡经饿了么下出；受理后立即观察，这条请求的结果是
    带购买明细的第一条，明细一行的 channel 是「饿了么」。"""
    engine = engine_factory()
    _, accepted = act(engine, "tea", purchase("柠檬茶", prefer="饿了么"))
    assert accepted.status == "in_progress", accepted.error_code
    assert engine.context.state.activities[accepted.activity_id].status == "active"
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_side_activity")[0]["result"]
    assert result["status"] == "in_progress"
    assert [row["channel"] for row in result["details"]["purchases"]] == ["饿了么"]
    return observed


def _app_in_progress_here(engine_factory) -> dict:
    """用内容 app 进行中、记录在这一段之内：打开哔哩哔哩，第一次重估时观察；这条请求的结果是只带 planned 与 content
    的第一条，没有 goods。"""
    engine = engine_factory(mutate=_attentive)
    act(engine, "video", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_side_activity")[0]["result"]
    assert result["status"] == "in_progress" and set(result["details"]) == {"planned", "content"}
    return observed


def _app_in_progress_carried(engine_factory) -> dict:
    """用内容 app 进行中、人物带着它出发：在家打开哔哩哔哩，重估时出发去公司，在路上那一段观察；这项 app 使用仍在
    进行。带入路上那一段的打开记录与这一段里的任何副工具记录都不带 goods。"""
    engine = engine_factory(mutate=_attentive)
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    _, trip = act(engine, "leave", travel("公司"))
    assert trip.status == "in_progress", trip.error_code
    assert engine.context.state.activities[video.activity_id].status == "active"
    observed = engine.observe(context(engine, "on-the-road"))
    assert observed["environment_layer"]["location"]["session_kind"] == "transit"
    assert all("goods" not in row["result"]["details"] for row in progress_calls(observed, "start_side_activity"))
    return observed


@pytest.mark.parametrize("situation", [
    pytest.param(_location_activity_in_progress, id="location-activity-in-progress"),
    pytest.param(_purchase_in_progress, id="purchase-in-progress"),
    pytest.param(_app_in_progress_here, id="app-in-progress-in-this-segment"),
    pytest.param(_app_in_progress_carried, id="app-in-progress-carried-into-the-next-segment"),
])
def test_real_progress_layer_passes_layer_validation(engine_factory, situation):
    """走真实引擎到给定情形后，观察里的进度层通过层校验。"""
    validate_layer(situation(engine_factory)["progress_layer"])
