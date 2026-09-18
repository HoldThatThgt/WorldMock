"""@brief 原子处理资金、库存、订单、履约和商品使用。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError
from worldmock.life.models import (
    AgentState,
    DeliveryState,
    FulfillmentKind,
    PurchaseRequest,
    ScheduledEvent,
    ScheduledKind,
    WorldState,
)
from worldmock.life.scenario import LifeScenario, ProductConfig
from worldmock.life.settlement import settle_money_transfer


@dataclass(frozen=True, slots=True)
class PurchaseOutcome:
    """@brief 一次购买结算后的权威状态和订单。"""

    state: WorldState
    delivery: DeliveryState
    scheduled: ScheduledEvent | None


def purchase(state: WorldState, scenario: LifeScenario, agent_id: int,
             request: PurchaseRequest) -> PurchaseOutcome:
    """@brief 在受理时同时扣款、收款和预留库存。"""
    products = {item.product_id: item for item in scenario.products}
    if request.product_id not in products:
        raise BusinessRuleError("PRODUCT_NOT_FOUND")
    product = products[request.product_id]
    agent = state.agents[agent_id]
    _validate_purchase(state, scenario, agent, product, request)
    total = product.price_fen * request.quantity
    settled = settle_money_transfer(state, agent.balance_account_id,
                                    product.merchant_account_id, total,
                                    f"purchase:{product.product_id}")
    stock = dict(settled.merchant_stock)
    stock[product.product_id] -= request.quantity
    delivery, scheduled = _fulfillment(state, scenario, agent, product, request)
    deliveries = dict(settled.deliveries)
    deliveries[delivery.order_id] = delivery
    agents = dict(settled.agents)
    if delivery.status == "delivered":
        agents[agent_id] = _add_inventory(agent, request.destination_id, product.product_id, request.quantity)
    changed = settled.model_copy(update={"merchant_stock": stock,
                                         "deliveries": deliveries, "agents": agents}, deep=True)
    if scheduled:
        changed.pending_events.append(scheduled)
        changed.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    return PurchaseOutcome(changed, delivery, scheduled)


def deliver_order(state: WorldState, order_id: str) -> tuple[WorldState, DeliveryState]:
    """@brief 到期配送转为人物持有，重复到期不会重复增加库存。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status != "pending":
        return state.model_copy(deep=True), order.model_copy(deep=True)
    agent = state.agents[order.agent_id]
    changed_agent = _add_inventory(agent, order.destination_id, order.product_id, order.quantity)
    changed_order = order.model_copy(update={"status": "delivered"}, deep=True)
    agents = dict(state.agents)
    agents[agent.agent_id] = changed_agent
    deliveries = dict(state.deliveries)
    deliveries[order_id] = changed_order
    changed = state.model_copy(update={"agents": agents, "deliveries": deliveries}, deep=True)
    return changed, changed_order


def held_quantity(agent: AgentState, product_id: str) -> float:
    """@brief 返回人物当前位置可以实际取得的商品份量。"""
    return float(agent.inventory.get(agent.location_id, {}).get(product_id, 0))


def _validate_purchase(state: WorldState, scenario: LifeScenario, agent: AgentState,
                       product: ProductConfig, request: PurchaseRequest) -> None:
    """@brief 核对报价、资金、库存、地点和履约路径。"""
    total = product.price_fen * request.quantity
    if request.price_version != product.price_version:
        raise BusinessRuleError("PRICE_VERSION_MISMATCH")
    if state.accounts[agent.balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    if state.merchant_stock[product.product_id] < request.quantity:
        raise BusinessRuleError("OUT_OF_STOCK")
    if not _merchant_open(state, scenario, product):
        raise BusinessRuleError("MERCHANT_CLOSED")
    locations = {location.location_id for location in scenario.locations}
    if request.destination_id not in locations:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    if request.fulfillment == FulfillmentKind.PICKUP:
        if request.destination_id != product.merchant_location_id or agent.location_id != request.destination_id:
            raise BusinessRuleError("PICKUP_LOCATION_REQUIRED")


def _fulfillment(state: WorldState, scenario: LifeScenario, agent: AgentState,
                 product: ProductConfig, request: PurchaseRequest) -> tuple[DeliveryState, ScheduledEvent | None]:
    """@brief 构造即时取货或带唯一到期事件的配送。"""
    now = state.current_time
    route_seconds = _delivery_route_seconds(scenario, product.merchant_location_id, request.destination_id)
    expected = now + product.delivery_delay_seconds + route_seconds
    status = "delivered" if request.fulfillment == FulfillmentKind.PICKUP else "pending"
    identity = f"{agent.agent_id}-{state.current_time}-{len(state.deliveries) + 1}"
    order = DeliveryState(order_id=f"order-{identity}", agent_id=agent.agent_id,
                          product_id=product.product_id, quantity=request.quantity,
                          destination_id=request.destination_id, fulfillment=request.fulfillment,
                          created_at=now, expected_at=expected, status=status,
                          total_price_fen=product.price_fen * request.quantity)
    if status == "delivered":
        return order, None
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    event = ScheduledEvent(scheduled_id=f"delivery-{identity}", kind=ScheduledKind.DELIVERY_ARRIVAL,
                           due_at=expected, sequence=sequence, agent_id=agent.agent_id,
                           data={"order_id": order.order_id})
    return order, event


def _delivery_route_seconds(scenario: LifeScenario, origin: str, destination: str) -> int:
    """@brief 配送跨地点时要求冻结 driving 路由。"""
    if origin == destination:
        return 0
    routes = [item for item in scenario.routes if item.origin_id == origin
              and item.destination_id == destination and item.mode.value == "driving"]
    if len(routes) != 1:
        raise BusinessRuleError("DELIVERY_ROUTE_NOT_FOUND")
    return routes[0].duration_seconds


def _add_inventory(agent: AgentState, location_id: str, product_id: str, quantity: int) -> AgentState:
    """@brief 在订单明确目的地增加实际已交付商品数量。"""
    inventory = _change_inventory(agent, product_id, quantity, location_id)
    return agent.model_copy(update={"inventory": inventory}, deep=True)


def _merchant_open(state: WorldState, scenario: LifeScenario, product: ProductConfig) -> bool:
    """@brief 以场景本地小时执行商家冻结营业时间。"""
    location = next(item for item in scenario.locations if item.location_id == product.merchant_location_id)
    hour = datetime.fromtimestamp(state.current_time, ZoneInfo(scenario.timezone)).hour
    return location.open_hour <= hour < location.close_hour


def _change_inventory(agent: AgentState, product_id: str, delta: float,
                      location_id: str | None = None) -> dict[str, dict[str, float]]:
    """@brief 在明确地点改变商品份量并保持其他地点独立。"""
    location = location_id or agent.location_id
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    local = inventory.setdefault(location, {})
    local[product_id] = round(local.get(product_id, 0) + delta, 9)
    if local[product_id] < -1e-9:
        raise BusinessRuleError("PRODUCT_NOT_HELD")
    return inventory
