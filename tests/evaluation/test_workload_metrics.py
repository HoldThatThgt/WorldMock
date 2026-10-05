"""行为评估的工作负荷与作息偏离（docs/VERIFICATION.md#errands-workday-度量与下游-行为评估 ）。

手写事件用例逐值核对各量的定义：班次按观察窗截取；在工作地点、休息时段、任务外出与缺勤四份之和等于班次；两种声明例外
（班次内的休息时段、本人已开放且未结清任务的要求地点的外出，预约任务从约定当天起算、赴约的路上也算）不计缺勤，不当班
的日子不进统计；早退、离开工作地点与到家
相对班次和作息窗口的偏离、班次两端是否在睡与证据事件身份；缺输入为 null 并写说明；不出现已删除的偏离指标键名；同一
输入的报告逐字节相同。评估输入经真实的场景文档解析打包。

真实运行用例（engine_factory 加 declare 改写场景副本）只发真实请求、不预置地点：从家步行去办公室、开工、15:00 收工并
步行回家，直到 18:30；从 SQLite 读回事件后经 behavior_metrics_report 重算出三小时的早退与缺勤。另一组真实运行把首个
任务改成集市的预约任务：班内赴约（约定开始前出发）、在那里工作、直接回办公室，重算为任务外出而没有缺勤与早退；同一
行程在按日期例外不当班的场景里十个量都是 0。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tests.engine_fixture import (_mutated_scenario, engine_factory)  # noqa: F401
from tests.life.conftest import (conclude_request, layer_start)  # noqa: F401
from tests.life.test_day_planning import _late_start
from tests.life.test_work_contracts import _clock, _decide, _decide_at, _drive, _start_work
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, declare
from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.evaluation.workload_metrics import (MISSING_AGENT_NOTE, NO_INPUT_NOTE, WORKLOAD_KEYS, AgentWorkday,
                                                   TaskSite, WorkdayInputs, workday_inputs, workday_reports)
from worldmock.footprints.models import canonical_json
from worldmock.life.scenario import WorkSchedule, parse_scenario_document

ZONE = "Asia/Shanghai"
CODE = "c" * 64
#: 已删除的计划偏离指标及其字段名（任何一个都不得出现在两个新报告里）。
REMOVED_DEVIATION_NAMES = (
    "plan_actual_deviation", "per_agent_day", "summary", "conventions", "plan_segments", "family_deviation",
    "planned_minutes", "actual_minutes", "deviation_minutes", "actual_same_family_minutes", "planned_total_minutes",
    "actual_total_minutes", "absolute_deviation_minutes", "mean_absolute_deviation_minutes", "agent_day_count",
)


def _at(day: int, clock: str) -> int:
    """2026-01-05（周一）起第 day 天的上海当地钟点（HH:MM）的时间戳。"""
    return int(datetime.fromisoformat(f"2026-01-{5 + day:02d}T{clock}:00+08:00").timestamp())


def _schedule(**exceptions) -> WorkSchedule:
    """周一至周五 09:00–18:00、午休 12:00–13:30，工作地点 office；exceptions 按 ISO 日期覆盖（null 为不当班）。"""
    weekly = {str(day): (STANDARD_SHIFT if day < 5 else None) for day in range(7)}
    return WorkSchedule.model_validate({"workplace_location_id": "office", "weekly": weekly,
                                        "exceptions": exceptions})


def _agent(schedule: WorkSchedule | None, tasks: tuple = (), leave=None, arrive=None) -> AgentWorkday:
    """住处为 home 的人物评估输入。"""
    return AgentWorkday(schedule, "home", leave, arrive, tuple(tasks))


class _Events:
    """按顺序构造足迹事件夹具（Event.model_dump 的形状，事件序号自增，同一代码身份）。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, event_type: str, occurred_at: int, agent_id: int, data: dict, activity_id=None) -> dict:
        """追加一条事件并返回它。"""
        seq = len(self.rows) + 1
        event = {"event_id": f"evt-{seq:04d}", "event_type": event_type, "event_seq": seq,
                 "occurred_at": occurred_at, "agent_id": agent_id, "activity_id": activity_id, "data": data,
                 "run_id": "run-workday", "code_sha256": CODE}
        self.rows.append(event)
        return event

    def body(self, agent_id: int, start: int, end: int) -> None:
        """一条共同身体结算（界定观察窗）。"""
        self.add("environment_event", end, agent_id, {"kind": "body_settlement", "interval_start": start,
                                                      "interval_end": end, "activity_ids": []})

    def progress(self, agent_id: int, interval: tuple[int, int], snapshot: dict, **data) -> dict:
        """一条活动进度：结算区间与活动快照。"""
        start, end = interval
        return self.add("activity_progress", end, agent_id,
                        {"interval_start": start, "interval_end": end, "activity": snapshot, **data},
                        snapshot["activity_id"])

    def trip(self, agent_id: int, start: int, end: int, route: tuple[str, str], arrived: bool = True):
        """一次出行，分成两个进度区间；到达时最后一个快照的 data.arrival 写到达处。返回（首个，最后一个）进度事件。"""
        origin, destination = route
        snapshot = {"activity_id": f"trip-{agent_id}-{len(self.rows)}", "kind": "travel", "started_at": start,
                    "location_id": origin, "destination_id": destination, "data": {}}
        middle = (start + end) // 2
        first = self.progress(agent_id, (start, middle), snapshot)
        last_snapshot = dict(snapshot, data={"arrival": {"location_id": destination}} if arrived else {})
        return first, self.progress(agent_id, (middle, end), last_snapshot)

    def work(self, agent_id: int, start: int, end: int, efficiency: float) -> None:
        """一段工作进度（带结算效率）。"""
        snapshot = {"activity_id": f"work-{agent_id}-{len(self.rows)}", "kind": "work", "started_at": start,
                    "location_id": "office", "data": {}}
        self.progress(agent_id, (start, end), snapshot, activity_efficiency=efficiency)

    def sleep(self, agent_id: int, start: int, end: int) -> None:
        """一段睡眠进度。"""
        snapshot = {"activity_id": f"sleep-{agent_id}-{len(self.rows)}", "kind": "sleep", "started_at": start,
                    "location_id": "home", "data": {}}
        self.progress(agent_id, (start, end), snapshot)

    def paid(self, agent_id: int, at: int, task_id: str) -> None:
        """工作验收通过并付款的动作结果（任务在这一刻结清）。"""
        self.add("action_result", at, agent_id, {"status": "completed", "data": {"task_id": task_id,
                                                                                  "payment_fen": 5000}})


def _reports(events: _Events, agents: dict[int, AgentWorkday], interval: int = 1800) -> dict:
    """经 behavior_metrics_report 的真实入口取本代码组的 reports。"""
    inputs = WorkdayInputs(agents, interval)
    return behavior_metrics_report(events.rows, timezone=ZONE, workday=inputs)[CODE]["reports"]


def _identities(row: dict, break_away: int) -> None:
    """规格 6.2 的恒等关系：在工作地点、缺勤、任务外出与不在工作地点的休息时段四份之和等于班次；在岗空闲不超过在工作
    地点；声明例外是休息时段与任务外出之和；早退不超过班次。"""
    assert list(row) == list(WORKLOAD_KEYS)
    assert (row["at_workplace_seconds"] + row["absence_seconds"] + row["task_outing_seconds"] + break_away
            == row["shift_seconds"])
    assert row["idle_at_workplace_seconds"] <= row["at_workplace_seconds"]
    assert row["declared_exception_seconds"] == row["break_seconds"] + row["task_outing_seconds"]
    assert row["early_leave_seconds"] <= row["shift_seconds"]


