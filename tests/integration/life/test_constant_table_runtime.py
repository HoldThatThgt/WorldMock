"""配送费、备餐时间、快递费、零食与食品热量、放松缓解只在常量表里写一次：换掉表里的值，真实报价、运费与收货全部跟着变。"""

from __future__ import annotations

import sys
from types import MappingProxyType

import pytest

from tests.life.conftest import start_activity
from tests.life.environment_runtime import act, events, finish_activity, reach
from worldmock.exchange import constants
from worldmock.life.purchase_offers import app_goods, quote_goods

#: 夹具里家到一心面馆的图上距离对应的配送行程秒数（2,524 米，每秒 5 米，向上取整）。
RIDE_SECONDS = 505


def replace_constant(monkeypatch, name: str, value) -> None:
    """把常量表里的一个值换成另一个数：每个已加载、绑定了这个名字的 worldmock 模块同时替换。

    哪个使用处写回了字面量，它就不跟着变，下面走真实路径的断言随之失败。
    """
    bound = [key for key, module in sys.modules.items()
             if key.startswith("worldmock") and module is not None and name in vars(module)]
    assert constants.__name__ in bound
    for key in bound:
        monkeypatch.setattr(sys.modules[key], name, value)


def at_store(energy_kcal: float | None = None):
    """人物从四季便利开始（到店当场买），必要时调低初始能量让食品不顶到能量上限。"""
    def mutate(persona, config):
        persona["initial_location"] = "四季便利"
        if energy_kcal is not None:
            persona["body"]["initial_energy_kcal"] = energy_kcal
    return mutate


def courier_charged(persona, config):
    """换一个抽中「收运费」的种子：快递运费在 0 与固定配送费之间抽，夹具种子抽到 0。"""
    config["seed"] = 4


