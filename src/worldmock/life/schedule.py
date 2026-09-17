"""@brief 决策节律与日程骨架：唤醒条件判定、下一决策时刻、当日计划（纯函数库）。

结算边界与决策机会分离（规格 L1447-1477）：activity_feedback_seconds=900 仍作为
身体/进度/内容的结算粒度，每个结算边界照常结算资源；是否在该边界向人物提供
决策机会只由唤醒条件决定。无任何命中的边界是静默边界：decision_offered=false、
wake_reasons=[]，环境按人物已提交的计划续行，不调用模型。续行不是环境替人物
选择——人物的选择就是它提交的计划持续时间，静默边界只是不打断这一选择。

下一决策时刻取下列各项的最小值：计划结束、体感分级预计跨越时刻（由调用方
传入，复用现有 next_body_boundary_seconds 预测，本模块不另写公式）、已排定的
通知与配送、计划段起点、重估间隔到期、地点营业变化（规格 L1471-1473）。

日程骨架（规格 L1604-1614）：每天第一次醒来的决策必须先提交 5-8 段 plan_day，
覆盖到当日就寝、段不重叠、不越过当日结束；环境不执行计划步，到达段起点只构成
plan_block_boundary 唤醒，由人物当场决定是否照做。revise_day_plan 只允许改写
尚未开始的段。

睡眠契约（规格 L1470）：睡眠不受重估间隔影响，只在计划结束或唤醒事件
（wake_from_sleep、run_start）时提供决策；外部事件不中断睡眠，引擎若要让事件
唤醒睡眠者，应以 wake_from_sleep 标志表达。游戏模式保持逐回合反馈，由引擎侧
处理，本模块不感知。

数值参数：reconsideration_interval_seconds 默认 5400 秒（Kleitman 基本休息-
活动周期约 90 分钟，规格 L1459/L1476-1477；场景参数可覆盖）；闭店预警窗口
15 分钟（规格 L1460 定值）与任务截止预警窗口 30 分钟（规格 L1457 定值）为
规格冻结常数。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, model_validator


RECONSIDERATION_INTERVAL_SECONDS = 5400
"""距上一次实际决策满该秒数触发重估唤醒；默认 90 分钟（Kleitman BRAC，规格 L1459）。"""

CLOSING_WARNING_SECONDS = 900
"""闭店预警窗口：距闭店不足该秒数视为"即将闭店"（规格 L1460 定值 15 分钟）。"""

TASK_DUE_SOON_SECONDS = 1800
"""截止预警窗口：距任务截止不足该秒数视为"即将截止"（规格 L1457 定值 30 分钟）。"""

MIN_PLAN_SEGMENTS = 5
"""日程骨架最少段数（规格 L1606-1607）。"""

MAX_PLAN_SEGMENTS = 8
"""日程骨架最多段数（规格 L1606-1607）。"""


class WakeReason(str, Enum):
    """@brief 唤醒原因冻结枚举（规格 L1452-1463，成员集不可增删）。"""

    PLANNED_END = "planned_end"
    FELT_BAND_CHANGE = "felt_band_change"
    MESSAGE_ARRIVAL = "message_arrival"
    DELIVERY_ARRIVAL = "delivery_arrival"
    TASK_AVAILABLE = "task_available"
    TASK_DUE_SOON = "task_due_soon"
    WORK_REVIEW_RESULT = "work_review_result"
    PLAN_BLOCK_BOUNDARY = "plan_block_boundary"
    RECONSIDERATION_INTERVAL = "reconsideration_interval"
    LOCATION_HOURS_CHANGE = "location_hours_change"
    PERSON_ARRIVED = "person_arrived"
    PERSON_LEFT = "person_left"
    ACTIVITY_INFEASIBLE = "activity_infeasible"
    REJECTION_FEEDBACK = "rejection_feedback"
    WAKE_FROM_SLEEP = "wake_from_sleep"
    RUN_START = "run_start"


SLEEP_ALLOWED_REASONS = frozenset(
    {WakeReason.PLANNED_END, WakeReason.WAKE_FROM_SLEEP, WakeReason.RUN_START}
)
"""睡眠期间仍可提供决策的唤醒原因：仅计划结束与唤醒事件（规格 L1470）。"""


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
"""计划段意图闭集（规格 L1607-1608）；与下方 IntentKind Literal 保持同步。"""

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
    plan_block_starts: tuple[int, ...] = ()
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    location_closing_at: int | None = None
    location_just_opened: bool = False
    people_present_now: frozenset[str] = frozenset()
    people_present_before: frozenset[str] = frozenset()
    activity_infeasible: bool = False
    consecutive_rejections: int = 0
    wake_from_sleep: bool = False
    run_start: bool = False
    is_asleep: bool = False


class WakeVerdict(BaseModel):
    """@brief 唤醒判定结果：是否提供决策与命中的全部唤醒原因。"""

    decision_offered: bool
    wake_reasons: list[WakeReason] = []


class NextDecisionQuery(BaseModel):
    """@brief 下一决策时刻计算的输入：各候选时刻由引擎侧预测/排定后传入。

    体感分级预计跨越时刻复用现有 next_body_boundary_seconds 预测（规格 L1471-
    1472），本模块不另写能耗或压力公式。scheduled_arrival_times 汇总已排定的
    通知与配送时刻。
    """

    now: int
    planned_end: int | None = None
    felt_band_crossing_times: tuple[int, ...] = ()
    scheduled_arrival_times: tuple[int, ...] = ()
    plan_block_starts: tuple[int, ...] = ()
    last_decision_time: int | None = None
    reconsideration_interval_seconds: int = RECONSIDERATION_INTERVAL_SECONDS
    location_hours_change_times: tuple[int, ...] = ()
    is_asleep: bool = False


class PlanSegment(BaseModel):
    """@brief 日程骨架的单段：起止时刻（UTC epoch 秒）、意图与备注。"""

    start: int
    end: int
    intent_kind: IntentKind
    note: str = ""

    @model_validator(mode="after")
    def _check_span(self) -> "PlanSegment":
        """@brief 校验段跨度为正。"""
        if self.start >= self.end:
            raise ValueError("segment start must be earlier than end")
        return self


class DayPlan(BaseModel):
    """@brief 当日日程骨架：5-8 段、覆盖到就寝、不越过当日结束（规格 L1606-1607）。

    bedtime 为当日就寝时刻，day_end 为当日结束时刻；段间允许留空（未计划的自由
    时间），但不得重叠、不得乱序，最末段必须覆盖到 bedtime。
    """

    bedtime: int
    day_end: int
    segments: tuple[PlanSegment, ...]

    @model_validator(mode="after")
    def _check_plan(self) -> "DayPlan":
        """@brief 校验段数与骨架约束。"""
        if not (MIN_PLAN_SEGMENTS <= len(self.segments) <= MAX_PLAN_SEGMENTS):
            raise ValueError(
                f"plan must contain {MIN_PLAN_SEGMENTS} to "
                f"{MAX_PLAN_SEGMENTS} segments, got {len(self.segments)}"
            )
        _validate_skeleton(self.segments, self.bedtime, self.day_end)
        return self


def _validate_skeleton(
    segments: Sequence[PlanSegment], bedtime: int, day_end: int
) -> None:
    """@brief 校验骨架结构：段不越过当日结束、不重叠不乱序、覆盖到就寝。

    @param segments 按起点升序排列的段序列
    @param bedtime 当日就寝时刻
    @param day_end 当日结束时刻
    @raises ValueError 任一约束不满足时抛出英文错误
    """
    for index, segment in enumerate(segments):
        if segment.end > day_end:
            raise ValueError(f"segment {index} ends after day_end")
    for previous, current in zip(segments, segments[1:]):
        if current.start < previous.end:
            raise ValueError("segments overlap or are out of order")
    if segments and segments[-1].end < bedtime:
        raise ValueError("plan does not cover until bedtime")


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
    return elapsed >= snapshot.reconsideration_interval_seconds


def evaluate_wake_reasons(snapshot: WakeSnapshot) -> WakeVerdict:
    """@brief 判定某结算边界命中的全部唤醒原因（规格 L1452-1463）。

    输出按 WakeReason 枚举声明顺序排列；无命中即静默边界
    （decision_offered=false、wake_reasons=[]）。睡眠期间只保留计划结束与唤醒
    事件（规格 L1470），其余命中一律忽略。

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
    if snap.now in snap.plan_block_starts:
        reasons.append(WakeReason.PLAN_BLOCK_BOUNDARY)
    if _reconsideration_due(snap):
        reasons.append(WakeReason.RECONSIDERATION_INTERVAL)
    if _closing_soon(snap.now, snap.location_closing_at) or snap.location_just_opened:
        reasons.append(WakeReason.LOCATION_HOURS_CHANGE)
    if snap.people_present_now - snap.people_present_before:
        reasons.append(WakeReason.PERSON_ARRIVED)
    if snap.people_present_before - snap.people_present_now:
        reasons.append(WakeReason.PERSON_LEFT)
    if snap.activity_infeasible:
        reasons.append(WakeReason.ACTIVITY_INFEASIBLE)
    if snap.consecutive_rejections > 0:
        reasons.append(WakeReason.REJECTION_FEEDBACK)
    if snap.wake_from_sleep:
        reasons.append(WakeReason.WAKE_FROM_SLEEP)
    if snap.run_start:
        reasons.append(WakeReason.RUN_START)
    if snap.is_asleep:
        reasons = [r for r in reasons if r in SLEEP_ALLOWED_REASONS]
    return WakeVerdict(decision_offered=bool(reasons), wake_reasons=reasons)


