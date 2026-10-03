"""世界查询的六键请求、来源筛选与两种新查询参数错误（docs/dev/SPEC-errands-workday.md 5.1、5.2、5.5、5.6；验收台账
「查询请求」「来源筛选」）。

全部用例在正式白领场景的真实 LifeEngine、SQLite、直接导入的 LifeAgent 与 LifeEnv 上运行：一个引擎只开一个决策轮、只冻结
一次观察（_open）；同一冻结观察上的每次查询（_ask）先经宿主整批校验，受理时宿主本地派发的页与环境 read_query（从 SQLite
读回同一观察）逐值相等，拒绝时环境给出同一份校验报告（比较错误码、原因与明细；宿主批次的 fix 是固定的批次句）。期望值由
用例自己从冻结条目算出（_oracle），不调用生产筛选函数。宿主一侧不生成工具 Schema（只走 prepare_react_loop 与本地校验），
所以给新键加缺省的变异在用例体内以断言失败暴露，而不是在 Schema 核验处崩溃。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_feedback import feedback_report  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.life.conftest import context, stage_day_plan  # noqa: E402
from tests.life.purchase_worlds import at_clock  # noqa: E402
from worldmock.life.guidance import default_reason  # noqa: E402
from worldmock.life.interaction_menu import WAYS  # noqa: E402
from worldmock.life.object_labels import object_label  # noqa: E402
from worldmock.life.world_query import QueryWorldRequest, validate_world_query  # noqa: E402

#: 宿主协议反馈里 query_world 参数的根位置（本批第 0 个调用）。
ROOT = ["tool_calls", 0, "function", "arguments"]
#: 正式场景里好德与农工商超市的地点身份（显示名一律从冻结地点表读）。
HAODE, NONGGONGSHANG = "poi-3538682402", "poi-3538763552"
#: 5.5 两种新查询参数错误的 expected 原文。
SOURCE_EXPECTED = "null, a known place name, or a known channel name is required."
CHANNEL_IN_NAME_EXPECTED = "A channel name belongs in from; name takes an object name."
EMPTY_PAGE = {"matches": [], "total_count": 0, "next_offset": None}


def _open_engine(engine_factory):
    """正式白领场景 1 月 5 日 10:00 的真实引擎：登记空计划，门店营业，人物在家。"""
    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    return engine


def _open(engine, round_id: str) -> SimpleNamespace:
    """开这个引擎唯一的决策轮并冻结一次观察：宿主按同一观察准备本地校验（不生成工具 Schema），环境直接持有同一引擎。
    状态改动（时刻、地点、提交与推进）都要在这之前做；之后只查询。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    host = LifeAgent()
    host.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                                  "data": observed}]))
    environment = LifeEnv()
    environment.engine = engine
    return SimpleNamespace(engine=engine, ctx=ctx, observed=observed, host=host, environment=environment)


def _query(source=None, **filters) -> dict:
    """六键查询参数（键序 name, way, category, from, available_only, offset）：未写的筛选为 null，available_only 缺省
    false，offset 缺省 0；source 写到键 from。"""
    return {"name": None, "way": None, "category": None, "from": source, "available_only": False, "offset": 0,
            **filters}


def _request_or_none(arguments: dict):
    """参数能否构成请求模型：能则返回请求（环境入口才可达），缺键、类型不符、空白或全空筛选时为 None。"""
    try:
        return QueryWorldRequest.model_validate(arguments)
    except ValidationError:
        return None


def _ask(session: SimpleNamespace, arguments: dict) -> SimpleNamespace:
    """同一冻结观察上的一次查询：宿主整批校验；受理时宿主本地派发与环境 read_query 的页逐值相等；拒绝且参数能构成请求时，
    环境给出同一份校验报告。返回（宿主纠错串 error，宿主报告 report，宿主页 page，环境结果 env）。"""
    decision = ReactDecision("", "query_world", arguments, "", "call-query")
    error = session.host.validate_react_decisions([decision], False)
    request = _request_or_none(arguments)
    env = None
    if request is not None:
        env = asyncio.run(session.environment.read_query(session.ctx, "query_world", request))
    if error == "":
        page = asyncio.run(session.host.dispatch_react_tool("query_world", arguments)).data
        assert page == env
        return SimpleNamespace(error=error, report=None, page=page, env=env)
    report = feedback_report(error).payload()
    if request is not None:
        assert env == validate_world_query(session.observed, request).payload()
        assert (env["error_code"], env["reason"], env["items"]) == (report["error_code"], report["reason"],
                                                                     report["items"])
    return SimpleNamespace(error=error, report=report, page=None, env=env)


