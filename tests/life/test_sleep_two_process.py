"""两过程睡眠与睡眠不足账的公式数值点测试（修订合同 2026-09-18）。

期望值一律用测试内独立推导的原始公式（raw_*）或冻结字面量表达，不复用
被测模块的组合逻辑。docs/dev/SPEC-life-runtime.md「两过程睡眠模型」的锚点示例中 22:00 与约 22:45 两档与冻结
公式存在小差异（见 test_anchor_trajectory_07_waker 注释），按合同以公式为准。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.life.conftest import context
from tests.life.test_scheduling import _custom_engine
from worldmock.life import felt, projection
from worldmock.life.body import (ActivityFactors, BoundaryClock, initial_body,
                                 next_shared_body_boundary_seconds, settle_shared_body)
from worldmock.life.engine import _initial_agent
from worldmock.life.models import ActivityKind, AgentState, BodyState
from worldmock.life.scenario import load_scenario
from worldmock.life.sleep import (
    DEFAULT_CHRONOTYPE_MIDSLEEP_H,
    EfficiencyCurvePoint,
    FOOD_UTILIZATION_CURVE,
    WORK_EFFICIENCY_CURVE,
    SleepinessGrade,
    deficit_after_awake,
    deficit_after_sleep,
    food_utilization_efficiency,
    initial_sleep_pressure,
    interpolate_efficiency,
    pressure_after_awake,
    pressure_after_sleep,
    sleepiness,
    sleepiness_grade,
    sleep_onset_threshold,
    validate_efficiency_curve,
    validate_sleep_need_hours,
    wake_threshold,
    work_efficiency,
)


def raw_cos(hour: float, midsleep: float = 4.0) -> float:
    """测试侧独立推导：昼夜相位余弦。"""
    return math.cos(2.0 * math.pi * (hour - midsleep) / 24.0)


def raw_h(hour: float, midsleep: float = 4.0) -> float:
    """测试侧独立推导：入睡阈值 H(t)。"""
    return 0.67 - 0.12 * raw_cos(hour, midsleep)


def raw_l(hour: float, midsleep: float = 4.0) -> float:
    """测试侧独立推导：醒来阈值 L(t)。"""
    return 0.17 - 0.12 * raw_cos(hour, midsleep)


def point(input_hours: float, output_factor: float) -> EfficiencyCurvePoint:
    """构造曲线节点的简写。"""
    return EfficiencyCurvePoint(input_hours=input_hours, output_factor=output_factor)


class TestThresholdOscillation:
    """入睡与醒来阈值的昼夜摆动。"""

    def test_threshold_values_at_phase_extremes(self):
        # t_min=04:00 处余弦为 1，阈值取最低；+12h（16:00）取最高。
        assert sleep_onset_threshold(4.0) == pytest.approx(0.67 - 0.12, rel=1e-9)
        assert wake_threshold(4.0) == pytest.approx(0.17 - 0.12, rel=1e-9)
        assert sleep_onset_threshold(16.0) == pytest.approx(0.67 + 0.12, rel=1e-9)
        assert wake_threshold(16.0) == pytest.approx(0.17 + 0.12, rel=1e-9)

    def test_thresholds_match_raw_formula_across_day(self):
        for hour in [0.0, 3.5, 7.0, 11.0, 15.75, 20.0, 23.0]:
            assert sleep_onset_threshold(hour) == pytest.approx(raw_h(hour), rel=1e-9)
            assert wake_threshold(hour) == pytest.approx(raw_l(hour), rel=1e-9)

    def test_threshold_span_is_constant_half(self):
        # H 与 L 共用同一余弦项，差恒为 0.5。
        for hour in [0.0, 6.0, 12.0, 18.0, 22.75]:
            span = sleep_onset_threshold(hour) - wake_threshold(hour)
            assert span == pytest.approx(0.5, rel=1e-9)

    def test_threshold_minimum_at_midsleep(self):
        assert wake_threshold(4.0) < wake_threshold(3.0)
        assert wake_threshold(4.0) < wake_threshold(5.0)
        assert sleep_onset_threshold(4.0) < sleep_onset_threshold(3.0)

    def test_threshold_period_24h(self):
        assert wake_threshold(28.0) == pytest.approx(wake_threshold(4.0), rel=1e-12)
        assert sleep_onset_threshold(-1.0) == pytest.approx(sleep_onset_threshold(23.0), rel=1e-12)

    def test_default_midsleep_is_04(self):
        assert DEFAULT_CHRONOTYPE_MIDSLEEP_H == 4.0


class TestLateChronotypeShift:
    """睡眠中点每晚一小时，阈值曲线整体后移一小时。"""

    def test_late_threshold_equals_default_one_hour_earlier(self):
        for hour in [5.0, 12.0, 20.0, 23.5]:
            assert wake_threshold(hour, 5.0) == pytest.approx(wake_threshold(hour - 1.0), rel=1e-12)
            assert sleep_onset_threshold(hour, 5.0) == pytest.approx(
                sleep_onset_threshold(hour - 1.0), rel=1e-12)

    def test_late_threshold_minimum_moves_to_05(self):
        assert wake_threshold(5.0, 5.0) == pytest.approx(0.05, rel=1e-9)
        assert wake_threshold(5.0, 5.0) < wake_threshold(4.0, 5.0)
        assert wake_threshold(5.0, 5.0) < wake_threshold(6.0, 5.0)

    def test_late_trajectory_shifts_one_hour(self):
        # 08:00 醒来、中点 05:00 的晚型人物与 07:00 醒来、中点 04:00 的默认人物
        # 在各自同一"醒后钟点差"下困意逐点相等（整体后移一小时）。
        s_default = wake_threshold(7.0)
        s_late = wake_threshold(8.0, 5.0)
        assert s_late == pytest.approx(s_default, rel=1e-12)
        for hours_awake in [1.0, 5.0, 13.0, 15.75]:
            hour_default = 7.0 + hours_awake
            hour_late = hour_default + 1.0
            pressure_default = pressure_after_awake(s_default, hours_awake)
            pressure_late = pressure_after_awake(s_late, hours_awake)
            assert sleepiness(pressure_late, hour_late, 5.0) == pytest.approx(
                sleepiness(pressure_default, hour_default), rel=1e-12)


class TestPressureSteps:
    """两过程 S 的清醒累积与睡眠衰减。"""

    def test_awake_step_matches_raw_formula(self):
        s0 = 0.0851471862576143
        for hours in [0.5, 7.0, 13.0, 20.0]:
            expected = 1.0 - (1.0 - s0) * math.exp(-hours / 18.2)
            assert pressure_after_awake(s0, hours) == pytest.approx(expected, rel=1e-9)

    def test_sleep_step_matches_raw_formula(self):
        s0 = 0.6389417145876977
        for hours in [0.25, 4.2, 8.0]:
            expected = s0 * math.exp(-hours / 4.2)
            assert pressure_after_sleep(s0, hours) == pytest.approx(expected, rel=1e-9)

    def test_sleep_decay_half_at_time_constant(self):
        # 4.2h 睡眠后压力衰减为 exp(-1) 倍。
        assert pressure_after_sleep(0.6, 4.2) == pytest.approx(0.6 / math.e, rel=1e-9)

    def test_awake_pressure_approaches_one(self):
        assert pressure_after_awake(0.1, 250.0) == pytest.approx(1.0, abs=1e-5)

    def test_composed_steps_depend_on_order(self):
        # 同一总时长，先睡后醒与先醒后睡结果不同（非线性过程）。
        asleep_then_awake = pressure_after_awake(pressure_after_sleep(0.6, 4.0), 4.0)
        awake_then_asleep = pressure_after_sleep(pressure_after_awake(0.6, 4.0), 4.0)
        assert asleep_then_awake != awake_then_asleep
        assert asleep_then_awake > awake_then_asleep

    @pytest.mark.parametrize("hours", [-0.5, -1e-9])
    def test_negative_duration_rejected(self, hours):
        with pytest.raises(ValueError):
            pressure_after_awake(0.5, hours)
        with pytest.raises(ValueError):
            pressure_after_sleep(0.5, hours)


class TestSleepinessClipping:
    """困意公式与 [0, 1.2] 截断。"""

    def test_sleepiness_mid_values_match_raw(self):
        for pressure, hour in [(0.55, 20.0), (0.3, 8.0), (0.0851471862576143, 7.0)]:
            expected = (pressure - raw_l(hour)) / (raw_h(hour) - raw_l(hour))
            assert sleepiness(pressure, hour) == pytest.approx(expected, rel=1e-9)

    def test_sleepiness_clips_to_zero_below_wake_threshold(self):
        # 04:00 时 L=0.05，压力低于阈值 -> 0。
        assert sleepiness(0.01, 4.0) == 0.0
        assert sleepiness(0.049, 4.0) == 0.0

    def test_sleepiness_clips_to_1_2_above(self):
        # 16:00 时 L=0.29，(1.5-0.29)/0.5=2.42 -> 截断 1.2。
        assert sleepiness(1.5, 16.0) == 1.2

    def test_sleepiness_at_threshold_edges(self):
        # S 恰在 L 上困意为 0，恰在 H 上困意为 1，均不触截断。
        assert sleepiness(wake_threshold(16.0), 16.0) == pytest.approx(0.0, abs=1e-12)
        assert sleepiness(sleep_onset_threshold(16.0), 16.0) == pytest.approx(1.0, rel=1e-9)

    def test_sleepiness_zero_at_wake_boundary(self):
        assert sleepiness(0.0, 4.0) == 0.0


class TestSleepinessGradeBands:
    """困意五档分级的区间边界（左闭右开）。"""

    @pytest.mark.parametrize("value,expected", [
        (0.0, SleepinessGrade.AWAKE),
        (0.4999, SleepinessGrade.AWAKE),
        (0.5, SleepinessGrade.SLIGHTLY_TIRED),
        (0.7499, SleepinessGrade.SLIGHTLY_TIRED),
        (0.75, SleepinessGrade.SLEEPY),
        (0.8999, SleepinessGrade.SLEEPY),
        (0.9, SleepinessGrade.VERY_SLEEPY),
        (0.9999, SleepinessGrade.VERY_SLEEPY),
        (1.0, SleepinessGrade.CANNOT_STAY_AWAKE),
        (1.2, SleepinessGrade.CANNOT_STAY_AWAKE),
    ])
    def test_grade_boundaries(self, value, expected):
        assert sleepiness_grade(value) is expected


class TestAnchorTrajectory:
    """07:00 起床者的规格锚点轨迹（以冻结公式为准）。"""

    @staticmethod
    def anchor_sleepiness(hour: float) -> float:
        """07:00 在醒来阈值上醒来，清醒推进到 hour 时刻的困意。"""
        s = pressure_after_awake(wake_threshold(7.0), hour - 7.0)
        return sleepiness(s, hour)

    def test_anchor_values_match_raw_derivation(self):
        # 字面期望由 raw 公式独立推导（rel=1e-6）。
        assert self.anchor_sleepiness(20.0) == pytest.approx(0.644283, rel=1e-6)
        assert self.anchor_sleepiness(21.0) == pytest.approx(0.750054, rel=1e-6)
        assert self.anchor_sleepiness(22.0) == pytest.approx(0.857498, rel=1e-6)
        assert self.anchor_sleepiness(22.75) == pytest.approx(0.936718, rel=1e-6)

    def test_anchor_grades_20_and_21_match_spec(self):
        # 规格锚点：20:00 有点乏；21:00 困（0.750054，恰好越过 0.75 档界）。
        assert sleepiness_grade(self.anchor_sleepiness(20.0)) is SleepinessGrade.SLIGHTLY_TIRED
        assert sleepiness_grade(self.anchor_sleepiness(21.0)) is SleepinessGrade.SLEEPY

    def test_anchor_grades_22_and_2245_follow_formula_not_spec(self):
        # 规格锚点写 22:00 很困、约 22:45 撑不住；冻结公式给出 22:00 困
        # （0.8575 < 0.9）、22:45 很困（0.9367 < 1.0）。按合同以公式为准，
        # 差异在交付报告中如实记录。
        assert sleepiness_grade(self.anchor_sleepiness(22.0)) is SleepinessGrade.SLEEPY
        assert sleepiness_grade(self.anchor_sleepiness(22.75)) is SleepinessGrade.VERY_SLEEPY

    def test_cannot_stay_awake_onset_about_23_22(self):
        # 公式下"撑不住"（困意=1.0）实际出现在约 23.37h（23:22），
        # 比规格示例的"约 22:45"晚约 37 分钟。
        lo, hi = 23.0, 23.5
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if self.anchor_sleepiness(mid) < 1.0:
                lo = mid
            else:
                hi = mid
        onset = (lo + hi) / 2.0
        assert 23.3 < onset < 23.4
        assert sleepiness_grade(self.anchor_sleepiness(onset + 0.01)) is (
            SleepinessGrade.CANNOT_STAY_AWAKE)


class TestInitialSleepPressure:
    """初始 sleep_pressure 的按已睡时长反推。"""

    def test_back_calculation_from_eight_hours_sleep(self):
        # 07:00 起点、已睡 8h：入睡时刻为 23:00（-1h 按周期解释），
        # S = H(23:00)·exp(-8/4.2h)。
        expected = raw_h(23.0) * math.exp(-8.0 / 4.2)
        assert initial_sleep_pressure(7.0, 8.0) == pytest.approx(expected, rel=1e-9)
        assert initial_sleep_pressure(7.0, 8.0) == pytest.approx(0.09511164, rel=1e-6)

    def test_zero_slept_hours_returns_onset_threshold(self):
        assert initial_sleep_pressure(7.0, 0.0) == pytest.approx(raw_h(7.0), rel=1e-9)

    def test_long_sleep_decays_toward_zero(self):
        # H(7-24=−17h 按周期即 H(7))·exp(−24/4.2) ≈ 0.5851×0.00330 ≈ 0.0019。
        assert initial_sleep_pressure(7.0, 24.0) < 3e-3

    def test_back_calculated_value_is_near_wake_threshold(self):
        # 反推值与 07:00 醒来阈值同量级（模型自洽：入睡在 H 穿越时刻）。
        value = initial_sleep_pressure(7.0, 8.0)
        assert 0.0 < value < 0.2
        assert value > wake_threshold(7.0)

    def test_late_chronotype_back_calculation_shifts(self):
        assert initial_sleep_pressure(8.0, 8.0, 5.0) == pytest.approx(
            initial_sleep_pressure(7.0, 8.0), rel=1e-12)

    def test_negative_slept_hours_rejected(self):
        with pytest.raises(ValueError):
            initial_sleep_pressure(7.0, -0.5)


class TestSleepNeedValidation:
    """睡眠需要量允许区间 [7, 9] 的校验。"""

    @pytest.mark.parametrize("need", [7.0, 7.5, 8.0, 9.0])
    def test_valid_need_returned(self, need):
        assert validate_sleep_need_hours(need) == need

    @pytest.mark.parametrize("need", [6.9, 9.1, 0.0, -1.0, float("nan")])
    def test_invalid_need_rejected(self, need):
        with pytest.raises(ValueError):
            validate_sleep_need_hours(need)


class TestSleepDeficitAccount:
    """睡眠不足账的累积、恢复与 [0, 24] 钳制。"""

    def test_default_accumulation_rate(self):
        # 默认 7.5h 需要量：每小时累积 7.5/16.5 = 0.454545...（约 0.45）。
        assert deficit_after_awake(0.0, 1.0) == pytest.approx(7.5 / 16.5, rel=1e-9)
        assert deficit_after_awake(0.0, 1.0) == pytest.approx(0.4545455, rel=1e-6)

    def test_need_boundaries_change_rate(self):
        assert deficit_after_awake(0.0, 1.0, 7.0) == pytest.approx(7.0 / 17.0, rel=1e-9)
        assert deficit_after_awake(0.0, 1.0, 9.0) == pytest.approx(9.0 / 15.0, rel=1e-9)

    def test_full_day_awake_accumulation(self):
        assert deficit_after_awake(0.0, 24.0) == pytest.approx(24.0 * 7.5 / 16.5, rel=1e-9)

    def test_cap_at_24(self):
        assert deficit_after_awake(23.9, 1.0) == 24.0
        assert deficit_after_awake(0.0, 100.0) == 24.0

    def test_negative_input_deficit_clamped_to_range(self):
        # 账本钳制同时覆盖两端：负输入累积后仍为负则落 0，翻正则原值通过。
        assert deficit_after_awake(-1.0, 1.0) == 0.0
        assert deficit_after_awake(-0.2, 1.0) == pytest.approx(7.5 / 16.5 - 0.2, rel=1e-9)

    def test_sleep_recovers_one_hour_per_hour(self):
        assert deficit_after_sleep(10.0, 4.0) == pytest.approx(6.0, rel=1e-9)
        assert deficit_after_sleep(24.0, 8.0) == pytest.approx(16.0, rel=1e-9)

    def test_floor_at_zero(self):
        assert deficit_after_sleep(0.5, 1.0) == 0.0
        assert deficit_after_sleep(0.0, 8.0) == 0.0

    @pytest.mark.parametrize("hours", [-0.5, -1e-9])
    def test_negative_duration_rejected(self, hours):
        with pytest.raises(ValueError):
            deficit_after_awake(0.0, hours)
        with pytest.raises(ValueError):
            deficit_after_sleep(0.0, hours)

    def test_invalid_need_rejected_in_accumulation(self):
        with pytest.raises(ValueError):
            deficit_after_awake(0.0, 1.0, 9.5)

    def test_balanced_week_returns_to_zero(self):
        # 平衡日（清醒 16.5h + 睡眠 7.5h）账目净零：连跑七天后回到 0。
        deficit = 0.0
        for _ in range(7):
            deficit = deficit_after_awake(deficit, 16.5)
            deficit = deficit_after_sleep(deficit, 7.5)
        assert deficit == pytest.approx(0.0, abs=1e-9)

    def test_chronic_short_sleep_accumulates(self):
        # 每天只睡 5h（清醒 19h）：每日净增 19·rate − 5 ≈ 3.636h。日间峰值
        # +8.64h 使上限从第六天起绑定（18.18+8.64>24），此处只跑不触顶的五天。
        deficit = 0.0
        for _ in range(5):
            deficit = deficit_after_awake(deficit, 19.0)
            deficit = deficit_after_sleep(deficit, 5.0)
        assert deficit == pytest.approx(5 * (19.0 * 7.5 / 16.5 - 5.0), rel=1e-9)


class TestEfficiencyCurves:
    """效率曲线节点、插值与两端外推。"""

    @pytest.mark.parametrize("deficit,expected", [
        (0.0, 1.0), (8.0, 1.0), (12.0, 0.925), (16.0, 0.85), (20.0, 0.75), (24.0, 0.65),
    ])
    def test_work_efficiency_nodes_and_midpoints(self, deficit, expected):
        assert work_efficiency(deficit) == pytest.approx(expected, rel=1e-9)

    @pytest.mark.parametrize("deficit,expected", [
        (-3.0, 1.0), (100.0, 0.65),
    ])
    def test_work_efficiency_boundary_extrapolation(self, deficit, expected):
        assert work_efficiency(deficit) == pytest.approx(expected, rel=1e-9)

    @pytest.mark.parametrize("deficit,expected", [
        (0.0, 0.90), (8.0, 0.90), (12.0, 0.875), (16.0, 0.85), (20.0, 0.825), (24.0, 0.80),
    ])
    def test_food_utilization_nodes_and_midpoints(self, deficit, expected):
        assert food_utilization_efficiency(deficit) == pytest.approx(expected, rel=1e-9)

    @pytest.mark.parametrize("deficit,expected", [
        (-1.0, 0.90), (30.0, 0.80),
    ])
    def test_food_utilization_boundary_extrapolation(self, deficit, expected):
        assert food_utilization_efficiency(deficit) == pytest.approx(expected, rel=1e-9)

    def test_frozen_curve_constants(self):
        assert [p.input_hours for p in WORK_EFFICIENCY_CURVE] == [0.0, 8.0, 16.0, 24.0]
        assert [p.output_factor for p in WORK_EFFICIENCY_CURVE] == [1.0, 1.0, 0.85, 0.65]
        assert [p.input_hours for p in FOOD_UTILIZATION_CURVE] == [0.0, 8.0, 16.0, 24.0]
        assert [p.output_factor for p in FOOD_UTILIZATION_CURVE] == [0.90, 0.90, 0.85, 0.80]

    def test_interpolate_two_point_curve(self):
        curve = (point(0.0, 1.0), point(10.0, 0.5))
        assert interpolate_efficiency(curve, 5.0) == pytest.approx(0.75, rel=1e-9)
        assert interpolate_efficiency(curve, -1.0) == 1.0
        assert interpolate_efficiency(curve, 11.0) == 0.5


class TestCurveValidation:
    """非法曲线节点必须报错。"""

    def test_empty_curve_rejected(self):
        with pytest.raises(ValueError):
            interpolate_efficiency((), 1.0)
        with pytest.raises(ValueError):
            validate_efficiency_curve(())

    def test_duplicate_inputs_rejected(self):
        curve = (point(0.0, 1.0), point(0.0, 0.9))
        with pytest.raises(ValueError):
            validate_efficiency_curve(curve)
        with pytest.raises(ValueError):
            interpolate_efficiency(curve, 0.0)

    def test_decreasing_inputs_rejected(self):
        curve = (point(8.0, 1.0), point(4.0, 0.9))
        with pytest.raises(ValueError):
            validate_efficiency_curve(curve)

    def test_non_finite_values_rejected_at_construction(self):
        # nan/inf 输出与 inf 输入在节点构造期即报错（ValidationError 属 ValueError）。
        with pytest.raises(ValueError):
            point(0.0, float("nan"))
        with pytest.raises(ValueError):
            point(0.0, float("inf"))
        with pytest.raises(ValueError):
            point(float("inf"), 1.0)
        with pytest.raises(ValueError):
            point(float("nan"), 1.0)

    def test_negative_input_rejected_at_construction(self):
        with pytest.raises(ValueError):
            point(-0.1, 1.0)

    def test_point_is_frozen_and_closed(self):
        node = point(0.0, 1.0)
        with pytest.raises(ValueError):
            node.input_hours = 5.0
        with pytest.raises(ValueError):
            EfficiencyCurvePoint(input_hours=1.0, output_factor=1.0, extra="x")

    def test_valid_curve_passes_validation(self):
        validate_efficiency_curve(WORK_EFFICIENCY_CURVE)
        validate_efficiency_curve(FOOD_UTILIZATION_CURVE)


# ---------------------------------------------------------------------------
# 作息初值与困意跨档预测（docs/dev/SPEC-time-and-energy.md 第八章「作息初值」「节律」）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def body_config():
    """读取正式场景组装后的身体配置。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


