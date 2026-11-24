"""即时电子履约（instant）、与快递混单同 app 结算、gain_on_receipt 钳位递增、同城商品隔离与导出不变量。"""

from __future__ import annotations

from datetime import timedelta

import jsonschema
import pytest

from tests.footprints.recording import artifact_rows, export_completed, reproject
from tests.life.conftest import (
    SEARCH_TOOL,
    bring_to_window,
    conclude_pending,
    progress_calls,
    purchase,
    search_world,
    target_actions,
    tool_targets,
    travel,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import (
    act,
    act_group,
    advance_next,
    events,
    finish_activity,
    observe_only,
    settle_decision,
)
from worldmock.agents.prompt.life_prompt import life_prompt_sha256
from worldmock.agents.prompt.prompt_schemas import PROGRESS_LAYER_SCHEMA
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.callback_contract import StateGainResult
from worldmock.recording_engine.validation.validation import validate_export
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext, _purchase_facts
from worldmock.simulation_engine.observation.wording.settled_text import experience_sentence, purchase_sentence
from worldmock.state.decisions.barrier import SearchSessionOutcome
from worldmock.state.interaction.search_requests import (
    InspectGoodsOffersRequest,
    SearchGoodsRequest,
    SubmitSearchResultsRequest,
)
from worldmock.state.world.kinds import ROUTE_NAMES, FulfillmentKind
from worldmock.state.world.order_notices import ORDER_DELIVERED, ORDER_PLACED, ORDER_SHIPPED, PAYMENT_SUCCEEDED


def _instant_and_gain_environment(root):
    """@brief 构造含 instant 票券与重置券、乙城本地店铺商品及 points(gain_on_goods)/kit-reset(reset_on_goods) 的副本。"""
    locs = catalog_rows(root, "location.jsonl")
    locs.extend([
        {"app_ids": [], "city_id": "city-b", "kind": "b_bistro", "location_id": "b-bistro",
         "name": "乙城风味屋", "rating": 4.2},
        {"app_ids": [], "city_id": "city-b", "kind": "place", "location_id": "b-hub", "name": "乙城驿站前街"},
    ])
    write_catalog_rows(root, "location.jsonl", sorted(locs, key=lambda r: r["location_id"]))

    goods = catalog_rows(root, "merchant-catalog.jsonl")
    goods.extend([
        {"category": "drink", "goods_id": "b-special-tea", "name": "乙城冻茶", "price_fen": 1600,
         "store_kinds": ["b_bistro"]},
        {"app_ids": ["bilibili"], "category": "goods", "goods_id": "digital-ticket",
         "instant": True, "name": "数字门票", "price_fen": 5000},
        {"app_ids": ["bilibili"], "category": "goods", "goods_id": "reset-voucher",
         "instant": True, "name": "重置卡券", "price_fen": 2000},
    ])
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda r: r["goods_id"]))

    callbacks = [
        {"cap": 3, "gain_on_goods": [], "initial": 0, "reset_on_goods": ["reset-voucher"], "state": "kit-reset"},
        {"cap": 2, "gain_on_goods": ["digital-ticket"], "initial": 0, "reset_on_goods": [], "state": "points"},
    ]
    write_catalog_rows(root, "callbacks.jsonl", callbacks)
    write_catalog_rows(root, "map.jsonl", [
        {"a": "b-hub", "b": "home", "length_m": 10000, "travel_method_ids": ["driving"]},
    ])
    return root


def _start_at(place_name: str, *, hours: int = 8, seed: int = 7, reconsideration: int | None = None):
    """@brief 返回短窗、快速反馈的环境配置修改器。"""
    def mutate(persona, config):
        persona["initial_location"] = place_name
        config["seed"] = seed
        config["activity_feedback_seconds"] = 60
        if reconsideration is not None:
            config["reconsideration_interval_seconds"] = reconsideration
        config["stop_time"] = config["start_time"] + timedelta(hours=hours)
    return mutate