def _traverse(session: SimpleNamespace, source=None, **filters) -> tuple[list[dict], list[dict]]:
    """沿 next_offset 翻完一个查询（每页都经 _ask 的两入口比对）：返回（全部匹配，各页）。"""
    matches, pages, offset = [], [], 0
    while offset is not None:
        asked = _ask(session, _query(source, **filters, offset=offset))
        assert asked.error == "", asked.error
        pages.append(asked.page)
        matches.extend(asked.page["matches"])
        offset = asked.page["next_offset"]
    return matches, pages


def _oracle(observed: dict, keep) -> list[str]:
    """用例自己从冻结条目算出的期望名称（按完整公开名与稳定身份排序），不调用生产筛选函数。"""
    entries = sorted((entry for entry in observed["world_query_bindings"] if keep(entry)),
                     key=lambda entry: (entry["match"]["name"], entry["identity"]))
    return [entry["match"]["name"] for entry in entries]


def _places(observed: dict) -> dict[str, str]:
    """冻结地点表：地点身份 → 显示名。"""
    return {row["location_id"]: row["name"] for row in observed["locations"]}


def _permitted(observed: dict) -> list[str]:
    """权限内有供给条目的门店身份（按身份排序）。"""
    return sorted({entry["location_id"] for entry in observed["world_query_bindings"]
                   if entry["location_id"] is not None})


def _source_candidates(asked: SimpleNamespace, arguments: dict) -> list[str]:
    """断言 5.5 第一行的形状（明细码与顶层码 ARGUMENTS_INVALID，path 为 from，written 照写，allowed_values 为空，
    expected 为规格原文，why 与购买侧同句，一至三条候选，每条是只换了 from 的完整六键 query_world 请求、note 为 null），
    返回各候选的 from。"""
    assert asked.error != "" and asked.report["error_code"] == "ARGUMENTS_INVALID"
    [item] = asked.report["items"]
    assert (item["path"], item["written"], item["error_code"]) == ([*ROOT, "from"], arguments["from"],
                                                                   "ARGUMENTS_INVALID")
    assert (item["allowed_values"], item["expected"]) == ([], SOURCE_EXPECTED)
    assert item["why"] == default_reason("SOURCE_NOT_FOUND")
    assert 1 <= len(item["candidates"]) <= 3
    rest = {key: value for key, value in arguments.items() if key != "from"}
    for candidate in item["candidates"]:
        assert (candidate["tool"], candidate["note"], list(candidate["arguments"])) == (
            "query_world", None, list(arguments))
        assert {key: value for key, value in candidate["arguments"].items() if key != "from"} == rest
    return [candidate["arguments"]["from"] for candidate in item["candidates"]]


def test_host_rejects_the_retired_four_key_request_at_both_new_keys(engine_factory):
    """旧四键请求按缺字段拒绝（5.1）：宿主协议反馈是 ARGUMENTS_INVALID，恰好两条明细落在 from 与 available_only，
    written 为 null、原因是缺字段；什么也没派发；请求模型同样拒绝，所以环境入口拿不到旧形状。"""
    session = _open(_open_engine(engine_factory), "four-keys")
    four = {"name": None, "way": "到店", "category": None, "offset": 0}
    asked = _ask(session, four)
    assert asked.report is not None, "the retired four-key request was accepted"
    assert asked.report["error_code"] == "ARGUMENTS_INVALID"
    assert [(item["path"], item["written"], item["why"]) for item in asked.report["items"]] == [
        ([*ROOT, "from"], None, "This required field was omitted."),
        ([*ROOT, "available_only"], None, "This required field was omitted.")]
    assert session.host._life_action_count == 0 and asked.env is None
    with pytest.raises(ValidationError):
        QueryWorldRequest(**four)


