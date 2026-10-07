"""@brief 汽车归属、真实驾车日期与现场加油的固定合同。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from worldmock.errors import PreconditionError, reject_value
from worldmock.exchange.types import GoodsCategory, LocationKind
from worldmock.life.admission import present_location

if TYPE_CHECKING:
    from worldmock.life.models import PurchaseOrderRequest, WorldState
    from worldmock.life.semantics import ScenarioSemantics


def driving_dates(started_at: int, ended_at: int, timezone: str) -> tuple[str, ...]:
    """@brief 返回半开真实驾驶区间内有正秒数的本地日期，遵循时区午夜。"""
    if ended_at < started_at:
        reject_value("Driving interval must not move backwards")
    dates = []
    cursor = started_at
    zone = ZoneInfo(timezone)
    while cursor < ended_at:
        day = datetime.fromtimestamp(cursor, zone).date()
        dates.append(day.isoformat())
        midnight = datetime.combine(day + timedelta(days=1), time.min, zone)
        cursor = min(ended_at, int(midnight.timestamp()))
    return tuple(dates)


def require_driving_days(state: WorldState, semantics: ScenarioSemantics, agent_id: int, ended_at: int) -> None:
    """@brief 受理前核对汽车归属和整个预计驾驶区间的未扣日期余额。"""
    config = next(item for item in semantics.scenario.agents if item.agent_id == agent_id)
    if not config.owns_car:
        raise PreconditionError("CAR_REQUIRED", ["owns_car"],
                                public_reason="Driving requires a car owned by this persona.")
    agent = state.agents[agent_id]
    dates = driving_dates(state.current_time, ended_at, semantics.scenario.timezone)
    needed = sum(day != agent.last_driving_date for day in dates)
    if needed > agent.driving_days:
        raise PreconditionError("DRIVING_DAYS_EXHAUSTED", ["driving_days"],
                                public_reason="No driving days are available for this trip.")


def charge_driving_days(state: WorldState, semantics: ScenarioSemantics, agent_id: int,
                        started_at: int, ended_at: int) -> WorldState:
    """@brief 只对实际正驾驶秒数扣日，同日重复或零秒区间不修改世界。"""
    dates = driving_dates(started_at, ended_at, semantics.scenario.timezone)
    if not dates:
        return state
    agent = state.agents[agent_id]
    new_dates = tuple(day for day in dates if agent.last_driving_date is None or day > agent.last_driving_date)
    if not new_dates:
        return state
    if len(new_dates) > agent.driving_days:
        raise PreconditionError("DRIVING_DAYS_EXHAUSTED", ["driving_days"],
                                public_reason="No driving days are available for this trip.")
    changed = agent.model_copy(update={"driving_days": agent.driving_days - len(new_dates),
                                       "last_driving_date": dates[-1]}, deep=True)
    return state.model_copy(update={"agents": {**state.agents, agent_id: changed}}, deep=True)


def require_fuel_purchase(state: WorldState, semantics: ScenarioSemantics,
                          agent_id: int, request: PurchaseOrderRequest) -> None:
    """@brief 燃油必须由有车本人实际在加油站为自己购买，先于任何付款。"""
    fuel = [item for item in request.items if semantics.catalog.goods[item.goods_id].category == GoodsCategory.FUEL]
    if not fuel:
        return
    config = next(item for item in semantics.scenario.agents if item.agent_id == agent_id)
    valid = config.owns_car and request.fulfillment == "pickup" and request.app_id is None
    for item in fuel:
        place = semantics.catalog.locations[item.store_id]
        valid = (valid and place.kind == LocationKind.GAS_STATION
                 and present_location(state, agent_id) == item.store_id
                 and item.recipient_id == f"agent:{agent_id}")
    if not valid:
        raise PreconditionError("FUEL_PURCHASE_INVALID", ["fuel_purchase"],
            public_reason="Fuel must be bought for yourself while present at a gas station.")
