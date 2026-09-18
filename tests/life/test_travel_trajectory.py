"""定位轨迹投影测试（规格 L454 / GAP-1）：沿冻结路线与模拟时间的确定性采样。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import canonical_json
from worldmock.life.scenario import load_scenario
from worldmock.life.travel import (
    TRACK_SAMPLING_METHOD,
    LocationTrackRequest,
    sample_location_track,
)

_CORPUS = Path("corpora")


def _request(route, interval: int = 90, span: int = 720) -> LocationTrackRequest:
    """按冻结路线与给定采样间隔构造一次轨迹投影请求。"""
    return LocationTrackRequest(agent_id=1, route=route, departure_epoch=1_000_000,
                                arrival_epoch=1_000_000 + span, sample_interval_seconds=interval)


def _route(route_id: str):
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    return next(item for item in scenario.routes if item.route_id == route_id)


def _geometry(route) -> list[tuple[float, float]]:
    """读取冻结响应的 LineString 几何。"""
    response = json.loads((_CORPUS / route.response_path).read_text())
    assert response["code"] == "Ok"
    return [(float(lon), float(lat)) for lon, lat in response["routes"][0]["geometry"]["coordinates"]]


def _distance_to_polyline(point: tuple[float, float], geometry) -> float:
    """计算点到折线最近段的球面距离（米），用于在线断言。"""
    best = float("inf")
    for index in range(1, len(geometry)):
        best = min(best, _point_segment_meters(point, geometry[index - 1], geometry[index]))
    return best


def _point_segment_meters(point, a, b) -> float:
    """点到线段的近似球面距离（局部平面投影足够判零）。"""
    px, py = point[0] - a[0], point[1] - a[1]
    qx, qy = b[0] - a[0], b[1] - a[1]
    dot = px * qx + py * qy
    length = qx * qx + qy * qy
    t = 0.0 if length == 0 else min(1.0, max(0.0, dot / length))
    dx, dy = px - t * qx, py - t * qy
    return math.hypot(dx, dy) * 111_320.0


def test_track_sampling_is_deterministic_byte_for_byte() -> None:
    """同一输入两次投影逐字节相同（含事件身份，可重放）。"""
    events = sample_location_track(_request(_route("home-office-driving")), _CORPUS)
    repeated = sample_location_track(_request(_route("home-office-driving")), _CORPUS)
    assert events and len(events) == len(repeated)
    assert [canonical_json(event.model_dump(mode="json")) for event in events] == \
        [canonical_json(event.model_dump(mode="json")) for event in repeated]


def test_track_samples_lie_on_the_frozen_route_geometry() -> None:
    """每个采样点都落在冻结路线几何折线上（插值不偏离道路）。"""
    route = _route("home-market-walking")
    geometry = _geometry(route)
    for event in sample_location_track(_request(route, interval=60), _CORPUS):
        lon, lat = event.data["position_lonlat"]
        assert _distance_to_polyline((lon, lat), geometry) < 1.0


def test_track_timestamps_are_strictly_monotonic_within_the_window() -> None:
    """模拟时间戳严格递增且都落在出发与到达之间。"""
    request = _request(_route("office-home-driving"), interval=45, span=600)
    stamps = [event.occurred_at for event in sample_location_track(request, _CORPUS)]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    assert all(request.departure_epoch < stamp < request.arrival_epoch for stamp in stamps)
    assert stamps[0] == request.departure_epoch + 45


def test_track_projection_is_not_a_person_decision() -> None:
    """定位采样只产出 environment_event(kind=location_track)，不产生决策或观察。"""
    events = sample_location_track(_request(_route("market-office-walking")), _CORPUS)
    assert events
    for event in events:
        assert event.event_type == "environment_event"
        assert event.data["kind"] == "location_track"
        assert event.decision_id is None and event.action_id is None
        assert event.source_kind == "environment"


def test_track_events_carry_sampling_rule_and_route_reference() -> None:
    """事件留采样规则（方法/间隔/时间窗）与路线引用（id/路径/哈希）。"""
    route = _route("home-office-driving")
    request = _request(route, interval=120, span=960)
    for event in sample_location_track(request, _CORPUS):
        rule = event.data["sampling_rule"]
        assert rule["method"] == TRACK_SAMPLING_METHOD
        assert rule["sample_interval_seconds"] == 120
        assert rule["departure_epoch"] == request.departure_epoch
        assert rule["arrival_epoch"] == request.arrival_epoch
        assert rule["origin_id"] == route.origin_id and rule["destination_id"] == route.destination_id
        assert event.data["route_id"] == route.route_id
        assert event.data["route_response_path"] == route.response_path
        assert event.data["route_response_sha256"] == route.response_sha256


def test_track_projection_rejects_invalid_windows_and_tampered_routes(tmp_path) -> None:
    """非正时间窗或哈希漂移的路线响应在投影前 fail-closed。"""
    route = _route("home-office-driving")
    zero_interval = LocationTrackRequest(agent_id=1, route=route, departure_epoch=0,
                                         arrival_epoch=600, sample_interval_seconds=0)
    with pytest.raises(BusinessRuleError, match="LOCATION_TRACK_WINDOW_INVALID"):
        sample_location_track(zero_interval, _CORPUS)
    inverted = LocationTrackRequest(agent_id=1, route=route, departure_epoch=600,
                                    arrival_epoch=0, sample_interval_seconds=60)
    with pytest.raises(BusinessRuleError, match="LOCATION_TRACK_WINDOW_INVALID"):
        sample_location_track(inverted, _CORPUS)
    tampered = route.model_copy(update={"response_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="hash mismatch"):
        sample_location_track(_request(tampered), _CORPUS)


def test_sparse_interval_longer_than_trip_yields_no_events() -> None:
    """采样间隔覆盖整段行程时不产生轨迹事件（边界合法，不虚构采样点）。"""
    events = sample_location_track(_request(_route("office-market-driving"), interval=3600,
                                            span=300), _CORPUS)
    assert events == []
