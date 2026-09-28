"""待办层：暂停的活动与今日计划（docs/dev/SPEC-persona-prompt.md「待办层」）。

暂停的活动按暂停时刻从老到新排列（先开始、后暂停的排在后面），带暂停时刻、只能回去恢复的地点、客观进度与恢复、结束两个
工具；人在别处时恢复不可用，原因点名地点。今日计划在零点计划生成前与当日结束后为 null，之后按计划顺序列出至多八个目标的
意图、文字、是否已推进与提醒时刻，目标行不显示人物自述。
"""

from __future__ import annotations

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity, submit_round, target_tools, tool_targets,
                                 wake_by_mail)
from tests.life.test_same_moment_decisions import _act, _content, _start
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActivityStatus, PauseActivityRequest, paused_activities
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.readable import iso_time, local_date
from worldmock.life.schedule import INTENT_KINDS, DayPlan, PlanGoal, day_end_seconds


def _pause(engine, round_id: str, activity_id: str) -> int:
    """暂停一项进行中的活动，返回暂停时刻。"""
    paused = _act(engine, round_id, "pause_activity", PauseActivityRequest(
        task=[activity_name(engine, activity_id)], summary=["先停一下。"]))
    assert paused.status == "completed"
    return engine.state.current_time


def test_paused_activities_are_ordered_by_pause_time_not_start_time(engine_factory) -> None:
    """先开始观看、后开始等待；等待先暂停、观看后暂停：待办层按暂停时刻列等待在前，而活动的开始顺序（models.paused_activities）
    仍是观看在前。每项带暂停时刻、恢复地点、客观进度与恢复、结束两个工具；暂停的活动不在工具层。"""
    engine = engine_factory()
    stage_day_plan(engine)
    (watch,) = _start(engine, "order-watch", _content(engine, "order"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    (wait,) = _start(engine, "order-wait", ("自己", "等待"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    wait_paused = _pause(engine, "order-pause-wait", wait)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    watch_paused = _pause(engine, "order-pause-watch", watch)
    assert engine.state.activities[watch].started_at < engine.state.activities[wait].started_at
    assert wait_paused < watch_paused
    assert [item.activity_id for item in paused_activities(engine.state, 1)] == [watch, wait]
    observed = engine.observe(context(engine, "order-seen"))
    todo = observed["todo_layer"]
    validate_layer(todo)
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    names = [activity_name(engine, wait), activity_name(engine, watch)]
    assert [item["name"] for item in todo["paused_activities"]] == names
    zone = engine.scenario.timezone
    assert [item["paused_at"] for item in todo["paused_activities"]] == [iso_time(wait_paused, zone),
                                                                         iso_time(watch_paused, zone)]
    for item in todo["paused_activities"]:
        assert item["place"] == home and item["progress"]
        assert item["tools"] == [{"tool": "report_status", "available": True, "reason": None},
                                 {"tool": "conclude_activity", "available": True, "reason": None}]
    assert not set(names) & set(tool_targets(observed))


def test_resume_elsewhere_is_unavailable_and_names_the_place(engine_factory) -> None:
    """在家暂停等待后去办公室：待办层里恢复（report_status）标不可用，原因点名家；结束照常可用；活动仍暂停并保留暂停时刻。"""
    engine = engine_factory()
    stage_day_plan(engine)
    (waiting,) = _start(engine, "away-wait", ("自己", "等待"))
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    _pause(engine, "away-pause", waiting)
    preview = engine.observe(context(engine, "away-preview"))
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    _, walking = submit_round(engine, {1: ("start_activity", start_activity("步行", office))}, "away-walk")
    assert advance_until_ready(engine)
    submit_round(engine, {1: ("conclude_activity", conclude_request(activity_name(engine, walking[0].activity_id)))},
                 "away-arrive")
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    observed = engine.observe(context(engine, "away-seen"))
    name = activity_name(engine, waiting)
    (item,) = observed["todo_layer"]["paused_activities"]
    assert (item["name"], item["place"]) == (name, home)
    tools = target_tools(observed, name)
    assert tools["report_status"] == {"tool": "report_status", "available": False,
                                      "reason": f"{name}要回到{home}才能接着做，在别处可以直接结束"}
    assert tools["conclude_activity"]["available"] is True
    assert engine.state.activities[waiting].paused_at is not None
    assert engine.state.activities[waiting].status == ActivityStatus.PAUSED


def _stage_plan(engine, plan: DayPlan | None) -> None:
    """把当日计划换成 plan 并持久提交（None 为尚未生成）。"""
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": plan}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_todo_plan_staged"})])
    engine.state = staged


def test_day_plan_is_null_before_the_plan_and_after_the_day_ends(engine_factory) -> None:
    """零点计划生成之前 day_plan 为 null；当日结束之后（day_end 不晚于此刻）也为 null。"""
    engine = engine_factory()
    now = engine.state.current_time
    _stage_plan(engine, None)
    todo = engine.observe(context(engine, "plan-none"))["todo_layer"]
    assert todo == {"layer": "待办层", "paused_activities": [], "day_plan": None}
    validate_layer(todo)
    expired = DayPlan(day_end=now, goals=(PlanGoal(intent_kind="休息", goal="歇一歇", registered_at=now - 60),))
    _stage_plan(engine, expired)
    assert engine.observe(context(engine, "plan-expired"))["todo_layer"]["day_plan"] is None


def test_day_plan_lists_eight_goals_in_plan_order_with_progress_and_reminders(engine_factory) -> None:
    """计划之后 day_plan 为 {date, goals}：八个目标保持计划顺序，progressed 只看推进证据，remind_time 为 HH:MM 或 null，
    目标行不带人物自述（progress_note 照存，不进提示）。"""
    engine = engine_factory()
    now = engine.state.current_time
    zone = engine.scenario.timezone
    day_end = day_end_seconds(now, zone)
    kinds = INTENT_KINDS[:8]
    remind_at = now + 3600
    goals = tuple(PlanGoal(intent_kind=kind, goal=f"目标{index}", registered_at=now - 120,
                           progressed_at=now - 60 if index == 2 else None,
                           progress_activity_id="activity-progressed" if index == 2 else None,
                           progress_note="人物自己写的进展" if index == 2 else "",
                           remind_at=remind_at if index == 5 else None)
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
    assert all(set(goal) == {"intent_kind", "goal", "progressed", "remind_time"} for goal in plan["goals"])
    assert "人物自己写的进展" not in str(todo)
