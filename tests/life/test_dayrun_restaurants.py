"""@brief 正式餐馆到店呈现、自提与实际消费路径验证。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan
from tests.life.test_scheduling import _custom_engine
from worldmock.life.dining import local_food_summary
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.models import (
    ConsumeItem, ConsumeRequest, ContinueActivityRequest, PurchaseItem,
    PurchaseOrderRequest, StartBasicActivityRequest, StartTravelRequest,
)
from worldmock.life.projection import visible_supplies

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


def _finish(engine, activity_id: str) -> None:
    """@brief 只通过真实时间边界推进持续活动到完成。"""
    for step in range(64):
        if engine.state.activities[activity_id].status == "completed":
            return
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"continue-{activity_id}-{step}", "continue_activity",
                    ContinueActivityRequest(activity_id=activity_id))
        else:
            advance_next(engine)
    raise AssertionError("Activity did not complete within its event boundaries")


def test_restaurant_arrival_immediately_exposes_pickup_and_consumption(tmp_path: Path) -> None:
    """@brief 真实出行后首轮可买，付款取得与实际进食各有独立证据。"""
    def set_lunch(document: dict) -> None:
        """@brief 选择三家餐馆均营业的正式实验时刻。"""
        document["start_at"] = "2026-01-05T08:00:00+08:00"

    engine = _custom_engine(SCENARIO, set_lunch, tmp_path, run_id="dayrun-restaurant")
    try:
        stage_day_plan(engine)
        agent = engine.state.agents[1]
        assert local_food_summary(engine._discovery_context(agent)) == "当前地点没有餐食供给。"
        route = next(item for item in engine.scenario.routes
                     if item.origin_id == "home" and item.destination_id == RESTAURANT
                     and item.mode.value == "walking")
        duration = engine.semantics.travel_planned_seconds(
            "walking", route.duration_seconds, engine.state.current_time)
        _, started = _action(engine, "to-restaurant", "start_travel", StartTravelRequest(
            destination_id=RESTAURANT, travel_mode="walking", planned_duration_seconds=duration))
        _finish(engine, started.activity_id)
        agent = engine.state.agents[1]
        assert agent.location_id == RESTAURANT
        assert visible_supplies(engine.state, agent, engine.scenario) == []
        opening = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        if engine.state.current_time < opening:
            assert local_food_summary(engine._discovery_context(agent)) == (
                "当前地点有餐食供给，但现在没有可购买餐食。")
            duration = opening - engine.state.current_time
            _, waiting = _action(engine, "wait-for-opening", "start_basic_activity",
                                 StartBasicActivityRequest(activity_type="wait",
                                                           planned_duration_seconds=duration))
            _finish(engine, waiting.activity_id)
            agent = engine.state.agents[1]
        assert local_food_summary(engine._discovery_context(agent)).startswith("当前地点有可购买餐食")
        quote = engine.state.supply_quotes[SUPPLY]
        before_balance = engine.state.accounts[agent.balance_account_id]
        before_stock = engine.state.supply_stock[SUPPLY]
        before_holding = agent.inventory.get(RESTAURANT, {}).get(MEAL, 0)
        observed, bought = _action(engine, "buy-meal", "purchase", PurchaseOrderRequest(
            items=(PurchaseItem(supply_id=SUPPLY, quantity=1,
                                price_version=quote.price_version),),
            fulfillment="pickup", destination_id=RESTAURANT))
        assert any(row["supply_id"] == SUPPLY for row in observed["facts"]["products"])
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
        duration = engine.rules.products[MEAL].use_duration_seconds
        _, eating = _action(engine, "eat-meal", "consume", ConsumeRequest(
            items=(ConsumeItem(product_id=MEAL, quantity=1),), use_mode="eat",
            duration_seconds=duration))
        _finish(engine, eating.activity_id)
        assert engine.state.activities[eating.activity_id].data["consumed"][MEAL] == 1
        assert engine.state.agents[1].inventory[RESTAURANT].get(MEAL, 0) == before_holding
        sold_out = engine.state.model_copy(deep=True)
        sold_out.supply_stock[SUPPLY] = 0
        empty_context = DiscoveryContext(sold_out, engine.scenario, engine.semantics,
                                         sold_out.agents[1])
        assert local_food_summary(empty_context) == "当前地点有餐食供给，但现在没有可购买餐食。"
    finally:
        engine.close()


def test_restaurant_without_selected_supply_is_a_valid_empty_menu(tmp_path: Path) -> None:
    """@brief 通用地图可以保留餐馆地点而不选择任何餐食供给。"""
    def empty_menu(document: dict) -> None:
        """@brief 只从实验选择中移去餐馆供给。"""
        document["supplies"] = [item for item in document["supplies"]
                                if item["supply_id"] != SUPPLY]

    engine = _custom_engine(SCENARIO, empty_menu, tmp_path, run_id="empty-restaurant")
    try:
        agent = engine.state.agents[1].model_copy(update={"location_id": RESTAURANT}, deep=True)
        local = DiscoveryContext(engine.state, engine.scenario, engine.semantics, agent)
        assert local_food_summary(local) == "当前地点没有餐食供给。"
        assert any(item.location_id == RESTAURANT for item in engine.scenario.locations)
        assert not any(item.location_id == RESTAURANT for item in engine.scenario.supplies)
    finally:
        engine.close()
