"""@brief 唤醒条件判定与下一决策时刻的规格测试（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""

from __future__ import annotations

import pytest
from typing import get_args

from worldmock.life.schedule import (
    INTENT_KINDS,
    RECONSIDERATION_INTERVAL_SECONDS,
    IntentKind,
    NextDecisionQuery,
    WakeReason,
    WakeSnapshot,
    evaluate_wake_reasons,
    next_decision_time,
)

NOW = 1_000_000

FROZEN_WAKE_REASONS = (
    "planned_end",
    "felt_band_change",
    "message_arrival",
    "delivery_arrival",
    "task_available",
    "task_due_soon",
    "work_review_result",
    "plan_block_boundary",
    "reconsideration_interval",
    "location_hours_change",
    "person_arrived",
    "person_left",
    "activity_infeasible",
    "rejection_feedback",
    "discovery_result",
    "wake_from_sleep",
    "run_start",
)


def make_snapshot(**overrides) -> WakeSnapshot:
    """@brief 全条件不命中的基线快照，按用例覆盖字段。"""
    base = dict(
        now=NOW,
        planned_end=None,
        felt_bands_now={},
        felt_bands_at_last_decision={},
        message_arrived=False,
        delivery_arrived=False,
        task_newly_visible=False,
        next_task_due_at=None,
        work_review_returned=False,
        plan_block_starts=(),
        last_decision_time=None,
        reconsideration_interval_seconds=RECONSIDERATION_INTERVAL_SECONDS,
        location_closing_at=None,
        location_just_opened=False,
        people_present_now=frozenset(),
        people_present_before=frozenset(),
        activity_infeasible=False,
        consecutive_rejections=0,
        wake_from_sleep=False,
        run_start=False,
        is_asleep=False,
    )
    base.update(overrides)
    return WakeSnapshot(**base)


def verdict(**overrides) -> list[WakeReason]:
    """@brief 便捷封装：构造快照并返回命中的唤醒原因列表。"""
    return evaluate_wake_reasons(make_snapshot(**overrides)).wake_reasons


# 每个冻结成员一例正例：仅该成员的条件命中。
POSITIVE_CASES = {
    "planned_end": {"planned_end": NOW},
    "felt_band_change": {
        "felt_bands_now": {"sleep_pressure": 2},
        "felt_bands_at_last_decision": {"sleep_pressure": 1},
    },
    "message_arrival": {"message_arrived": True},
    "delivery_arrival": {"delivery_arrived": True},
    "task_available": {"task_newly_visible": True},
    "task_due_soon": {"next_task_due_at": NOW + 900},
    "work_review_result": {"work_review_returned": True},
    "plan_block_boundary": {"plan_block_starts": (NOW - 60, NOW, NOW + 60)},
    "reconsideration_interval": {"last_decision_time": NOW - 5400},
    "location_hours_change": {"location_closing_at": NOW + 900},
    "person_arrived": {
        "people_present_now": frozenset({"alice"}),
        "people_present_before": frozenset(),
    },
    "person_left": {
        "people_present_now": frozenset(),
        "people_present_before": frozenset({"bob"}),
    },
    "activity_infeasible": {"activity_infeasible": True},
    "rejection_feedback": {"consecutive_rejections": 2},
    "wake_from_sleep": {"wake_from_sleep": True},
    "discovery_result": {"discovery_result_ready": True},
    "run_start": {"run_start": True},
}

# 每个冻结成员一例反例：该成员条件以邻近未命中形态出现，其余全不命中。
NEGATIVE_CASES = {
    "planned_end": {"planned_end": NOW + 1},
    "felt_band_change": {
        "felt_bands_now": {"sleep_pressure": 1},
        "felt_bands_at_last_decision": {"sleep_pressure": 1},
    },
    "message_arrival": {"message_arrived": False},
    "delivery_arrival": {"delivery_arrived": False},
    "task_available": {"task_newly_visible": False},
    "task_due_soon": {"next_task_due_at": NOW + 1801},
    "work_review_result": {"work_review_returned": False},
    "plan_block_boundary": {"plan_block_starts": (NOW - 1, NOW + 60)},
    "reconsideration_interval": {"last_decision_time": NOW - 5399},
    "location_hours_change": {"location_closing_at": NOW + 901},
    "person_arrived": {
        "people_present_now": frozenset({"alice"}),
        "people_present_before": frozenset({"alice"}),
    },
    "person_left": {
        "people_present_now": frozenset({"alice"}),
        "people_present_before": frozenset({"alice"}),
    },
    "activity_infeasible": {"activity_infeasible": False},
    "rejection_feedback": {"consecutive_rejections": 0},
    "wake_from_sleep": {"wake_from_sleep": False},
    "discovery_result": {"discovery_result_ready": False},
    "run_start": {"run_start": False},
}


@pytest.mark.parametrize("member", FROZEN_WAKE_REASONS)
def test_positive_each_member(member: str) -> None:
    """@brief 每个唤醒成员各有一例正例：单条件命中即返回该原因。"""
    assert verdict(**POSITIVE_CASES[member]) == [WakeReason(member)]


@pytest.mark.parametrize("member", FROZEN_WAKE_REASONS)
def test_negative_each_member(member: str) -> None:
    """@brief 每个唤醒成员各有一例反例：邻近未命中形态不返回该原因。"""
    assert verdict(**NEGATIVE_CASES[member]) == []


def test_wake_reason_frozen_member_set() -> None:
    """@brief 冻结成员集校验：17 个成员、名称与声明序与规格一致。"""
    assert [reason.value for reason in WakeReason] == list(FROZEN_WAKE_REASONS)
    assert len(WakeReason) == 17


def test_intent_kind_literal_matches_tuple() -> None:
    """@brief IntentKind Literal 与 INTENT_KINDS 元组保持同步（防漂移）。"""
    assert set(get_args(IntentKind)) == set(INTENT_KINDS)
    assert len(INTENT_KINDS) == 11


def test_silent_boundary_all_conditions_off() -> None:
    """@brief 静默边界：全条件不命中时不提供决策、原因为空。"""
    result = evaluate_wake_reasons(make_snapshot())
    assert result.decision_offered is False
    assert result.wake_reasons == []


def test_multi_hit_returns_all_reasons() -> None:
    """@brief 多重命中返回全部原因，且提供决策。"""
    result = evaluate_wake_reasons(
        make_snapshot(
            planned_end=NOW,
            message_arrived=True,
            next_task_due_at=NOW + 60,
            people_present_now=frozenset({"alice"}),
        )
    )
    assert result.decision_offered is True
    assert set(result.wake_reasons) == {
        WakeReason.PLANNED_END,
        WakeReason.MESSAGE_ARRIVAL,
        WakeReason.TASK_DUE_SOON,
        WakeReason.PERSON_ARRIVED,
    }


def test_multi_hit_order_follows_enum_declaration() -> None:
    """@brief 多重命中的输出顺序稳定，按枚举声明序排列。"""
    result = verdict(
        run_start=True,
        wake_from_sleep=False,
        consecutive_rejections=1,
        message_arrived=True,
        planned_end=NOW,
    )
    assert result == [
        WakeReason.PLANNED_END,
        WakeReason.MESSAGE_ARRIVAL,
        WakeReason.REJECTION_FEEDBACK,
        WakeReason.RUN_START,
    ]


def test_felt_band_change_detects_added_or_removed_band() -> None:
    """@brief 体感分级新增/缺失某个分级键同样构成变化。"""
    assert verdict(
        felt_bands_now={"sleep_pressure": 1, "social_need": 2},
        felt_bands_at_last_decision={"sleep_pressure": 1},
    ) == [WakeReason.FELT_BAND_CHANGE]


def test_sleep_ignores_reconsideration_interval() -> None:
    """@brief 睡眠不受重估间隔影响（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）：间隔早已期满也不唤醒。"""
    assert verdict(is_asleep=True, last_decision_time=NOW - 7200) == []


def test_sleep_allows_planned_end_and_wake_events_only() -> None:
    """@brief 睡眠只在计划结束或唤醒事件时提供决策（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""
    assert verdict(is_asleep=True, planned_end=NOW) == [WakeReason.PLANNED_END]
    assert verdict(is_asleep=True, wake_from_sleep=True) == [WakeReason.WAKE_FROM_SLEEP]
    assert verdict(is_asleep=True, run_start=True) == [WakeReason.RUN_START]


