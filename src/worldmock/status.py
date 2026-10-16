"""@brief 只读查询已确认世界与运行统计，不改变活动或推进时钟。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json

logger = logging.getLogger(__name__)


def write_status(run_dir: Path, status: str, phase: str, error_type: str | None = None) -> None:
    """@brief 在明确的运行边界写单份状态，生活阶段由环境调用此入口更新。"""
    if status not in {"created", "running", "completed", "failed", "interrupted"}:
        raise IntegrityError("Unknown run status")
    if phase not in {"deciding", "resolving", "advancing", "checkpointing", "stopped"}:
        raise IntegrityError("Unknown run phase")
    atomic_write(run_dir / "status.json", canonical_json({"status": status, "phase": phase, "error_type": error_type}))


def read_status(run_dir: Path) -> dict:
    """@brief 使用 SQLite 只读连接查询提交状态，模型调用数量与行为数量分开；运行目录的 JSON 文件一律按 UTF-8 读。"""
    root = run_dir.resolve()
    database = _run_database(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        state = json.loads(connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0])
        counts = dict(connection.execute(
            "SELECT json_extract(body,'$.event_type'),count(*) FROM events GROUP BY json_extract(body,'$.event_type')"))
        points = [json.loads(row[0])["checkpoint_id"] for row in connection.execute(
            "SELECT body FROM checkpoints ORDER BY rowid DESC LIMIT 5")]
        active = [activity for activity in _world_activities(state) if activity["status"] in ("active", "paused")]
        result = {"run_id": manifest["run_id"], "state_version": state["version"],
                  "current_time": datetime.fromtimestamp(state["current_time"],
                                                         ZoneInfo(manifest["window"]["timezone"])).isoformat(),
                  "active_activities": active, "event_counts": counts, "recent_checkpoints": points}
        status_path, receipt_path = root / "status.json", root / "result.json"
        result["runtime"] = (json.loads(status_path.read_text(encoding="utf-8"))
                             if status_path.exists() else None)
        result["token_stats"] = (json.loads(receipt_path.read_text(encoding="utf-8"))["token_stats"]
                                 if receipt_path.exists() else None)
        result["observed_model_usage"] = _observed_usage(connection)
        result["people"] = _people_status(connection, state)
        result["wall_time"] = _wall_time(connection, result["runtime"])
        return result
    finally:
        connection.close()


def observed_model_usage(run_dir: Path) -> list[dict]:
    """@brief 从运行库里已落盘的模型响应按模型累计用量，运行回执的 token_stats 与 status 共用这一个来源。

    :param run_dir: 运行目录；只读打开其中的 footprints.sqlite，用完关闭。
    :returns: 每个模型一行，按模型名升序（无模型名的排最前）；没有响应时为空数组。
    :raises IntegrityError: 运行目录里没有数据库。
    """
    database = _run_database(run_dir.resolve())
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        return _observed_usage(connection)
    finally:
        connection.close()


def _world_activities(state: dict) -> list[dict]:
    """@brief 已存世界的活动数组；不是数组的世界由更早的引擎写下，状态查询不读它，写英文错误日志并拒绝。"""
    activities = state["activities"]
    if not isinstance(activities, list):
        logger.error("Run directory was written by an older engine: world activities are not an array")
        raise ValueError("Run directory was written by an older engine: world activities are not an array")
    return activities


def _run_database(root: Path) -> Path:
    """@brief 运行目录里的数据库路径；不存在时写英文错误日志并拒绝。"""
    database = root / "footprints.sqlite"
    if not database.is_file():
        logger.error("Run database does not exist: %s", root)
        raise IntegrityError("Run database does not exist")
    return database


def _observed_usage(connection: sqlite3.Connection) -> list[dict]:
    """@brief 直接累计已保存提供商响应中的用量，按模型名排出确定的行序；未知响应不伪装成零 token。"""
    rows = connection.execute("""
        SELECT json_extract(body,'$.data.response.model'), count(*),
        count(json_extract(body,'$.data.response.usage.prompt_tokens')),
        sum(json_extract(body,'$.data.response.usage.prompt_tokens')),
        sum(json_extract(body,'$.data.response.usage.completion_tokens')),
        sum(json_extract(body,'$.data.response.usage.total_tokens'))
        FROM events WHERE json_extract(body,'$.event_type')='model_response'
        AND json_extract(body,'$.data.phase')='response'
        GROUP BY json_extract(body,'$.data.response.model')
        ORDER BY json_extract(body,'$.data.response.model')
    """)
    return [{"model": model, "responses": count, "responses_with_usage": known,
             "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total}
            for model, count, known, input_tokens, output_tokens, total in rows]


def _people_status(connection: sqlite3.Connection, state: dict) -> list[dict]:
    """@brief 显示每个人物已确认活动、最近业务结果与已排定的下一决策边界。"""
    people = []
    for person in state["agents"].values():
        identity = person["agent_id"]
        activities = sorted((item for item in state["activities"]
                             if item["agent_id"] == identity and item["status"] == "active"),
                            key=lambda item: (item["started_at"], item["activity_id"]))
        rows = connection.execute("""
            SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
            AND json_extract(body,'$.event_type') IN ('action_accepted','action_rejected','action_result')
            ORDER BY event_seq DESC LIMIT 3
        """, (identity,))
        results = [json.loads(row[0]) for row in rows]
        people.append({"agent_id": identity, "location_id": person["location_id"], "active_activities": activities,
                       "recent_action_results": results,
                       "next_decision_at": _decision_boundary(state, person)})
    return people


def _decision_boundary(state: dict, person: dict) -> int | None:
    """@brief 直接读取环境持久化的真实决策边界，不在状态界面另算一套生活调度。"""
    if state["current_time"] >= state["end_time"]:
        return None
    scheduled = person["next_decision_at"]
    if type(scheduled) is not int or scheduled < state["current_time"]:
        raise IntegrityError("Persisted agent decision time is invalid")
    return min(scheduled, state["end_time"])


def _wall_time(connection: sqlite3.Connection, runtime: dict | None) -> dict:
    """@brief 根据实际事件落盘时刻计算墙钟经过时间，终止运行固定在末事件。"""
    row = connection.execute("""
        SELECT min(json_extract(body,'$.recorded_at')),max(json_extract(body,'$.recorded_at')) FROM events
    """).fetchone()
    if row[0] is None:
        return {"started_at": None, "last_event_at": None, "elapsed_seconds": None}
    start, last = (datetime.fromisoformat(value) for value in row)
    stop = datetime.now(timezone.utc) if runtime and runtime["status"] == "running" else last
    return {"started_at": start.isoformat(), "last_event_at": last.isoformat(),
            "elapsed_seconds": max(0, (stop - start).total_seconds())}
