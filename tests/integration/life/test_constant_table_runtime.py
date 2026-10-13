"""配送费、备餐时间、快递费、零食与食品热量、放松缓解只在常量表里写一次：换掉表里的值，真实报价、运费与收货全部跟着变。"""

from __future__ import annotations

import sys
from types import MappingProxyType

import pytest

from tests.life.conftest import do_at, purchase, target_actions
from tests.life.environment_inputs import meal_environment
from tests.life.environment_runtime import act, events, finish_activity, reach, until_ended
from worldmock.exchange import constants
from worldmock.life.purchase_offers import quote_goods

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
    """在家同一次购买牛肉面和薯片（同一家店的一个外卖配送组），返回报价、订单、付款与下单前工具层「牛肉面」商品行的
    note（人物下单前读到同一份报价的地方）。"""
    context = engine.context
    quote = quote_goods(context.inputs, context.state, 1, "beef-noodles", None)
    started = context.state.current_time
    observed, ordered = act(engine, label, purchase("牛肉面", "薯片"))
    assert ordered.status == "in_progress", ordered.error_code
    note = target_actions(observed, "牛肉面")["purchase"]["note"]
    orders = {order.goods_id: order for order in context.state.deliveries.values()}
    assert len({order.group_key for order in orders.values()}) == 1
    finish_activity(engine, ordered, label)
    return {"quote": quote, "note": note, "orders": orders, "started": started,
            "payments": business(engine, "purchase_payment")}


def received(engine, *baskets: tuple[str, ...]) -> dict[str, dict]:
    """到店当场逐次买下每一篮商品并走完真实收货，返回按品类取的收货证据。"""
    for index, names in enumerate(baskets):
        _, bought = act(engine, f"buy-{index}", purchase(*names))
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
    assert "运费5 元" in order["note"]
    assert {key: (item.delivery_fee_fen, item.total_price_fen, item.prep_seconds) for key, item in orders.items()} == {
        "beef-noodles": (500, 3300, 600), "chips": (0, 800, 600)}
    assert [row["amount_fen"] for row in order["payments"]] == [2800 + 800 + 500]
    assert quote_goods(engine.context.inputs, engine.context.state, 1, "online-notebook", None).delivery_fee_fen == 0
    charged = engine_factory(mutate=courier_charged)
    assert quote_goods(charged.context.inputs, charged.context.state, 1, "online-notebook", None).delivery_fee_fen == 500


def test_delivery_fee_follows_the_constant_table(engine_factory, monkeypatch):
    """把 DELIVERY_FEE_FEN 换成 700：外卖报价的运费、订单总价、配送组运费都是 700，快递运费只可能是 0 或 700。"""
    engine, charged = engine_factory(), engine_factory(mutate=courier_charged)
    replace_constant(monkeypatch, "DELIVERY_FEE_FEN", 700)
    order = delivered_order(engine, "order")
    quote, orders = order["quote"], order["orders"]
    assert (quote.store_id, quote.delivery_fee_fen, quote.prep_seconds) == ("noodle-1", 700, 600)
    assert "运费7 元" in order["note"]
    assert {key: (item.delivery_fee_fen, item.total_price_fen) for key, item in orders.items()} == {
        "beef-noodles": (700, 3500), "chips": (0, 800)}
    assert [row["amount_fen"] for row in order["payments"]] == [2800 + 800 + 700]
    assert engine.context.state.accounts["agent:1"] == 200000 - 4300
    assert quote_goods(engine.context.inputs, engine.context.state, 1, "online-notebook", None).delivery_fee_fen == 0
    assert quote_goods(charged.context.inputs, charged.context.state, 1, "online-notebook", None).delivery_fee_fen == 700


def test_delivery_preparation_time_follows_the_constant_table(engine_factory, monkeypatch):
    """把 DELIVERY_PREP_SECONDS 换成 900：外卖报价的备餐秒数、预计送达时刻和订单都跟着变，运费不变。"""
    engine = engine_factory()
    replace_constant(monkeypatch, "DELIVERY_PREP_SECONDS", 900)
    order = delivered_order(engine, "order")
    quote, orders = order["quote"], order["orders"]
    assert (quote.delivery_fee_fen, quote.prep_seconds) == (500, 900)
    assert quote.expected_at == order["started"] + 900 + RIDE_SECONDS
    assert "预计01月05日 08:23到达" in order["note"]
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
    assert (rows["meal"]["applied"], rows["snack"]["applied"]) == (540.0, 180.0)
    assert rows["drink"]["applied"] == 0.1
    assert [flow["amount"] for flow in rows["drink"]["resource_flows"]] == [0.1]


