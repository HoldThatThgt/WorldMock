"""@brief 体感分级与情绪：数值锚点、昼夜/餐时项、分级边界、账本、半衰期与滞回。"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from worldmock.life.felt import (
    APPETITE_ANCHOR_KCAL_DEFAULT,
    MOOD_DISCOMFORT_TRIGGER_HOURS,
    MOOD_PULSES,
    MealWindows,
    SocialExposure,
    appetite_circadian_multiplier,
    appetite_level,
    apply_mood_pulse,
    apply_social_need_ledger,
    changed_felt_levels,
    compute_appetite,
    decay_mood_valence,
    discomfort_pulse,
    entertainment_need_level,
    meal_window_bonus,
    mood_halflife_minutes,
    mood_level,
    normalize_neuroticism,
    sleepiness_level,
    social_need_level,
    summarize_felt_levels,
)


def circadian_reference(time_hour: float) -> float:
    """测试内独立复算的昼夜因子（同公式重写，交叉核对实现）。"""
    return 1.0 + 0.17 * math.cos(2.0 * math.pi * (time_hour - 20.0) / 24.0)


class TestAppetiteFormula:
    """饥饿体感公式的数值锚点（先手算后验证）。"""

    def test_anchor_800kcal_at_20h_is_hungry(self) -> None:
        # 手算：base=(1200-800)/(1200-400)=0.5；20:00 因子=1.17；0.5×1.17=0.585。
        appetite = compute_appetite(800.0, 20.0)
        assert appetite == pytest.approx(0.585)
        assert appetite_level(appetite) == "饿"

    def test_anchor_400kcal_at_20h_is_unbearable(self) -> None:
        # 手算：base=1.0；1.0×1.17=1.17 ≥ 0.85。
        appetite = compute_appetite(400.0, 20.0)
        assert appetite == pytest.approx(1.17)
        assert appetite_level(appetite) == "饿得难受"

    def test_circadian_peak_trough_and_midpoint(self) -> None:
        # Scheer 2013：20:00 峰 1.17、08:00 谷 0.83、正午前中点约 1.0。
        assert appetite_circadian_multiplier(20.0) == pytest.approx(1.17)
        assert appetite_circadian_multiplier(8.0) == pytest.approx(0.83)
        assert appetite_circadian_multiplier(14.0) == pytest.approx(1.0)
        peak = appetite_circadian_multiplier(20.0)
        trough = appetite_circadian_multiplier(8.0)
        assert (peak - 1.0) == pytest.approx(0.17)
        assert (1.0 - trough) == pytest.approx(0.17)

    def test_circadian_matches_reference_over_day(self) -> None:
        for hour in [0.0, 3.5, 8.0, 11.5, 14.0, 17.25, 20.0, 23.75]:
            assert appetite_circadian_multiplier(hour) == pytest.approx(
                circadian_reference(hour)
            )

    def test_800kcal_trough_still_hungry(self) -> None:
        # 手算：0.5×0.83=0.415，仍在 [0.35, 0.6)。
        appetite = compute_appetite(800.0, 8.0)
        assert appetite == pytest.approx(0.415)
        assert appetite_level(appetite) == "饿"

    def test_satiated_above_anchor_is_zero(self) -> None:
        for hour in [8.0, 14.0, 20.0]:
            assert compute_appetite(1500.0, hour) == pytest.approx(0.0)
            assert appetite_level(compute_appetite(1500.0, hour)) == "不饿"

    def test_meal_window_inside_and_outside(self) -> None:
        windows = MealWindows(lunch=(11.5, 13.5), dinner=(18.0, 20.0))
        # 手算：12:00 因子=1+0.17×cos(−2π/3)=0.915；0.5×0.915=0.4575；+0.15=0.6075。
        in_window = compute_appetite(800.0, 12.0, windows)
        assert in_window == pytest.approx(0.6075)
        assert appetite_level(in_window) == "很饿"
        assert meal_window_bonus(12.0, windows) == pytest.approx(0.15)
        # 14:00 在窗口外：餐时项为 0。
        out_window = compute_appetite(800.0, 14.0, windows)
        assert out_window == pytest.approx(0.5)
        assert meal_window_bonus(14.0, windows) == 0.0
        # 晚餐窗口 19:00 命中：因子=1+0.17×cos(−π/12)≈1.1644。
        dinner = compute_appetite(800.0, 19.0, windows)
        assert dinner == pytest.approx(0.5 * circadian_reference(19.0) + 0.15)

    def test_meal_window_half_open_boundaries(self) -> None:
        windows = MealWindows(lunch=(11.5, 13.5))
        assert meal_window_bonus(11.5, windows) == pytest.approx(0.15)
        assert meal_window_bonus(13.5, windows) == 0.0
        assert meal_window_bonus(11.4999, windows) == 0.0

    def test_meal_bonus_at_zero_base_hits_first_threshold(self) -> None:
        # 吃饱（base=0）但在午餐窗口：0+0.15 恰在首档下界 → 有点想吃。
        appetite = compute_appetite(1200.0, 12.0, MealWindows(lunch=(11.5, 13.5)))
        assert appetite == pytest.approx(0.15)
        assert appetite_level(appetite) == "有点想吃"

    def test_default_anchor_is_1200(self) -> None:
        assert APPETITE_ANCHOR_KCAL_DEFAULT == 1200.0
        assert compute_appetite(800.0, 20.0) == pytest.approx(
            compute_appetite(800.0, 20.0, None, 1200.0)
        )

    def test_anchor_custom_value(self) -> None:
        # anchor=1000：base=(1000-600)/600≈0.6667；20:00 → 0.6667×1.17≈0.78。
        appetite = compute_appetite(600.0, 20.0, None, 1000.0)
        assert appetite == pytest.approx((1000.0 - 600.0) / 600.0 * 1.17)
        assert appetite_level(appetite) == "很饿"

    def test_appetite_input_validation(self) -> None:
        with pytest.raises(ValueError, match="anchor"):
            compute_appetite(800.0, 20.0, None, 400.0)
        with pytest.raises(ValueError, match="anchor"):
            compute_appetite(800.0, 20.0, None, 300.0)
        with pytest.raises(ValueError, match="energy"):
            compute_appetite(-1.0, 20.0)
        with pytest.raises(ValueError, match="time_hour"):
            compute_appetite(800.0, 24.0)
        with pytest.raises(ValueError, match="time_hour"):
            appetite_circadian_multiplier(-0.5)


class TestMealWindowsValidation:
    """人物卡片餐时窗口的模型校验。"""

    def test_invalid_window_order(self) -> None:
        with pytest.raises(ValidationError):
            MealWindows(lunch=(13.5, 11.5))

    def test_invalid_window_range(self) -> None:
        with pytest.raises(ValidationError):
            MealWindows(dinner=(18.0, 24.5))

    def test_zero_length_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MealWindows(lunch=(12.0, 12.0))

    def test_overlap_rejected_and_touching_ok(self) -> None:
        with pytest.raises(ValidationError):
            MealWindows(lunch=(11.0, 14.0), dinner=(13.0, 15.0))
        touching = MealWindows(lunch=(11.0, 13.0), dinner=(13.0, 15.0))
        assert touching.lunch == (11.0, 13.0)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MealWindows(breakfast=(7.0, 8.0))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("appetite", "expected"),
    [
        (0.0, "不饿"),
        (0.14, "不饿"),
        (0.15, "有点想吃"),
        (0.349, "有点想吃"),
        (0.35, "饿"),
        (0.599, "饿"),
        (0.6, "很饿"),
        (0.849, "很饿"),
        (0.85, "饿得难受"),
        (1.17, "饿得难受"),
        (1.32, "饿得难受"),
    ],
)
def test_appetite_level_boundaries(appetite: float, expected: str) -> None:
    assert appetite_level(appetite) == expected


@pytest.mark.parametrize(
    ("sleepiness", "expected"),
    [
        (0.0, "清醒"),
        (0.49, "清醒"),
        (0.5, "有点乏"),
        (0.74, "有点乏"),
        (0.75, "困"),
        (0.89, "困"),
        (0.9, "很困"),
        (0.99, "很困"),
        (1.0, "撑不住"),
        (1.2, "撑不住"),
    ],
)
def test_sleepiness_level_boundaries(sleepiness: float, expected: str) -> None:
    assert sleepiness_level(sleepiness) == expected


@pytest.mark.parametrize(
    ("need", "expected"),
    [
        (0.0, "不闷"),
        (0.29, "不闷"),
        (0.3, "有点想放松"),
        (0.59, "有点想放松"),
        (0.6, "很想放松"),
        (1.0, "很想放松"),
    ],
)
def test_entertainment_level_boundaries(need: float, expected: str) -> None:
    assert entertainment_need_level(need) == expected


@pytest.mark.parametrize(
    ("need", "expected"),
    [
        (0.0, "不想找人"),
        (0.29, "不想找人"),
        (0.3, "想和人说说话"),
        (0.59, "想和人说说话"),
        (0.6, "有点孤单"),
        (1.0, "有点孤单"),
    ],
)
def test_social_level_boundaries(need: float, expected: str) -> None:
    assert social_need_level(need) == expected


@pytest.mark.parametrize(
    ("valence", "expected"),
    [
        (-1.0, "很烦"),
        (-0.41, "很烦"),
        (-0.4, "有点烦"),
        (-0.16, "有点烦"),
        (-0.15, "平静"),
        (0.0, "平静"),
        (0.149, "平静"),
        (0.15, "挺好"),
        (0.39, "挺好"),
        (0.4, "很开心"),
        (1.0, "很开心"),
    ],
)
def test_mood_level_boundaries(valence: float, expected: str) -> None:
    assert mood_level(valence) == expected


class TestSocialLedger:
    """社交需要账本速率（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""

    def test_awake_growth(self) -> None:
        # 0.5 + 0.04×10 = 0.9。
        result = apply_social_need_ledger(0.5, SocialExposure(awake_hours=10.0))
        assert result == pytest.approx(0.9)

    def test_conversation_relief_floors_at_zero(self) -> None:
        # 0.5 − 0.5×1 = 0（下限零）。
        exposure = SocialExposure(awake_hours=0.0, conversation_hours=1.0)
        assert apply_social_need_ledger(0.5, exposure) == pytest.approx(0.0)

    def test_call_relief(self) -> None:
        # 0.6 − 0.4×0.5 = 0.4。
        exposure = SocialExposure(call_hours=0.5)
        assert apply_social_need_ledger(0.6, exposure) == pytest.approx(0.4)

    def test_shared_meal_relief(self) -> None:
        # 0.5 − 0.3×1 = 0.2。
        exposure = SocialExposure(shared_meal_hours=1.0)
        assert apply_social_need_ledger(0.5, exposure) == pytest.approx(0.2)

    def test_message_relief(self) -> None:
        # 0.5 − 0.05×4 = 0.3。
        exposure = SocialExposure(messages_read=4)
        assert apply_social_need_ledger(0.5, exposure) == pytest.approx(0.3)

    def test_combined_terms(self) -> None:
        # 0.6 + 0.04×8 − 0.5×1 − 0.05×2 = 0.32。
        exposure = SocialExposure(
            awake_hours=8.0, conversation_hours=1.0, messages_read=2
        )
        assert apply_social_need_ledger(0.6, exposure) == pytest.approx(0.32)

    def test_ceiling_clamp(self) -> None:
        # 0.95 + 0.4 = 1.35 → 上限 1。
        exposure = SocialExposure(awake_hours=10.0)
        assert apply_social_need_ledger(0.95, exposure) == pytest.approx(1.0)

    def test_input_validation(self) -> None:
        with pytest.raises(ValueError, match="social_need"):
            apply_social_need_ledger(1.5, SocialExposure(awake_hours=1.0))
        with pytest.raises(ValidationError):
            SocialExposure(awake_hours=-1.0)
        with pytest.raises(ValidationError):
            SocialExposure(awake_hours=1.0, messages_read=-1)


