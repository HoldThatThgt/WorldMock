"""@brief 配送到期委托唯一收货结算，不维护持有或库存。"""

from __future__ import annotations

from worldmock.errors import BusinessRuleError
from worldmock.life.models import FulfillmentKind, WorldState
from worldmock.life.receipt import ReceiptOutcome, settle_receipt
from worldmock.life.semantics import ScenarioSemantics


def deliver_order(state: WorldState, semantics: ScenarioSemantics, order_id: str) -> ReceiptOutcome:
    """@brief 直送经唯一收货入口，长途只到站，重复到站不产生任何效果。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.fulfillment == FulfillmentKind.STATION_PICKUP:
        if order.status != "pending":
            return ReceiptOutcome(state, order, ())
        if state.current_time < order.expected_at:
            raise BusinessRuleError("DELIVERY_NOT_DUE")
        order = order.model_copy(update={"status": "ready_for_pickup", "arrived_at": order.expected_at}, deep=True)
        changed = state.model_copy(update={"deliveries": {**state.deliveries, order_id: order}}, deep=True)
        return ReceiptOutcome(changed, order, ())
    return settle_receipt(state, semantics, order_id)
