"""可读对象动作合同在真实观察、菜单和共同结算中的验收。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import context, stage_day_plan
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


def test_observation_exposes_semantic_choices_and_keeps_bindings_private(engine_factory):
    """人物所见没有内部对象编号、报价版本或路线秒数，执行绑定只在私有观察。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "semantic-observation"))
    rows = observed["facts"]["interactive_objects"]
    assert rows
    assert all(set(row) == {"object", "actions", "preconditions_met"} for row in rows)
    assert all("object_id" not in row and "price_version" not in row for row in rows)
    assert all(row["object"] and all(action for action in row["actions"]) for row in rows)
    assert {row["object"] for row in rows} == {
        row["object"] for row in observed["interaction_bindings"]}
    travel = _binding(observed, "location", "travel_to")
    assert {item["label"] for item in travel["actions"]} <= {"步行前往", "驾车前往"}


def test_travel_uses_exact_frozen_route_seconds(engine_factory):
    """人物只选目的地和方式；引擎使用本轮路线合同计算精确时长。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "travel-preview"))
    location = _binding(preview, "location", "travel_to")
    choice = next(item for item in location["actions"] if item["travel_mode"] == "walking")
    request = StartActivityRequest(targets=[location["object"]], actions=[choice["label"]])
    _, result = _observed_action(engine, "travel-exact", request)
    assert result.status == "in_progress", result.error_code
    activity = engine.state.activities[result.activity_id]
    route = next(route for route in engine.scenario.routes
                 if route.origin_id == "home" and route.destination_id == location["object_id"]
                 and route.mode.value == choice["travel_mode"])
    expected = engine.semantics.travel_planned_seconds(
        choice["travel_mode"], route.duration_seconds, activity.started_at)
    assert activity.planned_duration_seconds == expected


def test_menu_rejects_unoffered_semantic_action_before_travel(engine_factory):
    """绕过模型 Schema 直接提交被地点菜单剪掉的动作也不能启动或扣费。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "denied-preview"))
    location = _binding(preview, "location", "travel_to")
    menus = engine.scenario.interaction_menus_by_location_kind.copy()
    home = menus["home"]
    menus["home"] = home.model_copy(update={"allowed_actions": tuple(
        action for action in home.allowed_actions if action != "travel_to")})
    engine.scenario = engine.scenario.model_copy(update={"interaction_menus_by_location_kind": menus})
    request = StartActivityRequest(targets=[location["object"]], actions=["步行前往"])
    before = engine.state.accounts.copy(), engine.state.activities.copy()
    _, result = _observed_action(engine, "denied-travel", request)
    assert result.error_code == "INTERACTION_MENU_DENIED"
    assert (engine.state.accounts, engine.state.activities) == before
