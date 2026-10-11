"""@brief 地点停留的投影与自查：每段停留一行，由停留进出事件按会话身份配对。

投影只读已提交的事件；自查再用出行活动和最终检查点里的停留会话核对每一行，任一不成立导出不发布。
"""

from __future__ import annotations

from worldmock.errors import IntegrityError

#: 停留行与出行、与最终检查点不符时的英文原因。
_TRAVEL_MISMATCH = "Location stay disagrees with its actual travel"
_CHECKPOINT_MISMATCH = "Location stays disagree with the final checkpoint"
_DISORDER = "Location stays overlap or are out of order"


def project_location_stays(events: list[dict]) -> list[dict]:
    """@brief 进入事件各起一行并按先后排列，离开事件按会话身份补上离开时刻；导出截止时还在的那段没有离开时刻。"""
    rows: dict[str, dict] = {}
    for event in events:
        data = event["data"]
        if event["event_type"] != "environment_event" or data.get("kind") not in {"location_entered", "location_left"}:
            continue
        row = rows.get(data["session_id"])
        if data["kind"] == "location_entered":
            if row is not None:
                raise IntegrityError(_DISORDER)
            rows[data["session_id"]] = {
                "agent_id": event["agent_id"], "session_id": data["session_id"], "location_id": data["location_id"],
                "entry_kind": data["entry_kind"], "entered_at": event["occurred_at"], "left_at": None,
                "enter_source_event_id": event["event_id"], "leave_source_event_id": None}
        elif row is None or row["left_at"] is not None or row["location_id"] != data["location_id"]:
            raise IntegrityError(_DISORDER)
        else:
            row.update(left_at=event["occurred_at"], leave_source_event_id=event["event_id"])
    return list(rows.values())


def validate_location_stays(stays: list[dict], events: list[dict], activities: list[dict], state: dict,
                            window: dict) -> None:
    """@brief 停留行的先后、与出行的对应、与最终检查点里停留会话的对应。

    :param stays: 已与事件重新投影核对过的停留行。
    :param activities: 导出的活动行；出行取其中的 travel。
    :param state: 最终检查点的世界状态。
    :param window: 冻结运行清单里的运行窗口，start 与 end 是纪元秒。
    """
    _validate_order(stays, window["start"])
    sources = {event["event_id"]: event for event in events}
    trips = {row["activity_id"]: row for row in activities if row["activity_type"] == "travel"}
    for previous, stay in zip([None, *stays], stays):
        _validate_entry(previous, stay, sources, trips)
        if stay["left_at"] is not None:
            _validate_departure(stay, sources)
    _validate_sessions(stays, state, window["end"])


def _validate_order(stays: list[dict], run_start: int) -> None:
    """@brief 恰一行运行开始且排在最前，各行按时间先后互不重叠，只有最后一行可以没有离开时刻。"""
    kinds = [row["entry_kind"] for row in stays]
    if kinds[:1] != ["run_start"] or kinds.count("run_start") != 1 or stays[0]["entered_at"] != run_start:
        raise IntegrityError(_DISORDER)
    for index, row in enumerate(stays):
        last = index == len(stays) - 1
        if row["left_at"] is None and not last or row["left_at"] is not None and row["left_at"] < row["entered_at"]:
            raise IntegrityError(_DISORDER)
        if not last and stays[index + 1]["entered_at"] < row["left_at"]:
            raise IntegrityError(_DISORDER)


def _validate_entry(previous: dict | None, stay: dict, sources: dict, trips: dict) -> None:
    """@brief 到达对应一趟走完的出行，途中提前结束出行回到的是前一段停留的地点。"""
    kind = stay["entry_kind"]
    if kind == "run_start":
        return
    causes = [sources.get(key) for key in sources[stay["enter_source_event_id"]]["caused_by"]]
    cause = causes[0] if len(causes) == 1 else None
    if cause is None or previous is None or cause["agent_id"] != stay["agent_id"]:
        raise IntegrityError(_TRAVEL_MISMATCH)
    if kind == "travel_ended_early":
        if cause["event_type"] != "action_result" or stay["location_id"] != previous["location_id"]:
            raise IntegrityError(_TRAVEL_MISMATCH)
        return
    trip = trips.get(cause["activity_id"]) if cause["event_type"] == "activity_progress" else None
    if (trip is None or trip["elapsed_seconds"] != trip["planned_duration_seconds"]
            or (trip["ended_at"], trip["destination_id"]) != (stay["entered_at"], stay["location_id"])
            or (trip["started_at"], trip["location_id"]) != (previous["left_at"], previous["location_id"])):
        raise IntegrityError(_TRAVEL_MISMATCH)


def _validate_departure(stay: dict, sources: dict) -> None:
    """@brief 每次离开对应一次被受理的出发：起因是带着从这里、在这一秒开始的出行活动的受理结果。"""
    causes = [sources.get(key) for key in sources[stay["leave_source_event_id"]]["caused_by"]]
    cause = causes[0] if len(causes) == 1 else None
    started = [] if cause is None else cause["data"].get("data", {}).get("activities", [])
    if (cause is None or cause["event_type"] != "action_result" or cause["agent_id"] != stay["agent_id"]
            or cause["data"].get("status") != "in_progress"
            or not any((row["kind"], row["started_at"], row["location_id"])
                       == ("travel", stay["left_at"], stay["location_id"]) for row in started)):
        raise IntegrityError(_TRAVEL_MISMATCH)


def _validate_sessions(stays: list[dict], state: dict, run_end: int) -> None:
    """@brief 各行与检查点里的停留会话逐一对应；没有离开时刻的那一行对应结束于运行截止的会话。"""
    sessions = {key: item for key, item in state.get("sessions", {}).items() if item["kind"] == "stay"}
    if len(sessions) != len(stays) or set(sessions) != {stay["session_id"] for stay in stays}:
        raise IntegrityError(_CHECKPOINT_MISMATCH)
    for stay in stays:
        session = sessions[stay["session_id"]]
        ended = run_end if stay["left_at"] is None else stay["left_at"]
        if ((session["agent_id"], session["location_id"], session["started_at"], session["ended_at"])
                != (stay["agent_id"], stay["location_id"], stay["entered_at"], ended)):
            raise IntegrityError(_CHECKPOINT_MISMATCH)
