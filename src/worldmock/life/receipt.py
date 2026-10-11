"""@brief 到店、外卖和本人驿站取件共用恰好一次真实收货结算。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.body import RESOURCE_METAS
from worldmock.exchange.operators import evaluate_goods_receipt
from worldmock.footprints.models import EventDraft
from worldmock.life.callbacks import reset_on_receipt
from worldmock.life.models import ActivityKind, DeliveryState, FulfillmentKind, WorldState, active_activities
from worldmock.life.purchase_offers import present_location
from worldmock.resources import FlowEntry, ResourceLedger


@dataclass(frozen=True, slots=True)
class ReceiptOutcome:
    """@brief 一笔真实收货的世界、订单与本人事件。"""

    #: 已结算世界。
    state: WorldState
    #: 订单权威终态。
    order: DeliveryState
    #: 本人恰好一次收货证据。
    receipts: tuple[EventDraft, ...]


def settle_receipt(state: WorldState, context, order_id: str) -> ReceiptOutcome:
    """@brief 已送达重放无效果，未付款永不收货，到站仅本人在站领取。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status == "delivered":
        return ReceiptOutcome(state, order, ())
    _require_receipt(state, order)
    arrived = order.arrived_at if order.arrived_at is not None else state.current_time
    order = order.model_copy(update={"status": "delivered", "arrived_at": arrived,
                                     "received_at": state.current_time}, deep=True)
    owner = state.agents[order.agent_id]
    body, evidence = _receipt_effect(owner.body, order, context)
    changed = state.model_copy(update={"deliveries": {**state.deliveries, order_id: order},
        "agents": {**state.agents, owner.agent_id: owner.model_copy(update={"body": body}, deep=True)}}, deep=True)
    draft = EventDraft(event_type="environment_event", occurred_at=state.current_time,
        agent_id=order.agent_id, source_kind="environment", visibility="agent",
        data=_receipt_data(state, order, evidence))
    reset = reset_on_receipt(changed, context.catalog, draft)
    context.callback_causes.update(reset.causes)
    return ReceiptOutcome(reset.state, order, (draft, *reset.drafts))


def _require_receipt(state: WorldState, order: DeliveryState) -> None:
    """@brief 付款和到期是收货必要事实；取件须已到本站。"""
    if order.paid_at is None or order.status == "cancelled":
        raise BusinessRuleError("ORDER_NOT_PAID")
    if state.current_time < order.expected_at:
        raise BusinessRuleError("DELIVERY_NOT_DUE")
    if order.fulfillment == FulfillmentKind.STATION_PICKUP:
        if order.status != "ready_for_pickup":
            raise PreconditionError("PARCEL_NOT_READY", ["parcel_ready"],
                                    public_reason="No parcels are ready for you at this station.")
        if present_location(state, order.agent_id) != order.destination_id:
            raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                                    public_reason="Parcel collection requires presence at the named parcel station.")


def _receipt_effect(body, order: DeliveryState, context) -> tuple:
    """@brief 食品按真实睡眠不足效率补能；饮料和商品只缓解放松需要。"""
    if order.category in {"snack", "meal"}:
        return _food_effect(body, order, context)
    provided = evaluate_goods_receipt(order.category, order.quantity)
    applied = min(provided, body.relaxation_need)
    flows = []
    changed = body.model_copy(deep=True)
    if applied:
        owner = f"agent:{order.agent_id}"
        entry = FlowEntry("relaxation_need", owner, "drain", applied, "goods:receipt", order.received_at)
        settled = ResourceLedger([RESOURCE_METAS["relaxation_need"]]).apply([entry],
            {("relaxation_need", owner): body.relaxation_need})
        changed = body.model_copy(update={"relaxation_need": settled[("relaxation_need", owner)]}, deep=True)
        flows = [{"resource": entry.resource, "owner": owner, "direction": entry.direction,
                  "amount": entry.amount, "cause": entry.cause}]
    return changed, {"provided": provided, "applied": applied, "efficiency": None, "resource_flows": flows}


def _food_effect(body, order: DeliveryState, context) -> tuple:
    """@brief 收货函数取走自身食品分录，不让它落到之后的身体事件。"""
    from worldmock.life.body import apply_food, last_flows

    provided = evaluate_goods_receipt(order.category, order.quantity)
    settlement = apply_food(body, provided, context.body)
    flows = [{"resource": entry.resource, "owner": f"agent:{order.agent_id}", "direction": entry.direction,
              "amount": entry.amount, "cause": entry.cause} for entry in last_flows()]
    return settlement.after, {"provided": provided, "applied": settlement.after.energy_kcal - body.energy_kcal,
                              "efficiency": settlement.food_efficiency, "resource_flows": flows}


def _receipt_data(state: WorldState, order: DeliveryState, evidence: dict) -> dict:
    """@brief 收货证据同时保留实际目的地与人物当秒位置和睡眠事实。"""
    asleep = any(item.kind == ActivityKind.SLEEP for item in active_activities(state, order.agent_id))
    return {"kind": "goods_received", "order_id": order.order_id, "payer_id": order.agent_id,
            "goods_id": order.goods_id, "category": order.category, "quantity": 1,
            "destination_id": order.destination_id,
            "recipient_location_id": present_location(state, order.agent_id),
            "recipient_asleep": asleep, **evidence}
