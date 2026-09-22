"""@brief 当日目标与事件驱动决策节律的纯函数合同。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo

import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator


_LOGGER = logging.getLogger(__name__)

#: 有在手承诺时的常规重估间隔。
RECONSIDERATION_INTERVAL_SECONDS = 5400
#: 没有在手承诺时的较长重估间隔。
IDLE_RECONSIDERATION_INTERVAL_SECONDS = 10800
#: 当前地点的闭店预警窗口。
CLOSING_WARNING_SECONDS = 900
#: 可见工作任务的截止预警窗口。
TASK_DUE_SOON_SECONDS = 1800
#: 有序目标清单的容量上限。
MAX_PLAN_GOALS = 8
#: 单条自然语言目标的字符上限。
PLAN_GOAL_MAX_CHARS = 300


class WakeReason(str, Enum):
    """@brief 唤醒原因冻结枚举（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」，成员集不可增删）。"""

    PLANNED_END = "planned_end"
    FELT_BAND_CHANGE = "felt_band_change"
    MESSAGE_ARRIVAL = "message_arrival"
    DELIVERY_ARRIVAL = "delivery_arrival"
    TASK_AVAILABLE = "task_available"
    TASK_DUE_SOON = "task_due_soon"
    WORK_REVIEW_RESULT = "work_review_result"
    COMMITMENT_THREATENED = "commitment_threatened"
    RECONSIDERATION_INTERVAL = "reconsideration_interval"
    LOCATION_HOURS_CHANGE = "location_hours_change"
    PERSON_ARRIVED = "person_arrived"
    PERSON_LEFT = "person_left"
    ACTIVITY_INFEASIBLE = "activity_infeasible"
    REJECTION_FEEDBACK = "rejection_feedback"
    DISCOVERY_RESULT = "discovery_result"
    WAKE_FROM_SLEEP = "wake_from_sleep"
    RUN_START = "run_start"
    PROGRESS_REPORT_DUE = "progress_report_due"


SLEEP_ALLOWED_REASONS = frozenset(
    {WakeReason.PLANNED_END, WakeReason.WAKE_FROM_SLEEP, WakeReason.RUN_START}
)
"""睡眠期间仍可提供决策的唤醒原因：仅计划结束与唤醒事件（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""


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


INTENT_ACTIVITY_FAMILIES: dict[str, frozenset[str]] = {
    "睡眠": frozenset({"sleep"}),
    "晨间例程": frozenset({"lifestyle:wellness", "wait"}),
    "通勤": frozenset({"travel"}),
    "工作": frozenset({"work"}),
    "用餐": frozenset({"consume"}),
    "休息": frozenset({"wait", "lifestyle:wellness"}),
    "娱乐": frozenset({"watch", "play_game", "browse", "lifestyle:hobby"}),
    "社交": frozenset({"chat", "call", "lifestyle:social"}),
    "家务": frozenset({"lifestyle:household", "lifestyle:pet"}),
    "运动": frozenset({"lifestyle:sport"}),
    "外出": frozenset({"travel", "lifestyle:outdoor"}),
}
"""意图到实际活动家族的映射；共享家族由目标清单顺序裁决。"""


def day_end_seconds(now: int, timezone_name: str) -> int:
    """@brief 当日目标失效时刻：场景时区次日零点。"""
    midnight = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int((midnight + timedelta(days=1)).timestamp())


class WakeSnapshot(BaseModel):
    """@brief 某一结算边界处的世界摘要，唤醒条件判定的全部输入。

    引擎在每个结算边界装配本对象：时刻类字段为 UTC epoch 秒；分级为体感分级
    （见身体语义）；集合类字段为该边界处的实际状态，与上一次实际提供决策时的
    状态对比。所有标志缺省为 False/None，即全不命中即静默边界。
    """

    now: int
    planned_end: int | None = None
    felt_bands_now: dict[str, int] = {}
    felt_bands_at_last_decision: dict[str, int] = {}
    message_arrived: bool = False
    delivery_arrived: bool = False
    task_newly_visible: bool = False
    next_task_due_at: int | None = None
    work_review_returned: bool = False
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    idle_reconsideration_interval_seconds: int = IDLE_RECONSIDERATION_INTERVAL_SECONDS
    has_commitments: bool = False
    location_closing_at: int | None = None
    location_just_opened: bool = False
    people_present_now: frozenset[str] = frozenset()
    people_present_before: frozenset[str] = frozenset()
    activity_infeasible: bool = False
    consecutive_rejections: int = 0
    discovery_result_ready: bool = False
    wake_from_sleep: bool = False
    run_start: bool = False
    progress_report_due: bool = False
    is_asleep: bool = False


