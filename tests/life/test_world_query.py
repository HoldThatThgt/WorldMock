"""世界查询的严格类型、冻结权限、交集筛选、完整分页和紧凑返回行为。"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query


def _observation(count=47, page_size=20):
    """公开冻结页夹具，每个商品保留零金额和不可用状态，不含任何秘密字段；商品都属地点表里的「商店」，没有购买渠道。"""
    entries = [{"identity": f"supply:{index:03}", "category": "食品", "aliases": [f"商店出售的食品{index:03}"],
                "location_id": "store",
                "match": {"name": f"商店出售的食品{index:03}", "kind": "product", "state": None,
                          "actions": [{"way": "到店", "available": index % 2 == 0,
                                       "reason": None if index % 2 == 0 else "Out of stock", "price": 0}],
                          "tools": []}}
               for index in range(count)]
    return {"world_query_bindings": list(reversed(entries)), "world_query_channels": [],
            "world_query_page_size": page_size, "locations": [{"location_id": "store", "name": "商店"}],
            "home_location_id": "home",
            "tool_layer": {"callable_index": {"query_categories": [{"category": "食品", "count": count}]}}}


def _request(source=None, **fields):
    """固定六个必填字段（docs/dev/SPEC-errands-workday.md 5.1），用显式过滤条件构造查询；source 写到键 from。"""
    return QueryWorldRequest(**{"name": None, "way": None, "category": "食品", "from": source,
                                "available_only": False, "offset": 0, **fields})


@pytest.mark.parametrize("offset", [True, False, "0", "20", 0.0, 1.5, -1, None])
def test_world_query_offset_requires_nonnegative_json_integer(offset):
    """布尔、数值字符串、小数和负数不能被类型转换为合法分页位置。"""
    with pytest.raises(ValidationError) as caught:
        _request(offset=offset)
    assert any(error["loc"] == ("offset",) for error in caught.value.errors())


@pytest.mark.parametrize("field", ["name", "way", "category", "from", "available_only", "offset"])
def test_world_query_requires_every_declared_field(field):
    """六个键全部必填、无缺省（5.1）：首次查询也不能省略可空字段与 available_only，缺哪个就在哪个位置报缺字段。"""
    payload = {"name": None, "way": "到店", "category": None, "from": None, "available_only": False, "offset": 0}
    del payload[field]
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest(**payload)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [((field,), "missing")]


def test_world_query_rejects_the_retired_four_key_request():
    """旧四键请求按缺字段拒绝，不留读取分支（5.1）：错误恰好落在 from 与 available_only 两个新键上，都是缺字段；JSON
    入口（宿主的严格 JSON 校验）同样拒绝；来源键只认 JSON 键 from，代码里的字段名 from_ 是未声明的键。"""
    four = {"name": None, "way": None, "category": "食品", "offset": 0}
    for validate in (lambda: QueryWorldRequest(**four),
                     lambda: QueryWorldRequest.model_validate_json(json.dumps(four), strict=True)):
        with pytest.raises(ValidationError) as caught:
            validate()
        assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
            (("from",), "missing"), (("available_only",), "missing")]
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest(**{**four, "from_": None, "available_only": False})
    assert {(error["loc"], error["type"]) for error in caught.value.errors()} == {
        (("from",), "missing"), (("from_",), "extra_forbidden")}
    request = _request(source="商店")
    assert list(request.model_dump(mode="json")) == ["name", "way", "category", "from", "available_only", "offset"]
    assert request.from_ == "商店" and request.model_dump(mode="json")["from"] == "商店"


@pytest.mark.parametrize("value", ["true", "false", 0, 1, 1.0, None])
def test_world_query_available_only_requires_a_json_boolean(value):
    """available_only 是布尔：字符串、数字与 null 都被拒绝（5.1），Python 与 JSON 两个入口都落在 available_only 位置。"""
    payload = {"name": None, "way": None, "category": "食品", "from": None, "available_only": value, "offset": 0}
    for validate in (lambda: QueryWorldRequest(**payload),
                     lambda: QueryWorldRequest.model_validate_json(json.dumps(payload), strict=True)):
        with pytest.raises(ValidationError) as caught:
            validate()
        assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
            (("available_only",), "bool_type")]


@pytest.mark.parametrize("field", ["name", "way", "category", "from"])
def test_world_query_rejects_blank_and_coerced_filters(field):
    """字符串条件不能为空白，数字与布尔不能冒充名称、标签或来源。名称、方式与品类的空白写法由请求模型拒绝；来源的空白
    写法既不是地点显示名也不是渠道称呼，按 docs/dev/SPEC-errands-workday.md 5.5 第一行在 from 位置报查询参数错误，
    候选是换了 from 的完整查询（夹具里唯一有供给条目的门店「商店」）。"""
    for value in (12, True):
        with pytest.raises(ValidationError):
            _request(**{field: value})
    for value in ("", " \n\t"):
        if field != "from":
            with pytest.raises(ValidationError):
                _request(**{field: value})
            continue
        request = _request(source=value)
        [item] = validate_world_query(_observation(), request).payload()["items"]
        assert (item["path"][-1], item["written"], item["allowed_values"]) == ("from", value, [])
        assert item["expected"] == "null, a known place name, or a known channel name is required."
        assert [candidate["arguments"] for candidate in item["candidates"]] == [
            _request(source="商店").model_dump(mode="json")]


@pytest.mark.parametrize("available_only", [False, True])
def test_world_query_null_filter_errors_keep_affected_field_locations(available_only):
    """跨字段验证报告全部四个筛选位置，没有空根位置；available_only 不单独构成查询条件（5.1）。"""
    with pytest.raises(ValidationError) as caught:
        _request(category=None, available_only=available_only)
    assert [error["loc"] for error in caught.value.errors()] == [("name",), ("way",), ("category",), ("from",)]
    assert {error["msg"] for error in caught.value.errors()} == {
        "Value error, At least one query filter must be non-null"}


def test_world_query_paginates_complete_frozen_set_and_preserves_false_and_zero():
    """从首个窗口跨页遍历全集，包含尾页；每项是紧凑投影：null 与空集合（state、tools、可用动作的 reason）被略去，
    false、零价与不可用原因逐值保留，不带逐项候选；返回新结构，改动返回项不改变冻结事实。"""
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
    assert collected[1]["actions"][0]["reason"] == "Out of stock"
    assert collected[0]["actions"][0]["price"] == 0
    assert all(not {"candidates", "state", "tools"} & set(item) for item in collected)
    assert all("reason" not in action for item in collected for action in item["actions"] if action["available"])
    collected[0]["name"] = "changed"
    collected[1]["actions"][0]["available"] = True
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
    """动态标签与方式独立报错，合法闭集不受三条候选限制；未知来源与方式、品类错误一次全报，按方式、品类、来源的顺序，
    偏移量范围只在全部筛选合法后计算，所以越界的偏移量此时不另报（docs/dev/SPEC-errands-workday.md 5.5）。"""
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
    together = validate_world_query(observation, _request(source="仓库", way="随便做", category="商品", offset=999))
    items = together.payload()["items"]
    assert together.error_code == "ARGUMENTS_INVALID" and [item["error_code"] for item in items] == [
        "ARGUMENTS_INVALID"] * 3
    assert [(item["path"][-1], item["written"]) for item in items] == [
        ("way", "随便做"), ("category", "商品"), ("from", "仓库")]
    assert items[2]["allowed_values"] == [] and items[2]["candidates"]
    assert all(candidate["arguments"]["from"] == "商店" for candidate in items[2]["candidates"])


def test_world_query_does_not_return_internal_identity_or_private_evidence():
    """私有排序身份、别名、所属门店、品类与渠道索引只在宿主快照中，查询结果不带逐项候选，也无额外资料泄露。"""
    observation = _observation()
    result = query_world(observation, _request())
    serialized = json.dumps(result)
    for field in ("identity", "aliases", "object_id", "seller_account_id", "answer", "inventory", "location_id",
                  "candidates", "category", "channel_id", "store_location_ids"):
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


#: 返回项、动作与工具的键闭集（docs/dev/SPEC-errands-workday.md 5.4），以及任何位置都不得出现的内部键。
_MATCH_KEYS = {"name", "kind", "phase", "state", "contract", "actions", "tools"}
_ACTION_KEYS = {"action", "way", "available", "reason", "price", "duration", "quantity", "note"}
_TOOL_KEYS = {"tool", "available", "reason"}
_HIDDEN_KEYS = {"candidates", "identity", "aliases", "category", "location_id", "object_id"}


def _walk(value):
    """递归列出结果里每个字典键与其值（含嵌套在字典与列表里的），供紧凑与泄露断言使用。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _compact_pages(observed, frozen, environment, category):
    """沿 next_offset 遍历一个品类：每页先经宿主校验，宿主本地求值与环境 read_query 读回的观察逐值相等，返回全部页。"""
    import asyncio

    pages, offset = [], 0
    while offset is not None:
        request = _request(category=category, offset=offset)
        assert validate_world_query(observed, request) is None
        page = query_world(observed, request)
        assert asyncio.run(environment.read_query(frozen, "query_world", request)) == page
        pages.append(page)
        offset = page["next_offset"]
    return pages


