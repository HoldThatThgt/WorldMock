"""世界查询的六键请求、来源筛选、只看此刻能做的、筛选后分页与只读保证（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-请求 –5.6；验收台账
「查询请求」「来源筛选」「可执行筛选」「分页与返回」「查询只读」）。

全部用例在白领场景的真实 LifeEngine、SQLite、直接导入的 LifeAgent 与 LifeEnv 上运行：一个引擎只开一个决策轮、只冻结
一次观察（_open），世界状态的改动（时刻、地点、报价、提交与推进）都在开轮之前做，需要另一种世界状态时另建一个引擎；
同一冻结观察上的每次查询（_ask）先经宿主整批校验，受理时宿主本地派发的页与环境 read_query（从 SQLite 读回同一观察）
逐值相等，拒绝时环境给出同一份校验报告（比较错误码、原因与明细；宿主批次的 fix 是固定的批次句）。期望值由用例自己从
冻结条目算出（_oracle 配 _has_way、_runnable），不调用生产筛选函数。宿主一侧不生成工具 Schema（只走 prepare_react_loop
与本地校验），所以给新键加缺省的变异在用例体内以断言失败暴露，而不是在 Schema 核验处崩溃。营业未知的一例用隔离语料
（tests/life/purchase_worlds.py：好德的营业日历写成空表）。最终回合一例不经 _open：它用脚本化模型（只替换远程传输，
tests/integration/test_same_second_continuation.py 的 _single）在同一真实引擎上跑完整的 ReAct 循环并结算。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from collections import Counter
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_feedback import feedback_report  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_same_second_continuation import (_environment, _reply as _model_reply,  # noqa: E402
                                                             _single, _wait_reply)
from tests.life.conftest import (advance_until_ready, context, pending_names, stage_day_plan,  # noqa: E402
                                 start_activity, submit_round)
from tests.life.purchase_worlds import at_clock, build_world, decide  # noqa: E402
from tests.life.routing_fixtures import place, stage  # noqa: E402
from tests.life.test_grounded_observation import _stage_progress_limits  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.guidance import default_reason  # noqa: E402
from worldmock.life.vocabulary import WAYS
from worldmock.life.models import REQUEST_MODELS  # noqa: E402
from worldmock.life.object_labels import object_label, supply_label  # noqa: E402
from worldmock.life.tool_groups import expand_tool_layer  # noqa: E402
from worldmock.life.world_query import QueryWorldRequest, validate_world_query  # noqa: E402

#: 宿主协议反馈里 query_world 参数的根位置（本批第 0 个调用）。
ROOT = ["tool_calls", 0, "function", "arguments"]
#: 正式场景里好德与农工商超市的地点身份（显示名一律从冻结地点表读）。
HAODE, NONGGONGSHANG = "poi-3538682402", "poi-3538763552"
#: 5.5 两种新查询参数错误的 expected 原文。
SOURCE_EXPECTED = "null, a known place name, or a known channel name is required."
CHANNEL_IN_NAME_EXPECTED = "A channel name belongs in from; name takes an object name."
#: 查询明细（方式、品类与渠道称呼写进 name）共用的 why。
_QUERY_WHY = "The submitted value is outside the current query contract."
EMPTY_PAGE = {"matches": [], "total_count": 0, "next_offset": None}
#: 两种取得方式：「购买」及其同义写法等同于同时写这两种（5.1）；用例自己的判定用它，不调生产的方式展开。
ACQUIRE = frozenset({"到店", "远程下单"})
#: 纯牛奶：好德、农工商超市与集市都卖，都能远程下单。
MILK = "纯牛奶"


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


def _has_way(entry: dict, ways) -> bool:
    """冻结条目有方式落在 ways 里的动作（用例自己的判定）。"""
    return any(action["way"] in ways for action in entry["match"]["actions"])


def _runnable(entry: dict, ways=None) -> bool:
    """用例按 5.3 自己判定冻结条目此刻已确认可执行：写了方式只看方式落在 ways 里的动作的 available，未写方式时看全部
    动作以及以该目标为参数的工具的 available。"""
    if ways is not None:
        return any(action["available"] for action in entry["match"]["actions"] if action["way"] in ways)
    return any(row["available"] for row in (*entry["match"]["actions"], *entry["match"]["tools"]))


def _names(matches: list[dict]) -> list[str]:
    """返回项的完整名称（保持返回顺序）。"""
    return [match["name"] for match in matches]


def _blocked_engine(engine_factory):
    """10:00 在家开始等待并推进到它结束：人物有一项刚结束、待收尾的活动（人物级前置条件：先收尾），决策轮还没开。"""
    engine = _open_engine(engine_factory)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "wait")
    assert advance_until_ready(engine)
    return engine


def _purchase_ways(session: SimpleNamespace, available_only: bool) -> dict[str, list[str]]:
    """way 分别写「购买」「买」「到店」「远程下单」时沿 next_offset 翻完得到的名称。"""
    return {way: _names(_traverse(session, way=way, available_only=available_only)[0])
            for way in ("购买", "买", "到店", "远程下单")}


def _check_traversal(session: SimpleNamespace, expected: list[str], **filters) -> None:
    """沿 next_offset 翻完一个筛选组合：每页的 total_count 都是期望的完整匹配数，非末页恰好一页大小，按页拼接的名称与期望
    逐项同序，末页 next_offset 为 null，offset 等于总数时是合法的空末页。"""
    size = session.observed["world_query_page_size"]
    matches, pages = _traverse(session, **filters)
    assert [page["total_count"] for page in pages] == [len(expected)] * len(pages), filters
    assert all(len(page["matches"]) == size for page in pages[:-1]), filters
    assert _names(matches) == expected and pages[-1]["next_offset"] is None, filters
    tail = _ask(session, _query(**filters, offset=len(expected)))
    assert tail.page == {"matches": [], "total_count": len(expected), "next_offset": None}, filters


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
    （5.1）。available_only 同时不是布尔、offset 同时为负时，宿主的同一份报告依次列出这四条与那两个字段的错误。"""
    session = _open(_open_engine(engine_factory), "empty-filters")
    empty = [([*ROOT, field], None, "At least one query filter must be non-null")
             for field in ("name", "way", "category", "from")]
    for available_only in (False, True):
        asked = _ask(session, _query(available_only=available_only))
        assert asked.report["error_code"] == "ARGUMENTS_INVALID" and asked.env is None
        assert [(item["path"], item["written"], item["why"]) for item in asked.report["items"]] == empty
    asked = _ask(session, _query(available_only="true", offset=-1))
    assert asked.report["error_code"] == "ARGUMENTS_INVALID" and asked.env is None
    assert [(item["path"], item["written"], item["why"]) for item in asked.report["items"]] == [
        *empty, ([*ROOT, "available_only"], "true", "A JSON boolean is required."),
        ([*ROOT, "offset"], -1, "The value must be at least 0.")]
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
    before, sequence = engine.context.state.model_dump(mode="json"), engine.context.store.last_event_seq
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
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == sequence


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