def business(engine, kind: str) -> list[dict]:
    """已提交的某一类业务事件正文。"""
    return [row.data for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def delivered_order(engine, label: str) -> dict:
    """在家同一次购买牛肉面和薯片（同一家店的一个外卖配送组），返回报价、订单、付款与平台明细。"""
    context = engine.context
    quote = quote_goods(context.inputs, context.state, 1, "beef-noodles")
    listed = {row["name"]: row for row in app_goods(context.inputs, context.state, 1, "meituan")}
    started = context.state.current_time
    _, ordered = act(engine, label, start_activity("purchase", "牛肉面", "薯片"))
    assert ordered.status == "in_progress", ordered.error_code
    orders = {order.goods_id: order for order in context.state.deliveries.values()}
    assert len({order.group_key for order in orders.values()}) == 1
    finish_activity(engine, ordered, label)
    return {"quote": quote, "listed": listed, "orders": orders, "started": started,
            "payments": business(engine, "purchase_payment")}


def received(engine, *baskets: tuple[str, ...]) -> dict[str, dict]:
    """到店当场逐次买下每一篮商品并走完真实收货，返回按品类取的收货证据。"""
    for index, names in enumerate(baskets):
        _, bought = act(engine, f"buy-{index}", start_activity("purchase", *names))
        assert bought.status == "in_progress", bought.error_code
        finish_activity(engine, bought, f"buy-{index}")
    rows = business(engine, "goods_received")
    assert len(rows) == sum(len(names) for names in baskets)
    return {row["category"]: row for row in rows}


def test_unreplaced_delivery_quote_shipping_and_arrival_are_unchanged(engine_factory):
    """不替换时与修订前逐位相同：外卖运费 500 分、备餐 600 秒，配送组只收一次运费，快递运费是 0 或 500。"""
    engine = engine_factory()
    order = delivered_order(engine, "order")
    quote, orders = order["quote"], order["orders"]
    assert (quote.fulfillment, quote.store_id, quote.delivery_fee_fen, quote.prep_seconds) == (
        "delivery", "noodle-1", 500, 600)
    assert quote.expected_at == order["started"] + 600 + RIDE_SECONDS
    assert order["listed"]["牛肉面"]["delivery_fee"] == "5 元"
    assert {key: (item.delivery_fee_fen, item.total_price_fen, item.prep_seconds) for key, item in orders.items()} == {
        "beef-noodles": (500, 3300, 600), "chips": (0, 800, 600)}
    assert [row["amount_fen"] for row in order["payments"]] == [2800 + 800 + 500]
    assert quote_goods(engine.context.inputs, engine.context.state, 1, "online-notebook").delivery_fee_fen == 0
    charged = engine_factory(mutate=courier_charged)
    assert quote_goods(charged.context.inputs, charged.context.state, 1, "online-notebook").delivery_fee_fen == 500


def test_delivery_fee_follows_the_constant_table(engine_factory, monkeypatch):
    """把 DELIVERY_FEE_FEN 换成 700：外卖报价的运费、订单总价、配送组运费都是 700，快递运费只可能是 0 或 700。"""
    engine, charged = engine_factory(), engine_factory(mutate=courier_charged)
    replace_constant(monkeypatch, "DELIVERY_FEE_FEN", 700)
    order = delivered_order(engine, "order")
    quote, orders = order["quote"], order["orders"]
    assert (quote.store_id, quote.delivery_fee_fen, quote.prep_seconds) == ("noodle-1", 700, 600)
    assert order["listed"]["牛肉面"]["delivery_fee"] == "7 元"
    assert {key: (item.delivery_fee_fen, item.total_price_fen) for key, item in orders.items()} == {
        "beef-noodles": (700, 3500), "chips": (0, 800)}
    assert [row["amount_fen"] for row in order["payments"]] == [2800 + 800 + 700]
    assert engine.context.state.accounts["agent:1"] == 200000 - 4300
    assert quote_goods(engine.context.inputs, engine.context.state, 1, "online-notebook").delivery_fee_fen == 0
    assert quote_goods(charged.context.inputs, charged.context.state, 1, "online-notebook").delivery_fee_fen == 700


def test_delivery_preparation_time_follows_the_constant_table(engine_factory, monkeypatch):
    """把 DELIVERY_PREP_SECONDS 换成 900：外卖报价的备餐秒数、预计送达时刻和订单都跟着变，运费不变。"""
    engine = engine_factory()
    replace_constant(monkeypatch, "DELIVERY_PREP_SECONDS", 900)
    order = delivered_order(engine, "order")
    quote, orders = order["quote"], order["orders"]
    assert (quote.delivery_fee_fen, quote.prep_seconds) == (500, 900)
    assert quote.expected_at == order["started"] + 900 + RIDE_SECONDS
    assert order["listed"]["牛肉面"]["expected_at"] == "2026-01-05T08:23:25+08:00"
    assert {item.prep_seconds for item in orders.values()} == {900}
    assert {item.expected_at for item in orders.values()} == {order["started"] + 900 + RIDE_SECONDS}
    reach(engine, quote.expected_at)
    assert {row["order_id"] for row in business(engine, "goods_received")} == {item.order_id for item in orders.values()}


def test_unreplaced_receipts_keep_their_energy_and_relief(engine_factory):
    """不替换时收货证据与修订前逐位相同：食品 600、零食 200 千卡（欠觉不超过 8 小时实得九成），饮料缓解 0.1。"""
    engine = engine_factory(mutate=at_store(1000.0))
    rows = received(engine, ("饭团", "薯片"), ("柠檬茶",))
    assert {key: (row["provided"], row["efficiency"]) for key, row in rows.items()} == {
        "meal": (600, 0.9), "snack": (200, 0.9), "drink": (0.1, None)}
    assert (rows["meal"]["applied"], rows["snack"]["applied"]) == (pytest.approx(540), pytest.approx(180))
    assert rows["drink"]["applied"] == 0.1
    assert [flow["amount"] for flow in rows["drink"]["resource_flows"]] == [0.1]


def test_goods_energy_follows_the_constant_table(engine_factory, monkeypatch):
    """把 GOODS_ENERGY_KCAL 换成 250 与 650：真实收货证据的 provided 是 250 与 650，实得能量跟着变。"""
    engine = engine_factory(mutate=at_store(1000.0))
    replace_constant(monkeypatch, "GOODS_ENERGY_KCAL", MappingProxyType({"snack": 250, "meal": 650}))
    rows = received(engine, ("饭团", "薯片"))
    assert (rows["meal"]["provided"], rows["snack"]["provided"]) == (650, 250)
    assert (rows["meal"]["applied"], rows["snack"]["applied"]) == (pytest.approx(585), pytest.approx(225))


def test_goods_relaxation_relief_follows_the_constant_table(engine_factory, monkeypatch):
    """把 GOODS_RELAXATION_RELIEF 换成 0.2：饮料收货证据的 provided 是 0.2，放松需要按它减少。"""
    engine = engine_factory(mutate=at_store())
    replace_constant(monkeypatch, "GOODS_RELAXATION_RELIEF", 0.2)
    rows = received(engine, ("柠檬茶",))
    assert (rows["drink"]["provided"], rows["drink"]["applied"]) == (0.2, 0.2)
    assert [(flow["resource"], flow["direction"], flow["amount"]) for flow in rows["drink"]["resource_flows"]] == [
        ("relaxation_need", "drain", 0.2)]