def test_instant_tool_preview_query_route_and_city_commerce_isolation(engine_factory, tmp_path):
    """@brief instant 在工具层写运费0元、付款即收货，搜索写即时到账并过 Schema，且店铺商品与搜索按当前城市隔离。"""
    root = _instant_and_gain_environment(copy_environment(tmp_path / "env"))
    assert life_prompt_sha256() == "bcf0fdf97fb01ef864e92f2a42d66e9dbe412c0d3d1d389d68ca885a79c5a154"
    assert ROUTE_NAMES["instant"] == "即时到账"

    engine = engine_factory(environment_root=root, mutate=_start_at("家", seed=4))
    obs = observe_only(engine, "obs-home")
    assert "乙城冻茶" not in tool_targets(obs) and "数字门票" not in tool_targets(obs)
    layer_ctx = LayerContext(engine.context, 1, {}, [], ())
    assert _purchase_facts(layer_ctx, {"goods_id": "digital-ticket"})["note"] == "运费0元，付款即收货"

    # 在 test-city 搜索 数字门票：给出即时到账行且通过 PROGRESS_LAYER_SCHEMA。
    _, qres = act(engine, "q-instant", search_world("数字门票"))
    assert qres.status == "in_progress", qres.error_code
    advance_next(engine)
    [(task, view)] = engine.due_searches()
    found = view.search_goods(SearchGoodsRequest(
        keywords=["数字门票"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))
    assert found["total"] == 1
    assert found["items"][0]["route"] == "即时到账" and found["items"][0]["delivery_fee_fen"] == 0
    engine.complete_searches([SearchSessionOutcome(
        action_id=task.action_id, agent_id=1, activity_id=task.activity_id,
        goods_ids=("digital-ticket",), place_ids=(), summary="数字门票即时到账",
    )])
    obs_q = observe_only(engine, "obs-query")
    jsonschema.validate(obs_q["progress_layer"], PROGRESS_LAYER_SCHEMA)
    q_call = progress_calls(obs_q, SEARCH_TOOL)[-1]
    rows = q_call["result"]["details"]["search"]["goods_rows"]
    assert len(rows) == 1
    assert rows[0]["route"] == "即时到账" and rows[0]["store"] is None and rows[0]["delivery_fee"] == "0 元"
    assert rows[0]["now"] == "可以下单"

    # 在乙城：callable_index.goods 只露出乙城门店类别饮品并隐藏跨城店铺商品，SearchCatalogView 同样隔离非本城门店商品。
    engine_b = engine_factory(environment_root=root, mutate=_start_at("乙城驿站前街"))
    bring_to_window(engine_b, ("b-special-tea",))
    obs_b = observe_only(engine_b, "obs-b")
    assert "乙城冻茶" in tool_targets(obs_b) and "牛肉面" not in tool_targets(obs_b)
    assert all(row["category"] == "饮料" for row in obs_b["tool_layer"]["callable_index"]["goods"])

    _, q_beef = act(engine_b, "q-beef", search_world("牛肉面"))
    assert q_beef.status == "in_progress", q_beef.error_code
    advance_next(engine_b)
    [(task_b, view_b)] = engine_b.due_searches()
    assert view_b.search_goods(SearchGoodsRequest(
        keywords=["牛肉面"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))["total"] == 0
    assert view_b.search_goods(SearchGoodsRequest(
        keywords=["乙城冻茶"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))["total"] == 1
    assert view_b.search_goods(SearchGoodsRequest(
        keywords=["数字门票"], category=None, app=None, store=None,
        max_price_yuan=None, open_only=False, limit=20, offset=0,
    ))["total"] == 1
    assert view_b.inspect_goods_offers(InspectGoodsOffersRequest(goods_ids=["beef-noodles"]))["items"] == [{
        "goods_id": "beef-noodles", "found": False, "error": "Out of scope or unknown goods_id",
    }]
    bad_submit = view_b.validate_submission(SubmitSearchResultsRequest(
        goods_ids=["beef-noodles"], place_ids=[], summary="跨城查牛肉面",
    ))
    assert bad_submit["valid"] is False and bad_submit["error_code"] == "SEARCH_FACT_INVALID"
    engine_b.complete_searches([SearchSessionOutcome(
        action_id=task_b.action_id, agent_id=1, activity_id=task_b.activity_id,
        goods_ids=(), place_ids=(), summary="本城无售牛肉面",
    )])

    try:
        _, buy_remote = act(engine_b, "buy-remote-beef", purchase("牛肉面"))
    except RegistrationError as error:
        raise AssertionError(f"Cross-city delivery leaked outside current city: {error}") from error
    assert buy_remote.status == "rejected" and buy_remote.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert buy_remote.data["reason"] == "No stores in the current city sell this item."


def test_mixed_instant_and_station_pickup_same_app_basket_settlement(engine_factory, tmp_path):
    """@brief 同 app 混购 instant 与 station_pickup：合付一笔但逐笔结算，instant 付款即收货、无物流且省去商铺预计。"""
    root = _instant_and_gain_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", seed=4))
    _, buy = act(engine, "mix-buy", purchase("数字门票", "平台笔记本", prefer="哔哩哔哩"))
    assert buy.status == "in_progress"

    orders = [engine.context.state.deliveries[key]
              for key in sorted(engine.context.state.activities[buy.activity_id].data["order_ids"])]
    by_goods = {order.goods_id: order for order in orders}
    inst, courier = by_goods["digital-ticket"], by_goods["online-notebook"]
    assert inst.fulfillment == FulfillmentKind.INSTANT and inst.store_id is None and inst.delivery_fee_fen == 0
    assert inst.weather_delay_seconds is None and inst.expected_at is not None
    assert courier.fulfillment == FulfillmentKind.STATION_PICKUP and courier.delivery_fee_fen > 0

    # 购买自然结束并结算：在同一次提交于 paid_at 当秒完成 instant 收货，不写物流排程。
    while engine.context.state.activities[buy.activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"mix-step-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    paid_state = engine.context.state
    inst_done = paid_state.deliveries[inst.order_id]
    courier_done = paid_state.deliveries[courier.order_id]
    assert inst_done.paid_at is not None and inst_done.status == "delivered"
    assert inst_done.arrived_at == inst_done.received_at == inst_done.paid_at
    assert courier_done.status == "pending" and courier_done.received_at is None

    scheduled_ids = {item.scheduled_id for item in paid_state.pending_events
                     if item.kind in {"order_shipped", "delivery_arrival"}}
    assert scheduled_ids == {f"shipped-{courier.order_id}", f"delivery-{courier.order_id}"}

    env_evs = events(engine, "environment_event")
    pay_ev = next(e for e in env_evs if e.data.get("kind") == "purchase_payment")
    assert set(pay_ev.data["order_ids"]) == {inst.order_id, courier.order_id}
    received_evs = [e for e in env_evs if e.data.get("kind") == "goods_received"]
    assert len(received_evs) == 1
    rec = received_evs[0]
    assert rec.data["order_id"] == inst.order_id and rec.occurred_at == inst_done.paid_at
    assert rec.caused_by == [pay_ev.event_id]
    assert "store_id" not in rec.data and "quality" not in rec.data
    notices = [e for e in env_evs if e.data.get("kind") in {"order_notification", "payment_notification"}]
    assert [n.data["title"] for n in notices if n.data.get("kind") == "order_notification"] == [ORDER_PLACED]
    assert len([n for n in notices if n.data.get("kind") == "payment_notification"]) == 1
    assert PAYMENT_SUCCEEDED

    obs = observe_only(engine, "obs-settled")
    assert [row["order"].split("（")[0] for row in obs["todo_layer"]["orders"]] == ["平台笔记本 1 份"]
    assert all(ev["type"] != "receipt" for ev in obs["progress_layer"]["events"])
    buy_call = progress_calls(obs, "start_side_activity")[-1]
    inst_pub = next(row for row in buy_call["result"]["details"]["purchases"] if row["product"] == "数字门票")
    assert "store" not in inst_pub and "rating" not in inst_pub and inst_pub["channel"] == "哔哩哔哩"

    # settled_text 购买句与无品质收货经历句均省略商铺与预计送达。
    layer_ctx = LayerContext(engine.context, 1, {}, [], ())
    inst_line = purchase_sentence(layer_ctx, inst_done.model_dump(mode="json"))
    courier_line = purchase_sentence(layer_ctx, courier_done.model_dump(mode="json"))
    assert "买了数字门票 1 份，付 50 元" in inst_line and "预计" not in inst_line
    assert "预计" in courier_line and "送到快递驿站" in courier_line
    exp_line = experience_sentence(layer_ctx, {
        "kind": "received", "occurred_at": inst_done.received_at,
        "goods_id": "digital-ticket", "quantity": 1,
    })
    assert "收到数字门票 1 份" in exp_line


def test_instant_purchase_allowed_on_road_and_with_departure_group(engine_factory, tmp_path):
    """@brief instant 商品可与出发同组开始，也可在无停留会话的途中下单且不阻断当前行程。"""
    root = _instant_and_gain_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", seed=4, reconsideration=60))
    _, group = act_group(engine, "depart-and-instant", travel("二味小炒", "步行"), purchase("数字门票"))
    assert all(row.status == "in_progress" for row in group)
    trip_id, buy_id = group[0].activity_id, group[1].activity_id

    # 先推进至组内购买自然完成并收尾，此时行程仍在进行中（人在路上，无 stay 会话）。
    while engine.context.state.activities[buy_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"step-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    assert engine.context.state.activities[trip_id].status == "active"
    conclude_pending(engine, "conclude-group-buy")
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    transit = [s for s in engine.context.state.sessions.values() if s.status == "active"]
    assert len(transit) == 1 and transit[0].kind == "transit"

    _, road_buy = act(engine, "buy-on-road", purchase("数字门票"))
    assert road_buy.status == "in_progress"
    while engine.context.state.activities[road_buy.activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"road-step-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    oid = engine.context.state.activities[road_buy.activity_id].data["order_ids"][0]
    assert engine.context.state.deliveries[oid].status == "delivered"


def test_gain_on_receipt_increments_and_clamps_versus_reset_to_cap(engine_factory, tmp_path):
    """@brief gain_on_goods 收货按 min(cap, before + 1) 写 gain_on_receipt 并钳位，与 reset_to_cap 区分开。"""
    root = _instant_and_gain_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", seed=4))

    for step in range(1, 4):
        _, res = act(engine, f"buy-ticket-{step}", purchase("数字门票"))
        finish_activity(engine, res, f"ticket-done-{step}")
    gains = [e for e in events(engine, "environment_event")
             if e.data.get("kind") == "callback_effect" and e.data.get("type") == "gain_on_receipt"]
    assert [(g.data["state"], g.data["goods_id"], g.data["before"], g.data["after"]) for g in gains] == [
        ("points", "digital-ticket", 0, 1),
        ("points", "digital-ticket", 1, 2),
        ("points", "digital-ticket", 2, 2),
    ]
    assert all(g.data["rule_id"] is None for g in gains)
    assert StateGainResult.model_validate(gains[0].data)
    assert engine.context.state.agents[1].callback_states["points"] == 2

    _, buy_reset = act(engine, "buy-reset", purchase("重置卡券"))
    finish_activity(engine, buy_reset, "reset-done")
    resets = [e for e in events(engine, "environment_event")
              if e.data.get("kind") == "callback_effect" and e.data.get("type") == "reset_to_cap"]
    assert len(resets) == 1
    assert resets[0].data["state"] == "kit-reset" and resets[0].data["before"] == 0 and resets[0].data["after"] == 3
    assert engine.context.state.agents[1].callback_states["kit-reset"] == 3


def test_instant_export_invariants_and_tamper_rejection(engine_factory, tmp_path):
    """@brief instant 完整导出不带商铺/品质、无发货送达通知且经 validate_export 认证，篡改即拒。"""
    root = _instant_and_gain_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", hours=1))
    _, buy = act(engine, "export-instant", purchase("数字门票"))
    finish_activity(engine, buy, "export-instant-done")

    export_dir = tmp_path / "export-ok"
    try:
        manifest, ev_rows, _ = export_completed(engine, export_dir)
    except IntegrityError as error:
        raise AssertionError(f"Clean instant export must allow absent store/quality: {error}") from error
    assert manifest["experiment_completed"] is True
    purchases = artifact_rows(export_dir, "purchases")
    receipts = [row for row in purchases if row["fact_kind"] == "receipt"]
    assert len(receipts) == 1 and "store_id" not in receipts[0] and "quality" not in receipts[0]
    verified = artifact_rows(export_dir, "verified_experiences")
    rec_facts = [row for row in verified if row["kind"] == "received"]
    assert len(rec_facts) == 1 and "store_id" not in rec_facts[0] and "quality" not in rec_facts[0]
    assert all(row.get("data", {}).get("title") not in {ORDER_SHIPPED, ORDER_DELIVERED}
               for row in ev_rows if row.get("data", {}).get("kind") == "order_notification")

    # 篡改 1：给 instant 的 goods_received 事件强加 quality 与 store_id 必须被拒绝。
    bad_quality_dir = tmp_path / "tamper-quality"
    e1 = engine_factory(environment_root=root, mutate=_start_at("家", hours=1))
    _, b1 = act(e1, "export-t1", purchase("数字门票"))
    finish_activity(e1, b1, "done-t1")
    m1, ev1, _ = export_completed(e1, bad_quality_dir)
    for row in ev1:
        if row.get("data", {}).get("kind") == "goods_received":
            row["data"]["quality"] = "好"
            row["data"]["store_id"] = "cafe"
    reproject(bad_quality_dir, m1, ev1)
    with pytest.raises(IntegrityError, match="Goods receipt store or quality disagrees"):
        validate_export(bad_quality_dir)

    # 篡改 2：为 instant 订单伪造一条 ORDER_SHIPPED 通知必须被拒绝。
    bad_notice_dir = tmp_path / "tamper-shipped"
    e2 = engine_factory(environment_root=root, mutate=_start_at("家", hours=1))
    _, b2 = act(e2, "export-t2", purchase("数字门票"))
    finish_activity(e2, b2, "done-t2")
    m2, ev2, _ = export_completed(e2, bad_notice_dir)
    placed_idx = next(idx for idx, row in enumerate(ev2) if row.get("data", {}).get("title") == ORDER_PLACED)
    placed = ev2[placed_idx]
    bogus = {**placed, "event_id": "bogus-shipped",
             "data": {**placed["data"], "title": ORDER_SHIPPED, "summary": "数字门票已发货"}}
    ev2.insert(placed_idx + 1, bogus)
    for seq, row in enumerate(ev2, start=1):
        row["event_seq"] = seq
    reproject(bad_notice_dir, m2, ev2)
    with pytest.raises(IntegrityError, match="Order notification count disagrees"):
        validate_export(bad_notice_dir)
