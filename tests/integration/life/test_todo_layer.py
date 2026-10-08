"""待办层：暂停的活动与今日计划（docs/design/06-agent.md「待办层」）。

暂停的活动按暂停时刻从老到新排列（先开始、后暂停的排在后面；同一秒两次决策先后暂停的按暂停事件顺序），带暂停时刻、只能
回去恢复的地点、客观进度（进食只写「已吃 X 份，剩 Y 份」）与恢复、结束两个工具；人在别处时恢复不可用，原因点名地点。今日计划在零点计划生成前与当日结束后为 null，之后按计划顺序列出至多八个目标的
意图、文字、是否已推进、提醒时刻与大致时刻（HH:MM），目标行不显示人物自述。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity, start_request, submit_round, target_tools,
                                 tool_targets, wake_by_mail, pause_request, report_request)
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from tests.life.work_runtime import trip_to
from worldmock.life.models import StartActivityRequest
from worldmock.footprints.models import EventDraft
from worldmock.life.vocabulary import WAYS
from worldmock.life.models import ActivityKind, ActivityStatus, SendMessageActionRequest
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.readable import iso_time, local_date, portion_phrase
from worldmock.life.schedule import INTENT_KINDS, DayPlan, PlanGoal, day_end_seconds


def _pause(engine, label, identity):
    """@brief 真实暂停请求与实际时间锚点。"""
    activity = engine.context.state.activities[identity]
    result = perform(engine, label, pause_request(activity.name), "pause_activity")
    assert result.error_code is None, result.data
    return engine.context.state.current_time


def _chance(engine, offset):
    """@brief 真实邮件到达为在进行中人物提供未来决策。"""
    due = wake_by_mail(engine, offset=offset)
    pump(engine, lambda: engine.context.state.current_time >= due and 1 in engine.scheduling_state().ready_agent_ids)


def test_paused_activities_are_ordered_by_pause_time_not_start_time(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    watch = perform(engine, "watch", request())
    waited = perform(engine, "wait", request("自己", None, "等待"))
    assert watch.error_code is waited.error_code is None
    _chance(engine, 60)
    wait_paused = _pause(engine, "pause-wait", waited.activity_id)
    _chance(engine, 60)
    watch_paused = _pause(engine, "pause-watch", watch.activity_id)
    assert wait_paused < watch_paused
    observed = engine.observe(context(engine, "seen"))
    validate_layer(observed["todo_layer"])
    rows = observed["todo_layer"]["paused_activities"]
    assert [row["name"] for row in rows] == [engine.context.state.activities[item].name
        for item in (waited.activity_id, watch.activity_id)]
    assert [row["paused_at"] for row in rows] == [iso_time(item, engine.context.scenario.timezone)
                                                  for item in (wait_paused, watch_paused)]
    for row in rows:
        assert row["progress"] and row["tools"] == [
            {"tool": "report_status", "available": True, "reason": None},
            {"tool": "conclude_activity", "available": True, "reason": None}]
    assert not {row["name"] for row in rows} & set(tool_targets(observed))


def test_same_second_pauses_from_two_decisions_follow_the_pause_event_order(engine_factory):
    engine = engine_factory("multi-person.json")
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    both = StartActivityRequest(items=[request().items[0], request("自己", None, "等待").items[0]], answering=None)
    result = perform(engine, "both", both)
    assert result.error_code is None, result.data
    activities = [item for item in engine.context.state.activities.values() if item.agent_id == 1]
    watch = next(item for item in activities if item.kind == ActivityKind.ACTION)
    waited = next(item for item in activities if item.kind == ActivityKind.WAIT)
    wake_by_mail(engine, 1, offset=60)
    wake_by_mail(engine, 2, offset=60)
    pump(engine, lambda: set(engine.scheduling_state().ready_agent_ids) == {1, 2})
    message = SendMessageActionRequest(recipient_name=f"人物：{engine.context.state.agents[1].name}", text="在吗")
    now = engine.context.state.current_time
    submit_round(engine, {1: ("pause_activity", pause_request(waited.name)), 2: ("send_message", message)}, "first")
    assert engine.scheduling_state().ready_agent_ids == (1,)
    submit_round(engine, {1: ("pause_activity", pause_request(watch.name))}, "second")
    assert {engine.context.state.activities[item.activity_id].paused_at for item in (watch, waited)} == {now}
    rows = engine.observe(context(engine, "tied"))["todo_layer"]["paused_activities"]
    assert [row["name"] for row in rows] == [waited.name, watch.name]


def test_paused_action_preserves_real_content_position_and_objective_progress(engine_factory):
    engine = engine_factory()
    started = perform(engine, "watch", request())
    assert started.error_code is None
    _chance(engine, 60)
    _pause(engine, "pause", started.activity_id)
    activity = engine.context.state.activities[started.activity_id]
    [row] = engine.observe(context(engine, "paused"))["todo_layer"]["paused_activities"]
    assert activity.media_position_seconds > 0
    assert row["name"] == activity.name and row["progress"]
    assert activity.data["content_id"] in engine.context.semantics.catalog.contents
    resumed = perform(engine, "resume", report_request(activity.name), "report_status")
    assert resumed.error_code is None, resumed.data
    current = engine.context.state.activities[activity.activity_id]
    assert current.media_position_seconds == activity.media_position_seconds
    assert current.data["content_id"] == activity.data["content_id"]


def test_resume_elsewhere_is_unavailable_and_names_the_place(engine_factory):
    engine = engine_factory()
    started = perform(engine, "wait", request("自己", None, "等待"))
    assert started.error_code is None
    _chance(engine, 60)
    paused_at = _pause(engine, "pause", started.activity_id)
    trip_to(engine, "office", "away")
    home = next(place.name for place in engine.context.scenario.locations if place.location_id == "home")
    [row] = engine.observe(context(engine, "away"))["todo_layer"]["paused_activities"]
    assert (row["name"], row["place"]) == (engine.context.state.activities[started.activity_id].name, home)
    tools = {item["tool"]: item for item in row["tools"]}
    assert tools["report_status"] == {"tool": "report_status", "available": False,
        "reason": f"Resume {row['name']} at its start location {home}; it can be concluded elsewhere."}
    assert tools["conclude_activity"]["available"] is True
    refused = perform(engine, "resume-away", report_request(row["name"]), "report_status")
    assert refused.error_code == "ACTIVITY_PRECONDITION_UNMET"
    actual = engine.context.state.activities[started.activity_id]
    assert actual.status == ActivityStatus.PAUSED and actual.paused_at == paused_at


def _stage_plan(engine, plan: DayPlan | None) -> None:
    """把当日计划换成 plan 并持久提交（None 为尚未生成）。"""
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": plan}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_todo_plan_staged"})])
    engine.context.state = staged


def test_day_plan_is_null_before_the_plan_and_after_the_day_ends(engine_factory) -> None:
    """零点计划生成之前 day_plan 为 null；当日结束之后（day_end 不晚于此刻）也为 null。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    _stage_plan(engine, None)
    todo = engine.observe(context(engine, "plan-none"))["todo_layer"]
    assert todo == {"layer": "待办层", "paused_activities": [], "errands": [], "day_plan": None}
    validate_layer(todo)
    expired = DayPlan(day_end=now, goals=(PlanGoal(intent_kind="休息", goal="歇一歇", registered_at=now - 60),))
    _stage_plan(engine, expired)
    assert engine.observe(context(engine, "plan-expired"))["todo_layer"]["day_plan"] is None


