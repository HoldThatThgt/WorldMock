"""睡眠经真实请求、引擎与存储：睡过头、按实际时长补觉、睡着时谁也叫不醒、睡醒那一刻的唤醒原因与醒来后的说法。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (
    advance_next, conclude_request, context, do_at, purchase, sleep, stage_day_plan, stage_errands, travel,
)
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, action_result, events, finish_activity, settle_decision
from tests.unit.life.test_planning_and_errand_boundaries import note
from worldmock.errors import ProtocolError
from worldmock.life.schedule import PlanGoal
from worldmock.life.sleep_plan import oversleep_seconds

#: 规格给出的可复算例子：种子 7 时这条动作身份抽到的一位是 1，随后的分钟数是 27。
_LATE_ACTION = "run-a:round:1791356400:42:agent:1:order:0:action:0001"
_LATE_SECONDS = 27 * 60


def _after_a_long_day(persona, config):
    """运行从周一 07:00 开始，人物惯常 08:00 起床（睡眠中点 4.0、每日睡眠需要 8 小时）：运行开始前的假定睡眠在
    前一天 08:00 就结束了，开始时已经醒了 23 小时，往回 24 小时里只睡过 1 小时；带着 5 小时欠的觉。"""
    persona["sleep_need_hours"] = 8.0
    persona["body"]["initial_sleep_debt_hours"] = 5.0
    config["start_time"] = datetime.fromisoformat("2026-01-05T07:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-07T07:00:00+08:00")


def _at_midnight(persona, config):
    """运行从周一 00:00 开始：人物惯常 07:45 起床（睡眠中点 4.0、每日睡眠需要 7.5 小时），运行开始前的假定睡眠是
    周日 00:15 到 07:45，也就是照常的前一晚。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T00:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-07T00:00:00+08:00")


def _one_in_the_morning(persona, config):
    """运行从周一 01:00 开始、持续三天：运行开始前的假定睡眠是周日 00:15 到 07:45，人物照常睡过前一晚后熬到了 01:00；
    带着 6 小时欠的觉。"""
    persona["body"]["initial_sleep_debt_hours"] = 6.0
    config["start_time"] = datetime.fromisoformat("2026-01-05T01:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T01:00:00+08:00")


def _drawn(engine, prefix: str, seconds: int | None = None) -> str:
    """找一条会抽中多睡的动作身份（给了秒数时要求恰好多睡这么久）：此前睡得不足时，用它开始的睡眠一定睡过头。"""
    state = engine.context.state.random_state
    return next(f"{prefix}-{index}" for index in range(4000)
                if (extra := oversleep_seconds(0, state, f"{prefix}-{index}")) and seconds in (None, extra))


def _clock(engine, epoch: int) -> str:
    """绝对时刻在运行时区的本地钟点 HH:MM。"""
    return f"{datetime.fromtimestamp(epoch, ZoneInfo(engine.context.config.timezone)):%H:%M}"


def _drive(engine, place: str) -> None:
    """驾车到给定地点并收尾。"""
    label = f"to-{place}-{engine.context.state.current_time}"
    _, trip = act(engine, label, travel(place, "驾车"))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, label)


