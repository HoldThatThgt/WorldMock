"""身体变化、餐时体感与睡眠参数的实际边界。"""

import math
import tomllib

import pytest
from pydantic import ValidationError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.unit.life.test_environment_body import body_inputs
from worldmock.input_models import PersonaConfig
from worldmock.life.body import (
    ActivityFactors, BoundaryClock, apply_discomfort_pulse, apply_relaxation_change, initial_body, last_flows,
    next_shared_body_boundary_seconds, settle_shared_body,
)
from worldmock.life.body_config import assemble_body_config
from worldmock.life.felt import (
    MealWindows, appetite_level, changed_felt_levels, compute_appetite, meal_window_bonus, sleepiness_level,
)
from worldmock.life.models import ActivityKind
from worldmock.life.sleep import (
    EfficiencyCurvePoint, SleepinessGrade, initial_sleep_pressure, interpolate_efficiency,
    sleepiness_grade, validate_efficiency_curve,
)


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf])
def test_nonfinite_relaxation_change_rejects_without_state_or_resource_effect(body_inputs, caplog, amount):
    _, body = body_inputs
    before = body.model_dump()
    with pytest.raises(ValueError, match="relaxation change must be finite"):
        apply_relaxation_change(body, amount)
    assert body.model_dump() == before and last_flows() == ()
    assert "Relaxation change must be finite" in caplog.text


@pytest.mark.parametrize("hours", [-0.001, math.nan, math.inf, -math.inf])
def test_invalid_discomfort_duration_cannot_create_a_relaxation_pulse(body_inputs, caplog, hours):
    _, body = body_inputs
    before = body.model_dump()
    with pytest.raises(ValueError, match="discomfort hours must be finite and nonnegative"):
        apply_discomfort_pulse(body, hours)
    assert body.model_dump() == before and last_flows() == ()
    assert "Discomfort hours must be finite and nonnegative" in caplog.text


@pytest.mark.parametrize("name", ["lunch", "dinner"])
@pytest.mark.parametrize("window", [(-0.01, 1), (1, 1), (2, 1), (23, 24.01)])
def test_meal_windows_reject_empty_backward_or_out_of_day_intervals(name, window, caplog):
    with pytest.raises(ValidationError, match=f"{name} window must satisfy"):
        MealWindows(**{name: window})
    assert "Felt input is invalid" in caplog.text


@pytest.mark.parametrize("dinner", [(12.5, 14), (12, 13), (11, 14)])
def test_overlapping_meal_windows_cannot_double_count_a_meal_bonus(dinner, caplog):
    with pytest.raises(ValidationError, match="dinner window must not overlap lunch window"):
        MealWindows(lunch=(12, 13), dinner=dinner)
    assert "Felt input is invalid" in caplog.text


@pytest.mark.parametrize("hour,expected", [
    (11.999, 0), (12, 0.15), (12.5, 0.15), (13, 0.15), (14, 0), (23.999, 0),
])
def test_adjacent_meal_windows_use_half_open_intervals_and_one_bonus(hour, expected):
    windows = MealWindows(lunch=(12, 13), dinner=(13, 14))
    before = windows.model_dump()
    assert meal_window_bonus(hour, windows) == expected
    assert windows.model_dump() == before


def test_dinner_without_a_lunch_window_still_provides_its_actual_bonus():
    windows = MealWindows(dinner=(18, 19))
    assert meal_window_bonus(18, windows) == 0.15
    assert meal_window_bonus(19, windows) == 0
    assert meal_window_bonus(18, MealWindows()) == 0


@pytest.mark.parametrize("anchor", [400, 399.99])
def test_appetite_requires_an_anchor_above_the_formula_floor(anchor, caplog):
    with pytest.raises(ValueError, match="appetite_anchor_kcal must exceed 400 kcal"):
        compute_appetite(800, 12, appetite_anchor_kcal=anchor)
    assert "Felt input is invalid" in caplog.text