def test_all_four_empty_filters_are_reported_and_available_only_alone_is_no_filter(engine_factory):
    """名称、方式、品类与来源四者全空时在四个位置各报一条、没有空根位置；available_only 为 true 也不单独构成查询条件
    （5.1）。"""
    session = _open(_open_engine(engine_factory), "empty-filters")
    for available_only in (False, True):
        asked = _ask(session, _query(available_only=available_only))
        assert asked.report["error_code"] == "ARGUMENTS_INVALID" and asked.env is None
        assert [(item["path"], item["written"], item["why"]) for item in asked.report["items"]] == [
            ([*ROOT, field], None, "At least one query filter must be non-null")
            for field in ("name", "way", "category", "from")]
    assert session.host._life_action_count == 0


def test_available_only_must_be_a_json_boolean_on_the_host_path(engine_factory):
    """available_only 只收 JSON 布尔：字符串、数字与 null 经宿主严格 JSON 校验都在 available_only 位置被拒绝，原样回显
    所写的值；true 与 false 受理。"""
    session = _open(_open_engine(engine_factory), "boolean-filter")
    for value in ("true", "false", 0, 1, 1.0, None):
        asked = _ask(session, _query(name="纯牛奶", available_only=value))
        assert [(item["path"], item["written"], item["why"]) for item in asked.report["items"]] == [
            ([*ROOT, "available_only"], value, "A JSON boolean is required.")], value
    for value in (False, True):
        assert _ask(session, _query(name="纯牛奶", available_only=value)).error == ""


def test_from_alone_is_a_legal_query_matching_only_that_stores_supplies(engine_factory):
    """只写 from 合法（5.1）：门店显示名只匹配该门店的供给条目（非供给条目一律排除），沿 next_offset 翻完等于用例按
    location_id 算出的完整集合，每项都是商品；去掉「地点：」前缀、空白与全角空白不同的写法得到同一集合（5.2 现行写法）。"""
    session = _open(_open_engine(engine_factory), "from-alone")
    name = _places(session.observed)[HAODE]
    expected = _oracle(session.observed, lambda entry: entry["location_id"] == HAODE)
    matches, pages = _traverse(session, name)
    assert [match["name"] for match in matches] == expected and len(expected) > 20
    assert {match["kind"] for match in matches} == {"product"}
    assert {page["total_count"] for page in pages} == {len(expected)}
    for spelling in (object_label("location", name), f" {name}　"):
        assert [match["name"] for match in _traverse(session, spelling)[0]] == expected, spelling


def test_home_word_resolves_and_a_source_without_permitted_supply_is_a_legal_empty_page(engine_factory):
    """「家」「住处」指本人住处，地点显示名都是合法来源（5.2）；来源解析成功但没有权限内供给（住处、办公室、公园等没有
    供给条目的地点）时是合法空页：不报错、不放宽、不换来源（5.4、5.5 第三行）。"""
    session = _open(_open_engine(engine_factory), "home-word")
    places, permitted = _places(session.observed), set(_permitted(session.observed))
    empty = [name for identity, name in sorted(places.items()) if identity not in permitted]
    assert places[session.observed["home_location_id"]] in empty and len(empty) >= 2
    for written in ("家", "住处", *empty):
        asked = _ask(session, _query(written))
        assert asked.error == "" and asked.page == EMPTY_PAGE, written
        assert _ask(session, _query(written, available_only=True)).page == EMPTY_PAGE


