"""@brief 从真实活动区间重算班次工作负荷与作息偏离，不包含任务预约、期限、效率或任务付款。"""

from __future__ import annotations

from worldmock.footprints.timeline_labels import window_days, local_day_bounds
from worldmock.footprints.intervals import valid_interval, body_settlements
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from worldmock.life.work_schedule import break_spans, shift_on, shift_span

if TYPE_CHECKING:
    from worldmock.environment import EnvironmentInputs
    from worldmock.input_models import WorkSchedule

#: 没有打包输入、与某个人物缺输入时的说明。
NO_INPUT_NOTE = "未提供工作安排评估输入，workload 与 routine_deviation 为 null"
MISSING_AGENT_NOTE = "人物 {agent_id} 缺少工作安排评估输入，workload 与 routine_deviation 为 null"

#: 每个人物的六个实际班次与出勤量。
WORKLOAD_KEYS = (
    "shift_seconds", "at_workplace_seconds", "break_seconds", "declared_exception_seconds",
    "absence_seconds", "early_leave_seconds",
)


_Spans = list[tuple[int, int]]




@dataclass(frozen=True, slots=True)
class AgentWorkday:
    """@brief 一个人物的评估输入：工作安排、住处和人设表写的下班、到家窗口（没有写的为 None）。"""

    work_schedule: WorkSchedule | None
    home_location_id: str
    workplace_location_id: str | None
    initial_location_id: str
    leave_work_window: tuple[float, float] | None
    arrive_home_window: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class WorkdayInputs:
    """@brief 打包的评估输入：人物编号到其输入，与场景的播报间隔（即报告写明的容差）。"""

    agents: Mapping[int, AgentWorkday]
    report_interval_seconds: int


@dataclass(frozen=True, slots=True)
class _Trip:
    """@brief 一次出行：起止时刻、出发地、目的地、实际终点、首个进度事件身份与到达（时刻、事件身份；没有到达为 None）。"""

    start: int
    end: int
    origin: str
    destination: str | None
    end_location: str
    departure_event_id: str
    arrival: tuple[int, str] | None


@dataclass(frozen=True, slots=True)
class _Timeline:
    """@brief 从真实事件重建观察窗、出行、停留和睡眠区间。"""

    start: int
    end: int
    trips: tuple[_Trip, ...]
    stays: tuple[tuple[int, int, str], ...]
    sleep: tuple[tuple[int, int], ...]


def workday_inputs(inputs: EnvironmentInputs) -> WorkdayInputs:
    """@brief 人设表提供班次、地点与下班、到家窗口的原值，配置提供播报间隔；不读人物工作区。"""
    persona = inputs.persona
    agent = AgentWorkday(
        persona.work_schedule, inputs.home_location_id, inputs.workplace_location_id, inputs.initial_location_id,
        _window(persona.leave_work_window), _window(persona.arrive_home_window))
    return WorkdayInputs({persona.agent_id: agent}, inputs.config.progress_report_interval_seconds)


def workday_reports(events: list[dict[str, Any]], timezone: str, inputs: WorkdayInputs | None) -> dict[str, Any]:
    """@brief 计算 workload 与 routine_deviation 两个报告与说明；没有打包输入时两者为 None。

    事件里出现而输入里没有的人物在两个报告里都为 None 并各写一条说明；没有工作安排的人物不进 workload，在
    routine_deviation 里照常逐日报告。
    """
    if inputs is None:
        return {"workload": None, "routine_deviation": None, "notes": [NO_INPUT_NOTE]}
    zone = ZoneInfo(timezone)
    ordered = sorted(events, key=lambda event: (event.get("event_seq", 0), str(event.get("event_id"))))
    workload: dict[str, Any] = {}
    deviation: dict[str, Any] = {}
    notes: list[str] = []
    for agent_id in _agent_ids(ordered, inputs):
        if agent_id not in inputs.agents:
            workload[str(agent_id)] = deviation[str(agent_id)] = None
            notes.append(MISSING_AGENT_NOTE.format(agent_id=agent_id))
            continue
        agent = inputs.agents[agent_id]
        timeline = _timeline([event for event in ordered if event.get("agent_id") == agent_id], agent, zone)
        if agent.work_schedule is not None:
            workload[str(agent_id)] = _workload(timeline, agent, zone)
        deviation[str(agent_id)] = {"days": [_deviation_row(timeline, agent, day, zone)
                                             for day in _days(timeline, zone)]}
    return {"workload": {"agents": workload},
            "routine_deviation": {"tolerance_seconds": inputs.report_interval_seconds, "agents": deviation},
            "notes": notes}


