"""归日：左闭右开的本地日窗口、日期加减、零点界与它只在当天开始后才读取；全仓只经 day_window 求本地零点。"""

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.time import readable
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.layers import progress_layer
from worldmock.simulation_engine.observation.memory.days import (
    DayDigest,
    day_bound,
    day_digests,
    day_window,
    recent_dates,
    shift_date,
)
from worldmock.simulation_engine.scheduling.plans import day_plan_inputs

ZONE = "Asia/Shanghai"


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "America/New_York", "UTC"])
def test_day_window_is_left_closed_right_open_at_local_midnight(zone):
    """窗口从当地零点起、到下一个当地零点止：零点那一秒与最后一秒在里面，前一秒与下一个零点不在。"""
    window = day_window("2026-10-10", zone)
    assert window.start == int(datetime(2026, 10, 10, tzinfo=ZoneInfo(zone)).timestamp())
    assert (window.day, window.timezone) == ("2026-10-10", zone)
    assert window.contains(window.start) and window.contains(window.start + 86399)
    assert not window.contains(window.start - 1) and not window.contains(window.end)
    assert window.end == day_window("2026-10-11", zone).start


def test_shift_date_and_recent_dates():
    assert shift_date("2026-10-01", -1) == "2026-09-30"
    assert shift_date("2026-12-31", 1) == "2027-01-01"
    assert recent_dates("2026-10-08") == ("2026-10-05", "2026-10-06", "2026-10-07")


def _store(tmp_path, moments):
    """依次在给定时刻各提交一步推进（每步一条被拒证据），返回足迹库与各步事件的序号。"""
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": moments[0] - 1})
    sequences = []
    for version, moment in enumerate(moments, start=1):
        draft = EventDraft(event_type="action_rejected", occurred_at=moment, agent_id=1,
                           decision_id=f"decision-{version}", action_id=f"action-{version}",
                           data={"error_code": "CODE", "reason": "CODE", "items": []})
        sequences.extend(event.event_seq for event in store.commit(
            store.state, {"version": version, "current_time": moment}, [draft]))
    return store, sequences


def test_day_bound_is_the_last_event_before_local_midnight(tmp_path):
    """事件依次在 10-09 23:59:59、10-10 00:00:00、10-10 00:10:00：10-10 的零点界是 23:59:59 那条的序号，
    10-09 的零点界是 0，(B_10, B_11] 恰是零点那一秒与 00:10 的两条。"""
    midnight = day_window("2026-10-10", ZONE).start
    store, (last_evening, at_midnight, after_midnight) = _store(tmp_path, [midnight - 1, midnight, midnight + 600])
    now = midnight + 600
    try:
        assert day_bound(store, "2026-10-10", ZONE, now) == last_evening
        assert day_bound(store, "2026-10-09", ZONE, now) == 0
        later = day_bound(store, "2026-10-11", ZONE, day_window("2026-10-11", ZONE).start)
        assert list(range(day_bound(store, "2026-10-10", ZONE, now) + 1, later + 1)) == [at_midnight, after_midnight]
    finally:
        store.close()


def test_day_bound_refuses_a_day_that_has_not_started(tmp_path, caplog):
    midnight = day_window("2026-10-10", ZONE).start
    store, _ = _store(tmp_path, [midnight])
    try:
        with pytest.raises(IntegrityError, match="^Day bound is read before the day starts$"):
            day_bound(store, "2026-10-11", ZONE, day_window("2026-10-11", ZONE).start - 1)
    finally:
        store.close()
    assert "Day bound is read before the day starts" in caplog.text


def _digest_event(store, tier: str, period: str, digests: dict, fallback: list[str]) -> int:
    """在足迹库里登记一条回顾事件（与一条作为起因的被拒证据同一步提交），返回回顾事件的序号。"""
    moment = store.state["current_time"] + 60
    cause = EventDraft(event_type="action_rejected", occurred_at=moment, agent_id=1, decision_id=f"d-{period}",
                       action_id=f"a-{period}", data={"error_code": "CODE", "reason": "CODE", "items": []})
    digest = EventDraft(event_type="environment_event", occurred_at=moment, agent_id=1, caused_by=[cause.event_id],
                        visibility="system", source_kind="model",
                        data={"kind": "memory_digest_generated", "tier": tier, "period": period,
                              "digests": digests, "fallback": fallback})
    version = store.state["version"] + 1
    return store.commit(store.state, {"version": version, "current_time": moment}, [cause, digest])[-1].event_seq


def test_day_digests_read_day_events_up_to_the_cutoff(tmp_path):
    """10-07 日回顾（饮食有正文、工作为 null 且在 fallback、生活为 null 不在 fallback）、2026-09 月回顾与 10-08 日回顾：
    截止在 10-08 那条之前时只读到 10-07；月回顾与序号大于截止的日回顾都不读；截止取末序号时两天都在，按日期升序。"""
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": day_window("2026-10-08", ZONE).start})
    paragraph = {"text": "中午吃了牛肉面。", "source_event_ids": ["e-1"], "cited_event_ids": ["e-1"]}
    try:
        _digest_event(store, "day", "2026-10-07", {"food": paragraph, "work": None, "life": None}, ["work"])
        _digest_event(store, "month", "2026-09", {"food": {**paragraph, "text": "九月常吃面。"}, "work": None,
                                                  "life": None}, [])
        last = _digest_event(store, "day", "2026-10-08", {"food": None, "work": None, "life": paragraph}, [])
        assert day_digests(store, 1, last - 1) == {
            "2026-10-07": DayDigest({"饮食": "中午吃了牛肉面。"}, frozenset({"工作"}))}
        both = day_digests(store, 1, last)
        assert list(both) == ["2026-10-07", "2026-10-08"]
        assert both["2026-10-08"] == DayDigest({"生活": "中午吃了牛肉面。"}, frozenset())
    finally:
        store.close()


def test_local_midnight_is_computed_only_by_day_window():
    """当日拒绝经 day_bound 取零点界，零点计划的回顾时段经 day_window 取零点，不再各自把时刻改写到零点。"""
    assert not hasattr(readable, "local_day_start")
    progress_source = inspect.getsource(progress_layer)
    assert "local_day_start" not in progress_source and "day_bound(" in progress_source
    assert "replace(hour=0" not in inspect.getsource(day_plan_inputs)