def raw_initial(hour: float, need: float, midsleep: float) -> float:
    """测试侧独立推导：睡满后于惯常起床时刻醒来，此后一直清醒到起点的睡眠压力。"""
    wake = (midsleep + need / 2.0) % 24.0
    awake = (hour - wake) % 24.0
    at_wake = raw_h(wake - need, midsleep) * math.exp(-need / 4.2)
    return 1.0 - (1.0 - at_wake) * math.exp(-awake / 18.2)


class TestInitialBodyFromUsualWake:
    """起点身体按惯常起床时刻反推，不假定起点前刚睡满。"""

    def test_pressure_counts_awake_hours_since_the_most_recent_usual_wake(self, body_config):
        # 默认作息惯常 07:45 起床：00:00 起点已清醒 16.25 小时。
        body = initial_body(body_config, 0.0, 7.5, 4.0)
        at_wake = raw_h(0.25) * math.exp(-7.5 / 4.2)
        assert body.sleep_pressure == pytest.approx(1 - (1 - at_wake) * math.exp(-16.25 / 18.2), rel=1e-9)
        assert body.sleep_pressure == pytest.approx(raw_initial(0.0, 7.5, 4.0), rel=1e-9)
        assert body.energy_kcal == body_config.initial_energy_kcal

    def test_usual_wake_hour_wraps_modulo_24(self, body_config):
        # 时型 23.0、需要 9 小时：23 + 4.5 = 27.5，取模后 03:30 起床。
        late = initial_body(body_config, 2.0, 9.0, 23.0)
        early = initial_body(body_config, 4.0, 9.0, 23.0)
        assert late.sleep_pressure == pytest.approx(raw_initial(2.0, 9.0, 23.0), rel=1e-9)
        assert early.sleep_pressure == pytest.approx(raw_initial(4.0, 9.0, 23.0), rel=1e-9)
        assert early.sleep_pressure == pytest.approx(pressure_after_awake(initial_sleep_pressure(3.5, 9.0, 23.0), 0.5))
        assert late.sleep_pressure > 0.5 > early.sleep_pressure

    def test_start_inside_usual_sleep_is_drowsy_and_after_waking_is_alert(self, body_config):
        # 默认作息的惯常睡眠时段为 00:15–07:45：午夜起点困倦，08:00 起点清醒。
        midnight = initial_body(body_config, 0.0)
        morning = initial_body(body_config, 8.0)
        assert felt.sleepiness_level(sleepiness(midnight.sleep_pressure, 0.0)) in {"困", "很困", "撑不住"}
        assert felt.sleepiness_level(sleepiness(morning.sleep_pressure, 8.0)) == "清醒"

    def test_lianqiu_midnight_start_first_observation_is_at_least_sleepy(self, tmp_path: Path):
        """练秋湖作息（中点 04:45、睡 8 小时）的 00:00 起点：首个观察的困意为「困」或以上；不读就寝窗口。"""
        def lianqiu(document: dict) -> None:
            document["agents"][0].update(chronotype_midsleep=4.75, sleep_need_hours=8.0,
                                         bedtime_window=[23.5, 24.5])

        engine = _custom_engine("white-collar-12-weeks.json", lianqiu, tmp_path, run_id="lianqiu-start")
        try:
            assert engine.scenario.start_at.hour == 0 and engine.scenario.start_at.minute == 0
            decision = context(engine, "first-look")
            engine.begin_decision_round([decision])
            observed = engine.observe(decision)
            assert observed["body"]["levels"]["sleepiness"] in {"困", "很困", "撑不住"}
            assert observed["environment_layer"]["body"]["sleepiness"] == observed["body"]["levels"]["sleepiness"]
            config = engine.scenario.agents[0]
            start = int(engine.scenario.start_at.timestamp())
            other = config.model_copy(update={"bedtime_window": (21.0, 22.0)})
            assert _initial_agent(other, engine.scenario, "agent:1", start, {}).body == (
                _initial_agent(config, engine.scenario, "agent:1", start, {}).body)
        finally:
            engine.close()


