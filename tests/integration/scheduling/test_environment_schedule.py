"""冻结环境按绝对时刻排程，并保持本地日历和夏令时规则。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.life.environment_inputs import copy_environment, write_catalog_rows
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
    assert set(ScheduledKind) == {"delivery_arrival", "calendar_flag", "app_push", "time_trigger", "weather"}
