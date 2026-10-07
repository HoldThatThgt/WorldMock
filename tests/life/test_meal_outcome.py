"""@brief 阶段三纯食品生命周期：真实活动推进、暂停恢复、份量结算和收尾证据。"""

import pytest

from tests.life.test_rrs_food_body import body_config, food_activity, food_agent, food_request, foods
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import advance_activity, change_activity, truncate_activity
from worldmock.life.activity_evidence import conclusion_outcome, discard_opened
from worldmock.life.basket import apply_meal_progress, meal_duration, meal_food_rate, meal_portion, open_meal
from worldmock.life.basket import portion_duration
from worldmock.life.body import last_flows
from worldmock.life.models import ActivityStatus, ChangeActivityRequest
from worldmock.life.sleep import food_utilization_efficiency


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """@brief 每个纯进食用例前后清空分录证据，避免向后续引擎结算泄漏。"""
    last_flows()
    yield
    last_flows()


def _opened(initial, quantity, foods, body_config):
    """@brief 以实际正餐类别时长开餐，库存份量转入活动 opened。"""
    request = food_request(quantity=quantity, duration=portion_duration(1200, quantity))
    agent, opened = open_meal(food_agent(quantity=initial), request)
    activity = food_activity(request, opened)
    activity.data["food_energy_kcal_per_hour"] = (meal_food_rate(foods, request)
                                                 * food_utilization_efficiency(agent.body.sleep_deficit_hours))
    return agent, activity


def _advance(agent, activity, now, foods, body_config):
    """@brief 真实活动结算身体消耗，再以实际累计差额结算食品摄入。"""
    advance = advance_activity(activity, agent, now, body_config)
    return apply_meal_progress(advance.agent, foods, advance.activity, body_config), advance.activity


def _change(agent, activity, operation, now):
    """@brief 使用生产活动状态转换，暂停与恢复本身不消费份数。"""
    return change_activity(agent, activity, ChangeActivityRequest(activity_id=activity.activity_id,
                                                                  operation=operation), now, 900)


def _outcome(activity, agent):
    """@brief 从活动终局与当地实际持有投影收尾结果。"""
    return conclusion_outcome(activity, "吃饭", {"beef-noodles": "牛肉面"}, agent)


def test_ending_a_meal_at_240_of_1200_seconds_eats_0_2_discards_0_8_and_keeps_3(body_config, foods):
    """@brief 正餐固定 1200 秒，吃 240 秒后收尾只摄入 0.2 份，丢弃 0.8 份，持有仍为 3 份。"""
    agent, activity = _opened(4, 1, foods, body_config)
    agent, activity = _advance(agent, activity, 240, foods, body_config)
    agent, activity = _change(agent, activity, "cancel", 240)
    ended = discard_opened(activity)
    assert (ended.data["consumed"], ended.data["discarded"]) == ({"beef-noodles": 0.2}, {"beef-noodles": 0.8})
    assert _outcome(ended, agent) == {"activity": "吃饭", "outcome": "提前结束", "items": [
        {"product": "牛肉面", "eaten": "0.2 份", "discarded": "0.8 份", "remaining": "3 份"}], "note": None}


def test_ending_a_meal_early_adds_no_energy_for_the_discarded_portion(body_config, foods):
    """@brief 提前结束与丢弃不补算剩余热量。"""
    agent, activity = _opened(4, 1, foods, body_config)
    last_flows()
    agent, activity = _advance(agent, activity, 240, foods, body_config)
    gains = [flow.amount for flow in last_flows() if flow.cause == "food:intake"]
    assert gains == [pytest.approx(108)]
    before = agent.body.energy_kcal
    agent, activity = _change(agent, activity, "cancel", 240)
    ended = discard_opened(activity)
    assert agent.body.energy_kcal == before and ended.data["discarded"] == {"beef-noodles": 0.8}
    assert last_flows() == ()


def test_pause_keeps_the_opened_remainder_and_resume_eats_it_after_another_meal_took_the_stock(body_config, foods):
    """@brief 两餐分别持有 opened，第二餐吃掉库存后第一餐恢复仍按自己的余份结算。"""
    agent, first = _opened(1.5, 1, foods, body_config)
    agent, first = _advance(agent, first, 600, foods, body_config)
    agent, first = _change(agent, first, "pause", 600)
    paused_body = agent.body
    assert first.data["consumed"] == {"beef-noodles": 0.5}
    agent, first = _advance(agent, first, 900, foods, body_config)
    assert agent.body == paused_body and first.data["consumed"] == {"beef-noodles": 0.5}
    request = food_request(quantity=0.5, duration=600)
    agent, opened = open_meal(agent, request)
    second = food_activity(request, opened, "second")
    second.started_at, second.last_settled_at, second.planned_end_at = 900, 900, 1500
    second.data["food_energy_kcal_per_hour"] = meal_food_rate(foods, request) * 0.9
    agent, second = _advance(agent, second, 1500, foods, body_config)
    assert second.status == ActivityStatus.COMPLETED and agent.inventory == {"home": {"beef-noodles": 0}}
    agent, first = _change(agent, first, "resume", 1500)
    before = agent.body.energy_kcal
    assert first.data["consumed"] == {"beef-noodles": 0.5}
    agent, first = _advance(agent, first, 2100, foods, body_config)
    assert first.status == ActivityStatus.COMPLETED and first.data["consumed"] == {"beef-noodles": 1.0}
    assert agent.body.energy_kcal > before
    assert discard_opened(first).data["discarded"] == {"beef-noodles": 0.0}
    assert agent.inventory == {"home": {"beef-noodles": 0}}