def _workload_values(*values) -> dict:
    """按 WORKLOAD_KEYS 的顺序写出十个量。"""
    return dict(zip(WORKLOAD_KEYS, values, strict=True))


def test_workload_fields_follow_their_definitions_on_a_hand_built_day() -> None:
    """周一 09:00–18:00、午休 12:00–13:30 的一天：08:20 出门 08:50 到办公室，09:30–12:00 以效率 0.8 工作，午休时
    12:10 出去吃饭 13:20 回来，13:30–16:30 以效率 0.6 工作，17:00 离开回家。班次 9 小时；在工作地点 6 小时 50 分（午休
    在办公室的 20 分钟在内）；工作活动 5.5 小时，有效工时按效率加权；在岗空闲 80 分钟；午休 90 分钟是声明例外；17:00
    之后缺勤 1 小时，也是 1 小时早退。办公室里的本人任务不算外出。另一人物的观察窗在 15:00 结束：班次只截到 15:00，
    下班时刻是否在睡不知道（null）；第三个人物的观察窗从 19:00 开始，班次整个在观察窗外，十个量都是 0。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(0, "20:00"))
    events.trip(1, _at(0, "08:20"), _at(0, "08:50"), ("home", "office"))
    events.work(1, _at(0, "09:30"), _at(0, "11:00"), 0.8)
    events.work(1, _at(0, "11:00"), _at(0, "12:00"), 0.8)
    events.trip(1, _at(0, "12:10"), _at(0, "12:20"), ("office", "canteen"))
    events.trip(1, _at(0, "13:00"), _at(0, "13:20"), ("canteen", "office"))
    events.work(1, _at(0, "13:30"), _at(0, "16:30"), 0.6)
    events.trip(1, _at(0, "17:00"), _at(0, "17:30"), ("office", "home"))
    events.body(2, _at(0, "07:00"), _at(0, "15:00"))
    events.trip(2, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.body(3, _at(0, "19:00"), _at(0, "23:00"))
    desk = TaskSite("task-desk", "office", _at(0, "08:00"), None)
    reports = _reports(events, {1: _agent(_schedule(), [desk]), 2: _agent(_schedule()), 3: _agent(_schedule())})
    first, cut = reports["workload"]["agents"]["1"], reports["workload"]["agents"]["2"]
    assert reports["workload"]["agents"]["3"] == dict.fromkeys(WORKLOAD_KEYS, 0)
    assert first == _workload_values(32400, 24600, 19800, 13680.0, 4800, 5400, 0, 5400, 3600, 3600)
    _identities(first, 5400 - 1200)
    assert cut == _workload_values(21600, 21600, 0, 0.0, 21600, 5400, 0, 5400, 0, 0)
    _identities(cut, 0)
    [day] = reports["routine_deviation"]["agents"]["2"]["days"]
    assert (day["asleep_at_shift_start"], day["asleep_at_shift_end"]) == (False, None)


def test_breaks_and_task_outings_are_not_absence_and_rest_days_are_left_out() -> None:
    """周一当班、周二按日期例外不当班。人物 1：09:40 从办公室去客户处（任务 10:00 开放、11:30 付款结清），11:45 直接回
    办公室，午休出去吃饭 13:40 才回来。路上 10:00 之后、在客户处到付款为止、回办公室的一程（午休之前的部分）是任务外出；
    任务开放之前的路上与付款之后的停留是缺勤；午休里不在办公室的时间不是缺勤，午休结束后还在路上的 10 分钟是缺勤；
    午休里离开又回来不是早退。人物 2 午休时离开、再没回来：早退为 0，午休结束后的时间是缺勤。人物 3 16:00 去已开放
    未结清的预约任务地点、待到下班：是任务外出，不是早退也不是缺勤。周二（不当班）三人都不在办公室，也不产生任何秒数。"""
    rest = {"2026-01-06": None}
    events = _Events()
    for agent_id in (1, 2, 3):
        events.body(agent_id, _at(0, "07:00"), _at(1, "20:00"))
    events.trip(1, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.trip(1, _at(0, "09:40"), _at(0, "10:10"), ("office", "client"))
    events.work(1, _at(0, "10:10"), _at(0, "11:10"), 0.5)
    events.paid(1, _at(0, "11:30"), "task-client")
    events.trip(1, _at(0, "11:45"), _at(0, "12:15"), ("client", "office"))
    events.trip(1, _at(0, "12:30"), _at(0, "12:40"), ("office", "canteen"))
    events.trip(1, _at(0, "13:20"), _at(0, "13:40"), ("canteen", "office"))
    events.work(1, _at(0, "13:40"), _at(0, "17:40"), 1.0)
    events.trip(1, _at(0, "18:10"), _at(0, "18:40"), ("office", "home"))
    events.trip(2, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.trip(2, _at(0, "12:30"), _at(0, "13:00"), ("office", "home"))
    events.trip(3, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.trip(3, _at(0, "16:00"), _at(0, "16:20"), ("office", "client"))
    client = TaskSite("task-client", "client", _at(0, "10:00"), None)
    appointment = TaskSite("task-visit", "client", _at(0, "15:00"), _at(0, "15:00"))
    workload = _reports(events, {1: _agent(_schedule(**rest), [client]), 2: _agent(_schedule(**rest)),
                                 3: _agent(_schedule(**rest), [appointment])})["workload"]["agents"]
    assert workload["1"] == _workload_values(32400, 18900, 18000, 16200.0, 4500, 5400, 6300, 11700, 2700, 0)
    _identities(workload["1"], 5400 - 900)
    assert workload["2"] == _workload_values(32400, 12600, 0, 0.0, 12600, 5400, 0, 5400, 16200, 0)
    _identities(workload["2"], 3600)
    assert workload["3"] == _workload_values(32400, 25200, 0, 0.0, 25200, 5400, 7200, 12600, 0, 0)
    _identities(workload["3"], 0)


def test_travel_to_an_appointment_before_it_starts_is_a_task_outing_not_absence_or_early_leave() -> None:
    """规格 4.5「含预约任务」：周一 09:00–18:00、午休 12:00–13:30，人物 1 本人的预约任务约在周一 16:00 的客户处（开放
    时刻即约定开始）。15:30 从办公室出发、15:55 到客户处，17:30 直接回家：约定开始之前去客户处的路上、提前到达的等候与
    在客户处的时间都是任务外出，最后一次离开办公室是赴约，不是早退；从客户处回家的一程不是回工作地点，17:30 之后是
    缺勤。人物 2 同样的行程，而预约在周二：约定当天之外去那里不算外出，15:30 之后都是缺勤，也是 2.5 小时早退。"""
    events = _Events()
    for agent_id in (1, 2):
        events.body(agent_id, _at(0, "07:00"), _at(0, "20:00"))
        events.trip(agent_id, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
        events.trip(agent_id, _at(0, "15:30"), _at(0, "15:55"), ("office", "client"))
        events.trip(agent_id, _at(0, "17:30"), _at(0, "18:10"), ("client", "home"))
    today = TaskSite("task-visit", "client", _at(0, "16:00"), _at(0, "16:00"))
    tomorrow = TaskSite("task-visit-next", "client", _at(1, "16:00"), _at(1, "16:00"))
    workload = _reports(events, {1: _agent(_schedule(), [today]),
                                 2: _agent(_schedule(), [tomorrow])})["workload"]["agents"]
    assert workload["1"] == _workload_values(32400, 23400, 0, 0.0, 23400, 5400, 7200, 12600, 1800, 0)
    _identities(workload["1"], 0)
    assert workload["2"] == _workload_values(32400, 23400, 0, 0.0, 23400, 5400, 0, 5400, 9000, 9000)
    _identities(workload["2"], 0)


def test_missing_inputs_are_null_with_a_note() -> None:
    """没有打包输入时两个报告为 null 并写一条说明（行为评估入口不带 workday 也一样）；事件里出现而输入里没有的人物在
    两个报告里都为 null，各写一条说明，其他人物照常报告。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(0, "20:00"))
    events.body(2, _at(0, "07:00"), _at(0, "20:00"))
    assert workday_reports(events.rows, ZONE, None) == {"workload": None, "routine_deviation": None,
                                                        "notes": [NO_INPUT_NOTE]}
    report = behavior_metrics_report(events.rows)[CODE]
    assert (report["reports"]["workload"], report["reports"]["routine_deviation"]) == (None, None)
    assert NO_INPUT_NOTE in report["notes"]
    partial = behavior_metrics_report(events.rows, workday=WorkdayInputs({1: _agent(_schedule())}, 1800))[CODE]
    assert partial["reports"]["workload"]["agents"]["2"] is None
    assert partial["reports"]["routine_deviation"]["agents"]["2"] is None
    assert partial["reports"]["workload"]["agents"]["1"]["absence_seconds"] == 9 * 3600 - 5400
    assert MISSING_AGENT_NOTE.format(agent_id=2) in partial["notes"]
    assert MISSING_AGENT_NOTE.format(agent_id=1) not in partial["notes"] and NO_INPUT_NOTE not in partial["notes"]


