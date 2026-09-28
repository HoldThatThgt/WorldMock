"""@brief 按门店供给执行整单购买，并按持有商品执行多件进食结算。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.categories import Action
from worldmock.errors import BusinessRuleError
from worldmock.life.body import apply_food
from worldmock.life.category_contract import RuntimeCategoryRule, match_rows, require_runtime_rule
from worldmock.life.commerce import add_holding
from worldmock.life.map_models import RouteConfig, SupplyConfig
from worldmock.life.models import (
    ActivityState,
    ActivityStatus,
    AgentState,
    ConsumeRequest,
    DeliveryState,
    FulfillmentKind,
    PurchaseOrderRequest,
    ScheduledEvent,
    SupplyQuoteState,
    WorldState,
)
from worldmock.life.opening_hours import opening_status
from worldmock.life.scenario import BodyConfig, InventoryItemConfig, RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.storefront_state import current_quote
from worldmock.scheduling.events import ScheduledKind


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """@brief 一笔整单购买结算后的权威状态、订单与配送事件。"""

    state: WorldState
    orders: tuple[DeliveryState, ...]
    scheduled: tuple[ScheduledEvent, ...]
    total_price_fen: int
    inventory_changes: tuple[dict, ...]
    category_matches: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _OrderRow:
    """@brief 一项已经通过报价、库存和资格检查的门店供给。"""

    supply: SupplyConfig
    quote: SupplyQuoteState
    quantity: int
    total_price_fen: int
    category_match: RuntimeCategoryRule
    recipient_id: str
    request_message_id: str | None


@dataclass(frozen=True, slots=True)
class _Fulfillment:
    """@brief 整单共用的创建时刻、交付时刻和运输路线。"""

    created_at: int
    expected_at: int
    route: RouteConfig | None


def purchase_order(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                   request: PurchaseOrderRequest) -> OrderOutcome:
    """@brief 逐供给校验后一次付款；任意失败不改变任何世界状态。付款人是 agent_id，送达只入各项收货人的持有。"""
    agent = state.agents[agent_id]
    rows = _order_rows(state, semantics, agent, request)
    fulfillment = _order_fulfillment(state, semantics, request, rows)
    total = sum(row.total_price_fen for row in rows)
    if state.accounts[agent.balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    orders = _build_orders(state, rows, request, fulfillment, agent_id)
    settled = settle_money_transfer(state, agent.balance_account_id,
                                    rows[0].supply.seller_account_id, total, "purchase:order")
    stock = dict(settled.supply_stock)
    received = settled
    inventory_changes = []
    for order in orders:
        stock[order.supply_id] -= order.quantity
        if order.status == "delivered":
            received = add_holding(received, order.recipient_id, order.destination_id,
                                   (order.product_id, order.quantity))
            inventory_changes.append({"order_id": order.order_id, "supply_id": order.supply_id,
                                      "location_id": order.destination_id, "product_id": order.product_id,
                                      "quantity": order.quantity})
    events = _delivery_events(state, orders)
    deliveries = dict(settled.deliveries)
    deliveries.update({order.order_id: order for order in orders})
    pending = sorted([*settled.pending_events, *events],
                     key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    changed = received.model_copy(update={"supply_stock": stock, "deliveries": deliveries,
                                          "pending_events": pending}, deep=True)
    return OrderOutcome(changed, orders, events, total, tuple(inventory_changes),
                        tuple(match_rows(row.category_match for row in rows)))


def _order_rows(state: WorldState, semantics: ScenarioSemantics, agent: AgentState,
                request: PurchaseOrderRequest) -> tuple[_OrderRow, ...]:
    """@brief 拒绝重复项（去重键为供给与收货人）与跨店整单，使用世界中的当前报价检查每项供给。"""
    if not 1 <= len(request.items) <= 5:
        raise BusinessRuleError("ORDER_ITEM_COUNT_OUT_OF_RANGE")
    if len({(item.supply_id, item.recipient_id) for item in request.items}) != len(request.items):
        raise BusinessRuleError("ORDER_DUPLICATE_SUPPLY")
    scenario = semantics.scenario
    supplies = {item.supply_id: item for item in scenario.supplies}
    rows = []
    for item in request.items:
        if item.supply_id not in supplies:
            raise BusinessRuleError("SUPPLY_NOT_FOUND")
        supply = supplies[item.supply_id]
        quote = current_quote(state, supply.supply_id)
        if quote is None:
            raise BusinessRuleError("SUPPLY_QUOTE_UNKNOWN")
        if item.price_version != quote.price_version:
            raise BusinessRuleError("PRICE_VERSION_MISMATCH")
        if state.supply_stock[supply.supply_id] < item.quantity:
            raise BusinessRuleError("OUT_OF_STOCK")
        if not set(supply.required_qualifications) <= set(agent.qualifications):
            raise BusinessRuleError("SUPPLY_QUALIFICATION_REQUIRED")
        if request.fulfillment not in supply.fulfillment_modes:
            raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
        match = require_runtime_rule(semantics.catalog,
                                     semantics.catalog.objects[supply.product_id].category_id, Action.PURCHASE)
        amount = semantics.purchase_amount(supply.product_id, quote.price_fen, item.quantity)
        rows.append(_OrderRow(supply, quote, item.quantity, amount, match, item.recipient_id,
                              item.request_message_id))
    if len({row.supply.seller_account_id for row in rows}) != 1:
        raise BusinessRuleError("ORDER_SELLER_MISMATCH")
    if len({row.supply.location_id for row in rows}) != 1:
        raise BusinessRuleError("ORDER_LOCATION_MISMATCH")
    _validate_storefront(state, scenario, agent, rows[0].supply.location_id)
    return tuple(rows)


def _validate_storefront(state: WorldState, scenario: RuntimeScenario,
                         agent: AgentState, location_id: str) -> None:
    """@brief 购买时重新检查地点资格与分钟级营业事实，未知不等同闭店。"""
    location = next(item for item in scenario.locations if item.location_id == location_id)
    if not set(location.required_qualifications) <= set(agent.qualifications):
        raise BusinessRuleError("LOCATION_ACCESS_DENIED")
    status = opening_status(location, state.current_time, scenario.timezone,
                            state.opening_exceptions[location_id])
    if status == "unknown":
        raise BusinessRuleError("MERCHANT_OPENING_UNKNOWN")
    if status == "closed":
        raise BusinessRuleError("MERCHANT_CLOSED")


def _order_fulfillment(state: WorldState, semantics: ScenarioSemantics,
                       request: PurchaseOrderRequest, rows: tuple[_OrderRow, ...]) -> _Fulfillment:
    """@brief 自提即时交付；配送按最慢备货和真实出发时刻的道路时长安排一次到达。"""
    scenario = semantics.scenario
    if request.destination_id not in {item.location_id for item in scenario.locations}:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    if request.fulfillment == FulfillmentKind.PICKUP:
        return _Fulfillment(state.current_time, state.current_time, None)
    plan = plan_delivery(semantics, tuple(row.supply for row in rows), request.destination_id,
                         state.current_time)
    return _Fulfillment(state.current_time, plan.expected_at, plan.route)


def plan_delivery(semantics: ScenarioSemantics, supplies: tuple[SupplyConfig, ...],
                  destination_id: str, now: int) -> _Fulfillment:
    """@brief 纯函数：整单最慢备货后出发，加出发时刻的驾车规划时长即预计送达时刻。

    购买结算、观察投影与吃饭途径句共用这一算法（docs/dev/SPEC-time-and-energy.md 第八章「呈现」）。
    送达地不在任一供给的可配送目的地时以 DELIVERY_DESTINATION_NOT_SUPPORTED 拒绝；道路缺失、
    不可达或未知时按 `_delivery_route` 的业务拒绝码拒绝。
    """
    if any(destination_id not in supply.delivery_destination_ids for supply in supplies):
        raise BusinessRuleError("DELIVERY_DESTINATION_NOT_SUPPORTED")
    departure = now + max(supply.delivery_delay_seconds for supply in supplies)
    route = _delivery_route(semantics.scenario, supplies[0].location_id, destination_id)
    duration = (semantics.travel_planned_seconds("driving", route.duration_seconds, departure)
                if route is not None else 0)
    return _Fulfillment(now, departure + duration, route)


def delivery_expected_at(semantics: ScenarioSemantics, supply: SupplyConfig,
                         destination_id: str, now: int) -> int | None:
    """@brief 单项供给现在下单送到指定地点的预计送达时刻；送不到这里（目的地不支持、道路未知或不可达）时为 None。"""
    try:
        return plan_delivery(semantics, (supply,), destination_id, now).expected_at
    except BusinessRuleError:
        return None


def _delivery_route(scenario: RuntimeScenario, origin: str, destination: str) -> RouteConfig | None:
    """@brief 同地点无道路行程，其余配送只允许明确可达的冻结驾车路线。"""
    if origin == destination:
        return None
    matches = [route for route in scenario.routes if route.origin_id == origin
               and route.destination_id == destination and route.mode.value == "driving"]
    if len(matches) != 1:
        raise BusinessRuleError("DELIVERY_ROUTE_NOT_FOUND")
    route = matches[0]
    if route.status == "unreachable":
        raise BusinessRuleError("DELIVERY_ROUTE_UNREACHABLE")
    if route.status == "unknown":
        raise BusinessRuleError("DELIVERY_ROUTE_INFORMATION_UNKNOWN")
    return route


def _build_orders(state: WorldState, rows: tuple[_OrderRow, ...], request: PurchaseOrderRequest,
                  fulfillment: _Fulfillment, agent_id: int) -> tuple[DeliveryState, ...]:
    """@brief 每个订单保存实际供给、报价、来源、收货人与所回应的消息，整单共享交付时刻与路线。"""
    if request.fulfillment == FulfillmentKind.PICKUP:
        origin = rows[0].supply.location_id
        if request.destination_id != origin or state.agents[agent_id].location_id != origin:
            raise BusinessRuleError("PICKUP_LOCATION_REQUIRED")
    orders = []
    for index, row in enumerate(rows, start=1):
        supply = row.supply
        identity = f"{agent_id}-{state.current_time}-{len(state.deliveries) + index}"
        route = fulfillment.route
        orders.append(DeliveryState(
            order_id=f"order-{identity}", agent_id=agent_id, product_id=supply.product_id,
            supply_id=supply.supply_id, location_id=supply.location_id,
            seller_account_id=supply.seller_account_id, price_version=row.quote.price_version,
            route_id=route.route_id if route is not None else None,
            source={"supply": supply.source.model_dump(mode="json"), "quote": row.quote.source,
                    "route": route.source.model_dump(mode="json") if route is not None else None},
            quantity=row.quantity, destination_id=request.destination_id, fulfillment=request.fulfillment,
            created_at=fulfillment.created_at, expected_at=fulfillment.expected_at,
            status="delivered" if fulfillment.expected_at == fulfillment.created_at else "pending",
            total_price_fen=row.total_price_fen, recipient_id=row.recipient_id,
            request_message_id=row.request_message_id))
    return tuple(orders)


def _delivery_events(state: WorldState, orders: tuple[DeliveryState, ...]) -> tuple[ScheduledEvent, ...]:
    """@brief 整单只登记一个携带全部订单身份的配送事件。"""
    if orders[0].status == "delivered":
        return ()
    sequence = max((item.sequence for item in state.pending_events), default=0) + 1
    return (ScheduledEvent(
        scheduled_id=f"delivery-{orders[0].order_id}", kind=ScheduledKind.DELIVERY_ARRIVAL,
        due_at=orders[0].expected_at, sequence=sequence, agent_id=orders[0].agent_id,
        data={"order_ids": [order.order_id for order in orders]}),)


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


def meal_duration(products: dict[str, InventoryItemConfig], request: ConsumeRequest) -> int:
    """@brief 多件进食时长取最长者；非食品单件必须等于目录时长（docs/dev/SPEC-life-realism.md「商务与进食」）。"""
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


def validate_meal_use(agent: AgentState, products: dict[str, InventoryItemConfig],
                      request: ConsumeRequest) -> None:
    """@brief 校验配方与普通使用共享的件数、使用方式和地点条件。"""
    if not 1 <= len(request.items) <= 3:
        raise BusinessRuleError("MEAL_ITEM_COUNT_OUT_OF_RANGE")  # docs/dev/SPEC-life-realism.md「商务与进食」：1-3 件
    if request.use_mode == "use" and len(request.items) != 1:
        raise BusinessRuleError("NON_FOOD_USE_SINGLE_ITEM")
    for item in request.items:
        product = products.get(item.product_id)
        if product is None:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        if request.use_mode not in product.use_modes:
            raise BusinessRuleError("PRODUCT_USE_MODE_NOT_ALLOWED")
        if product.required_location_id and agent.location_id != product.required_location_id:
            raise BusinessRuleError("PRODUCT_USE_LOCATION_REQUIRED")


def validate_meal_inventory(agent: AgentState, request: ConsumeRequest) -> None:
    """@brief 普通使用按请求份数核对库存，配方数量由配方计划核对。"""
    for item in request.items:
        if held_quantity(agent, item.product_id) + 1e-9 < item.quantity:
            raise BusinessRuleError("PRODUCT_NOT_HELD")


def meal_food_rate(products: dict[str, InventoryItemConfig], request: ConsumeRequest) -> float:
    """@brief 进食活动按目录热量合计形成的每小时净可用热量（进度结算输入）。"""
    if request.use_mode != "eat":
        return 0.0
    total = sum(products[item.product_id].energy_kcal_per_unit * item.quantity
                for item in request.items)
    return total * 3600 / request.duration_seconds


def apply_meal_progress(agent: AgentState, products: dict[str, InventoryItemConfig],
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
        # 库存既有九位份量精度必须同时用于累计目标和差额，避免分段舍入累积成缺货。
        target = round(target, 9)
        delta = round(max(0.0, target - previous.get(item.product_id, 0.0)), 9)
        held = held_quantity(agent, product.product_id)
        if held + 1e-9 < delta:
            raise BusinessRuleError("PRODUCT_NOT_HELD")
        delta = min(delta, held)
        agent = _apply_item_delta(agent, product, delta, config)
        activity.data.setdefault("consumed", {})[item.product_id] = round(
            previous.get(item.product_id, 0.0) + delta, 9)
    return agent


def _apply_item_delta(agent: AgentState, product: InventoryItemConfig, quantity: float,
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
