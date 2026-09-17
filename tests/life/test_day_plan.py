"""@brief 日程骨架 DayPlan 的规格测试（规格 L1604-1614）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life.schedule import (
    DayPlan,
    PlanSegment,
    current_segment,
    is_plan_block_boundary,
    next_segment_start,
    revise_day_plan,
)

DAY_START = 25200  # 07:00
BEDTIME = 75600  # 21:00
DAY_END = 86400  # 当日结束

KINDS = ("晨间例程", "通勤", "工作", "用餐", "娱乐", "社交", "家务", "运动", "休息")


def make_segments(count: int) -> list[PlanSegment]:
    """@brief 构造覆盖 [DAY_START, BEDTIME] 的 count 段合法骨架（段间连续）。"""
    width = (BEDTIME - DAY_START) // count
    bounds = [DAY_START + index * width for index in range(count)] + [BEDTIME]
    return [
        PlanSegment(start=bounds[i], end=bounds[i + 1], intent_kind=KINDS[i], note="")
        for i in range(count)
    ]


def make_plan(count: int = 6, **overrides) -> DayPlan:
    """@brief 构造合法 DayPlan，可覆盖段/就寝/当日结束。"""
    fields = dict(bedtime=BEDTIME, day_end=DAY_END, segments=make_segments(count))
    fields.update(overrides)
    return DayPlan(**fields)


def seg(start: int, end: int, kind: str = "娱乐") -> PlanSegment:
    """@brief 构造单段。"""
    return PlanSegment(start=start, end=end, intent_kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [5, 6, 7, 8])
def test_valid_plan_segment_counts(count: int) -> None:
    """@brief 5-8 段均为合法骨架。"""
    plan = make_plan(count)
    assert len(plan.segments) == count


def test_too_few_segments_rejected() -> None:
    """@brief <5 段非法。"""
    with pytest.raises(ValidationError, match="5 to 8 segments"):
        make_plan(4)


def test_too_many_segments_rejected() -> None:
    """@brief >8 段非法。"""
    with pytest.raises(ValidationError, match="5 to 8 segments"):
        make_plan(9)


def test_overlapping_segments_rejected() -> None:
    """@brief 段重叠非法。"""
    segments = make_segments(5)
    segments[1] = seg(segments[1].start, segments[2].end - 60, "工作")
    with pytest.raises(ValidationError, match="overlap"):
        DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)


def test_touching_boundary_is_valid() -> None:
    """@brief 边界值：前段 end 恰等于后段 start 不算重叠。"""
    segments = [
        seg(DAY_START, 36000, "晨间例程"),
        seg(36000, 46800, "工作"),
        seg(46800, 57600, "用餐"),
        seg(57600, 68400, "社交"),
        seg(68400, BEDTIME, "娱乐"),
    ]
    plan = DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)
    assert plan.segments[-1].end == BEDTIME


def test_out_of_order_segments_rejected() -> None:
    """@brief 段乱序（后段起点早于前段终点）非法。"""
    segments = make_segments(5)
    segments[0], segments[1] = segments[1], segments[0]
    with pytest.raises(ValidationError, match="overlap or are out of order"):
        DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)


def test_segment_beyond_day_end_rejected() -> None:
    """@brief 段越过当日结束非法。"""
    segments = make_segments(5)
    segments[-1] = seg(segments[-1].start, DAY_END + 60, "娱乐")
    with pytest.raises(ValidationError, match="ends after day_end"):
        DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)


def test_unknown_intent_kind_rejected() -> None:
    """@brief intent_kind 闭集外的取值非法。"""
    with pytest.raises(ValidationError):
        seg(DAY_START, DAY_START + 3600, "钓鱼")


def test_not_covering_bedtime_rejected() -> None:
    """@brief 最末段未覆盖到就寝非法。"""
    segments = make_segments(5)
    segments[-1] = seg(segments[-1].start, BEDTIME - 600, "娱乐")
    with pytest.raises(ValidationError, match="cover until bedtime"):
        DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)


def test_zero_length_segment_rejected() -> None:
    """@brief start == end 的零长段非法（含边界值 start 恰等于 end）。"""
    with pytest.raises(ValidationError, match="earlier than end"):
        seg(DAY_START, DAY_START, "睡眠")


def test_gap_between_segments_is_allowed() -> None:
    """@brief 段间留空（自由时间）合法——规格只禁重叠、要求覆盖到就寝。"""
    segments = [
        seg(DAY_START, 28800, "晨间例程"),
        seg(28800, 32400, "通勤"),
        seg(36000, 46800, "工作"),
        seg(46800, 61200, "用餐"),
        seg(61200, BEDTIME, "娱乐"),
    ]
    plan = DayPlan(bedtime=BEDTIME, day_end=DAY_END, segments=segments)
    assert current_segment(plan, 34000) is None


def test_current_segment_inside_and_at_exact_start() -> None:
    """@brief 当前段查询：段内与恰在段起点的时刻均返回该段（边界值）。"""
    plan = make_plan(6)
    assert current_segment(plan, plan.segments[2].start) is plan.segments[2]
    assert current_segment(plan, plan.segments[2].start + 1) is plan.segments[2]


def test_current_segment_at_exact_end_is_next_or_none() -> None:
    """@brief 恰在段终点：连续骨架落到后继段；最末段终点之后为 None（边界值）。"""
    plan = make_plan(6)
    assert current_segment(plan, plan.segments[0].end) is plan.segments[1]
    assert current_segment(plan, BEDTIME) is None
    assert current_segment(plan, BEDTIME + 60) is None


def test_next_segment_start_returns_earliest_future() -> None:
    """@brief 下一计划段起点：返回严格晚于查询时刻的最早起点。"""
    plan = make_plan(6)
    moment = next_segment_start(plan, plan.segments[1].end - 1)
    assert moment == plan.segments[2].start


def test_next_segment_start_none_when_exhausted() -> None:
    """@brief 已无后续段时返回 None。"""
    plan = make_plan(6)
    assert next_segment_start(plan, BEDTIME) is None


def test_is_plan_block_boundary_exact_match_only() -> None:
    """@brief 段起点判定只认精确相等：恰在起点为 True，差一秒为 False（边界值）。"""
    plan = make_plan(6)
    assert is_plan_block_boundary(plan, plan.segments[3].start) is True
    assert is_plan_block_boundary(plan, plan.segments[3].start + 1) is False
    assert is_plan_block_boundary(plan, plan.segments[3].start - 1) is False


def test_revise_replaces_only_unstarted_segments() -> None:
    """@brief 修订只改写尚未开始的段，已开始段原样保留。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60  # 位于第 3 段内（42000-50400）
    revised = revise_day_plan(
        plan,
        now,
        [
            seg(50400, 61200, "用餐"),
            seg(61200, BEDTIME, "社交"),
        ],
    )
    assert revised.segments[:3] == plan.segments[:3]
    assert len(revised.segments) == 5
    assert revised.segments[-1].end == BEDTIME
    assert plan.segments == make_plan(6).segments  # 原对象不变


