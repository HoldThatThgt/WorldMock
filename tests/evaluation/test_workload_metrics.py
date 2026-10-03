"""行为评估的工作负荷与作息偏离（docs/dev/SPEC-errands-workday.md 6.2）。

手写事件用例逐值核对各量的定义：班次按观察窗截取；在工作地点、休息时段、任务外出与缺勤四份之和等于班次；两种声明例外
（班次内的休息时段、本人已开放且未结清任务的要求地点的外出）不计缺勤，不当班的日子不进统计；早退、离开工作地点与到家
相对班次和作息窗口的偏离、班次两端是否在睡与证据事件身份；缺输入为 null 并写说明；不出现已删除的偏离指标键名；同一
输入的报告逐字节相同。评估输入经真实的场景文档解析打包。

真实运行用例（engine_factory 加 declare 改写场景副本）只发真实请求、不预置地点：从家步行去办公室、开工、15:00 收工并
步行回家，直到 18:30；从 SQLite 读回事件后经 behavior_metrics_report 重算出三小时的早退与缺勤。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tests.life.conftest import _mutated_scenario, conclude_request, engine_factory, layer_start  # noqa: F401
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
    events = [event.model_dump(mode="json") for event in engine.store.read_events(engine.store.last_event_seq)]
    inputs = workday_inputs(engine.semantics.document, {1: None})
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
    office = f"地点：{next(item.name for item in engine.scenario.locations if item.location_id == 'office')}"
    home = f"地点：{next(item.name for item in engine.scenario.locations if item.location_id == 'home')}"
    _decide_at(engine, _clock("08:00:00"))
    _empty_handed(engine, "stop-waiting")
    assert _decide(engine, "commute", "start_activity",
                   lambda observed: layer_start(observed, office, "步行")).status == "in_progress"
    _drive(engine, "to-office", lambda: engine.state.agents[1].location_id == "office")
    assert engine.state.current_time < _clock("09:00:00")
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
    open_ids = {item.activity_id for item in engine.state.activities.values()
                if item.agent_id == 1 and item.concluded_at is None and item.status != "paused"}
    if not open_ids:
        return

    def request(observed: dict):
        """本轮观察里这些活动的可读名称。"""
        return conclude_request(*[row["name"] for row in observed["activity_bindings"]
                                  if row["activity_id"] in open_ids])

    assert _decide(engine, name, "conclude_activity", request).status == "completed"


def test_reports_are_byte_reproducible() -> None:
    """同一输入两次计算（第二次把事件顺序倒过来）的规范 JSON 逐字节相同。"""
    events, _ = _three_days()
    inputs = WorkdayInputs(_deviation_inputs(), 1800)
    first = workday_reports(events.rows, ZONE, inputs)
    second = workday_reports(list(reversed(events.rows)), ZONE, inputs)
    assert canonical_json(first) == canonical_json(second)
    assert json.loads(canonical_json(first))["routine_deviation"]["agents"]["1"]["days"]
