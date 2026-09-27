"""@brief 待办层：暂停的活动与今日计划（docs/dev/SPEC-persona-prompt.md「待办层」）。

暂停的活动按暂停时刻从老到新（同刻按活动身份，即同一次暂停写活动事件的顺序），带暂停时刻、只能回去恢复的地点、客观进度
与两个控制工具（report_status 表示恢复，conclude_activity 表示结束）的可用性；人在别处时恢复不可用，原因点名地点。
今日计划在零点计划生成之前或当日结束之后为 null，之后按计划顺序列出至多八个目标的意图、文字、是否已推进与提醒时刻；
目标行不显示人物自述（PlanGoal.progress_note 照存，不进提示）。
"""

from __future__ import annotations

from worldmock.life import activity_control
from worldmock.life.models import ActivityState, ActivityStatus
from worldmock.life.readable import iso_time, local_date
from worldmock.life.schedule import remind_clock
from worldmock.life.tool_layer import Candidate, LayerContext, availability


def todo_layer(context: LayerContext) -> dict:
    """@brief 待办层 JSON：暂停的活动与今日计划。"""
    paused = sorted((item for item in context.state.activities.values()
                     if item.agent_id == context.agent_id and item.status == ActivityStatus.PAUSED),
                    key=lambda item: (item.paused_at, item.activity_id))
    return {"layer": "待办层", "paused_activities": [_paused_item(context, item) for item in paused],
            "day_plan": _day_plan(context)}


def _paused_item(context: LayerContext, activity: ActivityState) -> dict:
    """@brief 一项暂停的活动：名称、暂停时刻、恢复地点、客观进度与恢复、结束两个工具的可用性。"""
    place = next(item.name for item in context.scenario.locations if item.location_id == activity.location_id)
    tools = [{"tool": tool, **availability(context, Candidate(tool, activity_id=activity.activity_id))}
             for tool in ("report_status", "conclude_activity")]
    return {"name": context.names["activities"][activity.activity_id],
            "paused_at": iso_time(activity.paused_at, context.scenario.timezone), "place": place,
            "progress": activity_control.objective_progress(activity), "tools": tools}


def _day_plan(context: LayerContext) -> dict | None:
    """@brief 今日计划：计划日期与各目标的意图、文字、是否已推进、提醒时刻（HH:MM 或 null）；没有或已过期时为 null。"""
    plan = context.state.agents[context.agent_id].day_plan
    if plan is None or plan.day_end <= context.state.current_time:
        return None
    zone = context.scenario.timezone
    goals = [{"intent_kind": goal.intent_kind, "goal": goal.goal, "progressed": goal.progressed_at is not None,
              "remind_time": remind_clock(goal.remind_at, zone) if goal.remind_at is not None else None}
             for goal in plan.goals]
    return {"date": local_date(plan.day_end - 1, zone), "goals": goals}
