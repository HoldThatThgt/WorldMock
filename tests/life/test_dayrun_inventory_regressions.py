"""进食分段结算必须守住库存、累计消耗和热量的同一份量账本。"""

import pytest

from tests.life.test_domain_branch_coverage import _agent, _body, _consume, _product, _use_activity
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import apply_meal_progress
from worldmock.life.body import apply_food
from worldmock.life.models import ActivityState, WorldState


@pytest.mark.parametrize("steps", range(2, 50))
def test_meal_partitions_reach_zero_without_losing_quantity_or_energy(engine_factory, steps: int) -> None:
    """任意等分推进均吃完一份；每次 JSON 再水合不出现负库存或重复消耗。"""
    initial = _agent(inventory={"home": {"food-1": 1.0}})
    world = engine_factory().state
    agent = initial
    activity = _use_activity(_consume(duration=steps * 60))
    product, config = _product(), _body()
    for tick in range(1, steps + 1):
        activity.elapsed_seconds = tick * 60
        agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
        consumed = activity.data["consumed"]["food-1"]
        assert round(agent.inventory["home"]["food-1"] + consumed, 9) == 1.0
        assert agent.inventory["home"]["food-1"] >= 0
        world.agents[1] = agent
        agent = WorldState.model_validate_json(world.model_dump_json()).agents[1]
        activity = ActivityState.model_validate_json(activity.model_dump_json())
    assert agent.inventory["home"]["food-1"] == 0.0
    assert activity.data["consumed"]["food-1"] == 1.0
    expected = apply_food(initial.body, product.energy_kcal_per_unit, config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)
    repeated = apply_meal_progress(agent, {"food-1": product}, activity, config)
    assert repeated == agent


def test_irregular_meal_progress_matches_one_settlement() -> None:
    """不规则唤醒与同一进度重放不能让库存或食品收益随分段改变。"""
    initial = _agent(inventory={"home": {"food-1": 0.7}})
    agent = initial
    activity = _use_activity(_consume(quantity=0.7, duration=1800))
    product, config = _product(), _body()
    for elapsed in (1, 73, 419, 419, 899, 1477, 1799, 1800):
        activity.elapsed_seconds = elapsed
        agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
    assert agent.inventory["home"]["food-1"] == 0
    assert activity.data["consumed"]["food-1"] == 0.7
    expected = apply_food(initial.body, product.energy_kcal_per_unit * 0.7, config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)


def test_meal_rounding_tolerance_charges_only_the_actual_quantity() -> None:
    """容差内只消耗实际持有份量，库存、累计消耗与热量保持一致。"""
    held = 0.999999999
    initial = _agent(inventory={"home": {"food-1": held}})
    activity = _use_activity(_consume(), elapsed_seconds=900)
    product, config = _product(), _body()
    agent = apply_meal_progress(initial, {"food-1": product}, activity, config)
    assert agent.inventory["home"]["food-1"] == 0
    assert activity.data["consumed"]["food-1"] == held
    expected = apply_food(initial.body, product.energy_kcal_per_unit * held, config).after
    assert agent.body.energy_kcal == expected.energy_kcal


@pytest.mark.parametrize("held", [0.999999998, 0.9999999986, 0.9])
def test_meal_actual_shortage_still_rejects_without_recording_consumption(held: float) -> None:
    """超过既有十亿分之一份容差的真实缺货必须拒绝，不用钳制吞掉。"""
    agent = _agent(inventory={"home": {"food-1": held}})
    activity = _use_activity(_consume(), elapsed_seconds=900)
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        apply_meal_progress(agent, {"food-1": _product()}, activity, _body())
    assert "consumed" not in activity.data
    assert agent.inventory["home"]["food-1"] == held