def test_source_errors_are_reported_together_with_request_model_errors_on_the_host_path(engine_factory):
    """请求模型错误不压下按冻结观察可独立判定的筛选错误（5.5「来源错误与方式、品类错误一次全报」）：方式空白、品类不是
    字符串或 available_only 不是布尔时，未知来源仍在同一份宿主报告里，排在请求模型错误之后；未声明的方式与品类类型错误、
    未知来源同写时三条都在；候选只换 from、其余键照原样（含仍不合法的键），越界的偏移量不另报。什么也没派发。"""
    session = _open(_open_engine(engine_factory), "model-and-source")
    store = _places(session.observed)[HAODE]
    blank = ([*ROOT, "way"], " ", "A non-null filter must contain a non-whitespace character")
    category = ([*ROOT, "category"], 12, "A JSON string is required.")
    flag = ([*ROOT, "available_only"], "true", "A JSON boolean is required.")
    undeclared = ([*ROOT, "way"], "随便做", _QUERY_WHY)
    source = ([*ROOT, "from"], "好德超市", default_reason("SOURCE_NOT_FOUND"))
    for arguments, expected in ((_query("好德超市", way=" "), [blank, source]),
                                (_query("好德超市", category=12), [category, source]),
                                (_query("好德超市", way="到店", available_only="true", offset=10 ** 6), [flag, source]),
                                (_query("好德超市", way="随便做", category=12), [category, undeclared, source])):
        asked = _ask(session, arguments)
        assert asked.report["error_code"] == "ARGUMENTS_INVALID" and asked.env is None, arguments
        items = asked.report["items"]
        assert [(item["path"], item["written"], item["why"]) for item in items] == expected, arguments
        assert items[-1]["expected"] == SOURCE_EXPECTED and items[-1]["allowed_values"] == []
        candidates = [candidate["arguments"] for candidate in items[-1]["candidates"]]
        assert store in [row["from"] for row in candidates]
        assert all(list(row) == list(arguments) and {**row, "from": None} == {**arguments, "from": None}
                   for row in candidates)
    assert session.host._life_action_count == 0


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


def test_available_only_judges_only_the_actions_of_the_written_way(engine_factory):
    """写了 way 时只看方式命中的动作（5.3）：10:00 在家，到店动作全不可用、远程下单可用——way=到店 加 available_only 是
    合法空页，way=远程下单 加 available_only 等于用例按远程下单动作的 available 算出的完整集合；保留的条目照常返回全部动作，
    不可用的到店动作仍带原因（5.3 第二条）。人在好德时（另一个引擎），way=到店 加 available_only 恰是到店可用的条目，
    它们都是好德的供给。"""
    session = _open(_open_engine(engine_factory), "written-way")
    observed = session.observed
    assert _oracle(observed, lambda entry: _has_way(entry, {"到店"}))
    assert _ask(session, _query(way="到店", available_only=True)).page == EMPTY_PAGE
    expected = _oracle(observed, lambda entry: _runnable(entry, {"远程下单"}))
    matches, _ = _traverse(session, way="远程下单", available_only=True)
    assert _names(matches) == expected and expected
    kept = [{action["way"]: action for action in match["actions"]} for match in matches]
    assert all(ways["远程下单"]["available"] is True for ways in kept)
    pickup = [ways["到店"] for ways in kept if "到店" in ways]
    assert pickup and all(action["available"] is False and action["reason"] for action in pickup)
    engine = _open_engine(engine_factory)
    place(engine, 1, HAODE)
    inside = _open(engine, "written-way-in-store")
    expected = _oracle(inside.observed, lambda entry: _runnable(entry, {"到店"}))
    assert expected and set(expected) <= set(_oracle(inside.observed, lambda entry: entry["location_id"] == HAODE))
    matches, _ = _traverse(inside, way="到店", available_only=True)
    assert _names(matches) == expected


