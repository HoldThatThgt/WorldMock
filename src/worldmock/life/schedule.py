"""@brief 当日目标与事件驱动决策节律的纯函数合同。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo

import logging
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worldmock.base_models import FrozenModel
from worldmock.errors import reject_value
from worldmock.footprints.intervals import valid_interval

_LOGGER = logging.getLogger(__name__)


LIFESTYLE_CATEGORY_FAMILIES = {
    "sport": "exercise", "household": "chore", "social": "social", "outdoor": "outing",
    "hobby": "entertainment", "learning": "entertainment", "wellness": "rest",
    "pet": "chore", "volunteer": "social",
}


def alarm_time(value: str) -> int:
    """@brief 接受带偏移的 ISO 整秒时刻，拒绝小数秒和无时区输入。"""
    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
    try:
        parsed = datetime.fromisoformat(value) if re.fullmatch(pattern, value) else None
    except ValueError:
        parsed = None
    if parsed is None:
        reject_value("ALARM_TIME_INVALID: Alarm time must be a future whole second before the run ends.")
    return int(parsed.timestamp())

#: 有在手承诺时的常规重估间隔。
RECONSIDERATION_INTERVAL_SECONDS = 5400
#: 没有在手承诺时的较长重估间隔。
IDLE_RECONSIDERATION_INTERVAL_SECONDS = 10800
#: 当前地点的闭店预警窗口。
#: 可见工作任务的截止预警窗口。
#: 有序目标清单的容量上限。
MAX_PLAN_GOALS = 8
#: 单条自然语言目标的字符上限。
PLAN_GOAL_MAX_CHARS = 300
#: 目标大致时刻的判定容差：同家族活动在当日内的进行区间与 [around − 容差, around + 容差] 有正时长交集才算推进
#: （docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ，裁决 D12）。experiment_assumption：
#: 敏感区间 3600–7200 秒，登记在
#: docs/EXPERIMENT.md#experiment-worldmock-实验设置「实验假设声明」，复跑后按数据校准。
GOAL_TIME_TOLERANCE_SECONDS = 5400


class WakeReason(str, Enum):
    """@brief 唤醒原因冻结枚举（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」，成员不可改名、
    不可删除，新增走版本表）。"""

    PLANNED_END = "planned_end"
    FELT_BAND_CHANGE = "felt_band_change"
    MESSAGE_ARRIVAL = "message_arrival"
    DELIVERY_ARRIVAL = "delivery_arrival"
    COMMITMENT_THREATENED = "commitment_threatened"
    RECONSIDERATION_INTERVAL = "reconsideration_interval"
    PERSON_ARRIVED = "person_arrived"
    PERSON_LEFT = "person_left"
    ACTIVITY_INFEASIBLE = "activity_infeasible"
    REJECTION_FEEDBACK = "rejection_feedback"
    DISCOVERY_RESULT = "discovery_result"
    WAKE_FROM_SLEEP = "wake_from_sleep"
    RUN_START = "run_start"
    PROGRESS_REPORT_DUE = "progress_report_due"
    NO_ACTIVE_ACTIVITY = "no_active_activity"
    PLAN_REMINDER_DUE = "plan_reminder_due"
    #: 个人事务提醒到点（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ）：不进入睡眠允许集合，睡眠中错过的在醒来的边界交付。
    ERRAND_REMINDER_DUE = "errand_reminder_due"
    #: 班次与休息时段的起止（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 ）：不进入睡眠允许集合，睡过的醒来后不补。
    SHIFT_START = "shift_start"
    SHIFT_END = "shift_end"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    APP_PUSH = "app_push"
    ALARM_RING = "alarm_ring"


SLEEP_ALLOWED_REASONS = frozenset(
    {WakeReason.PLANNED_END, WakeReason.WAKE_FROM_SLEEP, WakeReason.RUN_START,
     WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING}
)
"""睡眠期间仍可提供决策的唤醒原因：计划结束、唤醒事件与当日目标提醒（docs/dev/SPEC-life-runtime.md#spec-life-runtime-决策节律结算边界与唤醒条件分离 「决策节律」）。"""

DUTY_EDGE_REASONS: tuple[WakeReason, ...] = (
    WakeReason.SHIFT_START, WakeReason.SHIFT_END, WakeReason.BREAK_START, WakeReason.BREAK_END,
)
"""班次与休息时段起止的四个唤醒成员，按声明次序（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 ）。"""


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


#: 系统活动与数据集动作的固定聚合家族。
ACTIVITY_KIND_FAMILIES = {"sleep": "sleep", "travel": "commute", "wait": "rest"}
ACTION_CATEGORY_FAMILIES = {
    "entertainment": "entertainment", "work_study": "work", "social": "social",
    "exercise": "exercise", "daily_care": "chore",
}


