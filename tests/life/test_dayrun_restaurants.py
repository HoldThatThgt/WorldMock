"""@brief 真实到店、固定菜单、配送与进食的连续食品足迹。"""

from __future__ import annotations
import json
import pytest
from tests.integration.test_rrs_engine_wave import _progress_until
from tests.life.conftest import (conclude_request, context, progress_calls, stage_day_plan, start_request,
                                 target_actions, submit_round)
from tests.life.test_work_protocol import trip_to
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.models import SessionStatus, WorldState
from worldmock.life.readable import clock_phrase, iso_time

STORE_ID, FOOD_ID, STORE_FOOD_ID = "noodle-2", "beef-noodles", "noodle-2:beef-noodles"
ZONE = "Asia/Shanghai"


def _action(engine, name, tool, request):
    """@brief 真实人物请求经宿主结算，返回后续观察与实际结果。"""
    _, results = submit_round(engine, {1: (tool, request)}, name)
    assert results[0].status in {"completed", "in_progress"}, results[0].error_code
    return engine.observe(context(engine, f"{name}-after")), results[0]


def _start(engine, name, kind, object_id, way):
    """@brief 当前目录对象和其方式直接生成真实人物请求。"""
    observed = engine.observe(context(engine, f"{name}-preview"))
    binding, verb = next((row, action["verb"]) for row in observed["interaction_bindings"]
                        if row["kind"] == kind and (object_id is None or row["object_id"] == object_id)
                        for action in row["actions"] if action["label"] == way)
    request = start_request(targets=[binding["object"]], actions=[verb], ways=[way])
    _, results = submit_round(engine, {1: ("start_activity", request)}, name)
    assert results[0].status in {"completed", "in_progress"}, results[0].error_code
    return observed, results[0]


def _finish(engine, activity_id):
    """@brief 实际活动走完真实边界后当秒收尾。"""
    _progress_until(engine, engine.context.state.activities[activity_id].planned_end_at)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "completed"
    _action(engine, f"finish-{activity_id}", "conclude_activity", conclude_request(activity.name))


def _stage(engine, location, energy, inventory=None):
    """@brief 无进行中活动的测试起点同时冻结人物位置、时钟和地点会话。"""
    staged = engine.context.state.model_copy(deep=True)
    now = int(engine.context.scenario.start_at.timestamp()) + 10 * 3600 + 1800
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    for agent in staged.agents.values():
        agent.body_settled_at = agent.next_decision_at = now
    agent = staged.agents[1]
    agent.location_id, agent.body.energy_kcal = location, energy
    if inventory is not None:
        agent.inventory = inventory
    for session in staged.sessions.values():
        if session.status == SessionStatus.ACTIVE:
            session.status, session.ended_at = SessionStatus.ENDED, now
    names = {identity: place.display_name for identity, place in engine.context.semantics.catalog.locations.items()}
    for agent_id in staged.agents:
        staged = sessions.open_stay(staged, agent_id, names)
    staged.version += 1
    staged = WorldState.model_validate(staged.model_dump())
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        data={"kind": "test_food_path_position"})])
    engine.context.state = staged


def test_store_arrival_exposes_fixed_menu_and_real_consumption(engine_factory):
    """到店可购买全部固定菜单，进食严格结算真实份数。"""
    engine = engine_factory()
    stage_day_plan(engine)
    trip_to(engine, STORE_ID, "visit-store")
    observed = engine.observe(context(engine, "arrived-menu"))
    local = {row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "supply"
             and row["object_id"].startswith(f"{STORE_ID}:")}
    assert local == {f"{STORE_ID}:{food}" for food in engine.context.semantics.catalog.foods_by_store[STORE_ID]}
    _, bought = _start(engine, "buy-meal", "supply", STORE_FOOD_ID, "到店")
    assert bought.status == "completed"
    _, meal = _start(engine, "eat-meal", "holding", FOOD_ID, "食用")
    assert engine.context.state.activities[meal.activity_id].planned_duration_seconds == 1200
    _finish(engine, meal.activity_id)
    assert engine.context.state.activities[meal.activity_id].data["consumed"] == {FOOD_ID: 1}
    assert engine.context.state.agents[1].inventory[STORE_ID][FOOD_ID] == 0


def test_same_kind_stores_reject_different_food_menus(tmp_path):
    """重发身份也不能让同类店铺出售不同食品。"""
    from tests.life.scenario_fixtures import read_scenario, write_scenario
    from worldmock.life.world_setup import load_runtime_scenario

    path = write_scenario(read_scenario("single-person.json"), tmp_path / "environment")
    foods = path.parent.parent / "catalog" / "foods.json"
    payload = json.loads(foods.read_text())
    next(row for row in payload["foods"] if row["food_id"] == FOOD_ID)["store_ids"] = ["noodle-1"]
    foods.write_text(json.dumps(payload), encoding="utf-8")
    refresh_manifest_identity(foods.parent)
    with pytest.raises(RegistrationError, match="FOOD_BINDING_KIND_INCONSISTENT"):
        load_runtime_scenario(path)


def test_food_paths_are_discoverable_as_tool_layer_targets(engine_factory):
    """此处商品与手边食品可调用，留在家里的食品只能在当地食用。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, STORE_ID, 700, {STORE_ID: {FOOD_ID: 1}, "home": {"chips": 1}})
    observed = engine.observe(context(engine, "food-targets"))
    assert target_actions(observed, "二味小炒出售的牛肉面")["到店"]["available"]
    assert target_actions(observed, "持有物：牛肉面")["食用"]["available"]
    away = next(row["object"] for row in observed["interaction_bindings"]
                if row["kind"] == "holding" and row["object_id"] == "chips")
    assert not target_actions(observed, away)["食用"]["available"]


def test_delivery_eta_is_readable_and_matches_actual_delivery(engine_factory):
    """图距离产生的预计送达时刻与订单、公开回执和实际送达一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed, bought = _start(engine, "deliver-home", "supply", "noodle-1:beef-noodles", "远程下单")
    order = engine.context.state.deliveries[bought.data["orders"][0]["order_id"]]
    listed = target_actions(observed, "一心面馆出售的牛肉面")["远程下单"]
    assert listed["note"] == f"预计 {clock_phrase(order.expected_at, observed['current_time'], ZONE)} 送到"
    after = engine.observe(context(engine, "delivery-receipt"))
    purchase = progress_calls(after, "start_activity")[-1]["result"]["details"]["purchases"][0]
    assert purchase["expected_at"] == iso_time(order.expected_at, ZONE)
    _progress_until(engine, order.expected_at)
    assert engine.context.state.deliveries[order.order_id].status == "delivered"
    assert engine.context.state.agents[1].inventory["home"][FOOD_ID] == 1


def test_no_app_store_rejects_remote_delivery_without_changing_accounts(engine_factory):
    """无 app 店铺的远程拒绝不扣款、不生成订单。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.context.state.accounts.copy()
    request = start_request(targets=["二味小炒出售的牛肉面"], actions=["obtain"], ways=["远程下单"])
    _, results = submit_round(engine, {1: ("start_activity", request)}, "no-app-order")
    assert results[0].status == "rejected"
    assert engine.context.state.accounts == before and engine.context.state.deliveries == {}
