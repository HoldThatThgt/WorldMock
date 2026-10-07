"""可读对象动作合同在真实观察、菜单和共同结算中的验收。"""

from __future__ import annotations

from dataclasses import replace

from agentsociety2.society import DirectToolCall

from tests.life.conftest import context, stage_day_plan, start_request
from worldmock.life.vocabulary import LIFE_VERBS, WAYS
from worldmock.life.models import StartActivityRequest


def _observed_action(engine, round_id: str, request: StartActivityRequest):
    """@brief 在真实观察事件后提交一个人物动作并共同结算。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    call = DirectToolCall(decision, f"action-{round_id}", "start_activity", request)
    engine.submit_action(call)
    return observed, engine.commit_decision_round(round_id)[0]


def _binding(observation: dict, kind: str, action: str | None = None) -> dict:
    """@brief 从本轮私有绑定取一个具备指定动作的对象。"""
    return next(row for row in observation["interaction_bindings"]
                if row["kind"] == kind and (action is None or any(
                    item["canonical_action"] == action for item in row["actions"])))


def _deny_travel_at_home(engine) -> None:
    """@brief 把家的地点菜单收窄为不含前往（权威菜单随场景重算）。"""
    menus = engine.context.scenario.interaction_menus_by_location_kind.copy()
    home = menus["home"]
    menus["home"] = home.model_copy(update={"allowed_actions": tuple(
        action for action in home.allowed_actions if action != "travel_to")})
    scenario = engine.context.scenario.model_copy(update={"interaction_menus_by_location_kind": menus})
    engine.context.semantics = replace(engine.context.semantics, scenario=scenario)
    engine.context.rules.scenario = scenario


def test_observation_exposes_semantic_choices_and_keeps_bindings_private(engine_factory):
    """人物所见只有工具层的目标、动词与方式及其可用性与说明；没有内部编号、报价版本、路线秒数或 preconditions_met。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "semantic-observation"))
    rows = [row for row in observed["tool_layer"]["targets"] if row["kind"] != "activity"]
    assert rows
    assert all(set(row) == {"name", "kind", "phase", "state", "actions", "tools"} or row["kind"] == "task"
               for row in rows)
    assert all(row["name"] for row in rows)
    for row in rows:
        for action in row["actions"]:
            assert set(action) == {"action", "way", "app", "available", "reason", "price", "duration", "quantity", "note"}
            assert action["action"] in LIFE_VERBS and WAYS[action["way"]][0] == action["action"]
            assert action["reason"] is None or isinstance(action["reason"], str) and action["reason"]
    assert "preconditions_met" not in str(observed["tool_layer"])
    assert all("preconditions_met" not in row for row in observed["interaction_bindings"])
    assert [row["name"] for row in rows] == [row["object"] for row in observed["interaction_bindings"]]
    own = next(row for row in rows if row["name"] == "自己")
    assert {(item["action"], item["way"]) for item in own["actions"]} == {("interact", "等待"), ("sleep", "睡觉")}
    travel = _binding(observed, "location", "travel_to")
    assert {item["label"] for item in travel["actions"]} <= {"步行", "驾车"}
    assert all(set(item) >= {"verb", "label", "canonical_action", "travel_mode", "note"}
               for item in travel["actions"])
    public = next(row for row in rows if row["name"] == travel["object"])
    assert {(item["action"], item["way"]) for item in public["actions"]} == {
        ("travel", item["label"]) for item in travel["actions"]}
    assert ("travel", "步行") in {(item["action"], item["way"]) for row in rows for item in row["actions"]}


def test_travel_uses_exact_frozen_route_seconds(engine_factory):
    """人物只选目的地、动词 travel 与方式；引擎使用本轮路线合同计算精确时长。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "travel-preview"))
    location = _binding(preview, "location", "travel_to")
    choice = next(item for item in location["actions"] if item["travel_mode"] == "walking")
    assert choice["label"] == "步行" and choice["verb"] == "travel"
    request = start_request(targets=[location["object"]], actions=["travel"], ways=[choice["label"]])
    _, result = _observed_action(engine, "travel-exact", request)
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name.startswith(f"步行前往{location['object'].removeprefix('地点：')}")
    from worldmock.exchange.operators import travel_seconds
    graph = engine.context.semantics.catalog.graph
    meters = graph.distance_meters('home', location['object_id'])
    expected = travel_seconds(choice['travel_mode'], meters)
    assert activity.data['path'] == list(graph.path('home', location['object_id']))
    assert activity.planned_duration_seconds == expected


def test_menu_rejects_unoffered_semantic_action_before_travel(engine_factory):
    """菜单外的能力不列出，绕过 Schema 直接提交以 ACTION_REQUEST_INVALID 拒绝；观察之后菜单收窄时，共同结算按
    权威菜单以 INTERACTION_MENU_DENIED 拒绝。两种情况都不启动、不扣费。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "denied-preview"))
    location = _binding(preview, "location", "travel_to")
    request = start_request(targets=[location["object"]], actions=["travel"], ways=["步行"])
    _deny_travel_at_home(engine)
    before = engine.context.state.accounts.copy(), engine.context.state.activities.copy()
    observed, result = _observed_action(engine, "denied-travel", request)
    listed = next(row for row in observed["tool_layer"]["targets"] if row["name"] == location["object"])
    assert listed["actions"] == []
    assert observed["environment_layer"]["location"]["fixed_rules"] == ["这里不能出发去别处"]
    assert result.error_code == "ACTION_REQUEST_INVALID"
    assert (engine.context.state.accounts, engine.context.state.activities) == before

    late = engine_factory()
    stage_day_plan(late)
    decision = context(late, "late-menu")
    late.begin_decision_round([decision])
    offered = late.observe(decision)
    assert any(item["canonical_action"] == "travel_to"
               for item in _binding(offered, "location", "travel_to")["actions"])
    _deny_travel_at_home(late)
    before = late.context.state.accounts.copy(), late.context.state.activities.copy()
    late.submit_action(DirectToolCall(decision, "action-late-menu", "start_activity", request))
    result = late.commit_decision_round("late-menu")[0]
    assert result.error_code == "INTERACTION_MENU_DENIED"
    assert (late.context.state.accounts, late.context.state.activities) == before


def test_availability_judges_the_menu_first_with_the_admission_reason(engine_factory):
    """可用性与受理同序、菜单在先（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）：观察之后菜单收窄，按本轮冻结绑定重算的
    可用性把被收窄的前往标为不可用，原因与共同结算以 INTERACTION_MENU_DENIED 拒绝时的原因一字不差。"""
    engine = engine_factory()
    stage_day_plan(engine)
    decision = context(engine, "menu-first")
    engine.begin_decision_round([decision])
    offered = engine.observe(decision)
    location = _binding(offered, "location", "travel_to")
    _deny_travel_at_home(engine)
    recomputed = engine.observations._layer_context(engine.context.state.agents[1], offered["interaction_bindings"])
    note = next(action["note"] for row in recomputed.bindings if row["object"] == location["object"]
                for action in row["actions"] if action["label"] == "步行")
    request = start_request(targets=[location["object"]], actions=["travel"], ways=["步行"])
    engine.submit_action(DirectToolCall(decision, "action-menu-first", "start_activity", request))
    result = engine.commit_decision_round("menu-first")[0]
    assert result.error_code == "INTERACTION_MENU_DENIED"
    assert note == result.data["reason"] == "The current location does not admit this action."
