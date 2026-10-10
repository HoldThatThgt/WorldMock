"""真实区间心理效果、睡眠冻结与成熟身体算法的回归。"""
import math
import tomllib
from pathlib import Path
import pytest
from worldmock.input_models import PersonaConfig
from worldmock.life.body import (ActivityFactors, BoundaryClock, apply_discomfort_pulse, apply_food,
    apply_relaxation_change, initial_body, last_flows, next_shared_body_boundary_seconds, settle_shared_body)
from worldmock.life.body_config import assemble_body_config
from worldmock.life.felt import EnergyReading, MealWindows, summarize_felt_levels
from worldmock.life.models import ActivityKind

@pytest.fixture
def body_inputs():
    raw = tomllib.loads(Path('tests/fixtures/dataset-environment/persona.toml').read_text(encoding='utf-8'))
    persona = PersonaConfig.model_validate(raw)
    config = assemble_body_config(persona)
    body = initial_body(config, 8.0, persona, 0.06)
    last_flows()
    return config, body

def test_awake_growth_is_added_once_across_both_slots(body_inputs):
    config, body = body_inputs
    activities = ((ActivityKind.TRAVEL, ActivityFactors(3.5)), (ActivityKind.INTERACT, ActivityFactors(1.0)))
    result = settle_shared_body(body, activities, 3600, config)
    assert result.after.relaxation_need == pytest.approx(body.relaxation_need + 0.08)
    assert result.after.social_need == pytest.approx(body.social_need + 0.06)
    assert result.basal_energy_kcal == 65
    assert result.extra_energy_kcal == 2.5 * 65

def test_two_content_effects_are_independent_actual_time(body_inputs):
    config, body = body_inputs
    result = settle_shared_body(body, ((ActivityKind.INTERACT, ActivityFactors(1.5, 0.3, 0.1)),), 900, config)
    assert result.after.relaxation_need == pytest.approx(0.2 + (0.08 - 0.3) / 4)
    assert result.after.social_need == pytest.approx(0.2 + (0.06 - 0.1) / 4)
    assert result.after.energy_kcal == pytest.approx(1800 - (65 + 0.5 * 65) / 4)

def test_sleep_freezes_both_needs_and_recovers_sleep_deficit(body_inputs):
    config, body = body_inputs
    body = body.model_copy(update={'sleep_deficit_hours': 3.0})
    result = settle_shared_body(body, ((ActivityKind.SLEEP, ActivityFactors(0.95)),), 3600, config)
    assert result.after.relaxation_need == body.relaxation_need
    assert result.after.social_need == body.social_need
    assert result.after.sleep_deficit_hours == 2.0
    assert result.after.sleep_pressure < body.sleep_pressure
    assert next_shared_body_boundary_seconds(body, ((ActivityKind.SLEEP, ActivityFactors(0.95)),),
        config, BoundaryClock(8.0)) is None

def test_no_active_effects_pure_browse_and_purchase_have_only_growth(body_inputs):
    config, body = body_inputs
    for activities in ((), ((ActivityKind.PURCHASE, ActivityFactors(1.0)),),
                       ((ActivityKind.INTERACT, ActivityFactors(1.0)),)):
        result = settle_shared_body(body, activities, 3600, config)
        assert result.after.relaxation_need == pytest.approx(0.28)
        assert result.after.social_need == pytest.approx(0.26)

def test_zero_interval_cannot_repeat_effects(body_inputs):
    config, body = body_inputs
    result = settle_shared_body(body, ((ActivityKind.INTERACT, ActivityFactors(5.0, 0.5, 0.5)),), 0, config)
    assert result.after == body
    assert last_flows() == ()

def test_need_resources_clamp_and_record_actual_delta(body_inputs):
    config, body = body_inputs
    body = body.model_copy(update={'relaxation_need': 0.99, 'social_need': 0.01})
    result = settle_shared_body(body, ((ActivityKind.INTERACT, ActivityFactors(1.0, 0.0, 0.5)),), 3600, config)
    assert result.after.relaxation_need == 1.0
    assert result.after.social_need == 0.0
    flows = {flow.resource: flow for flow in last_flows()}
    assert flows['relaxation_need'].amount == pytest.approx(0.01)
    assert flows['social_need'].amount == pytest.approx(0.01)

def test_food_uses_actual_sleep_deficit_and_existing_energy_cap(body_inputs):
    config, body = body_inputs
    body = body.model_copy(update={'energy_kcal': 2390.0, 'sleep_deficit_hours': 8.0})
    result = apply_food(body, 600, config)
    assert result.food_efficiency == pytest.approx(0.9)
    assert result.after.energy_kcal == 2400
    assert result.after.relaxation_need == body.relaxation_need

def test_discomfort_and_rejection_modify_only_relaxation(body_inputs):
    _, body = body_inputs
    assert apply_discomfort_pulse(body, 0.5).relaxation_need == pytest.approx(0.25)
    changed = apply_relaxation_change(body, 0.05)
    assert changed.social_need == body.social_need
    assert changed.relaxation_need == pytest.approx(0.25)

def test_four_felt_signals_have_exact_approved_boundaries():
    levels = summarize_felt_levels({'appetite': 0, 'sleepiness': 0, 'relaxation_need': .3, 'social_need': .6},
        EnergyReading(1800, 400))
    assert levels == {'appetite':'不饿', 'sleepiness':'清醒', 'relaxation':'有点想放松', 'social':'有点孤单'}
    assert MealWindows(lunch=(12, 13), dinner=(18, 19)).lunch == (12, 13)

def test_negative_actual_duration_is_rejected(body_inputs):
    config, body = body_inputs
    with pytest.raises(ValueError, match='cannot be negative'):
        settle_shared_body(body, (), -1, config)