def test_way_purchase_equals_pickup_and_delivery_together(engine_factory):
    """way 写「购买」（及同义写法「买」）等同于同时写到店与远程下单（5.1），只看此刻能做的时取两种取得动作中任一可用
    （5.3）：available_only 为 false 与 true 时，「购买」翻完的名称都等于到店与远程下单两个查询的并集、不为空，与用例
    算出的完整集合逐项同序，「买」逐项相同。在家时到店全不可用，留下的条目都靠远程下单；人在好德时（另一个引擎）到店
    也有可用的条目，并集两边都出力。"""
    home = _open(_open_engine(engine_factory), "purchase-way")
    engine = _open_engine(engine_factory)
    place(engine, 1, HAODE)
    inside = _open(engine, "purchase-way-in-store")
    for session, available_only in ((home, False), (home, True), (inside, True)):
        found = _purchase_ways(session, available_only)
        assert found["购买"] and found["买"] == found["购买"]
        assert set(found["购买"]) == set(found["到店"]) | set(found["远程下单"])
        assert found["购买"] == _oracle(session.observed, lambda entry: _has_way(entry, ACQUIRE) and (
            not available_only or _runnable(entry, ACQUIRE)))
        if available_only:
            assert bool(found["到店"]) is (session is inside) and found["远程下单"]


def test_available_only_without_way_counts_actions_and_tools_of_the_target(engine_factory):
    """way 为 null 时看全部动作以及以该目标为参数的工具的 available（5.3）：刚结束、待收尾的等待活动没有动作，只有以它为
    参数的工具——播报与暂停不可用（带原因）、收尾可用——所以按它的名称加 available_only 仍留下它；返回项照常带全部工具
    （5.3 第二条），没有动作键，与不加筛选时同一页。"""
    session = _open(_blocked_engine(engine_factory), "pending-target")
    [name] = pending_names(session.observed)
    [entry] = [entry for entry in session.observed["world_query_bindings"] if entry["match"]["name"] == name]
    assert entry["match"]["actions"] == [] and _runnable(entry)
    asked = _ask(session, _query(name=name, available_only=True))
    assert asked.error == ""
    [match] = asked.page["matches"]
    assert match["name"] == name and "actions" not in match
    tools = {row["tool"]: row for row in match["tools"]}
    assert set(tools) == {"report_status", "pause_activity", "conclude_activity"}
    assert tools["conclude_activity"] == {"tool": "conclude_activity", "available": True}
    for tool in ("report_status", "pause_activity"):
        assert tools[tool]["available"] is False and tools[tool]["reason"]
    assert _ask(session, _query(name=name)).page == asked.page


def test_person_level_block_yields_a_legal_empty_page(engine_factory):
    """因人物级前置条件（先收尾）使全部动作不可用时，结果是合法空页（5.3 末条）：等待刚结束、待收尾时，用例算出没有任何
    带品类的条目可执行；每个公开品类（含门店商品的品类）不加筛选都有条目，加 available_only 都是三键空页而不是错误；
    way=等待 不加筛选有条目，加 available_only 也是空页。"""
    session = _open(_blocked_engine(engine_factory), "person-block")
    observed = session.observed
    assert not _oracle(observed, lambda entry: entry["category"] is not None and _runnable(entry))
    categories = [row["category"] for row in observed["tool_layer"]["callable_index"]["query_categories"]]
    goods = {entry["category"] for entry in observed["world_query_bindings"] if entry["location_id"] is not None}
    assert goods & set(categories)
    for category in categories:
        assert _ask(session, _query(category=category)).page["total_count"] > 0, category
        assert _ask(session, _query(category=category, available_only=True)).page == EMPTY_PAGE, category
    assert _ask(session, _query(way="等待")).page["total_count"] > 0
    assert _ask(session, _query(way="等待", available_only=True)).page == EMPTY_PAGE


@pytest.fixture(scope="module")
def opening_world(tmp_path_factory):
    """好德的营业日历写成空表（营业未知）的隔离语料，不钉手机配置；地图加载照常接受。"""
    return build_world(tmp_path_factory.mktemp("opening-world"), {}, locations={HAODE: {"opening_calendars": []}})


def _unknown_quote(engine_factory) -> None:
    """一件能远程下单的纯牛奶此刻没有已知报价（开轮之前改世界）：name=纯牛奶 照常列出它，远程下单 available 为 false、
    原因是报价未知、没有价格键；加 available_only 后它不在页里，total_count 少一。"""
    engine = _open_engine(engine_factory)
    supply = next(item for item in engine.context.scenario.supplies
                  if engine.context.rules.items[item.product_id].name == MILK and "delivery" in item.fulfillment_modes)

    def unknown(state) -> None:
        """这件纯牛奶的报价变成未知。"""
        state.supply_quotes[supply.supply_id] = None
    stage(engine, unknown)
    session = _open(engine, "unknown-quote")
    label = supply_label(_places(session.observed)[supply.location_id], MILK)
    listed = _ask(session, _query(name=MILK)).page
    [match] = [match for match in listed["matches"] if match["name"] == label]
    delivery = next(action for action in match["actions"] if action["way"] == "远程下单")
    assert delivery["available"] is False and "price" not in delivery
    assert delivery["reason"] == default_reason("SUPPLY_QUOTE_UNKNOWN")
    filtered = _ask(session, _query(name=MILK, available_only=True)).page
    assert label not in _names(filtered["matches"]) and filtered["total_count"] == listed["total_count"] - 1


