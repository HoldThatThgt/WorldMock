"""@brief 在到期事件中交付已付款订单，购买统一由整单结算实现；送达与给予只入收货人的持有。

收货人（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」）：同世界人物（agent:<id>，含付款人自己）入其库存的
指定地点，场景联系人入 WorldState.contact_holdings；付款人仍是订单的 agent_id。
"""

from __future__ import annotations

from worldmock.errors import BusinessRuleError
from worldmock.life.models import AgentState, DeliveryState, WorldState


def deliver_order(state: WorldState, order_id: str) -> tuple[WorldState, DeliveryState]:
    """@brief 到期配送转为收货人持有，重复到期不会重复增加库存。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status != "pending":
        return state.model_copy(deep=True), order.model_copy(deep=True)
    if state.current_time < order.expected_at:
        raise BusinessRuleError("DELIVERY_NOT_DUE")
    changed = add_holding(state, order.recipient_id, order.destination_id, (order.product_id, order.quantity))
    changed_order = order.model_copy(update={"status": "delivered"}, deep=True)
    deliveries = dict(changed.deliveries)
    deliveries[order_id] = changed_order
    return changed.model_copy(update={"deliveries": deliveries}, deep=True), changed_order


def add_holding(state: WorldState, recipient_id: str, location_id: str, item: tuple[str, float]) -> WorldState:
    """@brief 把一件商品的份数记入收货人的持有：同世界人物在 location_id 处的库存，联系人的 contact_holdings。

    :param recipient_id: agent:<id> 或场景联系人身份。
    :param item: （商品身份，份数）。
    """
    product_id, quantity = item
    if recipient_id.startswith("agent:"):
        agent = state.agents[int(recipient_id.split(":", 1)[1])]
        inventory = {key: dict(value) for key, value in agent.inventory.items()}
        local = inventory.setdefault(location_id, {})
        local[product_id] = round(local.get(product_id, 0) + quantity, 9)
        changed = agent.model_copy(update={"inventory": inventory}, deep=True)
        return state.model_copy(update={"agents": {**state.agents, agent.agent_id: changed}}, deep=True)
    holdings = {key: dict(value) for key, value in state.contact_holdings.items()}
    held = holdings.setdefault(recipient_id, {})
    held[product_id] = round(held.get(product_id, 0) + quantity, 9)
    return state.model_copy(update={"contact_holdings": holdings}, deep=True)


def held_quantity(agent: AgentState, product_id: str) -> float:
    """@brief 返回人物当前位置可以实际取得的商品份量。"""
    return float(agent.inventory.get(agent.location_id, {}).get(product_id, 0))
