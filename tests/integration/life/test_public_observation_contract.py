"""真实场景观察的工具层与私有动作绑定合同（docs/design/06-agent.md「工具层」）。"""

from __future__ import annotations

import json

import pytest

from tests.life.conftest import target_actions, tool_targets
from worldmock.life import projection
from worldmock.life.vocabulary import LIFE_VERBS, WAYS
from worldmock.life.tool_layer import BINDING_KINDS

#: 发给人物模型的环境侧层。
_LAYERS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


@pytest.mark.parametrize("scenario_name", [
    "single-person.json", "multi-person.json", "multi-person-half-day.json",
])
def test_real_observation_uses_readable_objects_and_private_bindings(engine_factory, scenario_name: str) -> None:
    """@brief 人物只见工具层的可读目标；引擎在同一观察里保留独立的身份绑定。

    工具层目标与私有绑定按顺序一一对应（之后是活动目标）：目标名等于绑定的对象名，类别按绑定类别映射；每个动作的 action 与
    way 就是绑定动作的动词与方式，reason 与绑定动作的 note 同源（available 为 true 时都为 null）。「自己」恒列等待与睡觉；
    内部身份不进工具层。
    """
    engine = engine_factory(scenario_name)
    agent_id = engine.context.scenario.agents[0].agent_id
    observed = engine.observations._observation_data(engine.context.state.agents[agent_id])
    targets = observed["tool_layer"]["targets"]
    bindings = observed["interaction_bindings"]
    assert bindings and targets[:len(bindings)] and all(row["kind"] == "activity" for row in targets[len(bindings):])
    assert [row["name"] for row in targets[:len(bindings)]] == [row["object"] for row in bindings]
    assert len({row["name"] for row in targets}) == len(targets)
    assert all("object_id" in row for row in bindings)
    for target, binding in zip(targets, bindings):
        assert target["kind"] == BINDING_KINDS[binding["kind"]]
        assert [(item["action"], item["way"], item["reason"]) for item in target["actions"]] == [
            (item["verb"], item["label"], item["note"]) for item in binding["actions"]]
        for item in target["actions"]:
            assert item["action"] in LIFE_VERBS
            assert (item["reason"] is None) == item["available"]
        for item in binding["actions"]:
            assert WAYS[item["label"]] == (item["verb"], item["canonical_action"])
    assert {"等待", "睡觉"} <= set(target_actions(observed, "自己"))
    ways = {"person": {"交谈"}, "contact": {"通话"}}  # 同场人物只列交谈，通话只对通讯录联系人开放
    for binding in bindings:
        assert {item["label"] for item in binding["actions"]} <= ways.get(binding["kind"], set(WAYS))
    text = json.dumps([observed[key] for key in _LAYERS], ensure_ascii=False)
    for hidden in ("object_id", "session_id", "task_id", "product_id", "supply_id",
                   "price_version", "recent_action_results", "preconditions_met", "canonical_action",
                   "food_id", "store_id", "app_id", "content_id", "topic_id", "push_id", "trip_id"):
        assert f'"{hidden}"' not in text
    for content_id in engine.context.scenario.agents[0].work_content_ids:
        assert content_id not in text
    assert "scene" not in observed and "facts" not in observed and "recent_action_results" not in observed


def test_food_energy_label_rounds_half_up_to_ten_kcal() -> None:
    """@brief 食品每份热量按 5 进位取整到 10 千卡；不足 5 千卡写「几乎不含热量」（docs/design/02-exchange-engine.md「能量感知标签」）。"""
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


def _staged_at_store(engine, energy: float = 1600.0):
    """@brief 把人物放到登记门店，手边与家里各有实际食品，读取本轮工具层。"""
    goods = engine.context.rules.goods
    food = goods["chips"]
    drink = goods["lemon-tea"]
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[1]
    agent.location_id = "store"
    agent.body.energy_kcal = energy
    engine.context.state = staged
    return goods, food, drink


def test_products_show_fixed_energy_and_no_inventory_targets(engine_factory):
    """商品按固定类别显示热量，库存字段和持有物目标全部退役。"""
    from worldmock.exchange.constants import GOODS_ENERGY_KCAL
    from worldmock.exchange.types import GOODS_CATEGORY_LABELS
    engine = engine_factory()
    goods, _, _ = _staged_at_store(engine)
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    targets = tool_targets(observed)
    offered = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"]
    assert offered
    for row in offered:
        item = goods[row["goods_id"]]
        label = GOODS_CATEGORY_LABELS[item.category]
        assert targets[row["object"]]["state"].startswith(label)
        if item.category in GOODS_ENERGY_KCAL:
            assert "千卡" in targets[row["object"]]["state"]
    assert not hasattr(engine.context.state.agents[1], "inventory")
    assert not any(row["name"].startswith("持有物：") for row in observed["tool_layer"]["targets"])


def test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers(engine_factory) -> None:
    """@brief 低能量时环境层只含饥饿分级，工具层只含可读标签；身体数值只留在不进提示的 body.values。"""
    engine = engine_factory()
    _staged_at_store(engine, energy=60.0)
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    text = json.dumps([observed[key] for key in _LAYERS], ensure_ascii=False)
    assert observed["environment_layer"]["body"]["appetite"] == "饿得发慌"
    assert observed["body"]["values"]["energy_kcal"] == 60.0
    for key in ("energy_kcal", "sleep_pressure", "sleep_deficit_hours", "emergency_threshold_kcal", "appetite\":0"):
        assert key not in text
    assert "千卡" in text


def test_routes_carry_effort_and_only_reachable_routes_are_listed_at_zero_energy(engine_factory) -> None:
    """@brief 出行动作的 note 写费力程度（驾车轻松、步行费力）；只有可达的路线列出为动作，未核实的路线不列；零能量时照常
    可用（能量不拦人）。"""
    engine = engine_factory(mutate=lambda doc: doc["agents"][0].update(owns_car=True, initial_driving_days=3))
    _staged_at_store(engine, energy=0.0)
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    places = [row for row in observed["interaction_bindings"] if row["kind"] == "location"]
    assert places
    trips = [(row, item) for row in places for item in tool_targets(observed)[row["object"]]["actions"]
             if item["way"] in {"步行", "驾车"}]
    assert trips and all(item["note"] == {"驾车": "费力程度：轻松", "步行": "费力程度：费力"}[item["way"]]
                         for _, item in trips)
    expected = set(engine.context.rules.locations) - {"store"}
    assert {row["object_id"] for row, _ in trips} == expected
    assert {item["way"] for _, item in trips} == {"步行", "驾车"}
    assert any(item["available"] for _, item in trips)