def _unknown_opening(engine_factory, world) -> None:
    """好德营业未知：from=好德、way=远程下单 照常列出好德的全部远程下单条目，远程下单都不可用、原因是营业未知；加
    available_only 是合法空页；营业已知的农工商超市加 available_only 照常留下它能送的条目。"""
    engine = engine_factory(str(world.scenario("unknown-opening")))
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    session = _open(engine, "unknown-opening")
    observed, places = session.observed, _places(session.observed)
    matches, _ = _traverse(session, places[HAODE], way="远程下单")
    assert matches and _names(matches) == _oracle(
        observed, lambda entry: entry["location_id"] == HAODE and _has_way(entry, {"远程下单"}))
    delivery = {(action["available"], action["reason"]) for match in matches for action in match["actions"]
                if action["way"] == "远程下单"}
    assert delivery == {(False, default_reason("MERCHANT_OPENING_UNKNOWN"))}
    assert _ask(session, _query(places[HAODE], way="远程下单", available_only=True)).page == EMPTY_PAGE
    kept, _ = _traverse(session, places[NONGGONGSHANG], way="远程下单", available_only=True)
    assert kept and _names(kept) == _oracle(
        observed, lambda entry: entry["location_id"] == NONGGONGSHANG and _runnable(entry, {"远程下单"}))


def _closed_at_scenario_start(engine_factory) -> None:
    """场景起点门店都没开：用例算出没有可执行的取得动作，way=购买 加 available_only 是合法空页，不加筛选照常列出全部能
    取得的商品——空页不等于「都不能买」。"""
    engine = engine_factory()
    stage_day_plan(engine)
    session = _open(engine, "scenario-start")
    listed = _oracle(session.observed, lambda entry: _has_way(entry, ACQUIRE))
    assert listed and not _oracle(session.observed, lambda entry: _runnable(entry, ACQUIRE))
    assert _ask(session, _query(way="购买", available_only=True)).page == EMPTY_PAGE
    assert _ask(session, _query(way="购买")).page["total_count"] == len(listed)


def test_unknown_quote_and_unknown_opening_are_filtered_out_but_stay_listed_with_their_reason(
        engine_factory, opening_world):
    """报价未知、营业未知的条目也被滤掉（5.3 第四条）：true 的含义是「已确认可执行」——不加筛选时这些条目照常列出、不可用
    的动作带原因，加 available_only 时被滤掉；场景起点门店未开时是合法空页，空页不等于「都不能买」。三个引擎各管一种
    世界状态（报价未知、隔离语料里的营业未知、场景起点）。"""
    _unknown_quote(engine_factory)
    _unknown_opening(engine_factory, opening_world)
    _closed_at_scenario_start(engine_factory)


def test_every_filtered_match_is_accepted_with_its_full_name_and_an_available_way(engine_factory):
    """筛出的每条以完整名称与可用方式提交被受理（5.3 第五条：按本人为收货人、此刻此地求值，成功以结算时重验为准）：10:00
    在家 name=纯牛奶、way=购买、available_only 只有一页几条；对其中每一条，在同一状态的新引擎上照抄它的完整名称与一个
    available 为 true 的动作的方式提交 start_activity（来源与去向为 null，按最快拿到），宿主校验通过、环境结算为
    completed，唯一一笔订单的供给正是这个标签所指的门店商品。方式从返回项抄，不写死。"""
    session = _open(_open_engine(engine_factory), "filtered-milk")
    page = _ask(session, _query(name=MILK, way="购买", available_only=True)).page
    assert 1 <= page["total_count"] <= 6 and page["next_offset"] is None
    assert len(page["matches"]) == page["total_count"]
    places = _places(session.observed)
    for index, match in enumerate(page["matches"]):
        way = next(action["way"] for action in match["actions"] if action["available"] and action["way"] in ACQUIRE)
        engine = _open_engine(engine_factory)
        arguments = {"items": [{"target": match["name"], "way": way, "from": None, "to": None}],
                     "answering": None, "prefer": "最快拿到"}
        _, error, result = decide(engine, f"accept-{index}", arguments)
        assert error == "" and (result.status, result.error_code) == ("completed", None), match["name"]
        [order] = result.data["orders"]
        supply = engine.context.rules.supplies[order["supply_id"]]
        assert supply_label(places[supply.location_id], engine.context.rules.items[supply.product_id].name) == match["name"]


def _written_ways(way: str | None):
    """用例自己对所写方式的理解：未写为 None，「购买」是两种取得方式，其余就是所写的那一种。"""
    if way is None:
        return None
    return ACQUIRE if way == "购买" else frozenset({way})