def activity_family(snapshot: Any) -> str | None:
    """@brief 动作只读其真实类别，系统活动只认三个固定种类。"""
    kind = snapshot.get("kind")
    if kind == "action":
        category = snapshot.get("data", {}).get("category")
        return category if category in ACTION_CATEGORY_FAMILIES else None
    return kind if isinstance(kind, str) and kind in ACTIVITY_KIND_FAMILIES else None


INTENT_ACTIVITY_FAMILIES: dict[str, frozenset[str]] = {
    "睡眠": frozenset({"sleep"}), "晨间例程": frozenset({"daily_care"}),
    "通勤": frozenset({"travel"}), "工作": frozenset({"work_study"}), "用餐": frozenset(),
    "休息": frozenset({"wait", "daily_care"}), "娱乐": frozenset({"entertainment"}), "社交": frozenset({"social"}),
    "家务": frozenset({"daily_care"}), "运动": frozenset({"exercise"}), "外出": frozenset({"travel"}),
}
"""意图到实际活动家族的固定映射；用餐只由正餐收货推进。"""


ROUTINE_INTENT_KINDS: frozenset[str] = frozenset({"睡眠", "晨间例程", "通勤", "用餐"})
"""例行意图（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事 ，裁决 S11）：十一种意图闭集中属于人设层作息的四种。这类目标照常受理、
判定与记录，质量报告的推进比例只计非例行意图的目标，例行意图的目标另计一行。"""


def day_end_seconds(now: int, timezone_name: str) -> int:
    """@brief 当日目标失效时刻：场景时区次日零点。"""
    midnight = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int((midnight + timedelta(days=1)).timestamp())


def clock_epoch(clock: str | None, now: int, timezone_name: str) -> int | None:
    """@brief 把场景本地 HH:MM 换算为 now 所在本地日的绝对时刻；None 原样返回。

    提醒时刻 remind_time（None 表示不设提醒，docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章）与大致时刻 around_time
    （None 表示不限时刻，docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）共用这一换算。
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

    大致时刻不像提醒：不要求晚于登记时刻，也不触发决策（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）。
    """
    return tuple(PlanGoal(intent_kind=goal.intent_kind, goal=goal.goal, registered_at=now,
                          remind_at=clock_epoch(goal.remind_time, now, timezone_name),
                          around_at=clock_epoch(goal.around_time, now, timezone_name)) for goal in goals)


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
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    idle_reconsideration_interval_seconds: int = IDLE_RECONSIDERATION_INTERVAL_SECONDS
    has_commitments: bool = False
    people_present_now: frozenset[str] = frozenset()
    people_present_before: frozenset[str] = frozenset()
    discovery_result_ready: bool = False
    wake_from_sleep: bool = False
    run_start: bool = False
    progress_report_due: bool = False
    #: 边界兜底（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章规则 5）：结算后没有进行中活动。
    no_active_activity: bool = False
    #: 当日目标提醒到点（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章）：同时到期的合并为一项。
    plan_reminder_due: bool = False
    #: 有已到点未提醒的个人事务（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ）：同时到期的合并为一项。
    errand_reminder_due: bool = False
    #: 本边界恰好成立的班次与休息时段起止成员（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 ）：同一秒重合的各记一次。
    duty_edges_now: frozenset[WakeReason] = frozenset()
    #: 本边界实际收到应用推送，睡眠过滤仍由既有允许集合负责。
    app_push_arrived: bool = False
    #: 本边界实际结算的一次性闹钟，睡眠期间也可以提供决策。
    alarm_rang: bool = False
    is_asleep: bool = False


class WakeVerdict(BaseModel):
    """@brief 唤醒判定结果：是否提供决策与命中的全部唤醒原因。"""

    decision_offered: bool
    wake_reasons: list[WakeReason] = []