def test_world_query_matches_are_compact_targets_without_candidates(engine_factory):
    """真实引擎 10:00 在家：返回项是冻结目标的无损紧凑投影——顶层恒为三键，项、动作与工具的键取自闭集，任何位置都没有
    null 与空集合；门店商品到店动作 available 为 false 且带原因、远程下单 available 为 true 且不带 reason 键，首个动作的
    来源标记仍在；返回里没有逐项候选与内部身份，冻结条目照存完整目标（null 保留、无候选），构造候选的函数已删除；宿主
    本地求值与环境 read_query 的页逐值相等（docs/dev/SPEC-errands-workday.md 5.4）。"""
    import worldmock.life.world_query as world_query
    from custom.envs.life_env import LifeEnv
    from tests.life.conftest import context, stage_day_plan
    from tests.life.purchase_worlds import at_clock

    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    frozen = context(engine, "query-compact")
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    environment = LifeEnv()
    environment.engine = engine
    before = copy.deepcopy(observed)
    entries = observed["world_query_bindings"]
    goods = {entry["match"]["name"]: entry for entry in entries if entry["identity"].startswith("supply:")}
    category = min(entry["category"] for entry in goods.values() if entry["category"] is not None)
    pages = _compact_pages(observed, frozen, environment, category)
    matches = [match for page in pages for match in page["matches"]]
    assert all(set(page) == {"matches", "total_count", "next_offset"} for page in pages)
    assert len(matches) == pages[0]["total_count"] > 0
    for match in matches:
        assert set(match) <= _MATCH_KEYS
        assert all(set(action) <= _ACTION_KEYS for action in match.get("actions", ()))
        assert all(set(tool) <= _TOOL_KEYS for tool in match.get("tools", ()))
    pairs = list(_walk(pages))
    assert all(value is not None and value != [] and value != {} for key, value in pairs if key != "next_offset")
    assert not _HIDDEN_KEYS & {key for key, _ in pairs}
    sold = [match for match in matches if match["name"] in goods]
    assert sold and any(action["way"] == "远程下单" for match in sold for action in match["actions"])
    for match in sold:
        assert match["kind"] == "product" and {"name", "kind", "actions"} <= set(match)
        assert not {"phase", "tools"} & set(match)
        assert goods[match["name"]]["match"]["phase"] is None and goods[match["name"]]["match"]["tools"] == []
        assert "来源：" in match["actions"][0]["note"]
        ways = {action["way"]: action for action in match["actions"]}
        assert ways["到店"]["available"] is False and ways["到店"]["reason"]
        if "远程下单" in ways:
            assert ways["远程下单"]["available"] is True and "reason" not in ways["远程下单"]
    assert all("candidates" not in entry["match"] for entry in entries)
    assert not hasattr(world_query, "_request_candidates")
    assert observed == before