def _work_until(engine, label: str, until: int) -> None:
    """在公司做「季度销售数据整理」，逐边界推进并在每次决策机会播报，到给定时刻之后的第一次决策机会就结束它。"""
    _, work = act(engine, label, do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    name = engine.context.state.activities[work.activity_id].name
    while engine.context.state.current_time < until or not engine.scheduling_state().ready_agent_ids:
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"{label}-report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    _, left = act(engine, f"{label}-leave", conclude_request(name), "conclude_activity")
    assert left.status == "completed", left.error_code


def _settlements(engine, since: int, until: int) -> list:
    """给定时段（不含起点、含终点）里的共同身体结算事件。"""
    return [row for row in events(engine, "environment_event")
            if row.data.get("kind") == "body_settlement" and since < row.occurred_at <= until]


def _sleep_through(engine, activity_id: str) -> int:
    """逐边界推进到睡眠的计划结束：到那之前人物始终没有决策机会，也没有待处理的唤醒原因。返回经过的边界数。"""
    planned_end = engine.context.state.activities[activity_id].planned_end_at
    boundaries = 0
    while engine.context.state.current_time < planned_end:
        assert engine.scheduling_state().ready_agent_ids == ()
        assert engine.context.state.agents[1].pending_wake_reasons == []
        advance_next(engine)
        boundaries += 1
    assert engine.context.state.current_time == planned_end
    return boundaries


def _observe_and_conclude(engine, label: str, activity_id: str) -> dict:
    """睡醒那一秒取一次观察并给刚结束的睡眠收尾，返回这次观察。"""
    from agentsociety2.society import DirectToolCall

    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    name = engine.context.state.activities[activity_id].name
    engine.submit_action(DirectToolCall(decision, f"action-{label}", "conclude_activity", conclude_request(name)))
    assert engine.commit_decision_round(label)[0].status == "completed"
    return observed


def _activity_row(observed: dict, name: str) -> dict:
    """工具层里以活动名为目标的那一行。"""
    return next(row for row in observed["tool_layer"]["targets"] if row["kind"] == "activity" and row["name"] == name)


def _settled_lines(observed: dict) -> list[str]:
    """观察的进度层各条记录对应的已结算文字。"""
    return [line for row in observed["progress_meta"] for line in row["lines"]]


def test_short_sleep_after_a_long_day_oversleeps_once_and_recovers_by_the_time_actually_slept(engine_factory):
    """开始睡之前的 24 小时合计不足 4 小时的一觉：受理时就定下唯一的计划结束（写的时长加多睡的时长），写的时长到点的
    那一刻没有事件也没有决策，计划结束时才醒；欠的觉按实际睡的全长补回；醒来后看到「比打算的多睡了 27 分钟」，收尾后的
    已结算文字同样。这段多睡过的睡眠按实际长度计入下一觉的 24 小时合计：写的 220 分钟不足 4 小时，加上多睡的 27 分钟
    才满，所以下一觉换哪条动作身份都按时醒，说法仍是「按计划结束」。"""
    engine = engine_factory(mutate=_after_a_long_day)
    start = engine.context.state.current_time
    assert engine.context.state.agents[1].body.sleep_deficit_hours == 5.0
    _, result = act(engine, "late", sleep(220), action_id=_LATE_ACTION)
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert (activity.data["declared_duration_seconds"], activity.data["oversleep_seconds"]) == (13200, _LATE_SECONDS)
    assert activity.planned_duration_seconds == 13200 + _LATE_SECONDS
    assert activity.planned_end_at == start + 13200 + _LATE_SECONDS
    accepted = action_result(engine, _LATE_ACTION).data["activities"][0]
    assert accepted["planned_end_at"] == activity.planned_end_at
    assert (accepted["data"]["declared_duration_seconds"], accepted["data"]["oversleep_seconds"]) == (13200, 1620)

    assert _sleep_through(engine, result.activity_id) > 10
    assert not [row for row in events(engine) if row.occurred_at == start + 13200]
    assert [row.data["decision_offered"] for row in _settlements(engine, start, activity.planned_end_at - 1)].count(
        True) == 0
    woke = engine.context.state.activities[result.activity_id]
    assert woke.status == "completed" and woke.boundary_type == "planned_end"
    assert woke.ended_at == start + 14820 and woke.elapsed_seconds == 14820
    assert woke.data["oversleep_seconds"] == _LATE_SECONDS and woke.planned_end_at == activity.planned_end_at
    assert engine.context.state.agents[1].body.sleep_deficit_hours == pytest.approx(5.0 - 14820 / 3600)
    assert engine.scheduling_state().ready_agent_ids == (1,)

    observed = _observe_and_conclude(engine, "woke", result.activity_id)
    assert _activity_row(observed, woke.name)["state"] == "已做约 4 小时，比打算的多睡了 27 分钟"
    after, again = act(engine, "again", sleep(60), action_id=_drawn(engine, "again"))
    assert "睡觉（1月5日 07:00 起）：07:00–11:07，比打算的多睡了 27 分钟" in _settled_lines(after)
    assert again.status == "in_progress", again.error_code
    second = engine.context.state.activities[again.activity_id]
    assert second.data["oversleep_seconds"] == 0 and second.planned_duration_seconds == 3600
    _sleep_through(engine, again.activity_id)
    observed = _observe_and_conclude(engine, "woke-again", again.activity_id)
    assert _activity_row(observed, second.name)["state"] == "已做约 1 小时"
    final = engine.observe(context(engine, "final"))
    assert "睡觉（1月5日 11:07 起）：11:07–12:07，按计划结束" in _settled_lines(final)


def test_the_same_short_sleep_wakes_on_time_when_the_draw_misses(engine_factory):
    """同一段此前睡得不足 4 小时的睡眠，换一条抽到 0 的动作身份就按时醒：多睡的时长记为 0，计划结束就是写的时长到点的
    那一刻。"""
    engine = engine_factory(mutate=_after_a_long_day)
    start = engine.context.state.current_time
    missed = next(f"on-time-{index}" for index in range(200)
                  if not oversleep_seconds(0, engine.context.state.random_state, f"on-time-{index}"))
    _, result = act(engine, "on-time", sleep(170), action_id=missed)
    activity = engine.context.state.activities[result.activity_id]
    assert activity.data["oversleep_seconds"] == 0 and activity.planned_end_at == start + 10200
    _sleep_through(engine, result.activity_id)
    assert engine.context.state.activities[result.activity_id].ended_at == start + 10200
    observed = _observe_and_conclude(engine, "woke", result.activity_id)
    assert _activity_row(observed, activity.name)["state"] == "已做约 3 小时"


def test_a_sleep_declared_at_twelve_hours_oversleeps_after_a_day_with_one_hour_of_sleep(engine_factory):
    """写多长都不影响判定：开始睡之前的 24 小时只睡过 1 小时的人物写满 720 分钟，照样多睡。12 小时只限制人物写的数，
    加上多睡的那段，睡眠活动超过 12 小时仍被接受并睡到计划结束，欠的觉按实际睡的全长补回，醒来后看到多睡了多久。"""
    def worn_out(persona, config):
        """醒了 23 小时，并带着 14 小时欠的觉。"""
        _after_a_long_day(persona, config)
        persona["body"]["initial_sleep_debt_hours"] = 14.0

    engine = engine_factory(mutate=worn_out)
    start = engine.context.state.current_time
    _, result = act(engine, "long", sleep(720), action_id=_LATE_ACTION)
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.data["declared_duration_seconds"] == 43200 and activity.data["oversleep_seconds"] == 1620
    assert activity.planned_duration_seconds == 44820 > 12 * 3600 and activity.planned_end_at == start + 44820
    _sleep_through(engine, result.activity_id)
    woke = engine.context.state.activities[result.activity_id]
    assert woke.status == "completed" and woke.elapsed_seconds == 44820 and woke.ended_at == start + 44820
    assert engine.context.state.agents[1].body.sleep_deficit_hours == pytest.approx(14.0 - 44820 / 3600)
    observed = _observe_and_conclude(engine, "woke", result.activity_id)
    assert _activity_row(observed, woke.name)["state"] == "已做约 12.5 小时，比打算的多睡了 27 分钟"


def test_a_normal_night_is_judged_from_the_moment_it_starts_and_wakes_on_time(engine_factory):
    """照常的一夜：零点开始睡，往回 24 小时里有前一晚的 7.5 小时，写满 470 分钟也在 07:50 按时醒，用一条会抽中多睡的
    动作身份也一样。窗口要是算到写的时长到点的那一刻，前一晚的睡眠就全在窗口之外了。"""
    engine = engine_factory(mutate=_at_midnight)
    start = engine.context.state.current_time
    _, result = act(engine, "night", sleep(470), action_id=_drawn(engine, "night"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.data["oversleep_seconds"] == 0 and activity.planned_duration_seconds == 28200
    assert _clock(engine, start) == "00:00" and _clock(engine, activity.planned_end_at) == "07:50"
    _sleep_through(engine, result.activity_id)
    woke = engine.context.state.activities[result.activity_id]
    assert woke.status == "completed" and woke.ended_at == start + 28200
    observed = _observe_and_conclude(engine, "woke", result.activity_id)
    assert _activity_row(observed, woke.name)["state"] == "已做约 8 小时"


def test_a_short_night_wakes_on_time_and_the_full_night_after_it_oversleeps(engine_factory):
    """照常睡过一夜后熬到 01:00 只睡 180 分钟：往回 24 小时里还有前一晚落在窗口内的 6 小时 45 分，04:00 按时醒，用一条
    会抽中多睡的动作身份也一样。醒着过完白天，当晚写满 470 分钟：这时往回 24 小时里只有那 3 小时，抽中就多睡。
    计划结束是开始时刻加写的时长再加多睡的 40 分钟，到那一刻才醒；欠的觉按实际睡的全长补回；醒来后工具层和收尾后的
    已结算文字都写「比打算的多睡了 40 分钟」。"""
    engine = engine_factory(mutate=_one_in_the_morning)
    start = engine.context.state.current_time
    _, short = act(engine, "short-night", sleep(180), action_id=_drawn(engine, "short-night"))
    assert short.status == "in_progress", short.error_code
    first = engine.context.state.activities[short.activity_id]
    assert first.data["oversleep_seconds"] == 0 and first.planned_end_at == start + 10800
    _sleep_through(engine, short.activity_id)
    assert _clock(engine, engine.context.state.current_time) == "04:00"
    _observe_and_conclude(engine, "woke-early", short.activity_id)

    _drive(engine, "公司")
    _, morning = act(engine, "morning", do_at("公司", "季度销售数据整理"))
    assert morning.status == "in_progress", morning.error_code
    finish_activity(engine, morning, "morning")
    _work_until(engine, "afternoon", start + 20 * 3600)
    _drive(engine, "家")
    night = engine.context.state.current_time
    assert start + 20 * 3600 <= night < start + 23 * 3600
    sleeps = [item for item in engine.context.state.activities.values() if item.kind == "sleep"]
    assert [(item.started_at, item.ended_at) for item in sleeps] == [(start, start + 10800)]
    before = engine.context.state.agents[1].body.sleep_deficit_hours
    _, late = act(engine, "full-night", sleep(470), action_id=_drawn(engine, "full-night", 40 * 60))
    assert late.status == "in_progress", late.error_code
    second = engine.context.state.activities[late.activity_id]
    assert (second.data["declared_duration_seconds"], second.data["oversleep_seconds"]) == (28200, 2400)
    assert second.planned_duration_seconds == 30600 and second.planned_end_at == night + 30600

    _sleep_through(engine, late.activity_id)
    assert not [row for row in events(engine) if row.occurred_at == night + 28200]
    woke = engine.context.state.activities[late.activity_id]
    assert woke.status == "completed" and woke.boundary_type == "planned_end"
    assert woke.ended_at == night + 30600 and woke.elapsed_seconds == 30600
    assert before > 9.0
    assert engine.context.state.agents[1].body.sleep_deficit_hours == pytest.approx(before - 30600 / 3600)
    observed = _observe_and_conclude(engine, "woke-late", late.activity_id)
    assert _activity_row(observed, woke.name)["state"] == "已做约 8.5 小时，比打算的多睡了 40 分钟"
    final = engine.observe(context(engine, "final"))
    span = f"1月5日 {_clock(engine, night)}–{_clock(engine, night + 30600)}"
    assert f"{woke.name}：{span}，比打算的多睡了 40 分钟" in _settled_lines(final)


def test_nothing_but_its_own_planned_end_wakes_a_sleeper_and_reminders_are_delivered_after_waking(engine_factory):
    """睡着时到点的日计划提醒与个人事务提醒、整点推送、外卖送达、班次起止和进度播报都不给决策机会，也开不出决策轮；
    提醒不在睡着时交付，醒来的那个边界当天补交付；睡醒那一刻的原因里有 planned_end 和 session_open。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    zone = ZoneInfo(engine.context.config.timezone)
    ten = int(datetime.fromtimestamp(start, zone).replace(hour=10, minute=0).timestamp())
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="午后歇一歇", registered_at=start,
                                           remind_at=ten),), submitted=True)
    stage_errands(engine, 1, [note(engine.context.state).model_copy(update={"remind_at": ten + 1800})])
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    finish_activity(engine, bought, "takeaway")
    (order,) = engine.context.state.deliveries.values()
    _, result = act(engine, "sleep", sleep(300))
    assert result.status == "in_progress", result.error_code
    slept_at = engine.context.state.current_time
    activity = engine.context.state.activities[result.activity_id]
    assert activity.data["oversleep_seconds"] == 0 and slept_at < order.expected_at < activity.planned_end_at
    advance_next(engine)
    with pytest.raises(ProtocolError, match="must match the scheduled agent order"):
        engine.begin_decision_round([context(engine, "asleep")])
    _sleep_through(engine, result.activity_id)

    during = [row for row in events(engine, "environment_event")
              if slept_at < row.occurred_at < activity.planned_end_at]
    kinds = {row.data.get("kind") for row in during}
    assert {"delivery_arrival", "app_push", "body_settlement"} <= kinds and "errand_reminded" not in kinds
    assert all(row.data["decision_offered"] is False and row.data["wake_reasons"] == []
               for row in during if row.data.get("kind") == "body_settlement")
    local = [datetime.fromtimestamp(row.occurred_at, zone) for row in during if row.data.get("kind") == "app_push"]
    assert {moment.hour for moment in local} >= {9, 10, 11, 12} and all(moment.minute == 0 for moment in local)
    assert not [row for row in events(engine, "decision_started") if row.occurred_at > slept_at]

    woke_at = activity.planned_end_at
    (wake,) = _settlements(engine, woke_at - 1, woke_at)
    reasons = wake.data["wake_reasons"]
    assert wake.data["decision_offered"] is True
    assert [name for name in reasons if name not in {"felt_band_change", "duty_boundary"}] == [
        "planned_end", "reconsideration_interval", "session_open", "no_active_activity", "reminder_due"]
    person = engine.context.state.agents[1]
    assert person.pending_wake_reasons == reasons
    assert person.day_plan.goals[0].reminded_at == woke_at
    (errand,) = person.errands.values()
    assert errand.reminded_at == woke_at
    reminded = [row for row in events(engine, "environment_event") if row.data.get("kind") == "errand_reminded"]
    assert [row.occurred_at for row in reminded] == [woke_at]
    observed = engine.observe(context(engine, "woke"))
    assert observed["wake_reasons"] == reasons


def test_wake_up_records_session_open_and_runs_a_category_callback_once(engine_factory, tmp_path):
    """睡醒边界的唤醒原因含 planned_end 与 session_open，两者同属 time_boundary：按类别订阅的回调在这个边界只执行一次。
    运行开始仍只有 session_open 且不执行类别回调；睡着期间的边界一次也不触发。"""
    root = copy_environment(tmp_path / "input")
    write_catalog_rows(root, "callbacks.jsonl", [
        {"state": "wakes", "initial": 50, "cap": 50, "reset_on_goods": ["tissue"]},
        {"rule_id": "count-time-boundary", "trigger": {"class": "event", "key": "time_boundary"},
         "effect": {"type": "state_delta", "state": "wakes", "delta": -1}}])
    engine = engine_factory(root)
    person = engine.context.state.agents[1]
    assert person.pending_wake_reasons == ["session_open"] and person.callback_states == {"wakes": 50}
    assert not [row for row in events(engine, "environment_event") if row.data.get("kind") == "callback_effect"]
    _, result = act(engine, "nap", sleep(50))
    assert result.status == "in_progress", result.error_code
    before = engine.context.state.agents[1].callback_states["wakes"]
    planned_end = engine.context.state.activities[result.activity_id].planned_end_at
    _sleep_through(engine, result.activity_id)
    (wake,) = _settlements(engine, planned_end - 1, planned_end)
    assert [name for name in wake.data["wake_reasons"] if name != "felt_band_change"] == [
        "planned_end", "session_open", "no_active_activity"]
    assert engine.context.state.agents[1].callback_states["wakes"] == before - 1
    effects = [row for row in events(engine, "environment_event")
               if row.data.get("kind") == "callback_effect" and row.occurred_at == planned_end]
    assert len(effects) == 1 and effects[0].caused_by == [wake.event_id]
    observed = _observe_and_conclude(engine, "woke", result.activity_id)
    assert observed["wake_reasons"] == wake.data["wake_reasons"]
    (started,) = [row for row in events(engine, "decision_started") if row.occurred_at == planned_end]
    assert started.data["wake_reasons"] == wake.data["wake_reasons"]


def test_office_nap_resets_the_discomfort_timer_freezes_both_needs_and_must_be_concluded_first(engine_factory):
    """午睡是真正的睡眠，既有的三条行为照旧：睡着的区间把「饿或困连续超过两小时」的计时清零；睡着时放松需要和
    社交需要不增长；醒来后先用 conclude_activity 给这段睡眠收尾，才能开始下一件事。"""
    engine = engine_factory(mutate=_after_a_long_day)
    _, trip = act(engine, "to-office", travel("公司", "驾车"))
    finish_activity(engine, trip, "to-office")
    awake = engine.context.state.agents[1]
    assert awake.discomfort_started_at is not None
    _, nap = act(engine, "nap", sleep(170), action_id=_LATE_ACTION)
    assert nap.status == "in_progress", nap.error_code
    activity = engine.context.state.activities[nap.activity_id]
    assert activity.location_id == "office" and activity.data["oversleep_seconds"] == _LATE_SECONDS
    needs = (awake.body.relaxation_need, awake.body.social_need)
    while engine.context.state.current_time < activity.planned_end_at:
        advance_next(engine)
        person = engine.context.state.agents[1]
        assert (person.discomfort_started_at, person.discomfort_pulse_hours) == (None, 0.0)
        assert (person.body.relaxation_need, person.body.social_need) == needs
    for label, request in (("work-first", do_at("公司", "季度销售数据整理")), ("sleep-again", sleep(30))):
        _, refused = act(engine, label, request)
        assert refused.status == "rejected" and refused.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    name = engine.context.state.activities[nap.activity_id].name
    _, concluded = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    _, work = act(engine, "work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code


def test_sleep_ending_exactly_at_local_midnight_wakes_with_session_open_after_the_day_plan(engine_factory):
    """恰在本地零点结束的睡眠：零点边界先生成当日计划、再判定唤醒，睡醒的原因里照样有 planned_end 与 session_open；
    这一刻属于新的一天，就寝反思的归属日期是刚过去的那一天。"""
    def evening(persona, config):
        config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
        config["stop_time"] = datetime.fromisoformat("2026-01-07T20:00:00+08:00")

    engine = engine_factory(mutate=evening)
    stage_day_plan(engine, submitted=True)
    _, result = act(engine, "evening", sleep(240))
    assert result.status == "in_progress", result.error_code
    midnight = int(datetime.fromisoformat("2026-01-06T00:00:00+08:00").timestamp())
    assert engine.context.state.activities[result.activity_id].planned_end_at == midnight
    _sleep_through(engine, result.activity_id)
    person = engine.context.state.agents[1]
    assert [name for name in person.pending_wake_reasons if name != "felt_band_change"] == [
        "planned_end", "reconsideration_interval", "session_open", "no_active_activity"]
    assert person.day_plan is not None and person.day_plan.day_end == midnight + 86400
    woke = context(engine, "midnight")
    engine.begin_decision_round([woke])
    assert engine.due_reflection(woke) == {"date": "2026-01-05", "sleep_activity_id": result.activity_id}