class NextDecisionQuery(BaseModel):
    """@brief 下一决策时刻计算的输入：各候选时刻由引擎侧预测/排定后传入。

    体感分级预计跨越时刻复用现有 next_body_boundary_seconds 预测
    （docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」），本模块不另写能耗或压力
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
    next_progress_report_at: int | None = None
    #: 当日未触发的提醒时刻（睡眠中不剔除，docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章）。
    plan_reminder_times: tuple[int, ...] = ()
    #: 未提醒的个人事务提醒时刻（睡眠中剔除，docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ）。
    errand_reminder_times: tuple[int, ...] = ()
    #: 严格晚于当前的班次与休息时段起止时刻（醒睡都照填、睡眠中剔除，docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。
    duty_edge_times: tuple[int, ...] = ()
    is_asleep: bool = False


class PlanGoal(FrozenModel):
    """@brief 已登记的当日目标；推进时刻与活动标识是环境证据，remind_at/reminded_at 是提醒及其触发时刻，
    around_at 是大致时刻 around_time 换算出的当日绝对时刻（为空表示不限时刻，
    docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）。"""

    intent_kind: IntentKind
    goal: str = Field(min_length=1, max_length=PLAN_GOAL_MAX_CHARS, pattern=r"\S")
    registered_at: int = Field(ge=0)
    progressed_at: int | None = Field(default=None, ge=0)
    progress_activity_id: str | None = Field(default=None, min_length=1)
    progress_order_id: str | None = Field(default=None, min_length=1)
    progress_note: str = Field(default="", max_length=500)
    remind_at: int | None = Field(default=None, ge=0)
    reminded_at: int | None = Field(default=None, ge=0)
    around_at: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_progress(self) -> "PlanGoal":
        """@brief 推进证据必须成对存在，且不能早于登记；只有设了提醒的目标才有触发时刻，且不早于提醒时刻。"""
        evidence_count = sum(item is not None for item in (self.progress_activity_id, self.progress_order_id))
        if evidence_count != int(self.progressed_at is not None):
            _LOGGER.error("Goal progress evidence is incomplete")
            raise ValueError("goal progress evidence must be present together")
        if (self.progressed_at is not None and
                (self.progressed_at < self.registered_at or
                 (self.progressed_at == self.registered_at and self.progress_activity_id is not None))):
            _LOGGER.error("Goal progress must follow registration")
            raise ValueError("goal progress must follow registration")
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
        """@brief 核验意图唯一、活动证据唯一及当日登记和推进边界。"""
        kinds = [goal.intent_kind for goal in self.goals]
        evidence = [(kind, identity) for goal in self.goals
                    for kind, identity in (("activity", goal.progress_activity_id), ("order", goal.progress_order_id))
                    if identity is not None]
        if len(kinds) != len(set(kinds)) or len(evidence) != len(set(evidence)):
            _LOGGER.error("Day plan contains duplicate intent or activity evidence")
            raise ValueError("day plan intent and activity evidence must be unique")
        if any(goal.registered_at >= self.day_end or
               (goal.progressed_at is not None and goal.progressed_at > self.day_end)
               or (goal.remind_at is not None and goal.remind_at >= self.day_end)
               or (goal.around_at is not None and goal.around_at >= self.day_end) for goal in self.goals):
            _LOGGER.error("Day plan goal evidence exceeds its local day")
            raise ValueError("goal evidence exceeds day_end")
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
    """@brief 按当前结算边界判定全部唤醒原因。
    规格：docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则。
    按 WakeReason 声明顺序输出（前半由 _event_reasons、后半由 _clock_reasons 逐项追加）；无命中即静默边界，
    decision_offered=false、wake_reasons=[]。
    睡眠期间只保留 SLEEP_ALLOWED_REASONS（含目标提醒，不含边界兜底、事务提醒与班次边界的四个成员）。
    """
    snap = snapshot
    reasons = _event_reasons(snap) + _clock_reasons(snap)
    if snap.is_asleep:
        reasons = [r for r in reasons if r in SLEEP_ALLOWED_REASONS]
    return WakeVerdict(decision_offered=bool(reasons), wake_reasons=reasons)


def _event_reasons(snap: WakeSnapshot) -> list[WakeReason]:
    """@brief 唤醒原因的前半（计划结束至发现结果）：活动、体感、到达事件、任务与地点、在场人物，按声明顺序逐项追加。"""
    reasons: list[WakeReason] = []
    if snap.planned_end is not None and snap.now >= snap.planned_end:
        reasons.append(WakeReason.PLANNED_END)
    if snap.felt_bands_now != snap.felt_bands_at_last_decision:
        reasons.append(WakeReason.FELT_BAND_CHANGE)
    if snap.message_arrived:
        reasons.append(WakeReason.MESSAGE_ARRIVAL)
    if snap.delivery_arrived:
        reasons.append(WakeReason.DELIVERY_ARRIVAL)
    if _reconsideration_due(snap):
        reasons.append(WakeReason.RECONSIDERATION_INTERVAL)
    if snap.people_present_now - snap.people_present_before:
        reasons.append(WakeReason.PERSON_ARRIVED)
    if snap.people_present_before - snap.people_present_now:
        reasons.append(WakeReason.PERSON_LEFT)
    if snap.discovery_result_ready:
        reasons.append(WakeReason.DISCOVERY_RESULT)
    return reasons


def _clock_reasons(snap: WakeSnapshot) -> list[WakeReason]:
    """@brief 唤醒原因的后半（睡醒至班次边界）：睡醒、运行开始、播报、边界兜底、两种提醒与班次和休息时段的起止，
    按声明顺序逐项追加；同一秒重合的起止各记一次，按 DUTY_EDGE_REASONS 的次序并列
    （docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 ）。"""
    reasons: list[WakeReason] = []
    if snap.wake_from_sleep:
        reasons.append(WakeReason.WAKE_FROM_SLEEP)
    if snap.run_start:
        reasons.append(WakeReason.RUN_START)
    if snap.progress_report_due:
        reasons.append(WakeReason.PROGRESS_REPORT_DUE)
    if snap.no_active_activity:
        reasons.append(WakeReason.NO_ACTIVE_ACTIVITY)
    if snap.plan_reminder_due:
        reasons.append(WakeReason.PLAN_REMINDER_DUE)
    if snap.errand_reminder_due:
        reasons.append(WakeReason.ERRAND_REMINDER_DUE)
    reasons.extend(member for member in DUTY_EDGE_REASONS if member in snap.duty_edges_now)
    if snap.app_push_arrived:
        reasons.append(WakeReason.APP_PUSH)
    if snap.alarm_rang:
        reasons.append(WakeReason.ALARM_RING)
    return reasons


def next_decision_time(query: NextDecisionQuery) -> int | None:
    """@brief
    计算下一决策时刻：各项候选的最小值（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）。

    候选=计划结束、体感分级预计跨越、已排定通知与配送、承诺威胁边界、重估间隔
    到期、地点营业变化、进行中活动的播报到期、当日未触发的提醒、未提醒的个人事务提醒与清醒人物的班次与休息时段起止
    （睡眠中剔除）；只取严格晚于 now 的时刻，本边界已判定的机会不重复计。
    睡眠不受重估间隔影响（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」），is_asleep
    时剔除该项；
    当日目标提醒在睡眠中不剔除（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章），个人事务提醒在睡眠中剔除
    （docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 ），班次与休息时段的起止同样在睡眠中剔除
    （docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。无任何候选返回 None。游戏模式保持逐回合反馈，不经过本函数。

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
    if query.next_progress_report_at is not None:
        candidates.append(query.next_progress_report_at)
    candidates.extend(query.plan_reminder_times)
    if not query.is_asleep:
        candidates.extend(query.errand_reminder_times)
    if not query.is_asleep:
        candidates.extend(query.duty_edge_times)
    future = [moment for moment in candidates if moment > query.now]
    return min(future) if future else None


