"""@brief 行为评估的工作负荷与作息偏离（docs/VERIFICATION.md#errands-workday-度量与下游-行为评估 ）：从冻结足迹事件与一个可选的打包输入重算，只报告。

打包输入（WorkdayInputs）带各人物的工作安排、本人任务的要求地点与开放时刻（含预约）、住处、覆盖后的下班与到家窗口
（取自结构化卡片）与播报间隔；不解析任何提示文本，参照物是场景班次与作息窗口，不是当日计划。证据只取三类事件：
出行、工作与睡眠的 activity_progress（结算区间与活动快照；到达写在快照的 data.arrival）、每人每区间的共同身体结算
（界定观察窗）与带 task_id 和 payment_fen 的已完成动作结果（任务结清时刻）。区间一律半开。

所在地点只由出行重建：首次出行之前在该次出行的出发地（没有出行时在住处），两次出行之间在前一次的终点（到达处；没有
到达即取消，回到出发地）。班次按观察窗截取，只统计有工作安排的人物与当班日；不当班的日子不产生班次，不进统计。声明
的例外只有两种场景事实：班次内声明的休息时段（人在哪里都算），与本人已开放且未结清的任务要求地点（不是工作地点）的
外出——去往那里的路上、在那里的时间，以及从那里直接回工作地点的一程。普通任务从开放时刻（最早的本人通知）算起；
预约任务（规格 4.5「含预约任务」）从约定开始那一天的当地零点算起，赴约前去往约定地点的路上与提前到达的等候也算，
不等约定开始；两者都到首次付款结清为止。其余不在工作地点的班次时间是缺勤。

报告说明沿用行为评估的中文惯例；容差等于播报间隔、写进报告，不作用于偏离值。
"""

from __future__ import annotations
from worldmock.life.scenario import runtime_tasks

from worldmock.footprints.timeline_labels import window_days, local_day_bounds
from worldmock.footprints.intervals import valid_interval, body_settlements
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping
from zoneinfo import ZoneInfo

from worldmock.life.work_schedule import break_spans, shift_on, shift_span

if TYPE_CHECKING:
    from worldmock.life.scenario import LifeScenario, WorkSchedule

#: 没有打包输入、与某个人物缺输入时的说明。
NO_INPUT_NOTE = "未提供工作安排评估输入，workload 与 routine_deviation 为 null"
MISSING_AGENT_NOTE = "人物 {agent_id} 缺少工作安排评估输入，workload 与 routine_deviation 为 null"

#: workload 每个人物的十个量（规格 6.2 的八项，另把声明例外拆成休息时段与任务外出两项）。
WORKLOAD_KEYS = (
    "shift_seconds", "at_workplace_seconds", "work_activity_seconds", "effective_work_seconds",
    "idle_at_workplace_seconds", "break_seconds", "task_outing_seconds", "declared_exception_seconds",
    "absence_seconds", "early_leave_seconds",
)

#: 从未结清的任务的结清时刻（开放到观察结束之后）。
_NEVER = 2 ** 62

