"""真实区间心理效果、睡眠冻结与成熟身体算法的回归。"""
import math
import tomllib
from pathlib import Path
import pytest
from worldmock.exchange_engine.commerce.operators import evaluate_goods_receipt
from worldmock.configuration.inputs.models import PersonaConfig
from worldmock.simulation_engine.body.body import (
    ActivityFactors,
    BoundaryClock,
    apply_discomfort_pulse,
    apply_food,
    apply_relaxation_change,
    food_intake,
    initial_body,
    last_flows,
    next_shared_body_boundary_seconds,
    settle_shared_body,
)
from worldmock.simulation_engine.body.body_config import assemble_body_config
from worldmock.simulation_engine.body.felt import EnergyReading, MealWindows, summarize_felt_levels
from worldmock.state.world.kinds import ActivityKind

@pytest.fixture
def body_inputs():
    raw = tomllib.loads(Path('tests/fixtures/dataset-environment/persona.toml').read_text(encoding='utf-8'))
    persona = PersonaConfig.model_validate(raw)
    config = assemble_body_config(persona)
    body = initial_body(config, 8.0, persona)
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
    raw = tomllib.loads(Path('tests/fixtures/dataset-environment/persona.toml').read_text(encoding='utf-8'))
    heavier = assemble_body_config(PersonaConfig.model_validate({**raw, 'body': {**raw['body'], 'mass_kg': 70.0}}))
    other = settle_shared_body(body, activities, 3600, heavier)
    assert (other.basal_energy_kcal, other.extra_energy_kcal) == (70, 2.5 * 70)

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

def test_sleep_debt_recovers_by_the_time_actually_slept_including_the_extra_part(body_inputs):
    """欠的觉每睡 1 小时补 1 小时，按实际睡的时长算：写 170 分钟又多睡 27 分钟，补回的是 197 分钟；
    多睡的那段与写的那段分开结算和一次结算相同，补到 0 为止。"""
    config, body = body_inputs
    body = body.model_copy(update={'sleep_deficit_hours': 5.0})
    asleep = ((ActivityKind.SLEEP, ActivityFactors(0.95)),)
    whole = settle_shared_body(body, asleep, 10200 + 1620, config).after
    assert whole.sleep_deficit_hours == pytest.approx(5.0 - 11820 / 3600)
    declared = settle_shared_body(body, asleep, 10200, config).after
    assert declared.sleep_deficit_hours == pytest.approx(5.0 - 10200 / 3600)
    extra = settle_shared_body(declared, asleep, 1620, config).after
    assert extra.sleep_deficit_hours == pytest.approx(whole.sleep_deficit_hours)
    assert extra.sleep_pressure < declared.sleep_pressure < body.sleep_pressure
    assert settle_shared_body(body, asleep, 13 * 3600, config).after.sleep_deficit_hours == 0.0

def test_no_active_effects_relief_free_interaction_and_purchase_have_only_growth(body_inputs):
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

def test_received_meal_and_snack_yield_their_fixed_energy_and_the_pool_stops_at_its_cap(body_inputs):
    """欠觉不超过 8 小时时一件食品实得 540、一件零食实得 180；能量 1500 连收两件食品，第二件只能加到 2400。"""
    config, body = body_inputs
    meal, snack = evaluate_goods_receipt('meal', 1), evaluate_goods_receipt('snack', 1)
    body = body.model_copy(update={'energy_kcal': 1000.0})
    assert apply_food(body, meal, config).after.energy_kcal - body.energy_kcal == 540
    assert apply_food(body, snack, config).after.energy_kcal - body.energy_kcal == 180
    last_flows()
    first = apply_food(body.model_copy(update={'energy_kcal': 1500.0}), meal, config)
    second = apply_food(first.after, meal, config)
    assert (first.after.energy_kcal, second.after.energy_kcal) == (2040, 2400)
    assert second.food_energy_kcal == 540
    assert [flow.amount for flow in last_flows() if flow.resource == 'energy_kcal'] == [540, 360]

