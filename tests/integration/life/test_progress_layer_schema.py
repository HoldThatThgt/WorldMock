"""真实进度层通过层校验：每个参数是一种情形，经真实请求、引擎与 SQLite 走到那一刻，观察里的进度层交给
validate_layer。"""

from __future__ import annotations

import pytest

from tests.life.conftest import (SIDE_TOOL, advance_next, advance_until_ready, conclude_request, context, do_at,
                                 progress_calls, purchase, query)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act
from worldmock.life.prompt_schemas import validate_layer


def _attentive(persona, config):
    """每五分钟到一次重估间隔：人物在一件事做到一半时就有决策机会。"""
    config["reconsideration_interval_seconds"] = 300


def _location_activity_in_progress(engine_factory, tmp_path) -> dict:
    """在地点做事进行中：点名「测试休息」，第一次重估时观察；这条请求的结果是带 content 的第一条。"""
    engine = engine_factory(mutate=_attentive)
    act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "during"))
    result = progress_calls(observed, "start_main_activity")[0]["result"]
    assert result["status"] == "in_progress" and "content" in result["details"]
    return observed


def _purchase_in_progress(engine_factory, tmp_path) -> dict:
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


def _answered_query(engine_factory, tmp_path) -> dict:
    """查完的查询：副本里零食加到 23 件，人在三杯咖啡查询「零食」「柠檬茶」；做完那一秒观察，这条请求的结果是带答案的
    第二条，答案里有可以下单的行、不可下单的行和「还有 N 件没列」。"""
    root = copy_environment(tmp_path / "snacks")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    rows.extend({"category": "snack", "goods_id": f"snack-{index:02d}", "name": f"零食{index:02d}", "price_fen": 500,
                 "store_kinds": ["restaurant" if index % 2 else "convenience_store"]} for index in range(22))
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))

    def at_cafe(persona, config):
        persona["initial_location"] = "三杯咖啡"

    engine = engine_factory(root, mutate=at_cafe)
    _, result = act(engine, "answered", query("零食", "柠檬茶"))
    while engine.context.state.activities[result.activity_id].status == "active":
        advance_next(engine)
    observed = engine.observe(context(engine, "answered-read"))
    answer = progress_calls(observed, SIDE_TOOL)[0]["result"]["details"]["query"]
    assert {row["now"] for row in answer["rows"]} == {"可以下单", "不可下单"} and answer["omitted"]
    return observed


def _unfinished_query(engine_factory, tmp_path) -> dict:
    """没有查完的查询：每 60 秒一次重估，查询进行中用 conclude_activity 结束它；结果是「没有查完」的第二条。"""
    def attentive(persona, config):
        config["reconsideration_interval_seconds"] = 60

    engine = engine_factory(mutate=attentive)
    _, result = act(engine, "unfinished", query("柠檬茶"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[result.activity_id].name
    act(engine, "stop", conclude_request(name), "conclude_activity")
    observed = engine.observe(context(engine, "unfinished-read"))
    assert progress_calls(observed, SIDE_TOOL)[0]["result"]["details"]["query"]["state"] == "没有查完"
    return observed


@pytest.mark.parametrize("situation", [
    pytest.param(_location_activity_in_progress, id="location-activity-in-progress"),
    pytest.param(_purchase_in_progress, id="purchase-in-progress"),
    pytest.param(_answered_query, id="answered-query"),
    pytest.param(_unfinished_query, id="unfinished-query"),
])
def test_real_progress_layer_passes_layer_validation(engine_factory, tmp_path, situation):
    """走真实引擎到给定情形后，观察里的进度层通过层校验。"""
    validate_layer(situation(engine_factory, tmp_path)["progress_layer"])
