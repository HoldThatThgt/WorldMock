"""@brief 场景本地零点与当日目标提醒（docs/dev/SPEC-time-and-energy.md 第四章「运行时机」「计划与提醒」）。

引擎只接线，本模块给出：零点与未触发的提醒作为下一边界候选（不依赖人物的 next_decision_at）；
零点边界（不是终点）把该秒的唤醒判定留到零点计划提交之后；提醒到点的判定、只触发一次的记账，
以及提醒触发那一秒的进度提醒句。提醒与推进无关，只在修订替换或删除该目标、或当日结束时失效。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life.models import AgentState, WorldState, active_activities
from worldmock.life.schedule import PlanGoal, day_end_seconds, day_plan_expired, remind_clock


def is_local_midnight(epoch: int, timezone_name: str) -> bool:
    """@brief 该时刻是否恰为场景本地 00:00:00。"""
    local = datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))
    return (local.hour, local.minute, local.second) == (0, 0, 0)


def defers_rhythm(state: WorldState, target: int, timezone_name: str) -> bool:
    """@brief 零点边界且不是运行终点：该秒的唤醒判定与下一决策候选在零点计划提交之后执行。

    运行终点恰为零点时不执行零点计划，节律照旧在 advance 内判定。
    """
    return target != state.end_time and is_local_midnight(target, timezone_name)


def _live_goals(agent: AgentState, now: int) -> tuple[PlanGoal, ...]:
    """@brief 尚未跨日失效的当日目标。"""
    plan = agent.day_plan
    if plan is None or day_plan_expired(plan, now):
        return ()
    return plan.goals


def reminder_times(agent: AgentState, now: int) -> tuple[int, ...]:
    """@brief 当日未触发、严格晚于 now 的提醒时刻（升序去重，NextDecisionQuery 的提醒候选）。"""
    return tuple(sorted({goal.remind_at for goal in _live_goals(agent, now)
                         if goal.remind_at is not None and goal.reminded_at is None and goal.remind_at > now}))


def boundary_candidates(state: WorldState, timezone_name: str) -> list[int]:
    """@brief 下一边界候选：场景本地下一零点，以及每个人物当日未触发的提醒。"""
    now = state.current_time
    moments = [day_end_seconds(now, timezone_name)]
    for agent in state.agents.values():
        moments.extend(reminder_times(agent, now))
    return moments


def _due(goal: PlanGoal, now: int) -> bool:
    """@brief 该目标的提醒已到点且尚未触发。"""
    return goal.remind_at is not None and goal.reminded_at is None and goal.remind_at <= now


def reminder_due(agent: AgentState, now: int) -> bool:
    """@brief 本边界有到点未触发的提醒（同时到期的合并为一项唤醒原因）。"""
    return any(_due(goal, now) for goal in _live_goals(agent, now))


def mark_reminded(agent: AgentState, now: int) -> AgentState:
    """@brief 把本边界到点的提醒记为已触发（reminded_at），此后不再触发。"""
    if not reminder_due(agent, now):
        return agent
    goals = tuple(goal.model_copy(update={"reminded_at": now}) if _due(goal, now) else goal
                  for goal in agent.day_plan.goals)
    return agent.model_copy(update={"day_plan": agent.day_plan.model_copy(update={"goals": goals})}, deep=True)


def alarm_goals(state: WorldState, agent: AgentState) -> tuple[PlanGoal, ...]:
    """@brief 本秒触发了提醒、要在进度层写提醒句的目标。

    触发那一秒的首轮写出；同一秒后续的同刻轮次中保留，直到人物有进行中活动。
    """
    now = state.current_time
    goals = tuple(goal for goal in _live_goals(agent, now) if goal.reminded_at == now)
    if goals and agent.same_moment_rounds and active_activities(state, agent.agent_id):
        return ()
    return goals


def alarm_sentence(goal: PlanGoal, timezone_name: str) -> str:
    """@brief 提醒句「到了你给『<目标>』定的 HH:MM」。"""
    return f"到了你给『{goal.goal}』定的 {remind_clock(goal.remind_at, timezone_name)}"