def test_filtered_traversal_equals_the_complete_match_set_and_counts_after_filtering(engine_factory):
    """筛选在排序与切页之前施加（5.3 第三条）：同一冻结观察上逐个组合（品类加可执行、只写来源、来源加远程下单加可执行、
    「购买」加可执行、名称加来源加可执行、品类加来源加到店不加可执行）沿 next_offset 翻完，等于用例算出的完整匹配集
    （同序），每页的 total_count 都是筛选后的完整匹配数，非末页恰好一页大小，末页 next_offset 为 null，offset 等于总数时
    是合法空末页；「购买」加可执行一组（远程下单不可用的条目被滤掉）的筛选后总数严格少于不加可执行时，所以这些断言分得
    清筛选与不筛选。"""
    session = _open(_open_engine(engine_factory), "filtered-traversal")
    observed = session.observed
    places = _places(observed)
    goods = Counter(entry["category"] for entry in observed["world_query_bindings"] if entry["location_id"])
    category = max(sorted(goods), key=goods.__getitem__)
    stocked = Counter(entry["category"] for entry in observed["world_query_bindings"]
                      if entry["location_id"] == HAODE)
    haode_category = max(sorted(stocked), key=stocked.__getitem__)
    combinations = {
        "category": ({"category": category, "available_only": True}, lambda entry: entry["category"] == category),
        "from": ({"source": places[HAODE]}, lambda entry: entry["location_id"] == HAODE),
        "from-delivery": ({"source": places[NONGGONGSHANG], "way": "远程下单", "available_only": True},
                          lambda entry: entry["location_id"] == NONGGONGSHANG and _has_way(entry, {"远程下单"})),
        "purchase": ({"way": "购买", "available_only": True}, lambda entry: _has_way(entry, ACQUIRE)),
        "name-from": ({"name": MILK, "source": places[HAODE], "available_only": True},
                      lambda entry: MILK in entry["aliases"] and entry["location_id"] == HAODE),
        "category-from-pickup": ({"category": haode_category, "source": places[HAODE], "way": "到店"},
                                 lambda entry: entry["category"] == haode_category
                                 and entry["location_id"] == HAODE and _has_way(entry, {"到店"})),
    }
    narrowed = {}
    for label, (filters, keep) in combinations.items():
        ways = _written_ways(filters.get("way"))
        expected = _oracle(observed, lambda entry: keep(entry) and (
            not filters.get("available_only") or _runnable(entry, ways)))
        assert expected, label
        _check_traversal(session, expected, **filters)
        narrowed[label] = len(expected) < len(_oracle(observed, keep))
    assert narrowed["purchase"], narrowed


def test_no_match_is_a_legal_empty_page_and_offset_beyond_total_is_still_an_error(engine_factory):
    """各键合法、offset 在范围内时任何筛选组合无匹配都是三键合法空页（5.4）：不报错、不自动放宽、不换来源；total_count 为
    0 时合法的 offset 只有 0，offset=1 仍在 offset 位置报错、范围是 0..0。筛选后非空时越界按筛选后的总数报错：offset 等于
    筛选后总数是合法空末页，多一就被拒绝，而同一偏移量不加 available_only 时合法。"""
    session = _open(_open_engine(engine_factory), "no-match")
    observed = session.observed
    entries, places = observed["world_query_bindings"], _places(observed)
    label = _oracle(observed, lambda entry: entry["location_id"] == HAODE)[0]
    goods = min(entry["category"] for entry in entries if entry["location_id"] is not None)
    remote = {entry["category"] for entry in entries if _has_way(entry, {"远程下单"})}
    other = min({entry["category"] for entry in entries if entry["category"] is not None} - remote)
    for arguments in (_query(way="到店", available_only=True), _query(places[NONGGONGSHANG], name=label),
                      _query("家", category=goods), _query(name="并不存在的东西"),
                      _query(category=other, way="远程下单", available_only=True)):
        assert _ask(session, arguments).page == EMPTY_PAGE, arguments
        [item] = _ask(session, {**arguments, "offset": 1}).report["items"]
        assert (item["path"], item["written"], item["expected"]) == (
            [*ROOT, "offset"], 1, "An integer in the inclusive range 0..0."), arguments
    filtered = _query(way="购买", available_only=True)
    total = len(_oracle(observed, lambda entry: _runnable(entry, ACQUIRE)))
    assert 0 < total < len(_oracle(observed, lambda entry: _has_way(entry, ACQUIRE)))
    assert _ask(session, {**filtered, "offset": total}).page == {"matches": [], "total_count": total,
                                                                 "next_offset": None}
    [item] = _ask(session, {**filtered, "offset": total + 1}).report["items"]
    assert (item["path"], item["expected"]) == ([*ROOT, "offset"], f"An integer in the inclusive range 0..{total}.")
    assert _ask(session, {**filtered, "available_only": False, "offset": total + 1}).error == ""


def test_query_categories_counts_do_not_change_with_filters(engine_factory):
    """筛选不改变可调用索引里各品类的计数口径（5.1 末条）：可调用索引是四键，query_categories 恰是全部冻结条目按品类的
    完整计数；按品类查询的 total_count 等于这个计数，再加来源或 available_only 时 total_count 可以更少，索引计数不变——
    这些查询之后冻结观察逐值不变，同一世界再观察一次，索引计数仍相同。"""
    session = _open(_open_engine(engine_factory), "category-counts")
    observed = session.observed
    frozen = copy.deepcopy(observed)
    counts = Counter(entry["category"] for entry in observed["world_query_bindings"] if entry["category"] is not None)
    expected = [{"category": category, "count": counts[category]} for category in sorted(counts)]
    index = observed["tool_layer"]["callable_index"]
    assert list(index) == ["lifestyle", "contents", "goods", "query_categories"]
    assert index["query_categories"] == expected
    store, narrowed = _places(observed)[HAODE], False
    for row in expected:
        assert _ask(session, _query(category=row["category"])).page["total_count"] == row["count"], row
        for filters in ({"available_only": True}, {"source": store}):
            total = _ask(session, _query(category=row["category"], **filters)).page["total_count"]
            assert total <= row["count"], (row, filters)
            narrowed = narrowed or total < row["count"]
    assert narrowed and observed == frozen
    again = session.engine.observe(session.ctx)
    assert again["tool_layer"]["callable_index"]["query_categories"] == expected


