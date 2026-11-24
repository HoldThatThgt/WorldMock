"""@brief search_world 搜索耗时随机流、意向筛选、SearchCatalogView、Fact Guard、归因与记忆合并规则。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.life.conftest import action_identity, advance_next, do_at, search_world, stage_day_plan
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, finish_activity
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.randomness import random_stream
from worldmock.recording_engine.events.models import EventDraft
from worldmock.simulation_engine.activities.search.search_activity import attribute_search_app, search_seconds
from worldmock.simulation_engine.commerce.purchase.order_apps import order_app_ids
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.layers.search_projection import (
    ALL_ROUTES,
    SCOPED,
    UNFINISHED,
    search_details,
)
from worldmock.simulation_engine.observation.memory.days import published_today
from worldmock.state.decisions.barrier import SearchSessionOutcome
from worldmock.state.interaction.search_requests import (
    InspectGoodsOffersRequest,
    SearchGoodsRequest,
    SearchPlacesRequest,
    SubmitSearchResultsRequest,
)
from worldmock.state.world.kinds import ActivityStatus


def test_search_duration_uses_seeded_stream_in_180_to_420_range(engine_factory):
    """search_world 计划耗时由 search-duration:{action_id} 随机流在 180 到 420 秒确定抽取。"""
    engine = engine_factory()
    stage_day_plan(engine)
    durations = {search_seconds(engine.context.state.random_state, f"w:action:{i:04d}") for i in range(1, 30)}
    assert min(durations) >= 180 and max(durations) <= 420 and len(durations) > 1
    aid = action_identity("search-dur")
    expected = random_stream(engine.context.state.random_state, f"search-duration:{aid}").randint(180, 420)
    _, started = act(engine, "search-dur", search_world("想喝饮品"))
    assert started.status == "in_progress"
    activity = engine.context.state.activities[started.activity_id]
    assert activity.planned_duration_seconds == expected
    assert activity.data["app_id"] is None and activity.data["scoped"] is False
    assert engine.context.state.agents[1].suspended_loop is not None
    assert search_details(activity.model_dump(mode="json"), engine.context.inputs) == {
        "app": None, "state": UNFINISHED,
    }


def test_search_catalog_view_enforces_prefer_scope_offers_places_and_fact_guard(engine_factory):
    """意向 prefer 解析成功时写入 data.app_id、限定可买商品范围并经 Fact Guard 拦截越界或不存在的身份。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "scoped-s", search_world("想买牛肉面", prefer="美团"))
    activity = engine.context.state.activities[started.activity_id]
    assert activity.data["app_id"] == "meituan" and activity.data["scoped"] is True
    target_time = engine.scheduling_state().next_event_at
    assert int(target_time.timestamp()) == activity.planned_end_at
    advance_next(engine)
    [(spec, catalog)] = engine.due_searches()
    assert spec.scoped_app_id == "meituan"
    goods = catalog.search_goods(SearchGoodsRequest(
        keywords=["牛肉"], category=None, app=None, store=None,
        max_price_yuan=100.0, open_only=False, limit=20, offset=0,
    ))
    assert goods["total"] >= 1
    offers = catalog.inspect_goods_offers(InspectGoodsOffersRequest(goods_ids=["beef-noodles", "no-such-goods"]))
    assert offers["items"][0]["found"] is True and offers["items"][1]["found"] is False
    places = catalog.search_places(SearchPlacesRequest(
        keywords=["公司"], kind=None, open_only=False, limit=20, offset=0,
    ))
    assert places["total"] >= 1 and places["items"][0]["location_id"] == "office"
    bad = catalog.validate_submission(
        SubmitSearchResultsRequest(goods_ids=["fabricated-goods"], place_ids=["no-place"], summary="测试"),
    )
    assert bad["valid"] is False and bad["error_code"] == "SEARCH_FACT_INVALID"
    good = catalog.validate_submission(
        SubmitSearchResultsRequest(goods_ids=["beef-noodles"], place_ids=["office"], summary="找到牛肉面和公司"),
    )
    assert good["valid"] is True and good["goods_ids"] == ["beef-noodles"]


