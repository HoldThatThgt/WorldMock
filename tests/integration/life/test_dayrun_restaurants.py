"""@brief 真实到店、统一商品目录、固定价格和配送收货的连续足迹。"""

import json

import pytest

from tests.life.conftest import context
from tests.integration.life.test_acs_amended_runtime import all_events, perform, pump
from tests.integration.life.test_acs_runtime import request
from tests.life.work_runtime import trip_to
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.life.readable import clock_phrase

STORE_ID, GOODS_ID = "noodle-2", "beef-noodles"


def test_store_arrival_exposes_fixed_menu_and_real_receipt(engine_factory):
    """@brief 到店购买立即收到，不需要或产生库存及进食活动。"""
    engine = engine_factory()
    trip_to(engine, STORE_ID, "visit-store")
    observed = engine.observe(context(engine, "arrived-menu"))
    catalog = engine.context.semantics.catalog
    names = {catalog.goods[item].name for item in catalog.goods_by_store[STORE_ID]}
    assert names <= {name for row in observed["tool_layer"]["callable_index"]["goods"] for name in row["examples"]}
    bought = perform(engine, "buy-meal", request("牛肉面", None, "购买"))
    assert bought.error_code is None, bought.data
    [order] = bought.data["orders"]
    assert order["store_id"] == STORE_ID and order["fulfillment"] == "pickup"
    assert order["received_at"] == engine.context.state.current_time
    [receipt] = [event for event in all_events(engine) if event.data.get("kind") == "goods_received"]
    assert receipt.data["order_id"] == order["order_id"] and receipt.agent_id == 1
    actual = next(event for event in all_events(engine) if event.event_type == "action_result"
                  and event.action_id == bought.action_id)
    assert receipt.caused_by == [actual.event_id]
    assert "inventory" not in engine.context.state.agents[1].model_dump()
    assert not [item for item in engine.context.state.activities.values() if item.kind.value == "consume"]


def test_same_kind_stores_reject_different_goods_menus(tmp_path):
    """@brief 同类店铺仍必须完整绑定同一固定菜单，重新签名不能放行不同菜单。"""
    from tests.life.scenario_fixtures import read_scenario, write_scenario
    from worldmock.life.world_setup import load_runtime_scenario

    path = write_scenario(read_scenario("single-person.json"), tmp_path / "environment")
    goods = path.parent.parent / "catalog/goods.json"
    payload = json.loads(goods.read_bytes())
    next(row for row in payload["goods"] if row["goods_id"] == GOODS_ID)["store_ids"] = ["noodle-1"]
    goods.write_text(json.dumps(payload), encoding="utf-8")
    refresh_manifest_identity(goods.parent)
    with pytest.raises(RegistrationError, match="GOODS_BINDING_KIND_INCONSISTENT"):
        load_runtime_scenario(path)


def test_goods_paths_are_discoverable_once_without_holdings(engine_factory):
    """@brief 商品只以统一名称呈现，购买可以现场或明确应用，没有持有物入口。"""
    engine = engine_factory()
    trip_to(engine, STORE_ID, "discover-store")
    observed = engine.observe(context(engine, "goods-targets"))
    targets = observed["tool_layer"]["targets"]
    [meal] = [row for row in targets if row["name"] == "牛肉面"]
    assert meal["kind"] == "product"
    local = next(row for row in meal["actions"] if row["app"] is None and row["way"] == "购买")
    assert local["available"] is True and local["quantity"] == "1 份"
    assert all(row["kind"] != "holding" for row in targets)
    assert not any("持有物" in row["name"] or "出售的" in row["name"] for row in targets)


def test_delivery_eta_is_readable_and_matches_actual_receipt(engine_factory):
    """@brief 展示的到达时刻与实际选择的门店、付款订单和收货事件一致。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "delivery-preview"))
    [meal] = [row for row in observed["tool_layer"]["targets"] if row["name"] == "牛肉面"]
    listed = next(row for row in meal["actions"] if row["app"] == "美团")
    bought = perform(engine, "deliver-home", request("牛肉面", "美团", "购买", payment="微信"))
    assert bought.error_code is None, bought.data
    [order] = bought.data["orders"]
    when = clock_phrase(order["expected_at"], observed["current_time"], engine.context.scenario.timezone)
    assert listed["note"].startswith(f"预计 {when} 送到；")
    assert not [event for event in all_events(engine) if event.data.get("kind") == "goods_received"]
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    [receipt] = [event for event in all_events(engine) if event.data.get("kind") == "goods_received"]
    assert receipt.occurred_at == order["expected_at"] and receipt.data["goods_id"] == GOODS_ID
    assert "inventory" not in engine.context.state.agents[1].model_dump()


def test_goods_without_an_online_channel_rejects_before_payment(engine_factory):
    """@brief 商品与应用组合没有绑定即原子拒绝，不扣款也不产生订单。"""
    engine = engine_factory()
    before = engine.context.state.accounts.copy()
    rejected = perform(engine, "unbound-app", request("牛肉面", "抖音", "购买", payment="微信"))
    assert rejected.error_code is not None
    assert engine.context.state.accounts == before and engine.context.state.deliveries == {}
