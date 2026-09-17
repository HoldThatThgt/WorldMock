"""@brief 商务与进食的多件结算（修订合同 L1672-1674）：整单购买与一次多件进食。

purchase.items 为 1-5 件商品与数量，同一商家、同一履约方式、一次付款、一次
配送：任一件不可购则整单拒绝，不自动删件。consume.items 为 1-3 件已持有食品，
同一地点一次进食：用餐时长取最长者、热量与效果累加；非食品使用仍为单件。

commerce.py 是冻结模块（本单元无所有权），其单件 purchase/apply_product_
progress 的请求形状与单配送合同无法承载多件语义，故本模块以 settlement 资金
通道与 body 食品结算为基元实现整单结算；与 commerce 的单件路径并存，引擎按
请求件数路由。批次内的各件共享同一配送时刻（一次配送），付款合并为单笔分录
（一次付款）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError
from worldmock.life.body import apply_food
from worldmock.life.models import (
    ActivityState,
    ActivityStatus,
    AgentState,
    ConsumeRequest,
    DeliveryState,
    FulfillmentKind,
    PurchaseOrderRequest,
    ScheduledEvent,
    WorldState,
)
from worldmock.life.scenario import BodyConfig, ProductConfig, RuntimeScenario
from worldmock.life.settlement import settle_money_transfer
from worldmock.scheduling.events import ScheduledKind


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """@brief 一笔整单购买结算后的权威状态、订单与配送事件。"""

    state: WorldState
    orders: tuple[DeliveryState, ...]
    scheduled: tuple[ScheduledEvent, ...]
    total_price_fen: int


def purchase_order(state: WorldState, scenario: RuntimeScenario, agent_id: int,
                   request: PurchaseOrderRequest) -> OrderOutcome:
    """@brief 结算 1-5 件整单购买：一次付款、同一配送时刻、任一件失败整单拒绝。"""
    products = {item.product_id: item for item in scenario.products}
    agent = state.agents[agent_id]
    rows = _order_rows(state, scenario, agent, request, products)
    total = sum(product.price_fen * row.quantity for product, row in rows)
    settled = settle_money_transfer(state, agent.balance_account_id,
                                    rows[0][0].merchant_account_id, total, "purchase:order")
    stock = dict(settled.merchant_stock)
    orders: list[DeliveryState] = []
    events: list[ScheduledEvent] = []
    expected_at = _order_arrival(state, scenario, rows[0][0], request)
    sequence = max((item.sequence for item in settled.pending_events), default=0)
    changed_agents = dict(settled.agents)
    for index, (product, row) in enumerate(rows):
        stock[product.product_id] -= row.quantity
        identity = f"{agent.agent_id}-{state.current_time}-{len(settled.deliveries) + index + 1}"
        status = "delivered" if request.fulfillment == FulfillmentKind.PICKUP else "pending"
        order = DeliveryState(
            order_id=f"order-{identity}", agent_id=agent.agent_id, product_id=product.product_id,
            quantity=row.quantity, destination_id=request.destination_id,
            fulfillment=request.fulfillment, created_at=state.current_time,
            expected_at=expected_at, status=status,
            total_price_fen=product.price_fen * row.quantity)
        orders.append(order)
        if status == "delivered":
            changed_agents[agent.agent_id] = _add_inventory(
                changed_agents[agent.agent_id], request.destination_id,
                product.product_id, row.quantity)
        else:
            sequence += 1
            events.append(ScheduledEvent(
                scheduled_id=f"delivery-{identity}", kind=ScheduledKind.DELIVERY_ARRIVAL,
                due_at=expected_at, sequence=sequence, agent_id=agent.agent_id,
                data={"order_id": order.order_id}))
    deliveries = dict(settled.deliveries)
    deliveries.update({order.order_id: order for order in orders})
    pending = sorted(list(settled.pending_events) + list(events),
                     key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    changed = settled.model_copy(update={"merchant_stock": stock, "deliveries": deliveries,
                                         "agents": changed_agents,
                                         "pending_events": list(pending)}, deep=True)
    return OrderOutcome(changed, tuple(orders), tuple(events), total)


def _order_rows(state: WorldState, scenario: RuntimeScenario, agent: AgentState,
                request: PurchaseOrderRequest, products: dict[str, ProductConfig]):
    """@brief 校验整单：每件可见、同商家、报价一致、资金与库存充足、商家营业。"""
    if not 1 <= len(request.items) <= 5:
        raise BusinessRuleError("ORDER_ITEM_COUNT_OUT_OF_RANGE")  # 修订合同 L1672：1-5 件
    rows = []
    for item in request.items:
        if item.product_id not in products:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        product = products[item.product_id]  # 可见性由引擎观察门控（PRODUCT_NOT_VISIBLE）
        if item.price_version != product.price_version:
            raise BusinessRuleError("PRICE_VERSION_MISMATCH")
        rows.append((product, item))
    merchants = {product.merchant_account_id for product, _ in rows}
    if len(merchants) != 1:
        raise BusinessRuleError("ORDER_MERCHANT_MISMATCH")
    total = sum(product.price_fen * row.quantity for product, row in rows)
    if state.accounts[agent.balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    for product, row in rows:
        if state.merchant_stock[product.product_id] < row.quantity:
            raise BusinessRuleError("OUT_OF_STOCK")
    if not _merchant_open(state, scenario, rows[0][0]):
        raise BusinessRuleError("MERCHANT_CLOSED")
    locations = {item.location_id for item in scenario.locations}
    if request.destination_id not in locations:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    if request.fulfillment == FulfillmentKind.PICKUP:
        if (request.destination_id != rows[0][0].merchant_location_id
                or agent.location_id != request.destination_id):
            raise BusinessRuleError("PICKUP_LOCATION_REQUIRED")
    return rows


def _order_arrival(state: WorldState, scenario: RuntimeScenario, product: ProductConfig,
                   request: PurchaseOrderRequest) -> int:
    """@brief 整单共享的配送到达时刻：商品延迟 + 冻结 driving 路线时长。"""
    route_seconds = 0
    if product.merchant_location_id != request.destination_id:
        routes = [item for item in scenario.routes
                  if item.origin_id == product.merchant_location_id
                  and item.destination_id == request.destination_id
                  and item.mode.value == "driving"]
        if len(routes) != 1:
            raise BusinessRuleError("DELIVERY_ROUTE_NOT_FOUND")
        route_seconds = routes[0].duration_seconds
    return state.current_time + product.delivery_delay_seconds + route_seconds


def _merchant_open(state: WorldState, scenario: RuntimeScenario, product: ProductConfig) -> bool:
    """@brief 以场景本地小时执行商家冻结营业时间（与 commerce 口径一致）。"""
    location = next(item for item in scenario.locations
                    if item.location_id == product.merchant_location_id)
    hour = datetime.fromtimestamp(state.current_time, ZoneInfo(scenario.timezone)).hour
    return location.open_hour <= hour < location.close_hour


def _add_inventory(agent: AgentState, location_id: str, product_id: str,
                   quantity: int) -> AgentState:
    """@brief 在指定地点增加已交付商品数量（与 commerce._add_inventory 同口径）。"""
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    local = inventory.setdefault(location_id, {})
    local[product_id] = round(local.get(product_id, 0) + quantity, 9)
    return agent.model_copy(update={"inventory": inventory}, deep=True)


def change_inventory(agent: AgentState, product_id: str, delta: float) -> dict:
    """@brief 在人物当前位置改变商品份量并保持其他地点独立（负值超持即拒绝）。"""
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    local = inventory.setdefault(agent.location_id, {})
    local[product_id] = round(local.get(product_id, 0) + delta, 9)
    if local[product_id] < -1e-9:
        raise BusinessRuleError("PRODUCT_NOT_HELD")
    return inventory


def held_quantity(agent: AgentState, product_id: str) -> float:
    """@brief 返回人物当前位置可以实际取得的商品份量（委托 commerce 口径）。"""
    from worldmock.life.commerce import held_quantity as commerce_held

    return commerce_held(agent, product_id)


def meal_duration(products: dict[str, ProductConfig], request: ConsumeRequest) -> int:
    """@brief 多件进食时长取最长者；非食品单件必须等于目录时长（L1673）。"""
    durations = []
    for item in request.items:
        product = products.get(item.product_id)
        if product is None:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        durations.append(product.use_duration_seconds)
        if request.use_mode == "use" and request.duration_seconds != product.use_duration_seconds:
            raise BusinessRuleError("PRODUCT_USE_DURATION_MISMATCH")
    if request.use_mode == "eat" and request.duration_seconds != max(durations):
        raise BusinessRuleError("MEAL_DURATION_MUST_BE_LONGEST")
    return max(durations)


def validate_meal(agent: AgentState, products: dict[str, ProductConfig],
                  request: ConsumeRequest) -> None:
    """@brief 校验一次进食/使用：持有量、使用方式、食品多件与非食品单件。"""
    if not 1 <= len(request.items) <= 3:
        raise BusinessRuleError("MEAL_ITEM_COUNT_OUT_OF_RANGE")  # 修订合同 L1673：1-3 件
    if request.use_mode == "use" and len(request.items) != 1:
        raise BusinessRuleError("NON_FOOD_USE_SINGLE_ITEM")
    for item in request.items:
        product = products.get(item.product_id)
        if product is None:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        if request.use_mode not in product.use_modes:
            raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
        if held_quantity(agent, product.product_id) + 1e-9 < item.quantity:
            raise BusinessRuleError("PRODUCT_NOT_HELD")
        if product.required_location_id and agent.location_id != product.required_location_id:
            raise BusinessRuleError("PRODUCT_USE_LOCATION_REQUIRED")


def meal_food_rate(products: dict[str, ProductConfig], request: ConsumeRequest) -> float:
    """@brief 进食活动按目录热量合计形成的每小时净可用热量（进度结算输入）。"""
    if request.use_mode != "eat":
        return 0.0
    total = sum(products[item.product_id].energy_kcal_per_unit * item.quantity
                for item in request.items)
    return total * 3600 / request.duration_seconds


def apply_meal_progress(agent: AgentState, products: dict[str, ProductConfig],
                        activity: ActivityState, config: BodyConfig) -> AgentState:
    """@brief 按实际进食进度逐件扣份量、累加热量与效果（镜像 commerce 口径）。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    previous = dict(activity.data.get("consumed", {}))
    for item in request.items:
        product = products[item.product_id]
        if request.use_mode == "eat":
            target = item.quantity * activity.elapsed_seconds / activity.planned_duration_seconds
        elif activity.status == ActivityStatus.COMPLETED:
            target = item.quantity
        else:
            target = previous.get(item.product_id, 0.0)
        delta = max(0.0, target - previous.get(item.product_id, 0.0))
        if held_quantity(agent, product.product_id) + 1e-9 < delta:
            raise BusinessRuleError("PRODUCT_NOT_HELD")
        agent = _apply_item_delta(agent, product, delta, config)
        activity.data.setdefault("consumed", {})[item.product_id] = previous.get(
            item.product_id, 0.0) + delta
    return agent


def _apply_item_delta(agent: AgentState, product: ProductConfig, quantity: float,
                      config: BodyConfig) -> AgentState:
    """@brief 原子投影单件商品份量、效果和食品热量（效果与热量累加）。"""
    inventory = (change_inventory(agent, product.product_id, -quantity)
                 if product.consumable else agent.inventory)
    body = agent.body
    if product.energy_kcal_per_unit and quantity > 0:
        settlement = apply_food(body, product.energy_kcal_per_unit * quantity, config)
        body = settlement.after
    effects = set(agent.active_effects)
    if quantity > 0:
        effects.add(product.effect)
    return agent.model_copy(update={"inventory": inventory, "body": body,
                                    "active_effects": effects}, deep=True)
