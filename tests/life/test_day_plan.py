"""@brief 日程骨架 DayPlan 的规格测试（docs/dev/SPEC-life-realism.md「日程骨架」）。"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from worldmock.life.schedule import (
    INTENT_ACTIVITY_FAMILIES,
    INTENT_KINDS,
    DayPlan,
    PlanSegment,
    bedtime_anchor,
    current_segment,
    day_end_seconds,
    day_plan_expired,
    default_bedtime_end_hour,
    expired_plan_agent_ids,
    initial_day_plan,
    is_plan_block_boundary,
    next_segment_start,
    revise_day_plan,
    segment_followed,
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
    """@brief <5 段在首次 plan_day 构造处非法；模型本身接受修订后的短尾骨架。

    docs/dev/SPEC-life-realism.md「日程骨架」：下限只约束首次 plan_day（initial_day_plan 承载），
    尾段修订后剩余段数可少于 5——该状态必须可通过加载口径重校验。
    """
    with pytest.raises(ValueError, match="at least 5 segments"):
        initial_day_plan(make_segments(4), DAY_END, BEDTIME)
    assert len(make_plan(4).segments) == 4  # 修订形态的短骨架在模型层合法


def test_too_many_segments_rejected() -> None:
    """@brief >8 段非法（首次与修订同一上限）。"""
    with pytest.raises(ValidationError, match="at most 8 segments"):
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


def test_day_plan_expired_boundary_at_day_end() -> None:
    """@brief day_plan_expired：day_end 前未过期，恰在 day_end（跨日零点）起过期。

    引擎用该判定在跨日边界把 day_plan 归零，使docs/dev/SPEC-life-realism.md「日程骨架」"每天第一次醒来
    必须先提交 plan_day"在第二天重新成立（边界值：now == day_end 即过期）。
    """
    plan = make_plan(5)
    assert day_plan_expired(plan, DAY_START) is False
    assert day_plan_expired(plan, BEDTIME) is False
    assert day_plan_expired(plan, DAY_END - 1) is False
    assert day_plan_expired(plan, DAY_END) is True
    assert day_plan_expired(plan, DAY_END + 3600) is True


def test_expired_plan_agent_ids_lists_only_stale_plans() -> None:
    """@brief 跨日归零名单只含残留计划；当日计划与无计划人物不在名单。"""
    from types import SimpleNamespace

    state = SimpleNamespace(current_time=DAY_END - 300, agents={
        1: SimpleNamespace(day_plan=make_plan(5, day_end=DAY_END - 600)),  # 已跨日残留
        2: SimpleNamespace(day_plan=make_plan(5)),                         # 仍是当日计划
        3: SimpleNamespace(day_plan=None),
    })
    assert expired_plan_agent_ids(state) == (1,)


# ---------------------------------------------------------------------------
# D6 缺口 3：就寝锚点取值链与真实骨架约束
# ---------------------------------------------------------------------------

_TZ = "Asia/Shanghai"


def test_initial_plan_bedtime_is_real_constraint() -> None:
    """@brief 首次骨架的 bedtime 来自传入锚点：末段早于锚点拒绝、恰好到达通过。"""
    segments = make_segments(5)  # 覆盖到 BEDTIME（21:00）
    with pytest.raises(ValidationError, match="cover until bedtime"):
        initial_day_plan(segments, DAY_END, BEDTIME + 3600)
    anchored = initial_day_plan(segments, DAY_END, BEDTIME)
    assert anchored.bedtime == BEDTIME, "锚点必须原样留痕在 DayPlan.bedtime"


def test_bedtime_anchor_prefers_declaration_then_persona_mapping_default() -> None:
    """@brief 取值链：人物声明末端优先；缺省=persona-mapping 默认窗口末端（非时钟常量）。"""
    from datetime import datetime

    base = int(datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo(_TZ)).timestamp())
    declared = bedtime_anchor(base, _TZ, 23.0)
    assert declared == int(datetime(2026, 1, 5, 23, 0, tzinfo=ZoneInfo(_TZ)).timestamp())
    # 缺省：persona-mapping [defaults.time_anchors].bedtime 窗口末端（当前 24:00）
    assert default_bedtime_end_hour() == 24.0
    assert bedtime_anchor(base, _TZ, None) == day_end_seconds(base, _TZ)


def test_bedtime_anchor_cross_midnight_declaration_clamps_to_day_end() -> None:
    """@brief 跨午夜声明（末端 >24h）钳制到当日结束，锚点不可越过 day_end。"""
    from datetime import datetime

    base = int(datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo(_TZ)).timestamp())
    assert bedtime_anchor(base, _TZ, 25.0) == day_end_seconds(base, _TZ)
    assert bedtime_anchor(base, _TZ, 30.0) == day_end_seconds(base, _TZ)


# ---------------------------------------------------------------------------
# D6 缺口 8：照做判定（时间加权主导活动家族）
# ---------------------------------------------------------------------------

def test_intent_activity_families_cover_closed_intent_set() -> None:
    """@brief 家族映射恰好覆盖 11 类意图闭集（不遗漏、不多出）。"""
    assert set(INTENT_ACTIVITY_FAMILIES) == set(INTENT_KINDS)
    assert all(family for family in INTENT_ACTIVITY_FAMILIES.values())


def test_segment_followed_dominant_family_decides() -> None:
    """@brief 照做两态：主导家族匹配发放判定通过；不匹配或空闲主导则不通过。"""
    window = 3600
    assert segment_followed("工作", [("work", 3600)], window) is True
    assert segment_followed("工作", [("wait", 3600)], window) is False
    # 时间加权：工作 2000s + 等待 1600s → 主导工作 → 照做
    assert segment_followed("工作", [("work", 2000), ("wait", 1600)], window) is True
    # 空闲主导：段大部分无活动 → 未照做（idle 不属于任何意图家族）
    assert segment_followed("工作", [("work", 1000)], window) is False
    # 生活活动按品类令牌匹配
    assert segment_followed("家务", [("lifestyle:household", 3600)], window) is True
    assert segment_followed("家务", [("lifestyle:hobby", 3600)], window) is False
    assert segment_followed("娱乐", [("lifestyle:hobby", 3600)], window) is True
    # 目录缺失的生活活动解析为 unknown：不匹配任何家族（fail-closed）
    assert segment_followed("运动", [("lifestyle:unknown", 3600)], window) is False
    # 非正覆盖秒数跳过（不贡献权重）：零长覆盖 + 满窗等待 → 等待主导
    assert segment_followed("工作", [("work", 0), ("wait", 3600)], window) is False
    # 未知意图或非正窗口 fail-closed
    assert segment_followed("钓鱼", [("work", 3600)], window) is False
    assert segment_followed("工作", [("work", 0)], 0) is False
