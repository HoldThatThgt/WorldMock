"""完成效果的结算：档位的数额只取两张既有的表，实得取登记的数额，分录次序与原因，三个成员互不影响。"""

import tomllib
from pathlib import Path

import pytest

from worldmock.catalog.models import CompletionEffects
from worldmock.exchange.constants import GOODS_ENERGY_KCAL
from worldmock.input_models import PersonaConfig
from worldmock.life.body import initial_body, last_flows
from worldmock.life.body_config import assemble_body_config
from worldmock.life.completion_effects import COMPLETION_CAUSE, settle_completion_effects
from worldmock.life.interact import RELIEF_PER_HOUR
from worldmock.life.sleep import food_utilization_efficiency

PERSONA = Path("tests/fixtures/dataset-environment/persona.toml")


@pytest.fixture
def body_inputs():
    persona = PersonaConfig.model_validate(tomllib.loads(PERSONA.read_text(encoding="utf-8")))
    config = assemble_body_config(persona)
    body = initial_body(config, 8.0, persona).model_copy(
        update={"energy_kcal": 1000.0, "sleep_deficit_hours": 8.0, "relaxation_need": 0.8, "social_need": 0.9})
    last_flows()
    return config, body


def settle(body, config, **members):
    return settle_completion_effects(body, CompletionEffects(**members), config, RELIEF_PER_HOUR)


@pytest.mark.parametrize("tier", ["snack", "meal"])
def test_food_provides_the_goods_energy_of_its_tier(body_inputs, tier):
    """补能的 provided 等于同名商品类别的收货热量；欠觉不超过 8 小时时利用率 0.9，分录是能量的 source。"""
    config, body = body_inputs
    changed, evidence, flows = settle(body, config, food=tier)
    provided = GOODS_ENERGY_KCAL[tier]
    applied = provided * food_utilization_efficiency(body.sleep_deficit_hours)
    assert evidence == {"food": {"tier": tier, "provided": provided, "applied": applied, "efficiency": 0.9},
                        "relaxation_relief": None, "social_relief": None}
    assert [(f.resource, f.direction, f.amount, f.cause) for f in flows] == [
        ("energy_kcal", "source", applied, "settle:shared:energy_kcal")]
    assert changed.energy_kcal == body.energy_kcal + applied
    assert last_flows() == ()


def test_a_meal_at_no_more_than_eight_hours_of_sleep_debt_applies_540(body_inputs):
    config, body = body_inputs
    for deficit in (0.0, 8.0):
        _, evidence, flows = settle(body.model_copy(update={"sleep_deficit_hours": deficit}), config, food="meal")
        assert evidence["food"]["applied"] == 540 == flows[0].amount


def test_food_at_the_energy_cap_applies_nothing_and_writes_no_flow(body_inputs):
    """能量已到上限时 applied 为 0，证据照写，没有分录。"""
    config, body = body_inputs
    full = body.model_copy(update={"energy_kcal": config.maximum_energy_kcal})
    changed, evidence, flows = settle(full, config, food="meal")
    assert evidence["food"] == {"tier": "meal", "provided": 600, "applied": 0.0, "efficiency": 0.9}
    assert flows == () and changed.energy_kcal == config.maximum_energy_kcal


def test_food_near_the_cap_applies_the_registered_amount(body_inputs):
    """接近上限时只进到上限，applied 取登记的分录数额，不用结算前后两数相减。"""
    config, body = body_inputs
    near = body.model_copy(update={"energy_kcal": config.maximum_energy_kcal - 100.3})
    changed, evidence, flows = settle(near, config, food="snack")
    assert evidence["food"]["applied"] == flows[0].amount
    assert flows[0].amount == pytest.approx(100.3) and changed.energy_kcal == config.maximum_energy_kcal


@pytest.mark.parametrize("member,resource", [("relaxation_relief", "relaxation_need"),
                                             ("social_relief", "social_need")])
@pytest.mark.parametrize("tier", ["low", "medium", "high"])
def test_relief_provides_one_hour_of_its_tier(body_inputs, member, resource, tier):
    """缓解的 provided 等于内容按小时缓解的同一档；需要够减时一次减去这个量，分录是 drain、原因 content:completion。"""
    config, body = body_inputs
    changed, evidence, flows = settle(body, config, **{member: tier})
    provided = RELIEF_PER_HOUR[tier]
    assert evidence[member] == {"tier": tier, "provided": provided, "applied": provided}
    assert [(f.resource, f.owner, f.direction, f.amount, f.cause) for f in flows] == [
        (resource, "body", "drain", provided, COMPLETION_CAUSE)]
    assert getattr(changed, resource) == pytest.approx(getattr(body, resource) - provided)
    assert changed.energy_kcal == body.energy_kcal


@pytest.mark.parametrize("member,resource", [("relaxation_relief", "relaxation_need"),
                                             ("social_relief", "social_need")])
def test_relief_clamps_to_the_need(body_inputs, member, resource):
    """需要低于档位的量时减到 0，applied 等于结算前的需要；需要已为 0 时 applied 为 0、没有分录。"""
    config, body = body_inputs
    low = body.model_copy(update={resource: 0.05})
    changed, evidence, flows = settle(low, config, **{member: "high"})
    assert evidence[member] == {"tier": "high", "provided": 0.5, "applied": 0.05}
    assert getattr(changed, resource) == 0.0 and [f.amount for f in flows] == [0.05]
    empty = body.model_copy(update={resource: 0.0})
    changed, evidence, flows = settle(empty, config, **{member: "low"})
    assert evidence[member] == {"tier": "low", "provided": 0.1, "applied": 0.0}
    assert flows == () and changed == empty


def test_members_settle_in_order_and_do_not_affect_each_other(body_inputs):
    """三个成员按 food、relaxation_relief、social_relief 的次序结算，结果与逐个结算相同。"""
    config, body = body_inputs
    changed, evidence, flows = settle(body, config, food="meal", relaxation_relief="medium", social_relief="low")
    assert [entry.resource for entry in flows] == ["energy_kcal", "relaxation_need", "social_need"]
    assert list(evidence) == ["food", "relaxation_relief", "social_relief"]
    for member, tier in (("food", "meal"), ("relaxation_relief", "medium"), ("social_relief", "low")):
        _, part, _ = settle(body, config, **{member: tier})
        assert part[member] == evidence[member]
    assert (changed.energy_kcal, changed.relaxation_need, changed.social_need) == (
        body.energy_kcal + 540, pytest.approx(0.5), pytest.approx(0.8))
    assert changed.sleep_deficit_hours == body.sleep_deficit_hours
