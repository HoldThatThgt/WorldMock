"""@brief 正式餐馆到店呈现、自提与实际消费路径验证。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan)
from tests.life.test_scheduling import _custom_engine
from worldmock.life.dining import local_food_summary
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.models import ReportStatusRequest, StartActivityRequest
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


def _start(engine, name: str, kind: str, object_id: str | None, action: str):
    """@brief 从冻结观察选取可读对象和动作，让引擎推导领域参数。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == kind and (object_id is None or row["object_id"] == object_id)
                   and any(item["label"] == action for item in row["actions"]))
    request = StartActivityRequest(targets=[binding["object"]], actions=[action])
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
                    ReportStatusRequest(task=[activity_name(engine, activity_id)], summary=["继续进行。"]))
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
        _, started = _start(engine, "to-restaurant", "location", RESTAURANT, "步行前往")
        _run_to_end(engine, started.activity_id)
        agent = engine.state.agents[1]
        assert agent.location_id == RESTAURANT
        assert visible_supplies(engine.state, agent, engine.scenario) == []
        # 到达当秒先给出行收尾；这一轮就是到店后的首次观察（第七章「活动一结束就要结束它」）。
        _conclude(engine, started.activity_id)
        opening = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        if engine.state.current_time < opening:
            assert local_food_summary(engine._discovery_context(agent)) == (
                "当前地点有餐食供给，但现在没有可购买餐食。")
        for step in range(16):
            if engine.state.current_time >= opening:
                break
            _, waiting = _start(engine, f"wait-for-opening-{step}", "self", None, "等待")
            _finish(engine, waiting.activity_id)
        assert engine.state.current_time >= opening
        agent = engine.state.agents[1]
        assert local_food_summary(engine._discovery_context(agent)).startswith("当前地点有可购买餐食")
        before_balance = engine.state.accounts[agent.balance_account_id]
        before_stock = engine.state.supply_stock[SUPPLY]
        before_holding = agent.inventory.get(RESTAURANT, {}).get(MEAL, 0)
        observed, bought = _start(engine, "buy-meal", "supply", SUPPLY, "到店购买")
        offer = next(row for row in observed["interaction_bindings"] if row["object_id"] == SUPPLY)
        assert any(row["object"] == offer["object"] for row in observed["facts"]["products"])
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