def _category_query(observed):
    """按可调用索引里的第一个品类查询。"""
    return _request(category=observed["tool_layer"]["callable_index"]["query_categories"][0]["category"])


def _name_query(observed):
    """按冻结事实里一个对象的完整公开名称查询。"""
    names = sorted(entry["match"]["name"] for entry in observed["world_query_bindings"])
    return _request(category=None, name=names[0])


def _way_query(observed):
    """按冻结事实里实际出现的一种方式查询。"""
    ways = sorted({action["way"] for entry in observed["world_query_bindings"] for action in entry["match"]["actions"]})
    return _request(category=None, way=ways[0])


def _rejected_query(observed):
    """方式与品类都不在闭集内的请求：两处错误在同一份校验报告里。"""
    del observed
    return _request(category="并不存在的品类", way="并不存在的方式")


def _store_name(observed):
    """冻结供给条目所属门店中身份最小的一家的显示名（取自冻结地点表）。"""
    store = min(entry["location_id"] for entry in observed["world_query_bindings"] if entry["location_id"] is not None)
    return next(row["name"] for row in observed["locations"] if row["location_id"] == store)


def _source_query(observed):
    """只写来源：一家有供给条目的门店的显示名（只写 from 合法）。"""
    return _request(category=None, source=_store_name(observed))


