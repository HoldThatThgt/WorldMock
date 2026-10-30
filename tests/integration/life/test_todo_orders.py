"""待办层的在途订单（docs/design/06-agent.md「待办层」）：本人已付款、状态为 pending 或 ready_for_pickup 的商品履约
记录，同商品、同购买时刻、同状态、同目的地、同预计到达的合成一行，至多六行、多出的写一行「另有 N 笔在途」；零点计划的
「在途订单」与待办层用同一个选择函数。经真实请求、引擎与 SQLite，读观察里的待办层。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.integration.life.test_grocery_cooking import _six_days, _until_free
from tests.life.conftest import do_at, purchase, travel
from tests.life.environment_runtime import act, finish_activity, observe_only, reach, until_ended
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.foundation.time.readable import iso_time
from worldmock.simulation_engine.observation.layers.progress_layer import order_label
from worldmock.simulation_engine.observation.layers.todo_layer import open_orders, todo_layer
from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import plan_inputs
from worldmock.state.world.kinds import FulfillmentKind


def _orders(engine, label: str) -> list[dict]:
    """人物此刻一次观察里待办层的 orders。"""
    return observe_only(engine, label)["todo_layer"]["orders"]


def _label(engine, order) -> str:
    """这条履约记录在此刻的订单标签（与进度层送达记录同一写法）。"""
    names = {key: item.name for key, item in engine.context.catalog.goods.items()}
    return order_label(order.model_dump(), names, (engine.context.config.timezone, engine.context.state.current_time))


def _row(engine, order, place: str) -> dict:
    """一条在途订单的待办层行。"""
    return {"order": _label(engine, order), "status": order.status, "destination": place,
            "expected_at": iso_time(order.expected_at, engine.context.config.timezone)}


def test_a_paid_delivery_is_an_order_in_transit_until_it_arrives(engine_factory):
    """经 app 买一件外卖：购买进行中（还没付款）时 orders 为空；付款完成后恰一行，四个键依次是 order、status、destination、
    expected_at，order 是订单标签、status 是 pending、expected_at 是冻结的预计到达；送达之后 orders 为空。"""
    engine = engine_factory()
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    (order,) = engine.context.state.deliveries.values()
    assert order.paid_at is None and _orders(engine, "quoted") == []
    until_ended(engine, bought.activity_id)
    paid = engine.context.state.deliveries[order.order_id]
    (row,) = _orders(engine, "paid")
    assert list(row) == ["order", "status", "destination", "expected_at"]
    assert row == _row(engine, paid, "家") and row["status"] == "pending"
    reach(engine, paid.expected_at)
    assert engine.context.state.deliveries[order.order_id].status == "delivered"
    assert _orders(engine, "delivered") == []


def test_a_parcel_waiting_at_the_station_is_ready_for_pickup_until_collected(engine_factory):
    """买一件快递：在途时 status 是 pending、destination 是快递驿站；到站后同一笔订单的行 status 变成 ready_for_pickup
    （隔了一天，订单标签按此刻写出购买日期）；在驿站取件之后 orders 为空。"""
    engine = engine_factory(mutate=_six_days)
    _, bought = act(engine, "notebook", purchase("平台笔记本"))
    finish_activity(engine, bought, "notebook")
    (order,) = engine.context.state.deliveries.values()
    (transit,) = _orders(engine, "transit")
    assert (transit["status"], transit["destination"]) == ("pending", "快递驿站")
    reach(engine, order.expected_at)
    _until_free(engine)
    (waiting,) = _orders(engine, "waiting")
    arrived = engine.context.state.deliveries[order.order_id]
    assert waiting == {**transit, "order": _label(engine, arrived), "status": "ready_for_pickup"}
    _, trip = act(engine, "to-station", travel("快递驿站", "驾车"))
    finish_activity(engine, trip, "at-station")
    _, collected = act(engine, "collect", do_at("快递驿站", "驿站取件"))
    assert collected.status == "in_progress", collected.error_code
    assert engine.context.state.deliveries[order.order_id].status == "delivered"
    assert _orders(engine, "collected") == []


def test_two_portions_of_one_goods_bought_together_are_one_row(engine_factory):
    """一次购买写同一商品两次：两条履约记录合成一行，order 为「牛肉面 2 份（HH:MM 购买）」。"""
    engine = engine_factory()
    _, bought = act(engine, "two", purchase("牛肉面", "牛肉面"))
    until_ended(engine, bought.activity_id)
    assert len(engine.context.state.deliveries) == 2
    (row,) = _orders(engine, "paid")
    assert row["order"] == "牛肉面 2 份（08:00 购买）"


def _paid_order(engine):
    """一笔付了款的外卖履约记录：买一件牛肉面并做完购买。"""
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    until_ended(engine, bought.activity_id)
    (order,) = engine.context.state.deliveries.values()
    return order


def _staged(engine, deliveries) -> None:
    """把世界状态里的履约记录换成给定的这些（只给只读投影用，不提交）。"""
    state = engine.context.state.model_copy(deep=True)
    state.deliveries = {order.order_id: order for order in deliveries}
    engine.context.state = state


def _todo(engine) -> dict:
    """此刻的待办层（直接由层构造器给出）。"""
    context = engine.observations._layer_context(engine.context.state.agents[1], [])
    return todo_layer(context, engine.context.store)


def _groups(order, count: int) -> list:
    """count 组互不合并的已付款在途记录：商品相同，购买时刻各不相同，预计到达按组倒序（排列要按预计到达）。"""
    return [order.model_copy(update={"order_id": f"order-{index}", "created_at": order.created_at - index * 60,
                                     "expected_at": order.expected_at - index}) for index in range(count)]


def test_orders_list_six_rows_and_one_summary_row(engine_factory):
    """世界状态里放入 8 组、6 组、7 组互不合并的已付款在途记录：8 组时 7 项、末项恰为「另有 2 笔在途」；6 组时 6 项、
    没有合成行；7 组时末项为「另有 1 笔在途」；行按预计到达与订单身份升序。"""
    engine = engine_factory()
    paid = _paid_order(engine)
    for count, summary in ((8, [{"order": "另有 2 笔在途"}]), (6, []), (7, [{"order": "另有 1 笔在途"}])):
        groups = _groups(paid, count)
        _staged(engine, groups)
        rows = _todo(engine)["orders"]
        ordered = sorted(groups, key=lambda item: (item.expected_at, item.order_id))
        assert rows == [*(_row(engine, item, "家") for item in ordered[:6]), *summary]


def _three_records(engine) -> tuple:
    """一笔未付款的报价、一笔在途外卖、一件已到驿站的快递。"""
    paid = _paid_order(engine)
    quote = paid.model_copy(update={"order_id": "order-quote", "paid_at": None, "goods_id": "tissue"})
    parcel = paid.model_copy(update={"order_id": "order-parcel", "goods_id": "online-notebook",
                                     "status": "ready_for_pickup", "destination_id": "parcel-station",
                                     "fulfillment": FulfillmentKind("station_pickup"),
                                     "expected_at": paid.expected_at - 600})
    return quote, paid, parcel


def test_the_day_plan_lists_the_same_open_orders_as_the_todo_layer(engine_factory):
    """未付款的报价、在途外卖与已到站的快递：零点计划输入的「在途订单」恰两行，四个键与写法照旧，与 open_orders 逐条
    对应（含 ready_for_pickup 那件，不含报价），「预计送达」写各自冻结的 expected_at。"""
    engine = engine_factory()
    quote, paid, parcel = _three_records(engine)
    _staged(engine, (quote, paid, parcel))
    assert open_orders(engine.context.state, 1) == (parcel, paid)
    rows = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["在途订单"]
    assert [list(row) for row in rows] == [["商品", "数量", "预计送达", "送到"]] * 2
    assert [(row["商品"], row["数量"], row["送到"]) for row in rows] == [("平台笔记本", 1, "快递驿站"), ("牛肉面", 1, "家")]
    zone = ZoneInfo(engine.context.config.timezone)
    frozen = [datetime.fromtimestamp(item.expected_at, zone) for item in (parcel, paid)]
    assert [row["预计送达"] for row in rows] == [f"{local.month}月{local.day}日 {local:%H:%M}" for local in frozen]
    assert [row["order"] for row in _todo(engine)["orders"]] == [_label(engine, parcel), _label(engine, paid)]


def test_todo_layer_with_orders_passes_layer_validation(engine_factory):
    """上面几种情形的待办层都通过层校验；orders 放 8 项、合成行不合「另有 N 笔在途」的写法、订单行多一个键或少一个键时
    层校验报错。"""
    engine = engine_factory()
    quote, paid, parcel = _three_records(engine)
    layers = []
    for deliveries in ((quote,), (quote, paid, parcel), _groups(paid, 8), _groups(paid, 6)):
        _staged(engine, deliveries)
        layers.append(_todo(engine))
    for layer in layers:
        validate_layer(layer)
    row = layers[1]["orders"][0]
    broken = [[*layers[2]["orders"], {"order": "另有 3 笔在途"}], [*layers[3]["orders"], {"order": "另有 0 笔在途"}],
              [*layers[3]["orders"][:5], {"order": "还有 1 笔在途"}], [{**row, "price": "22 元"}],
              [{key: value for key, value in row.items() if key != "destination"}]]
    for orders in broken:
        with pytest.raises(ValueError):
            validate_layer({**layers[1], "orders": orders})
