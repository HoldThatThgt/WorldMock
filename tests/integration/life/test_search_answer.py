"""@brief SearchCatalogView 长尾召回、Fact Guard 校验与防幻觉、app_use 三支归因与 SearchSessionRunner 全流程。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from tests.integration.runtime.current_contract_fixtures import ScriptedModel, ToolResponse, resolved_binding
from tests.life.conftest import advance_next, do_at, search_world, stage_day_plan, use_app
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act
from worldmock.agents.search.search_session import SearchSessionRunner
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.activities.search.search_activity import attribute_search_app
from worldmock.simulation_engine.commerce.purchase.order_apps import order_app_ids
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.layers.search_projection import (
    ALL_ROUTES,
    SCOPED,
    search_details,
)
from worldmock.simulation_engine.observation.memory.days import published_today
from worldmock.simulation_engine.observation.memory.entries import entry_full
from worldmock.state.decisions.barrier import SearchSessionOutcome
from worldmock.state.interaction.search_requests import (
    InspectGoodsOffersRequest,
    SearchGoodsRequest,
    SearchPlacesRequest,
    SubmitSearchResultsRequest,
)


def _search_goods_req(keywords, *, category=None, app=None, store=None,
                      max_price_yuan=None, open_only=False, limit=50, offset=0) -> SearchGoodsRequest:
    """构造无默认值 ToolRequestModel 的 SearchGoodsRequest。"""
    return SearchGoodsRequest(
        keywords=keywords, category=category, app=app, store=store,
        max_price_yuan=max_price_yuan, open_only=open_only, limit=limit, offset=offset,
    )


def _search_places_req(keywords, *, kind=None, open_only=False, limit=50, offset=0) -> SearchPlacesRequest:
    """构造无默认值 ToolRequestModel 的 SearchPlacesRequest。"""
    return SearchPlacesRequest(keywords=keywords, kind=kind, open_only=open_only, limit=limit, offset=offset)


def _tail_catalog_environment(tmp_path):
    """构造超过 100 项商品的环境副本，目标商品按单价排序落在第 100 名之后（远超 20）。"""
    root = copy_environment(tmp_path / "tail-catalog")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    for idx in range(1, 116):
        rows.append({
            "goods_id": f"filler-{idx:03d}",
            "name": f"常规饮品-{idx:03d}",
            "category": "drink",
            "price_fen": 100 + idx * 10,
            "store_kinds": ["cafe"],
        })
    rows.append({
        "goods_id": "tail-hibiscus-tea",
        "name": "长尾限定洛神花茶",
        "category": "drink",
        "price_fen": 8800,
        "store_kinds": ["cafe"],
    })
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))
    return root


def _closed_cafe_environment(tmp_path, folder: str = "closed-cafe"):
    """令三杯咖啡与相关饮品商户在周一（1月5日）全天打烊，用于验证不可买报价与地点 open_only 筛选。"""
    root = copy_environment(tmp_path / folder)
    locs = catalog_rows(root, "location.jsonl")
    for row in locs:
        if row["location_id"] in {"cafe", "noodle-1", "noodle-2", "store"}:
            row["opens"] = {"weekly": {"tue": [["00:00", "24:00"]]}}
    write_catalog_rows(root, "location.jsonl", locs)
    return root


def test_tail_recall_over_hundred_goods_and_place_filtering(engine_factory, tmp_path):
    """超过 100 项商品时目标排在后 20 名之外：分页可翻遍全部目录，关键词加报价核对精准召回，地点按距离与营业筛选。"""
    root = _tail_catalog_environment(tmp_path)
    engine = engine_factory(environment_root=root)
    _, started = act(engine, "tail", search_world("找洛神花茶"))
    assert started.status == "in_progress", started.error_code
    advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    all_page1 = catalog.search_goods(_search_goods_req(["常规", "花茶"], limit=50, offset=0))
    all_page2 = catalog.search_goods(_search_goods_req(["常规", "花茶"], limit=50, offset=50))
    all_page3 = catalog.search_goods(_search_goods_req(["常规", "花茶"], limit=50, offset=100))
    assert all_page1["total"] > 100
    assert all(row["goods_id"] != "tail-hibiscus-tea" for row in all_page1["items"][:20])
    combined_ids = [row["goods_id"] for page in (all_page1, all_page2, all_page3) for row in page["items"]]
    assert combined_ids.index("tail-hibiscus-tea") >= 100

    targeted = catalog.search_goods(_search_goods_req(["洛神花茶"], max_price_yuan=90.0, open_only=True))
    assert [row["goods_id"] for row in targeted["items"]] == ["tail-hibiscus-tea"]
    offers = catalog.inspect_goods_offers(InspectGoodsOffersRequest(goods_ids=["tail-hibiscus-tea", "missing-id"]))
    assert offers["items"][0]["found"] is True and offers["items"][0]["price_fen"] == 8800
    assert offers["items"][1] == {"goods_id": "missing-id", "found": False, "error": "Out of scope or unknown goods_id"}

    closed_engine = engine_factory(environment_root=_closed_cafe_environment(tmp_path))
    act(closed_engine, "place-s", search_world("找咖啡馆"))
    advance_next(closed_engine)
    [(_, closed_catalog)] = closed_engine.due_searches()
    all_places = closed_catalog.search_places(_search_places_req(["三杯咖啡", "家"], open_only=False))
    distances = [closed_engine.context.catalog.shortest_m("home", row["location_id"]) for row in all_places["items"]]
    assert distances == sorted(distances) and all_places["items"][0]["location_id"] == "home"
    cafe_row = next(row for row in all_places["items"] if row["location_id"] == "cafe")
    assert cafe_row["open_now"] is False and "今日休息" in cafe_row["today_hours"]
    open_places = closed_catalog.search_places(_search_places_req(["三杯咖啡"], open_only=True))
    assert open_places["items"] == []


@pytest.mark.asyncio
async def test_search_session_fact_guard_prevents_hallucinated_ids_and_prices(engine_factory):
    """SearchSessionRunner 与 Fact Guard：编造 ID 与越界 app ID 在循环内被拦截重试，记忆和公开详情绝不留编造数字。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, started = act(engine, "fg", search_world("在饿了么买柠檬茶和牛肉面", prefer="饿了么"))
    assert started.status == "in_progress", started.error_code
    advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    assert task.scoped_app_id == "eleme"

    # 直接验证 SearchCatalogView 对越界 goods_id（beef-noodles 仅美团有）和不存在 ID 的拦截
    verdict = catalog.validate_submission(SubmitSearchResultsRequest(
        goods_ids=["beef-noodles", "fake-goods-999"], place_ids=["fake-place-1"], summary="测试拦截",
    ))
    assert verdict["valid"] is False and verdict["error_code"] == "SEARCH_FACT_INVALID"
    assert set(verdict["invalid_goods_ids"]) == {"beef-noodles", "fake-goods-999"}
    assert verdict["invalid_place_ids"] == ["fake-place-1"]

    model = ScriptedModel([
        ToolResponse("search_goods", {
            "keywords": ["柠檬茶"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 50, "offset": 0,
        }, call_id="sc-1", reasoning=None),
        # 模型尝试伪造商品 ID 与夸大价格并夹带只在美团出售的牛肉面 -> Fact Guard 在环内挡回
        ToolResponse("submit_search_results", {
            "goods_ids": ["hallucinated-999", "beef-noodles"],
            "place_ids": [],
            "summary": "编造 999 元",
        }, call_id="sc-2", reasoning=None),
        # 模型按真实白名单重新提交
        ToolResponse("submit_search_results", {
            "goods_ids": ["lemon-tea"],
            "place_ids": ["office"],
            "summary": "饿了么三杯咖啡售柠檬茶",
        }, call_id="sc-3", reasoning=None),
    ])
    binding = resolved_binding(model, frozen=engine.context.inputs, stage="search", agent_id=1)
    outcome = await SearchSessionRunner().run(task, catalog, binding)
    assert outcome.goods_ids == ("lemon-tea",) and outcome.place_ids == ("office",)
    # 第二回合给模型的反馈明确携带了 SEARCH_FACT_INVALID 与越界列表
    tool_fb = model.requests[2]["messages"][-1]["content"]
    assert "SEARCH_FACT_INVALID" in tool_fb and "hallucinated-999" in tool_fb
    assert all(draft.data["purpose"] == "search_session" for draft in outcome.evidence_drafts)
    assert all(draft.activity_id == task.activity_id for draft in outcome.evidence_drafts)

    # 在 complete_searches 入口再次直接挡住伪造提交（即使绕过 SearchCatalogView）
    with pytest.raises(ProtocolError, match="Fact guard rejected"):
        engine.complete_searches([SearchSessionOutcome(
            action_id=task.action_id, agent_id=1, activity_id=task.activity_id,
            goods_ids=("beef-noodles",), place_ids=(), summary="绕过核验",
        )])

    engine.complete_searches([outcome])
    done = engine.context.state.activities[task.activity_id]
    projected = search_details(done.model_dump(mode="json"), engine.context.inputs)
    assert projected["scope"] == SCOPED and projected["app"] == "饿了么"
    assert [row["price"] for row in projected["goods_rows"]] == ["12 元"]
    assert projected["goods_rows"][0]["product"] == "柠檬茶" and projected["goods_rows"][0]["now"] == "可以下单"
    assert "999" not in str(projected)
    # 公开详情与内存/SQLite 证据均不含内部身份与幻觉数字
    for hidden in ("lemon-tea", "eleme", "cafe", "goods_id", "location_id"):
        assert hidden not in str(projected)
    assert all(row["kind"] == "office" and "office" not in row["name"] for row in projected["place_rows"])
    stored_req = [row for row in engine.context.store.read_agent_events("model_request", 1)
                  if row.activity_id == task.activity_id]
    assert len(stored_req) == 3

    # 完成后的次轮决策之后，今日记忆条目包含完整「当时查到」真实报价且绝无伪造价格
    act(engine, "after-fact", use_app("哔哩哔哩"))
    today = published_today(engine.context.inputs, engine.context.store, 1,
                            engine.context.state.current_time, engine.context.store.last_event_seq)
    (search_entry,) = [e for e in today.entries if any(line.kind == "search" for line in e.lines)]
    full = entry_full(search_entry)
    assert any("柠檬茶 12元" in line for line in full["当时查到"])
    assert not any("999" in line for line in full["当时查到"])


@pytest.mark.asyncio
async def test_search_session_budget_exhausted_raises_with_evidence(engine_factory):
    """SearchSessionRunner 回合预算耗尽时抛 ProtocolError 并在异常上保留 evidence_drafts。"""
    engine = engine_factory()
    _, started = act(engine, "exhaust", search_world("反复查"))
    advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    model = ScriptedModel([
        ToolResponse("search_places", {"keywords": ["家"], "kind": None, "open_only": False, "limit": 50, "offset": 0},
                     call_id="ex-1", reasoning=None),
        # 第 2 回合是最后一回合，仅开放 submit_search_results；模型误调了 search_places，被截回但预算耗尽
        ToolResponse("search_places", {"keywords": ["公司"], "kind": None, "open_only": False, "limit": 50, "offset": 0},
                     call_id="ex-2", reasoning=None),
    ])
    binding = resolved_binding(model, frozen=engine.context.inputs, stage="search", agent_id=1, max_turns=2)
    with pytest.raises(ProtocolError, match="Search session turn budget exhausted") as caught:
        await SearchSessionRunner().run(task.model_copy(update={"turn_budget": 2}), catalog, binding)
    assert len(getattr(caught.value, "evidence_drafts", ())) >= 2


def test_app_use_attribution_covers_prefer_lock_majority_tie_break_and_empty_draw(engine_factory, tmp_path):
    """app_use 归因：prefer 指定即锁定；否则取 goods_rows 卖方多数票并按字典序平票破局；无候选时按 search-app 流抽取。"""
    # 分支 A：prefer 指定锁定（即使结果为空仍为 eleme）
    scoped_engine = engine_factory()
    _, a_started = act(scoped_engine, "pref", search_world("查空白", prefer="饿了么"))
    advance_next(scoped_engine)
    [(t_scoped, _)] = scoped_engine.due_searches()
    scoped_engine.complete_searches([SearchSessionOutcome(
        action_id=t_scoped.action_id, agent_id=1, activity_id=t_scoped.activity_id,
        goods_ids=(), place_ids=(), summary="没找到",
    )])
    assert scoped_engine.context.state.activities[t_scoped.activity_id].data["app_id"] == "eleme"

    # 分支 B：未设 prefer，命中多件商品 -> 多数票决出 meituan（牛肉面只美团，柠檬茶两家）
    maj_engine = engine_factory()
    act(maj_engine, "maj", search_world("吃面配茶"))
    advance_next(maj_engine)
    [(t_maj, _)] = maj_engine.due_searches()
    maj_engine.complete_searches([SearchSessionOutcome(
        action_id=t_maj.action_id, agent_id=1, activity_id=t_maj.activity_id,
        goods_ids=("beef-noodles", "lemon-tea"), place_ids=(), summary="牛肉面与柠檬茶",
    )])
    maj_done = maj_engine.context.state.activities[t_maj.activity_id]
    assert maj_done.data["app_id"] == "meituan"
    assert search_details(maj_done.model_dump(mode="json"), maj_engine.context.inputs)["scope"] == ALL_ROUTES

    # 分支 C：未设 prefer 且票数打平（lemon-tea 在 eleme 与 meituan 各 1 票）-> 按字典序取 eleme
    tie_engine = engine_factory()
    act(tie_engine, "tie", search_world("单查柠檬茶"))
    advance_next(tie_engine)
    [(t_tie, _)] = tie_engine.due_searches()
    tie_engine.complete_searches([SearchSessionOutcome(
        action_id=t_tie.action_id, agent_id=1, activity_id=t_tie.activity_id,
        goods_ids=("lemon-tea",), place_ids=(), summary="柠檬茶",
    )])
    assert tie_engine.context.state.activities[t_tie.activity_id].data["app_id"] == "eleme"

    # 分支 D：未设 prefer 且无命中或所选商品仅到店无下单 app -> random_stream(search-app:{action_id}) 抽取可用下单 app
    empty_engine = engine_factory(environment_root=_closed_cafe_environment(tmp_path))
    act(empty_engine, "draw", search_world("随便逛逛"))
    advance_next(empty_engine)
    [(t_empty, _)] = empty_engine.due_searches()
    activity = empty_engine.context.state.activities[t_empty.activity_id]
    apps = order_app_ids(empty_engine.context.inputs.catalog)
    expected_app = random_stream(empty_engine.context.state.random_state,
                                 f"search-app:{activity.origin_action_id}").choice(apps)
    empty_engine.complete_searches([SearchSessionOutcome(
        action_id=t_empty.action_id, agent_id=1, activity_id=t_empty.activity_id,
        goods_ids=(), place_ids=("cafe",), summary="打烊了",
    )])
    empty_done = empty_engine.context.state.activities[t_empty.activity_id]
    assert empty_done.data["app_id"] == expected_app
    # 不可买商品（cafe 打烊导致 lemon-tea 当时不可买）在 search_details 与记忆中同样保留状态且不写虚构店铺
    unavailable_engine = engine_factory(environment_root=_closed_cafe_environment(tmp_path, "closed-cafe-unavail"))
    act(unavailable_engine, "unavail", search_world("柠檬茶"))
    advance_next(unavailable_engine)
    [(t_unavail, _)] = unavailable_engine.due_searches()
    unavailable_engine.complete_searches([SearchSessionOutcome(
        action_id=t_unavail.action_id, agent_id=1, activity_id=t_unavail.activity_id,
        goods_ids=("lemon-tea",), place_ids=("cafe",), summary="三杯咖啡周一休息",
    )])
    unavail_done = unavailable_engine.context.state.activities[t_unavail.activity_id]
    unavail_proj = search_details(unavail_done.model_dump(mode="json"), unavailable_engine.context.inputs)
    assert unavail_proj["goods_rows"][0]["now"] == "不可下单"
    assert unavail_proj["goods_rows"][0]["store"] is None and unavail_proj["goods_rows"][0]["expected_at"] is None
    assert "rating" not in unavail_proj["goods_rows"][0]
    act(unavailable_engine, "after-unavail", do_at("家", "测试休息"))
    today = published_today(unavailable_engine.context.inputs, unavailable_engine.context.store, 1,
                            unavailable_engine.context.state.current_time,
                            unavailable_engine.context.store.last_event_seq)
    (line,) = [row for e in today.entries for row in e.lines if row.kind == "search"]
    assert "柠檬茶 12元，当时不可买" in line.answers
    assert any("三杯咖啡（cafe" in item and "今日休息" in item for item in line.answers)
    assert Counter(["bilibili", "eleme", "meituan"]) == Counter(apps)
    assert attribute_search_app(
        unavailable_engine.context.inputs, unavailable_engine.context.state.random_state, activity, (),
    ) == expected_app
    pub = public_result({"occurred_at": unavail_done.ended_at, "status": "completed", "message": "ok",
                         "data": {"activity": unavail_done.model_dump(mode="json")}},
                        unavailable_engine.context.inputs, {})
    assert pub["details"]["search"] == unavail_proj