def _three_days() -> tuple[_Events, dict[str, str]]:
    """人物 1 周一、周二当班，周三不当班（日期例外）；返回事件与证据事件身份。周一睡到 09:30，17:30 离开办公室、17:50
    到家后小睡到 18:30；周二 18:45 离开、19:10 到家；周三没去办公室，19:40 从商店回到家。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(2, "21:00"))
    events.sleep(1, _at(0, "07:00"), _at(0, "09:30"))
    events.trip(1, _at(0, "09:40"), _at(0, "10:00"), ("home", "office"))
    monday_leave, monday_home = events.trip(1, _at(0, "17:30"), _at(0, "17:50"), ("office", "home"))
    events.sleep(1, _at(0, "17:55"), _at(0, "18:30"))
    events.sleep(1, _at(0, "23:00"), _at(1, "07:00"))
    events.trip(1, _at(1, "08:30"), _at(1, "08:50"), ("home", "office"))
    tuesday_leave, tuesday_home = events.trip(1, _at(1, "18:45"), _at(1, "19:10"), ("office", "home"))
    events.trip(1, _at(2, "18:30"), _at(2, "18:50"), ("home", "shop"))
    _, wednesday_home = events.trip(1, _at(2, "19:20"), _at(2, "19:40"), ("shop", "home"))
    ids = {"monday_leave": monday_leave["event_id"], "monday_home": monday_home["event_id"],
           "tuesday_leave": tuesday_leave["event_id"], "tuesday_home": tuesday_home["event_id"],
           "wednesday_home": wednesday_home["event_id"]}
    return events, ids


def _deviation_inputs() -> dict[int, AgentWorkday]:
    """下班窗口 18:00–18:30、到家窗口 19:00–19:30 的人物 1，周三不当班。"""
    return {1: _agent(_schedule(**{"2026-01-07": None}), leave=(18.0, 18.5), arrive=(19.0, 19.5))}


def test_routine_deviation_reports_three_items_with_evidence_tolerance_and_sleep_flags() -> None:
    """每个本地日一行：离开工作地点的时刻对班次结束与对下班窗口各一项、到家时刻对到家窗口一项（早于窗口为负、晚于为正、
    窗口内为 0），附离开与到家的证据事件身份；班次两端在睡的单独标注（周一睡过了上班时刻、下班时刻在小睡），不当班的
    周三这两项为 null、离开为 null，到家取当天最后一次到家。容差等于播报间隔写进报告，偏离值不按容差抹平（周三 10 分钟
    小于 15 分钟的容差，照报 10）。"""
    events, ids = _three_days()
    deviation = _reports(events, _deviation_inputs(), interval=900)["routine_deviation"]
    assert deviation["tolerance_seconds"] == 900
    monday, tuesday, wednesday = deviation["agents"]["1"]["days"]
    assert monday == {"date": "2026-01-05", "left_workplace_at": "2026-01-05T17:30:00+08:00",
                      "leave_vs_shift_end_minutes": -30.0, "leave_vs_window_minutes": -30.0,
                      "arrived_home_at": "2026-01-05T17:50:00+08:00", "arrive_home_vs_window_minutes": -70.0,
                      "asleep_at_shift_start": True, "asleep_at_shift_end": True,
                      "evidence_event_ids": {"left_workplace": ids["monday_leave"],
                                             "arrived_home": ids["monday_home"]}}
    assert tuesday == {"date": "2026-01-06", "left_workplace_at": "2026-01-06T18:45:00+08:00",
                       "leave_vs_shift_end_minutes": 45.0, "leave_vs_window_minutes": 15.0,
                       "arrived_home_at": "2026-01-06T19:10:00+08:00", "arrive_home_vs_window_minutes": 0.0,
                       "asleep_at_shift_start": False, "asleep_at_shift_end": False,
                       "evidence_event_ids": {"left_workplace": ids["tuesday_leave"],
                                              "arrived_home": ids["tuesday_home"]}}
    assert wednesday == {"date": "2026-01-07", "left_workplace_at": None, "leave_vs_shift_end_minutes": None,
                         "leave_vs_window_minutes": None, "arrived_home_at": "2026-01-07T19:40:00+08:00",
                         "arrive_home_vs_window_minutes": 10.0, "asleep_at_shift_start": None,
                         "asleep_at_shift_end": None,
                         "evidence_event_ids": {"left_workplace": None, "arrived_home": ids["wednesday_home"]}}


def test_people_without_a_schedule_have_null_leave_items_and_still_report_arrival_home() -> None:
    """没有工作安排的人物不进 workload；它去了办公室也没有「离开工作地点」，离开的两项与班次两端在睡都为 null，到家取
    当天最后一次到家、对到家窗口照常报偏离。输入里有、事件里没有的人物：有工作安排时十个量都是 0，没有日子可报。"""
    events = _Events()
    events.body(2, _at(0, "07:00"), _at(0, "22:00"))
    events.trip(2, _at(0, "08:30"), _at(0, "09:00"), ("home", "office"))
    _, home = events.trip(2, _at(0, "17:00"), _at(0, "18:00"), ("office", "home"))
    reports = _reports(events, {1: _agent(_schedule()), 2: _agent(None, leave=(18.0, 18.5), arrive=(19.0, 19.5))})
    assert reports["workload"]["agents"] == {"1": dict.fromkeys(WORKLOAD_KEYS, 0)}
    assert reports["routine_deviation"]["agents"]["1"] == {"days": []}
    assert reports["routine_deviation"]["agents"]["2"]["days"] == [{
        "date": "2026-01-05", "left_workplace_at": None, "leave_vs_shift_end_minutes": None,
        "leave_vs_window_minutes": None, "arrived_home_at": "2026-01-05T18:00:00+08:00",
        "arrive_home_vs_window_minutes": -60.0, "asleep_at_shift_start": None, "asleep_at_shift_end": None,
        "evidence_event_ids": {"left_workplace": None, "arrived_home": home["event_id"]}}]


def _keys(value: Any) -> set[str]:
    """嵌套字典与列表里出现的全部键。"""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_routine_deviation_uses_none_of_the_removed_plan_deviation_names() -> None:
    """两个新报告的任何一层键都不是已删除的计划偏离指标及其字段名，报告里也没有 plan_actual_deviation。"""
    events, _ = _three_days()
    reports = _reports(events, _deviation_inputs())
    names = _keys(reports["routine_deviation"]) | _keys(reports["workload"])
    assert "left_workplace_at" in names and "absence_seconds" in names
    assert names.isdisjoint(REMOVED_DEVIATION_NAMES)
    assert "plan_actual_deviation" not in reports


def test_workday_inputs_take_shifts_tasks_home_windows_and_interval_from_the_scenario_and_cards(tmp_path) -> None:
    """打包输入取自经真实解析的场景文档与结构化卡片：双人场景给人物 1 声明工作安排、人物 2 写 null，播报间隔改为 1200，
    人物 2 的一项任务改成裸编号指派并带 15:00 的预约。工作安排、住处与播报间隔照场景；任务按指派人物分开（两种写法都
    认），开放时刻是最早的本人通知或预约开始，预约任务带约定开始；下班与到家窗口只取卡片（覆盖已写进卡片），卡片里
    为 null、没有卡片或卡片缺席时为 None。"""
    def mutate(document: dict) -> None:
        """声明工作安排、改播报间隔、把一项任务改成裸编号指派的预约任务。"""
        declare(0)(document)
        document["progress_report_interval_seconds"] = 1200
        task = next(item for item in document["tasks"] if item["task_id"] == "backend-piece-2026-01-05")
        task.update(assignee_agent_id="2", appointment={"starts_at": "2026-01-05T15:00:00+08:00", "reason": "现场"})

    path = _mutated_scenario(Path("scenarios/multi-person.json"), mutate, tmp_path / "scenario")
    document, _ = parse_scenario_document(path)
    card = {"time_anchors": {"leave_work": {"start_hour": 18.0, "end_hour": 18.5}, "arrive_home": None}}
    inputs = workday_inputs(document, {1: card})
    assert inputs.report_interval_seconds == 1200 and sorted(inputs.agents) == [1, 2]
    first, second = inputs.agents[1], inputs.agents[2]
    assert first.work_schedule == document.agents[0].work_schedule and first.work_schedule is not None
    assert second.work_schedule is None
    assert (first.home_location_id, second.home_location_id) == ("home", "home")
    assert (first.leave_work_window, first.arrive_home_window) == ((18.0, 18.5), None)
    assert (second.leave_work_window, second.arrive_home_window) == (None, None)
    notices = {}
    for message in document.messages:
        if message.related_task_id is not None:
            stamp = int(message.sent_at.timestamp())
            notices[message.related_task_id] = min(stamp, notices.get(message.related_task_id, stamp))
    expected = {agent_id: [] for agent_id in (1, 2)}
    for task in document.tasks:
        owner = 2 if task.task_id.startswith("backend") else 1
        appointment = None if task.appointment is None else int(task.appointment.starts_at.timestamp())
        expected[owner].append(TaskSite(task.task_id, task.required_location_id,
                                        appointment if appointment is not None else notices[task.task_id],
                                        appointment))
    assert list(first.tasks) == expected[1] and list(second.tasks) == expected[2]
    assert [site.appointment_at for site in second.tasks] == [None, None, _at(0, "15:00")]
    assert workday_inputs(document, {}).agents[1].leave_work_window is None


def _workday_run(document: dict) -> None:
    """真实运行用的场景副本：人物 1 周一 09:00–18:00 当班、午休 12:00–13:30；运行从周一 07:30 在家开始（同零点计划
    用例的晚启动改写），等待与播报间隔放长到两小时、重估间隔放长到三小时，少一些途中的决策机会。"""
    declare()(document)
    _late_start("2026-01-05T07:30:00+08:00", activity_feedback_seconds=7200, progress_report_interval_seconds=7200,
                reconsideration_interval_seconds=10800)(document)


def test_a_real_run_leaving_on_duty_is_recomputed_as_early_leave(engine_factory) -> None:
    """真实运行（只发真实请求，不预置地点）：周一 09:00–18:00 当班的人物 07:30 在家等着，08:00 首个任务的通知到后步行
    去办公室、09:00 前到达并开工；15:00:00 收工、同一秒步行回家，一直运行到 18:30。从 SQLite 读回全部事件、用改写后的
    场景文档打包输入（没有卡片）经 behavior_metrics_report 重算：班次 9 小时都在观察窗内，到达之后到 15:00 都在办公室
    工作（午休也在），15:00 之后的 3 小时既是缺勤也是早退，恒等关系成立；当天离开工作地点比班次结束早 180 分钟，没有
    卡片所以对下班窗口的一项为 null，离开的证据是回家那一程的首个进度事件，容差是场景的播报间隔。"""
    engine = engine_factory(mutate=_workday_run)
    _leave_on_duty_at_three(engine)
    events = [event.model_dump(mode="json") for event in engine.context.store.read_events(engine.context.store.last_event_seq)]
    inputs = workday_inputs(engine.context.semantics.document, {1: None})
    [group] = behavior_metrics_report(events, workday=inputs).values()
    reports = group["reports"]
    row = reports["workload"]["agents"]["1"]
    assert (row["shift_seconds"], row["early_leave_seconds"], row["absence_seconds"]) == (32400, 10800, 10800), row
    assert (row["at_workplace_seconds"], row["task_outing_seconds"], row["break_seconds"]) == (21600, 0, 5400), row
    assert (row["work_activity_seconds"], row["idle_at_workplace_seconds"]) == (21600, 0), row
    _identities(row, 0)
    assert reports["routine_deviation"]["tolerance_seconds"] == 7200
    [today] = reports["routine_deviation"]["agents"]["1"]["days"]
    assert (today["date"], today["left_workplace_at"]) == ("2026-01-05", "2026-01-05T15:00:00+08:00")
    assert (today["leave_vs_shift_end_minutes"], today["leave_vs_window_minutes"]) == (-180.0, None)
    [leaving] = [event for event in events if event["event_type"] == "activity_progress"
                 and event["data"]["activity"]["kind"] == "travel"
                 and event["data"]["activity"]["started_at"] == _clock("15:00:00")][:1]
    assert today["evidence_event_ids"]["left_workplace"] == leaving["event_id"]


def _leave_on_duty_at_three(engine) -> None:
    """只发真实请求驱动 _workday_run 改写的运行：08:00 首个任务的通知到后从家步行去办公室、09:00 前到达并开工；
    15:00:00 收工、同一秒步行回家，一直运行到 18:30（班次整个落在观察窗内）。"""
    office = f"地点：{next(item.name for item in engine.context.scenario.locations if item.location_id == 'office')}"
    home = f"地点：{next(item.name for item in engine.context.scenario.locations if item.location_id == 'home')}"
    _decide_at(engine, _clock("08:00:00"))
    _empty_handed(engine, "stop-waiting")
    assert _decide(engine, "commute", "start_activity",
                   lambda observed: layer_start(observed, office, "步行")).status == "in_progress"
    _drive(engine, "to-office", lambda: engine.context.state.agents[1].location_id == "office")
    assert engine.context.state.current_time < _clock("09:00:00")
    _empty_handed(engine, "arrived")
    _start_work(engine, "work-start")
    _decide_at(engine, _clock("15:00:00"))
    _empty_handed(engine, "stop-work")
    assert _decide(engine, "leave", "start_activity",
                   lambda observed: layer_start(observed, home, "步行")).status == "in_progress"
    _decide_at(engine, _clock("18:30:00"))


def _empty_handed(engine, name: str) -> None:
    """同一秒经真实 conclude_activity 结束人物 1 手上全部进行中与待收尾的活动（例如等待、刚到达的那一程、工作），让
    下一个请求从空手开始；手上没有活动时不发请求。"""
    open_ids = {item.activity_id for item in engine.context.state.activities.values()
                if item.agent_id == 1 and item.concluded_at is None and item.status != "paused"}
    if not open_ids:
        return

    def request(observed: dict):
        """本轮观察里这些活动的可读名称。"""
        return conclude_request(*[row["name"] for row in observed["activity_bindings"]
                                  if row["activity_id"] in open_ids])

    assert _decide(engine, name, "conclude_activity", request).status == "completed"


#: 任务外出用例：周一首个任务改成约在集市（场景所选地点，不是工作地点）11:00 开始的客户预约；08:00 的本人通知照旧，
#: 预约任务在约定开始时开放，当晚截止。
_OUTING_TASK, _OUTING_SITE, _APPOINTMENT = "task-2026-01-05", "market", "11:00:00"


def _outing_run(exceptions: dict):
    """返回 mutate(document)：同 _workday_run 的工作安排与晚启动，exceptions 按日期覆盖工作安排；再把周一首个任务改成
    约在集市 11:00 开始的预约任务。"""
    def mutate(document: dict) -> None:
        """写工作安排与晚启动，把任务改成集市的预约任务。"""
        declare(exceptions=exceptions)(document)
        _late_start("2026-01-05T07:30:00+08:00", activity_feedback_seconds=7200,
                    progress_report_interval_seconds=7200, reconsideration_interval_seconds=10800)(document)
        task = next(item for item in document["tasks"] if item["task_id"] == _OUTING_TASK)
        task.update(required_location_id=_OUTING_SITE,
                    appointment={"starts_at": f"2026-01-05T{_APPOINTMENT}+08:00", "reason": "客户现场会"})

    return mutate


def _place(engine, location_id: str) -> str:
    """工具层出行目标名「地点：<显示名>」。"""
    return f"地点：{next(item.name for item in engine.context.scenario.locations if item.location_id == location_id)}"


def _walk_to(engine, name: str, location_id: str) -> None:
    """此刻空手，经真实 start_activity 步行去 location_id，驱动到到达并收尾到达的那一程。"""
    assert _decide(engine, name, "start_activity",
                   lambda observed: layer_start(observed, _place(engine, location_id), "步行")).status == "in_progress"
    _drive(engine, f"to-{name}", lambda: engine.context.state.agents[1].location_id == location_id)
    _empty_handed(engine, f"arrived-{name}")


def _task_outing_day(engine) -> int:
    """只发真实请求驱动 _outing_run 改写的运行，返回回到办公室的时刻：08:00 通知到后步行去办公室、09:00 前到达；
    10:00:00 从办公室步行去集市赴 11:00 的预约（约定开始之前出发、提前到达后等着），11:00:00 在那里开工；14:00:00
    收工、同一秒直接走回办公室，一直待到 18:30。"""
    _decide_at(engine, _clock("08:00:00"))
    _empty_handed(engine, "stop-waiting")
    _walk_to(engine, "office", "office")
    assert engine.context.state.current_time < _clock("09:00:00")
    _decide_at(engine, _clock("10:00:00"))
    _empty_handed(engine, "stop-waiting-at-office")
    _walk_to(engine, "market", _OUTING_SITE)
    assert engine.context.state.current_time < _clock(_APPOINTMENT)
    _decide_at(engine, _clock(_APPOINTMENT))
    _empty_handed(engine, "stop-waiting-at-market")
    _start_work(engine, "work-at-market")
    _decide_at(engine, _clock("14:00:00"))
    _empty_handed(engine, "stop-work")
    _walk_to(engine, "back", "office")
    back = engine.context.state.current_time
    _decide_at(engine, _clock("18:30:00"))
    return back


def _recomputed(engine) -> dict:
    """从 SQLite 读回全部事件，用运行自己的场景文档打包输入（没有卡片），经 behavior_metrics_report 重算 reports。"""
    events = [event.model_dump(mode="json") for event in engine.context.store.read_events(engine.context.store.last_event_seq)]
    [group] = behavior_metrics_report(events, workday=workday_inputs(engine.context.semantics.document, {1: None})).values()
    return group["reports"]


def test_a_real_task_outing_is_not_absence_and_a_declared_rest_day_counts_nothing(engine_factory) -> None:
    """真实运行（engine_factory 加 declare，只发真实请求、不预置地点）：周一 09:00–18:00 当班、午休 12:00–13:30，首个
    任务改成约在集市 11:00 开始的预约任务。人物 09:00 前步行到办公室，10:00 去集市赴约、提前到达后等到 11:00 开工（午休
    也在那里），14:00 直接走回办公室并待到 18:30。从 SQLite 读回的事件与运行自己的场景文档经 behavior_metrics_report
    重算：10:00 到回到办公室都是任务外出（扣掉午休；约定开始之前的路上与等候也算），没有缺勤、没有早退，在工作地点与
    任务外出加上不在办公室的午休恰好是班次。同一行程在场景按日期例外把周一写成不当班的另一次真实运行里，周一不产生
    班次，十个量都是 0，作息偏离对班次的两项为 null。"""
    engine = engine_factory(mutate=_outing_run({}))
    back = _task_outing_day(engine)
    row = _recomputed(engine)["workload"]["agents"]["1"]
    away = back - _clock("10:00:00")
    assert (row["shift_seconds"], row["absence_seconds"], row["early_leave_seconds"]) == (32400, 0, 0), row
    assert (row["break_seconds"], row["task_outing_seconds"]) == (5400, away - 5400), row
    assert row["at_workplace_seconds"] == 32400 - away == row["idle_at_workplace_seconds"], row
    assert row["work_activity_seconds"] > 0, row
    _identities(row, 5400)

    rest = engine_factory(mutate=_outing_run({"2026-01-05": None}))
    assert _task_outing_day(rest) == back
    reports = _recomputed(rest)
    assert reports["workload"]["agents"]["1"] == dict.fromkeys(WORKLOAD_KEYS, 0)
    [today] = reports["routine_deviation"]["agents"]["1"]["days"]
    assert today["left_workplace_at"] == "2026-01-05T10:00:00+08:00"
    assert (today["leave_vs_shift_end_minutes"], today["asleep_at_shift_start"],
            today["asleep_at_shift_end"]) == (None, None, None)


def test_reports_are_byte_reproducible() -> None:
    """同一输入两次计算（第二次把事件顺序倒过来）的规范 JSON 逐字节相同。"""
    events, _ = _three_days()
    inputs = WorkdayInputs(_deviation_inputs(), 1800)
    first = workday_reports(events.rows, ZONE, inputs)
    second = workday_reports(list(reversed(events.rows)), ZONE, inputs)
    assert canonical_json(first) == canonical_json(second)
    assert json.loads(canonical_json(first))["routine_deviation"]["agents"]["1"]["days"]


def _office_day(events: _Events, agent_id: int = 1, end: str = "20:00") -> None:
    """@brief 周一 07:00 起、到 end 为止的观察窗，08:30–08:50 从家到办公室。"""
    events.body(agent_id, _at(0, "07:00"), _at(0, end))
    events.trip(agent_id, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))


def _day_row(day: str, leave: tuple, home: tuple, asleep: tuple) -> dict:
    """@brief 作息偏离的一行：leave 为（离开工作地点的时刻, 对班次结束, 对下班窗口, 证据事件身份），home 为（到家时刻,
    对到家窗口, 证据事件身份），asleep 为班次开始与结束时是否在睡。"""
    return {"date": day, "left_workplace_at": leave[0], "leave_vs_shift_end_minutes": leave[1],
            "leave_vs_window_minutes": leave[2], "arrived_home_at": home[0],
            "arrive_home_vs_window_minutes": home[1], "asleep_at_shift_start": asleep[0],
            "asleep_at_shift_end": asleep[1],
            "evidence_event_ids": {"left_workplace": leave[3], "arrived_home": home[2]}}


def test_workload_is_the_sum_over_every_duty_day() -> None:
    """@brief 十个量是各当班日之和（评估设计 2.2「在各当班日上的合计」）：周一、周二都当班。周一整个班次在办公室、
    09:00–12:00 以效率 1.0 工作、18:10 才走；周二 13:30–16:30 以效率 0.5 工作、17:00 离开（一小时的缺勤与早退）。
    每个量都是两天相加，不是最后一个当班日的值。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(1, "20:00"))
    events.trip(1, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.work(1, _at(0, "09:00"), _at(0, "12:00"), 1.0)
    events.trip(1, _at(0, "18:10"), _at(0, "18:40"), ("office", "home"))
    events.trip(1, _at(1, "08:30"), _at(1, "08:50"), ("home", "office"))
    events.work(1, _at(1, "13:30"), _at(1, "16:30"), 0.5)
    events.trip(1, _at(1, "17:00"), _at(1, "17:30"), ("office", "home"))
    row = _reports(events, {1: _agent(_schedule())})["workload"]["agents"]["1"]
    assert row == _workload_values(64800, 61200, 21600, 16200.0, 39600, 10800, 0, 10800, 3600, 3600)
    _identities(row, 0)


def test_leaving_and_coming_back_before_shift_end_is_not_early_leave() -> None:
    """@brief 早退要求「此后班次结束前没有回到工作地点」。人物 1 14:00 离开、14:40 回到办公室并待到下班：离开的
    40 分钟是缺勤，早退为 0。人物 2 16:00 离开、恰在 18:00 整才回到（区间半开，班次结束前没有回来）：两小时既是缺勤
    也是早退。"""
    events = _Events()
    _office_day(events, 1)
    events.trip(1, _at(0, "14:00"), _at(0, "14:10"), ("office", "cafe"))
    events.trip(1, _at(0, "14:30"), _at(0, "14:40"), ("cafe", "office"))
    _office_day(events, 2)
    events.trip(2, _at(0, "16:00"), _at(0, "16:20"), ("office", "cafe"))
    events.trip(2, _at(0, "17:40"), _at(0, "18:00"), ("cafe", "office"))
    rows = _reports(events, {1: _agent(_schedule()), 2: _agent(_schedule())})["workload"]["agents"]
    assert rows["1"] == _workload_values(32400, 30000, 0, 0.0, 30000, 5400, 0, 5400, 2400, 0)
    _identities(rows["1"], 0)
    assert rows["2"] == _workload_values(32400, 25200, 0, 0.0, 25200, 5400, 0, 5400, 7200, 7200)
    _identities(rows["2"], 0)


def test_a_departure_before_the_shift_starts_is_absence_not_early_leave() -> None:
    """@brief 早退只看班次内从工作地点的出发。人物 1 班前到过办公室、08:30 就走了，再没回来；人物 2 周一 18:10 下班，
    周二整天没来：两人都没有班次内的出发，早退为 0，没来的班次时间（扣掉休息时段）是缺勤，早退不超过班次。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(0, "20:00"))
    events.trip(1, _at(0, "07:10"), _at(0, "07:30"), ("home", "office"))
    events.trip(1, _at(0, "08:30"), _at(0, "08:50"), ("office", "home"))
    events.body(2, _at(0, "07:00"), _at(1, "20:00"))
    events.trip(2, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    events.trip(2, _at(0, "18:10"), _at(0, "18:40"), ("office", "home"))
    rows = _reports(events, {1: _agent(_schedule()), 2: _agent(_schedule())})["workload"]["agents"]
    assert rows["1"] == _workload_values(32400, 0, 0, 0.0, 0, 5400, 0, 5400, 27000, 0)
    _identities(rows["1"], 5400)
    assert rows["2"] == _workload_values(64800, 32400, 0, 0.0, 32400, 10800, 0, 10800, 27000, 0)
    _identities(rows["2"], 5400)


def test_effective_work_counts_only_the_part_of_an_interval_inside_the_shift() -> None:
    """@brief 有效工作秒数是工作进度区间落在班次内的时长乘该区间的效率（规格 4.7 允许班前班后工作）：08:30–10:00 以
    效率 1.0、17:30–18:30 以效率 0.5 工作，班次内的部分是 3600 与 1800 秒，有效工时 3600 + 900。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(0, "20:00"))
    events.trip(1, _at(0, "08:00"), _at(0, "08:20"), ("home", "office"))
    events.work(1, _at(0, "08:30"), _at(0, "10:00"), 1.0)
    events.work(1, _at(0, "17:30"), _at(0, "18:30"), 0.5)
    row = _reports(events, {1: _agent(_schedule())})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 32400, 5400, 4500.0, 27000, 5400, 0, 5400, 0, 0)
    _identities(row, 0)