def test_filtered_queries_leave_world_clock_events_credentials_and_presentation_unchanged(engine_factory):
    """筛选查询只读（5.6）：宿主本地求值与环境 read_query 两入口上做过来源、方式、可执行筛选的若干查询（含被拒的与空页）
    之后，世界状态逐值不变——时钟、世界版本、可见供给与浏览凭据、呈现时刻与本秒呈现暂存都没动——事件序号不变、没有
    action_requested 事件，冻结观察也不变；查询列出的门店商品并没有因此变成可见供给。"""
    session = _open(_open_engine(engine_factory), "read-only-filters")
    engine, observed = session.engine, session.observed
    before, sequence, frozen = engine.context.state.model_dump(mode="json"), engine.context.store.last_event_seq, copy.deepcopy(observed)
    agent = engine.context.state.agents[1]
    seen = (set(agent.visible_supply_ids), dict(agent.supply_browsed_at), dict(agent.supply_browsed_location))
    presented = (copy.deepcopy(engine.context.state.presentation_state), copy.deepcopy(engine.context.state.same_moment_presentation))
    places, listed = _places(observed), set()
    for arguments in (_query(places[HAODE], available_only=True), _query(way="购买", available_only=True),
                      _query(places[NONGGONGSHANG], name=MILK, way="远程下单"), _query("家", available_only=True),
                      _query("好德超市", available_only=True), _query(way="到店", available_only=True)):
        asked = _ask(session, arguments)
        listed |= set(_names(asked.page["matches"])) if asked.page else set()
    supplies = {entry["identity"].removeprefix("supply:") for entry in observed["world_query_bindings"]
                if entry["match"]["name"] in listed and entry["location_id"] is not None}
    agent = engine.context.state.agents[1]
    assert supplies - seen[0] and not (supplies - seen[0]) & agent.visible_supply_ids
    assert (engine.context.state.current_time, engine.context.state.version) == (before["current_time"], before["version"])
    assert (set(agent.visible_supply_ids), dict(agent.supply_browsed_at), dict(agent.supply_browsed_location)) == seen
    assert (engine.context.state.presentation_state, engine.context.state.same_moment_presentation) == presented
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == sequence
    assert not engine.context.store.read_agent_events("action_requested", 1)
    assert observed == frozen


def test_query_batch_is_validated_whole_before_any_query_runs(engine_factory):
    """同一模型回合的只读查询整批先校验后执行（5.6）：一个合法的筛选查询、一个未知来源的查询与一个记忆查询同批时，宿主回
    一份报告，唯一一条明细落在真实调用下标 1 的 from 上；带筛选的查询与世界请求混批时整批拒绝在 tool_calls 位置；两次都
    什么也没派发，世界状态、事件序号与冻结观察不变。只有合法只读查询的一批照常受理。"""
    session = _open(_open_engine(engine_factory), "query-batch")
    host, engine, observed = session.host, session.engine, session.observed
    before, sequence, frozen = engine.context.state.model_dump(mode="json"), engine.context.store.last_event_seq, copy.deepcopy(observed)
    valid = _query(_places(observed)[HAODE], way="远程下单", available_only=True)
    batch = [ReactDecision("", "query_world", valid, "", "call-valid"),
             ReactDecision("", "query_world", _query("好德超市", available_only=True), "", "call-unknown"),
             ReactDecision("", "memory_recent", {"limit": 1}, "", "call-memory")]
    report = feedback_report(host.validate_react_decisions(batch, False)).payload()
    assert report["error_code"] == "ARGUMENTS_INVALID"
    [item] = report["items"]
    assert (item["path"], item["written"], item["expected"]) == (
        ["tool_calls", 1, "function", "arguments", "from"], "好德超市", SOURCE_EXPECTED)
    world = {"items": [{"target": "自己", "way": "等待", "from": None, "to": None}], "answering": None, "prefer": None}
    mixed = [ReactDecision("", "start_activity", world, "", "call-world"),
             ReactDecision("", "query_world", valid, "", "call-query")]
    [item] = feedback_report(host.validate_react_decisions(mixed, False)).payload()["items"]
    assert (item["path"], item["written"]) == (["tool_calls"], ["start_activity", "query_world"])
    assert host._life_action_count == 0
    assert engine.context.state.model_dump(mode="json") == before and engine.context.store.last_event_seq == sequence
    assert not engine.context.store.read_agent_events("action_requested", 1)
    assert observed == frozen
    readonly = [batch[0], ReactDecision("", "query_world", _query(way="购买", available_only=True), "", "call-more"),
                batch[2]]
    assert host.validate_react_decisions(readonly, False) == ""


