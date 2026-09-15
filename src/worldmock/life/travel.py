"""@brief 校验并消费 prepare 阶段冻结的 OSRM 路由。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.models import ActivityState, AgentState, StartActivityRequest, WorldState
from worldmock.life.scenario import LifeScenario, RouteConfig


def route_for(scenario: LifeScenario, origin_id: str, request: StartActivityRequest) -> RouteConfig:
    """@brief 按起点、终点和方式选择唯一冻结路线。"""
    matches = [route for route in scenario.routes if route.origin_id == origin_id
               and route.destination_id == request.destination_id and route.mode == request.travel_mode]
    if len(matches) != 1:
        raise BusinessRuleError("FROZEN_ROUTE_NOT_FOUND")
    route = matches[0]
    if request.planned_duration_seconds != route.duration_seconds:
        raise BusinessRuleError("ROUTE_DURATION_MISMATCH")
    return route


def pay_travel_fare(state: WorldState, scenario: LifeScenario, agent_id: int,
                    route: RouteConfig) -> tuple[WorldState, dict]:
    """@brief 按可见的冻结路线报价预付交通费；费用不足时拒绝整个启程请求。"""
    payer = state.agents[agent_id].balance_account_id
    payee = scenario.transport_account_id
    fare = route.fare_fen
    if state.accounts[payer] < fare:
        raise BusinessRuleError("INSUFFICIENT_TRAVEL_FUNDS")
    accounts = dict(state.accounts)
    accounts[payer] -= fare
    accounts[payee] += fare
    receipt = {"route_id": route.route_id, "fare_fen": fare, "currency": "CNY",
               "payer_account_id": payer, "payee_account_id": payee,
               "fare_source_kind": route.fare_source_kind, "payment_timing": "accepted_departure",
               "cancellation_refund_fen": 0,
               "resource_changes": {f"account:{payer}": -fare, f"account:{payee}": fare}}
    return state.model_copy(update={"accounts": accounts}, deep=True), receipt


def validate_routes(scenario: LifeScenario, corpus_root: Path) -> None:
    """@brief 全量核验路线文件身份及 OSRM 返回的距离和时长。"""
    for route in scenario.routes:
        path = _safe_file(corpus_root, route.response_path)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != route.response_sha256:
            raise IntegrityError("Frozen route response hash mismatch")
        try:
            response = json.loads(content)
            first = response["routes"][0]
        except Exception as error:
            raise IntegrityError("Frozen route response is invalid") from error
        if response.get("code") != "Ok":
            raise IntegrityError("Frozen route response was not successful")
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