def test_a_real_run_counts_effective_work_only_inside_the_shift(engine_factory) -> None:
    """@brief 真实运行（同「班内 15:00 离开」的驱动，只发真实请求）：人物 09:00 前到办公室就开工，班前的那段工作进度
    不计入有效工时——从 SQLite 读回的工作进度区间各自截到 09:00–18:00、乘该区间的效率再求和，等于重算的有效工作秒数，
    并且小于不截取的总和。"""
    engine = engine_factory(mutate=_workday_run)
    _leave_on_duty_at_three(engine)
    events = [event.model_dump(mode="json") for event in engine.context.store.read_events(engine.context.store.last_event_seq)]
    work = [(event["data"]["interval_start"], event["data"]["interval_end"], event["data"]["activity_efficiency"])
            for event in events
            if event["event_type"] == "activity_progress" and event["data"]["activity"]["kind"] == "work"]
    assert min(start for start, _, _ in work) < _clock("09:00:00")
    inside = round(sum(max(0, min(end, _clock("18:00:00")) - max(start, _clock("09:00:00"))) * efficiency
                       for start, end, efficiency in work), 6)
    assert 0 < inside < round(sum((end - start) * efficiency for start, end, efficiency in work), 6)
    row = _recomputed(engine)["workload"]["agents"]["1"]
    assert row["effective_work_seconds"] == inside, row


