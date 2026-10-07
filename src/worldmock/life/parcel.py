"""@brief 家庭最近驿站选址与本人现场零秒领取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.catalog.errors import RegistrationError
from worldmock.errors import PreconditionError
from worldmock.exchange.types import LocationKind
from worldmock.life.admission import present_location
from worldmock.life.receipt import settle_receipt

if TYPE_CHECKING:
    from worldmock.footprints.models import EventDraft
    from worldmock.life.models import DeliveryState, WorldState
    from worldmock.life.semantics import ScenarioSemantics


@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    """@brief 本人领取后的完整状态、权威订单及待绑定真实结果的收货证据。"""

    #: 领取结算后的世界。
    state: WorldState
    #: 本次实际领取的本人订单。
    orders: tuple[DeliveryState, ...]
    #: 只含真实收货人的收货事件草稿。
    receipts: tuple[EventDraft, ...]


def nearest_parcel_station(semantics: ScenarioSemantics, home_id: str) -> str:
    """@brief 从完整图取家庭最近驿站，平局按地点身份；不能改选更远已选站。"""
    stations = [item.location_id for item in semantics.catalog.locations.values()
                if item.kind == LocationKind.PARCEL_STATION]
    if not stations:
        raise RegistrationError("PARCEL_STATION_REQUIRED")
    selected = min(stations, key=lambda identity: (semantics.catalog.shortest_m(home_id, identity), identity))
    if selected not in {item.location_id for item in semantics.scenario.locations}:
        raise RegistrationError("PARCEL_STATION_REQUIRED", selected)
    return selected


def collect_parcels(state: WorldState, semantics: ScenarioSemantics,
                    agent_id: int, station_id: str) -> CollectionOutcome:
    """@brief 本人在所指驿站领取本人全部可取订单，没有订单或离站时原子拒绝。"""
    station = semantics.catalog.locations[station_id]
    if station.kind != LocationKind.PARCEL_STATION or present_location(state, agent_id) != station_id:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                                public_reason="Parcel collection requires presence at the named parcel station.")
    orders = sorted((order for order in state.deliveries.values()
                     if order.recipient_id == f"agent:{agent_id}" and order.destination_id == station_id
                     and order.status == "ready_for_pickup"), key=lambda item: (item.created_at, item.order_id))
    if not orders:
        raise PreconditionError("PARCEL_NOT_READY", ["parcel_ready"],
                                public_reason="No parcels are ready for you at this station.")
    changed = state
    receipts = []
    received = []
    for order in orders:
        outcome = settle_receipt(changed, semantics, order.order_id)
        changed = outcome.state
        receipts.extend(outcome.receipts)
        received.append(outcome.order)
    return CollectionOutcome(changed, tuple(received), tuple(receipts))