class TestMood:
    """情绪脉冲、N_norm 调制、半衰期与多脉冲叠加（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""

    def test_pulse_table_values(self) -> None:
        assert MOOD_PULSES == {
            "work_review_passed": 0.3,
            "work_review_returned": -0.3,
            "task_deadline_missed": -0.4,
            "message_received": 0.1,
            "discomfort_hourly": -0.1,
            "goal_progressed": 0.05,
            "service_refused": -0.05,
        }

    def test_normalize_neuroticism(self) -> None:
        # 1-7 量表：4 → 0；1 → −1；7 → +1；越界夹断。
        assert normalize_neuroticism(4.0, 1.0, 7.0) == pytest.approx(0.0)
        assert normalize_neuroticism(1.0, 1.0, 7.0) == pytest.approx(-1.0)
        assert normalize_neuroticism(7.0, 1.0, 7.0) == pytest.approx(1.0)
        assert normalize_neuroticism(0.0, 1.0, 7.0) == pytest.approx(-1.0)
        assert normalize_neuroticism(9.0, 1.0, 7.0) == pytest.approx(1.0)
        with pytest.raises(ValueError, match="scale"):
            normalize_neuroticism(4.0, 7.0, 7.0)

    def test_halflife_modulation(self) -> None:
        # 45×(1+0.3N)：N=0 → 45；N=1 → 58.5；N=−1 → 31.5；N=0.5 → 51.75。
        assert mood_halflife_minutes(0.0) == pytest.approx(45.0)
        assert mood_halflife_minutes(1.0) == pytest.approx(58.5)
        assert mood_halflife_minutes(-1.0) == pytest.approx(31.5)
        assert mood_halflife_minutes(0.5) == pytest.approx(51.75)
        with pytest.raises(ValueError, match="neuroticism"):
            mood_halflife_minutes(1.5)

    def test_pulse_amplitude_modulation(self) -> None:
        # 0.3×(1+0.5N)：N=0 → 0.3；N=1 → 0.45；N=−1 → 0.15。
        assert apply_mood_pulse(0.0, 0.3, 0.0) == pytest.approx(0.3)
        assert apply_mood_pulse(0.0, 0.3, 1.0) == pytest.approx(0.45)
        assert apply_mood_pulse(0.0, 0.3, -1.0) == pytest.approx(0.15)

    def test_pulse_clamp_to_unit_range(self) -> None:
        assert apply_mood_pulse(0.8, 0.3, 1.0) == pytest.approx(1.0)
        assert apply_mood_pulse(-0.8, -0.3, 0.0) == pytest.approx(-1.0)

    def test_decay_halves_at_halflife(self) -> None:
        # N=0：45 分钟减半，90 分钟四分之一，135 分钟八分之一。
        assert decay_mood_valence(0.3, 45.0, 0.0) == pytest.approx(0.15)
        assert decay_mood_valence(0.3, 90.0, 0.0) == pytest.approx(0.075)
        assert decay_mood_valence(0.3, 135.0, 0.0) == pytest.approx(0.0375)

    def test_decay_neuroticism_changes_speed(self) -> None:
        # N=−1 半衰期 31.5 分钟：45 分钟后 0.3×0.5^(45/31.5)。
        fast = decay_mood_valence(0.3, 45.0, -1.0)
        assert fast == pytest.approx(0.3 * 0.5 ** (45.0 / 31.5))
        # N=+1 半衰期 58.5 分钟：高神经质情绪消退更慢。
        slow = decay_mood_valence(0.3, 45.0, 1.0)
        assert slow == pytest.approx(0.3 * 0.5 ** (45.0 / 58.5))
        assert slow > fast

    def test_negative_valence_decays_toward_zero(self) -> None:
        assert decay_mood_valence(-0.4, 45.0, 0.0) == pytest.approx(-0.2)
        assert mood_level(decay_mood_valence(-0.4, 45.0, 0.0)) == "有点烦"

    def test_multi_pulse_superposition_and_decay(self) -> None:
        # 0 → +0.3 → 衰减 45min → 0.15 → +0.1 → 0.25 → 衰减 45min → 0.125。
        valence = apply_mood_pulse(0.0, MOOD_PULSES["work_review_passed"], 0.0)
        assert valence == pytest.approx(0.3)
        valence = decay_mood_valence(valence, 45.0, 0.0)
        assert valence == pytest.approx(0.15)
        valence = apply_mood_pulse(valence, MOOD_PULSES["message_received"], 0.0)
        assert valence == pytest.approx(0.25)
        valence = decay_mood_valence(valence, 45.0, 0.0)
        assert valence == pytest.approx(0.125)
        assert mood_level(valence) == "平静"

    def test_multi_pulse_with_neuroticism(self) -> None:
        # N=1：任务到期未交 −0.4×1.5=−0.6；衰减 58.5 分钟后 −0.3。
        valence = apply_mood_pulse(0.0, MOOD_PULSES["task_deadline_missed"], 1.0)
        assert valence == pytest.approx(-0.6)
        valence = decay_mood_valence(valence, 58.5, 1.0)
        assert valence == pytest.approx(-0.3)
        assert mood_level(valence) == "有点烦"

    def test_discomfort_pulse_threshold(self) -> None:
        # 不足 2 小时无脉冲；≥ 2 小时按 −0.1/小时。
        assert MOOD_DISCOMFORT_TRIGGER_HOURS == 2.0
        assert discomfort_pulse(1.9) == 0.0
        assert discomfort_pulse(2.0) == pytest.approx(-0.2)
        assert discomfort_pulse(3.0) == pytest.approx(-0.3)
        assert apply_mood_pulse(-0.1, discomfort_pulse(2.0), 0.0) == pytest.approx(-0.3)
        with pytest.raises(ValueError, match="continuous_hours"):
            discomfort_pulse(-1.0)

    def test_mood_input_validation(self) -> None:
        with pytest.raises(ValueError, match="mood_valence"):
            apply_mood_pulse(1.2, 0.3, 0.0)
        with pytest.raises(ValueError, match="neuroticism"):
            apply_mood_pulse(0.0, 0.3, -2.0)
        with pytest.raises(ValueError, match="elapsed_minutes"):
            decay_mood_valence(0.3, -5.0, 0.0)


class TestSummarizeAndHysteresis:
    """分级汇总与滞回判定（决策参照，非结算参照，docs/dev/SPEC-life-runtime.md「滞回规则」）。"""

    def test_summarize_felt_levels(self) -> None:
        summary = summarize_felt_levels(0.585, 0.8, 0.4, 0.65, 0.2)
        assert summary == {
            "appetite": "饿",
            "sleepiness": "困",
            "entertainment": "有点想放松",
            "social": "有点孤单",
            "mood": "挺好",
        }

    def test_no_change_when_decision_reference_matches(self) -> None:
        # 结算边界曾重算为"很饿"又回到"饿"：参照是决策时的"饿"，非结算中间值。
        at_decision = {"appetite": "饿"}
        settled_middle = {"appetite": "很饿"}  # 仅存在于结算，不进入参照
        current = {"appetite": "饿"}
        assert changed_felt_levels(settled_middle, at_decision) == ["appetite"]
        assert changed_felt_levels(current, at_decision) == []
        assert changed_felt_levels(current, {"appetite": "饿"}) == []

    def test_change_detected_against_decision_reference(self) -> None:
        current = {"appetite": "饿"}
        at_decision = {"appetite": "很饿"}
        assert changed_felt_levels(current, at_decision) == ["appetite"]

    def test_multiple_keys_sorted(self) -> None:
        current = {"appetite": "饿", "mood": "挺好", "sleepiness": "困"}
        at_decision = {"appetite": "很饿", "mood": "挺好", "sleepiness": "困"}
        assert changed_felt_levels(current, at_decision) == ["appetite"]

    def test_new_and_missing_keys_count_as_change(self) -> None:
        assert changed_felt_levels({"social": "有点孤单"}, {}) == ["social"]
        assert changed_felt_levels({}, {"social": "不想找人"}) == ["social"]

    def test_full_flow_settlement_wiggle_does_not_wake(self) -> None:
        # 决策时快照 → 结算中途波动 → 当前回落：以决策快照为参照无变化。
        at_decision = summarize_felt_levels(0.585, 0.8, 0.4, 0.2, 0.1)
        mid_settlement = summarize_felt_levels(0.61, 0.8, 0.4, 0.2, 0.1)
        current = summarize_felt_levels(0.59, 0.8, 0.4, 0.2, 0.1)
        assert changed_felt_levels(mid_settlement, at_decision) == ["appetite"]
        assert changed_felt_levels(current, at_decision) == []
        # 真实越档（0.59 → 0.9）才构成唤醒条件。
        crossed = summarize_felt_levels(0.9, 0.8, 0.4, 0.2, 0.1)
        assert changed_felt_levels(crossed, at_decision) == ["appetite"]
