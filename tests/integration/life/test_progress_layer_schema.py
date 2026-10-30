"""真实进度层通过层校验：每个参数是一种情形，经真实请求、引擎与 SQLite 走到那一刻，观察里的进度层交给
validate_layer。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.life.conftest import (
    SIDE_TOOL,
    advance_next,
    advance_until_ready,
    context,
    do_at,
    progress_calls,
    purchase,
    query,
    sleep,
    travel,
    use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, every_five_minutes, until_ended
from worldmock.agents.prompt.prompt_schemas import validate_layer


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


def _sleep_awaiting_conclusion(engine_factory) -> dict:
    """睡眠做完、待收尾：睡 30 分钟，醒来那一秒观察；带入的睡眠记录是 completed 的终态结果。"""
    engine = engine_factory()
    _, slept = act(engine, "nap", sleep(30))
    until_ended(engine, slept.activity_id)
    observed = engine.observe(context(engine, "woke"))
    (record,) = progress_calls(observed, "start_main_activity")
    assert record["arguments"]["action"] == "sleep" and record["result"]["status"] == "completed"
    return observed


def _trip_awaiting_conclusion(engine_factory) -> dict:
    """出行到达、待收尾：开车去公司，到达那一秒观察；带入的出行记录是带 arrival 的终态结果。"""
    engine = engine_factory()
    _, trip = act(engine, "drive", travel("公司", "驾车"))
    until_ended(engine, trip.activity_id)
    observed = engine.observe(context(engine, "arrived"))
    (record,) = progress_calls(observed, "start_main_activity")
    assert record["result"]["status"] == "completed" and "arrival" in record["result"]["details"]
    return observed


def _paid_purchase_awaiting_conclusion(engine_factory) -> dict:
    """购买付完款、待收尾：经饿了么买柠檬茶，付款完成那一秒观察；带入的购买记录是付了款的终态结果。"""
    engine = engine_factory()
    _, bought = act(engine, "tea", purchase("柠檬茶", prefer="饿了么"))
    until_ended(engine, bought.activity_id)
    observed = engine.observe(context(engine, "paid"))
    (record,) = progress_calls(observed, SIDE_TOOL)
    assert record["result"]["status"] == "completed"
    assert ["paid" in row for row in record["result"]["details"]["purchases"]] == [True]
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
    engine = engine_factory(mutate=every_five_minutes)
    act(engine, "video", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_side_activity")[0]["result"]
    assert result["status"] == "in_progress" and set(result["details"]) == {"planned", "content"}
    return observed


def _app_in_progress_carried(engine_factory) -> dict:
    """用内容 app 进行中、人物带着它出发：在家打开哔哩哔哩，重估时出发去公司，在路上那一段观察；这项 app 使用仍在
    进行。带入路上那一段的打开记录与这一段里的任何副工具记录都不带 goods。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    _, trip = act(engine, "leave", travel("公司"))
    assert trip.status == "in_progress", trip.error_code
    assert engine.context.state.activities[video.activity_id].status == "active"
    observed = engine.observe(context(engine, "on-the-road"))
    assert observed["environment_layer"]["location"]["session_kind"] == "transit"
    assert all("goods" not in row["result"]["details"] for row in progress_calls(observed, "start_side_activity"))
    return observed


def _answered_query(engine_factory) -> dict:
    """查完的查询：副本里零食加到 23 件，人在三杯咖啡查询「零食」「柠檬茶」；做完那一秒观察，这条请求的结果是带答案的
    第二条，答案里有可以下单的行、不可下单的行和「还有 N 件没列」。

    各组参数共用「只收 engine_factory」这一种调用，所以副本建在一个临时目录里：工厂带着 mutate 建引擎时把它再复制进
    自己的目录，建完就可以删。"""
    def at_cafe(persona, config):
        persona["initial_location"] = "三杯咖啡"

    with TemporaryDirectory() as scratch:
        root = copy_environment(Path(scratch) / "snacks")
        rows = catalog_rows(root, "merchant-catalog.jsonl")
        rows.extend({"category": "snack", "goods_id": f"snack-{index:02d}", "name": f"零食{index:02d}",
                     "price_fen": 500, "store_kinds": ["restaurant" if index % 2 else "convenience_store"]}
                    for index in range(22))
        write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))
        engine = engine_factory(root, mutate=at_cafe)
    _, result = act(engine, "answered", query("零食", "柠檬茶"))
    while engine.context.state.activities[result.activity_id].status == "active":
        advance_next(engine)
    observed = engine.observe(context(engine, "answered-read"))
    answer = progress_calls(observed, SIDE_TOOL)[0]["result"]["details"]["query"]
    assert {row["now"] for row in answer["rows"]} == {"可以下单", "不可下单"} and answer["omitted"]
    return observed


@pytest.mark.parametrize("situation", [
    pytest.param(_location_activity_in_progress, id="location-activity-in-progress"),
    pytest.param(_location_activity_finished, id="location-activity-finished"),
    pytest.param(_app_use_finished, id="app-use-finished"),
    pytest.param(_sleep_awaiting_conclusion, id="sleep-awaiting-conclusion"),
    pytest.param(_trip_awaiting_conclusion, id="trip-awaiting-conclusion"),
    pytest.param(_paid_purchase_awaiting_conclusion, id="paid-purchase-awaiting-conclusion"),
    pytest.param(_carried_record_finished, id="carried-record-finished"),
    pytest.param(_purchase_in_progress, id="purchase-in-progress"),
    pytest.param(_app_in_progress_here, id="app-in-progress-in-this-segment"),
    pytest.param(_app_in_progress_carried, id="app-in-progress-carried-into-the-next-segment"),
    pytest.param(_answered_query, id="answered-query"),
])
def test_real_progress_layer_passes_layer_validation(engine_factory, situation):
    """走真实引擎到给定情形后，观察里的进度层通过层校验。"""
    validate_layer(situation(engine_factory)["progress_layer"])