def test_sleep_suppresses_external_events() -> None:
    """@brief 睡眠期间外部事件（消息/人物/任务）不中断睡眠。"""
    result = verdict(
        is_asleep=True,
        message_arrived=True,
        delivery_arrived=True,
        task_newly_visible=True,
        people_present_now=frozenset({"alice"}),
        location_just_opened=True,
    )
    assert result == []


def test_reconsideration_never_decided_does_not_fire() -> None:
    """@brief 从未提供过决策（last=None）不触发重估间隔——首决策由 run_start 承担。"""
    assert verdict(last_decision_time=None) == []


@pytest.mark.parametrize(
    ("field", "overrides"),
    [
        ("planned_end", {"planned_end": NOW}),
        ("task_due_soon", {"next_task_due_at": NOW + 1800}),
        ("location_hours_change", {"location_closing_at": NOW + 900}),
        ("reconsideration_interval", {"last_decision_time": NOW - 5400}),
        ("plan_block_boundary", {"plan_block_starts": (NOW,)}),
    ],
)
def test_boundary_exactly_at_threshold_fires(field: str, overrides: dict) -> None:
    """@brief 边界值：时刻恰好落在阈值起点（计划结束/预警窗口起点/间隔期满/段起点）即命中。"""
    assert verdict(**overrides) == [WakeReason(field)]


