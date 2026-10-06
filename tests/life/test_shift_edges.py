"""班次与休息时段起止的唤醒：纯函数与生成器用例（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账 的 T16、T17）。

T16 只调用 life.schedule 的纯函数：四个成员排在 errand_reminder_due 之后、不进睡眠允许集合，睡着时逐个成员都被过滤；
清醒而其余标志与命中条件同时成立时输出声明次序（去掉判定从不产出的 activity_infeasible 与 rejection_feedback）；
next_decision_time 只在清醒时取起止时刻（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 ）。

T17 调用 life.duty_edges 的生成器与两个查询（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）：写作 24:00
与 00:00 的起止映射到当地零点；休息日与没有工作安排的人物没有起止；日期例外优先；查询秒恰为某个起止时，严格晚于的
结果不含它、首项是下一个起止，恰在的结果含它；夏令时日的起止与 work_schedule.shift_span、break_spans 的换算一致。
生成器只读场景的 timezone 与人物配置（经 world_ops.agent_config），用例以带这两项的场景替身承载经真实场景模型
WorkSchedule 校验的工作安排：人物 1 声明班次，人物 2 没有工作安排。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from worldmock.life.duty_edges import duty_edges, edge_times_after, edges_at
from worldmock.life.scenario import WorkSchedule
from worldmock.life.schedule import (DUTY_EDGE_REASONS, SLEEP_ALLOWED_REASONS, NextDecisionQuery, WakeReason,
                                     WakeSnapshot, evaluate_wake_reasons, next_decision_time)
from worldmock.life.work_schedule import STATUS_AFTER, break_spans, duty, shift_span

NOW = 1000000
_SHANGHAI = "Asia/Shanghai"
_NEW_YORK = "America/New_York"
#: 标准班次：09:00–18:00，午休 12:00–13:30（自当地零点起的分钟）。
_STANDARD = {"starts": 540, "ends": 1080, "breaks": [[720, 810]]}
_START, _END = WakeReason.SHIFT_START, WakeReason.SHIFT_END
_BREAK_START, _BREAK_END = WakeReason.BREAK_START, WakeReason.BREAK_END


def _week(workday: dict | None, weekend: dict | None = None) -> dict[str, dict | None]:
    """每周七天的班次表：周一至周五 workday，周六、周日 weekend（周一为 "0"）。"""
    return {str(day): (workday if day < 5 else weekend) for day in range(7)}


def _schedule(weekly: dict, exceptions: dict | None = None) -> WorkSchedule:
    """经场景模型校验的工作安排，工作地点为办公室。"""
    return WorkSchedule.model_validate(
        {"workplace_location_id": "office", "weekly": weekly, "exceptions": exceptions or {}})


def _world(timezone: str, schedule: WorkSchedule) -> SimpleNamespace:
    """场景替身：时区与两个人物的配置，人物 1 声明 schedule，人物 2 没有工作安排。"""
    return SimpleNamespace(timezone=timezone, agents=(SimpleNamespace(agent_id=1, work_schedule=schedule),
                                                      SimpleNamespace(agent_id=2, work_schedule=None)))


def _local(text: str, timezone: str = _SHANGHAI) -> int:
    """当地墙钟时刻（ISO 写法，不带偏移）的时间戳；夏令时跳过与重复的时刻按 fold=0 取。"""
    return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(timezone)).timestamp())


# --- T16：唤醒判定与下一次决策时刻的纯函数 ---


def test_duty_edge_members_follow_errand_reminder_due_and_stay_out_of_sleep() -> None:
    """四个成员按 shift_start、shift_end、break_start、break_end 追加在 errand_reminder_due 之后，营业原因退役后闭集为 24 个；
    DUTY_EDGE_REASONS 就是这四个、按声明次序；四个都不在睡眠允许集合里，睡眠允许集合仍恰为四项。"""
    names = [member.value for member in WakeReason]
    assert len(names) == 24
    assert names[names.index("errand_reminder_due") + 1:] == ["shift_start", "shift_end", "break_start", "break_end"]
    assert DUTY_EDGE_REASONS == (_START, _END, _BREAK_START, _BREAK_END)
    assert not set(DUTY_EDGE_REASONS) & SLEEP_ALLOWED_REASONS
    assert len(SLEEP_ALLOWED_REASONS) == 4


@pytest.mark.parametrize("member", DUTY_EDGE_REASONS, ids=lambda member: member.value)
def test_each_duty_edge_member_is_filtered_while_asleep(member: WakeReason) -> None:
    """逐个成员：清醒时本边界恰好成立的起止单独就提供一次决策并带这个名字；睡着时被过滤，边界静默；与睡眠允许的计划结束
    同一秒时只剩计划结束。"""
    awake = evaluate_wake_reasons(WakeSnapshot(now=NOW, duty_edges_now={member}))
    assert awake.decision_offered and awake.wake_reasons == [member]
    asleep = evaluate_wake_reasons(WakeSnapshot(now=NOW, duty_edges_now={member}, is_asleep=True))
    assert not asleep.decision_offered and asleep.wake_reasons == []
    ended = evaluate_wake_reasons(WakeSnapshot(now=NOW, planned_end=NOW, duty_edges_now={member}, is_asleep=True))
    assert ended.wake_reasons == [WakeReason.PLANNED_END]


def test_awake_snapshot_with_every_condition_lists_the_declaration_order() -> None:
    """清醒（is_asleep 为假）而其余标志与命中条件同时成立、四个起止同一秒重合时，输出是 WakeReason 的声明次序去掉判定从不
    产出的 activity_infeasible 与 rejection_feedback：两种提醒之后是 shift_start、shift_end、break_start、break_end。"""
    snapshot = WakeSnapshot(
        now=NOW, planned_end=NOW, felt_bands_now={"appetite": 1}, message_arrived=True, delivery_arrived=True,
        task_newly_visible=True, next_task_due_at=NOW + 1800, work_review_returned=True,
        last_decision_time=NOW - 5400, has_commitments=True, location_closing_at=NOW + 900,
        location_just_opened=True, people_present_now={"甲"}, people_present_before={"乙"},
        discovery_result_ready=True, wake_from_sleep=True, run_start=True, progress_report_due=True,
        no_active_activity=True, plan_reminder_due=True, errand_reminder_due=True,
        duty_edges_now=frozenset(DUTY_EDGE_REASONS), is_asleep=False)
    verdict = evaluate_wake_reasons(snapshot)
    never = {WakeReason.ACTIVITY_INFEASIBLE, WakeReason.REJECTION_FEEDBACK}
    assert verdict.decision_offered
    assert verdict.wake_reasons == [member for member in WakeReason if member not in never]
    assert [member.value for member in verdict.wake_reasons[-5:]] == [
        "errand_reminder_due", "shift_start", "shift_end", "break_start", "break_end"]


def test_next_decision_time_takes_duty_edges_only_while_awake() -> None:
    """严格晚于当前的起止时刻醒睡都照填：清醒时取到最早的那个；睡着时剔除，没有别的候选就没有下一次决策时刻，有计划结束
    时取计划结束；不晚于当前的时刻不取。"""
    query = NextDecisionQuery(now=NOW, duty_edge_times=(NOW + 600, NOW + 7200))
    assert next_decision_time(query) == NOW + 600
    assert next_decision_time(query.model_copy(update={"planned_end": NOW + 20000})) == NOW + 600
    asleep = query.model_copy(update={"is_asleep": True})
    assert next_decision_time(asleep) is None
    assert next_decision_time(asleep.model_copy(update={"planned_end": NOW + 20000})) == NOW + 20000
    assert next_decision_time(NextDecisionQuery(now=NOW, duty_edge_times=(NOW,))) is None


# --- T17：起止生成器 ---


def test_midnight_edges_come_from_the_previous_and_next_local_day() -> None:
    """写作 24:00 与 00:00 的起止都落在当地零点：周五 16:00–24:00 的止点在周六零点恰好成立（扫描前一日），此前一小时它是
    严格晚于的唯一一项；周一 00:00–08:00 的起点在周日 23:00 就是严格晚于的首项（扫描次日），零点那一秒恰在成立。"""
    weekly = {**_week(None), "4": {"starts": 960, "ends": 1440, "breaks": []},
              "0": {"starts": 0, "ends": 480, "breaks": []}}
    world = _world(_SHANGHAI, _schedule(weekly))
    saturday, monday = _local("2026-01-10T00:00"), _local("2026-01-12T00:00")
    assert edges_at(world, 1, saturday) == {_END}
    assert edge_times_after(world, 1, saturday - 3600) == (saturday,)
    assert edge_times_after(world, 1, saturday) == ()
    assert edges_at(world, 1, monday - 3600) == frozenset()
    assert edge_times_after(world, 1, monday - 3600) == (monday, monday + 8 * 3600)
    assert edges_at(world, 1, monday) == {_START}
    verdict = evaluate_wake_reasons(WakeSnapshot(now=saturday, duty_edges_now=edges_at(world, 1, saturday)))
    assert verdict.wake_reasons == [_END]


def test_rest_days_and_missing_schedules_have_no_edges() -> None:
    """不当班的日子本身没有起止：周六中午扫描到的只有周五的四个起止，严格晚于与恰在都为空；周日中午扫描到的只有周一的。
    没有工作安排的人物什么都不生成。"""
    zone = ZoneInfo(_SHANGHAI)
    world = _world(_SHANGHAI, _schedule(_week(_STANDARD)))
    saturday_noon, sunday_noon = _local("2026-01-10T12:00"), _local("2026-01-11T12:00")
    friday = [(_local(f"2026-01-09T{clock}"), member) for clock, member in
              (("09:00", _START), ("12:00", _BREAK_START), ("13:30", _BREAK_END), ("18:00", _END))]
    assert list(duty_edges(world, 1, saturday_noon)) == friday
    assert edge_times_after(world, 1, saturday_noon) == () and edges_at(world, 1, saturday_noon) == frozenset()
    sunday_scan = list(duty_edges(world, 1, sunday_noon))
    assert len(sunday_scan) == 4
    assert {datetime.fromtimestamp(moment, zone).date() for moment, _member in sunday_scan} == {date(2026, 1, 12)}
    friday_end = friday[-1][0]
    assert list(duty_edges(world, 2, friday_end)) == []
    assert edges_at(world, 2, friday_end) == frozenset() and edge_times_after(world, 2, friday_end) == ()


def test_date_exceptions_override_the_weekly_shift() -> None:
    """日期例外优先于每周声明：周三的例外班次 10:00–15:00 取代标准班次（12:00 不再是起止），周四的 null 例外不当班，
    周六的例外班次 08:00–12:00 当班。"""
    exceptions = {"2026-01-07": {"starts": 600, "ends": 900, "breaks": []}, "2026-01-08": None,
                  "2026-01-10": {"starts": 480, "ends": 720, "breaks": []}}
    world = _world(_SHANGHAI, _schedule(_week(_STANDARD), exceptions))
    zone = ZoneInfo(_SHANGHAI)
    thursday_noon = _local("2026-01-08T12:00")
    by_day: dict[date, list] = {}
    for moment, member in duty_edges(world, 1, thursday_noon):
        by_day.setdefault(datetime.fromtimestamp(moment, zone).date(), []).append((moment, member))
    assert by_day == {
        date(2026, 1, 7): [(_local("2026-01-07T10:00"), _START), (_local("2026-01-07T15:00"), _END)],
        date(2026, 1, 9): [(_local(f"2026-01-09T{clock}"), member) for clock, member in
                           (("09:00", _START), ("12:00", _BREAK_START), ("13:30", _BREAK_END), ("18:00", _END))],
    }
    assert edges_at(world, 1, _local("2026-01-07T12:00")) == frozenset()
    assert edges_at(world, 1, _local("2026-01-10T08:00")) == {_START}
    assert edges_at(world, 1, _local("2026-01-10T12:00")) == {_END}


def test_strictly_later_excludes_the_query_second_and_exactly_at_includes_it() -> None:
    """查询秒恰为某个起止时：恰在的结果含它（且只含这一秒的起止），严格晚于的结果不含它、首项是下一个起止；前一秒恰在为空、
    严格晚于的首项就是它。四种起止各在自己的那一秒成立；严格晚于的结果升序去重，覆盖到次日的班次。"""
    world = _world(_SHANGHAI, _schedule(_week(_STANDARD)))
    clocks = ("09:00", "12:00", "13:30", "18:00")
    monday = [_local(f"2026-01-05T{clock}") for clock in clocks]
    tuesday = [_local(f"2026-01-06T{clock}") for clock in clocks]
    nine, noon, half_past_one, six = monday
    assert [edges_at(world, 1, moment) for moment in monday] == [{_START}, {_BREAK_START}, {_BREAK_END}, {_END}]
    assert noon not in edge_times_after(world, 1, noon)
    assert edge_times_after(world, 1, noon)[0] == half_past_one
    assert edges_at(world, 1, noon - 1) == frozenset() and edges_at(world, 1, noon + 1) == frozenset()
    assert edge_times_after(world, 1, noon - 1)[0] == noon
    assert edge_times_after(world, 1, nine) == (noon, half_past_one, six, *tuesday)
    assert edge_times_after(world, 1, six) == tuple(tuesday)


def test_dst_day_edges_match_shift_and_break_spans() -> None:
    """夏令时日按当地墙钟换算，与 work_schedule.shift_span、break_spans 一致：America/New_York 2026-03-08 的例外班次
    60–180、休息 120–150，跳过的 02:00 与 02:30 按跳变前的偏移落到 03:00 与 03:30；03:00 记 shift_end 与 break_start
    （按声明次序并列），03:30 记 break_end，两个时刻的 duty.status 都是已下班。"""
    zone = ZoneInfo(_NEW_YORK)
    schedule = _schedule(_week(None), {"2026-03-08": {"starts": 60, "ends": 180, "breaks": [[120, 150]]}})
    world = _world(_NEW_YORK, schedule)
    day, declared = date(2026, 3, 8), schedule.exceptions["2026-03-08"]
    start, end = shift_span(declared, day, zone)
    ((begin, finish),) = break_spans(declared, day, zone)
    one, three, half_past = (_local(f"2026-03-08T{clock}", _NEW_YORK) for clock in ("01:00", "03:00", "03:30"))
    assert (start, end, begin, finish) == (one, three, three, half_past)
    assert half_past - one == timedelta(hours=1, minutes=30).total_seconds()
    assert list(duty_edges(world, 1, three)) == [(start, _START), (begin, _BREAK_START), (finish, _BREAK_END),
                                                  (end, _END)]
    assert edges_at(world, 1, one) == {_START}
    assert edges_at(world, 1, three) == {_END, _BREAK_START}
    assert edges_at(world, 1, half_past) == {_BREAK_END}
    assert edge_times_after(world, 1, one) == (three, half_past)
    verdict = evaluate_wake_reasons(WakeSnapshot(now=three, duty_edges_now=edges_at(world, 1, three)))
    assert verdict.wake_reasons == [_END, _BREAK_START]
    places = {"office": "办公室"}
    assert [duty(schedule, moment, _NEW_YORK, places)["status"] for moment in (three, half_past)] == [STATUS_AFTER] * 2