class WakeVerdict(BaseModel):
    """@brief 唤醒判定结果：是否提供决策与命中的全部唤醒原因。"""

    decision_offered: bool
    wake_reasons: list[WakeReason] = []


class NextDecisionQuery(BaseModel):
    """@brief 下一决策时刻计算的输入：各候选时刻由引擎侧预测/排定后传入。

    体感分级预计跨越时刻复用现有 next_body_boundary_seconds 预测
    （docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」），本模块不另写能耗或压力
    公式。scheduled_arrival_times 汇总已排定的通知与配送时刻。
    """

    now: int
    planned_end: int | None = None
    felt_band_crossing_times: tuple[int, ...] = ()
    scheduled_arrival_times: tuple[int, ...] = ()
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    idle_reconsideration_interval_seconds: int = IDLE_RECONSIDERATION_INTERVAL_SECONDS
    has_commitments: bool = False
    location_hours_change_times: tuple[int, ...] = ()
    next_task_due_at: int | None = None
    location_closing_at: int | None = None
    next_progress_report_at: int | None = None
    is_asleep: bool = False


class PlanGoal(BaseModel):
    """@brief 已登记的当日目标；推进时刻与活动标识是环境证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    intent_kind: IntentKind
    goal: str = Field(min_length=1, max_length=PLAN_GOAL_MAX_CHARS, pattern=r"\S")
    registered_at: int = Field(ge=0)
    progressed_at: int | None = Field(default=None, ge=0)
    progress_activity_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_progress(self) -> "PlanGoal":
        """@brief 推进证据必须成对存在，且不能早于登记。"""
        if (self.progressed_at is None) != (self.progress_activity_id is None):
            _LOGGER.error("Goal progress evidence is incomplete")
            raise ValueError("goal progress evidence must be present together")
        if self.progressed_at is not None and self.progressed_at <= self.registered_at:
            _LOGGER.error("Goal progress must follow registration")
            raise ValueError("goal progress must follow registration")
        return self


class DayPlan(BaseModel):
    """@brief 有序当日目标清单；空清单也是一次有效登记。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    day_end: int = Field(gt=0)
    goals: tuple[PlanGoal, ...] = Field(max_length=MAX_PLAN_GOALS)

    @model_validator(mode="after")
    def _check_plan(self) -> "DayPlan":
        """@brief 核验意图唯一、活动证据唯一及当日登记和推进边界。"""
        kinds = [goal.intent_kind for goal in self.goals]
        evidence = [goal.progress_activity_id for goal in self.goals if goal.progress_activity_id is not None]
        if len(kinds) != len(set(kinds)) or len(evidence) != len(set(evidence)):
            _LOGGER.error("Day plan contains duplicate intent or activity evidence")
            raise ValueError("day plan intent and activity evidence must be unique")
        if any(goal.registered_at >= self.day_end or
               (goal.progressed_at is not None and goal.progressed_at > self.day_end) for goal in self.goals):
            _LOGGER.error("Day plan goal evidence exceeds its local day")
            raise ValueError("goal evidence exceeds day_end")
        return self


def initial_day_plan(goals: Sequence[PlanGoal], day_end: int) -> DayPlan:
    """@brief 登记初次有序目标清单，所有容量和证据约束由领域模型校验。"""
    return DayPlan(day_end=day_end, goals=tuple(goals))


def _task_due_soon(now: int, due_at: int | None) -> bool:
    """@brief 截止预警窗口判定：[due-30min, due) 内视为即将截止。"""
    return due_at is not None and due_at - TASK_DUE_SOON_SECONDS <= now < due_at


