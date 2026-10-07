"""@brief 到店与配送共用的恰好一次商品收货结算。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.body import RESOURCE_METAS
from worldmock.exchange.constants import GOODS_ENERGY_KCAL
from worldmock.exchange.operators import evaluate_goods_receipt
from worldmock.exchange.types import GoodsCategory
from worldmock.footprints.models import EventDraft, RESOURCE_FLOWS_FIELD, validate_resource_flows
from worldmock.life import felt
from worldmock.life.body import apply_food
from worldmock.life.admission import present_location
from worldmock.life.models import ActivityKind, BodyState, DeliveryState, FulfillmentKind, WorldState, active_activities
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.world_ops import _resource_flows_data
from worldmock.resources import FlowEntry, ResourceLedger
from worldmock.resources.models import DRAIN_DIRECTION


@dataclass(frozen=True, slots=True)
class ReceiptOutcome:
    """@brief 一笔订单的已结算世界、权威订单及模拟收货人事件。"""

    state: WorldState
    order: DeliveryState
    receipts: tuple[EventDraft, ...]


def settle_receipt(state: WorldState, semantics: ScenarioSemantics, order_id: str) -> ReceiptOutcome:
    """@brief 一次更新收货时刻、身体效果与专属分录；已送达订单原样返回。"""
    if order_id not in state.deliveries:
        raise BusinessRuleError("ORDER_NOT_FOUND")
    order = state.deliveries[order_id]
    if order.status == "delivered":
        return ReceiptOutcome(state, order, ())
    if state.current_time < order.expected_at:
        raise BusinessRuleError("DELIVERY_NOT_DUE")
    _require_parcel_receipt(state, order)
    arrived = order.arrived_at if order.arrived_at is not None else order.expected_at
    order = order.model_copy(update={"status": "delivered", "arrived_at": arrived,
                                     "received_at": state.current_time}, deep=True)
    changed = state.model_copy(update={"deliveries": {**state.deliveries, order_id: order}}, deep=True)
    if not order.recipient_id.startswith("agent:"):
        return ReceiptOutcome(changed, order, ())
    identity = int(order.recipient_id.split(":", 1)[1])
    recipient = state.agents[identity]
    if order.category == GoodsCategory.FUEL:
        provided = evaluate_goods_receipt(order.category, order.quantity)
        changed.agents[identity] = recipient.model_copy(
            update={"driving_days": recipient.driving_days + provided}, deep=True)
        evidence = {"provided": provided, "applied": provided, "efficiency": None,
                    RESOURCE_FLOWS_FIELD: _resource_flows_data(identity, order.received_at)}
    else:
        body, evidence = _receipt_effect(recipient.body, order, semantics)
        changed.agents[identity] = recipient.model_copy(update={"body": body}, deep=True)
    data = _receipt_data(state, order, identity, evidence)
    draft = EventDraft(event_type="environment_event", occurred_at=state.current_time,
                       agent_id=identity, source_kind="environment", visibility="agent", data=data)
    return ReceiptOutcome(changed, order, (draft,))


def _require_parcel_receipt(state: WorldState, order: DeliveryState) -> None:
    """@brief 到站不能自动收货，驿站商品只能在真实收货人物现场领取时结算。"""
    if order.fulfillment != FulfillmentKind.STATION_PICKUP:
        return
    if order.status != "ready_for_pickup":
        raise PreconditionError("PARCEL_NOT_READY", ["parcel_ready"],
                                public_reason="No parcels are ready for you at this station.")
    identity = int(order.recipient_id.split(":", 1)[1])
    if present_location(state, identity) != order.destination_id:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                                public_reason="Parcel collection requires presence at the named parcel station.")


def _receipt_effect(body: BodyState, order: DeliveryState, semantics: ScenarioSemantics) -> tuple[BodyState, dict]:
    """@brief 类别固定效果只影响真实收货人，并在返回前取走食品分录。"""
    provided = evaluate_goods_receipt(order.category, order.quantity)
    identity = int(order.recipient_id.split(":", 1)[1])
    if order.category in GOODS_ENERGY_KCAL:
        settlement = apply_food(body, provided, semantics.body)
        flows = _resource_flows_data(identity, order.received_at)
        return settlement.after, {"provided": provided,
                                  "applied": settlement.after.energy_kcal - body.energy_kcal,
                                  "efficiency": settlement.food_efficiency, RESOURCE_FLOWS_FIELD: flows}
    flows = _resource_flows_data(identity, order.received_at)
    if order.category == GoodsCategory.LEISURE:
        changed, relief = _leisure_effect(body, provided, order)
        return changed, {"provided": provided, "applied": relief["applied"], "efficiency": None,
                         RESOURCE_FLOWS_FIELD: [*flows, *relief[RESOURCE_FLOWS_FIELD]]}
    valence = felt.apply_mood_pulse(body.mood_valence, provided, body.neuroticism_norm)
    changed = body.model_copy(update={"mood_valence": valence}, deep=True)
    return changed, {"provided": provided, "applied": changed.mood_valence - body.mood_valence,
                     "efficiency": None, RESOURCE_FLOWS_FIELD: flows}


def _leisure_effect(body: BodyState, provided: float, order: DeliveryState) -> tuple[BodyState, dict]:
    """@brief 休闲用品按需求下限结算一次减少分录。"""
    applied = min(provided, body.entertainment_need)
    if applied == 0:
        return body.model_copy(deep=True), {"applied": 0, RESOURCE_FLOWS_FIELD: []}
    owner = order.recipient_id
    entry = FlowEntry("entertainment_need", owner, DRAIN_DIRECTION, applied, "goods:receipt", order.received_at)
    ledger = ResourceLedger([RESOURCE_METAS["entertainment_need"]])
    settled = ledger.apply([entry], {("entertainment_need", owner): body.entertainment_need})
    changed = body.model_copy(update={"entertainment_need": settled[("entertainment_need", owner)]}, deep=True)
    flows = validate_resource_flows([{"resource": entry.resource, "owner": owner,
                                    "direction": entry.direction, "amount": entry.amount, "cause": entry.cause}])
    return changed, {"applied": applied, RESOURCE_FLOWS_FIELD: flows}


def _receipt_data(state: WorldState, order: DeliveryState, identity: int, evidence: dict) -> dict:
    """@brief 收货事件记录订单收货地与人物当秒实际状态。"""
    activities = active_activities(state, identity)
    travelling = any(item.kind == ActivityKind.TRAVEL for item in activities)
    sleeping = any(item.kind == ActivityKind.SLEEP for item in activities)
    return {"kind": "goods_received", "order_id": order.order_id, "payer_id": order.agent_id,
            "goods_id": order.goods_id, "category": order.category.value, "quantity": order.quantity,
            "destination_id": order.destination_id,
            "recipient_location_id": None if travelling else state.agents[identity].location_id,
            "recipient_asleep": sleeping, **evidence}