def test_the_final_turn_offers_only_world_requests_after_queries_used_the_earlier_turns(engine_factory, monkeypatch):
    """最终回合不开放查询（5.6 保留约束）：真实引擎、SQLite、LifeEnv 与经脚本化模型（只替换远程传输）完整跑 ReAct 循环的
    LifeAgent，回合预算 4。前三个回合各发一个合法的六键筛选查询（名称、「购买」加可执行、只写门店 from），宿主本地答出
    非空的页；落库的前三个模型请求都开放 query_world、recall_task_summary 与记忆查询，第四个即最终回合的请求不是截断重试
    （开启思考、tool_choice=auto），只开放世界请求，三次查询的结果都在它的消息里；它给出的世界请求照常登记与结算，查询
    没有登记成世界动作。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)
    engine = _open_engine(engine_factory)

    def responses(observation: dict) -> list:
        """三个查询回合，然后开始等待。"""
        queries = (_query(name=MILK), _query(way="购买", available_only=True), _query(_places(observation)[HAODE]))
        return [*(_model_reply(f"call-query-{index}", "query_world", arguments, f"先查一下第{index}样。")
                  for index, arguments in enumerate(queries)), *_wait_reply()]

    asyncio.run(_single(_environment(engine), "final-turn", responses))
    requests = [event.data["request"] for event in engine.context.store.read_agent_events("model_request", 1)
                if event.decision_id == "decision-final-turn-1"]
    offered = [{item["function"]["name"] for item in request["tools"]} for request in requests]
    queries = {"query_world", "recall_task_summary", "memory_recent", "memory_search", "memory_time_range"}
    assert offered[:3] == [set(REQUEST_MODELS) | queries] * 3
    assert offered[3] == set(REQUEST_MODELS) - queries and len(requests) == 4
    assert (requests[3]["extra_body"], requests[3]["tool_choice"]) == ({"thinking": {"type": "enabled"}}, "auto")
    answers = [message["content"] for message in requests[3]["messages"] if message["role"] == "tool"]
    assert len(answers) == 3 and all('"total_count"' in answer for answer in answers)
    [registered] = engine.context.store.read_agent_events("action_requested", 1)
    assert (registered.decision_id, registered.data["tool_name"]) == ("decision-final-turn-1", "start_activity")


def _limit_of_twelve(document: dict) -> None:
    """@brief 场景副本的单次呈现上限改为 12（换新名额 floor(12 × 0.15) 为 1，仍可准入）。"""
    document["candidate_presentation_limit"] = 12


def test_page_size_is_the_scenario_presentation_limit_and_follows_that_constant(engine_factory):
    """@brief 每页大小不变、没有页长参数（5.3 第三条；接口合同：每页取场景常量 candidate_presentation_limit）：正式场景
    的冻结页长是 20，只写好德的查询多于一页时首页恰好 20 项、next_offset 为 20，沿 next_offset 翻完的各页偏移依次是页长
    的倍数、名称等于用例算出的完整集合；场景副本把上限改成 12 时页长随之是 12。页长按字面值断言，不从观察读回。"""
    for mutate, limit in ((None, 20), (_limit_of_twelve, 12)):
        engine = engine_factory(mutate=mutate)
        stage_day_plan(engine)
        at_clock(engine, 10, 0)
        session = _open(engine, f"page-size-{limit}")
        assert engine.context.scenario.candidate_presentation_limit == limit
        assert session.observed["world_query_page_size"] == limit
        store = _places(session.observed)[HAODE]
        expected = _oracle(session.observed, lambda entry: entry["location_id"] == HAODE)
        first = _ask(session, _query(store)).page
        assert first["total_count"] == len(expected) > limit
        assert len(first["matches"]) == limit and first["next_offset"] == limit
        matches, pages = _traverse(session, store)
        assert _names(matches) == expected and len(pages) == -(-len(expected) // limit)
        assert [len(page["matches"]) for page in pages[:-1]] == [limit] * (len(pages) - 1)
        assert [page["next_offset"] for page in pages] == [
            *(limit * index for index in range(1, len(pages))), None]


@pytest.fixture(scope="module")
def gated_store_world(tmp_path_factory):
    """@brief 农工商超市要求人物没有的资格 member 才能进入（地点级准入）的隔离语料；它的供给本身不设门槛，不钉手机配置。"""
    return build_world(tmp_path_factory.mktemp("gated-store-world"), {},
                       locations={NONGGONGSHANG: {"required_qualifications": ["member"]}})


def test_supplies_of_a_store_the_person_may_not_enter_are_neither_frozen_nor_offered(engine_factory,
                                                                                     gated_store_world):
    """@brief 筛选不扩大读取权限、不泄露不可访问门店（5.5 第三行、5.6）：农工商超市在地点一级不可进入时，它在场景里照常有
    供给（含纯牛奶），但没有一条进冻结条目——from=农工商超市 是合法空页（加名称与「购买」也是），按名称查纯牛奶翻完没有
    它的那一件，与它相近的未知写法的候选不含它、只取权限内有供给条目的门店；可调用索引的品类计数恰是冻结条目的计数。"""
    engine = engine_factory(str(gated_store_world.scenario("gated-store")))
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    session = _open(engine, "gated-store")
    observed, places = session.observed, _places(session.observed)
    sold = {engine.context.rules.items[supply.product_id].name for supply in engine.context.scenario.supplies
            if supply.location_id == NONGGONGSHANG}
    assert MILK in sold and len(sold) > 20
    assert NONGGONGSHANG not in _permitted(observed) and HAODE in _permitted(observed)
    assert _ask(session, _query(places[NONGGONGSHANG])).page == EMPTY_PAGE
    assert _ask(session, _query(places[NONGGONGSHANG], name=MILK, way="购买")).page == EMPTY_PAGE
    milk = _names(_traverse(session, name=MILK)[0])
    assert supply_label(places[HAODE], MILK) in milk and supply_label(places[NONGGONGSHANG], MILK) not in milk
    arguments = _query(places[NONGGONGSHANG][:-1])
    sources = _source_candidates(_ask(session, arguments), arguments)
    assert places[NONGGONGSHANG] not in sources
    assert set(sources) <= {places[store] for store in _permitted(observed)}
    counts = Counter(entry["category"] for entry in observed["world_query_bindings"] if entry["category"] is not None)
    assert observed["tool_layer"]["callable_index"]["query_categories"] == [
        {"category": category, "count": counts[category]} for category in sorted(counts)]


def _used_up_task(engine_factory, round_id: str) -> tuple[SimpleNamespace, dict]:
    """@brief 工作合同剩余不足一分钟的任务所在的冻结观察：任务可见、有效工时已达标，「工作」不可用并带原因，以它为参数的
    submit_work 可用。返回（会话，这条任务的冻结条目）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_progress_limits(engine, "contract")
    session = _open(engine, round_id)
    [entry] = [entry for entry in session.observed["world_query_bindings"] if entry["match"]["kind"] == "task"]
    assert _has_way(entry, {"工作"}) and not _runnable(entry, {"工作"})
    assert {tool["tool"] for tool in entry["match"]["tools"] if tool["available"]} == {"submit_work"}
    return session, entry