def test_food_receipt_applied_is_the_registered_flow_amount(engine_factory):
    """初始能量 987.65 的人物在家买一份食品外卖：收货的 applied 恰是 540.0，等于同一事件登记的分录数额之和，不是补能前后
    两个浮点数相减。两数相减偏不偏随送达时的能量而定，所以换几次决策身份各走一遍，其中至少一次两数相减不等于 540。"""
    def low_energy(persona, config):
        persona["body"]["initial_energy_kcal"] = 987.65

    subtracted = []
    for label in ("noodles", "order", "pair"):
        engine = engine_factory(mutate=low_energy)
        _, ordered = act(engine, label, purchase("牛肉面"))
        assert ordered.status == "in_progress", ordered.error_code
        [order] = engine.context.state.deliveries.values()
        reach(engine, order.expected_at)
        [row] = business(engine, "goods_received")
        assert row["applied"] == 540.0
        assert row["applied"] == sum(flow["amount"] for flow in row["resource_flows"])
        [settled] = [item for item in business(engine, "body_settlement")
                     if item["interval_end"] == order.expected_at]
        energy = settled["body_settlement"]["after"]["energy_kcal"]
        subtracted.append((energy + 540.0) - energy)
    assert any(value != 540.0 for value in subtracted)


def test_food_receipt_at_the_energy_cap_applies_nothing_and_registers_no_flow(engine_factory):
    """初始能量 2400 的人物到店一次买饭团和薯片：同一秒里后结算的那一件实得为 0，applied 是 0.0 且没有分录。"""
    engine = engine_factory(mutate=at_store(2400.0))
    received(engine, ("饭团", "薯片"))
    first, later = business(engine, "goods_received")
    assert first["applied"] > 0 and first["applied"] == sum(flow["amount"] for flow in first["resource_flows"])
    assert (later["applied"], later["resource_flows"]) == (0.0, [])
    assert type(later["applied"]) is float


def test_goods_energy_follows_the_constant_table(engine_factory, monkeypatch):
    """把 GOODS_ENERGY_KCAL 换成 250 与 650：真实收货证据的 provided 是 250 与 650，实得能量跟着变。"""
    engine = engine_factory(mutate=at_store(1000.0))
    replace_constant(monkeypatch, "GOODS_ENERGY_KCAL", MappingProxyType({"snack": 250, "meal": 650}))
    rows = received(engine, ("饭团", "薯片"))
    assert (rows["meal"]["provided"], rows["snack"]["provided"]) == (650, 250)
    assert (rows["meal"]["applied"], rows["snack"]["applied"]) == (pytest.approx(585), pytest.approx(225))


def test_location_meal_energy_follows_the_constant_table(engine_factory, monkeypatch, tmp_path):
    """把 GOODS_ENERGY_KCAL 的 meal 换成 650：在地点做完供一餐的「测试休息」，完成效果证据的 provided 是 650，实得是
    650 的九成。"""
    def hungry(persona, config):
        persona["body"]["initial_energy_kcal"] = 1000.0

    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=hungry)
    replace_constant(monkeypatch, "GOODS_ENERGY_KCAL", MappingProxyType({"snack": 250, "meal": 650}))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    [done] = [row for row in events(engine, "activity_progress") if row.data.get("content", {}).get("completed")]
    food = done.data["content"]["completion_effects"]["food"]
    assert (food["provided"], food["applied"]) == (650, pytest.approx(585))


def test_goods_relaxation_relief_follows_the_constant_table(engine_factory, monkeypatch):
    """把 GOODS_RELAXATION_RELIEF 换成 0.2：饮料收货证据的 provided 是 0.2，放松需要按它减少。"""
    engine = engine_factory(mutate=at_store())
    replace_constant(monkeypatch, "GOODS_RELAXATION_RELIEF", 0.2)
    rows = received(engine, ("柠檬茶",))
    assert (rows["drink"]["provided"], rows["drink"]["applied"]) == (0.2, 0.2)
    assert [(flow["resource"], flow["direction"], flow["amount"]) for flow in rows["drink"]["resource_flows"]] == [
        ("relaxation_need", "drain", 0.2)]