def next_decision_time(query: NextDecisionQuery) -> int | None:
    """@brief 计算下一决策时刻：各项候选的最小值（规格 L1471-1473）。

    候选=计划结束、体感分级预计跨越、已排定通知与配送、计划段起点、重估间隔
    到期、地点营业变化；只取严格晚于 now 的时刻，本边界已判定的机会不重复计。
    睡眠不受重估间隔影响（规格 L1470），is_asleep 时剔除该项；无任何候选返回
    None。游戏模式保持逐回合反馈，不经过本函数。

    @param query 各候选时刻的汇总输入
    @return 下一决策时刻（UTC epoch 秒），无候选时为 None
    """
    candidates: list[int] = []
    if query.planned_end is not None:
        candidates.append(query.planned_end)
    candidates.extend(query.felt_band_crossing_times)
    candidates.extend(query.scheduled_arrival_times)
    candidates.extend(query.plan_block_starts)
    if not query.is_asleep and query.last_decision_time is not None:
        candidates.append(
            query.last_decision_time + query.reconsideration_interval_seconds
        )
    candidates.extend(query.location_hours_change_times)
    future = [moment for moment in candidates if moment > query.now]
    return min(future) if future else None


def current_segment(plan: DayPlan, now: int) -> PlanSegment | None:
    """@brief 查询 now 时刻所在的当前计划段。

    @param plan 当日日程骨架
    @param now 查询时刻（UTC epoch 秒）
    @return 满足 start <= now < end 的段；处于段间空隙时为 None
    """
    for segment in plan.segments:
        if segment.start <= now < segment.end:
            return segment
    return None


