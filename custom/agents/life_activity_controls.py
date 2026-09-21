"""@brief 人物对活动集合的严格读取、独立控制与资源资格筛选。"""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from typing import Any, Mapping

from pydantic import BaseModel

from worldmock.catalog.affordances import default_affordances, RESOURCE_CLAIM_SLOTS
from worldmock.life.models import activity_start_tool

_SOCIAL_KINDS = frozenset({"chat", "call"})
_CONTROL_KEYS = frozenset({"activity_id", "kind", "status", "title", "progress",
                           "elapsed_seconds", "planned_duration_seconds", "data"})
_START_TOOLS = frozenset({"start_basic_activity", "start_targeted_activity", "start_travel",
                          "consume", "start_social_activity"})


def _invalid(message: str) -> None:
    """@brief 记录观察完整性错误并终止当前人物准备。"""
    logging.error(message)
    raise RuntimeError(message)


def _visible_activity_controls(data: dict, contract: Mapping, energy_limits: dict) -> dict:
    """@brief 从完整 ACTIVE 与 PAUSED 集合冻结每项的真实控制资格。"""
    active = _active_rows(data, contract)
    paused_ids, resumable, paused_kinds = _visible_paused_activity_ids(data)
    if set(active) & set(paused_ids):
        _invalid("LifeAgent activity is both active and paused")
    occupied = data.get("occupied_resources")
    if (not isinstance(occupied, list) or not all(isinstance(value, str) for value in occupied)
            or occupied != sorted(set(occupied)) or not set(occupied) <= RESOURCE_CLAIM_SLOTS):
        _invalid("LifeAgent occupied resources are invalid")
    resumable = _resource_resumable(data["paused_activities"], resumable, frozenset(occupied))
    controls = {"active": active, "paused_ids": paused_ids, "resumable_paused_ids": resumable,
                "menu_resumable_paused_ids": resumable, "paused_activity_kinds": paused_kinds,
                "maximum_duration_seconds_by_kind": energy_limits,
                "occupied_resources": frozenset(occupied), "game_session_id": None, "game_commands": (),
                "continuable_ids": tuple(identity for identity, row in active.items()
                                         if row["kind"] in contract and contract[row["kind"]]["continuable"]),
                "watch_ids": tuple(identity for identity, row in active.items() if row["kind"] == "watch")}
    games = [row for row in active.values() if row["kind"] == "play_game"]
    if len(games) > 1:
        _invalid("LifeAgent has multiple active games")
    if games:
        session_id, commands = _visible_game_control(data, games[0])
        controls.update(game_session_id=session_id, game_commands=commands)
    return controls


def _resource_resumable(rows: list, identities: tuple, occupied: frozenset) -> tuple:
    """@brief 暂停活动仍可取消，恢复必须与当前活动资源相容。"""
    allowed = []
    for row in rows:
        claims = row.get("resource_claims")
        if not isinstance(claims, list) or not all(isinstance(value, str) for value in claims):
            _invalid("LifeAgent paused activity resources are invalid")
        if set(claims) != claims_for_kind(row["kind"], row.get("travel_mode")):
            _invalid("LifeAgent paused activity resource declaration is inconsistent")
        if row["activity_id"] in identities and not occupied & set(claims):
            allowed.append(row["activity_id"])
    return tuple(sorted(allowed))


def _active_rows(data: dict, contract: Mapping) -> dict[str, dict]:
    """@brief 逐项核对八键控制行；空集合和社交边界具有明确语义。"""
    rows = data.get("active_activities")
    if not isinstance(rows, list):
        _invalid("LifeAgent active_activities must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _CONTROL_KEYS:
            _invalid("LifeAgent active activity control fields are invalid")
        identity, kind = row["activity_id"], row["kind"]
        if not isinstance(identity, str) or not identity.strip() or identity in result:
            _invalid("LifeAgent active activity identity is invalid or duplicated")
        if row["status"] != "active" or not isinstance(kind, str) or kind not in {*contract, *_SOCIAL_KINDS}:
            _invalid("LifeAgent active activity state or kind is invalid")
        times = (row["elapsed_seconds"], row["planned_duration_seconds"])
        if (any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in times)
                or times[1] <= 0 or times[0] >= times[1] or not isinstance(row["data"], dict)
                or row["title"] is not None and not isinstance(row["title"], str)
                or not isinstance(row["progress"], str)):
            _invalid("LifeAgent active activity progress is invalid")
        result[identity] = deepcopy(row)
    return result


