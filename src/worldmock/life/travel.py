"""@brief 按完整登记图计划行程、预付车费并推进可追溯位置。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.catalog.graph import Graph
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError, reject_value
from worldmock.exchange.operators import evaluate_travel_fare, travel_seconds
from worldmock.life.models import ActivityStartParams, ActivityState, AgentState, TravelMode, WorldState
from worldmock.life.settlement import settle_money_transfer


@dataclass(frozen=True, slots=True)
class Trip:
    """@brief 一个由完整路径和固定规则唯一确定的出行计划。"""

    #: 起点、终点和出行方式形成的稳定行程身份。
    trip_id: str
    #: 包含起终点与全部中间地点的路径。
    path: tuple[str, ...]
    #: 最短路的整数米数。
    distance_meters: int
    #: 按方式速度向上取整的计划秒数。
    duration_seconds: int
    #: 出发时支付的整数分数。
    fare_fen: int

    @property
    def origin_id(self) -> str:
        """@brief 起点只取完整路径首项，不存第二份身份。"""
        return self.path[0]

    @property
    def destination_id(self) -> str:
        """@brief 终点只取完整路径末项。"""
        return self.path[-1]


def plan_trip(graph: Graph, origin: str, destination: str, mode: TravelMode | str) -> Trip:
    """@brief 从完整图计算唯一路径，计时与计费只消费系统算子。"""
    try:
        selected = TravelMode(mode)
    except ValueError:
        reject_value("Travel mode is outside the fixed set")
    path = graph.path(origin, destination)
    meters = graph.distance_meters(origin, destination)
    return Trip(f"{origin}->{destination}:{selected.value}", path, meters,
                travel_seconds(selected.value, meters), evaluate_travel_fare(selected.value, meters))


def planned_travel(scenario, semantics, origin_id: str,
                   request: ActivityStartParams, now: int) -> tuple[Trip, int]:
    """@brief 工具报价和受理共用完整图计划，同地点出行与未选目的地直接拒绝。"""
    if request.destination_id == origin_id:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["already_at_destination"],
                                public_reason="You are already at this place.")
    if request.destination_id not in {place.location_id for place in scenario.locations}:
        raise BusinessRuleError("DESTINATION_NOT_FOUND")
    trip = plan_trip(semantics.catalog.graph, origin_id, request.destination_id, request.travel_mode)
    return trip, trip.duration_seconds


def require_travel_duration(scenario, semantics, origin_id: str,
                            request: ActivityStartParams, now: int) -> Trip:
    """@brief 出发前按同一完整图重新核对请求时长，不能接受外部路线报价。"""
    trip, expected = planned_travel(scenario, semantics, origin_id, request, now)
    if request.planned_duration_seconds != expected:
        raise BusinessRuleError("ROUTE_DURATION_MISMATCH")
    return trip


def travel_activity_data(trip: Trip) -> dict:
    """@brief 活动与事件持有完整图路径、米数和行程身份，禁止旧路线身份。"""
    return {"trip_id": trip.trip_id, "path": list(trip.path), "distance_meters": trip.distance_meters,
            "origin_id": trip.origin_id, "destination_id": trip.destination_id}


def pay_travel_fare(state: WorldState, scenario, agent_id: int, trip: Trip) -> tuple[WorldState, dict]:
    """@brief 按唯一行程计划经既有货币账本付款，步行零分不改变余额。"""
    payer = state.agents[agent_id].balance_account_id
    payee = scenario.transport_account_id
    fare = trip.fare_fen
    if state.accounts[payer] < fare:
        raise BusinessRuleError("INSUFFICIENT_TRAVEL_FUNDS")
    changed = settle_money_transfer(state, payer, payee, fare, f"travel:depart:{trip.trip_id}")
    receipt = {"trip_id": trip.trip_id, "fare_fen": fare, "currency": "CNY",
               "payer_account_id": payer, "payee_account_id": payee,
               "payment_timing": "accepted_departure", "cancellation_refund_fen": 0,
               "resource_changes": {f"account:{payer}": -fare, f"account:{payee}": fare}}
    return changed, receipt


def update_travel(activity: ActivityState, trip: Trip, agent: AgentState) -> tuple[ActivityState, AgentState]:
    """@brief 按实际完成比例推进米数，到达前人物地点始终保留原始起点。"""
    ratio = min(1, activity.elapsed_seconds / activity.planned_duration_seconds)
    changed = activity.model_copy(update={"route_progress_meters": trip.distance_meters * ratio}, deep=True)
    if ratio < 1:
        return changed, agent
    arrived = agent.model_copy(update={"location_id": trip.destination_id}, deep=True)
    return changed, arrived


def persisted_trip(graph: Graph, activity: ActivityState) -> Trip:
    """@brief 恢复与推进按活动原始起点重算同一行程，完整身份和路径必须匹配。"""
    trip = plan_trip(graph, activity.location_id, activity.destination_id, activity.travel_mode)
    if (activity.data.get("trip_id") != trip.trip_id or tuple(activity.data.get("path", ())) != trip.path
            or activity.data.get("distance_meters") != trip.distance_meters
            or activity.data.get("origin_id") != trip.origin_id
            or activity.data.get("destination_id") != trip.destination_id
            or activity.planned_duration_seconds != trip.duration_seconds):
        raise IntegrityError("Persisted travel plan does not match the published graph")
    return trip


def record_arrival(activity: ActivityState, agent: AgentState) -> None:
    """@brief 行程完成后记录已经结算的位置事实，场景不再声明访问资格。"""
    if activity.elapsed_seconds >= activity.planned_duration_seconds:
        activity.data["arrival"] = {"location_id": agent.location_id, "access_status": "allowed"}
