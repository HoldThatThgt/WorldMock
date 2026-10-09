"""@brief 人物在真实驿站交互时一次领取本站全部已到站包裹。"""

from dataclasses import dataclass

from worldmock.errors import PreconditionError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import DeliveryState, WorldState
from worldmock.life.purchase_offers import present_location
from worldmock.life.receipt import settle_receipt


@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    """@brief 本人取件后的世界、权威订单及待绑定真实交互结果的收货证据。"""

    #: 领取后的世界。
    state: WorldState
    #: 本次实际领取订单。
    orders: tuple[DeliveryState, ...]
    #: 本人的真实收货事件。
    receipts: tuple[EventDraft, ...]


def require_ready_parcels(state: WorldState, context, agent_id: int, station_id: str) -> tuple[DeliveryState, ...]:
    """@brief 交互受理前核对本人在站且本站存在已到站包裹。"""
    station = context.catalog.locations[station_id]
    if not station.accepts_parcels or present_location(state, agent_id) != station_id:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                                public_reason="Parcel collection requires presence at the named parcel station.")
    orders = tuple(sorted((order for order in state.deliveries.values()
        if order.agent_id == agent_id and order.destination_id == station_id and order.status == "ready_for_pickup"),
        key=lambda item: (item.created_at, item.order_id)))
    if not orders:
        raise PreconditionError("PARCEL_NOT_READY", ["parcel_ready"],
                                public_reason="No parcels are ready for you at this station.")
    return orders


def collect_parcels(state: WorldState, context, agent_id: int, station_id: str) -> CollectionOutcome:
    """@brief 全部可取订单按创建时间和身份结算，重复已领取订单不在候选中。"""
    orders = require_ready_parcels(state, context, agent_id, station_id)
    changed, receipts, received = state, [], []
    for order in orders:
        outcome = settle_receipt(changed, context, order.order_id)
        changed = outcome.state
        receipts.extend(outcome.receipts)
        received.append(outcome.order)
    return CollectionOutcome(changed, tuple(received), tuple(receipts))
