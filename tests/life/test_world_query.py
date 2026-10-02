"""世界查询的严格类型、冻结权限、交集筛选和完整分页行为。"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query


def _observation(count=47, page_size=20):
    """公开冻结页夹具，每个商品保留零金额和不可用状态，不含任何秘密字段。"""
    entries = [{"identity": f"supply:{index:03}", "category": "食品", "aliases": [f"商店出售的食品{index:03}"],
                "match": {"name": f"商店出售的食品{index:03}", "kind": "product", "state": None,
                          "actions": [{"way": "到店", "available": index % 2 == 0,
                                       "reason": None if index % 2 == 0 else "Out of stock", "price": 0}],
                          "tools": [], "candidates": []}}
               for index in range(count)]
    return {"world_query_bindings": list(reversed(entries)), "world_query_page_size": page_size,
            "tool_layer": {"callable_index": {"query_categories": [{"category": "食品", "count": count}]}}}


def _request(**fields):
    """固定四个必填字段，用显式过滤条件构造查询。"""
    return QueryWorldRequest(**{"name": None, "way": None, "category": "食品", "offset": 0, **fields})


@pytest.mark.parametrize("offset", [True, False, "0", "20", 0.0, 1.5, -1, None])
def test_world_query_offset_requires_nonnegative_json_integer(offset):
    """布尔、数值字符串、小数和负数不能被类型转换为合法分页位置。"""
    with pytest.raises(ValidationError) as caught:
        _request(offset=offset)
    assert any(error["loc"] == ("offset",) for error in caught.value.errors())


@pytest.mark.parametrize("field", ["name", "way", "category", "offset"])
def test_world_query_requires_every_declared_field(field):
    """首次查询也不能省略可空字段。"""
    payload = {"name": None, "way": "到店", "category": None, "offset": 0}
    del payload[field]
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest(**payload)
    assert caught.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["name", "way", "category"])
def test_world_query_rejects_blank_and_coerced_filters(field):
    """字符串条件不能为空白，数字不能冒充名称或标签。"""
    for value in ("", " \n\t", 12, True):
        with pytest.raises(ValidationError):
            _request(**{field: value})


def test_world_query_null_filter_errors_keep_affected_field_locations():
    """跨字段验证报告全部依赖字段，没有空根位置。"""
    with pytest.raises(ValidationError) as caught:
        _request(category=None)
    assert {error["loc"] for error in caught.value.errors()} == {("name",), ("way",), ("category",)}


def test_world_query_paginates_complete_frozen_set_and_preserves_false_and_zero():
    """从首个窗口跨页遍历全集，包含尾页，返回副本且不改变冻结事实。"""
    observation = _observation()
    before = copy.deepcopy(observation)
    offset, collected, offsets = 0, [], []
    while True:
        request = _request(offset=offset)
        assert validate_world_query(observation, request) is None
        page = query_world(observation, request)
        assert set(page) == {"matches", "total_count", "next_offset"}
        assert page["total_count"] == 47
        collected.extend(page["matches"])
        offsets.append(page["next_offset"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert offsets == [20, 40, None]
    assert [item["name"] for item in collected] == [f"商店出售的食品{index:03}" for index in range(47)]
    assert collected[1]["actions"][0]["available"] is False
    assert collected[0]["actions"][0]["price"] == 0
    collected[0]["name"] = "changed"
    assert observation == before
    assert query_world(observation, _request(offset=47)) == {"matches": [], "total_count": 47, "next_offset": None}


def test_world_query_intersection_uses_existing_deterministic_name_and_way_rules():
    """名称归一和方式同义闭集取交集，不通过模糊文字猜目标。"""
    observation = _observation()
    exact = query_world(observation, _request(name="商店出售的食品003", way="到店"))
    assert exact["total_count"] == 1
    assert query_world(observation, _request(name="商店出售的食品003", way="食用"))["matches"] == []
    assert query_world(observation, _request(name="可能想吃东西"))["matches"] == []


def test_world_query_reports_all_dynamic_errors_and_full_legal_values():
    """动态标签与方式独立报错，合法闭集不受三条候选限制。"""
    observation = _observation()
    report = validate_world_query(observation, _request(way="随便做", category="商品"))
    payload = report.payload()
    assert set(payload) == {"error_code", "reason", "fix", "items", "unmet"}
    assert {item["path"][-1] for item in payload["items"]} == {"way", "category"}
    way, category = payload["items"]
    assert len(way["allowed_values"]) > 3
    assert category["allowed_values"] == ["食品"]
    assert all(item["expected"] and item["why"] for item in payload["items"])
    bad = validate_world_query(observation, _request(offset=48)).payload()["items"][0]
    assert bad["path"] == ["tool_calls", 0, "function", "arguments", "offset"]
    assert bad["written"] == 48 and bad["allowed_values"] == [] and "0..47" in bad["expected"]


def test_world_query_does_not_return_internal_identity_or_private_evidence():
    """私有排序身份与别名只在宿主快照中，查询结果无额外资料泄露。"""
    observation = _observation()
    result = query_world(observation, _request())
    serialized = json.dumps(result)
    for field in ("identity", "aliases", "object_id", "seller_account_id", "answer", "inventory"):
        assert f'"{field}"' not in serialized


def test_world_query_real_engine_reads_all_goods_without_browse_or_world_changes(engine_factory):
    """真实冻结供给能够完整翻页；查询不改变世界、证据或呈现凭据。"""
    from tests.life.conftest import context, stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "query-frozen"))
    before = engine.state.model_dump(mode="json")
    evidence_seq = engine.store.last_event_seq
    goods = [entry for entry in observed["world_query_bindings"] if entry["identity"].startswith("supply:")]
    assert len(goods) > engine.scenario.candidate_presentation_limit
    categories = sorted({entry["category"] for entry in goods})
    for category in categories:
        offset, names = 0, []
        while True:
            request = _request(category=category, offset=offset)
            assert validate_world_query(observed, request) is None
            page = query_world(observed, request)
            names.extend(row["name"] for row in page["matches"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        expected = {entry["match"]["name"] for entry in observed["world_query_bindings"]
                    if entry["category"] == category}
        assert set(names) == expected and len(names) == len(expected)
    assert engine.state.model_dump(mode="json") == before
    assert engine.store.last_event_seq == evidence_seq


def test_environment_read_query_keeps_world_clock_round_and_store_unchanged(engine_factory):
    """通过真实环境即时查询，禁止写状态、消费生活轮数或追加世界事件。"""
    import asyncio

    from custom.envs.life_env import LifeEnv
    from tests.life.conftest import context, stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine)
    frozen = context(engine, "query-environment")
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    environment = LifeEnv()
    environment.engine = engine
    before, sequence = engine.state.model_dump(mode="json"), engine.store.last_event_seq
    category = observed["tool_layer"]["callable_index"]["query_categories"][0]["category"]
    request = _request(category=category)
    result = asyncio.run(environment.read_query(frozen, "query_world", request))
    assert result == query_world(observed, request)
    assert engine.state.model_dump(mode="json") == before
    assert engine.store.last_event_seq == sequence
