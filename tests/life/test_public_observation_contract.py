"""真实场景观察的人物可见信息与私有动作绑定合同。"""

from __future__ import annotations

import json

import pytest

from worldmock.life import projection
from worldmock.life.narration import render_facts, render_scene


@pytest.mark.parametrize("scenario_name", [
    "white-collar-12-weeks.json", "multi-person.json", "multi-person-half-day.json",
])
def test_real_observation_uses_readable_objects_and_private_bindings(engine_factory, scenario_name: str) -> None:
    """@brief 人物只见语义名称；引擎在同一观察里保留独立的身份绑定。"""
    engine = engine_factory(scenario_name)
    agent_id = engine.scenario.agents[0].agent_id
    observed = engine._observation_data(engine.state.agents[agent_id])
    facts = observed["facts"]
    public = facts["interactive_objects"]
    bindings = observed["interaction_bindings"]

    assert public
    assert len(public) == len(bindings)
    assert [row["object"] for row in public] == [row["object"] for row in bindings]
    assert len({row["object"] for row in public}) == len(public)
    assert all(set(row) == {"object", "actions", "preconditions_met"} for row in public)
    assert all("object_id" in row for row in bindings)
    assert set(facts["progress"]) == {"sessions", "results", "alarms"}

    text = render_facts(facts)
    encoded = json.loads(text)
    assert encoded == facts
    for hidden in ("object_id", "session_id", "task_id", "product_id", "supply_id",
                   "price_version", "recent_action_results"):
        assert f'"{hidden}"' not in text
    for task in engine.scenario.tasks:
        assert task.task_id not in text


def test_food_energy_label_rounds_half_up_to_ten_kcal() -> None:
    """@brief 食品每份热量按 5 进位取整到 10 千卡；不足 5 千卡写「几乎不含热量」（docs/EXPERIMENT.md「能量感知标签」）。"""
    assert projection.food_energy_label(245.0) == "每份约 250 千卡"
    assert projection.food_energy_label(244.9) == "每份约 240 千卡"
    assert projection.food_energy_label(1234.0) == "每份约 1230 千卡"
    assert projection.food_energy_label(5.0) == "每份约 10 千卡"
    assert projection.food_energy_label(4.99) == "几乎不含热量"
    assert projection.food_energy_label(0.0) == "几乎不含热量"


@pytest.mark.parametrize(("met", "effort"), [
    (1.0, "轻松"), (1.5, "轻松"), (1.99, "轻松"), (2.0, "一般"), (2.99, "一般"), (3.0, "费力"), (3.5, "费力"),
])
def test_route_effort_bands_follow_travel_met(met: float, effort: str) -> None:
    """@brief 路线费力：MET < 2 轻松，2 ≤ MET < 3 一般，≥ 3 费力。"""
    assert projection.route_effort(met) == effort


def _staged_at_market(engine, energy: float = 1600.0):
    """@brief 把人物放到门店所在地，手边与家里各有持有物，用于读取本轮公开事实。"""
    products = {item.product_id: item for item in engine.scenario.products}
    food = next(item for item in engine.scenario.supplies
                if item.location_id == "market" and "eat" in products[item.product_id].use_modes)
    gadget = next(item for item in engine.scenario.supplies
                  if item.location_id == "market" and "eat" not in products[item.product_id].use_modes)
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    agent.location_id = "market"
    agent.body.energy_kcal = energy
    agent.inventory = {"market": {food.product_id: 2.0, gadget.product_id: 1.0},
                       "home": {food.product_id: 1.0}}
    engine.state = staged
    return products, food, gadget


def test_products_and_inventory_show_food_energy_and_elsewhere_holdings(engine_factory) -> None:
    """@brief 食品行带每份热量、非食品不带；持有物按地点列出，他处持有物注明只能在当地使用。"""
    engine = engine_factory()
    products, food, gadget = _staged_at_market(engine)
    facts = engine._observation_data(engine.state.agents[1])["facts"]
    assert facts["products"]
    for row in facts["products"]:
        product = next(item for item in products.values() if item.name == row["product"])
        if "eat" in product.use_modes:
            assert row["energy"] == projection.food_energy_label(product.energy_kcal_per_unit)
        else:
            assert "energy" not in row
        assert "delivery_delay_seconds" not in row
    places = {item.location_id: item.name for item in engine.scenario.locations}
    rows = {(row["place"], row["product"]): row for row in facts["inventory"]}
    here = rows[(places["market"], products[food.product_id].name)]
    elsewhere = rows[(places["home"], products[food.product_id].name)]
    gadget_row = rows[(places["market"], products[gadget.product_id].name)]
    assert here["energy"] == elsewhere["energy"] == projection.food_energy_label(
        products[food.product_id].energy_kcal_per_unit)
    assert "note" not in here and "energy" not in gadget_row
    assert elsewhere["note"] == f"不在手边，只能在{places['home']}使用"
    assert here["quantity"] == 2.0 and elsewhere["quantity"] == 1.0
    render_facts(facts)


def test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers(engine_factory) -> None:
    """@brief 低能量时提示只含饥饿分级与可读标签；身体数值只留在不进提示的 body.values。"""
    engine = engine_factory()
    _staged_at_market(engine, energy=60.0)
    observed = engine._observation_data(engine.state.agents[1])
    facts_text = render_facts(observed["facts"])
    scene_text = render_scene(observed["scene"])
    assert observed["facts"]["felt"]["appetite"] == "饿得发慌"
    assert "饿得发慌" in scene_text and "饿得发慌" in facts_text
    assert observed["body"]["values"]["energy_kcal"] == 60.0
    for key in ("energy_kcal", "sleep_pressure", "sleep_deficit_hours", "emergency_threshold_kcal", "appetite\":0"):
        assert key not in facts_text
    assert "千卡" in facts_text


def test_routes_carry_effort_and_feasible_means_reachable_at_zero_energy(engine_factory) -> None:
    """@brief 路线带费力标签（驾车轻松、步行费力，未核实的路线也给）；feasible 只看可达，零能量时仍为真。"""
    engine = engine_factory()
    _staged_at_market(engine, energy=0.0)
    unknown = next(item for item in engine.scenario.routes if item.origin_id == "market")
    blocked = unknown.model_copy(update={"status": "unknown", "distance_meters": None, "duration_seconds": None,
                                         "fare_fen": None, "reason": "no_route_record"})
    engine.scenario = engine.scenario.model_copy(update={"routes": tuple(
        blocked if item.route_id == unknown.route_id else item for item in engine.scenario.routes)})
    routes = engine._observation_data(engine.state.agents[1])["facts"]["routes"]
    assert routes
    for route in routes:
        assert route["feasible"] == (route["status"] == "reachable")
        assert route["effort"] == {"driving": "轻松", "walking": "费力"}[route["mode"]]
    assert any(route["status"] == "unknown" and route["effort"] for route in routes)
    assert any(route["feasible"] for route in routes)
