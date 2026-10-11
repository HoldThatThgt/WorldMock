"""系统规则与身体常数由当前行为合同冻结。"""

from decimal import Decimal
import pytest
from worldmock.exchange import constants as c
from worldmock.exchange.body import BODY_CONSTANTS, RESOURCE_METAS


def test_fixed_travel_delivery_map_and_push_constants():
    assert c.RULES_VERSION == 3
    assert (c.WALKING_METERS_PER_SECOND, c.DRIVING_METERS_PER_SECOND) == (1.2, 7.0)
    assert (c.DRIVING_FARE_MINIMUM_FEN, c.DRIVING_FARE_FEN_PER_METER) == (500, Decimal("0.2"))
    assert (c.DELIVERY_METERS_PER_SECOND, c.DELIVERY_PREP_SECONDS, c.DELIVERY_FEE_FEN) == (5.0, 600, 500)
    assert dict(c.GOODS_ENERGY_KCAL) == {"snack": 200, "meal": 600}
    assert c.GOODS_RELAXATION_RELIEF == 0.10
    with pytest.raises(TypeError):
        c.GOODS_ENERGY_KCAL["meal"] = 650
    assert (c.MAP_MIN_NODES, c.MAP_EDGE_LENGTH_M) == (2, (200, 2000))
    assert (c.MAP_EXTRA_EDGES(2), c.MAP_EXTRA_EDGES(3), c.MAP_EXTRA_EDGES(7)) == (0, 1, 3)
    assert tuple(c.PUSH_HOURS_LOCAL) == tuple(range(8, 22))
    assert c.PUSH_SUMMARY_MAX_CHARS == 200


def test_body_constant_rules_have_all_actual_actions_and_four_resources():
    body = BODY_CONSTANTS
    assert body.maximum_energy_kcal == 2400
    assert (body.hunger_threshold_kcal, body.emergency_threshold_kcal) == (800, 400)
    assert [(point.input_value, point.output_value) for point in body.food_efficiency_curve] == [
        (0, 0.9), (8, 0.9), (16, 0.85), (24, 0.8)]
    for deleted in ("basal_kcal_per_hour", "awake_sleep_debt_per_hour", "sleep_recovery_per_hour"):
        assert not hasattr(body, deleted)
    assert body.relaxation_need_per_awake_hour == 0.08
    assert dict(body.activity_met) == {
        "sleep": 0.95, "walk": 3.5, "drive": 1.5, "take_taxi": 1.5, "take_bus": 1.5,
        "purchase": 1.0, "idle": 1.0, "light": 1.5, "moderate": 2.0, "heavy": 5.0}
    assert set(RESOURCE_METAS) == {
        "money_fen", "energy_kcal", "sleep_deficit_hours", "relaxation_need", "social_need"}
    assert RESOURCE_METAS["money_fen"].conservation == "closed"
    assert all(meta.kind == "level" and meta.conservation == "open"
               for key, meta in RESOURCE_METAS.items() if key != "money_fen")


@pytest.mark.parametrize("retired", ["FUEL_DRIVING_DAYS", "GOODS_RECEIPT_EFFECTS", "CATEGORY_MET",
                                     "MOOD_PULSES", "ENTERTAINMENT_RELIEF_PER_HOUR"])
def test_obsolete_physiology_and_category_inference_is_deleted(retired):
    assert not hasattr(c, retired)