def test_a_trip_given_up_on_the_way_leaves_the_person_at_its_origin() -> None:
    """@brief 所在地点只由出行重建，没有到达的出行结束后人回到出发地：10:00 从办公室出发、10:20 途中结束这一程（没有
    到达），之后一直在办公室、18:10 才下班。只有路上的 20 分钟是缺勤，不是早退，其余班次时间都在工作地点。"""
    events = _Events()
    _office_day(events)
    events.trip(1, _at(0, "10:00"), _at(0, "10:20"), ("office", "canteen"), arrived=False)
    events.trip(1, _at(0, "18:10"), _at(0, "18:40"), ("office", "home"))
    row = _reports(events, {1: _agent(_schedule())})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 31200, 0, 0.0, 31200, 5400, 0, 5400, 1200, 0)
    _identities(row, 0)


def test_a_real_trip_given_up_on_the_way_keeps_the_person_at_the_workplace(engine_factory) -> None:
    """@brief 真实运行（只发真实请求、不预置地点）：09:00 前步行到办公室并开工，15:00 收工、步行回家，15:10 还在路上时
    经真实 conclude_activity 结束这一程——没有到达，引擎里人物仍在办公室——之后在办公室待到 18:30。从 SQLite 读回的事件
    重算：只有路上的 10 分钟是缺勤，不是早退，其余班次时间都在工作地点，恒等关系成立。"""
    engine = engine_factory(mutate=_workday_run)
    _decide_at(engine, _clock("08:00:00"))
    _empty_handed(engine, "stop-waiting")
    assert _decide(engine, "commute", "start_activity",
                   lambda observed: layer_start(observed, _place(engine, "office"), "步行")).status == "in_progress"
    _drive(engine, "to-office", lambda: engine.context.state.agents[1].location_id == "office")
    assert engine.context.state.current_time < _clock("09:00:00")
    _empty_handed(engine, "arrived")
    _start_work(engine, "work-start")
    _decide_at(engine, _clock("15:00:00"))
    _empty_handed(engine, "stop-work")
    assert _decide(engine, "leave", "start_activity",
                   lambda observed: layer_start(observed, _place(engine, "home"), "步行")).status == "in_progress"
    _decide_at(engine, _clock("15:10:00"))
    assert engine.context.state.agents[1].location_id == "office"
    _empty_handed(engine, "turn-back")
    _decide_at(engine, _clock("18:30:00"))
    assert engine.context.state.agents[1].location_id == "office"
    row = _recomputed(engine)["workload"]["agents"]["1"]
    assert (row["shift_seconds"], row["at_workplace_seconds"], row["absence_seconds"],
            row["early_leave_seconds"]) == (32400, 31800, 600, 0), row
    _identities(row, 0)