_Spans = list[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class TaskSite:
    """@brief 一项本人工作任务的要求地点与开放时刻（epoch 秒）；预约任务另带约定开始，否则为 None。"""

    task_id: str
    location_id: str
    open_from: int
    appointment_at: int | None


@dataclass(frozen=True, slots=True)
class AgentWorkday:
    """@brief 一个人物的评估输入：工作安排（None 为没有工作安排）、住处、覆盖后的下班与到家窗口（自当地零点起的小时，
    卡片里为 null 时为 None）与本人的任务。"""

    work_schedule: WorkSchedule | None
    home_location_id: str
    leave_work_window: tuple[float, float] | None
    arrive_home_window: tuple[float, float] | None
    tasks: tuple[TaskSite, ...]


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
    """@brief 一个人物在观察窗内的时间线：观察窗、出行、停留（起、止、地点）、工作区间（起、止、效率）、睡眠区间与
    各本人任务算作任务外出的半开时段（起、止）。"""

    start: int
    end: int
    trips: tuple[_Trip, ...]
    stays: tuple[tuple[int, int, str], ...]
    work: tuple[tuple[int, int, float], ...]
    sleep: tuple[tuple[int, int], ...]
    task_windows: Mapping[str, tuple[int, int]]


def workday_inputs(document: LifeScenario, cards: Mapping[int, Mapping[str, Any] | None]) -> WorkdayInputs:
    """@brief 由场景文档与各人物的结构化卡片打包评估输入：工作安排、住处、按指派人物分开的任务（开放时刻取装配后的
    通知或预约）与播报间隔取自场景文档；下班与到家窗口只取卡片 time_anchors 的 leave_work、arrive_home（覆盖已写进
    卡片），没有卡片或该锚点为 null 时为 None。"""

    tasks = runtime_tasks(document.tasks, document.messages)
    agents = {}
    for agent in document.agents:
        card = cards.get(agent.agent_id)
        own = tuple(TaskSite(task.task_id, task.required_location_id, int(task.available_at.timestamp()),
                             None if task.appointment is None else int(task.appointment.starts_at.timestamp()))
                    for task in tasks if int(task.assignee_agent_id.removeprefix("agent:")) == agent.agent_id)
        agents[agent.agent_id] = AgentWorkday(agent.work_schedule, agent.home_location_id,
                                              _card_window(card, "leave_work"), _card_window(card, "arrive_home"),
                                              own)
    return WorkdayInputs(agents, document.progress_report_interval_seconds)


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


def _card_window(card: Mapping[str, Any] | None, name: str) -> tuple[float, float] | None:
    """@brief 结构化卡片 time_anchors 里一个锚点的（起, 止）小时；没有卡片或锚点为 null 时为 None。"""
    window = None if card is None else card["time_anchors"][name]
    return None if window is None else (float(window["start_hour"]), float(window["end_hour"]))


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
    """@brief 由一个人物的事件重建时间线：观察窗取共同身体结算区间的最早起点与最晚终点；预约任务的外出时段按 zone 的
    当地日期起算。"""
    bodies = [event["data"] for event in body_settlements(events)
              if valid_interval(event.get("data", {}).get("interval_start"),
                                 event.get("data", {}).get("interval_end"))]
    start = min((data["interval_start"] for data in bodies), default=0)
    end = max((data["interval_end"] for data in bodies), default=0)
    trips = _trips(events)
    work = tuple((event["data"]["interval_start"], event["data"]["interval_end"],
                  float(event["data"]["activity_efficiency"])) for event in _progress(events, "work"))
    sleep = tuple((event["data"]["interval_start"], event["data"]["interval_end"])
                  for event in _progress(events, "sleep"))
    settled = _settled(events)
    windows = {task.task_id: (_counts_from(task, zone), settled.get(task.task_id, _NEVER)) for task in agent.tasks}
    return _Timeline(start, end, trips, _stays(trips, agent.home_location_id, start, end), work, sleep, windows)


def _counts_from(task: TaskSite, zone: ZoneInfo) -> int:
    """@brief 本人任务开始算作任务外出的时刻：普通任务为开放时刻；预约任务为约定开始那一天的当地零点（赴约前去往约定
    地点的路上已算，不等约定开始）。"""
    if task.appointment_at is None:
        return task.open_from
    day = datetime.fromtimestamp(task.appointment_at, zone).date()
    return min(task.open_from, local_day_bounds(day, zone)[0])


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


def _settled(events: list[dict[str, Any]]) -> dict[str, int]:
    """@brief 各任务首次付款结清的时刻（已完成、内层数据带 task_id 与 payment_fen 的动作结果）。"""
    settled: dict[str, int] = {}
    for event in events:
        data = event.get("data", {})
        inner = data.get("data")
        if (event.get("event_type") == "action_result" and data.get("status") == "completed"
                and isinstance(inner, dict) and "task_id" in inner and "payment_fen" in inner):
            settled.setdefault(str(inner["task_id"]), event["occurred_at"])
    return settled


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


def _valid(timeline: _Timeline, task: TaskSite) -> _Spans:
    """@brief 任务算作任务外出的时间（已开放且未结清；预约任务从约定当天的当地零点起）。"""
    return [timeline.task_windows[task.task_id]]


def _outings(timeline: _Timeline, agent: AgentWorkday, workplace: str) -> _Spans:
    """@brief 任务外出的时间（还没扣休息时段）：要求地点不是工作地点的本人任务开放且未结清时（预约任务从约定当天的
    当地零点起），在去那里的路上与在那里的时间；以及从那里直接回工作地点的一程——前提是离开前那段停留里任务有算作
    外出的时间。"""
    pieces: _Spans = []
    for task in agent.tasks:
        if task.location_id == workplace:
            continue
        valid = _valid(timeline, task)
        toward = [(trip.start, trip.end) for trip in timeline.trips if trip.destination == task.location_id]
        pieces += _intersect(_at(timeline, task.location_id) + toward, valid)
        for index, trip in enumerate(timeline.trips):
            arrived = timeline.trips[index - 1].end if index else timeline.start
            if (trip.origin == task.location_id and trip.destination == workplace
                    and _intersect([(arrived, trip.start)], valid)):
                pieces.append((trip.start, trip.end))
    return _union(pieces)


def _workload(timeline: _Timeline, agent: AgentWorkday, zone: ZoneInfo) -> dict[str, Any]:
    """@brief 一个有工作安排的人物在观察窗内各当班日的十个量之和（有效工作秒数求和后保留六位小数）；不当班的日子与
    完全落在观察窗外的班次不计。"""
    outings = _outings(timeline, agent, agent.work_schedule.workplace_location_id)
    totals: dict[str, Any] = dict.fromkeys(WORKLOAD_KEYS, 0)
    for day in _days(timeline, zone):
        shift = shift_on(agent.work_schedule, day)
        if shift is None:
            continue
        begin, finish = shift_span(shift, day, zone)
        span = (max(begin, timeline.start), min(finish, timeline.end))
        if span[1] <= span[0]:
            continue
        row = _duty_day(timeline, agent, outings, span, list(break_spans(shift, day, zone)))
        for key in WORKLOAD_KEYS:
            totals[key] += row[key]
    totals["effective_work_seconds"] = round(totals["effective_work_seconds"], 6)
    return totals


def _duty_day(timeline: _Timeline, agent: AgentWorkday, outings: _Spans, span: tuple[int, int],
              breaks: _Spans) -> dict[str, Any]:
    """@brief 一个当班日（已截到观察窗的班次）的十个量：班次时间分成在工作地点、休息时段（不在工作地点的部分）、任务外出
    与缺勤四份；工作活动与有效工时按班次内的进度区间计；在岗空闲是在工作地点而没有工作活动的时间。"""
    shift = [span]
    rests = _intersect(breaks, shift)
    present = _intersect(_at(timeline, agent.work_schedule.workplace_location_id), shift)
    working = _intersect([(start, end) for start, end, _ in timeline.work], shift)
    away = _subtract(_intersect(outings, shift), rests)
    absent = _subtract(_subtract(_subtract(shift, rests), present), away)
    effective = sum(_length(_intersect([(start, end)], shift)) * efficiency
                    for start, end, efficiency in timeline.work)
    return {"shift_seconds": _length(shift), "at_workplace_seconds": _length(present),
            "work_activity_seconds": _length(working), "effective_work_seconds": effective,
            "idle_at_workplace_seconds": _length(_subtract(present, working)), "break_seconds": _length(rests),
            "task_outing_seconds": _length(away), "declared_exception_seconds": _length(rests) + _length(away),
            "absence_seconds": _length(absent), "early_leave_seconds": _early_leave(timeline, agent, span, rests)}


def _open_at(timeline: _Timeline, task: TaskSite, instant: int) -> bool:
    """@brief 某一时刻任务算作任务外出（已开放且未结清；预约任务从约定当天的当地零点起）。"""
    start, end = timeline.task_windows[task.task_id]
    return start <= instant < end


def _early_leave(timeline: _Timeline, agent: AgentWorkday, span: tuple[int, int], rests: _Spans) -> int:
    """@brief 早退秒数：班次内最后一次从工作地点出发，到班次结束前没有回到工作地点，且出发时刻不在休息时段内、目的地
    不是出发时刻算作任务外出的本人任务要求地点（已开放且未结清；预约任务从约定当天的当地零点起）时，为出发到班次结束
    的时长；否则为 0（休息时段内离开后的时间记作缺勤）。"""
    workplace = agent.work_schedule.workplace_location_id
    departures = [trip for trip in timeline.trips if trip.origin == workplace and span[0] <= trip.start < span[1]]
    if not departures:
        return 0
    last = departures[-1]
    back = any(trip.end_location == workplace and trip.start >= last.start and trip.end < span[1]
               for trip in timeline.trips)
    rest = any(start <= last.start < end for start, end in rests)
    site = last.destination != workplace and any(
        task.location_id == last.destination and _open_at(timeline, task, last.start) for task in agent.tasks)
    return 0 if back or rest or site else span[1] - last.start


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
    workplace = agent.work_schedule.workplace_location_id
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
