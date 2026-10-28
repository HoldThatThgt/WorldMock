"""就寝反思的触发判定：每个本地日只在当天第一次睡醒、这一秒第一次决策之前为真，归属睡醒那天的前一天。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from worldmock.simulation_engine.evidence.memory import due_reflection
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.state.world.world import WorldState, WORLD_SCHEMA_VERSION

_ZONE = "Asia/Shanghai"
#: 2026 年 10 月 5 日是周一：5、6、7、8、9 日依次是周一到周五。
_MONDAY = 5


def _at(day: int, clock: str) -> int:
    """2026 年 10 月某日某个本地钟点（HH:MM 或 HH:MM:SS）的绝对时刻。"""
    parts = [int(part) for part in clock.split(":")]
    return int(datetime(2026, 10, day, *parts, tzinfo=ZoneInfo(_ZONE)).timestamp())


def _sleep(identity: str, started: int, ended: int, status=ActivityStatus.COMPLETED, concluded: bool = True):
    """一段睡眠：completed 的在 ended 结束，截断的状态为 truncated，进行中的没有结束时刻。"""
    terminal = status != ActivityStatus.ACTIVE
    return ActivityState(
        activity_id=identity, agent_id=1, location_id="home", kind=ActivityKind.SLEEP, status=status,
        started_at=started, ended_at=ended if terminal else None, planned_end_at=ended, last_settled_at=started,
        elapsed_seconds=0, planned_duration_seconds=ended - started, resource_claims={"first"},
        origin_action_id=f"action-{identity}", origin_decision_id="decision", origin_request_event_id="event",
        concluded_at=ended if terminal and concluded and status == ActivityStatus.COMPLETED else None,
        data={"action": "sleep", "declared_duration_seconds": ended - started, "oversleep_seconds": 0})


def _world(now: int, sleeps, last_decision_time: int | None = None, timezone: str = _ZONE) -> WorldState:
    """只含一个人物与给定睡眠的世界，当前时刻为 now。"""
    body = BodyState(energy_kcal=1200.0, sleep_pressure=0.2, sleep_deficit_hours=0.0, sleep_need_hours=7.5,
                     chronotype_midsleep=4.0, relaxation_need=0.2, social_need=0.2, social_need_growth_per_hour=0.06)
    person = AgentState(agent_id=1, name="林悦", location_id="home", balance_account_id="agent:1", body=body,
                        body_settled_at=now, next_decision_at=now, last_decision_time=last_decision_time)
    return WorldState.model_validate(dict(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=now, end_time=now + 30 * 86400, run_id="run",
        timezone=timezone, persona_sha256="a" * 64, config_sha256="b" * 64, agents={1: person},
        accounts={"agent:1": 100, "external:scenario": -100},
        activities=list(sleeps), random_seed=7, random_state="frozen"))


def _woke(day: int, start: str, end: str, identity: str = "night", start_day: int | None = None, **options):
    """在 day 这一天 end 时刻睡醒的一段睡眠；start_day 缺省为入睡钟点晚于睡醒钟点时的前一天。"""
    began_on = start_day if start_day is not None else (day - 1 if start >= end else day)
    return _sleep(identity, _at(began_on, start), _at(day, end), **options)


def test_night_sleep_triggers_once_at_wake_up_and_files_under_the_previous_day():
    """林悦周四 00:10 睡到 08:00：周四 08:00 触发，归属周三；所属睡眠是刚结束的那一段。"""
    thursday = _MONDAY + 3
    night = _woke(thursday, "00:10", "08:00", concluded=False)
    assert due_reflection(_world(_at(thursday, "08:00"), [night]), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "night"}


def test_same_day_nap_does_not_trigger():
    """同一天 13:00 到 14:00 的午睡醒来不是当天第一次睡醒。"""
    thursday = _MONDAY + 3
    night = _woke(thursday, "00:10", "08:00")
    nap = _woke(thursday, "13:00", "14:00", "nap", concluded=False)
    assert due_reflection(_world(_at(thursday, "14:00"), [night, nap]), 1) is None


@pytest.mark.parametrize("start,end,filed", [("23:00", "07:00", "2026-10-07"), ("03:00", "10:00", "2026-10-07")])
def test_filing_date_is_the_day_before_the_wake_up_whenever_the_sleep_began(start, end, filed):
    """23:00 睡到次日 07:00 归入睡那天；03:00 睡到 10:00 归前一天：只看睡醒落在哪个本地日。"""
    thursday = _MONDAY + 3
    night = _woke(thursday, start, end, concluded=False)
    assert due_reflection(_world(_at(thursday, end), [night]), 1) == {"date": filed, "sleep_activity_id": "night"}


def test_split_night_triggers_at_the_first_wake_up_of_the_day_only():
    """周三 22:00 睡到周四 00:30，再从 01:00 睡到 08:00：00:30 触发并归周三，08:00 不触发。"""
    thursday = _MONDAY + 3
    first = _woke(thursday, "22:00", "00:30", "first", concluded=False)
    assert due_reflection(_world(_at(thursday, "00:30"), [first]), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "first"}
    second = _woke(thursday, "01:00", "08:00", "second", concluded=False)
    settled = first.model_copy(update={"concluded_at": first.ended_at})
    assert due_reflection(_world(_at(thursday, "08:00"), [settled, second]), 1) is None


def test_nap_after_an_all_nighter_is_the_first_wake_up_of_its_day():
    """通宵之后 13:00 到 14:00 的小睡是当天第一次睡醒：14:00 触发，归前一天。"""
    thursday = _MONDAY + 3
    wednesday_night = _woke(thursday - 1, "00:10", "08:00")
    nap = _woke(thursday, "13:00", "14:00", "nap", concluded=False)
    assert due_reflection(_world(_at(thursday, "14:00"), [wednesday_night, nap]), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "nap"}


def test_a_day_without_sleep_has_no_reflection_and_the_next_wake_up_files_under_it():
    """周四整天没睡：周四任何时刻都不触发；周五 01:00 睡到 09:00，09:00 触发并记在周四名下。"""
    thursday, friday = _MONDAY + 3, _MONDAY + 4
    wednesday_night = _woke(thursday - 1, "00:10", "08:00")
    for clock in ("00:00", "08:00", "14:00", "23:59"):
        assert due_reflection(_world(_at(thursday, clock), [wednesday_night]), 1) is None
    late = _woke(friday, "01:00", "09:00", "late", concluded=False)
    assert due_reflection(_world(_at(friday, "09:00"), [wednesday_night, late]), 1) == {
        "date": "2026-10-08", "sleep_activity_id": "late"}


def test_second_decision_in_the_wake_up_second_does_not_trigger():
    """睡醒那一秒已经提交过一轮决策（last_decision_time 等于当前时刻）时为空；更早的决策时刻不影响。"""
    thursday = _MONDAY + 3
    night = _woke(thursday, "00:10", "08:00", concluded=False)
    now = _at(thursday, "08:00")
    assert due_reflection(_world(now, [night], last_decision_time=now), 1) is None
    assert due_reflection(_world(now, [night], last_decision_time=now - 1), 1) is not None
    assert due_reflection(_world(now, [night], last_decision_time=None), 1) is not None


def test_run_start_truncated_and_ongoing_sleep_do_not_trigger():
    """运行开始没有任何睡眠；被运行终点截断的睡眠不算睡醒；进行中的睡眠与此前某刻结束的睡眠都不是「此刻睡醒」。"""
    thursday = _MONDAY + 3
    now = _at(thursday, "08:00")
    assert due_reflection(_world(now, []), 1) is None
    truncated = _woke(thursday, "00:10", "08:00", status=ActivityStatus.TRUNCATED)
    assert due_reflection(_world(now, [truncated]), 1) is None
    ongoing = _sleep("ongoing", _at(thursday, "00:10"), _at(thursday, "09:00"), ActivityStatus.ACTIVE)
    ongoing = ongoing.model_copy(update={"last_settled_at": now, "elapsed_seconds": now - ongoing.started_at})
    assert due_reflection(_world(now, [ongoing]), 1) is None
    earlier = _woke(thursday, "00:10", "07:00")
    assert due_reflection(_world(now, [earlier]), 1) is None


def test_sleep_ending_exactly_at_local_midnight_belongs_to_the_new_day():
    """恰在 00:00:00 结束的睡眠属于新的一天：归属日期是刚过去的那一天，同一天稍后的睡醒不再触发；
    差一秒在 23:59:59 结束的属于前一天。"""
    thursday = _MONDAY + 3
    evening = _woke(thursday, "21:00", "00:00", "evening", concluded=False)
    assert due_reflection(_world(_at(thursday, "00:00"), [evening]), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "evening"}
    night = _woke(thursday, "01:00", "08:00", "night", concluded=False)
    settled = evening.model_copy(update={"concluded_at": evening.ended_at})
    assert due_reflection(_world(_at(thursday, "08:00"), [settled, night]), 1) is None
    before = _sleep("before", _at(thursday - 1, "21:00"), _at(thursday - 1, "23:59:59"), concluded=False)
    assert due_reflection(_world(_at(thursday - 1, "23:59:59"), [before]), 1) == {
        "date": "2026-10-06", "sleep_activity_id": "before"}
    next_night = _woke(thursday, "01:00", "08:00", "next", concluded=False)
    settled_before = before.model_copy(update={"concluded_at": before.ended_at})
    assert due_reflection(_world(_at(thursday, "08:00"), [settled_before, next_night]), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "next"}


def test_local_day_follows_the_run_timezone():
    """本地日按运行时区分：同一绝对时刻在上海已是周四早晨，在 UTC 还是周四零点。"""
    thursday = _MONDAY + 3
    night = _woke(thursday, "00:10", "08:00", concluded=False)
    now = _at(thursday, "08:00")
    assert due_reflection(_world(now, [night]), 1)["date"] == "2026-10-07"
    earlier = _sleep("earlier", _at(thursday, "02:00"), _at(thursday, "07:00"))
    moved = night.model_copy(update={"started_at": _at(thursday, "07:30"), "last_settled_at": _at(thursday, "07:30"),
                                     "planned_duration_seconds": 1800})
    # 07:00 与 08:00（上海）在 UTC 是周三 23:00 与周四 00:00：按 UTC 分日时两次睡醒不在同一天。
    assert due_reflection(_world(now, [earlier, moved]), 1) is None
    assert due_reflection(_world(now, [earlier, moved], timezone="UTC"), 1) == {
        "date": "2026-10-07", "sleep_activity_id": "night"}