def test_search_validation_and_complete_searches_attribute_app_and_release_slot(engine_factory):
    """search_world 校验空/超长参数、complete_searches 校验 Fact Guard、归因 data.app_id、释出副槽位并注入今天条目。"""
    engine = engine_factory()
    stage_day_plan(engine)
    with pytest.raises(ValueError):
        search_world("   ")
    _, started = act(engine, "unscoped-s", search_world("想吃零食"))
    activity = engine.context.state.activities[started.activity_id]
    advance_next(engine)
    [(spec, catalog)] = engine.due_searches()
    assert not catalog.search_goods(SearchGoodsRequest(
        keywords=["零食"], category=None, app="nonexistent-app", store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))["items"]
    assert not catalog.search_places(SearchPlacesRequest(
        keywords=["不存在地点"], kind=None, open_only=False, limit=20, offset=0,
    ))["items"]
    # Fact Guard 拒绝伪造商品
    for bad in (
        SearchSessionOutcome(action_id=spec.action_id, agent_id=999, activity_id=spec.activity_id,
                             goods_ids=(), place_ids=(), summary="错归属"),
        SearchSessionOutcome(action_id=spec.action_id, agent_id=1, activity_id=spec.activity_id,
                             goods_ids=("fake-id",), place_ids=(), summary="伪造商品"),
        SearchSessionOutcome(action_id=spec.action_id, agent_id=1, activity_id=spec.activity_id,
                             goods_ids=(), place_ids=(), summary="错草稿",
                             evidence_drafts=(EventDraft(event_type="action_result", occurred_at=0, agent_id=1),)),
    ):
        with pytest.raises(ProtocolError):
            engine.complete_searches([bad])
    model_req = EventDraft(event_type="model_request", occurred_at=0, agent_id=1,
                           wakeup_id=spec.origin_wakeup_id, action_id=spec.action_id,
                           activity_id=spec.activity_id, visibility="system", source_kind="model",
                           data={"phase": "request", "call_id": "sc1", "request": {}})
    receipt = engine.complete_searches([SearchSessionOutcome(
        action_id=spec.action_id, agent_id=1, activity_id=spec.activity_id,
        goods_ids=("lemon-tea",), place_ids=("office",), summary="茶饮推荐与公司营业信息",
        evidence_drafts=(model_req,),
    )])
    assert receipt.completed_count == 1
    done = engine.context.state.activities[spec.activity_id]
    assert done.status == ActivityStatus.COMPLETED and done.concluded_at == done.ended_at
    assert done.data["app_id"] == "eleme" and done.data["scoped"] is False
    projected = search_details(done.model_dump(mode="json"), engine.context.inputs)
    assert projected["scope"] == ALL_ROUTES and projected["app"] == "饿了么"
    assert len(projected["goods_rows"]) == 1 and len(projected["place_rows"]) == 1
    assert projected["goods_rows"][0]["product"] == "柠檬茶" and projected["goods_rows"][0]["price"] == "12 元"
    assert isinstance(projected["place_rows"][0]["today_hours"], list)
    scoped_unfinished = search_details(
        {**done.model_dump(mode="json"), "status": "active", "data": {"app_id": "meituan"}}, engine.context.inputs,
    )
    assert scoped_unfinished == {"app": "美团", "state": UNFINISHED}
    with_omitted = search_details(
        {**done.model_dump(mode="json"),
         "data": {**done.data, "answer": {**done.data["answer"], "omitted": 2}}},
        engine.context.inputs,
    )
    assert with_omitted["omitted"] == "还有 2 件没列"
    pub = public_result({"occurred_at": done.ended_at, "status": "completed", "message": "ok",
                         "data": {"activity": done.model_dump(mode="json")}}, engine.context.inputs, {})
    assert pub["details"]["search"] == projected
    assert "planned_end" in engine.context.state.agents[1].pending_wake_reasons
    # 空命中触发随机数流兜底归因 app_id；无下单 app 时返回 None
    app_drawn = attribute_search_app(engine.context.inputs, engine.context.state.random_state, activity, ())
    assert app_drawn in set(order_app_ids(engine.context.inputs.catalog))
    empty_apps = replace(engine.context.inputs, catalog=replace(engine.context.inputs.catalog, apps={}))
    assert attribute_search_app(empty_apps, engine.context.state.random_state, activity, ()) is None
    # 恢复后同日继续唤醒并归并到同一个 TodayEntry
    [wakeup] = engine.prepare_barrier((1,))
    assert wakeup.mode == "resume_search" and wakeup.wakeup_id == spec.origin_wakeup_id
    assert wakeup.search_result_payload is not None and wakeup.search_result_payload["search"] == projected
    sandbox = engine.open_sandbox(wakeup)
    sandbox.dispatch_world_tool("start_main_activity", {"action": "sleep", "target": "60", "detail": None},
                                f"{wakeup.wakeup_id}:action:0002")
    engine.commit_barrier([sandbox.finish_wakeup(None)])
    today = published_today(engine.context.inputs, engine.context.store, 1,
                            engine.context.state.current_time, engine.context.store.last_event_seq)
    merged = [entry for entry in today.entries if entry.wakeup_id == spec.origin_wakeup_id]
    assert len(merged) == 1
    assert any(line.kind == "search" for line in merged[0].lines)