def _closing_soon(now: int, closing_at: int | None) -> bool:
    """@brief 闭店预警窗口判定：[closing-15min, closing) 内视为即将闭店。"""
    return (
        closing_at is not None
        and closing_at - CLOSING_WARNING_SECONDS <= now < closing_at
    )


def _reconsideration_due(snapshot: WakeSnapshot) -> bool:
    """@brief 重估间隔判定：距上一次实际决策已满 interval 秒。

    last_decision_time 为 None（从未提供过决策）时不触发——首次决策由 run_start
    承担。
    """
    if snapshot.last_decision_time is None:
        return False
    elapsed = snapshot.now - snapshot.last_decision_time
    return elapsed >= reconsideration_interval(snapshot)


def reconsideration_interval(query: WakeSnapshot | NextDecisionQuery) -> int:
    """@brief 判定与调度共用同一在手承诺口径选择重估间隔。"""
    return (query.reconsideration_interval_seconds if query.has_commitments
            else query.idle_reconsideration_interval_seconds)


def evaluate_wake_reasons(snapshot: WakeSnapshot) -> WakeVerdict:
    """@brief 判定某结算边界命中的全部唤醒原因（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。

    输出按 WakeReason 枚举声明顺序排列；无命中即静默边界
    （decision_offered=false、wake_reasons=[]）。睡眠期间只保留计划结束与唤醒
    事件（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」），其余命中一律忽略。

    @param snapshot 该结算边界处的世界摘要
    @return 唤醒判定结果
    """
    snap = snapshot
    reasons: list[WakeReason] = []
    if snap.planned_end is not None and snap.now >= snap.planned_end:
        reasons.append(WakeReason.PLANNED_END)
    if snap.felt_bands_now != snap.felt_bands_at_last_decision:
        reasons.append(WakeReason.FELT_BAND_CHANGE)
    if snap.message_arrived:
        reasons.append(WakeReason.MESSAGE_ARRIVAL)
    if snap.delivery_arrived:
        reasons.append(WakeReason.DELIVERY_ARRIVAL)
    if snap.task_newly_visible:
        reasons.append(WakeReason.TASK_AVAILABLE)
    if _task_due_soon(snap.now, snap.next_task_due_at):
        reasons.append(WakeReason.TASK_DUE_SOON)
    if snap.work_review_returned:
        reasons.append(WakeReason.WORK_REVIEW_RESULT)
    closing_threat = snap.has_commitments and _closing_soon(snap.now, snap.location_closing_at)
    if _task_due_soon(snap.now, snap.next_task_due_at) or closing_threat or snap.activity_infeasible:
        reasons.append(WakeReason.COMMITMENT_THREATENED)
    if _reconsideration_due(snap):
        reasons.append(WakeReason.RECONSIDERATION_INTERVAL)
    if closing_threat or snap.location_just_opened:
        reasons.append(WakeReason.LOCATION_HOURS_CHANGE)
    if snap.people_present_now - snap.people_present_before:
        reasons.append(WakeReason.PERSON_ARRIVED)
    if snap.people_present_before - snap.people_present_now:
        reasons.append(WakeReason.PERSON_LEFT)
    if snap.activity_infeasible:
        reasons.append(WakeReason.ACTIVITY_INFEASIBLE)
    if snap.consecutive_rejections > 0:
        reasons.append(WakeReason.REJECTION_FEEDBACK)
    if snap.discovery_result_ready:
        reasons.append(WakeReason.DISCOVERY_RESULT)
    if snap.wake_from_sleep:
        reasons.append(WakeReason.WAKE_FROM_SLEEP)
    if snap.run_start:
        reasons.append(WakeReason.RUN_START)
    if snap.progress_report_due:
        reasons.append(WakeReason.PROGRESS_REPORT_DUE)
    if snap.is_asleep:
        reasons = [r for r in reasons if r in SLEEP_ALLOWED_REASONS]
    return WakeVerdict(decision_offered=bool(reasons), wake_reasons=reasons)


