"""进食分段结算必须守住库存、开吃份量、累计食用和热量的同一份量账本。

开吃时本次份量从库存移入活动的 opened（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」），之后只按 opened 与实际进度结算：
任一时刻「库存 ＋ 开吃份量中未吃的部分 ＋ 已吃 ＝ 初始」，其他份数不动，暂停期间库存被别处吃掉也不影响结算。
"""

import pytest

from tests.life.test_rrs_food_body import body_config, food_activity, food_agent, food_request, foods
from worldmock.life.basket import apply_meal_progress, open_meal
from worldmock.life.body import apply_food
from worldmock.life.models import ActivityState, ActivityStatus, AgentState


def _opened_meal(initial: float, quantity: float, duration: int):
    """开吃一份进食：返回（开吃后的人物，带 opened 的活动）。"""
    request = food_request(quantity=quantity, duration=duration)
    agent, opened = open_meal(food_agent(quantity=initial), request)
    activity = food_activity(request, opened)
    return agent, activity


def _ledger(agent, activity) -> float:
    """库存 ＋ 开吃份量中未吃的部分 ＋ 已吃。"""
    eaten = activity.data.get("consumed", {}).get("beef-noodles", 0.0)
    return round(agent.inventory["home"]["beef-noodles"] + (activity.data["opened"]["beef-noodles"] - eaten)
                 + eaten, 9)


@pytest.mark.parametrize("steps", range(2, 50))
def test_meal_partitions_reach_zero_without_losing_quantity_or_energy(body_config, foods, steps: int) -> None:
    """持有 1.5 份开吃 1 份：任意等分推进都守住账本，开吃份量中未吃的部分降到零；每次 JSON 再水合不出现负库存或
    重复食用，库存留下的 0.5 份不动。"""
    agent, activity = _opened_meal(1.5, 1.0, 1200)
    initial = agent
    assert agent.inventory["home"]["beef-noodles"] == 0.5
    for tick in range(1, steps + 1):
        activity.elapsed_seconds = tick * 1200 // steps
        agent = apply_meal_progress(agent, foods, activity, body_config)
        assert _ledger(agent, activity) == 1.5
        assert agent.inventory["home"]["beef-noodles"] == 0.5
        agent = AgentState.model_validate_json(agent.model_dump_json())
        activity = ActivityState.model_validate_json(activity.model_dump_json())
    assert activity.data["consumed"]["beef-noodles"] == 1.0
    expected = apply_food(initial.body, 600, body_config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)
    repeated = apply_meal_progress(agent, foods, activity, body_config)
    assert repeated == agent


def test_irregular_meal_progress_matches_one_settlement(body_config, foods) -> None:
    """不足一份（0.7 份）按实际持有开吃：不规则唤醒与同一进度重放不能让库存、账本或食品收益随分段改变。"""
    agent, activity = _opened_meal(0.7, 0.7, 840)
    initial = agent
    for elapsed in (1, 73, 419, 419, 699, 817, 839, 840):
        activity.elapsed_seconds = elapsed
        agent = apply_meal_progress(agent, foods, activity, body_config)
        assert _ledger(agent, activity) == 0.7
    assert agent.inventory["home"]["beef-noodles"] == 0
    assert activity.data["consumed"]["beef-noodles"] == 0.7
    expected = apply_food(initial.body, 420, body_config).after
    assert agent.body.energy_kcal == pytest.approx(expected.energy_kcal, abs=1e-8, rel=0)


def test_meal_rounding_tolerance_eats_only_the_opened_quantity(body_config, foods) -> None:
    """持有 0.999999999 份时开吃份量就是它：吃完只算实际开吃的份量，库存为 0，账本与热量一致。"""
    held = 0.999999999
    agent, activity = _opened_meal(held, held, 1200)
    initial = agent
    activity.elapsed_seconds = 1200
    agent = apply_meal_progress(agent, foods, activity, body_config)
    assert agent.inventory["home"]["beef-noodles"] == 0
    assert activity.data["consumed"]["beef-noodles"] == held
    expected = apply_food(initial.body, 600 * held, body_config).after
    assert agent.body.energy_kcal == expected.energy_kcal


@pytest.mark.parametrize("left", [0.0, 0.3])
def test_eating_progress_ignores_stock_taken_while_the_meal_was_paused(body_config, foods, left: float) -> None:
    """暂停的进食只按自己的开吃份量结算：暂停期间库存被别的进食吃掉（剩 0 或 0.3 份），恢复后照常吃完，不再以
    PRODUCT_NOT_HELD 失败，库存不再变化（旧口径在推进时按剩余量扣库存而抛出未捕获的业务拒绝）。"""
    agent, activity = _opened_meal(1.5, 1.0, 1200)
    activity.elapsed_seconds = 600
    agent = apply_meal_progress(agent, foods, activity, body_config)
    activity.status = ActivityStatus.PAUSED
    agent = agent.model_copy(update={"inventory": {"home": {"beef-noodles": left}}}, deep=True)
    activity.status, activity.elapsed_seconds = ActivityStatus.COMPLETED, 1200
    agent = apply_meal_progress(agent, foods, activity, body_config)
    assert activity.data["consumed"]["beef-noodles"] == 1.0
    assert agent.inventory["home"]["beef-noodles"] == left
