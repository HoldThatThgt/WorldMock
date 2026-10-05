"""环境层：观察时刻、五项体感分级与所在地点或路程（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「环境层」）。

体感取现有分级文字、数值不进提示；停留时写地点名与场所类型的中文标签，路程中写路段标签「去<目的地>的路上」与出行方式
（现行路程中误写出发地）；fixed_rules 是本轮有效菜单排除的动作的可读说法，仓库场景菜单全开时为 []。场所类型标签覆盖
仓库场景的全部地点类型，未知类型是场景缺陷。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.life.conftest import advance_next, context, stage_day_plan, start_activity, submit_round, wake_by_mail
from worldmock.errors import IntegrityError
from worldmock.life.environment_layer import ACTION_PHRASES, PLACE_TYPES, environment_layer
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.readable import iso_time
from worldmock.life.world_setup import load_runtime_scenario


def test_stay_writes_the_place_name_type_felt_levels_and_no_fixed_rules(engine_factory) -> None:
    """运行开始在家：observed_at 为观察时刻，body 五项按固定顺序取观察的体感分级文字（没有数值），location 为停留、
    地点名、场所类型「家」，菜单全开时 fixed_rules 为 []；仓库场景的人物没有工作安排，duty 为 null。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "stay"))
    layer = observed["environment_layer"]
    validate_layer(layer)
    levels = observed["body"]["levels"]
    home = next(item.name for item in engine.context.scenario.locations if item.location_id == "home")
    assert layer == {"layer": "环境层", "observed_at": iso_time(engine.context.state.current_time, engine.context.scenario.timezone),
                     "body": {key: levels[key] for key in ("appetite", "sleepiness", "entertainment", "social",
                                                           "mood")},
                     "location": {"session_kind": "stay", "name": home, "place_type": "家", "fixed_rules": []},
                     "duty": None}
    assert list(layer["body"]) == ["appetite", "sleepiness", "entertainment", "social", "mood"]
    assert all(isinstance(value, str) for value in layer["body"].values())


@pytest.mark.parametrize(("way", "label"), [("步行", "步行"), ("驾车", "驾车")])
def test_transit_names_the_road_to_the_destination_and_the_travel_mode(engine_factory, way: str, label: str) -> None:
    """路程中：session_kind 为 transit，name 为「去<目的地>的路上」（不是出发地），place_type 为出行方式。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, f"{way}-preview"))
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    _, results = submit_round(engine, {1: ("start_activity", start_activity(way, office))}, f"{way}-depart")
    assert results[0].status == "in_progress", results[0].error_code
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    layer = engine.observe(context(engine, f"{way}-road"))["environment_layer"]
    validate_layer(layer)
    places = {item.location_id: item.name for item in engine.context.scenario.locations}
    assert layer["location"] == {"session_kind": "transit", "name": f"去{places['office']}的路上",
                                 "place_type": label, "fixed_rules": []}


def test_fixed_rules_name_every_action_the_menu_excludes(engine_factory) -> None:
    """有效菜单只开放出行、等待与睡觉时，fixed_rules 按固定顺序为其余每个动作写一句「这里不能<说法>」。"""
    engine = engine_factory()
    base = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    narrow = replace(base, menu=InteractionMenu(allowed_actions=("sleep", "travel_to", "wait")))
    layer = environment_layer(narrow, engine.observe(context(engine, "menu"))["body"]["levels"])
    validate_layer(layer)
    excluded = [action for action in ACTION_PHRASES if action not in {"sleep", "travel_to", "wait"}]
    assert layer["location"]["fixed_rules"] == [f"这里不能{ACTION_PHRASES[action]}" for action in excluded]
    assert layer["location"]["fixed_rules"][:2] == ["这里不能工作", "这里不能看内容或听播客"]


def test_place_types_cover_every_scenario_location_kind_and_unknown_kinds_are_defects(engine_factory) -> None:
    """仓库全部生活场景的地点类型都有中文标签；当前地点的类型没有标签时以 IntegrityError 报场景缺陷。

    越界类别在场景加载时就被拒绝（tests/life/test_load_checks.py，同一张 PLACE_TYPES 表）；这里绕过加载直接改运行时
    场景，核对环境层仍保留的运行中兜底。
    """
    kinds = set()
    for name in ("white-collar-12-weeks.json", "multi-person.json", "multi-person-half-day.json"):
        semantics, _ = load_runtime_scenario(Path("scenarios") / name)
        kinds |= {item.kind for item in semantics.scenario.locations}
    assert kinds and kinds <= set(PLACE_TYPES)
    engine = engine_factory()
    base = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    locations = tuple(item.model_copy(update={"kind": "observatory"}) if item.location_id == "home" else item
                      for item in engine.context.scenario.locations)
    broken = replace(base, scenario=engine.context.scenario.model_copy(update={"locations": locations}))
    with pytest.raises(IntegrityError, match="place type label"):
        environment_layer(broken, engine.observe(context(engine, "kind"))["body"]["levels"])
