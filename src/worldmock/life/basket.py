"""@brief 从登记菜单原子购买固定价食品，并按实际份量结算进食。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from worldmock.catalog.models import Food
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.constants import DELIVERY_FEE_FEN, DELIVERY_PREP_SECONDS, FOOD_CONSTANTS
from worldmock.exchange.operators import delivery_seconds, evaluate_food_energy, evaluate_purchase
from worldmock.life.admission import en_route, present_location
from worldmock.life.body import apply_food
from worldmock.life.commerce import add_holding, held_quantity
from worldmock.life.guidance import guided, default_reason
from worldmock.life.models import (
    ActivityState, AgentState, ConsumeRequest, DeliveryState, FulfillmentKind, PurchaseOrderRequest,
    ScheduledEvent, TravelMode, WorldState,
)
from worldmock.life.scenario import BodyConfig
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.travel import Trip, plan_trip
from worldmock.scheduling.events import ScheduledKind


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """@brief 一次整单付款后的状态、食品订单、配送事件和持有变化。"""

    state: WorldState
    orders: tuple[DeliveryState, ...]
    scheduled: tuple[ScheduledEvent, ...]
    total_price_fen: int
    inventory_changes: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class _OrderRow:
    """@brief 已经核对同店菜单的食品、份数与收货人。"""

    store_id: str
    food: Food
    quantity: int
    recipient_id: str
    request_message_id: str | None


@dataclass(frozen=True, slots=True)
class _Fulfillment:
    """@brief 整单创建时间、固定配送时间、图路径与实际来源应用。"""

    created_at: int
    expected_at: int
    trip: Trip | None
    app_id: str | None


def purchase_order(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                   request: PurchaseOrderRequest) -> OrderOutcome:
    """@brief 校验整单后一次转账给店铺；任意失败不改变输入世界。"""
    rows, fulfillment, total = validate_purchase_order(state, semantics, agent_id, request)
    orders = _build_orders(state, rows, request, fulfillment, agent_id)
    agent = state.agents[agent_id]
    changed = settle_money_transfer(state, agent.balance_account_id, f"merchant:{rows[0].store_id}",
                                    total, "purchase:order")
    changes = []
    for order in orders:
        if order.status == "delivered":
            changed = add_holding(changed, order.recipient_id, order.destination_id,
                                  (order.food_id, order.quantity))
            changes.append(inventory_change(order))
    events = _delivery_events(state, orders)
    deliveries = {**changed.deliveries, **{order.order_id: order for order in orders}}
    pending = sorted([*changed.pending_events, *events],
                     key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    changed = changed.model_copy(update={"deliveries": deliveries, "pending_events": pending}, deep=True)
    return OrderOutcome(changed, orders, events, total, tuple(changes))


def validate_purchase_order(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                            request: PurchaseOrderRequest) -> tuple[tuple[_OrderRow, ...], _Fulfillment, int]:
    """@brief 从唯一菜单核对食品、店铺、来源应用、收货地点与整单余额。"""
    rows = _order_rows(semantics, request)
    fulfillment = _order_fulfillment(state, semantics, agent_id, request)
    total = sum(evaluate_purchase(row.food.price_fen, row.quantity) for row in rows)
    total += DELIVERY_FEE_FEN if request.fulfillment == "delivery" else 0
    if state.accounts[state.agents[agent_id].balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    return rows, fulfillment, total


def _order_rows(semantics: ScenarioSemantics, request: PurchaseOrderRequest) -> tuple[_OrderRow, ...]:
    """@brief 拒绝重复店铺食品与跨店订单，不合并重复食品。"""
    if not 1 <= len(request.items) <= 5:
        raise BusinessRuleError("ORDER_ITEM_COUNT_OUT_OF_RANGE")
    if len({(item.store_id, item.food_id) for item in request.items}) != len(request.items):
        raise BusinessRuleError("ORDER_DUPLICATE_ITEM")
    if len({item.store_id for item in request.items}) != 1:
        raise BusinessRuleError("ORDER_LOCATION_MISMATCH")
    catalog = semantics.catalog
    rows = []
    for item in request.items:
        if item.food_id not in catalog.foods_by_store.get(item.store_id, ()):
            raise BusinessRuleError("SUPPLY_NOT_FOUND")
        rows.append(_OrderRow(item.store_id, catalog.foods[item.food_id], item.quantity,
                              item.recipient_id, item.request_message_id))
    return tuple(rows)


def _order_fulfillment(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                       request: PurchaseOrderRequest) -> _Fulfillment:
    """@brief 到店即时交付；外卖只受真实应用绑定与收货地点约束。"""
    store_id = request.items[0].store_id
    if request.destination_id not in {item.location_id for item in semantics.scenario.locations}:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    if request.fulfillment == FulfillmentKind.PICKUP:
        if request.app_id is not None:
            raise BusinessRuleError("SUPPLY_SOURCE_MISMATCH")
        if present_location(state, agent_id) != store_id or request.destination_id != store_id:
            raise BusinessRuleError("PICKUP_LOCATION_REQUIRED")
        return _Fulfillment(state.current_time, state.current_time, None, None)
    if en_route(state, agent_id) and any(item.recipient_id == f"agent:{agent_id}" for item in request.items):
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["delivery_destination"],
                                suggested={"next_step": "Wait until you arrive, then place the order."},
                                public_reason="Place the order before departing or after arriving.")
    apps = semantics.catalog.apps_by_store[store_id]
    if not apps:
        raise guided("FULFILLMENT_NOT_SUPPORTED", default_reason("FULFILLMENT_NOT_SUPPORTED"),
                      f"Go to {semantics.catalog.locations[store_id].display_name}.")
    app_id = request.app_id if request.app_id is not None else min(apps)
    if app_id not in apps:
        raise guided("APP_NOT_BOUND", default_reason("APP_NOT_BOUND"),
                      f"Order through {semantics.catalog.apps[min(apps)].name}.")
    plan = plan_delivery(semantics, store_id, request.destination_id, state.current_time)
    return _Fulfillment(plan.created_at, plan.expected_at, plan.trip, app_id)


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
    """@brief 每个食品行保存权威价格与履约事实，整单配送费只记一次。"""
    orders = []
    for index, row in enumerate(rows, start=1):
        trip = fulfillment.trip
        fee = DELIVERY_FEE_FEN if trip is not None and index == 1 else 0
        orders.append(DeliveryState(
            order_id=f"order-{agent_id}-{state.current_time}-{len(state.deliveries) + index}",
            agent_id=agent_id, food_id=row.food.food_id, store_id=row.store_id, app_id=fulfillment.app_id,
            seller_account_id=f"merchant:{row.store_id}", quantity=row.quantity, unit_price_fen=row.food.price_fen,
            delivery_fee_fen=fee, total_price_fen=evaluate_purchase(row.food.price_fen, row.quantity) + fee,
            destination_id=request.destination_id, fulfillment=request.fulfillment, recipient_id=row.recipient_id,
            request_message_id=row.request_message_id, created_at=fulfillment.created_at,
            expected_at=fulfillment.expected_at, status="delivered" if trip is None else "pending",
            path=trip.path if trip is not None else (row.store_id,), trip_id=trip.trip_id if trip is not None else None,
            distance_meters=trip.distance_meters if trip is not None else 0,
            prep_seconds=DELIVERY_PREP_SECONDS if trip is not None else 0))
    return tuple(orders)


def _delivery_events(state: WorldState, orders: tuple[DeliveryState, ...]) -> tuple[ScheduledEvent, ...]:
    """@brief 一笔整单只有一个到期事件，包含全部食品订单行。"""
    if orders[0].status == "delivered":
        return ()
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    return (ScheduledEvent(scheduled_id=f"delivery-{orders[0].order_id}", kind=ScheduledKind.DELIVERY_ARRIVAL,
        due_at=orders[0].expected_at, sequence=sequence, agent_id=orders[0].agent_id,
        data={"order_ids": [order.order_id for order in orders]}),)


def inventory_change(order: DeliveryState) -> dict:
    """@brief 即时交付与到期交付使用同一食品持有变化形状。"""
    return {"order_id": order.order_id, "store_id": order.store_id, "food_id": order.food_id,
            "location_id": order.destination_id, "quantity": order.quantity}


def change_inventory(agent: AgentState, food_id: str, delta: float) -> dict:
    """@brief 只改变当前地点食品份数，保持九位库存精度并拒绝负持有。"""
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    local = inventory.setdefault(agent.location_id, {})
    local[food_id] = round(local.get(food_id, 0) + delta, 9)
    if local[food_id] < -1e-9:
        raise BusinessRuleError("PRODUCT_NOT_HELD")
    return inventory


def portion_duration(full_seconds: int, portion: float) -> int:
    """@brief 按份量计算时长，保持一分钟下限。"""
    return max(60, round(full_seconds * portion))


def meal_portion(food: Food, use_mode: str, held: float) -> tuple[float, int]:
    """@brief 持有食品本次食用份数与类别固定时长，不再有耐用品使用。"""
    if use_mode != "eat":
        raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
    portion = round(min(1.0, max(0.0, held)), 9)
    return portion, portion_duration(FOOD_CONSTANTS[food.category].duration_seconds, portion)


def open_meal(agent: AgentState, request: ConsumeRequest) -> tuple[AgentState, dict[str, float]]:
    """@brief 把本餐份量从当地持有移入 opened，后续进度不再重复扣库存。"""
    if request.use_mode != "eat":
        raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
    validate_meal_inventory(agent, request)
    opened = {}
    for item in request.items:
        agent = agent.model_copy(update={"inventory": change_inventory(agent, item.product_id, -item.quantity)},
                                 deep=True)
        opened[item.product_id] = round(item.quantity, 9)
    return agent, opened


def meal_duration(foods: Mapping[str, Food], request: ConsumeRequest) -> int:
    """@brief 同餐多项按类别时长折算后取最长时长。"""
    if request.use_mode != "eat":
        raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
    durations = []
    for item in request.items:
        if item.product_id not in foods:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        seconds = FOOD_CONSTANTS[foods[item.product_id].category].duration_seconds
        durations.append(portion_duration(seconds, item.quantity))
    if request.duration_seconds != max(durations):
        raise BusinessRuleError("MEAL_DURATION_MUST_BE_LONGEST")
    return max(durations)


def validate_meal_use(agent: AgentState, foods: Mapping[str, Food], request: ConsumeRequest) -> None:
    """@brief 只准入一至三项实际食品和 eat 方式。"""
    if not 1 <= len(request.items) <= 3:
        raise BusinessRuleError("MEAL_ITEM_COUNT_OUT_OF_RANGE")
    if request.use_mode != "eat":
        raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
    if any(item.product_id not in foods for item in request.items):
        raise BusinessRuleError("PRODUCT_NOT_FOUND")


def validate_meal_inventory(agent: AgentState, request: ConsumeRequest) -> None:
    """@brief 开餐前整组核对当地食品持有，失败不会部分开封。"""
    for item in request.items:
        if held_quantity(agent, item.product_id) + 1e-9 < item.quantity:
            raise BusinessRuleError("PRODUCT_NOT_HELD")


def meal_food_rate(foods: Mapping[str, Food], request: ConsumeRequest) -> float:
    """@brief 按类别和本餐份数计算原始摄入的每小时速率。"""
    total = sum(evaluate_food_energy(foods[item.product_id].category, item.quantity) for item in request.items)
    return total * 3600 / request.duration_seconds


def apply_meal_progress(agent: AgentState, foods: Mapping[str, Food],
                        activity: ActivityState, config: BodyConfig) -> AgentState:
    """@brief 仅结算 opened 实际累计份数的差额，分段和恢复不重复获得能量。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    previous = dict(activity.data.get("consumed", {}))
    consumed = dict(previous)
    for item in request.items:
        target = round(activity.data["opened"][item.product_id] * activity.elapsed_seconds /
                       activity.planned_duration_seconds, 9)
        delta = round(max(0.0, target - previous.get(item.product_id, 0.0)), 9)
        settlement = apply_food(agent.body, evaluate_food_energy(foods[item.product_id].category, delta), config)
        agent = agent.model_copy(update={"body": settlement.after}, deep=True)
        consumed[item.product_id] = round(previous.get(item.product_id, 0.0) + delta, 9)
    activity.data["consumed"] = consumed
    return agent