def test_revise_rejects_rewriting_started_segment() -> None:
    """@brief 试图改写已开始的段（start < now）被拒绝。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60
    with pytest.raises(ValueError, match="already started"):
        revise_day_plan(plan, now, [seg(now - 60, BEDTIME, "工作")])


def test_revise_segment_starting_exactly_now_is_locked() -> None:
    """@brief 边界值：start 恰等于 now 的段视为已开始，不可作为新段写入。"""
    plan = make_plan(6)
    now = plan.segments[3].start
    with pytest.raises(ValueError, match="already started"):
        revise_day_plan(plan, now, [seg(now, BEDTIME, "工作")])


def test_revise_rejects_gap_in_coverage() -> None:
    """@brief 修订后未覆盖到就寝被拒绝。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60
    with pytest.raises(ValueError, match="cover until bedtime"):
        revise_day_plan(plan, now, [seg(50400, BEDTIME - 600, "工作")])


def test_revise_rejects_overlap_with_kept_prefix() -> None:
    """@brief 新段与保留前缀重叠被拒绝。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60
    kept_end = plan.segments[2].end
    with pytest.raises(ValueError, match="overlap"):
        revise_day_plan(plan, now, [seg(kept_end - 60, BEDTIME, "工作")])


def test_revise_rejects_exceeding_day_end() -> None:
    """@brief 修订后越过当日结束被拒绝。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60
    with pytest.raises(ValueError, match="day_end"):
        revise_day_plan(plan, now, [seg(50400, DAY_END + 60, "工作")])


def test_revise_rejects_exceeding_segment_cap() -> None:
    """@brief 修订后总数超过 8 段被拒绝。"""
    plan = make_plan(6)
    now = plan.segments[0].start + 60  # 仅第 1 段已开始（25200-33600）
    new_segments = [
        seg(33600 + index * 4000, 33600 + (index + 1) * 4000, "休息")
        for index in range(8)
    ]
    new_segments.append(seg(33600 + 8 * 4000, BEDTIME, "娱乐"))
    with pytest.raises(ValueError, match="exceeds 8"):
        revise_day_plan(plan, now, new_segments)


def test_revise_result_supports_segment_queries() -> None:
    """@brief 修订结果可继续用于当前段/下一起点查询。"""
    plan = make_plan(6)
    now = plan.segments[2].start + 60
    revised = revise_day_plan(plan, now, [seg(50400, BEDTIME, "社交")])
    assert current_segment(revised, now) is revised.segments[2]
    assert next_segment_start(revised, now) == 50400
    assert is_plan_block_boundary(revised, 50400) is True