def test_negative_energy_is_rejected_before_an_appetite_is_reported(caplog):
    with pytest.raises(ValueError, match="energy_kcal must be non-negative"):
        compute_appetite(-0.01, 12)
    assert "Felt input is invalid" in caplog.text


@pytest.mark.parametrize("hour", [-0.001, 24, math.nan, math.inf])
def test_appetite_and_meal_bonus_reject_times_outside_the_current_local_day(hour, caplog):
    for read in (lambda: compute_appetite(800, hour), lambda: meal_window_bonus(hour, MealWindows())):
        with pytest.raises(ValueError, match=r"time_hour must be within \[0, 24\)"):
            read()
    assert "Felt input is invalid" in caplog.text


@pytest.mark.parametrize("energy,hour,anchor,windows,expected", [
    (400, 20, 1200, MealWindows(dinner=(20, 21)), 1.32),
    (1600, 20, 1200, MealWindows(dinner=(20, 21)), 0.15),
    (800, 8, 1200, None, 0.415),
    (1000, 20, 1600, None, 0.585),
])
def test_appetite_uses_real_energy_day_phase_and_declared_meal_bonus(energy, hour, anchor, windows, expected):
    assert compute_appetite(energy, hour, windows, anchor) == pytest.approx(expected)


def test_felt_change_compares_the_actual_decision_reference_and_reports_new_or_missing_signals():
    previous = {"appetite": "不饿", "sleepiness": "清醒", "social": "不想找人"}
    current = {"relaxation": "有点想放松", "sleepiness": "清醒", "appetite": "饿"}
    assert changed_felt_levels(current, previous) == ["appetite", "relaxation", "social"]
    assert changed_felt_levels(previous, previous.copy()) == []
    assert previous == {"appetite": "不饿", "sleepiness": "清醒", "social": "不想找人"}
    assert current == {"relaxation": "有点想放松", "sleepiness": "清醒", "appetite": "饿"}


@pytest.mark.parametrize("value,grade,text", [
    (0, SleepinessGrade.AWAKE, "清醒"), (0.79999, SleepinessGrade.AWAKE, "清醒"),
    (0.80, SleepinessGrade.SLIGHTLY_TIRED, "有点乏"), (0.92999, SleepinessGrade.SLIGHTLY_TIRED, "有点乏"),
    (0.93, SleepinessGrade.SLEEPY, "困"), (1.06999, SleepinessGrade.SLEEPY, "困"),
    (1.07, SleepinessGrade.VERY_SLEEPY, "很困"), (1.17999, SleepinessGrade.VERY_SLEEPY, "很困"),
    (1.18, SleepinessGrade.CANNOT_STAY_AWAKE, "撑不住"), (1.2, SleepinessGrade.CANNOT_STAY_AWAKE, "撑不住"),
])
def test_sleepiness_grades_match_the_visible_felt_level_at_each_boundary(value, grade, text):
    assert sleepiness_grade(value) == grade
    assert sleepiness_level(value) == text


def test_negative_slept_duration_cannot_be_used_to_construct_sleep_pressure():
    with pytest.raises(ValueError, match="slept_hours cannot be negative"):
        initial_sleep_pressure(8, -0.01, 4)


@pytest.mark.parametrize("hour,hours,expected", [
    (4, 0, 0.55), (8, 8, 0.61 * math.exp(-8 / 4.2)), (32, 8, 0.61 * math.exp(-8 / 4.2)),
])
def test_initial_sleep_pressure_uses_actual_sleep_duration_and_periodic_day_phase(hour, hours, expected):
    assert initial_sleep_pressure(hour, hours, 4) == pytest.approx(expected)


