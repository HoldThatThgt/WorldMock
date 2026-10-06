"""@brief 阶段三的实际食品、身体分录与可追溯食用证据纯测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.data_paths import CATALOG_ROOT, ENVIRONMENT_ROOT
from tests.life.purchase_worlds import registered_world
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import Food
from worldmock.errors import IntegrityError
from worldmock.exchange.body import BODY_CONSTANTS, RESOURCE_METAS
from worldmock.exchange.operators import evaluate_food_energy
from worldmock.evaluation.behavior_metrics import _meal_diversity
from worldmock.footprints.experiences import project_verified_experiences, validate_verified_experiences
from worldmock.footprints.models import Event
from worldmock.footprints.quality import _food_efficiency_samples
from worldmock.life.activities import activity_factors
from worldmock.life.activity_evidence import conclusion_outcome, discard_opened, meal_consumption_data
from worldmock.life.basket import apply_meal_progress, meal_duration, meal_food_rate, meal_portion, open_meal
from worldmock.life.basket import purchase_order
from worldmock.life.body import apply_food, body_resource_metas, last_flows
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState
from worldmock.life.models import ConsumeItem, ConsumeRequest, PurchaseItem, PurchaseOrderRequest
from worldmock.life.sleep import food_utilization_efficiency
from worldmock.life.world_setup import load_scenario


@pytest.fixture(scope="module")
def body_config():
    """@brief 真实人工环境通过生产装配取得固定身体规则。"""
    return load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body


@pytest.fixture(scope="module")
def foods():
    """@brief 真实目录通过生产加载器提供食品，另一零食也使用真实 Food 合同。"""
    values = dict(load_catalog(CATALOG_ROOT).foods)
    values["crackers"] = Food(food_id="crackers", name="饼干", category="snack", price_fen=500, store_ids="all")
    return values


@pytest.fixture(autouse=True)
def clear_food_flows():
    """@brief 每个测试只核验自己的资源分录。"""
    last_flows()
    yield
    last_flows()


def food_agent(food_id="beef-noodles", quantity=1.5, energy=800, deficit=0):
    """@brief 构造真实人物状态，持有份量位于进食地点。"""
    return AgentState(agent_id=1, name="测试人物", location_id="home", balance_account_id="agent:1",
                      body=BodyState(energy_kcal=energy, sleep_pressure=0.2, sleep_deficit_hours=deficit,
                                     entertainment_need=0), inventory={"home": {food_id: quantity}},
                      body_settled_at=0, next_decision_at=0)


def food_request(food_id="beef-noodles", quantity=1.0, duration=1200):
    """@brief ConsumeRequest 保持 product_id，值是实际目录食品标识。"""
    return ConsumeRequest(items=(ConsumeItem(product_id=food_id, quantity=quantity),), use_mode="eat",
                          duration_seconds=duration)


def food_activity(request, opened, identity="meal"):
    """@brief 开餐快照冻结份量和实际开始时刻。"""
    return ActivityState(activity_id=identity, agent_id=1, location_id="home", kind=ActivityKind.CONSUME,
                         status=ActivityStatus.ACTIVE, started_at=0, planned_end_at=request.duration_seconds,
                         last_settled_at=0, elapsed_seconds=0, planned_duration_seconds=request.duration_seconds,
                         origin_action_id=f"action-{identity}", origin_decision_id="decision",
                         origin_request_event_id="request", data={"request": request.model_dump(mode="json"),
                                                                 "opened": opened, "consumed": {}})


def food_event(activity, consumption, start, end, seq=1):
    """@brief 使用真实 Event 合同封存实际消费区间，供经历与报告投影。"""
    return Event(event_id=f"food-{seq}", event_type="activity_progress", run_id="food-run", event_seq=seq,
                 recorded_at="2026-10-07T00:00:00Z", wall_elapsed_ms=1, state_version_before=seq - 1,
                 state_version_after=seq, occurred_at=end, agent_id=1, activity_id=activity.activity_id,
                 data={"interval_start": start, "interval_end": end, "consumption": consumption,
                       "activity": activity.model_dump(mode="json")})


def test_food_category_defines_equal_snack_intake_and_three_fixed_rates(foods, body_config):
    """@brief 两种零食等量摄入相同；三个类别按真实份数与固定时长计算。"""
    for identity, seconds, kcal in (("chips", 300, 200), ("crackers", 300, 200),
                                   ("lemon-tea", 180, 100), ("beef-noodles", 1200, 600)):
        request = food_request(identity, 0.5, max(60, round(seconds * 0.5)))
        assert meal_duration(foods, request) == request.duration_seconds
        assert meal_food_rate(foods, request) * request.duration_seconds / 3600 == kcal * 0.5
        agent, opened = open_meal(food_agent(identity), request)
        activity = food_activity(request, opened, identity)
        activity.elapsed_seconds = request.duration_seconds
        after = apply_meal_progress(agent, foods, activity, body_config)
        assert after.body.energy_kcal == pytest.approx(800 + kcal * 0.5 * 0.9)
        consumption = meal_consumption_data(activity, after, {}, foods)
        assert consumption["items"][0]["interval_kcal"] == kcal * 0.5


def test_food_purchase_changes_holding_and_only_actual_eating_increases_energy():
    """@brief 实际到店购买只改变付款与持有，随后吃半份才经身体账本获益。"""
    semantics, state = registered_world(location="noodle-1")
    before = state.agents[1].body.energy_kcal
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", food_id="beef-noodles", quantity=1,
                                                       recipient_id="agent:1"),), fulfillment="pickup",
                                   destination_id="noodle-1", app_id=None)
    bought = purchase_order(state, semantics, 1, request)
    agent = bought.state.agents[1]
    assert agent.body.energy_kcal == before and last_flows() == ()
    assert agent.inventory["noodle-1"]["beef-noodles"] == 1
    consume = food_request(quantity=0.5, duration=600)
    agent, opened = open_meal(agent, consume)
    activity = food_activity(consume, opened).model_copy(update={"location_id": "noodle-1"}, deep=True)
    activity.elapsed_seconds = 600
    agent = apply_meal_progress(agent, semantics.catalog.foods, activity, semantics.body)
    assert agent.body.energy_kcal == pytest.approx(before + 270)
    assert agent.inventory["noodle-1"]["beef-noodles"] == 0.5
    assert [(flow.cause, flow.amount) for flow in last_flows()] == [("food:intake", 270)]


def test_food_split_pause_resume_and_repeated_progress_conserve_raw_intake(foods, body_config):
    """@brief 暂停、再水合和重复推进只结算累计差额，一份分两次仍摄入 600 千卡。"""
    request = food_request()
    agent, opened = open_meal(food_agent(), request)
    activity = food_activity(request, opened)
    assert agent.inventory == {"home": {"beef-noodles": 0.5}}
    activity.elapsed_seconds = 600
    agent = apply_meal_progress(agent, foods, activity, body_config)
    first = meal_consumption_data(activity, agent, {}, foods)
    assert activity.data["consumed"] == {"beef-noodles": 0.5}
    activity.status = ActivityStatus.PAUSED
    activity = ActivityState.model_validate_json(activity.model_dump_json())
    agent = AgentState.model_validate_json(agent.model_dump_json())
    repeated = apply_meal_progress(agent, foods, activity, body_config)
    assert repeated == agent
    assert meal_consumption_data(activity, repeated, {"beef-noodles": 0.5}, foods)["items"][0]["interval_kcal"] == 0
    activity.status, activity.elapsed_seconds = ActivityStatus.COMPLETED, 1200
    agent = apply_meal_progress(agent, foods, activity, body_config)
    second = meal_consumption_data(activity, agent, {"beef-noodles": 0.5}, foods)
    assert first["items"][0]["interval_kcal"] + second["items"][0]["interval_kcal"] == 600
    assert agent.body.energy_kcal == pytest.approx(apply_food(food_agent().body, 600, body_config).after.energy_kcal)
    assert agent.inventory == {"home": {"beef-noodles": 0.5}}
    assert apply_meal_progress(agent, foods, activity, body_config) == agent


def test_food_fixed_body_efficiency_energy_cap_and_resource_source(body_config):
    """@brief 系统食品效率曲线与 2400 千卡上限真实约束净收益和分录余量。"""
    assert body_config.source_kind == "worldmock:1"
    assert body_config.maximum_energy_kcal == BODY_CONSTANTS.maximum_energy_kcal == 2400
    assert body_resource_metas(body_config)[0] is RESOURCE_METAS["energy_kcal"]
    for deficit, efficiency in ((0, 0.9), (8, 0.9), (12, 0.875), (16, 0.85), (24, 0.8)):
        assert food_utilization_efficiency(deficit) == pytest.approx(efficiency)
        body = food_agent(energy=2300, deficit=deficit).body
        result = apply_food(body, 600, body_config)
        assert result.food_efficiency == pytest.approx(efficiency)
        assert result.food_energy_kcal == pytest.approx(600 * efficiency)
        assert result.after.energy_kcal == 2400
        assert result.work_efficiency == 1
        assert [(flow.direction, flow.amount) for flow in last_flows()] == [("source", 100)]


def test_food_actual_portion_duration_bounds_and_multi_item_longest(foods):
    """@brief 不足一份按类别时长折算；请求窗口保持 60–14400 秒，多件取最长。"""
    assert meal_portion(foods["beef-noodles"], "eat", 0.25) == (0.25, 300)
    assert meal_portion(foods["chips"], "eat", 0.01) == (0.01, 60)
    request = ConsumeRequest(items=(ConsumeItem(product_id="chips", quantity=2),
                                    ConsumeItem(product_id="beef-noodles", quantity=0.25)),
                             use_mode="eat", duration_seconds=600)
    assert meal_duration(foods, request) == 600
    assert meal_duration(foods, food_request(quantity=12, duration=14400)) == 14400
    for duration in (59, 14401):
        with pytest.raises(ValidationError):
            food_request(duration=duration)


def test_food_progress_verified_experiences_and_quality_use_food_identity(foods, body_config):
    """@brief 消费事件、核验经历和质量统计保留真实食品、类别与区间份额。"""
    request = food_request(quantity=0.5, duration=600)
    agent, opened = open_meal(food_agent(), request)
    activity = food_activity(request, opened)
    activity.elapsed_seconds = 600
    activity.data["food_energy_kcal_per_hour"] = meal_food_rate(foods, request) * 0.9
    assert activity_factors(activity, agent, body_config).food_energy_kcal_per_hour == 1620
    agent = apply_meal_progress(agent, foods, activity, body_config)
    consumption = meal_consumption_data(activity, agent, {}, foods)
    assert consumption == {"use_mode": "eat", "location_id": "home", "food_efficiency": 0.9,
                           "items": [{"food_id": "beef-noodles", "category": "meal", "interval_portion": 0.5,
                                      "cumulative_portion": 0.5, "interval_kcal": 300}]}
    event = food_event(activity, consumption, 0, 600)
    facts = project_verified_experiences([event], agent_id=1, cutoff_seq=1)
    assert facts[0] == {"kind": "consumed", "agent_id": 1, "activity_id": "meal", "source_event_id": "food-1",
                        "event_seq": 1, "occurred_at": 600, "food_id": "beef-noodles", "category": "meal",
                        "location_id": "home", "interval_portion": 0.5, "cumulative_portion": 0.5,
                        "interval_start": 0, "interval_end": 600}
    assert validate_verified_experiences(facts, 1, 600) == facts
    assert _food_efficiency_samples([event]) == ([(600.0, 0.9)], 0)
    assert _meal_diversity([event.model_dump(mode="json")])["product_counts"] == {"beef-noodles": 1}
    with pytest.raises(IntegrityError):
        validate_verified_experiences([{**facts[0], "category": "dessert"}], 1, 600)


def test_food_ending_discards_only_opened_remainder_without_extra_intake(foods, body_config):
    """@brief 提前收尾丢弃本餐剩余份数，不补摄入，也不动未开封持有。"""
    request = food_request()
    agent, opened = open_meal(food_agent(quantity=4), request)
    activity = food_activity(request, opened)
    activity.elapsed_seconds = 240
    agent = apply_meal_progress(agent, foods, activity, body_config)
    before = agent.body.energy_kcal
    activity.status, activity.ended_at = ActivityStatus.CANCELLED, 240
    ended = discard_opened(activity)
    assert ended.data["discarded"] == {"beef-noodles": 0.8}
    assert conclusion_outcome(ended, "吃饭", {"beef-noodles": "牛肉面"}, agent)["items"] == [
        {"product": "牛肉面", "eaten": "0.2 份", "discarded": "0.8 份", "remaining": "3 份"}]
    assert agent.body.energy_kcal == before
    assert agent.inventory == {"home": {"beef-noodles": 3.0}}
