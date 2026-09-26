"""@brief 正式餐馆到店呈现、自提与实际消费路径验证。"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity)
from tests.life.test_scheduling import _custom_engine
from worldmock.footprints.models import EventDraft
from worldmock.life import dining, projection
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.basket import delivery_expected_at
from worldmock.life.dining import FoodPathFacts, MealOption, NearbyFood, PendingMeal, local_meal_sentence
from worldmock.life.discovery import DiscoveryContext, supply_row
from worldmock.life.models import ReportStatusRequest, active_activities
from worldmock.life.projection import visible_supplies
from worldmock.life.result_projection import public_supply_row

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
        assert local_meal_sentence(engine._discovery_context(agent)) == "当前地点没有餐食供给。"
        _, started = _start(engine, "to-restaurant", "location", RESTAURANT, "步行")
        _run_to_end(engine, started.activity_id)
        agent = engine.state.agents[1]
        assert agent.location_id == RESTAURANT
        assert visible_supplies(engine.state, agent, engine.scenario) == []
        # 到达当秒先给出行收尾；这一轮就是到店后的首次观察（第七章「活动一结束就要结束它」）。
        _conclude(engine, started.activity_id)
        opening = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        if engine.state.current_time < opening:
            assert local_meal_sentence(engine._discovery_context(agent)) == (
                "当前地点有餐食供给，但现在没有可购买餐食。")
        for step in range(16):
            if engine.state.current_time >= opening:
                break
            _, waiting = _start(engine, f"wait-for-opening-{step}", "self", None, "等待")
            _finish(engine, waiting.activity_id)
        assert engine.state.current_time >= opening
        agent = engine.state.agents[1]
        assert local_meal_sentence(engine._discovery_context(agent)).startswith("当前地点有可购买餐食")
        before_balance = engine.state.accounts[agent.balance_account_id]
        before_stock = engine.state.supply_stock[SUPPLY]
        before_holding = agent.inventory.get(RESTAURANT, {}).get(MEAL, 0)
        observed, bought = _start(engine, "buy-meal", "supply", SUPPLY, "到店")
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
        assert local_meal_sentence(empty_context) == "当前地点有餐食供给，但现在没有可购买餐食。"
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
        assert local_meal_sentence(local) == "当前地点没有餐食供给。"
        assert any(item.location_id == RESTAURANT for item in engine.scenario.locations)
        assert not any(item.location_id == RESTAURANT for item in engine.scenario.supplies)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# 饿时的吃饭途径句与人读配送时刻（docs/dev/SPEC-time-and-energy.md「让人物趁早吃」、第八章「呈现」）
# ---------------------------------------------------------------------------

NOW = 1_767_571_200  # 2026-01-05 08:00 Asia/Shanghai
ZONE = "Asia/Shanghai"


def _facts(**fields) -> FoodPathFacts:
    """@brief 以 08:00 为现在、原当地餐食句为「当前地点没有餐食供给。」构造途径事实。"""
    return FoodPathFacts(now=NOW, timezone=ZONE, local_sentence="当前地点没有餐食供给。", **fields)


ALL_PATHS = dict(on_hand=(MealOption("番茄炒蛋", 300),), pending=(PendingMeal("五指毛桃鸡煲", NOW + 1800),),
                 here=(MealOption("牛肉面", 650),), deliverable=(NOW + 1800,),
                 nearby=(NearbyFood("好德便利店", 8, "walking"),), browse_available=True)


def test_below_hungry_keeps_the_local_meal_sentence() -> None:
    """@brief 饥饿低于「饿」时保留原当地餐食句，即使本轮有吃饭途径。"""
    for hunger in ("不饿", "有点想吃"):
        assert dining.local_food_summary(hunger, _facts(**ALL_PATHS)) == "当前地点没有餐食供给。"


@pytest.mark.parametrize("dropped, expected", [
    ((), "你手边有番茄炒蛋，可以直接吃。"),
    (("on_hand",), "你点的五指毛桃鸡煲预计今天 08:30 送到。"),
    (("on_hand", "pending"), "这里就能买到牛肉面。"),
    (("on_hand", "pending", "here"), "列出的餐食里，有一样能送到这里，约 30 分钟。"),
    (("on_hand", "pending", "here", "deliverable"), "最近能买到吃的是好德便利店，步行约 8 分钟。"),
    (("on_hand", "pending", "here", "deliverable", "nearby"), "现在不知道附近哪里有吃的，可以浏览商品看看。"),
])
def test_food_path_takes_the_first_path_that_holds(dropped, expected) -> None:
    """@brief 六档依次为手边、在路上、这里能买、能送到、附近能买、都没有，取第一条成立的。"""
    fields = {key: value for key, value in ALL_PATHS.items() if key not in dropped}
    for hunger in ("饿", "很饿", "饿得难受", "饿得发慌"):
        assert dining.local_food_summary(hunger, _facts(**fields)) == expected


def test_food_path_choices_within_a_tier_are_deterministic() -> None:
    """@brief 同档内热量高者优先、同分按名称；在途取最早一单；附近取最短分钟、同分按地点名。"""
    facts = _facts(here=(MealOption("馒头", 220), MealOption("牛肉面", 650), MealOption("拉面", 650)))
    assert dining.local_food_summary("饿", facts) == "这里就能买到拉面。"
    facts = _facts(on_hand=(MealOption("饼干", 480), MealOption("苹果", 80)))
    assert dining.local_food_summary("饿", facts) == "你手边有饼干，可以直接吃。"
    facts = _facts(nearby=(NearbyFood("好德便利店", 8, "walking"), NearbyFood("全家便利店", 8, "driving"),
                           NearbyFood("便利蜂", 15, "walking")))
    assert "全家便利店" < "好德便利店"  # 同分按地点名（码位序）
    assert dining.local_food_summary("饿", facts) == "最近能买到吃的是全家便利店，驾车约 8 分钟。"
    facts = _facts(deliverable=(NOW + 5400, NOW + 1800, NOW + 3600))
    assert dining.local_food_summary("饿", facts) == "列出的餐食里，有几样能送到这里，最快约 30 分钟。"
    facts = _facts(pending=(PendingMeal("晚饭", NOW + 7200, "家"), PendingMeal("午饭", NOW + 600, "办公室")))
    assert dining.local_food_summary("饿", facts) == "你点的午饭预计今天 08:10 送到办公室。"
    assert dining.local_food_summary("饿", _facts()) == "现在不知道附近哪里有吃的。"


def test_late_delivery_adds_the_first_nearer_alternative() -> None:
    """@brief 在途送达晚于 3600 秒时另补第 3、4、5 档中第一条成立的；恰好 3600 秒或没有替代途径时不补。"""
    late = PendingMeal("五指毛桃鸡煲", NOW + 27 * 3600 + 960)
    expected = "你点的五指毛桃鸡煲预计明天 11:16 送到。"
    assert dining.local_food_summary("很饿", _facts(pending=(late,), deliverable=(NOW + 1800,),
                                                  nearby=ALL_PATHS["nearby"])) == (
        expected + "列出的餐食里，有一样能送到这里，约 30 分钟。")
    assert dining.local_food_summary("很饿", _facts(pending=(late,), nearby=ALL_PATHS["nearby"])) == (
        expected + "最近能买到吃的是好德便利店，步行约 8 分钟。")
    assert dining.local_food_summary("很饿", _facts(pending=(late,))) == expected
    on_time = PendingMeal("盒饭", NOW + 3600)
    assert dining.local_food_summary("饿", _facts(pending=(on_time,), here=ALL_PATHS["here"])) == (
        "你点的盒饭预计今天 09:00 送到。")


def test_readable_duration_and_clock() -> None:
    """@brief 人读时长与时刻：不到一分钟、分钟、小时；今天、明天、后天或月日。"""
    assert dining.readable_duration(59) == "不到 1 分钟"
    assert dining.readable_duration(60) == "约 1 分钟"
    assert dining.readable_duration(1790) == "约 30 分钟"
    assert dining.readable_duration(3599) == "约 1 小时"
    assert dining.readable_duration(13 * 3600) == "约 13 小时"
    assert dining.readable_clock(NOW, NOW + 60, ZONE) == "今天 08:01"
    assert dining.readable_clock(NOW, NOW + 86_400, ZONE) == "明天 08:00"
    assert dining.readable_clock(NOW, NOW + 2 * 86_400, ZONE) == "后天 08:00"
    assert dining.readable_clock(NOW, NOW + 3 * 86_400, ZONE) == "1月8日 08:00"


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


def test_scene_food_path_quotes_only_this_observation(engine_factory) -> None:
    """@brief 饿时场景的途径句引用本轮观察：手边持有的食品、这里呈现的供给，或都没有时提示浏览。"""
    engine = engine_factory()
    stage_day_plan(engine)
    meal = next(item for item in engine.scenario.products if item.product_id == MEAL)
    _stage(engine, RESTAURANT, 700)
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["body"]["levels"]["appetite"] in dining.FOOD_PATH_LEVELS
    assert observed["scene"]["local_food_summary"] == f"这里就能买到{meal.name}。"
    assert meal.name in {row["product"] for row in observed["facts"]["products"]}
    _stage(engine, "home", 700, {"home": {MEAL: 1}})
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["scene"]["local_food_summary"] == f"你手边有{meal.name}，可以直接吃。"
    assert meal.name in observed["scene"]["inventory"]
    _stage(engine, "home", 700, {RESTAURANT: {MEAL: 1}})
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["scene"]["local_food_summary"] == "现在不知道附近哪里有吃的，可以浏览商品看看。"
    _stage(engine, "home", 1500)
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["body"]["levels"]["appetite"] not in dining.FOOD_PATH_LEVELS
    assert observed["scene"]["local_food_summary"] == "当前地点没有餐食供给。"


def _hungry_after_browsing_at_home(engine) -> None:
    """@brief 饿着在家浏览商品目录直到浏览结束：手边没有食品、没有在途订单、家里没有供给。"""
    stage_day_plan(engine)
    _stage(engine, "home", 700)
    _, browsing = _action(engine, "browse-at-home", "start_activity",
                          start_activity("浏览", "商品目录"))
    _finish(engine, browsing.activity_id)
    assert engine.state.agents[1].location_id == "home" and not engine.state.deliveries


def _listed_food_rows(observed: dict) -> list[dict]:
    """@brief 本轮 facts.products 中可购买的食品行（食品行带每份热量）。"""
    return [row for row in observed["facts"]["products"] if "energy" in row and row["purchase_allowed"]]


def test_scene_food_path_names_the_fastest_delivery_to_here(engine_factory) -> None:
    """@brief 第 4 档：在家浏览后，途径句的分钟数取列出的食品中最早送达的一项（与下单结算同一算法）。"""
    engine = engine_factory()
    _hungry_after_browsing_at_home(engine)
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["body"]["levels"]["appetite"] in dining.FOOD_PATH_LEVELS
    now = observed["current_time"]
    supplies = {item.supply_id: item for item in engine.scenario.supplies}
    listed = {row["object"]: row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    arrivals = {row["object"]: delivery_expected_at(engine.semantics, supplies[listed[row["object"]]], "home", now)
                for row in _listed_food_rows(observed) if "delivery" in row["fulfillment_modes"]}
    arrivals = {name: moment for name, moment in arrivals.items() if moment is not None}
    assert len(arrivals) > 1
    fastest = min(arrivals.values())
    assert observed["scene"]["local_food_summary"] == (
        f"列出的餐食里，有几样能送到这里，最快{dining.readable_duration(fastest - now)}。")
    assert any(row["delivery_eta"] == f"{dining.readable_duration(fastest - now)}后送达，"
                                      f"{dining.readable_clock(now, fastest, ZONE)}"
               for row in observed["facts"]["products"] if arrivals.get(row["object"]) == fastest)


def test_scene_food_path_names_the_nearest_store_when_nothing_delivers_here(engine_factory) -> None:
    """@brief 第 5 档：列出的食品都送不到当前地点时，途径句取 facts.routes 中可达路线分钟数最短的有食品营业地点。"""
    engine = engine_factory()
    _hungry_after_browsing_at_home(engine)
    supplies = tuple(item.model_copy(update={"delivery_destination_ids": tuple(
        place for place in item.delivery_destination_ids if place != "home")}) for item in engine.scenario.supplies)
    scenario = engine.scenario.model_copy(update={"supplies": supplies})
    engine.semantics = dataclasses.replace(engine.semantics, scenario=scenario)
    engine.scenario = scenario
    observed = engine._observation_data(engine.state.agents[1])
    assert observed["body"]["levels"]["appetite"] in dining.FOOD_PATH_LEVELS
    foods = _listed_food_rows(observed)
    delivery_etas = [row["delivery_eta"] for row in foods if "delivery" in row["fulfillment_modes"]]
    assert delivery_etas and set(delivery_etas) == {"暂时送不到这里"}
    stores = {row["place"] for row in foods if "pickup" in row["fulfillment_modes"]}
    nearest = min((route for route in observed["facts"]["routes"]
                   if route["destination"] in stores and route["status"] == "reachable"),
                  key=lambda route: (route["minutes"], route["destination"]))
    mode = {"walking": "步行", "driving": "驾车"}[nearest["mode"]]
    assert observed["scene"]["local_food_summary"] == (
        f"最近能买到吃的是{nearest['destination']}，{mode}约 {nearest['minutes']} 分钟。")


def _keep_waiting(engine) -> tuple[str, object]:
    """@brief 就绪时的下一请求：先给刚结束的等待收尾（规格第七章「活动一结束就要结束它」），在等就播报，否则开始等待。"""
    waiting = pending_conclusions(engine.state, 1)
    if waiting:
        return "conclude_activity", conclude_request(*(activity_name(engine, item.activity_id) for item in waiting))
    running = active_activities(engine.state, 1)
    if running:
        return "report_status", ReportStatusRequest(task=[activity_name(engine, running[0].activity_id)],
                                                    summary=["接着等。"])
    return "start_activity", start_activity("等待", "自己")


def test_delivery_eta_is_readable_and_matches_actual_delivery(engine_factory) -> None:
    """@brief 配送行给出人读送达时刻，与下单后的实际送达时刻一致；饿时在途订单进入途径句。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 700)
    observed, bought = _start(engine, "deliver-home", "supply", None, "远程下单")
    row = next(item for item in observed["facts"]["products"]
               if item["object"] == next(binding["object"] for binding in observed["interaction_bindings"]
                                         if binding["object_id"] == bought.data["orders"][0]["supply_id"]))
    order = bought.data["orders"][0]
    now = observed["current_time"]
    assert "delivery_delay_seconds" not in row
    assert row["delivery_eta"] == (f"{dining.readable_duration(order['expected_at'] - now)}后送达，"
                                   f"{dining.readable_clock(now, order['expected_at'], ZONE)}")
    assert engine.state.deliveries[order["order_id"]].expected_at == order["expected_at"]
    product = next(item for item in engine.scenario.products if item.product_id == order["product_id"])
    scene = engine._observation_data(engine.state.agents[1])["scene"]
    if "eat" in product.use_modes:
        assert scene["local_food_summary"].startswith(f"你点的{product.name}预计")
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
    """@brief 送达地不在可配送目的地、或配送道路未知时没有预计送达时刻，投影写「暂时送不到这里」。"""
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
    agent = engine.state.agents[1]
    row = public_supply_row(supply_row(engine._discovery_context(agent), supply))
    etas = engine._delivery_expected_at(agent, [row])
    assert etas == {supply.supply_id: None}
    inputs = projection.ProjectionInputs(state=engine.state, scenario=scenario, agent=agent, recent_results=(),
                                         task_rows=(), extras={"product_rows": [row], "delivery_expected_at": etas})
    assert projection._public_products(inputs)[0]["delivery_eta"] == "暂时送不到这里"