def next_decision_time(query: NextDecisionQuery) -> int | None:
    """@brief 计算下一决策时刻：各项候选的最小值（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。

    候选=计划结束、体感分级预计跨越、已排定通知与配送、承诺威胁边界、重估间隔
    到期、地点营业变化；只取严格晚于 now 的时刻，本边界已判定的机会不重复计。
    睡眠不受重估间隔影响（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」），is_asleep 时剔除该项；无任何候选返回
    None。游戏模式保持逐回合反馈，不经过本函数。

    @param query 各候选时刻的汇总输入
    @return 下一决策时刻（UTC epoch 秒），无候选时为 None
    """
    candidates: list[int] = []
    if query.planned_end is not None:
        candidates.append(query.planned_end)
    candidates.extend(query.felt_band_crossing_times)
    candidates.extend(query.scheduled_arrival_times)
    if not query.is_asleep and query.last_decision_time is not None:
        candidates.append(
            query.last_decision_time + reconsideration_interval(query)
        )
    candidates.extend(query.location_hours_change_times)
    if not query.is_asleep and query.next_task_due_at is not None:
        candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS)
    if not query.is_asleep and query.has_commitments and query.location_closing_at is not None:
        candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS)
    if query.next_progress_report_at is not None:
        candidates.append(query.next_progress_report_at)
    future = [moment for moment in candidates if moment > query.now]
    return min(future) if future else None


def revise_day_plan(plan: DayPlan, now: int, new_goals: Sequence[PlanGoal]) -> DayPlan:
    """@brief 保留已推进目标，整体替换未推进目标，未改写条目保留登记时刻。"""
    kept = tuple(goal for goal in plan.goals if goal.progressed_at is not None)
    progressed_kinds = {goal.intent_kind for goal in kept}
    if any(goal.intent_kind in progressed_kinds for goal in new_goals):
        _LOGGER.error("A progressed day goal cannot be resubmitted")
        raise ValueError("goal already progressed")
    pending = {(goal.intent_kind, goal.goal): goal for goal in plan.goals if goal.progressed_at is None}
    changed = tuple(pending.get((goal.intent_kind, goal.goal), goal) for goal in new_goals)
    return DayPlan(day_end=plan.day_end, goals=kept + changed)


def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> DayPlan:
    """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。"""
    activity_id = activity["activity_id"]
    if activity["status"] in {"cancelled", "failed"} or end <= start:
        return plan
    if any(goal.progress_activity_id == activity_id for goal in plan.goals):
        return plan
    family = activity["kind"]
    if family == "lifestyle":
        category = activity.get("category")
        if category is None:
            return plan
        family = f"lifestyle:{category}"
    stop = min(end, plan.day_end)
    for index, goal in enumerate(plan.goals):
        if goal.progressed_at is not None or stop <= max(start, goal.registered_at):
            continue
        if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
            continue
        progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
        goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
        return DayPlan(day_end=plan.day_end, goals=goals)
    return plan


def day_plan_expired(plan: DayPlan, now: int) -> bool:
    """@brief 判定目标清单是否跨日残留：now 越过计划自带的 day_end 即属前一天。

    引擎在跨日边界据此把 day_plan 归零，使"每天第一次醒来的决策必须先提交
    plan_day"在第二天重新成立；判定不做时区换算，day_end 即提交日的次日
    本地零点（修订沿用原计划的 day_end，判定对修订结果稳定）。

    @param plan 当日目标
    @param now 查询时刻（UTC epoch 秒）
    @return now >= plan.day_end 时为 True
    """
    return now >= plan.day_end


def expired_plan_agent_ids(state: Any) -> tuple[int, ...]:
    """@brief 枚举 day_plan 已跨日残留的人物编号（引擎跨日边界归零用，docs/dev/SPEC-life-realism.md「当日目标」）。

    @param state 世界状态（只读 agents 与 current_time 两字段；鸭子类型避免
                 与人物状态模型的环依赖）
    @return day_plan 残留的人物编号元组（按人物表插入序）
    """
    return tuple(agent_id for agent_id, agent in state.agents.items()
                 if agent.day_plan is not None
                 and day_plan_expired(agent.day_plan, state.current_time))