def revise_day_plan(plan: DayPlan, now: int, new_goals: Sequence[PlanGoal]) -> DayPlan:
    """@brief 保留已推进目标，整体替换未推进目标（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「计划与提醒」）。

    意图与文字相同的条目是同一个目标，按四个字段（意图、文字、提醒、大致时刻）与原条目比较
    （docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）：四项都相同的原样保留登记时刻与提醒状态，不重校时刻（原样
    重发已触发的
    目标不被拒绝）；只改了大致时刻的保留登记时刻与提醒状态，只换大致时刻（大致时刻不重校、不触发决策）；改了提醒的
    保留登记时刻，换成新提醒与新的大致时刻；被替换或删除的目标，旧提醒随之取消。新登记与改了提醒的条目经
    require_remind_window 校验。

    @param plan 当日计划
    @param now 修订时刻
    @param new_goals 以修订时刻登记、提醒与大致时刻已换算为绝对时刻的新未推进目标
    """
    kept = tuple(goal for goal in plan.goals if goal.progressed_at is not None)
    progressed_kinds = {goal.intent_kind for goal in kept}
    if any(goal.intent_kind in progressed_kinds for goal in new_goals):
        _LOGGER.error("A progressed day goal cannot be resubmitted")
        raise ValueError("goal already progressed")
    pending = {(goal.intent_kind, goal.goal): goal for goal in plan.goals if goal.progressed_at is None}
    changed = tuple(_revised_goal(pending.get((goal.intent_kind, goal.goal)), goal, now, plan.day_end)
                    for goal in new_goals)
    return DayPlan(day_end=plan.day_end, goals=kept + changed)


