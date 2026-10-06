"""@brief 系统固定仿真假设与身体资源常数的证明。"""

from decimal import Decimal

import pytest

from worldmock.exchange import constants as c
from worldmock.exchange.body import BODY_CONSTANTS, RESOURCE_METAS


def test_fixed_constants_and_immutable_food_table():
    """@brief 食品、出行、配送、地图与推送常数只读且取冻结值。"""
    assert c.RULES_VERSION == 1
    assert {key: (row.energy_kcal, row.duration_seconds) for key, row in c.FOOD_CONSTANTS.items()} == {
        "snack": (200, 300), "drink": (100, 180), "meal": (600, 1200)}
    assert (c.WALKING_METERS_PER_SECOND, c.DRIVING_METERS_PER_SECOND) == (1.2, 7.0)
    assert (c.DRIVING_FARE_MINIMUM_FEN, c.DRIVING_FARE_FEN_PER_METER) == (500, Decimal("0.2"))
    assert (c.DELIVERY_METERS_PER_SECOND, c.DELIVERY_PREP_SECONDS, c.DELIVERY_FEE_FEN) == (5.0, 600, 500)
    assert (c.MAP_MIN_NODES, c.MAP_EDGE_LENGTH_M, c.MAP_GENERATOR_RULE_VERSION) == (2, (200, 2000), 1)
    assert (c.MAP_EXTRA_EDGES(2), c.MAP_EXTRA_EDGES(3), c.MAP_EXTRA_EDGES(7)) == (0, 1, 3)
    assert tuple(c.PUSH_HOURS_LOCAL) == tuple(range(8, 22))
    assert (c.PUSH_SUMMARY_MAX_CHARS, c.APP_USE_DEFAULT_SECONDS) == (200, 1800)
    assert dict(c.CONTENT_RELIEF_PER_HOUR) == {
        "article": 0.2, "image_text": 0.2, "short_video": 0.3, "long_video": 0.4, "live": 0.3}
    with pytest.raises(TypeError):
        c.FOOD_CONSTANTS["drink"] = c.FOOD_CONSTANTS["meal"]


def test_body_constants_and_resource_metas():
    """@brief 身体阈值、代谢和食品利用效率来自固定系统常数。"""
    body = BODY_CONSTANTS
    assert (body.maximum_energy_kcal, body.basal_kcal_per_hour) == (2400, 65)
    assert (body.hunger_threshold_kcal, body.emergency_threshold_kcal) == (800, 400)
    assert (body.awake_sleep_debt_per_hour, body.sleep_recovery_per_hour) == (1, 2)
    assert body.entertainment_need_per_awake_hour == 0.08
    assert dict(body.activity_met) == {
        "wait": 1.3, "sleep": 0.95, "work": 1.5, "use_app": 1.0, "read_mail": 1.3,
        "consume": 1.5, "chat": 1.5, "call": 1.3, "travel:walking": 3.5, "travel:driving": 1.5}
    assert [(p.input_value, p.output_value) for p in body.food_efficiency_curve] == [
        (0, 0.9), (8, 0.9), (16, 0.85), (24, 0.8)]
    assert set(RESOURCE_METAS) == {"money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"}
    assert (RESOURCE_METAS["money_fen"].kind, RESOURCE_METAS["money_fen"].conservation) == ("account", "closed")
    assert all(meta.kind == "level" and meta.conservation == "open"
               for key, meta in RESOURCE_METAS.items() if key != "money_fen")
