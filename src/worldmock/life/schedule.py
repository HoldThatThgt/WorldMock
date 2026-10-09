"""@brief 当日目标与事件驱动决策节律的纯函数合同。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

import logging
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worldmock.base_models import FrozenModel
from worldmock.errors import reject_value
from worldmock.footprints.intervals import valid_interval

_LOGGER = logging.getLogger(__name__)


#: 系统默认重估间隔。
RECONSIDERATION_INTERVAL_SECONDS = 5400
#: 有序目标清单与每条目标正文的容量上限。
MAX_PLAN_GOALS = 8
PLAN_GOAL_MAX_CHARS = 300


@dataclass(frozen=True, slots=True)
class WakeTrigger:
    """@brief 一个唤醒条件的名称、回调分类及睡眠许可。"""

    #: 足迹中的唯一条件名称。
    name: str
    #: 回调订阅的完整分类名称。
    category: str
    #: 是否允许在睡眠边界提供决策。
    can_wake_sleep: bool


#: 唤醒条件的唯一声明及输出次序；条件无命中时不提供决策。
WAKE_TRIGGERS = (
    WakeTrigger("planned_end", "time_boundary", True),
    WakeTrigger("felt_band_change", "state_change", False),
    WakeTrigger("delivery_arrival", "event_arrival", False),
    WakeTrigger("reconsideration_interval", "time_boundary", False),
    WakeTrigger("rejection_feedback", "outcome_feedback", False),
    WakeTrigger("session_open", "time_boundary", True),
    WakeTrigger("progress_report_due", "time_boundary", False),
    WakeTrigger("no_active_activity", "time_boundary", False),
    WakeTrigger("reminder_due", "reminder_due", False),
    WakeTrigger("duty_boundary", "duty_boundary", False),
    WakeTrigger("app_push", "reminder_due", False),
)


INTENT_KINDS: tuple[str, ...] = (
    "睡眠",
    "晨间例程",
    "通勤",
    "工作",
    "用餐",
    "休息",
    "娱乐",
    "社交",
    "家务",
    "运动",
    "外出",
)
"""当日目标意图闭集；与下方 IntentKind Literal 保持同步。"""

IntentKind = Literal[
    "睡眠",
    "晨间例程",
    "通勤",
    "工作",
    "用餐",
    "休息",
    "娱乐",
    "社交",
    "家务",
    "运动",
    "外出",
]



def day_end_seconds(now: int, timezone_name: str) -> int:
    """@brief 当日目标失效时刻：场景时区次日零点。"""
    midnight = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int((midnight + timedelta(days=1)).timestamp())


def clock_epoch(clock: str | None, now: int, timezone_name: str) -> int | None:
    """@brief 把场景本地 HH:MM 换算为 now 所在本地日的绝对时刻；None 原样返回。

    提醒时刻 remind_time（None 表示不设提醒，docs/design/04-simulation-engine.md）与大致时刻 around_time
    （None 表示不限时刻，docs/design/04-simulation-engine.md ）共用这一换算。
    """
    if clock is None:
        return None
    hour, minute = (int(part) for part in clock.split(":"))
    local = datetime.fromtimestamp(now, ZoneInfo(timezone_name))
    return int(local.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp())


def epoch_clock(moment: int, timezone_name: str) -> str:
    """@brief 提醒时刻或大致时刻的场景本地 HH:MM（待办层呈现用，clock_epoch 的逆换算）。"""
    return f"{datetime.fromtimestamp(moment, ZoneInfo(timezone_name)):%H:%M}"


class RemindTimeInvalid(ValueError):
    """@brief remind_time 换算后不晚于生成或修订时刻，或不早于次日零点（业务码 PLAN_REMIND_TIME_INVALID）。"""


def registered_goals(goals: Sequence[Any], now: int, timezone_name: str) -> tuple["PlanGoal", ...]:
    """@brief 以 now 登记请求目标（intent_kind、goal、remind_time、around_time），提醒与大致时刻换算为该本地日的绝对时刻。

    大致时刻不像提醒：不要求晚于登记时刻，也不触发决策（docs/design/04-simulation-engine.md ）。
    """
    return tuple(PlanGoal(intent_kind=goal.intent_kind, goal=goal.goal, registered_at=now,
                          remind_at=clock_epoch(goal.remind_time, now, timezone_name),
                          around_at=clock_epoch(goal.around_time, now, timezone_name)) for goal in goals)


class WakeSnapshot(BaseModel):
    """@brief 一个真实边界的完整唤醒输入，旧系统字段直接拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    now: int
    planned_end: int | None = None
    planned_end_is_sleep: bool = False
    felt_bands_now: dict[str, str] = Field(default_factory=dict)
    felt_bands_at_last_decision: dict[str, str] = Field(default_factory=dict)
    delivery_arrived: bool = False
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    session_open: bool = False
    progress_report_due: bool = False
    no_active_activity: bool = False
    reminder_due: bool = False
    duty_boundary: bool = False
    app_push_arrived: bool = False
    is_asleep: bool = False