def _levels_after(body: BodyState, config, clock: BoundaryClock, seconds: int) -> dict:
    """清醒等待 seconds 秒后按真实共同结算得到的体感分级（与观察同一投影）。"""
    after = settle_shared_body(body, ((ActivityKind.WAIT, ActivityFactors(1.0)),), seconds, config).after
    person = AgentState(agent_id=1, name="作息", location_id="home", balance_account_id="agent:1",
                        body=after, body_settled_at=0, next_decision_at=0)
    return projection.body_projection(person, (clock.hour_of_day + seconds / 3600) % 24, config)["levels"]


class TestSleepinessCrossingPrediction:
    """困意跨档从当前压力扫描：候选恰为真实结算首次变档的整秒，升档、降档都有。"""

    @pytest.mark.parametrize(("hour", "pressure", "midsleep", "expected"), [
        (20.0, 0.45, 4.0, ("清醒", "有点乏")),
        (7.0, 0.6, 4.0, ("撑不住", "很困")),
        (0.0, 0.13, 4.75, ("清醒", "有点乏")),
    ])
    def test_prediction_from_current_pressure_is_not_later_than_actual(self, body_config, hour, pressure,
                                                                        midsleep, expected):
        quiet = body_config.model_copy(update={"entertainment_need_per_awake_hour": 0})
        body = BodyState(energy_kcal=2400, sleep_pressure=pressure, chronotype_midsleep=midsleep,
                         entertainment_need=0, social_need=1.0)
        clock = BoundaryClock(hour)
        predicted = next_shared_body_boundary_seconds(
            body, ((ActivityKind.WAIT, ActivityFactors(1.0)),), quiet, clock)
        before = _levels_after(body, quiet, clock, 0)
        assert _levels_after(body, quiet, clock, predicted - 1) == before
        after = _levels_after(body, quiet, clock, predicted)
        assert (before["sleepiness"], after["sleepiness"]) == expected
        if hour == 0.0:
            # 本机运行的症状：旧近似在 00:00 预测 08:44 才「有点乏」，从当前压力扫描得到凌晨的实际时刻。
            assert predicted < 5 * 3600

