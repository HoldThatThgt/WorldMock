"""实际身体资源、双过程睡眠、食品效率与体感边界的数值行为。"""

import math

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from worldmock.environment import load_environment
from worldmock.life import sleep
from worldmock.life.body import (
    ActivityFactors, BoundaryClock, apply_food, initial_body, next_shared_body_boundary_seconds,
    settle_body, settle_shared_body, last_flows, body_resource_metas, apply_relaxation_change,
)
from worldmock.life.body_config import assemble_body_config
from worldmock.life.models import ActivityKind


@pytest.fixture
def parameters():
    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)
    config = assemble_body_config(inputs.persona)
    last_flows()
    yield config, initial_body(config, 8.0, inputs.persona, 0.06)
    last_flows()


@pytest.mark.parametrize("kind,met", [(ActivityKind.INTERACT, 1.5), (ActivityKind.TRAVEL, 3.5),
                                      (ActivityKind.PURCHASE, 1.0), (ActivityKind.SLEEP, 0.95)])
def test_one_basal_cost_and_only_excess_metabolic_cost(parameters, kind, met):
    config, body = parameters
    settled = settle_body(body, kind, 3600, config, ActivityFactors(met))
    assert settled.basal_energy_kcal == 65
    assert settled.extra_energy_kcal == max(met - 1, 0) * 65
    assert settled.after.energy_kcal == body.energy_kcal - settled.basal_energy_kcal - settled.extra_energy_kcal
    assert body.energy_kcal == 1800


def test_energy_cannot_drop_below_zero(parameters):
    config, body = parameters
    body = body.model_copy(update={"energy_kcal": 10.0})
    settled = settle_shared_body(body, ((ActivityKind.TRAVEL, ActivityFactors(3.5)),), 3600, config)
    assert settled.after.energy_kcal == 0


def test_sleep_requires_no_travel_mode_but_travel_does(parameters):
    config, body = parameters
    assert settle_body(body, ActivityKind.SLEEP, 60, config).after.energy_kcal < body.energy_kcal
    with pytest.raises(ValueError, match="actual mode"):
        settle_body(body, ActivityKind.TRAVEL, 60, config)


@pytest.mark.parametrize("debt,efficiency", [(0, 0.9), (8, 0.9), (12, 0.875), (16, 0.85), (24, 0.8)])
def test_actual_receipt_uses_sleep_deficit_at_that_second(parameters, debt, efficiency):
    config, body = parameters
    body = body.model_copy(update={"sleep_deficit_hours": float(debt)})
    settled = apply_food(body, 600, config)
    assert settled.food_efficiency == pytest.approx(efficiency)
    assert settled.after.energy_kcal == pytest.approx(1800 + 600 * efficiency)
    assert settled.duration_seconds == 0


@pytest.mark.parametrize("calories", [-1, math.nan, math.inf])
def test_invalid_food_cannot_produce_a_resource_change(parameters, calories):
    config, body = parameters
    with pytest.raises(ValueError):
        apply_food(body, calories, config)


@pytest.mark.parametrize("hours", [0.0, 0.25, 1.0, 8.0, 24.0])
def test_pressure_functions_are_exact_exponential_processes(hours):
    assert sleep.pressure_after_awake(0.3, hours) == pytest.approx(1 - 0.7 * math.exp(-hours / 18.2))
    assert sleep.pressure_after_sleep(0.3, hours) == pytest.approx(0.3 * math.exp(-hours / 4.2))


@pytest.mark.parametrize("need", [7.0, 7.5, 9.0])
def test_awake_debt_and_sleep_recovery_follow_personal_need(need):
    assert sleep.deficit_after_awake(1, 1, need) == pytest.approx(1 + need / (24 - need))
    assert sleep.deficit_after_sleep(5, 1) == 4
    assert sleep.deficit_after_awake(24, 1, need) == 24
    assert sleep.deficit_after_sleep(0, 1) == 0


@pytest.mark.parametrize("function", [sleep.pressure_after_awake, sleep.pressure_after_sleep,
                                     sleep.deficit_after_awake, sleep.deficit_after_sleep])
def test_negative_sleep_process_duration_is_rejected(function):
    with pytest.raises(ValueError):
        function(0.2, -1)


@pytest.mark.parametrize("need", [6.999, 9.001])
def test_personal_sleep_need_has_no_outside_values(need):
    with pytest.raises(ValueError):
        sleep.validate_sleep_need_hours(need)


