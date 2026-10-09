"""@brief 已付款外卖真实送达和快递到站的唯一入口。"""

from worldmock.errors import BusinessRuleError
from worldmock.life.models import FulfillmentKind, WorldState
from worldmock.life.receipt import ReceiptOutcome, settle_receipt


def deliver_order(state: WorldState, context, order_id: str) -> ReceiptOutcome:
    """@brief 快递只到站不收货；重复到站无效果，未付款不配送。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status != "pending":
        return ReceiptOutcome(state, order, ())
    if order.paid_at is None:
        raise BusinessRuleError("ORDER_NOT_PAID")
    if state.current_time < order.expected_at:
        raise BusinessRuleError("DELIVERY_NOT_DUE")
    if order.fulfillment == FulfillmentKind.STATION_PICKUP:
        order = order.model_copy(update={"status": "ready_for_pickup", "arrived_at": state.current_time}, deep=True)
        changed = state.model_copy(update={"deliveries": {**state.deliveries, order_id: order}}, deep=True)
        return ReceiptOutcome(changed, order, ())
    return settle_receipt(state, context, order_id)
