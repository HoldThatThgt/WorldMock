"""@brief 从登记商品菜单原子付款，到店受理与配送到期共用收货结算。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.catalog.models import Goods
from worldmock.errors import BusinessRuleError
from worldmock.exchange.constants import DELIVERY_FEE_FEN, DELIVERY_PREP_SECONDS
from worldmock.exchange.operators import delivery_seconds, evaluate_purchase
from worldmock.exchange.types import AppPurpose
from worldmock.footprints.models import EventDraft
from worldmock.life.admission import present_location
from worldmock.life.guidance import guided, default_reason
from worldmock.life.models import (
    DeliveryState, FulfillmentKind, PurchaseOrderRequest, ScheduledEvent, TravelMode, WorldState,
)
from worldmock.life.receipt import settle_receipt
from worldmock.life.recipients import recipient_location
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.travel import Trip, plan_trip
from worldmock.scheduling.events import ScheduledKind


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """@brief 一次整单付款后的世界、订单、配送预排与收货人证据。"""

    state: WorldState
    orders: tuple[DeliveryState, ...]
    scheduled: tuple[ScheduledEvent, ...]
    total_price_fen: int
    receipts: tuple[EventDraft, ...]


@dataclass(frozen=True, slots=True)
class _OrderRow:
    """@brief 已核对同店菜单的一件商品与其收货人。"""

    store_id: str
    goods: Goods
    quantity: int
    recipient_id: str
    request_message_id: str | None


@dataclass(frozen=True, slots=True)
class _Fulfillment:
    """@brief 整单创建时间、固定配送时间、图路径与购物应用。"""

    created_at: int
    expected_at: int
    trip: Trip | None
    app_id: str | None


def purchase_order(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                   request: PurchaseOrderRequest) -> OrderOutcome:
    """@brief 全量校验后每单只付款一次，到店同次收货，失败不改输入世界。"""
    rows, fulfillment, total = validate_purchase_order(state, semantics, agent_id, request)
    orders = _build_orders(state, rows, request, fulfillment, agent_id)
    agent = state.agents[agent_id]
    changed = settle_money_transfer(state, agent.balance_account_id, f"merchant:{rows[0].store_id}",
                                    total, "purchase:order")
    events = _delivery_events(state, orders)
    deliveries = {**changed.deliveries, **{order.order_id: order for order in orders}}
    pending = sorted([*changed.pending_events, *events],
                     key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    changed = changed.model_copy(update={"deliveries": deliveries, "pending_events": pending}, deep=True)
    receipts = []
    if request.fulfillment == FulfillmentKind.PICKUP:
        for order in orders:
            outcome = settle_receipt(changed, semantics, order.order_id)
            changed = outcome.state
            receipts.extend(outcome.receipts)
        orders = tuple(changed.deliveries[order.order_id] for order in orders)
    return OrderOutcome(changed, orders, events, total, tuple(receipts))


def validate_purchase_order(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                            request: PurchaseOrderRequest) -> tuple[tuple[_OrderRow, ...], _Fulfillment, int]:
    """@brief 核对商品、店铺、购物支付绑定、收货地点与整单余额。"""
    rows = _order_rows(semantics, request)
    fulfillment = _order_fulfillment(state, semantics, agent_id, request)
    _validate_recipients(state, semantics, agent_id, request)
    total = sum(evaluate_purchase(row.goods.price_fen, row.quantity) for row in rows)
    total += DELIVERY_FEE_FEN if request.fulfillment == "delivery" else 0
    if state.accounts[state.agents[agent_id].balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    return rows, fulfillment, total


def _order_rows(semantics: ScenarioSemantics, request: PurchaseOrderRequest) -> tuple[_OrderRow, ...]:
    """@brief 一至两行不重复同店同商品同收货人，整单只能来自同店。"""
    if not 1 <= len(request.items) <= 2:
        raise BusinessRuleError("ORDER_ITEM_COUNT_OUT_OF_RANGE")
    keys = {(item.store_id, item.goods_id, item.recipient_id) for item in request.items}
    if len(keys) != len(request.items):
        raise BusinessRuleError("ORDER_DUPLICATE_ITEM")
    if len({item.store_id for item in request.items}) != 1:
        raise BusinessRuleError("ORDER_LOCATION_MISMATCH")
    catalog = semantics.catalog
    rows = []
    for item in request.items:
        if item.goods_id not in catalog.goods_by_store.get(item.store_id, ()):
            raise BusinessRuleError("SUPPLY_NOT_FOUND")
        rows.append(_OrderRow(item.store_id, catalog.goods[item.goods_id], item.quantity,
                              item.recipient_id, item.request_message_id))
    return tuple(rows)


def _order_fulfillment(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                       request: PurchaseOrderRequest) -> _Fulfillment:
    """@brief 店内即时收货，配送必须明确选择绑定的购物与支付应用。"""
    store_id = request.items[0].store_id
    if request.destination_id not in {item.location_id for item in semantics.scenario.locations}:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    _payment_binding(semantics, request)
    if request.fulfillment == FulfillmentKind.PICKUP:
        if request.app_id is not None:
            raise BusinessRuleError("APP_NOT_BOUND")
        if present_location(state, agent_id) != store_id or request.destination_id != store_id:
            raise guided("ACTIVITY_PRECONDITION_UNMET",
                         f"Purchase requires presence at {semantics.catalog.locations[store_id].display_name}.",
                         f"Go to {semantics.catalog.locations[store_id].display_name}.", ("required_location_id",))
        return _Fulfillment(state.current_time, state.current_time, None, None)
    apps = semantics.catalog.apps_by_store[store_id]
    if request.app_id not in apps:
        step = f"Order through {semantics.catalog.apps[min(apps)].name}." if apps else "Buy them in a store."
        raise guided("APP_NOT_BOUND", default_reason("APP_NOT_BOUND"), step)
    plan = plan_delivery(semantics, store_id, request.destination_id, state.current_time)
    return _Fulfillment(plan.created_at, plan.expected_at, plan.trip, request.app_id)


def _payment_binding(semantics: ScenarioSemantics, request: PurchaseOrderRequest) -> None:
    """@brief 在线支付必须绑定购物应用，到店付款可明确不使用应用。"""
    apps = semantics.catalog.apps
    shopping = apps.get(request.app_id)
    if request.fulfillment == "delivery":
        if shopping is None or AppPurpose.SHOPPING not in shopping.purposes:
            raise BusinessRuleError("APP_NOT_OFFERED")
        if request.payment_app_id is None:
            raise BusinessRuleError("PAYMENT_APP_REQUIRED")
        if request.payment_app_id not in shopping.payment_app_ids:
            raise BusinessRuleError("PAYMENT_APP_NOT_BOUND")
    elif request.payment_app_id is not None:
        payment = apps.get(request.payment_app_id)
        if payment is None or AppPurpose.PAYMENT not in payment.purposes:
            raise BusinessRuleError("PAYMENT_APP_NOT_BOUND")


def _validate_recipients(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                         request: PurchaseOrderRequest) -> None:
    """@brief 到店收货人与店铺同处，配送收货人必须具有实际收货地。"""
    for item in request.items:
        place = recipient_location(state, semantics.scenario, agent_id, item.recipient_id)
        if place is None and item.recipient_id == f"agent:{agent_id}":
            raise guided("ACTIVITY_PRECONDITION_UNMET", "Place the order before departing or after arriving.",
                         "Wait until you arrive, then place the order.", ("delivery_destination",))
        if place is None or place != request.destination_id:
            from worldmock.life.order_routing import undeliverable

            raise undeliverable(state, semantics.scenario, agent_id, item.recipient_id)


def plan_delivery(semantics: ScenarioSemantics, store_id: str,
                  destination_id: str, now: int) -> _Fulfillment:
    """@brief 全图配送按固定备餐秒数与五米每秒计算，路径来自唯一登记图。"""
    trip = plan_trip(semantics.catalog.graph, store_id, destination_id, TravelMode.DRIVING)
    return _Fulfillment(now, now + delivery_seconds(trip.distance_meters), trip, None)


def delivery_expected_at(semantics: ScenarioSemantics, store_id: str,
                         destination_id: str, now: int) -> int:
    """@brief 工具报价与订单使用同一固定配送时间。"""
    return plan_delivery(semantics, store_id, destination_id, now).expected_at


def _build_orders(state: WorldState, rows: tuple[_OrderRow, ...], request: PurchaseOrderRequest,
                  fulfillment: _Fulfillment, agent_id: int) -> tuple[DeliveryState, ...]:
    """@brief 每行保存权威商品与支付选择，所有订单先建立为待收货。"""
    orders = []
    for index, row in enumerate(rows, start=1):
        trip = fulfillment.trip
        fee = DELIVERY_FEE_FEN if trip is not None and index == 1 else 0
        orders.append(DeliveryState(
            order_id=f"order-{agent_id}-{state.current_time}-{len(state.deliveries) + index}",
            agent_id=agent_id, goods_id=row.goods.goods_id, category=row.goods.category,
            store_id=row.store_id, app_id=fulfillment.app_id, payment_app_id=request.payment_app_id,
            seller_account_id=f"merchant:{row.store_id}", quantity=row.quantity, unit_price_fen=row.goods.price_fen,
            delivery_fee_fen=fee, total_price_fen=evaluate_purchase(row.goods.price_fen, row.quantity) + fee,
            destination_id=request.destination_id, fulfillment=request.fulfillment, recipient_id=row.recipient_id,
            request_message_id=row.request_message_id, created_at=fulfillment.created_at,
            expected_at=fulfillment.expected_at, received_at=None, status="pending",
            path=trip.path if trip is not None else (row.store_id,), trip_id=trip.trip_id if trip is not None else None,
            distance_meters=trip.distance_meters if trip is not None else 0,
            prep_seconds=DELIVERY_PREP_SECONDS if trip is not None else 0))
    return tuple(orders)


def _delivery_events(state: WorldState, orders: tuple[DeliveryState, ...]) -> tuple[ScheduledEvent, ...]:
    """@brief 配送整单只产生一个到期事件，到店收货没有预排事件。"""
    if orders[0].fulfillment == FulfillmentKind.PICKUP:
        return ()
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    return (ScheduledEvent(scheduled_id=f"delivery-{orders[0].order_id}", kind=ScheduledKind.DELIVERY_ARRIVAL,
        due_at=orders[0].expected_at, sequence=sequence, agent_id=orders[0].agent_id,
        data={"order_ids": [order.order_id for order in orders]}),)