def test_available_only_with_a_written_way_never_counts_the_tools_of_the_target(engine_factory):
    """@brief 写了 way 时只看方式命中的动作，以目标为参数的工具只在 way 为 null 时计入（5.3 第一条）：合同用完的任务按
    名称加 way=工作 查得到它（「工作」不可用、带原因），再加 available_only 是合法空页；不写 way 加 available_only 仍
    留下它（靠可用的 submit_work）；way=工作 加 available_only 翻完等于用例按「工作」动作的 available 算出的集合。"""
    session, entry = _used_up_task(engine_factory, "way-and-tools")
    name = entry["match"]["name"]
    listed = _ask(session, _query(name=name, way="工作")).page
    assert listed["total_count"] == 1 and _names(listed["matches"]) == [name]
    [action] = [action for action in listed["matches"][0]["actions"] if action["way"] == "工作"]
    assert action["available"] is False and action["reason"]
    assert _ask(session, _query(name=name, way="工作", available_only=True)).page == EMPTY_PAGE
    assert _runnable(entry)
    assert _names(_ask(session, _query(name=name, available_only=True)).page["matches"]) == [name]
    matches, _ = _traverse(session, way="工作", available_only=True)
    assert _names(matches) == _oracle(session.observed, lambda item: _runnable(item, {"工作"}))
    assert name not in _names(matches)


def _empty_values(value) -> list[str]:
    """@brief 一个 JSON 值里取值为 null 或空集合的键（含嵌套在字典与列表里的），按遍历顺序。"""
    if isinstance(value, dict):
        return [name for key, item in value.items()
                for name in ([key] if item is None or item == [] or item == {} else _empty_values(item))]
    if isinstance(value, list):
        return [name for item in value for name in _empty_values(item)]
    return []


def test_task_contract_drops_null_in_query_results_and_in_the_prompt_tool_layer(engine_factory):
    """@brief 紧凑投影在每一层都只略去 null 与空集合（5.4 第二条：任务项的 contract 是键闭集的一员，紧凑规则与工具层分组
    同一实现）：冻结任务目标的合同五键俱全、outlook 为 null；按名称查到的返回项里合同恰是其余四键，整页任何位置都没有
    null 与空集合；宿主照同一观察构建的提示里工具层的任务行同样是四键合同、正文没有 outlook，而分组还原后仍等于冻结的
    工具层（无损）。"""
    session, entry = _used_up_task(engine_factory, "nested-null")
    frozen = entry["match"]
    assert list(frozen["contract"]) == ["opens", "deadline", "place", "remaining", "outlook"]
    assert frozen["contract"]["outlook"] is None
    kept = {key: frozen["contract"][key] for key in ("opens", "deadline", "place", "remaining")}
    assert all(kept.values())
    page = _ask(session, _query(name=frozen["name"])).page
    [match] = page["matches"]
    assert match["contract"] == kept and _empty_values(page["matches"]) == []
    assert [tool for tool in match["tools"] if tool["tool"] == "submit_work"] == [
        {"tool": "submit_work", "available": True}]
    request = ReactLoopRequest(0, session.ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                            "data": session.observed}])
    [user] = [item["content"] for item in session.host.build_react_messages(request) if item["role"] == "user"]
    assert '"outlook"' not in user
    layer = next(layer for layer in json.loads(user) if layer["layer"] == "工具层")
    [group] = [group for group in layer["groups"] if group["kind"] == "task"]
    [row] = group["targets"]
    assert {**group["shared"], **row}["contract"] == kept and _empty_values(group) == []
    assert expand_tool_layer(layer) == session.observed["tool_layer"]


def test_home_words_keep_meaning_home_under_lossless_spellings(engine_factory):
    """@brief 「家」「住处」与地点显示名经同一无损归一（5.2：来源的合法写法以现行实现为准；接口合同：精确、无损归一或
    去掉「地点：」前缀）：带「地点：」前缀、首尾或中间空白、半角冒号的写法仍指本人住处——住处没有供给，是合法空页而
    不是 5.5 第一行的参数错误，加 available_only 同样；住处的别的说法（「家里」）不是来源，仍在 from 位置报错并给候选。"""
    session = _open(_open_engine(engine_factory), "home-spellings")
    for written in ("地点：家", " 住处 ", "住 处", "地点: 家", "地点：住处"):
        asked = _ask(session, _query(written))
        assert asked.error == "" and asked.page == EMPTY_PAGE, written
        assert _ask(session, _query(written, available_only=True)).page == EMPTY_PAGE, written
    arguments = _query("家里")
    assert _source_candidates(_ask(session, arguments), arguments)
