"""进食分段结算必须守住库存、开吃份量、累计食用和热量的同一份量账本。

开吃时本次份量从库存移入活动的 opened（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」），之后只按 opened 与实际进度结算：
任一时刻「库存 ＋ 开吃份量中未吃的部分 ＋ 已吃 ＝ 初始」，其他份数不动，暂停期间库存被别处吃掉也不影响结算。
"""

import pytest

from tests.life.test_domain_branch_coverage import _agent, _body, _consume, _product, _use_activity
from worldmock.life.basket import apply_meal_progress, open_meal
from worldmock.life.body import apply_food
from worldmock.life.models import ActivityState, ActivityStatus, WorldState


def _opened_meal(initial: float, quantity: float, duration: int):
    """开吃一份进食：返回（开吃后的人物，带 opened 的活动）。"""
    request = _consume(quantity=quantity, duration=duration)
    agent, opened = open_meal(_agent(inventory={"home": {"food-1": initial}}), request)
    activity = _use_activity(request)
    activity.data["opened"] = opened
    return agent, activity


def _ledger(agent, activity) -> float:
    """库存 ＋ 开吃份量中未吃的部分 ＋ 已吃。"""
    eaten = activity.data.get("consumed", {}).get("food-1", 0.0)
    return round(agent.inventory["home"]["food-1"] + (activity.data["opened"]["food-1"] - eaten) + eaten, 9)


@pytest.mark.parametrize("steps", range(2, 50))
def test_meal_partitions_reach_zero_without_losing_quantity_or_energy(engine_factory, steps: int) -> None:
    """持有 1.5 份开吃 1 份：任意等分推进都守住账本，开吃份量中未吃的部分降到零；每次 JSON 再水合不出现负库存或
    重复食用，库存留下的 0.5 份不动。"""
    world = engine_factory().context.state
    agent, activity = _opened_meal(1.5, 1.0, steps * 60)
    initial = agent
    product, config = _product(), _body()
    assert agent.inventory["home"]["food-1"] == 0.5
    for tick in range(1, steps + 1):
        activity.elapsed_seconds = tick * 60
        agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
        assert _ledger(agent, activity) == 1.5
        assert agent.inventory["home"]["food-1"] == 0.5
        world.agents[1] = agent
        agent = WorldState.model_validate_json(world.model_dump_json()).agents[1]
        activity = ActivityState.model_validate_json(activity.model_dump_json())
    assert activity.data["consumed"]["food-1"] == 1.0
    expected = apply_food(initial.body, product.energy_kcal_per_unit, config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)
    repeated = apply_meal_progress(agent, {"food-1": product}, activity, config)
    assert repeated == agent


def test_irregular_meal_progress_matches_one_settlement() -> None:
    """不足一份（0.7 份）按实际持有开吃：不规则唤醒与同一进度重放不能让库存、账本或食品收益随分段改变。"""
    agent, activity = _opened_meal(0.7, 0.7, 1260)
    initial = agent
    product, config = _product(), _body()
    for elapsed in (1, 73, 419, 419, 899, 1177, 1259, 1260):
        activity.elapsed_seconds = elapsed
        agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
        assert _ledger(agent, activity) == 0.7
    assert agent.inventory["home"]["food-1"] == 0
    assert activity.data["consumed"]["food-1"] == 0.7
    expected = apply_food(initial.body, product.energy_kcal_per_unit * 0.7, config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)


def test_meal_rounding_tolerance_eats_only_the_opened_quantity() -> None:
    """持有 0.999999999 份时开吃份量就是它：吃完只算实际开吃的份量，库存为 0，账本与热量一致。"""
    held = 0.999999999
    agent, activity = _opened_meal(held, held, 900)
    initial = agent
    activity.elapsed_seconds = 900
    product, config = _product(), _body()
    agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
    assert agent.inventory["home"]["food-1"] == 0
    assert activity.data["consumed"]["food-1"] == held
    expected = apply_food(initial.body, product.energy_kcal_per_unit * held, config).after
    assert agent.body.energy_kcal == expected.energy_kcal


@pytest.mark.parametrize("left", [0.0, 0.3])
def test_eating_progress_ignores_stock_taken_while_the_meal_was_paused(left: float) -> None:
    """暂停的进食只按自己的开吃份量结算：暂停期间库存被别的进食吃掉（剩 0 或 0.3 份），恢复后照常吃完，不再以
    PRODUCT_NOT_HELD 失败，库存不再变化（旧口径在推进时按剩余量扣库存而抛出未捕获的业务拒绝）。"""
    agent, activity = _opened_meal(1.5, 1.0, 900)
    product, config = _product(), _body()
    activity.elapsed_seconds = 450
    agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
    activity.status = ActivityStatus.PAUSED
    agent = agent.model_copy(update={"inventory": {"home": {"food-1": left}}}, deep=True)
    activity.status, activity.elapsed_seconds = ActivityStatus.COMPLETED, 900
    agent = apply_meal_progress(agent, {"food-1": product}, activity, config)
    assert activity.data["consumed"]["food-1"] == 1.0
    assert agent.inventory["home"]["food-1"] == left
