"""@brief 在到期事件中交付已付款订单，购买统一由整单结算实现。"""

from __future__ import annotations

from worldmock.errors import BusinessRuleError
from worldmock.life.models import AgentState, DeliveryState, WorldState


def deliver_order(state: WorldState, order_id: str) -> tuple[WorldState, DeliveryState]:
    """@brief 到期配送转为人物持有，重复到期不会重复增加库存。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status != "pending":
        return state.model_copy(deep=True), order.model_copy(deep=True)
    if state.current_time < order.expected_at:
        raise BusinessRuleError("DELIVERY_NOT_DUE")
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


def _add_inventory(agent: AgentState, location_id: str, product_id: str, quantity: int) -> AgentState:
    """@brief 在订单明确目的地增加实际已交付商品数量。"""
    inventory = _change_inventory(agent, product_id, quantity, location_id)
    return agent.model_copy(update={"inventory": inventory}, deep=True)


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
