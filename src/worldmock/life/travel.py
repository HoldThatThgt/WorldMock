"""@brief 校验并消费 prepare 阶段冻结的 OSRM 路由；沿路线投影定位轨迹。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActivityState, AgentState, StartActivityRequest, WorldState
from worldmock.life.scenario import LifeScenario, RouteConfig
from worldmock.life.settlement import settle_money_transfer

#: environment_event kind=location_track（docs/dev/SPEC-life-runtime.md「导航与配送」定位轨迹；沿用 environment_event kind 机制）。
LOCATION_TRACK_KIND = "location_track"
#: 采样方法冻结名：匀速沿冻结路线几何折线插值（非人物决策，不产生 decision_started）。
TRACK_SAMPLING_METHOD = "constant_speed_polyline_interpolation"
#: 球面距离采用地球平均半径（米），与投影公式一并写入采样规则留痕。
TRACK_EARTH_RADIUS_METERS = 6371008.8


def route_for(scenario: LifeScenario, origin_id: str, request: StartActivityRequest) -> RouteConfig:
    """@brief 按起点、终点和方式选择唯一冻结路线。

    时长合同（计划时长 = 冻结自由流时长 × travel-time 分时系数）由引擎
    start 路径执行：route_for 只做身份选择，_start 在拿到出发时刻与目录
    语义后校验 ROUTE_DURATION_MISMATCH（rules 层无目录语义，不做该校验）。
    """
    matches = [route for route in scenario.routes if route.origin_id == origin_id
               and route.destination_id == request.destination_id and route.mode == request.travel_mode]
    if len(matches) != 1:
        logging.getLogger(__name__).error("Frozen route identity is missing or ambiguous")
        raise BusinessRuleError("FROZEN_ROUTE_NOT_FOUND")
    if matches[0].status != "reachable":
        reason = "ROUTE_UNREACHABLE" if matches[0].status == "unreachable" else "ROUTE_INFORMATION_UNKNOWN"
        logging.getLogger(__name__).error("Travel rejected: %s", reason)
        raise BusinessRuleError(reason)
    return matches[0]


def pay_travel_fare(state: WorldState, scenario: LifeScenario, agent_id: int,
                    route: RouteConfig) -> tuple[WorldState, dict]:
    """@brief 按可见的冻结路线报价预付交通费；费用不足时拒绝整个启程请求。"""
    payer = state.agents[agent_id].balance_account_id
    payee = scenario.transport_account_id
    fare = route.fare_fen
    if state.accounts[payer] < fare:
        raise BusinessRuleError("INSUFFICIENT_TRAVEL_FUNDS")
    changed = settle_money_transfer(state, payer, payee, fare,
                                   f"travel:depart:{route.route_id}")
    receipt = {"route_id": route.route_id, "fare_fen": fare, "currency": "CNY",
               "payer_account_id": payer, "payee_account_id": payee,
               "fare_source_kind": route.fare_source_kind, "payment_timing": "accepted_departure",
               "cancellation_refund_fen": 0,
               "resource_changes": {f"account:{payer}": -fare, f"account:{payee}": fare}}
    return changed, receipt


def validate_routes(scenario: LifeScenario, corpus_root: Path) -> None:
    """@brief 全量核验路线文件身份及 OSRM 返回的距离和时长。"""
    for route in scenario.routes:
        if route.status == "unknown":
            continue
        path = _safe_file(corpus_root, route.response_path)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != route.response_sha256:
            raise IntegrityError("Frozen route response hash mismatch")
        try:
            response = json.loads(content)
        except Exception as error:
            raise IntegrityError("Frozen route response is invalid") from error
        if route.status == "unreachable":
            if response.get("code") != "NoRoute":
                raise IntegrityError("Frozen unreachable route requires a NoRoute response")
            continue
        if response.get("code") != "Ok":
            raise IntegrityError("Frozen route response was not successful")
        if not response.get("routes"):
            raise IntegrityError("Frozen route response has no route")
        first = response["routes"][0]
        if abs(float(first["distance"]) - route.distance_meters) > 0.1:
            raise IntegrityError("Frozen route distance mismatch")
        if abs(float(first["duration"]) - route.duration_seconds) > 1:
            raise IntegrityError("Frozen route duration mismatch")


def update_travel(activity: ActivityState, route: RouteConfig, agent: AgentState) -> tuple[ActivityState, AgentState]:
    """@brief 按活动实际完成比例推进距离，到达前不改变人物地点。"""
    ratio = min(1, activity.elapsed_seconds / activity.planned_duration_seconds)
    changed = activity.model_copy(update={"route_progress_meters": route.distance_meters * ratio}, deep=True)
    if ratio < 1:
        return changed, agent
    arrived = agent.model_copy(update={"location_id": route.destination_id}, deep=True)
    return changed, arrived


@dataclass(frozen=True, slots=True)
class LocationTrackRequest:
    """@brief 一次定位轨迹投影的完整输入：人物、冻结路线、时间窗与采样间隔。"""

    agent_id: int
    route: RouteConfig
    departure_epoch: int
    arrival_epoch: int
    sample_interval_seconds: int


def sample_location_track(request: LocationTrackRequest, corpus_root: Path) -> list[EventDraft]:
    """@brief 沿冻结路线几何按固定间隔生成模拟时间戳的定位轨迹事件。

    定位采样不是人物的一次决策（docs/dev/SPEC-life-runtime.md「导航与配送」）：本函数只产出
    environment_event(kind=location_track)，不产生 decision_started 或
    observation；每个事件携带采样规则与路线引用，同输入确定性可重放。
    """
    geometry = _route_geometry(corpus_root, request.route)
    cumulative, total = _cumulative_distances(geometry)
    interval = request.sample_interval_seconds
    span = request.arrival_epoch - request.departure_epoch
    if interval < 1 or span <= 0:
        raise BusinessRuleError("LOCATION_TRACK_WINDOW_INVALID")
    count = (span - 1) // interval
    events: list[EventDraft] = []
    for index in range(1, count + 1):
        moment = request.departure_epoch + index * interval
        fraction = (moment - request.departure_epoch) / span
        lon, lat, distance = _interpolate(geometry, cumulative, total, fraction)
        events.append(EventDraft(
            event_id=_track_event_id(request, index), occurred_at=moment,
            event_type="environment_event", agent_id=request.agent_id,
            source_kind="environment", visibility="agent", data={
                "kind": LOCATION_TRACK_KIND,
                "route_id": request.route.route_id,
                "route_response_path": request.route.response_path,
                "route_response_sha256": request.route.response_sha256,
                "sample_index": index,
                "sample_count": count,
                "position_lonlat": [lon, lat],
                "distance_along_route_meters": distance,
                "sampling_rule": _sampling_rule(request, count),
            }))
    return events


def _sampling_rule(request: LocationTrackRequest, count: int) -> dict[str, Any]:
    """@brief 形成随事件留痕的采样规则（方法、间隔、时间窗与路线语义）。"""
    return {
        "method": TRACK_SAMPLING_METHOD,
        "sample_interval_seconds": request.sample_interval_seconds,
        "departure_epoch": request.departure_epoch,
        "arrival_epoch": request.arrival_epoch,
        "origin_id": request.route.origin_id,
        "destination_id": request.route.destination_id,
        "mode": request.route.mode.value,
        "sample_count": count,
    }


def _track_event_id(request: LocationTrackRequest, index: int) -> str:
    """@brief 由投影输入派生稳定事件身份，使同输入轨迹逐字节可重放。"""
    seed = (f"location_track:{request.agent_id}:{request.route.route_id}:"
            f"{request.departure_epoch}:{request.arrival_epoch}:"
            f"{request.sample_interval_seconds}:{index}")
    return hashlib.sha256(seed.encode()).hexdigest()


def _route_geometry(corpus_root: Path, route: RouteConfig) -> list[tuple[float, float]]:
    """@brief 读取并核验冻结路线响应，返回 GeoJSON LineString 坐标序列。"""
    content = _safe_file(corpus_root, route.response_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != route.response_sha256:
        raise IntegrityError("Frozen route response hash mismatch")
    try:
        response = json.loads(content)
        geometry = response["routes"][0]["geometry"]
        coordinates = geometry["coordinates"] if geometry["type"] == "LineString" else None
    except Exception as error:
        raise IntegrityError("Frozen route response is invalid") from error
    if response.get("code") != "Ok" or not isinstance(coordinates, list) or len(coordinates) < 2:
        raise IntegrityError("Frozen route response geometry is missing")
    return [(float(lon), float(lat)) for lon, lat in coordinates]


def _haversine_meters(first: tuple[float, float], second: tuple[float, float]) -> float:
    """@brief 按地球平均半径计算两经纬度点的球面距离（米）。"""
    lon1, lat1 = math.radians(first[0]), math.radians(first[1])
    lon2, lat2 = math.radians(second[0]), math.radians(second[1])
    half = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2) ** 2
    return 2 * TRACK_EARTH_RADIUS_METERS * math.asin(math.sqrt(half))


def _cumulative_distances(geometry: list[tuple[float, float]]) -> tuple[list[float], float]:
    """@brief 计算折线各顶点的累计球面距离与总长（米）。"""
    cumulative = [0.0]
    for index in range(1, len(geometry)):
        cumulative.append(cumulative[-1] + _haversine_meters(geometry[index - 1], geometry[index]))
    return cumulative, cumulative[-1]


def _interpolate(geometry: list[tuple[float, float]], cumulative: list[float],
                 total: float, fraction: float) -> tuple[float, float, float]:
    """@brief 按距离比例在折线段内线性插值，返回经纬度与沿线距离。"""
    target = min(max(fraction, 0.0), 1.0) * total
    for index in range(1, len(geometry)):
        if target <= cumulative[index] or index == len(geometry) - 1:
            span = cumulative[index] - cumulative[index - 1]
            local = 0.0 if span <= 0 else (target - cumulative[index - 1]) / span
            local = min(max(local, 0.0), 1.0)
            lon1, lat1 = geometry[index - 1]
            lon2, lat2 = geometry[index]
            return (round(lon1 + (lon2 - lon1) * local, 7), round(lat1 + (lat2 - lat1) * local, 7),
                    round(target, 3))
    raise IntegrityError("Frozen route geometry interpolation is out of range")


def _safe_file(root: Path, relative: str) -> Path:
    """@brief 将内容路径限制在冻结语料根目录。"""
    base = Path(root).resolve()
    candidate = base / relative
    path = candidate.resolve()
    if _has_symlink(candidate, base) or base not in path.parents or not path.is_file():
        raise IntegrityError("Corpus file path is invalid")
    return path


def _has_symlink(path: Path, root: Path) -> bool:
    """@brief 在解析前拒绝文件及父目录符号链接。"""
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return current != root
