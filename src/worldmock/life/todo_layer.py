"""@brief 待办层保存暂停的活动、未办完的个人事务及当前完整目标清单。"""

from __future__ import annotations

from worldmock.footprints.store import FootprintStore
from worldmock.life import activity_control, errands
from worldmock.life.models import ActivityState, ActivityStatus, WorldState
from worldmock.life.readable import iso_time, local_date
from worldmock.life.schedule import PlanGoal, epoch_clock
from worldmock.life.tool_layer import Candidate, LayerContext, availability


def todo_layer(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 待办层 JSON：暂停的活动、还没办完的个人事务与今日计划。"""
    paused = [item for item in context.state.activities.values()
              if item.agent_id == context.agent_id and item.status == ActivityStatus.PAUSED]
    order = _pause_order(store, context.agent_id, [item.paused_at for item in paused])
    paused.sort(key=lambda item: (item.paused_at, order.get(item.activity_id, 0)))
    agent, names = context.state.agents[context.agent_id], _product_names(context)
    return {"layer": "待办层", "paused_activities": [_paused_item(context, item) for item in paused],
            "errands": errands.todo_rows(agent, names, context.config.timezone),
            "day_plan": _day_plan(context)}


def _product_names(context: LayerContext) -> dict[str, str]:
    """@brief 冻结目录里商品的商品身份 → 商品名（事务行的「要办成的事」按它写商品名）。"""
    return {product_id: item.name for product_id, item in context.catalog.goods.items()}


def _pause_order(store: FootprintStore, agent_id: int, moments: list[int]) -> dict[str, int]:
    """@brief 同刻暂停的活动的事件顺序：活动 → 其最近一次 activity_paused 事件的序号；没有同刻暂停时不读事件。"""
    tied = [moment for moment in moments if moments.count(moment) > 1]
    if not tied:
        return {}
    after = store.last_event_seq_before(min(tied))
    return {event.data["activity_id"]: event.event_seq
            for event in store.read_agent_events("environment_event", agent_id, after=after)
            if event.data.get("kind") == "activity_paused"}


def _paused_item(context: LayerContext, activity: ActivityState) -> dict:
    """@brief 一项暂停的活动：名称、暂停时刻、恢复地点、客观进度与恢复、结束两个工具的可用性。"""
    place = context.catalog.locations[activity.location_id].display_name
    tools = [{"tool": tool, **availability(context, Candidate(tool, activity_id=activity.activity_id))}
             for tool in ("report_status", "conclude_activity")]
    return {"name": context.names["activities"][activity.activity_id],
            "paused_at": iso_time(activity.paused_at, context.config.timezone), "place": place,
            "progress": activity_control.objective_progress(activity), "tools": tools}


def visible_goals(state: WorldState, agent_id: int) -> tuple[PlanGoal, ...] | None:
    """@brief 待办层列出的今日目标（按计划顺序）；零点计划生成之前或当日结束之后没有今日计划，为 None。"""
    plan = state.agents[agent_id].day_plan
    if plan is None or plan.day_end <= state.current_time:
        return None
    return plan.goals


def _day_plan(context: LayerContext) -> dict | None:
    """@brief 今日计划：计划日期与各目标的意图、文字、提醒时刻（HH:MM 或 null）与大致时刻（HH:MM 或
    null，docs/design/04-simulation-engine.md）；没有或已过期时为 null。"""
    goals = visible_goals(context.state, context.agent_id)
    if goals is None:
        return None
    plan, zone = context.state.agents[context.agent_id].day_plan, context.config.timezone
    rows = [{"intent_kind": goal.intent_kind, "goal": goal.goal,
             "remind_time": epoch_clock(goal.remind_at, zone) if goal.remind_at is not None else None,
             "around_time": epoch_clock(goal.around_at, zone) if goal.around_at is not None else None}
            for goal in goals]
    return {"date": local_date(plan.day_end - 1, zone), "goals": rows}