@pytest.mark.parametrize("hour", [0.0, 4.0, 8.0, 16.0, 23.0, 48.0])
def test_circadian_thresholds_are_periodic_and_have_a_positive_gap(hour):
    high = sleep.sleep_onset_threshold(hour)
    low = sleep.wake_threshold(hour)
    assert high > low
    assert high == pytest.approx(sleep.sleep_onset_threshold(hour + 24))
    assert low == pytest.approx(sleep.wake_threshold(hour + 24))
    assert sleep.sleepiness(low, hour) == 0
    assert sleep.sleepiness(high, hour) == pytest.approx(1)


def test_future_body_boundary_is_strictly_future_and_sleep_does_not_offer_one(parameters):
    config, body = parameters
    clock = BoundaryClock(8.0)
    boundary = next_shared_body_boundary_seconds(body, (), config, clock)
    assert isinstance(boundary, int) and boundary > 0
    assert next_shared_body_boundary_seconds(body, ((ActivityKind.SLEEP, ActivityFactors(0.95)),), config, clock) is None


def test_shared_settlement_is_invariant_to_splitting_actual_interval(parameters):
    config, body = parameters
    selected = ((ActivityKind.TRAVEL, ActivityFactors(3.5)), (ActivityKind.INTERACT, ActivityFactors(1.5, 0.3, 0.1)))
    whole = settle_shared_body(body, selected, 600, config).after
    first = settle_shared_body(body, selected, 200, config).after
    split = settle_shared_body(first, selected, 400, config).after
    for name in ("energy_kcal", "sleep_pressure", "sleep_deficit_hours", "relaxation_need", "social_need"):
        assert getattr(split, name) == pytest.approx(getattr(whole, name))


def test_four_open_level_domains_are_immutable_fixed_bounds(parameters):
    config, _ = parameters
    metas = {row.resource_id: row for row in body_resource_metas(config)}
    assert set(metas) == {"energy_kcal", "sleep_deficit_hours", "relaxation_need", "social_need"}
    assert [(metas[key].bounds_min, metas[key].bounds_max) for key in metas] == [
        (0, 2400), (0, 24), (0, 1), (0, 1)]
    assert all(row.kind == "level" and row.conservation == "open" for row in metas.values())


@pytest.mark.parametrize("initial,amount,delta", [(2390, 200, 10), (1800, 200, 180), (2400, 200, 0)])
def test_food_flow_records_the_actual_clamped_source(parameters, initial, amount, delta):
    config, body = parameters
    body = body.model_copy(update={"energy_kcal": float(initial)})
    after = apply_food(body, amount, config).after
    flows = last_flows()
    assert after.energy_kcal - body.energy_kcal == delta
    assert sum(row.amount for row in flows if row.resource == "energy_kcal") == delta
    assert all(row.direction == "source" for row in flows)
    assert last_flows() == ()


def test_shared_activity_has_one_actual_energy_drain_and_two_need_changes(parameters):
    config, body = parameters
    selected = ((ActivityKind.TRAVEL, ActivityFactors(3.5)), (ActivityKind.INTERACT, ActivityFactors(1.5, 0.3, 0.5)))
    after = settle_shared_body(body, selected, 600, config).after
    flows = last_flows()
    energy = [row for row in flows if row.resource == "energy_kcal"]
    assert len(energy) == 1 and energy[0].direction == "drain"
    assert energy[0].amount == pytest.approx(body.energy_kcal - after.energy_kcal)
    for name in ("relaxation_need", "social_need"):
        flow = next(row for row in flows if row.resource == name)
        assert flow.direction == "drain"
        assert flow.amount == pytest.approx(getattr(body, name) - getattr(after, name))


@pytest.mark.parametrize("initial,amount,delta", [(0.99, 0.05, 0.01), (0.05, -0.1, -0.05), (1.0, 0.1, 0)])
def test_relaxation_pulse_records_only_its_actual_limit_delta(parameters, initial, amount, delta):
    _, body = parameters
    body = body.model_copy(update={"relaxation_need": initial})
    after = apply_relaxation_change(body, amount)
    assert after.relaxation_need - initial == pytest.approx(delta)
    flows = last_flows()
    assert sum(row.amount for row in flows) == pytest.approx(abs(delta))
    if flows:
        assert flows[0].direction == ("source" if delta > 0 else "drain")