def _visible_game_control(data: dict, activity: dict) -> tuple[str, tuple[str, ...]]:
    """@brief 游戏命令只绑定点名的真实 ACTIVE 会话。"""
    session_id = activity["data"].get("game_session_id")
    games = data.get("games")
    if not isinstance(session_id, str) or not session_id or not isinstance(games, dict):
        _invalid("LifeAgent active game session is invalid")
    progress = games.get(session_id)
    if not isinstance(progress, dict) or progress.get("session_id") != session_id:
        _invalid("LifeAgent active game progress is unavailable")
    if progress.get("won") is not False or progress.get("lost") is not False:
        _invalid("LifeAgent active game progress is terminal")
    commands = progress.get("admissible_commands")
    if not isinstance(commands, list) or not all(isinstance(item, str) and item.strip() for item in commands):
        _invalid("LifeAgent active game commands are invalid")
    return session_id, tuple(sorted(set(commands)))


def _visible_paused_activity_ids(
    data: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    """@brief 校验暂停活动身份，并区分当前能量允许恢复的闭集。"""
    activities = data.get("paused_activities")
    if not isinstance(activities, list):
        _invalid("LifeAgent paused_activities must be a list")
    identities = []
    resumable = []
    kinds = {}
    for activity in activities:
        if not isinstance(activity, dict) or activity.get("status") != "paused":
            _invalid("LifeAgent paused activity state is invalid")
        activity_id = activity.get("activity_id")
        if not isinstance(activity_id, str) or not activity_id.strip():
            _invalid("LifeAgent paused activity identity is invalid")
        kind = activity.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            logging.error("LifeAgent paused activity kind is invalid")
            _invalid("LifeAgent paused activity kind is invalid")
        remaining = activity.get("remaining_duration_seconds")
        cost = activity.get("minimum_energy_cost_kcal")
        feasible = activity.get("resume_energy_feasible")
        if (isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0 or not isinstance(feasible, bool)):
            _invalid("LifeAgent paused activity energy feasibility is invalid")
        identities.append(activity_id)
        kinds[activity_id] = kind
        if feasible:
            resumable.append(activity_id)
    if len(identities) != len(set(identities)):
        _invalid("LifeAgent paused activity identities are duplicated")
    return tuple(sorted(identities)), tuple(sorted(resumable)), kinds


def _operations(identity: str, controls: dict) -> tuple[str, ...]:
    """@brief 为一个可见活动身份返回当前合法状态操作。"""
    active = controls["active"].get(identity)
    if active is not None:
        return ("cancel",) if active["kind"] in _SOCIAL_KINDS else ("pause", "cancel")
    if identity in controls["paused_ids"]:
        return ("resume", "cancel") if identity in controls["menu_resumable_paused_ids"] else ("cancel",)
    return ()


def _bind_activity_control_schemas(schemas: list[dict], controls: dict) -> None:
    """@brief 每种控制工具绑定自己的合法活动身份及命令集合。"""
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    if "set_activity_state" in functions:
        identifiers = sorted({*controls["active"], *controls["paused_ids"]})
        properties = functions["set_activity_state"]["parameters"]["properties"]
        properties["activity_id"]["enum"] = identifiers
        properties["operation"]["enum"] = sorted({operation for identity in identifiers
                                                    for operation in _operations(identity, controls)})
    for name, key in (("continue_activity", "continuable_ids"), ("change_playback_speed", "watch_ids")):
        if name in functions:
            functions[name]["parameters"]["properties"]["activity_id"]["enum"] = list(controls[key])
    if "game_action" in functions:
        properties = functions["game_action"]["parameters"]["properties"]
        properties["game_session_id"]["enum"] = [controls["game_session_id"]]
        properties["command"]["enum"] = list(controls["game_commands"])


def _validate_activity_control(action: str, request: BaseModel, controls: dict) -> str:
    """@brief 参数必须与本轮点名活动的状态、类型及游戏命令准确配对。"""
    if action == "set_activity_state":
        if request.operation not in _operations(request.activity_id, controls):
            return "set_activity_state is not valid for the visible activity state"
    elif action in {"continue_activity", "change_playback_speed"}:
        key = "continuable_ids" if action == "continue_activity" else "watch_ids"
        if request.activity_id not in controls[key]:
            return f"{action} activity_id is not available for this operation"
    elif action == "game_action":
        if (request.game_session_id != controls["game_session_id"]
                or request.command not in controls["game_commands"]):
            return "game_action is not valid for the current visible game state"
    return ""


def claims_for_kind(kind: str, travel_mode: str | None = None) -> frozenset[str]:
    """@brief 工具与环境从同一注册表读取资源，社交保留已冻结位置和注意力合同。"""
    if kind in _SOCIAL_KINDS:
        return frozenset({"position", "attention"})
    normalized = "lifestyle" if kind.startswith("lifestyle:") else kind
    return default_affordances().resource_claims(normalized, travel_mode)


def request_claims(request: BaseModel) -> frozenset[str]:
    """@brief 五种持续启动请求只映射为其原有活动资源。"""
    tool = activity_start_tool(request)
    if tool == "start_travel":
        return claims_for_kind("travel", request.travel_mode)
    if tool == "consume":
        return claims_for_kind("consume")
    kind = request.kind if tool == "start_social_activity" else request.activity_type
    return claims_for_kind(str(kind))


def start_resource_error(action: str, request: BaseModel, occupied: frozenset[str]) -> str:
    """@brief 宿主仅以声明资源交集校验单项与组合，不维护组合白名单。"""
    if action == "start_parallel_activities":
        for item in request.activities:
            claims = request_claims(item)
            if occupied & claims:
                return "start_parallel_activities contains conflicting activity resources"
            occupied = occupied | claims
    elif action in _START_TOOLS and occupied & request_claims(request):
        return "activity resources conflict with the visible active activities"
    return ""


def bind_parallel_schema(schemas: list[dict]) -> None:
    """@brief 组合子项复用本轮已经绑定菜单、目标和时长的真实单项 Schema。"""
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    if "start_parallel_activities" not in functions:
        return
    children = [deepcopy(functions[name]["parameters"]) for name in sorted(_START_TOOLS) if name in functions]
    functions["start_parallel_activities"]["parameters"]["properties"]["activities"]["items"] = {"anyOf": children}


def filter_starts(occupied: frozenset[str], targets: dict, contracts: dict, basic_types: tuple) -> tuple:
    """@brief 用当前资源并集收窄各启动入口，目标身份仍沿用本轮冻结观察。"""
    targets = {kind: values if not claims_for_kind(kind) & occupied else ()
               for kind, values in targets.items()}
    contracts = dict(contracts)
    contracts["start_travel"] = tuple(route for route in contracts["start_travel"]
                                      if not claims_for_kind("travel", route[1]) & occupied)
    if claims_for_kind("consume") & occupied:
        contracts["consume"] = {}
    if claims_for_kind("chat") & occupied:
        contracts["start_social_activity"] = {}
    basic_types = tuple(kind for kind in basic_types if not claims_for_kind(kind) & occupied)
    return targets, contracts, basic_types


def parallel_available(basic_types: tuple, targets: dict, contracts: dict) -> bool:
    """@brief 至少两个当前可见启动形状具有不相交资源时才提供组合入口。"""
    choices = [claims_for_kind(kind) for kind in basic_types]
    choices.extend(claims_for_kind(kind) for kind, identities in targets.items() if identities)
    choices.extend(claims_for_kind("travel", route[1]) for route in contracts["start_travel"])
    if contracts["consume"]:
        choices.append(claims_for_kind("consume"))
    if contracts["start_social_activity"]:
        choices.append(claims_for_kind("chat"))
    return any(not first & second for index, first in enumerate(choices) for second in choices[index + 1:])