def _city_isolation_search_env(tmp_path):
    """构造含双城直达枢纽、非直达远城、异城商铺、即时商品与一次性地点单元的隔离环境。"""
    root = copy_environment(tmp_path / "search-city-isolation")
    locs = catalog_rows(root, "location.jsonl")
    locs.extend([
        {"app_ids": [], "city_id": "city-b", "kind": "park", "location_id": "hub-b", "name": "乙城枢纽"},
        {"app_ids": [], "city_id": "city-b", "kind": "b_store", "location_id": "b-store", "name": "乙城小铺",
         "rating": 4.2},
        {"app_ids": [], "city_id": "city-c", "kind": "park", "location_id": "station-c", "name": "丙城远站"},
    ])
    write_catalog_rows(root, "location.jsonl", sorted(locs, key=lambda row: row["location_id"]))
    goods = catalog_rows(root, "merchant-catalog.jsonl")
    goods.extend([
        {"app_ids": ["bilibili"], "category": "goods", "goods_id": "instant-pass",
         "instant": True, "name": "在线月卡", "price_fen": 3000},
        {"category": "snack", "goods_id": "b-cake", "name": "乙城糕点", "price_fen": 1500,
         "store_kinds": ["b_store"]},
    ])
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda row: row["goods_id"]))
    contents = catalog_rows(root, "content-catalog.jsonl")
    contents.append({
        "content_id": "home-once-unit", "duration_seconds": 120, "exertion": "light",
        "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
        "repeatable": False, "social_relief": "none", "summary": "一次性整理测试摘要。",
        "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "一次性整理",
    })
    write_catalog_rows(root, "content-catalog.jsonl", sorted(contents, key=lambda row: row["content_id"]))
    write_catalog_rows(root, "map.jsonl", [
        {"a": "home", "b": "hub-b", "length_m": 10000, "travel_method_ids": ["driving"]},
        {"a": "hub-b", "b": "station-c", "length_m": 20000, "travel_method_ids": ["driving"]},
    ])
    return root


def test_search_catalog_view_isolates_city_preserves_instant_and_hides_locked_units(engine_factory, tmp_path):
    """同城商铺隔离、跨城即时商品保留、直达异地枢纽纳入 search_places、已耗尽单元过滤及分城天气字典。"""
    engine = engine_factory(environment_root=_city_isolation_search_env(tmp_path))
    assert set(engine.context.state.weather_by_date["2026-01-05"]) == {"test-city", "city-b", "city-c"}

    _, done_once = act(engine, "do-once", do_at("家", "一次性整理"))
    assert done_once.status == "in_progress", done_once.error_code
    finish_activity(engine, done_once, "finish-once")

    _, started = act(engine, "city-search", search_world("找商品和去乙城枢纽"))
    assert started.status == "in_progress", started.error_code
    advance_next(engine)
    [(spec, catalog)] = engine.due_searches()

    inst = catalog.search_goods(SearchGoodsRequest(
        keywords=["在线月卡"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))
    assert [row["goods_id"] for row in inst["items"]] == ["instant-pass"]
    assert inst["items"][0]["route"] == "即时到账" and inst["items"][0]["delivery_fee_fen"] == 0
    assert inst["items"][0]["store_id"] is None and inst["items"][0]["selling_apps"] == ["哔哩哔哩"]

    assert catalog.search_goods(SearchGoodsRequest(
        keywords=["乙城糕点"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))["total"] == 0
    assert catalog.inspect_goods_offers(InspectGoodsOffersRequest(goods_ids=["b-cake"]))["items"][0]["found"] is False

    places = catalog.search_places(SearchPlacesRequest(
        keywords=["家", "乙城", "丙城"], kind=None, open_only=False, limit=50, offset=0,
    ))
    place_ids = {row["location_id"] for row in places["items"]}
    assert "hub-b" in place_ids and "b-store" not in place_ids and "station-c" not in place_ids
    home_row = next(row for row in places["items"] if row["location_id"] == "home")
    assert "一次性整理" not in home_row["units"]
    hub_row = next(row for row in places["items"] if row["location_id"] == "hub-b")
    assert hub_row["travel_options"]

    for bad_goods, bad_places in ((["b-cake"], []), ([], ["b-store"]), ([], ["station-c"])):
        res = catalog.validate_submission(SubmitSearchResultsRequest(
            goods_ids=bad_goods, place_ids=bad_places, summary="越界",
        ))
        assert res["valid"] is False and res["error_code"] == "SEARCH_FACT_INVALID"
    assert catalog.validate_submission(SubmitSearchResultsRequest(
        goods_ids=["instant-pass"], place_ids=["hub-b"], summary="合规",
    ))["valid"] is True