def test_task_due_window_excludes_overdue_and_far() -> None:
    """@brief 截止预警是 [due-30min, due) 窗口：过期与远期均不命中。"""
    assert verdict(next_task_due_at=NOW) == []
    assert verdict(next_task_due_at=NOW - 60) == []
    assert verdict(next_task_due_at=NOW + 1801) == []


def test_location_open_event_fires() -> None:
    """@brief 已开门事件构成 location_hours_change 唤醒（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""
    assert verdict(location_just_opened=True) == [WakeReason.LOCATION_HOURS_CHANGE]


def test_closing_window_excludes_already_closed() -> None:
    """@brief 已过闭店时刻不再构成"即将闭店"。"""
    assert verdict(location_closing_at=NOW) == []
    assert verdict(location_closing_at=NOW - 60) == []


def make_query(**overrides) -> NextDecisionQuery:
    """@brief 基线下一决策查询构造器。"""
    base = dict(
        now=NOW,
        planned_end=None,
        felt_band_crossing_times=(),
        scheduled_arrival_times=(),
        plan_block_starts=(),
        last_decision_time=None,
        reconsideration_interval_seconds=RECONSIDERATION_INTERVAL_SECONDS,
        location_hours_change_times=(),
        is_asleep=False,
    )
    base.update(overrides)
    return NextDecisionQuery(**base)


def test_next_decision_takes_minimum_of_all_candidates() -> None:
    """@brief 下一决策时刻取全部候选的最小值（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）。"""
    moment = next_decision_time(
        make_query(
            planned_end=NOW + 500,
            felt_band_crossing_times=(NOW + 300, NOW + 700),
            scheduled_arrival_times=(NOW + 400,),
            plan_block_starts=(NOW + 200,),
            last_decision_time=NOW - 1000,
            location_hours_change_times=(NOW + 600,),
        )
    )
    assert moment == NOW + 200


def test_next_decision_excludes_now_and_past() -> None:
    """@brief 只取严格晚于 now 的候选：本边界与过去时刻不参与。"""
    moment = next_decision_time(
        make_query(
            planned_end=NOW,
            felt_band_crossing_times=(NOW - 5,),
            scheduled_arrival_times=(NOW,),
        )
    )
    assert moment is None


def test_next_decision_no_candidates_returns_none() -> None:
    """@brief 无任何候选返回 None。"""
    assert next_decision_time(make_query()) is None


def test_next_decision_awake_includes_reconsideration_expiry() -> None:
    """@brief 清醒时重估到期参与最小值计算。"""
    moment = next_decision_time(
        make_query(planned_end=NOW + 1000, last_decision_time=NOW - 5000)
    )
    assert moment == NOW + 400


def test_next_decision_asleep_skips_reconsideration_expiry() -> None:
    """@brief 睡眠不受重估间隔影响（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）：到期时刻被剔除。"""
    moment = next_decision_time(
        make_query(planned_end=NOW + 1000, last_decision_time=NOW - 5000, is_asleep=True)
    )
    assert moment == NOW + 1000


def test_next_decision_custom_interval_is_scenario_parameter() -> None:
    """@brief 重估间隔是场景参数，可覆盖默认 5400。"""
    moment = next_decision_time(
        make_query(
            planned_end=NOW + 1000,
            last_decision_time=NOW - 5000,
            reconsideration_interval_seconds=5500,
        )
    )
    assert moment == NOW + 500


def test_next_decision_reconsideration_without_last_decision_skipped() -> None:
    """@brief 无上一次决策时刻时不产生重估候选。"""
    moment = next_decision_time(
        make_query(last_decision_time=None, plan_block_starts=(NOW + 60,))
    )
    assert moment == NOW + 60