class WakeVerdict(BaseModel):
    """@brief 静默或提供决策，以及按声明次序记录的条件名称。"""

    decision_offered: bool
    wake_reasons: list[str] = Field(default_factory=list)


class NextDecisionQuery(BaseModel):
    """@brief 调用方汇总下一真实边界候选，睡眠只接受自己的计划结束。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    now: int
    planned_end: int | None = None
    felt_band_crossing_times: tuple[int, ...] = ()
    scheduled_arrival_times: tuple[int, ...] = ()
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    next_progress_report_at: int | None = None
    reminder_times: tuple[int, ...] = ()
    duty_edge_times: tuple[int, ...] = ()
    is_asleep: bool = False


class PlanGoal(FrozenModel):
    """@brief 当日目标只保存人物意图、登记时刻与提醒，不推断是否完成。"""

    intent_kind: IntentKind
    goal: str = Field(min_length=1, max_length=PLAN_GOAL_MAX_CHARS, pattern=r"\S")
    registered_at: int = Field(ge=0)
    remind_at: int | None = Field(default=None, ge=0)
    reminded_at: int | None = Field(default=None, ge=0)
    around_at: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_reminder(self) -> "PlanGoal":
        """@brief 提醒交付只能发生在实际提醒时刻之后。"""
        if self.reminded_at is not None and (self.remind_at is None or self.reminded_at < self.remind_at):
            _LOGGER.error("Goal reminder trigger precedes its reminder")
            raise ValueError("goal reminder trigger requires an earlier reminder")
        return self


class DayPlan(FrozenModel):
    """@brief 有序当日目标清单；空清单也是一次有效登记。"""

    day_end: int = Field(gt=0)
    goals: tuple[PlanGoal, ...] = Field(max_length=MAX_PLAN_GOALS)

    @model_validator(mode="after")
    def _check_plan(self) -> "DayPlan":
        """@brief 核对意图唯一，以及登记、提醒和大致时刻仍属于当日。"""
        kinds = [goal.intent_kind for goal in self.goals]
        if len(kinds) != len(set(kinds)):
            _LOGGER.error("Day plan contains duplicate intents")
            raise ValueError("day plan intents must be unique")
        if any(goal.registered_at >= self.day_end
               or goal.remind_at is not None and goal.remind_at >= self.day_end
               or goal.around_at is not None and goal.around_at >= self.day_end for goal in self.goals):
            _LOGGER.error("Day plan goal time exceeds its local day")
            raise ValueError("goal time exceeds day_end")
        return self


def require_remind_window(goal: PlanGoal, now: int, day_end: int) -> PlanGoal:
    """@brief 新登记或改了提醒的目标：提醒须晚于生成或修订时刻、早于次日零点，否则 RemindTimeInvalid。"""
    if goal.remind_at is not None and not now < goal.remind_at < day_end:
        _LOGGER.error("Goal reminder is outside the rest of its local day")
        raise RemindTimeInvalid("remind_time must be later than the plan time and earlier than the next midnight")
    return goal


def initial_day_plan(goals: Sequence[PlanGoal], day_end: int) -> DayPlan:
    """@brief 登记初次有序目标清单：提醒须晚于登记时刻、早于 day_end，其余容量和证据约束由领域模型校验。"""
    return DayPlan(day_end=day_end, goals=tuple(require_remind_window(goal, goal.registered_at, day_end)
                                                for goal in goals))






def reconsideration_interval(query: WakeSnapshot | NextDecisionQuery) -> int:
    """@brief 所有清醒状态共用配置声明的重估间隔。"""
    return query.reconsideration_interval_seconds


def evaluate_wake_reasons(snapshot: WakeSnapshot) -> WakeVerdict:
    """@brief 从统一条件表按声明次序输出命中，睡眠只准自己的结束边界。"""
    elapsed = (snapshot.last_decision_time is not None and
               snapshot.now - snapshot.last_decision_time >= snapshot.reconsideration_interval_seconds)
    hits = {
        "planned_end": snapshot.planned_end is not None and snapshot.now >= snapshot.planned_end,
        "felt_band_change": snapshot.felt_bands_now != snapshot.felt_bands_at_last_decision,
        "delivery_arrival": snapshot.delivery_arrived,
        "reconsideration_interval": elapsed,
        "rejection_feedback": False,
        "session_open": snapshot.session_open,
        "progress_report_due": snapshot.progress_report_due,
        "no_active_activity": snapshot.no_active_activity,
        "reminder_due": snapshot.reminder_due,
        "duty_boundary": snapshot.duty_boundary,
        "app_push": snapshot.app_push_arrived,
    }
    reasons = [trigger.name for trigger in WAKE_TRIGGERS if hits[trigger.name]
               and (not snapshot.is_asleep or trigger.name == "planned_end" and snapshot.planned_end_is_sleep)]
    return WakeVerdict(decision_offered=bool(reasons), wake_reasons=reasons)


def wake_categories(names: Sequence[str]) -> tuple[str, ...]:
    """@brief 一个边界的条件分类去重，保持统一条件表的声明次序。"""
    selected = set(names)
    known = {trigger.name for trigger in WAKE_TRIGGERS}
    if not selected <= known:
        _LOGGER.error("Wake condition name is unknown")
        raise ValueError("Wake condition name is unknown")
    return tuple(dict.fromkeys(trigger.category for trigger in WAKE_TRIGGERS if trigger.name in selected))


def next_decision_time(query: NextDecisionQuery) -> int | None:
    """@brief 只取严格未来候选；睡眠不接受外部到达、提醒和重估。"""
    candidates = [] if query.planned_end is None else [query.planned_end]
    if not query.is_asleep:
        candidates.extend(query.felt_band_crossing_times)
        candidates.extend(query.scheduled_arrival_times)
        candidates.extend(query.reminder_times)
        candidates.extend(query.duty_edge_times)
        if query.next_progress_report_at is not None:
            candidates.append(query.next_progress_report_at)
        if query.last_decision_time is not None:
            candidates.append(query.last_decision_time + query.reconsideration_interval_seconds)
    future = [moment for moment in candidates if moment > query.now]
    return min(future) if future else None


def revise_day_plan(plan: DayPlan, now: int, new_goals: Sequence[PlanGoal]) -> DayPlan:
    """@brief 替换当前完整清单；相同文字的未改提醒保持已交付状态，改提醒重新计时。"""
    previous = {(goal.intent_kind, goal.goal): goal for goal in plan.goals}
    changed = tuple(_revised_goal(previous.get((goal.intent_kind, goal.goal)), goal, now, plan.day_end)
                    for goal in new_goals)
    return DayPlan(day_end=plan.day_end, goals=changed)


def _revised_goal(old: PlanGoal | None, new: PlanGoal, now: int, day_end: int) -> PlanGoal:
    """@brief 修订中的一条目标：old 为同一意图与文字的原条目（没有时为 None），规则见 revise_day_plan。"""
    if old is not None and old.remind_at == new.remind_at:
        return old if old.around_at == new.around_at else old.model_copy(update={"around_at": new.around_at})
    require_remind_window(new, now, day_end)
    if old is None:
        return new
    return old.model_copy(update={"remind_at": new.remind_at, "reminded_at": None, "around_at": new.around_at})


def day_plan_expired(plan: DayPlan, now: int) -> bool:
    """@brief 判定目标清单是否跨日残留：now 越过计划自带的 day_end 即属前一天。

    引擎在零点边界据此把 day_plan 归零，随后由零点计划为新的一天生成计划
    （docs/design/04-simulation-engine.md）；判定不做时区换算，day_end 即生成日的次日
    本地零点（修订沿用原计划的 day_end，判定对修订结果稳定）。

    @param plan 当日目标
    @param now 查询时刻（UTC epoch 秒）
    @return now >= plan.day_end 时为 True
    """
    return now >= plan.day_end


def expired_plan_agent_ids(state: Any) -> tuple[int, ...]:
    """@brief 枚举 day_plan 已跨日残留的人物编号（引擎跨日边界归零用，docs/design/06-agent.md「当日目标」）。

    @param state 世界状态（只读 agents 与 current_time 两字段；鸭子类型避免
                 与人物状态模型的环依赖）
    @return day_plan 残留的人物编号元组（按人物表插入序）
    """
    return tuple(agent_id for agent_id, agent in state.agents.items()
                 if agent.day_plan is not None
                 and day_plan_expired(agent.day_plan, state.current_time))