def test_ending_a_paused_meal_discards_the_opened_remainder_without_intake(body_config, foods):
    """@brief 暂停后取消只丢弃本餐未吃余份，不增加摄入。"""
    agent, activity = _opened(4, 1, foods, body_config)
    agent, activity = _advance(agent, activity, 240, foods, body_config)
    agent, activity = _change(agent, activity, "pause", 240)
    before = agent.body.energy_kcal
    agent, activity = _change(agent, activity, "cancel", 900)
    ended = discard_opened(activity)
    assert agent.body.energy_kcal == before
    assert ended.data["discarded"] == {"beef-noodles": 0.8}
    assert _outcome(ended, agent)["outcome"] == "提前结束"


def test_concluding_a_failed_meal_names_the_failure_and_discards_the_remainder(body_config, foods):
    """@brief 真实进食后的失败终态只按已有摄入投影，失败不创造热量。"""
    agent, activity = _opened(4, 1, foods, body_config)
    agent, activity = _advance(agent, activity, 240, foods, body_config)
    activity = activity.model_copy(update={"status": ActivityStatus.FAILED, "ended_at": 240}, deep=True)
    before = agent.body.energy_kcal
    ended = discard_opened(activity)
    assert _outcome(ended, agent)["outcome"] == "失败"
    assert ended.data["discarded"] == {"beef-noodles": 0.8}
    assert agent.body.energy_kcal == before


def test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy(body_config, foods):
    """@brief 四分之一份正餐实际进食 300 秒，原始摄入 150 千卡，净流入 135 千卡。"""
    assert meal_portion(foods["beef-noodles"], "eat", 0.25) == (0.25, 300)
    agent, activity = _opened(0.25, 0.25, foods, body_config)
    assert activity.data["request"]["items"] == [{"product_id": "beef-noodles", "quantity": 0.25}]
    last_flows()
    agent, activity = _advance(agent, activity, 300, foods, body_config)
    assert activity.data["consumed"] == {"beef-noodles": 0.25}
    assert [flow.amount for flow in last_flows() if flow.cause == "food:intake"] == [135]
    assert _outcome(discard_opened(activity), agent)["items"] == [
        {"product": "牛肉面", "eaten": "0.25 份", "discarded": "0 份", "remaining": "0 份"}]


def test_experiment_cutoff_truncation_discards_nothing(body_config, foods):
    """@brief 实验截止仅截断实际活动，不丢弃本餐余份，也不动未开封食品。"""
    agent, activity = _opened(4, 1, foods, body_config)
    agent, activity = _advance(agent, activity, 600, foods, body_config)
    ended, agent = truncate_activity(activity, agent, 600)
    assert ended.status == ActivityStatus.TRUNCATED and ended.data["consumed"] == {"beef-noodles": 0.5}
    assert "discarded" not in ended.data
    assert agent.inventory == {"home": {"beef-noodles": 3.0}}


def test_portion_duration_and_meal_portion_share_one_basis(foods):
    """@brief 份量、时长均按类别常数计算，小份保留一分钟下限。"""
    assert [portion_duration(1200, portion) for portion in (1.0, 0.25, 0.5, 0.01)] == [1200, 300, 600, 60]
    assert meal_portion(foods["beef-noodles"], "eat", 4) == (1, 1200)
    assert meal_portion(foods["beef-noodles"], "eat", 0.25) == (0.25, 300)
    assert meal_portion(foods["beef-noodles"], "eat", 0) == (0, 60)


def test_meal_duration_is_validated_against_the_scaled_portion(foods):
    """@brief 四分之一份正餐必须对应 300 秒，不能继续使用一整份时长。"""
    request = food_request(quantity=0.25, duration=300)
    assert meal_duration(foods, request) == 300
    with pytest.raises(BusinessRuleError, match="MEAL_DURATION_MUST_BE_LONGEST"):
        meal_duration(foods, request.model_copy(update={"duration_seconds": 1200}))


def test_open_meal_moves_the_portion_out_of_stock_only_for_eating():
    """@brief 开餐只移出当地本次食品份量；非进食请求被明确拒绝。"""
    agent = food_agent(quantity=4)
    agent.inventory["office"] = {"beef-noodles": 2}
    changed, opened = open_meal(agent, food_request())
    assert opened == {"beef-noodles": 1}
    assert changed.inventory == {"home": {"beef-noodles": 3}, "office": {"beef-noodles": 2}}
    with pytest.raises(BusinessRuleError, match="PRODUCT_USE_MODE_NOT_ALLOWED"):
        open_meal(agent, food_request().model_copy(update={"use_mode": "use"}))


def test_conclusion_outcome_names_failure_and_explicitly_reports_no_item_changes(body_config, foods):
    """@brief 食品收尾只投影已结算份数，非食品活动明确没有物品变化。"""
    agent, activity = _opened(4, 1, foods, body_config)
    agent, activity = _advance(agent, activity, 240, foods, body_config)
    failed = activity.model_copy(update={"status": ActivityStatus.FAILED, "ended_at": 240}, deep=True)
    assert _outcome(discard_opened(failed), agent)["outcome"] == "失败"
    waiting = activity.model_copy(update={"kind": "wait", "status": ActivityStatus.CANCELLED, "ended_at": 240}, deep=True)
    assert discard_opened(waiting) is waiting
    assert conclusion_outcome(waiting, "等待", {}, agent) == {
        "activity": "等待", "outcome": "提前结束", "items": [], "note": "No item changes."}
