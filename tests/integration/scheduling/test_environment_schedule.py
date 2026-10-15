"""冻结环境按绝对时刻排程，并保持本地日历和夏令时规则。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.life.conftest import (
    advance_until_ready, conclude_request, do_at, report_request, target_actions, travel, use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, reach
from tests.life.prerequisite_inputs import (
    OFFICE, app_content, dingtalk_environment, example_window, moment, silence_other_pushes, weekday_hours,
)
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


def time_environment(tmp_path):
    root = copy_environment(tmp_path / "input")
    write_catalog_rows(root, "callbacks.jsonl", [
        {"rule_id": "daily", "trigger": {"class": "time", "key": "daily"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 1}},
        {"rule_id": "hourly", "trigger": {"class": "time", "key": "hourly"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 1}},
    ])
    return root


def test_schedule_excludes_start_includes_stop_and_orders_stably(engine_factory, tmp_path):
    engine = engine_factory(time_environment(tmp_path))
    rows = compile_schedule(engine.context.inputs)
    start, stop = engine.context.state.current_time, engine.context.state.end_time
    assert all(start < row.due_at <= stop for row in rows)
    assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
    assert [(row.due_at, row.scheduled_id) for row in rows] == sorted(
        (row.due_at, row.scheduled_id) for row in rows)
    hourly = [row for row in rows if row.kind == "time_trigger" and row.data["key"] == "hourly"]
    assert len(hourly) == 24 and hourly[-1].due_at == stop
    daily = [row for row in rows if row.kind == "time_trigger" and row.data["key"] == "daily"]
    assert len(daily) == 1
    assert datetime.fromtimestamp(daily[0].due_at, ZoneInfo("Asia/Shanghai")).hour == 0


@pytest.mark.parametrize("date,stop,expected,hours", [
    ("2026-11-01T00:00:00-04:00", "2026-11-01T03:00:00-05:00", 4, [1, 1, 2, 3]),
    ("2026-03-08T00:00:00-05:00", "2026-03-08T04:00:00-04:00", 3, [1, 3, 4]),
])
def test_hourly_schedule_uses_real_instants_across_daylight_saving_time(
        engine_factory, tmp_path, date, stop, expected, hours):
    def mutate(persona, config):
        config.update(timezone="America/New_York", start_time=datetime.fromisoformat(date),
                      stop_time=datetime.fromisoformat(stop))
    engine = engine_factory(time_environment(tmp_path), mutate=mutate)
    rows = [row for row in compile_schedule(engine.context.inputs)
            if row.kind == "time_trigger" and row.data["key"] == "hourly"]
    assert len(rows) == expected
    assert [datetime.fromtimestamp(row.due_at, ZoneInfo("America/New_York")).hour for row in rows] == hours
    assert len({row.due_at for row in rows}) == expected


def test_calendar_flag_has_no_money_body_or_wake_effect(engine_factory):
    engine = engine_factory()
    state = engine.context.state
    event = ScheduledEvent(scheduled_id="calendar-test", kind="calendar_flag", due_at=state.current_time,
                           sequence=1, agent_id=1, data={"date": "2026-01-05", "flags": ["makeup_workday"]})
    outcome = apply_scheduled_effect(state, event, engine.context)
    assert outcome.state is state and outcome.wake is None
    assert outcome.data == event.data and outcome.receipts == ()


@pytest.mark.parametrize("kind", ["mail_arrival", "message_arrival", "bill_due", "payroll", "alarm"])
def test_retired_background_events_are_rejected(kind):
    with pytest.raises(ValidationError):
        ScheduledEvent(scheduled_id="retired", kind=kind, due_at=0, sequence=1)


def test_current_background_event_kinds_are_exact():
    assert set(ScheduledKind) == {"delivery_arrival", "order_shipped", "calendar_flag", "app_push", "time_trigger",
                                  "weather"}


MORNING = app_content("dingtalk-morning", "晨会纪要",
                      {"location_kinds": ["office"], "opens": weekday_hours(("08:00", "10:00"))}, pushable=True)
NOTICE = app_content("dingtalk-notice", "公告", duration_seconds=300)


def push_environment(tmp_path):
    """只有钉钉的晨会纪要可能被推送：它只在办公室、工作日 08:00–10:00 能开始；公告没有前提但不可推送。"""
    return silence_other_pushes(dingtalk_environment(tmp_path / "input", MORNING, NOTICE))


def pushes(engine):
    """已处理的整点推送事件，按发生先后。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == "app_push"]


def stay(engine):
    """人物当前进行中的会话。"""
    return next(item for item in engine.context.state.sessions.values() if item.status == "active")


def test_hourly_push_never_selects_content_whose_prerequisite_is_unmet(engine_factory, tmp_path):
    """人在家，09:00 的整点推送处理时晨会纪要不满足地点前提：它不是候选，全部 app 都没有候选，写 pushed: false。"""
    engine = engine_factory(push_environment(tmp_path), mutate=example_window())
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    reach(engine, moment(12, 9))
    assert engine.context.state.agents[1].location_id == "home"
    assert [(row.occurred_at, row.data) for row in pushes(engine)] == [
        (moment(12, 9), {"kind": "app_push", "pushed": False, "scheduled_id": "push-1-2026-10-12T09",
                         "session_id": stay(engine).session_id})]
    assert not stay(engine).app_pushes