def test_day_plan_lists_eight_goals_in_plan_order_with_progress_and_reminders(engine_factory) -> None:
    """计划之后 day_plan 为 {date, goals}：八个目标保持计划顺序，progressed 只看推进证据，remind_time 为 HH:MM 或 null，
    around_time 为 HH:MM 或 null，目标行不带人物自述（progress_note 照存，
    不进提示）。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    zone = engine.context.scenario.timezone
    day_end = day_end_seconds(now, zone)
    kinds = INTENT_KINDS[:8]
    remind_at, around_at = now + 3600, now + 12 * 3600 + 1800
    goals = tuple(PlanGoal(intent_kind=kind, goal=f"目标{index}", registered_at=now - 120,
                           progressed_at=now - 60 if index == 2 else None,
                           progress_activity_id="activity-progressed" if index == 2 else None,
                           progress_note="人物自己写的进展" if index == 2 else "",
                           remind_at=remind_at if index == 5 else None,
                           around_at=around_at if index in (4, 5) else None)
                  for index, kind in enumerate(kinds))
    _stage_plan(engine, DayPlan(day_end=day_end, goals=goals))
    todo = engine.observe(context(engine, "plan-full"))["todo_layer"]
    validate_layer(todo)
    plan = todo["day_plan"]
    assert plan["date"] == local_date(now, zone) == local_date(day_end - 1, zone)
    assert [goal["intent_kind"] for goal in plan["goals"]] == list(kinds)
    assert [goal["goal"] for goal in plan["goals"]] == [f"目标{index}" for index in range(8)]
    assert [goal["progressed"] for goal in plan["goals"]] == [index == 2 for index in range(8)]
    reminder = iso_time(remind_at, zone)[11:16]
    assert [goal["remind_time"] for goal in plan["goals"]] == [reminder if index == 5 else None for index in range(8)]
    assert iso_time(around_at, zone)[11:16] == "12:30"
    assert [goal["around_time"] for goal in plan["goals"]] == [
        "12:30" if index in (4, 5) else None for index in range(8)]
    assert all(set(goal) == {"intent_kind", "goal", "progressed", "remind_time", "around_time"}
               for goal in plan["goals"])
    assert "人物自己写的进展" not in str(todo)
    validate_layer({**todo, "day_plan": {**plan, "goals": [plan["goals"][4]]}})
    bare = {**todo, "day_plan": {**plan, "goals": [{**plan["goals"][4], "around_time": "约 12:30"}]}}
    with pytest.raises(ValueError, match="day_plan"):
        validate_layer(bare)