def test_unknown_from_is_an_argument_error_with_candidates_from_permitted_sources(engine_factory):
    """from 既不是地点显示名也不是渠道称呼（5.5 第一行）：在 from 位置报查询参数错误，候选是至多三条只换了 from 的完整
    请求，来源只取权限内有供给条目的门店（正式场景没有渠道），照抄任一条都被受理；写成与没有供给的地点相近的写法
    （「<地点>旁」），那个地点也不在候选里；空白写法与毫不相近的写法按门店身份顺序取前三家。查询不改世界。"""
    session = _open(_open_engine(engine_factory), "unknown-from")
    engine = session.engine
    before, sequence = engine.state.model_dump(mode="json"), engine.store.last_event_seq
    places, permitted = _places(session.observed), _permitted(session.observed)
    allowed = {places[store] for store in permitted}
    arguments = _query("好德超市")
    sources = _source_candidates(_ask(session, arguments), arguments)
    assert set(sources) <= allowed and places[HAODE] in sources
    for source in sources:
        resubmitted = _ask(session, {**arguments, "from": source})
        assert resubmitted.error == "" and resubmitted.page["total_count"] > 0
    for identity in sorted(set(places) - set(permitted)):
        arguments = _query(f"{places[identity]}旁", available_only=True)
        sources = _source_candidates(_ask(session, arguments), arguments)
        assert places[identity] not in sources and set(sources) <= allowed, (identity, sources)
    for written in (" ", "", "xyz"):
        arguments = _query(written)
        assert _source_candidates(_ask(session, arguments), arguments) == [places[store] for store in permitted[:3]]
    assert engine.state.model_dump(mode="json") == before
    assert engine.store.last_event_seq == sequence


def test_source_way_and_category_errors_are_reported_together_before_any_offset_check(engine_factory):
    """来源错误与方式、品类错误一次全报（5.5）：恰好三条，依次是方式（合法值是完整方式闭集）、品类（合法值是索引里的
    全部品类）与来源；offset 范围只在全部筛选合法后计算，越界的偏移量此时不另报。"""
    session = _open(_open_engine(engine_factory), "all-errors")
    arguments = _query("好德超市", way="随便做", category="并不存在的品类", offset=10 ** 6)
    asked = _ask(session, arguments)
    items = asked.report["items"]
    assert asked.report["error_code"] == "ARGUMENTS_INVALID"
    assert [(item["path"], item["written"], item["error_code"]) for item in items] == [
        ([*ROOT, "way"], "随便做", "ARGUMENTS_INVALID"), ([*ROOT, "category"], "并不存在的品类", "ARGUMENTS_INVALID"),
        ([*ROOT, "from"], "好德超市", "ARGUMENTS_INVALID")]
    labels = [row["category"] for row in session.observed["tool_layer"]["callable_index"]["query_categories"]]
    assert items[0]["allowed_values"] == list(WAYS) and items[1]["allowed_values"] == labels and len(labels) > 3
    assert items[2]["expected"] == SOURCE_EXPECTED and items[2]["candidates"]
    alone = _ask(session, _query(way="随便做", offset=10 ** 6))
    assert [item["path"][-1] for item in alone.report["items"]] == ["way"]
    beyond = _ask(session, _query(_places(session.observed)[HAODE], offset=10 ** 6))
    [offset] = beyond.report["items"]
    total = len(_oracle(session.observed, lambda entry: entry["location_id"] == HAODE))
    assert (offset["path"][-1], offset["expected"]) == ("offset", f"An integer in the inclusive range 0..{total}.")


def test_full_label_with_another_store_in_from_is_a_legal_empty_page(engine_factory):
    """name 写某家门店商品的完整标签而 from 写另一家店：查询是交集筛选，是合法空页而不是错误（5.5 第四行）；from 写它
    自己的门店时恰好一项。"""
    session = _open(_open_engine(engine_factory), "label-and-store")
    places = _places(session.observed)
    label = _oracle(session.observed, lambda entry: entry["location_id"] == HAODE)[0]
    other = _ask(session, _query(places[NONGGONGSHANG], name=label))
    assert other.error == "" and other.page == EMPTY_PAGE
    own = _ask(session, _query(places[HAODE], name=label))
    assert own.error == "" and [match["name"] for match in own.page["matches"]] == [label]


def test_store_name_written_in_name_still_matches_the_location_target(engine_factory):
    """name 只写对象名称（5.1）：门店名写在 name 里仍按地点目标匹配，得到的是地点目标「地点：<门店>」，不是它的供给。"""
    session = _open(_open_engine(engine_factory), "store-in-name")
    name = _places(session.observed)[HAODE]
    asked = _ask(session, _query(name=name))
    assert asked.error == ""
    assert [(match["name"], match["kind"]) for match in asked.page["matches"]] == [
        (object_label("location", name), "location")]
    assert _oracle(session.observed, lambda entry: entry["location_id"] == HAODE)