def test_person_who_left_the_office_is_on_the_road_for_the_push(engine_factory, tmp_path):
    """08:55 从办公室步行回家，09:00 人在路上：既不算在出发地也不算在目的地，只在办公室、此刻在时段内的晨会纪要
    不是候选，全部 app 都没有候选。"""
    engine = engine_factory(push_environment(tmp_path), mutate=example_window(at_office=True))
    stage_moment(engine, moment(12, 8, 55))
    _, departure = act(engine, "leave", travel("家", "步行"))
    assert departure.status == "in_progress", departure.error_code
    assert engine.context.state.activities[departure.activity_id].planned_end_at > moment(12, 9)
    reach(engine, moment(12, 9))
    road = stay(engine)
    assert (road.kind, road.origin_id, road.destination_id) == ("transit", "office", "home")
    # 出发后人物记录的地点仍是出发地，推送看的是停留会话而不是它
    assert engine.context.state.agents[1].location_id == "office"
    assert [(row.occurred_at, row.data) for row in pushes(engine)] == [
        (moment(12, 9), {"kind": "app_push", "pushed": False, "scheduled_id": "push-1-2026-10-12T09",
                         "session_id": road.session_id})]
    assert not road.app_pushes


def test_arrival_exactly_on_the_hour_counts_as_present_for_the_push(engine_factory, tmp_path):
    """恰在 09:00:00 到达办公室：推送取这一秒活动结算之后的停留会话，带地点前提的晨会纪要已经是候选。"""
    root = push_environment(tmp_path)
    probe = engine_factory(root, mutate=example_window())
    _, sample = act(probe, "probe", travel(OFFICE, "步行"))
    trip = probe.context.state.activities[sample.activity_id]
    seconds = trip.planned_end_at - trip.started_at
    assert 600 < seconds < 3600
    engine = engine_factory(root, mutate=example_window())
    stage_moment(engine, moment(12, 9) - seconds, "home")
    _, departure = act(engine, "commute", travel(OFFICE, "步行"))
    assert engine.context.state.activities[departure.activity_id].planned_end_at == moment(12, 9)
    reach(engine, moment(12, 9))
    assert engine.context.state.agents[1].location_id == "office"
    assert stay(engine).kind == "stay" and stay(engine).started_at == moment(12, 9)
    [pushed] = pushes(engine)
    assert (pushed.occurred_at, pushed.data["app_id"], pushed.data["content_id"]) == (
        moment(12, 9), "dingtalk", "dingtalk-morning")
    assert pushed.data["session_id"] == stay(engine).session_id
    assert stay(engine).app_pushes["dingtalk"].status == "pending"


def test_push_stops_being_offered_after_its_window_and_expires_with_the_stay(engine_factory, tmp_path):
    """09:00 在办公室被推送；时段内工具层显示「有推送」，10:00 之后不再显示，打开钉钉不钉住、按没有这条推送来挑，
    推送保持 pending，离开办公室时随这段停留过期。"""
    engine = engine_factory(push_environment(tmp_path), mutate=example_window(at_office=True))
    _, work = act(engine, "work", do_at(OFFICE, "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    name = engine.context.state.activities[work.activity_id].name
    reach(engine, moment(12, 9))
    office = stay(engine)
    [pushed] = pushes(engine)
    assert pushed.data["content_id"] == "dingtalk-morning" and pushed.data["session_id"] == office.session_id
    observed, _ = act(engine, "after-push", report_request(name), "report_status")
    row = target_actions(observed, "钉钉")["interact"]
    assert (row["available"], row["duration"], row["note"]) == (True, "先放的一条约 1 分钟", "有推送：《晨会纪要》")
    reach(engine, moment(12, 10))
    assert advance_until_ready(engine)
    assert moment(12, 10) <= engine.context.state.current_time < moment(12, 11)
    # 10:00 的整点这个 app 在这段会话里已有推送，不再是候选
    assert [row.data.get("pushed", True) for row in pushes(engine)] == [True, False]
    observed, concluded = act(engine, "stop-work", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    row = target_actions(observed, "钉钉")["interact"]
    assert (row["duration"], row["note"]) == ("每条约 5 分钟", None)
    _, opened = act(engine, "open-late", use_app("钉钉"))
    assert opened.status == "in_progress", opened.error_code
    activity = engine.context.state.activities[opened.activity_id]
    assert (activity.data["content_id"], activity.data["pinned_by_push_id"]) == ("dingtalk-notice", None)
    assert stay(engine).app_pushes["dingtalk"].status == "pending"
    finish_activity(engine, opened, "open-late")
    _, departure = act(engine, "leave", travel("家", "步行"))
    assert departure.status == "in_progress", departure.error_code
    ended = engine.context.state.sessions[office.session_id]
    assert ended.status == "ended" and ended.app_pushes["dingtalk"].status == "pending"
    assert stay(engine).kind == "transit" and not stay(engine).app_pushes