def test_visiting_a_settled_task_site_and_walking_back_is_absence() -> None:
    """@brief 任务外出只算本人已开放且未结清的任务：任务 10:00 开放、11:30 付款结清，人物 14:00 仍去客户处、15:00 走回
    办公室。去的路上、在那里的时间与回办公室的一程都是缺勤，没有任务外出（回程只在离开前那段停留里任务仍未结清时
    才算）。"""
    events = _Events()
    _office_day(events)
    events.paid(1, _at(0, "11:30"), "task-client")
    events.trip(1, _at(0, "14:00"), _at(0, "14:20"), ("office", "client"))
    events.trip(1, _at(0, "15:00"), _at(0, "15:20"), ("client", "office"))
    client = TaskSite("task-client", "client", _at(0, "10:00"), None)
    row = _reports(events, {1: _agent(_schedule(), [client])})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 27600, 0, 0.0, 27600, 5400, 0, 5400, 4800, 0)
    _identities(row, 0)


def test_a_late_return_from_lunch_is_absence_even_with_an_open_task_elsewhere() -> None:
    """@brief 回程的例外只是「从任务要求地点直接回工作地点的一程」：当天有开放的预约任务（约在客户处 16:00），人物午休
    出去吃饭、13:45 才从食堂回到办公室。这一程不是从任务地点出发，午休结束后还在路上的 15 分钟是缺勤，不是任务外出。"""
    events = _Events()
    _office_day(events)
    events.trip(1, _at(0, "12:10"), _at(0, "12:20"), ("office", "canteen"))
    events.trip(1, _at(0, "13:25"), _at(0, "13:45"), ("canteen", "office"))
    visit = TaskSite("task-visit", "client", _at(0, "16:00"), _at(0, "16:00"))
    row = _reports(events, {1: _agent(_schedule(), [visit])})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 26700, 0, 0.0, 26700, 5400, 0, 5400, 900, 0)
    _identities(row, 4800)


