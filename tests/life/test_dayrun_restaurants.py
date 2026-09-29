"""@brief 正式餐馆到店呈现、自提与实际消费路径验证。"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, stage_day_plan, start_activity, target_actions, tool_targets,
                                 report_request)
from tests.life.test_scheduling import _custom_engine
from worldmock.footprints.models import EventDraft
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.basket import delivery_expected_at
from worldmock.life.models import active_activities
from worldmock.life.projection import visible_supplies
from worldmock.life.readable import clock_phrase, iso_time

SCENARIO = "white-collar-12-weeks.json"
RESTAURANT = "poi-3538660453"
MEAL = "meal-0018"
SUPPLY = "supply-poi-3538660453-meal-0018"


def _action(engine, name: str, tool: str, request):
    """@brief 以真实观察、动作登记和结算运行一个决策。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observed, result


def _start(engine, name: str, kind: str, object_id: str | None, way: str):
    """@brief 从冻结观察选取列出该方式的可读对象，按「动词＋对象＋方式」启动，让引擎推导领域参数。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == kind and (object_id is None or row["object_id"] == object_id)
                   and any(item["label"] == way for item in row["actions"]))
    request = start_activity(way, binding["object"])
    engine.submit_action(DirectToolCall(decision, f"action-{name}", "start_activity", request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observed, result


def _run_to_end(engine, activity_id: str) -> None:
    """@brief 只通过真实时间边界推进持续活动到完成，途中按活动名播报。"""
    for step in range(64):
        if engine.state.activities[activity_id].status == "completed":
            return
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"continue-{activity_id}-{step}", "report_status",
                    report_request(activity_name(engine, activity_id), summary="继续进行。"))
        else:
            advance_next(engine)
    raise AssertionError("Activity did not complete within its event boundaries")


def _conclude(engine, activity_id: str) -> None:
    """@brief 活动结束的那一秒当即收尾（规格第七章「活动一结束就要结束它」）。"""
    _action(engine, f"conclude-{activity_id}", "conclude_activity",
            conclude_request(activity_name(engine, activity_id)))
    assert engine.state.activities[activity_id].concluded_at == engine.state.current_time


def _finish(engine, activity_id: str) -> None:
    """@brief 推进持续活动到完成并当秒收尾。"""
    _run_to_end(engine, activity_id)
    _conclude(engine, activity_id)


def _supply_target(observed: dict, supply_id: str) -> str:
    """@brief 本轮某条供给在工具层的目标名（取自私有绑定的同名对象）。"""
    return next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == supply_id)


def test_restaurant_arrival_immediately_exposes_pickup_and_consumption(tmp_path: Path) -> None:
    """@brief 真实出行后首轮就列出到店取得：未营业时标不可用写「店没开门」，营业后可用；付款取得与实际进食各有独立证据，
    卖完的供给不进呈现窗口（docs/dev/SPEC-persona-prompt.md「工具层」列出即可提交）。"""
    def set_lunch(document: dict) -> None:
        """@brief 选择三家餐馆均营业的正式实验时刻。"""
        document["start_at"] = "2026-01-05T08:00:00+08:00"

    engine = _custom_engine(SCENARIO, set_lunch, tmp_path, run_id="dayrun-restaurant")
    try:
        stage_day_plan(engine)
        _, started = _start(engine, "to-restaurant", "location", RESTAURANT, "步行")
        _run_to_end(engine, started.activity_id)
        agent = engine.state.agents[1]
        assert agent.location_id == RESTAURANT
        assert visible_supplies(engine.state, agent, engine.scenario) == []
        # 到达当秒先给出行收尾；这一轮就是到店后的首次观察（第七章「活动一结束就要结束它」）。
        _conclude(engine, started.activity_id)
        opening = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        if engine.state.current_time < opening:
            closed = engine._observation_data(engine.state.agents[1])
            pickup = target_actions(closed, _supply_target(closed, SUPPLY))["到店"]
            assert (pickup["available"], pickup["reason"]) == (False, "店没开门")
        for step in range(16):
            if engine.state.current_time >= opening:
                break
            _, waiting = _start(engine, f"wait-for-opening-{step}", "self", None, "等待")
            _finish(engine, waiting.activity_id)
        assert engine.state.current_time >= opening
        agent = engine.state.agents[1]
        before_balance = engine.state.accounts[agent.balance_account_id]
        before_stock = engine.state.supply_stock[SUPPLY]
        before_holding = agent.inventory.get(RESTAURANT, {}).get(MEAL, 0)
        observed, bought = _start(engine, "buy-meal", "supply", SUPPLY, "到店")
        listed = target_actions(observed, _supply_target(observed, SUPPLY))["到店"]
        assert listed["available"] is True and listed["price"].endswith(" 元") and listed["quantity"] == "1 份"
        assert SUPPLY in engine.state.agents[1].visible_supply_ids
        order = bought.data["orders"][0]
        assert order["status"] == "delivered"
        assert order["destination_id"] == RESTAURANT
        assert engine.state.accounts[agent.balance_account_id] == before_balance - order["total_price_fen"]
        assert engine.state.supply_stock[SUPPLY] == before_stock - 1
        assert engine.state.agents[1].inventory[RESTAURANT][MEAL] == before_holding + 1
        assert not any(event.data.get("activity", {}).get("data", {}).get("consumed")
                       for event in engine.store.read_agent_events("activity_progress", 1))
        assert advance_until_ready(engine)
        _, eating = _start(engine, "eat-meal", "holding", MEAL, "食用")
        _finish(engine, eating.activity_id)
        assert engine.state.activities[eating.activity_id].data["consumed"][MEAL] == 1
        assert engine.state.agents[1].inventory[RESTAURANT].get(MEAL, 0) == before_holding
        _commit(engine, lambda staged: staged.supply_stock.update({SUPPLY: 0}))
        sold_out = engine._observation_data(engine.state.agents[1])
        assert not any(row["object_id"] == SUPPLY for row in sold_out["interaction_bindings"])
    finally:
        engine.close()


def test_restaurant_without_selected_supply_is_a_valid_empty_menu(tmp_path: Path) -> None:
    """@brief 通用地图可以保留餐馆地点而不选择任何餐食供给：人在店里时工具层没有这家店的商品目标。"""
    def empty_menu(document: dict) -> None:
        """@brief 只从实验选择中移去餐馆供给。"""
        document["supplies"] = [item for item in document["supplies"]
                                if item["supply_id"] != SUPPLY]

    engine = _custom_engine(SCENARIO, empty_menu, tmp_path, run_id="empty-restaurant")
    try:
        assert any(item.location_id == RESTAURANT for item in engine.scenario.locations)
        assert not any(item.location_id == RESTAURANT for item in engine.scenario.supplies)
        _commit(engine, lambda staged: staged.agents[1].__setattr__("location_id", RESTAURANT))
        observed = engine._observation_data(engine.state.agents[1])
        name = next(item.name for item in engine.scenario.locations if item.location_id == RESTAURANT)
        assert observed["environment_layer"]["location"]["name"] == name
        assert not any(row["name"].startswith(f"{name}出售的") for row in observed["tool_layer"]["targets"])
    finally:
        engine.close()


def _commit(engine, mutate) -> None:
    """@brief 改造一份世界副本并经存储落盘（版本连续）。"""
    staged = engine.state.model_copy(deep=True)
    mutate(staged)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_restaurant_staged"})])
    engine.state = staged


# ---------------------------------------------------------------------------
# 吃饭途径由工具层目标表达（docs/dev/SPEC-persona-prompt.md「工具层」，取代「让人物趁早吃」的途径句）
# ---------------------------------------------------------------------------

ZONE = "Asia/Shanghai"


def _stage(engine, location: str, energy: float, inventory: dict | None = None) -> None:
    """@brief 把人物放到指定地点、能量与持有物并持久提交（营业时段内的首日 10:30）。"""
    staged = engine.state.model_copy(deep=True)
    now = int(engine.scenario.start_at.timestamp()) + 10 * 3600 + 1800
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    agent = staged.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": energy}, deep=True)
    staged.agents[1] = agent.model_copy(update={
        "location_id": location, "body": body, "body_settled_at": now, "next_decision_at": now,
        "inventory": inventory or {}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        data={"kind": "test_food_path_position"})])
    engine.state = staged


def test_food_paths_are_discoverable_as_tool_layer_targets(engine_factory) -> None:
    """@brief 吃饭途径都在工具层：这里能买的是商品目标（到店可用，状态写每份热量）；手边的食品是持有物目标（食用可用）；
    留在别处的食品名称带地点，食用标不可用并写明只能在当地用；没有任何途径时商品目录的浏览照常可用。"""
    engine = engine_factory()
    stage_day_plan(engine)
    meal = next(item for item in engine.scenario.products if item.product_id == MEAL)
    restaurant = next(item.name for item in engine.scenario.locations if item.location_id == RESTAURANT)
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    _stage(engine, RESTAURANT, 700)
    observed = engine._observation_data(engine.state.agents[1])
    target = tool_targets(observed)[f"{restaurant}出售的{meal.name}"]
    assert target["kind"] == "product" and target["state"].startswith("每份约")
    assert target_actions(observed, target["name"])["到店"]["available"] is True
    _stage(engine, "home", 700, {"home": {MEAL: 1}})
    observed = engine._observation_data(engine.state.agents[1])
    held = tool_targets(observed)[f"持有物：{meal.name}"]
    assert held["state"].startswith("持有 1 份")
    assert target_actions(observed, held["name"])["食用"]["available"] is True
    _stage(engine, "home", 700, {RESTAURANT: {MEAL: 1}})
    observed = engine._observation_data(engine.state.agents[1])
    away = target_actions(observed, f"持有物：{meal.name}（{restaurant}）")["食用"]
    assert (away["available"], away["reason"]) == (False, f"这件东西在{restaurant}，只能在当地用")
    assert f"持有物：{meal.name}" not in tool_targets(observed)
    assert target_actions(observed, "商品目录")["浏览"]["available"] is True
    assert home == observed["environment_layer"]["location"]["name"]


def _keep_waiting(engine) -> tuple[str, object]:
    """@brief 就绪时的下一请求：先给刚结束的等待收尾（规格第七章「活动一结束就要结束它」），在等就播报，否则开始等待。"""
    waiting = pending_conclusions(engine.state, 1)
    if waiting:
        return "conclude_activity", conclude_request(*(activity_name(engine, item.activity_id) for item in waiting))
    running = active_activities(engine.state, 1)
    if running:
        return "report_status", report_request(activity_name(engine, running[0].activity_id), summary="接着等。")
    return "start_activity", start_activity("等待", "自己")


def test_delivery_eta_is_readable_and_matches_actual_delivery(engine_factory) -> None:
    """@brief 远程下单动作的 note 写送到这里的预计时刻「预计 HH:MM 送到」，与下单后的实际送达时刻一致；在途订单进入进度层
    该调用的结果，送达时进度层出现一次 delivery 记录。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 700)
    observed, bought = _start(engine, "deliver-home", "supply", None, "远程下单")
    order = bought.data["orders"][0]
    listed = target_actions(observed, _supply_target(observed, order["supply_id"]))["远程下单"]
    now = observed["current_time"]
    assert listed["note"] == f"预计 {clock_phrase(order['expected_at'], now, ZONE)} 送到"
    assert engine.state.deliveries[order["order_id"]].expected_at == order["expected_at"]
    after = engine._observation_data(engine.state.agents[1])
    (purchase,) = progress_calls(after, "start_activity")[-1]["result"]["details"]["purchases"]
    assert purchase["expected_at"] == iso_time(order["expected_at"], ZONE)
    # 实际送达：沿真实边界推进（人物其间只等待），订单在预计送达的那一秒转为已送达。
    assert order["expected_at"] > now and engine.state.deliveries[order["order_id"]].status == "pending"
    for step in range(64):
        if engine.state.deliveries[order["order_id"]].status != "pending":
            break
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"wait-delivery-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    assert engine.state.deliveries[order["order_id"]].status == "delivered"
    assert engine.state.current_time == order["expected_at"]