def _window(window: Sequence[float] | None) -> tuple[float, float] | None:
    """@brief 人设表的 [起点, 终点] 取成（起, 止）小时，原值不取整；没有写时为 None。"""
    return None if window is None else (float(window[0]), float(window[1]))


def _agent_ids(events: list[dict[str, Any]], inputs: WorkdayInputs) -> list[int]:
    """@brief 报告覆盖的人物：输入里的全部人物与事件里出现的人物（升序）。"""
    seen = {event["agent_id"] for event in events
            if isinstance(event.get("agent_id"), int) and not isinstance(event.get("agent_id"), bool)}
    return sorted(seen | set(inputs.agents))


def _snapshot(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief activity_progress 事件里的活动快照。"""
    return event["data"]["activity"]


def _progress(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """@brief 某类活动的进度事件（按事件顺序）。"""
    return [event for event in events
            if event.get("event_type") == "activity_progress" and _snapshot(event).get("kind") == kind
            and valid_interval(event.get("data", {}).get("interval_start"),
                               event.get("data", {}).get("interval_end"))]


def _timeline(events: list[dict[str, Any]], agent: AgentWorkday, zone: ZoneInfo) -> _Timeline:
    """@brief 按真实身体结算和活动进度重建人物时间线，观察窗取共同身体区间的两端。"""
    bodies = [event["data"] for event in body_settlements(events)
              if valid_interval(event.get("data", {}).get("interval_start"),
                                 event.get("data", {}).get("interval_end"))]
    start = min((data["interval_start"] for data in bodies), default=0)
    end = max((data["interval_end"] for data in bodies), default=0)
    trips = _trips(events)
    sleep = tuple((event["data"]["interval_start"], event["data"]["interval_end"])
                  for event in _progress(events, "sleep"))
    return _Timeline(start, end, trips, _stays(trips, agent.initial_location_id, start, end), sleep)




def _trips(events: list[dict[str, Any]]) -> tuple[_Trip, ...]:
    """@brief 出行按活动身份归组：起点取快照的 started_at，终点取最后一个进度区间的终点；快照带到达时终点在到达处
    （到达被拒也一样），否则回到出发地。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in _progress(events, "travel"):
        groups.setdefault(str(_snapshot(event)["activity_id"]), []).append(event)
    trips = []
    for group in groups.values():
        snapshot = _snapshot(group[-1])
        arrived = next((event for event in group
                        if isinstance(_snapshot(event).get("data", {}).get("arrival"), dict)), None)
        end_location = (snapshot["location_id"] if arrived is None
                        else _snapshot(arrived)["data"]["arrival"]["location_id"])
        arrival = None if arrived is None else (arrived["data"]["interval_end"], str(arrived["event_id"]))
        trips.append(_Trip(snapshot["started_at"], group[-1]["data"]["interval_end"], snapshot["location_id"],
                           snapshot.get("destination_id"), end_location, str(group[0]["event_id"]), arrival))
    return tuple(sorted(trips, key=lambda trip: (trip.start, trip.departure_event_id)))


def _stays(trips: tuple[_Trip, ...], home: str, start: int, end: int) -> tuple[tuple[int, int, str], ...]:
    """@brief 观察窗内不在路上的时间各在哪里：首次出行前在其出发地（没有出行时在住处），之后在上一次出行的终点。"""
    location = trips[0].origin if trips else home
    cursor = start
    stays = []
    for trip in trips:
        stays.append((cursor, trip.start, location))
        location, cursor = trip.end_location, trip.end
    stays.append((cursor, end, location))
    return tuple(stay for stay in stays if stay[1] > stay[0])




def _days(timeline: _Timeline, zone: ZoneInfo) -> list[date]:
    """@brief 观察窗覆盖的本地日使用共享切分规则。"""
    return window_days({"start": timeline.start, "end": timeline.end, "timezone": zone.key})


def _union(spans: _Spans) -> _Spans:
    """@brief 合并相交或接续的半开区间，丢掉空区间，升序返回。"""
    merged: _Spans = []
    for start, end in sorted(span for span in spans if span[1] > span[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _intersect(left: _Spans, right: _Spans) -> _Spans:
    """@brief 两组区间的交集。"""
    first, second = _union(left), _union(right)
    result: _Spans = []
    index = other = 0
    while index < len(first) and other < len(second):
        start, end = max(first[index][0], second[other][0]), min(first[index][1], second[other][1])
        if start < end:
            result.append((start, end))
        if first[index][1] < second[other][1]:
            index += 1
        else:
            other += 1
    return result


def _subtract(left: _Spans, right: _Spans) -> _Spans:
    """@brief 从 left 里去掉 right 覆盖的部分。"""
    holes = _union(right)
    result: _Spans = []
    for start, end in _union(left):
        cursor = start
        for hole_start, hole_end in holes:
            if hole_start < end and hole_end > cursor:
                if hole_start > cursor:
                    result.append((cursor, hole_start))
                cursor = max(cursor, hole_end)
        if cursor < end:
            result.append((cursor, end))
    return result


def _length(spans: _Spans) -> int:
    """@brief 区间并集的总秒数。"""
    return sum(end - start for start, end in _union(spans))


def _at(timeline: _Timeline, location: str) -> _Spans:
    """@brief 人在某地（不在路上）的时间。"""
    return [(start, end) for start, end, place in timeline.stays if place == location]






def _workload(timeline: _Timeline, agent: AgentWorkday, zone: ZoneInfo) -> dict[str, Any]:
    """@brief 一个有工作安排的人物在观察窗内各当班日的十个量之和（有效工作秒数求和后保留六位小数）；不当班的日子与
    完全落在观察窗外的班次不计。"""
    totals: dict[str, Any] = dict.fromkeys(WORKLOAD_KEYS, 0)
    for day in _days(timeline, zone):
        shift = shift_on(agent.work_schedule, day)
        if shift is None:
            continue
        begin, finish = shift_span(shift, day, zone)
        span = (max(begin, timeline.start), min(finish, timeline.end))
        if span[1] <= span[0]:
            continue
        row = _duty_day(timeline, agent, span, list(break_spans(shift, day, zone)))
        for key in WORKLOAD_KEYS:
            totals[key] += row[key]
    return totals


def _duty_day(timeline: _Timeline, agent: AgentWorkday, span: tuple[int, int],
              breaks: _Spans) -> dict[str, Any]:
    """@brief 班次按真实停留、休息、缺勤与早退拆分，不推断工作内容消费。"""
    shift = [span]
    rests = _intersect(breaks, shift)
    present = _intersect(_at(timeline, agent.workplace_location_id), shift)
    absent = _subtract(_subtract(shift, rests), present)
    return {"shift_seconds": _length(shift), "at_workplace_seconds": _length(present),
            "break_seconds": _length(rests),
            "declared_exception_seconds": _length(rests),
            "absence_seconds": _length(absent), "early_leave_seconds": _early_leave(timeline, agent, span, rests)}




def _early_leave(timeline: _Timeline, agent: AgentWorkday, span: tuple[int, int], rests: _Spans) -> int:
    """@brief 班内最后一次离开工作地点后未在下班前回来且出发不在休息时段时，计算早退秒数。"""
    workplace = agent.workplace_location_id
    departures = [trip for trip in timeline.trips if trip.origin == workplace and span[0] <= trip.start < span[1]]
    if not departures:
        return 0
    last = departures[-1]
    back = any(trip.end_location == workplace and trip.start >= last.start and trip.end < span[1]
               for trip in timeline.trips)
    rest = any(start <= last.start < end for start, end in rests)
    return 0 if back or rest else span[1] - last.start


def _moment(epoch: int | None, zone: ZoneInfo) -> str | None:
    """@brief 本地 ISO 8601 时刻（带偏移）；没有时刻为 None。"""
    return None if epoch is None else datetime.fromtimestamp(epoch, zone).isoformat()


def _minutes(delta: int) -> float:
    """@brief 秒差换算成分钟（六位小数）。"""
    return round(delta / 60, 6)


def _window_minutes(epoch: int | None, window: tuple[float, float] | None, day: date,
                    zone: ZoneInfo) -> float | None:
    """@brief 时刻相对窗口（当天的钟点，闭区间）的偏离分钟：早于起点为负，晚于终点为正，窗口内为 0；任一为空时 None。"""
    if epoch is None or window is None:
        return None
    midnight = datetime.combine(day, datetime.min.time(), zone)
    start, end = (int((midnight + timedelta(hours=hour)).timestamp()) for hour in window)
    if epoch < start:
        return _minutes(epoch - start)
    return _minutes(epoch - end) if epoch > end else 0.0


def _asleep(timeline: _Timeline, instant: int) -> bool | None:
    """@brief 某一时刻人物是否在睡（有睡眠进度区间覆盖它）；时刻不在观察窗内时不知道，为 None。"""
    if not timeline.start <= instant < timeline.end:
        return None
    return any(start <= instant < end for start, end in timeline.sleep)


def _left_workplace(timeline: _Timeline, agent: AgentWorkday, day: date, zone: ZoneInfo) -> _Trip | None:
    """@brief 当天最后一次从工作地点出发的出行；没有工作安排或当天没有这样的出发时为 None。"""
    if agent.work_schedule is None:
        return None
    workplace = agent.workplace_location_id
    leaving = [trip for trip in timeline.trips
               if trip.origin == workplace and datetime.fromtimestamp(trip.start, zone).date() == day]
    return leaving[-1] if leaving else None


def _arrived_home(timeline: _Timeline, home: str, day: date, zone: ZoneInfo,
                  after: int | None) -> tuple[int, str] | None:
    """@brief 当天到家：有离开工作地点的时刻时取其后的首次到家，否则取当天最后一次到家；没有时为 None。"""
    arrivals = [trip.arrival for trip in timeline.trips if trip.arrival is not None and trip.end_location == home
                and datetime.fromtimestamp(trip.arrival[0], zone).date() == day]
    if after is not None:
        return next((arrival for arrival in arrivals if arrival[0] > after), None)
    return arrivals[-1] if arrivals else None


def _deviation_row(timeline: _Timeline, agent: AgentWorkday, day: date, zone: ZoneInfo) -> dict[str, Any]:
    """@brief 一个本地日的作息偏离：离开工作地点对班次结束与下班窗口、到家对到家窗口各一项，班次两端是否在睡，附证据。"""
    shift = None if agent.work_schedule is None else shift_on(agent.work_schedule, day)
    span = None if shift is None else shift_span(shift, day, zone)
    left = _left_workplace(timeline, agent, day, zone)
    leave_at = None if left is None else left.start
    home = _arrived_home(timeline, agent.home_location_id, day, zone, leave_at)
    home_at = None if home is None else home[0]
    return {"date": day.isoformat(), "left_workplace_at": _moment(leave_at, zone),
            "leave_vs_shift_end_minutes": None if span is None or leave_at is None else _minutes(leave_at - span[1]),
            "leave_vs_window_minutes": _window_minutes(leave_at, agent.leave_work_window, day, zone),
            "arrived_home_at": _moment(home_at, zone),
            "arrive_home_vs_window_minutes": _window_minutes(home_at, agent.arrive_home_window, day, zone),
            "asleep_at_shift_start": None if span is None else _asleep(timeline, span[0]),
            "asleep_at_shift_end": None if span is None else _asleep(timeline, span[1]),
            "evidence_event_ids": {"left_workplace": None if left is None else left.departure_event_id,
                                   "arrived_home": None if home is None else home[1]}}
