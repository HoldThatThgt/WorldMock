"""@brief 按完整登记图计划行程、预付车费并推进可追溯位置。"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from worldmock.catalog.graph import Graph
from worldmock.catalog.models import Catalog
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.exchange.constants import TRAVEL_MAX_SECONDS
from worldmock.exchange.operators import evaluate_travel_fare, travel_seconds
from worldmock.life.models import ActivityState, AgentState, WorldState
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.travel_methods import Method, travel_method


@dataclass(frozen=True, slots=True)
class Trip:
    """@brief 一个由完整路径和固定规则唯一确定的出行计划。"""

    #: 起点、终点和出行方式形成的稳定行程身份。
    trip_id: str
    #: 包含起终点与全部中间地点的路径。
    path: tuple[str, ...]
    #: 最短路的整数米数。
    distance_meters: int
    #: 按出行方式的速度与天气系数向上取整的计划秒数。
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


def plan_trip(graph: Graph, origin: str, destination: str, method: Method,
              weather_factor: float = 1.0) -> Trip:
    """@brief 从完整图计算唯一路径，用时与车费按所选出行方式的速度和价格由系统算子给出。

    天气系数对自带的步行和数据集定义的全部方式生效；车费与天气无关。
    """
    path = graph.path(origin, destination)
    meters = graph.distance_meters(origin, destination)
    duration = travel_seconds(method.speed_meters_per_second, meters, weather_factor)
    if not 1 <= duration <= TRAVEL_MAX_SECONDS:
        logging.error("Travel route duration is outside the supported interval")
        raise BusinessRuleError("ROUTE_DURATION_INVALID")
    fare = evaluate_travel_fare(method.start_price_fen, method.price_per_km_fen, meters)
    return Trip(f"{origin}->{destination}:{method.travel_method_id}", path, meters, duration, fare)


def travel_activity_data(trip: Trip, method: Method, weather_factor: float) -> dict:
    """@brief 活动与事件持有完整图路径、米数、行程身份、受理时的天气系数与出行方式的代谢当量键。"""
    return {"trip_id": trip.trip_id, "path": list(trip.path), "distance_meters": trip.distance_meters,
            "origin_id": trip.origin_id, "destination_id": trip.destination_id, "weather_factor": weather_factor,
            "exertion": method.exertion}


def pay_travel_fare(state: WorldState, agent_id: int, trip: Trip) -> tuple[WorldState, dict]:
    """@brief 按唯一行程计划经既有货币账本付款，步行零分不改变余额。"""
    payer = state.agents[agent_id].balance_account_id
    payee = "external:scenario"
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


def persisted_trip(catalog: Catalog, activity: ActivityState) -> Trip:
    """@brief 恢复与推进按活动原始起点和同一出行方式重算同一行程，完整身份、路径和代谢当量键必须匹配。"""
    method = travel_method(catalog, activity.travel_method_id)
    trip = plan_trip(catalog.graph, activity.location_id, activity.destination_id, method,
                     activity.data["weather_factor"])
    if (activity.data.get("trip_id") != trip.trip_id or tuple(activity.data.get("path", ())) != trip.path
            or activity.data.get("exertion") != method.exertion
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
