"""@brief 固定收货、类别与迁入社交情绪常数的行为证明。"""

from decimal import Decimal

import pytest

from worldmock.exchange import constants as c
from worldmock.exchange.body import BODY_CONSTANTS, RESOURCE_METAS
from worldmock.exchange.types import ActionCategory


def test_fixed_constants_preserve_travel_delivery_map_and_push():
    """@brief 非本特性范围的出行、配送、地图与推送逐值不变。"""
    assert c.RULES_VERSION == 2
    assert (c.WALKING_METERS_PER_SECOND, c.DRIVING_METERS_PER_SECOND) == (1.2, 7.0)
    assert (c.DRIVING_FARE_MINIMUM_FEN, c.DRIVING_FARE_FEN_PER_METER) == (500, Decimal("0.2"))
    assert (c.DELIVERY_METERS_PER_SECOND, c.DELIVERY_PREP_SECONDS, c.DELIVERY_FEE_FEN) == (5.0, 600, 500)
    assert (c.MAP_MIN_NODES, c.MAP_EDGE_LENGTH_M, c.MAP_GENERATOR_RULE_VERSION) == (2, (200, 2000), 1)
    assert (c.MAP_EXTRA_EDGES(2), c.MAP_EXTRA_EDGES(3), c.MAP_EXTRA_EDGES(7)) == (0, 1, 3)
    assert tuple(c.PUSH_HOURS_LOCAL) == tuple(range(8, 22))
    assert c.PUSH_SUMMARY_MAX_CHARS == 200
    assert c.CONTENT_MIN_SECONDS == 60
    assert c.ENTERTAINMENT_RELIEF_PER_HOUR == 0.3
    assert not hasattr(c, "CONTENT_RELIEF_PER_HOUR")


def test_goods_receipt_effects_are_immutable_and_bounded():
    """@brief 收货固定效果不超过最弱娱乐内容半小时的缓解量。"""
    assert dict(c.GOODS_ENERGY_KCAL) == {"meal": 600, "snack": 200, "drink": 100}
    assert dict(c.GOODS_RECEIPT_EFFECTS) == {"leisure": 0.10, "daily": 0.05}
    assert c.GOODS_RECEIPT_EFFECTS["daily"] == c.MOOD_PULSES["goal_progressed"]
    assert max(c.GOODS_RECEIPT_EFFECTS.values()) <= c.ENTERTAINMENT_RELIEF_PER_HOUR * 0.5
    for table in (c.GOODS_ENERGY_KCAL, c.GOODS_RECEIPT_EFFECTS, c.MOOD_PULSES):
        with pytest.raises(TypeError):
            table[next(iter(table))] = 0


def test_category_met_is_fixed_and_deeply_immutable():
    """@brief 所有类别都可有或无 app，仅社交的代谢当量因绑定而变。"""
    assert set(c.CATEGORY_MET) == set(ActionCategory)
    assert {category: dict(values) for category, values in c.CATEGORY_MET.items()} == {
        "entertainment": {False: 1.0, True: 1.0}, "work_study": {False: 1.5, True: 1.5},
        "social": {False: 1.5, True: 1.3}, "exercise": {False: 5.0, True: 5.0},
        "daily_care": {False: 2.0, True: 2.0},
    }
    with pytest.raises(TypeError):
        c.CATEGORY_MET[ActionCategory.SOCIAL][True] = 1.5
    with pytest.raises(TypeError):
        c.CATEGORY_MET[ActionCategory.SOCIAL] = {False: 1.5, True: 1.5}


def test_social_mood_and_exercise_rates_keep_their_frozen_values():
    """@brief 迁入常数维持原名原值，使用者直接从 exchange 读取。"""
    assert (c.SOCIAL_NEED_AWAKE_RATE, c.SOCIAL_NEED_CONVERSATION_RATE, c.SOCIAL_NEED_CALL_RATE,
            c.SOCIAL_NEED_MESSAGE_RELIEF, c.CHAT_RELIEF_DECLINED_PER_HOUR) == (0.04, 0.5, 0.4, 0.05, 0.2)
    assert dict(c.MOOD_PULSES) == {
        "message_received": 0.1, "discomfort_hourly": -0.1, "goal_progressed": 0.05, "service_refused": -0.05}
    assert (c.MOOD_HALFLIFE_MINUTES, c.MOOD_HALFLIFE_NEUROTICISM_FACTOR,
            c.MOOD_PULSE_NEUROTICISM_FACTOR, c.MOOD_DISCOMFORT_TRIGGER_HOURS) == (45, 0.3, 0.5, 2)
    assert c.LIFESTYLE_HEALTH_MOOD_PER_HOUR == 0.1


def test_body_constants_and_resource_metas():
    """@brief 身体只保留系统活动代谢键，其他身体参数与利用曲线不变。"""
    body = BODY_CONSTANTS
    assert (body.maximum_energy_kcal, body.basal_kcal_per_hour) == (2400, 65)
    assert (body.hunger_threshold_kcal, body.emergency_threshold_kcal) == (800, 400)
    assert (body.awake_sleep_debt_per_hour, body.sleep_recovery_per_hour) == (1, 2)
    assert body.entertainment_need_per_awake_hour == 0.08
    assert dict(body.activity_met) == {
        "wait": 1.3, "sleep": 0.95, "travel:walking": 3.5, "travel:driving": 1.5}
    assert [(p.input_value, p.output_value) for p in body.food_efficiency_curve] == [
        (0, 0.9), (8, 0.9), (16, 0.85), (24, 0.8)]
    assert set(RESOURCE_METAS) == {"money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"}
    assert (RESOURCE_METAS["money_fen"].kind, RESOURCE_METAS["money_fen"].conservation) == ("account", "closed")
    assert all(meta.kind == "level" and meta.conservation == "open"
               for key, meta in RESOURCE_METAS.items() if key != "money_fen")


@pytest.mark.parametrize("retired", ["FOOD_CONSTANTS", "FoodConstants", "APP_USE_DEFAULT_SECONDS"])
def test_retired_duration_and_food_constants_are_deleted(retired):
    """@brief 食品食用时长与动作默认时长不留兼容接口。"""
    assert not hasattr(c, retired)