def next_segment_start(plan: DayPlan, now: int) -> int | None:
    """@brief 查询下一计划段起点。

    @param plan 当日日程骨架
    @param now 查询时刻（UTC epoch 秒）
    @return 严格晚于 now 的最早段起点；已无后续段时为 None
    """
    future = [segment.start for segment in plan.segments if segment.start > now]
    return min(future) if future else None


def is_plan_block_boundary(plan: DayPlan, now: int) -> bool:
    """@brief 判断 now 是否恰为某计划段起点（构成 plan_block_boundary 唤醒）。

    @param plan 当日日程骨架
    @param now 查询时刻（UTC epoch 秒）
    @return 恰在某段起点上为 True
    """
    return any(segment.start == now for segment in plan.segments)


def revise_day_plan(
    plan: DayPlan, now: int, new_segments: Sequence[PlanSegment]
) -> DayPlan:
    """@brief 增量修订日程：只允许改写尚未开始的段（规格 L1611）。

    已开始的段（start <= now，含恰在 now 起段的边界时刻）原样保留为前缀，
    尾部由 new_segments 整体替换；替换后仍须满足骨架约束（不重叠、不越过
    当日结束、覆盖到就寝）且总数不超过上限。修订结果不强制 5 段下限——
    一天尾段只剩少量待改段时下限会让修订无法成立，下限只约束首次 plan_day。

    @param plan 被修订的当日日程骨架
    @param now 修订时刻（UTC epoch 秒）
    @param new_segments 替换全部未开始段的新段序列（按起点升序）
    @return 修订后的新 DayPlan（原对象不变）
    @raises ValueError 试图改写已开始的段或替换后违反骨架约束时抛出
    """
    kept = tuple(segment for segment in plan.segments if segment.start <= now)
    for segment in new_segments:
        if segment.start <= now:
            raise ValueError("cannot rewrite a segment that already started")
    merged = kept + tuple(new_segments)
    if len(merged) > MAX_PLAN_SEGMENTS:
        raise ValueError(f"revised plan exceeds {MAX_PLAN_SEGMENTS} segments")
    _validate_skeleton(merged, plan.bedtime, plan.day_end)
    return DayPlan.model_construct(
        bedtime=plan.bedtime, day_end=plan.day_end, segments=merged
    )