@pytest.mark.parametrize("hours", [(), (0, 0), (8, 0)])
def test_efficiency_curve_rejects_missing_duplicate_or_descending_input_nodes(hours):
    points = tuple(EfficiencyCurvePoint(input_hours=value, output_factor=0.9) for value in hours)
    before = tuple(point.model_dump() for point in points)
    with pytest.raises(ValueError, match="at least one point|inputs must strictly increase"):
        validate_efficiency_curve(points)
    assert tuple(point.model_dump() for point in points) == before


def test_valid_efficiency_curve_interpolates_actual_nodes_and_keeps_boundary_factors():
    points = tuple(EfficiencyCurvePoint(input_hours=hours, output_factor=factor)
                   for hours, factor in ((0, 0.9), (8, 0.9), (16, 0.85), (24, 0.8)))
    validate_efficiency_curve(points)
    assert interpolate_efficiency(points, 12) == pytest.approx(0.875)
    assert interpolate_efficiency(points, -1) == 0.9
    assert interpolate_efficiency(points, 25) == 0.8


def _mass_inputs(mass_kg, hour):
    """夹具人设只换体重，经真实人设模型装配身体结算视图与给定钟点的初始身体。"""
    raw = tomllib.loads((ENVIRONMENT_ROOT / "persona.toml").read_text(encoding="utf-8"))
    persona = PersonaConfig.model_validate({**raw, "body": {**raw["body"], "mass_kg": mass_kg}})
    config = assemble_body_config(persona)
    body = initial_body(config, hour, persona, 0.06)
    last_flows()
    return config, body


@pytest.mark.parametrize("mass_kg,seconds", [(55.0, 12810), (65.0, 8114), (70.0, 6229)])
def test_first_hunger_after_waking_comes_earlier_for_a_heavier_body(mass_kg, seconds):
    """0 点带着 1800 千卡入睡、8 点起床后一直做 light 的事、没有声明饭点：第一次「有点想吃」的时刻随体重变，
    55 公斤 11:33:30、65 公斤 10:15:14（与修订前相同）、70 公斤 09:43:49。两项需要置于顶档，只留饥饿这一个边界。"""
    config, body = _mass_inputs(mass_kg, 0.0)
    slept = settle_shared_body(body, ((ActivityKind.SLEEP, ActivityFactors(0.95)),), 8 * 3600, config).after
    last_flows()
    assert slept.energy_kcal == 1800 - 8 * mass_kg
    awake = slept.model_copy(update={"relaxation_need": 1.0, "social_need": 1.0})
    light = ((ActivityKind.INTERACT, ActivityFactors(config.activity_met["light"])),)

    boundary = next_shared_body_boundary_seconds(awake, light, config, BoundaryClock(8.0))

    def level(elapsed):
        energy = awake.energy_kcal - elapsed / 3600 * 1.5 * mass_kg
        return appetite_level(compute_appetite(energy, 8.0 + elapsed / 3600), energy, 400)

    assert boundary == seconds
    assert (level(seconds - 1), level(seconds)) == ("不饿", "有点想吃")


@pytest.mark.parametrize("mass_kg,seconds", [(55.0, 13091), (65.0, 11077), (70.0, 10286)])
def test_idle_drop_below_the_hunger_threshold_follows_the_body_mass(mass_kg, seconds):
    """空闲时从 1000 千卡到第一次严格低于 800 千卡：55 公斤 13,091 秒、65 公斤 11,077 秒、70 公斤 10,286 秒。
    人物处在声明的饭点时段里（饥饿分级在此之前不变）、两项需要在顶档，下一个身体边界就是这一刻。"""
    config, body = _mass_inputs(mass_kg, 6.0)
    body = body.model_copy(update={"energy_kcal": 1000.0, "relaxation_need": 1.0, "social_need": 1.0})
    clock = BoundaryClock(6.0, MealWindows(lunch=(6.0, 12.0)))

    boundary = next_shared_body_boundary_seconds(body, (), config, clock)

    assert boundary == seconds
    assert 1000.0 - mass_kg * seconds / 3600 < 800 <= 1000.0 - mass_kg * (seconds - 1) / 3600
