"""@brief 只读查询已确认世界与运行统计，不改变活动或推进时钟。"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import EventType, canonical_json

logger = logging.getLogger(__name__)
#: 驱动心跳文件名与写入间隔（秒）。
HEARTBEAT_FILE = "heartbeat.json"
HEARTBEAT_INTERVAL_SECONDS = 60
#: status.json 仍是 running 而心跳超过这么多个间隔未更新时，status 报告驱动已失联（stale）。
HEARTBEAT_STALE_INTERVALS = 3
#: Ray 等写进异常文本的终端颜色序列。
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
#: 第一条与最后一条事件按主键读（不扫描事件表）；最后一条另带事件类型，供运行活性判断。
_FIRST_EVENT_SQL = ("SELECT json_extract(body,'$.recorded_at') FROM events "
                    "WHERE event_seq=(SELECT min(event_seq) FROM events)")
_LAST_EVENT_SQL = ("SELECT json_extract(body,'$.recorded_at'),json_extract(body,'$.event_type') FROM events "
                   "WHERE event_seq=(SELECT max(event_seq) FROM events)")
#: 已落盘提供商响应的按模型用量合计：经类型表达式索引找到模型响应，只读这些正文。
_OBSERVED_USAGE_SQL = """
        SELECT json_extract(body,'$.data.response.model'), count(*),
        count(json_extract(body,'$.data.response.usage.prompt_tokens')),
        sum(json_extract(body,'$.data.response.usage.prompt_tokens')),
        sum(json_extract(body,'$.data.response.usage.completion_tokens')),
        sum(json_extract(body,'$.data.response.usage.total_tokens'))
        FROM events WHERE event_seq IN (SELECT event_seq FROM events
                                        WHERE json_extract(body,'$.event_type')='model_response')
        AND json_extract(body,'$.data.phase')='response'
        GROUP BY json_extract(body,'$.data.response.model')
        ORDER BY json_extract(body,'$.data.response.model')
    """


def write_status(run_dir: Path, status: str, phase: str, error_type: str | None = None,
                 error_message: str | None = None) -> None:
    """@brief 在明确的运行边界写单份状态，生活阶段由环境调用此入口更新；失败时另写一行可读原因 error_message。"""
    if status not in {"created", "running", "completed", "failed", "interrupted"}:
        raise IntegrityError("Unknown run status")
    if phase not in {"deciding", "resolving", "advancing", "checkpointing", "stopped"}:
        raise IntegrityError("Unknown run phase")
    atomic_write(run_dir / "status.json", canonical_json(
        {"status": status, "phase": phase, "error_type": error_type, "error_message": error_message}))


def failure_message(error: BaseException) -> str:
    """@brief status.json 的 error_message：一行可读失败原因。异常组逐项以「 | 」连接；带 cause 属性且其值是另一异常的
    （Ray 任务错误）取该异常；其余为「类型名: 文本」，去掉终端颜色序列、连续空白折成一个空格，不截断。"""
    if isinstance(error, BaseExceptionGroup):
        return " | ".join(failure_message(item) for item in error.exceptions)
    cause = getattr(error, "cause", None)
    if isinstance(cause, BaseException) and cause is not error:
        return failure_message(cause)
    return " ".join(_ANSI_ESCAPE.sub("", f"{type(error).__name__}: {error}").split())


class DriverHeartbeat:
    """@brief 驱动进程心跳：进入时立即原子写一次 heartbeat.json，之后后台守护线程每 60 秒重写；写失败记录错误并继续；
    退出时停止线程，文件保留。"""

    def __init__(self, run_dir: Path) -> None:
        """@brief 绑定心跳文件、驱动开始时刻、停止信号与后台线程。"""
        self._path = Path(run_dir) / HEARTBEAT_FILE
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="worldmock-heartbeat", daemon=True)

    def __enter__(self) -> "DriverHeartbeat":
        """@brief 立即写一次心跳并启动后台线程。"""
        self._beat()
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """@brief 停止后台线程，不吞掉运行中的异常；心跳文件保留。"""
        self._stop.set()
        self._thread.join(HEARTBEAT_INTERVAL_SECONDS)

    def _run(self) -> None:
        """@brief 每个间隔重写一次心跳，直到停止信号。"""
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            self._beat()

    def _beat(self) -> None:
        """@brief 原子写一次心跳；写失败记录英文错误日志后继续，不让心跳线程退出。"""
        record = {"heartbeat_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                  "started_at": self._started_at}
        try:
            atomic_write(self._path, canonical_json(record))
        except Exception as error:
            logger.error("Driver heartbeat write failed: %s", error)


def read_status(run_dir: Path) -> dict:
    """@brief 使用 SQLite 只读连接查询提交状态，模型调用数量与行为数量分开；运行目录的 JSON 文件一律按 UTF-8 读；
    另报心跳、活性与等待模型的起点；首末事件按主键读取。

    每个读语句各自是一个短读，不开长事务，运行中可以查。
    """
    root = run_dir.resolve()
    database = _run_database(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        state = json.loads(connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0])
        counts = {name: count for name, count, _ in event_type_counts(connection)}
        points = [row[0] for row in connection.execute(
            "SELECT checkpoint_id FROM checkpoints ORDER BY rowid DESC LIMIT 5")]
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
        result.update(_liveness_view(connection, root, result["runtime"]))
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


def event_type_counts(connection: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """@brief 各事件类型的（类型，条数，首条序号），按类型名升序、没有的类型不列；只走类型表达式索引，不读事件正文。"""
    counts = []
    for name in sorted(get_args(EventType)):
        count, first = connection.execute(
            "SELECT count(*),min(event_seq) FROM events WHERE json_extract(body,'$.event_type')=?",
            (name,)).fetchone()
        if count:
            counts.append((name, count, first))
    return counts


def _observed_usage(connection: sqlite3.Connection) -> list[dict]:
    """@brief 直接累计已保存提供商响应中的用量，按模型名排出确定的行序；未知响应不伪装成零 token。"""
    rows = connection.execute(_OBSERVED_USAGE_SQL)
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


def _liveness_view(connection: sqlite3.Connection, root: Path, runtime: dict | None) -> dict:
    """@brief 心跳、活性、等待模型的起点与墙钟，首末事件按主键读取，不扫描事件表。"""
    heartbeat = _heartbeat(root)
    first = connection.execute(_FIRST_EVENT_SQL).fetchone()
    last = connection.execute(_LAST_EVENT_SQL).fetchone()
    last_type = last[1] if last else None
    liveness = _liveness(runtime, heartbeat, last_type)
    return {"heartbeat": heartbeat, "liveness": liveness,
            "waiting_for_model_since": last[0] if last_type == "model_request" else None,
            "wall_time": _wall_time(first[0] if first else None, last[0] if last else None, liveness, heartbeat)}


def _heartbeat(root: Path) -> dict | None:
    """@brief 读取驱动心跳并加上距今秒数；文件不存在为空；内容不是 JSON 对象或心跳时刻不可读时写英文错误日志并拒绝，
    读文件的系统错误原样传播。"""
    path = root / HEARTBEAT_FILE
    if not path.exists():
        return None
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(beat["heartbeat_at"])).total_seconds()
    except (ValueError, KeyError, TypeError) as error:
        logger.error("Run heartbeat is invalid: %s", error)
        raise IntegrityError("Run heartbeat is invalid") from error
    return {**beat, "age_seconds": round(max(0.0, age), 3)}


def _liveness(runtime: dict | None, heartbeat: dict | None, last_event_type: str | None) -> str | None:
    """@brief 运行活性：status.json 缺失为空；不是 running 为 stopped；心跳缺失或超过三个间隔为 stale（驱动进程已不在，
    不表示世界停在哪里）；最后一条事件是模型请求为 waiting_for_model；其余为 running。"""
    if runtime is None:
        return None
    if runtime["status"] != "running":
        return "stopped"
    if heartbeat is None or heartbeat["age_seconds"] > HEARTBEAT_STALE_INTERVALS * HEARTBEAT_INTERVAL_SECONDS:
        return "stale"
    if last_event_type == "model_request":
        return "waiting_for_model"
    return "running"


def _wall_time(started: str | None, last: str | None, liveness: str | None, heartbeat: dict | None) -> dict:
    """@brief 墙钟：起点为第一条事件的落盘时刻；运行中与等待模型时止于此刻，失联时止于最后一次心跳，其余止于最后一条
    事件；另报最后一条事件距今秒数。没有事件时四项都为空。"""
    if started is None:
        return {"started_at": None, "last_event_at": None, "elapsed_seconds": None, "last_event_age_seconds": None}
    start, end, now = datetime.fromisoformat(started), datetime.fromisoformat(last), datetime.now(timezone.utc)
    if liveness in ("running", "waiting_for_model"):
        stop = now
    elif liveness == "stale" and heartbeat is not None:
        stop = datetime.fromisoformat(heartbeat["heartbeat_at"])
    else:
        stop = end
    return {"started_at": start.isoformat(), "last_event_at": end.isoformat(),
            "elapsed_seconds": max(0, (stop - start).total_seconds()),
            "last_event_age_seconds": round(max(0.0, (now - end).total_seconds()), 3)}