def test_leaving_for_the_task_site_in_the_second_the_task_opens_is_not_early_leave() -> None:
    """@brief 任务从开放的那一秒起就是已开放（区间半开，含起点）：任务 10:00 开放，人物同一秒从办公室出发去客户处并
    待到下班。10:00 之后扣掉休息时段都是任务外出，不是早退，也没有缺勤。"""
    events = _Events()
    _office_day(events)
    events.trip(1, _at(0, "10:00"), _at(0, "10:30"), ("office", "client"))
    client = TaskSite("task-client", "client", _at(0, "10:00"), None)
    row = _reports(events, {1: _agent(_schedule(), [client])})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 3600, 0, 0.0, 3600, 5400, 23400, 28800, 0, 0)
    _identities(row, 5400)


def test_left_workplace_is_the_last_departure_and_arrival_home_the_first_one_after_it() -> None:
    """@brief 离开工作地点取当天最后一次出发，到家取其后当天的首次到家。两人中午都回家吃饭、下午回办公室。人物 1 18:05
    下班、18:35 到家，之后去商店、20:20 再到家：离开是 18:05（比班次结束晚 5 分钟、在下班窗口内），到家是 18:35，不是
    中午的 12:20，也不是晚上的 20:20，证据是 18:05 那一程的首个进度事件与它的到达事件。人物 2 18:00 下班去了健身房、
    当天没再到家：到家与它的偏离、证据都为 null，中午那次到家不算。"""
    events = _Events()
    for agent_id in (1, 2):
        events.body(agent_id, _at(0, "07:00"), _at(0, "22:00"))
        events.trip(agent_id, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
        events.trip(agent_id, _at(0, "12:00"), _at(0, "12:20"), ("office", "home"))
        events.trip(agent_id, _at(0, "13:00"), _at(0, "13:20"), ("home", "office"))
    leave, home = events.trip(1, _at(0, "18:05"), _at(0, "18:35"), ("office", "home"))
    events.trip(1, _at(0, "19:00"), _at(0, "19:20"), ("home", "shop"))
    events.trip(1, _at(0, "20:00"), _at(0, "20:20"), ("shop", "home"))
    gym, _ = events.trip(2, _at(0, "18:00"), _at(0, "18:20"), ("office", "gym"))
    windows = {"leave": (18.0, 18.5), "arrive": (18.5, 19.0)}
    agents = _reports(events, {1: _agent(_schedule(), **windows),
                               2: _agent(_schedule(), **windows)})["routine_deviation"]["agents"]
    assert agents["1"]["days"] == [_day_row("2026-01-05", ("2026-01-05T18:05:00+08:00", 5.0, 0.0, leave["event_id"]),
                                            ("2026-01-05T18:35:00+08:00", 0.0, home["event_id"]), (False, False))]
    assert agents["2"]["days"] == [_day_row("2026-01-05", ("2026-01-05T18:00:00+08:00", 0.0, 0.0, gym["event_id"]),
                                            (None, None, None), (False, False))]


def test_arrival_home_without_a_departure_is_the_last_one_of_that_local_day() -> None:
    """@brief 当天没有离开工作地点时取当天最后一次到家，「当天」是那个本地日。人物 2 没有工作安排，周一 13:00、20:00
    两次到家，周二 21:30 到家：每天各取当天最后一次（周一 20:00，不是 13:00，也不是周二的 21:30）。人物 3 周一 18:00
    下班去酒吧、周二 01:00 才到家：周一的到家为 null；周二按日期例外不当班，取当天的 01:00。"""
    events = _Events()
    events.body(2, _at(0, "07:00"), _at(1, "22:00"))
    events.trip(2, _at(0, "11:00"), _at(0, "11:20"), ("home", "shop"))
    events.trip(2, _at(0, "12:40"), _at(0, "13:00"), ("shop", "home"))
    events.trip(2, _at(0, "18:00"), _at(0, "18:20"), ("home", "shop"))
    _, evening = events.trip(2, _at(0, "19:40"), _at(0, "20:00"), ("shop", "home"))
    events.trip(2, _at(1, "10:00"), _at(1, "10:20"), ("home", "shop"))
    _, next_day = events.trip(2, _at(1, "21:00"), _at(1, "21:30"), ("shop", "home"))
    events.body(3, _at(0, "07:00"), _at(1, "20:00"))
    events.trip(3, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    bar, _ = events.trip(3, _at(0, "18:00"), _at(0, "18:20"), ("office", "bar"))
    _, late = events.trip(3, _at(1, "00:40"), _at(1, "01:00"), ("bar", "home"))
    agents = _reports(events, {
        2: _agent(None, arrive=(19.0, 19.5)),
        3: _agent(_schedule(**{"2026-01-06": None}), leave=(18.0, 18.5), arrive=(18.5, 19.0)),
    })["routine_deviation"]["agents"]
    nowhere, unknown = (None, None, None, None), (None, None)
    assert agents["2"]["days"] == [
        _day_row("2026-01-05", nowhere, ("2026-01-05T20:00:00+08:00", 30.0, evening["event_id"]), unknown),
        _day_row("2026-01-06", nowhere, ("2026-01-06T21:30:00+08:00", 120.0, next_day["event_id"]), unknown)]
    assert agents["3"]["days"] == [
        _day_row("2026-01-05", ("2026-01-05T18:00:00+08:00", 0.0, 0.0, bar["event_id"]), (None, None, None),
                 (False, False)),
        _day_row("2026-01-06", nowhere, ("2026-01-06T01:00:00+08:00", -1050.0, late["event_id"]), unknown)]


def test_leave_deviation_uses_the_declared_shift_end_when_the_run_is_cut_short() -> None:
    """@brief 作息偏离的参照是场景班次，不按观察窗截取：观察窗 15:00 结束，人物 14:00 离开办公室，对班次结束（18:00）
    早 240 分钟；下班时刻不在观察窗内，是否在睡为 null。截到观察窗的只是 workload 的班次：早退算到 15:00，一小时。"""
    events = _Events()
    _office_day(events, end="15:00")
    events.trip(1, _at(0, "14:00"), _at(0, "14:30"), ("office", "home"))
    reports = _reports(events, {1: _agent(_schedule())})
    [today] = reports["routine_deviation"]["agents"]["1"]["days"]
    assert (today["left_workplace_at"], today["leave_vs_shift_end_minutes"]) == ("2026-01-05T14:00:00+08:00", -240.0)
    assert (today["asleep_at_shift_start"], today["asleep_at_shift_end"]) == (False, None)
    assert reports["workload"]["agents"]["1"]["early_leave_seconds"] == 3600


def test_a_departure_on_a_rest_day_is_still_measured_against_the_leave_window() -> None:
    """@brief 没有班次的日子只有对班次结束的偏离与两个在睡标注为 null，对下班窗口的一项照常报告：按日期例外不当班的
    周一，人物去了办公室、17:00 离开，下班窗口 18:00–18:30。对班次结束为 null，对下班窗口早 60 分钟；卡片没有到家
    窗口，到家的偏离为 null 而到家时刻与证据照报。"""
    events = _Events()
    _office_day(events)
    leave, home = events.trip(1, _at(0, "17:00"), _at(0, "17:30"), ("office", "home"))
    reports = _reports(events, {1: _agent(_schedule(**{"2026-01-05": None}), leave=(18.0, 18.5))})
    assert reports["routine_deviation"]["agents"]["1"]["days"] == [
        _day_row("2026-01-05", ("2026-01-05T17:00:00+08:00", None, -60.0, leave["event_id"]),
                 ("2026-01-05T17:30:00+08:00", None, home["event_id"]), (None, None))]
    assert reports["workload"]["agents"]["1"] == dict.fromkeys(WORKLOAD_KEYS, 0)


def test_a_window_that_ends_at_local_midnight_has_no_row_for_the_next_day() -> None:
    """@brief 观察窗覆盖的每个本地日一行，区间半开：观察窗在周二零点整结束（正式场景的结束时刻就是零点），只有周一
    一行，没有观察不到的周二。"""
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(1, "00:00"))
    events.trip(1, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    days = _reports(events, {1: _agent(_schedule())})["routine_deviation"]["agents"]["1"]["days"]
    assert [day["date"] for day in days] == ["2026-01-05"]


def test_break_seconds_are_clipped_to_the_observed_part_of_the_shift() -> None:
    """@brief 班次截到观察窗，休息时段只算班次内（观察到）的部分。人物 1 的观察窗从 12:30（午休中）开始、13:00 到
    办公室、18:10 下班：休息时段是 12:30–13:30 的一小时。人物 2 的观察窗在 12:30 结束：休息时段是 12:00–12:30 的半
    小时。休息时段都不超过截取后的班次，恒等关系成立。"""
    events = _Events()
    events.body(1, _at(0, "12:30"), _at(0, "20:00"))
    events.trip(1, _at(0, "12:40"), _at(0, "13:00"), ("home", "office"))
    events.trip(1, _at(0, "18:10"), _at(0, "18:40"), ("office", "home"))
    events.body(2, _at(0, "07:00"), _at(0, "12:30"))
    events.trip(2, _at(0, "08:30"), _at(0, "08:50"), ("home", "office"))
    rows = _reports(events, {1: _agent(_schedule()), 2: _agent(_schedule())})["workload"]["agents"]
    assert rows["1"] == _workload_values(19800, 18000, 0, 0.0, 18000, 3600, 0, 3600, 0, 0)
    _identities(rows["1"], 1800)
    assert rows["2"] == _workload_values(12600, 12600, 0, 0.0, 12600, 1800, 0, 1800, 0, 0)
    _identities(rows["2"], 0)


def test_leaving_at_the_instant_the_break_ends_is_early_leave() -> None:
    """@brief 休息时段半开（规格 4.3）：只有在休息时段内的出发不算早退。人物 13:30 整——休息时段结束的那一秒——离开
    办公室、没再回来，是 4.5 小时的缺勤，也是 4.5 小时的早退。"""
    events = _Events()
    _office_day(events)
    events.trip(1, _at(0, "13:30"), _at(0, "14:00"), ("office", "home"))
    row = _reports(events, {1: _agent(_schedule())})["workload"]["agents"]["1"]
    assert row == _workload_values(32400, 16200, 0, 0.0, 16200, 5400, 0, 5400, 16200, 16200)
    _identities(row, 0)


def test_sleep_that_ends_at_a_shift_boundary_is_awake_at_that_boundary() -> None:
    """@brief 睡眠区间半开：睡到 09:00 整、午后小睡到 18:00 整，班次开始与结束的那一秒人物都已经醒着，两个在睡标注
    都是 False。"""
    events = _Events()
    events.body(1, _at(0, "00:00"), _at(0, "20:00"))
    events.sleep(1, _at(0, "00:00"), _at(0, "09:00"))
    events.sleep(1, _at(0, "17:00"), _at(0, "18:00"))
    [today] = _reports(events, {1: _agent(_schedule())})["routine_deviation"]["agents"]["1"]["days"]
    assert (today["asleep_at_shift_start"], today["asleep_at_shift_end"]) == (False, False)


def test_workday_reports_are_evaluated_in_the_timezone_given_to_the_behaviour_report() -> None:
    """@brief 工作安排没有自己的时区，沿用场景时区（规格 4.3），也就是行为评估入口的 timezone 参数：三天夹具的同一批
    事件按 Asia/Tokyo 评估，班次、作息窗口与本地日都取东京时间。周一 17:30（上海）离开是东京 18:30：比班次结束晚
    30 分钟、落在下班窗口内，到家比到家窗口早 10 分钟；东京 09:00 人物还在睡，18:00 没有在睡。"""
    events, ids = _three_days()
    inputs = WorkdayInputs(_deviation_inputs(), 900)
    reports = behavior_metrics_report(events.rows, timezone="Asia/Tokyo", workday=inputs)[CODE]["reports"]
    monday = reports["routine_deviation"]["agents"]["1"]["days"][0]
    assert monday == _day_row("2026-01-05", ("2026-01-05T18:30:00+09:00", 30.0, 0.0, ids["monday_leave"]),
                              ("2026-01-05T18:50:00+09:00", -10.0, ids["monday_home"]), (True, False))
