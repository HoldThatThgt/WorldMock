"""@brief 场景本地零点与当日目标提醒（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「运行时机」「计划与提醒」）。

引擎只接线，本模块给出：零点（本地日期变更的那一秒）与未触发的提醒作为下一边界候选（不依赖人物的 next_decision_at）；
零点边界（不是终点）把该秒的唤醒判定留到零点计划提交之后；提醒到点的判定与只触发一次的记账。提醒触发后由进度层
按触发时刻推导 plan_reminder 记录（只写目标名，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」）。提醒与推进无关，只在修订
替换或删除该目标、或当日结束时失效。个人事务的提醒时刻（只计清醒人物，errands.boundary_times）另行并入下一边界候选，
它们的到期判定与交付在 errands 与节律判定段，不经本模块的当日目标提醒判定（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life import errands
from worldmock.life.models import AgentState, WorldState
from worldmock.life.schedule import PlanGoal, day_end_seconds, day_plan_expired


def is_local_midnight(epoch: int, timezone_name: str) -> bool:
    """@brief 该时刻是否为场景本地零点：本地日期恰在这一秒变更（与前一秒的本地日期不同）。

    按日期变更而不按钟面 00:00:00 判定：夏令时在零点拨快的日子（如 Asia/Beirut 2026-03-29）没有 00:00，
    新的一日从 01:00 开始，day_end_seconds 给出的正是这一刻；零点回拨后重复出现的 00:00 不是新的一日。
    """
    zone = ZoneInfo(timezone_name)
    return datetime.fromtimestamp(epoch, zone).date() != datetime.fromtimestamp(epoch - 1, zone).date()


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
    """@brief 下一边界候选：场景本地下一零点、每个人物当日未触发的提醒，以及每个清醒人物未提醒的个人事务提醒时刻
    （docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ：睡眠中的人物不计入，事务提醒不形成静默边界）。"""
    now = state.current_time
    moments = [day_end_seconds(now, timezone_name)]
    for agent in state.agents.values():
        moments.extend(reminder_times(agent, now))
    moments.extend(errands.boundary_times(state))
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