def test_food_intake_returns_the_body_the_registered_amount_and_its_own_flows(body_inputs):
    """食品进账只有 food_intake 一份实现：返回的身体、{provided, applied, efficiency} 与这一笔分录互相一致，applied 是
    分录数额之和，调用后缓冲里不剩这一笔；欠觉 16 小时的利用率 0.85，进到能量上限为止。"""
    config, body = body_inputs
    hungry = body.model_copy(update={'energy_kcal': 2100.0, 'sleep_deficit_hours': 16.0})
    changed, intake, entries = food_intake(hungry, 600, config)
    assert intake == {'provided': 600, 'applied': sum(entry.amount for entry in entries), 'efficiency': 0.85}
    assert [(entry.resource, entry.direction, entry.cause) for entry in entries] == [
        ('energy_kcal', 'source', 'settle:shared:energy_kcal')]
    assert intake['applied'] == pytest.approx(300.0) and changed.energy_kcal == pytest.approx(2400.0)
    assert changed.model_copy(update={'energy_kcal': hungry.energy_kcal}) == hungry
    assert last_flows() == ()


def test_food_intake_at_the_energy_cap_applies_zero_without_flows(body_inputs):
    """能量已到上限：applied 是 0.0（浮点），没有分录，身体不变，缓冲里没有东西。"""
    config, body = body_inputs
    full = body.model_copy(update={'energy_kcal': config.maximum_energy_kcal})
    changed, intake, entries = food_intake(full, 200, config)
    assert (intake, entries, changed) == ({'provided': 200, 'applied': 0.0, 'efficiency': 0.9}, (), full)
    assert type(intake['applied']) is float and last_flows() == ()


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


@pytest.mark.parametrize("exertion,met", [("walking", 3.5), ("idle", 1.0), ("light", 1.5), ("moderate", 2.0),
                                          ("heavy", 5.0)])
def test_travel_burns_energy_by_the_exertion_key_frozen_at_departure(body_inputs, exertion, met):
    """出行与 interact 一样读受理时写下的代谢当量键：自带的步行是 walking（3.5），数据集的方式是它声明的体力档位；
    与并行活动同时进行时取较高的代谢当量，基础代谢只扣一次。"""
    from types import SimpleNamespace

    from worldmock.simulation_engine.activities.lifecycle.activities import activity_factors

    config, body = body_inputs
    trip = SimpleNamespace(kind=ActivityKind.TRAVEL, data={"action": "travel", "exertion": exertion})
    factors = activity_factors(trip, None, config)
    assert factors == ActivityFactors(met)
    alone = settle_shared_body(body, ((ActivityKind.TRAVEL, factors),), 3600, config)
    assert (alone.basal_energy_kcal, alone.extra_energy_kcal) == (65, (met - 1) * 65)
    for level, other in (("idle", 1.0), ("light", 1.5), ("moderate", 2.0), ("heavy", 5.0)):
        media = SimpleNamespace(kind=ActivityKind.INTERACT, data={"action": "interact", "exertion": level})
        pair = ((ActivityKind.TRAVEL, factors), (ActivityKind.INTERACT, activity_factors(media, None, config)))
        both = settle_shared_body(body, pair, 3600, config)
        assert (both.basal_energy_kcal, both.extra_energy_kcal) == (65, (max(met, other) - 1) * 65)


def test_activity_met_table_has_walking_and_the_four_exertion_levels_but_no_fixed_vehicles(body_inputs):
    """身体常量表里出行只剩自带步行的 walking 一个键，驾车、打车、公共交通三行与旧的 walk 键都已删除；查询按自己的
    query 键取 1.0，恰八项、次序如表。"""
    config, _ = body_inputs
    assert list(config.activity_met.items()) == [("sleep", 0.95), ("walking", 3.5), ("purchase", 1.0), ("query", 1.0),
                                                 ("idle", 1.0), ("light", 1.5), ("moderate", 2.0), ("heavy", 5.0)]
    assert not {"walk", "drive", "take_taxi", "take_bus", "travel"} & set(config.activity_met)