def test_delivery_is_unavailable_here_without_destination_or_route(engine_factory) -> None:
    """@brief 送达地不在可配送目的地、或配送道路未知时没有预计送达时刻；工具层的远程下单标不可用并写原因，不写送达时刻。"""
    engine = engine_factory()
    supply = next(item for item in engine.scenario.supplies
                  if item.location_id == "market" and "delivery" in item.fulfillment_modes)
    now = engine.state.current_time
    assert delivery_expected_at(engine.semantics, supply, "home", now) is not None
    narrowed = supply.model_copy(update={"delivery_destination_ids": ("office",)})
    assert delivery_expected_at(engine.semantics, narrowed, "home", now) is None
    route = next(item for item in engine.scenario.routes if item.origin_id == "market"
                 and item.destination_id == "home" and item.mode.value == "driving")
    blocked = route.model_copy(update={"status": "unknown", "distance_meters": None, "duration_seconds": None,
                                       "fare_fen": None, "reason": "no_route_record"})
    scenario = engine.scenario.model_copy(update={"routes": tuple(
        blocked if item.route_id == route.route_id else item for item in engine.scenario.routes)})
    engine.semantics = dataclasses.replace(engine.semantics, scenario=scenario)
    engine.scenario = scenario
    assert delivery_expected_at(engine.semantics, supply, "home", now) is None
    stage_day_plan(engine)
    _stage(engine, "home", 1500)

    def browsed(staged) -> None:
        """@brief 这条供给刚在家里浏览过，本轮呈现。"""
        agent = staged.agents[1]
        agent.visible_supply_ids = {supply.supply_id}
        agent.supply_browsed_at = {supply.supply_id: staged.current_time}
        agent.supply_browsed_location = {supply.supply_id: "home"}

    _commit(engine, browsed)
    observed = engine._observation_data(engine.state.agents[1])
    remote = target_actions(observed, _supply_target(observed, supply.supply_id))["远程下单"]
    assert (remote["available"], remote["reason"], remote["note"]) == (False, "暂时送不到这里", None)