def _available_query(observed):
    """按冻结事实里此刻有可用动作的一种方式，只看此刻能做的。"""
    ways = sorted({action["way"] for entry in observed["world_query_bindings"]
                   for action in entry["match"]["actions"] if action["available"]})
    return _request(category=None, way=ways[0], available_only=True)


def _source_available_query(observed):
    """来源与只看此刻能做的同时写（10:00 门店营业，远程下单可用）。"""
    return _request(category=None, source=_store_name(observed), available_only=True)


def _unknown_source_query(observed):
    """来源既不是地点显示名也不是渠道称呼：在 from 位置报查询参数错误并给候选。"""
    del observed
    return _request(category=None, source="并不存在的来源")


@pytest.mark.parametrize("build, accepted", [(_category_query, True), (_name_query, True), (_way_query, True),
                                             (_rejected_query, False), (_source_query, True),
                                             (_available_query, True), (_source_available_query, True),
                                             (_unknown_source_query, False)],
                         ids=["category", "name", "way", "rejected", "source", "available", "source-available",
                              "unknown-source"])
def test_environment_read_query_keeps_world_clock_round_and_store_unchanged(engine_factory, build, accepted):
    """通过真实环境即时查询，禁止写状态、消费生活轮数或追加世界事件。`query_world` 的两个入口——宿主在本次冻结观察上
    本地求值的纯函数与环境的 `read_query`——对同一观察、同一请求：受理时结果逐值相等，拒绝时给出同一份校验报告。
    来源筛选、只看此刻能做的与二者同写、未知来源各一例（docs/dev/SPEC-errands-workday.md 5.6），世界在 10:00（门店营业）。"""
    import asyncio

    from custom.envs.life_env import LifeEnv
    from tests.life.conftest import context, stage_day_plan
    from tests.life.purchase_worlds import at_clock

    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    frozen = context(engine, "query-environment")
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    environment = LifeEnv()
    environment.engine = engine
    before, sequence = engine.state.model_dump(mode="json"), engine.store.last_event_seq
    request = build(observed)
    report = validate_world_query(observed, request)
    result = asyncio.run(environment.read_query(frozen, "query_world", request))
    if accepted:
        assert report is None
        assert result == query_world(observed, request)
        assert result["matches"] and result["total_count"] >= len(result["matches"])
    else:
        assert report is not None and result == report.payload()
        assert result["error_code"] == "ARGUMENTS_INVALID"
        written = {"way": request.way, "category": request.category, "from": request.from_}
        assert [(item["path"][-1], item["written"]) for item in result["items"]] == [
            (field, value) for field, value in written.items() if value is not None]
        assert all(item["allowed_values"] for item in result["items"] if item["path"][-1] != "from")
        assert all(item["allowed_values"] == [] and item["candidates"] for item in result["items"]
                   if item["path"][-1] == "from")
    assert engine.state.model_dump(mode="json") == before
    assert engine.store.last_event_seq == sequence