def _revised_goal(old: PlanGoal | None, new: PlanGoal, now: int, day_end: int) -> PlanGoal:
    """@brief 修订中的一条未推进目标：old 为同一意图与文字的原条目（没有时为 None），规则见 revise_day_plan。"""
    if old is not None and old.remind_at == new.remind_at:
        return old if old.around_at == new.around_at else old.model_copy(update={"around_at": new.around_at})
    require_remind_window(new, now, day_end)
    if old is None:
        return new
    return old.model_copy(update={"remind_at": new.remind_at, "reminded_at": None, "around_at": new.around_at})


def ran_in_goal_window(around_at: int | None, started_at: int | None, stop: int) -> bool:
    """@brief 大致时刻判定（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）：活动在当日内的进行区间
    [started_at, stop] 与
    [around_at − 容差, around_at + 容差] 的交集时长为正；around_at 为空时不限时刻（沿用家族与登记后正时长结算的判定）。

    started_at 是活动的实际开始时刻，不是结算区间起点；stop 是已按次日零点裁剪的结算止点。当日零点一侧不另裁剪：
    目标登记不早于当日零点，而结算止点须晚于登记，交集时长是否为正不受零点之前那一段影响。有大致时刻却没有活动开始
    时刻（残缺快照）时不成立。推进判定（progress_day_plan）与目标度量（evaluation.goal_metrics）共用本函数。
    """
    if around_at is None:
        return True
    if started_at is None:
        return False
    lower = max(started_at, around_at - GOAL_TIME_TOLERANCE_SECONDS)
    return lower < min(stop, around_at + GOAL_TIME_TOLERANCE_SECONDS)


def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int,
                      work_content_ids: tuple[str, ...]) -> DayPlan:
    """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。

    匹配：未推进、同家族、登记后的正时长结算，目标带大致时刻时活动须在其前后容差内进行（ran_in_goal_window，
    以活动快照的 started_at 为活动开始时刻，docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）。

    @param activity 结算后的活动快照
    @param start 结算区间起点
    @param end 结算区间终点
    """
    activity_id = activity["activity_id"]
    if activity["status"] in {"cancelled", "failed"} or not valid_interval(start, end):
        return plan
    if any(goal.progress_activity_id == activity_id for goal in plan.goals):
        return plan
    family = activity_family(activity)
    if family == "work_study" and activity["data"]["content_id"] not in work_content_ids:
        return plan
    stop = min(end, plan.day_end)
    for index, goal in enumerate(plan.goals):
        if goal.progressed_at is not None or stop <= max(start, goal.registered_at):
            continue
        if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
            continue
        if not ran_in_goal_window(goal.around_at, activity.get("started_at"), stop):
            continue
        progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
        goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
        return DayPlan(day_end=plan.day_end, goals=goals)
    return plan


def progress_receipt_day_plan(plan: DayPlan, receipt: dict, now: int) -> DayPlan:
    """@brief 本人的正餐收货在登记之后、当天与大致时刻容差内推进用餐目标，订单证据当天只用一次。"""
    if receipt.get("kind") != "goods_received" or receipt["category"] != "meal" or now >= plan.day_end:
        return plan
    order_id = receipt["order_id"]
    if any(goal.progress_order_id == order_id for goal in plan.goals):
        return plan
    for index, goal in enumerate(plan.goals):
        if goal.intent_kind != "用餐" or goal.progressed_at is not None or now < goal.registered_at:
            continue
        if goal.around_at is not None and abs(now - goal.around_at) > GOAL_TIME_TOLERANCE_SECONDS:
            continue
        progressed = goal.model_copy(update={"progressed_at": now, "progress_order_id": order_id})
        goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
        return DayPlan(day_end=plan.day_end, goals=goals)
    return plan


def day_plan_expired(plan: DayPlan, now: int) -> bool:
    """@brief 判定目标清单是否跨日残留：now 越过计划自带的 day_end 即属前一天。

    引擎在零点边界据此把 day_plan 归零，随后由零点计划为新的一天生成计划
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章）；判定不做时区换算，day_end 即生成日的次日
    本地零点（修订沿用原计划的 day_end，判定对修订结果稳定）。

    @param plan 当日目标
    @param now 查询时刻（UTC epoch 秒）
    @return now >= plan.day_end 时为 True
    """
    return now >= plan.day_end


def expired_plan_agent_ids(state: Any) -> tuple[int, ...]:
    """@brief 枚举 day_plan 已跨日残留的人物编号（引擎跨日边界归零用，docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「当日目标」）。

    @param state 世界状态（只读 agents 与 current_time 两字段；鸭子类型避免
                 与人物状态模型的环依赖）
    @return day_plan 残留的人物编号元组（按人物表插入序）
    """
    return tuple(agent_id for agent_id, agent in state.agents.items()
                 if agent.day_plan is not None
                 and day_plan_expired(agent.day_plan, state.current_time))
