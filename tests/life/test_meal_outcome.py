"""进食的开吃份量、不足一份与收尾结果（docs/dev/SPEC-persona-prompt.md 第五章「进食」、第六章）。

真实 LifeEngine 与 SQLite 上：开吃时每件取 min(1, 持有) 从库存移入活动数据 opened，摄入只按 opened 与实际进度结算；
4 份里拿 1 份吃到 300/1500 秒时结束：摄入 0.2、丢弃 0.8、库存 3；暂停保留剩余并可恢复（恢复不再核库存，暂停期间别的
进食吃掉同一商品也不再抛出未捕获的 PRODUCT_NOT_HELD）；持有 0.25 份时受理、时长 375 秒、热量按 0.25 份；实验截止截断
不丢弃；收尾结果 details.outcome 逐项写终局（完成、提前结束、失败）与已吃、丢弃、剩余份量，人物写「吃完了」不改变它；
未完成的浏览收尾写「提前结束，未取得浏览结果」。
"""

from __future__ import annotations

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 last_result, layer_start, stage_day_plan, start_activity, target_actions,
                                 wake_by_mail)
from tests.life.test_domain_branch_coverage import _agent, _consume, _product, _use_activity
from tests.life.test_export_integration import _stage_final_interval
from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life.activity_evidence import conclusion_outcome, discard_opened
from worldmock.life.basket import meal_duration, meal_portion, open_meal, portion_duration
from worldmock.life.models import (ActivityStatus, ConsumeItem, ConsumeRequest, PauseActivityRequest,
                                   ReportStatusRequest, active_activities)
from worldmock.life.readable import duration_phrase
from worldmock.life.sleep import food_utilization_efficiency


def _food(engine, seconds: int = 1500):
    """场景中整份进食时长为 seconds 秒的食品。"""
    return next(item for item in engine.scenario.products
                if "eat" in item.use_modes and item.use_duration_seconds == seconds)


def _stage_holding(engine, product_id: str, quantity: float) -> None:
    """在家里放指定份数的持有物并持久提交（保持存储版本连续）。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    inventory.setdefault("home", {})[product_id] = quantity
    staged.agents[1] = agent.model_copy(update={"inventory": inventory}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_meal_holding_staged"})])
    engine.state = staged


def _round(engine, name: str, tool: str, request):
    """单人物按真实顺序观察、登记并结算一轮，返回（本轮观察，结果）。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _start_eating(engine, food, label: str):
    """按工具层的持有物目标与「食用」开始进食，返回（开始前的工具层动作，结果）。"""
    observed = engine.observe(context(engine, f"{label}-preview"))
    action = target_actions(observed, f"持有物：{food.name}")["食用"]
    _, result = _round(engine, label, "start_activity", layer_start(observed, f"持有物：{food.name}", "食用"))
    assert result.status == "in_progress", result.error_code
    return action, result


def _run_out(engine, activity_id: str, label: str) -> None:
    """只经真实时间边界推进到活动不再进行；中途的决策机会以播报继续全部进行中的活动。"""
    for step in range(96):
        if engine.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.state, 1)
            _round(engine, f"{label}-report-{step}", "report_status", ReportStatusRequest(
                task=[item.name for item in running], summary=["继续。"] * len(running)))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def _intake(engine, activity_id: str, kcal: float) -> tuple[float, float, float]:
    """活动各区间的（食用份量合计，按区间效率折算的应得热量，食物实际流入热量）。"""
    events = [event for event in engine.store.read_events(engine.store.last_event_seq)
              if event.event_type == "activity_progress" and event.activity_id == activity_id]
    eaten = sum(item["interval_quantity"] for event in events for item in event.data["consumption"]["items"])
    expected = kcal * sum(item["interval_quantity"] * event.data["consumption"]["food_efficiency"]
                          for event in events for item in event.data["consumption"]["items"])
    gained = sum(flow["amount"] for event in events for flow in event.data.get("resource_flows", [])
                 if flow["resource"] == "energy_kcal" and flow["cause"] == "food:intake")
    return eaten, expected, gained


def _held(engine, product_id: str) -> float:
    """家里该商品的持有份数。"""
    return engine.state.agents[1].inventory["home"][product_id]


def _items(food, eaten: str, discarded: str, remaining: str) -> list[dict]:
    """收尾结果里一件食品的份量。"""
    return [{"product": food.name, "eaten": eaten, "discarded": discarded, "remaining": remaining}]


def test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3(engine_factory) -> None:
    """4 份里拿 1 份开吃（库存 3，opened 1），吃到 300/1500 秒时结束：摄入 0.2、丢弃 0.8、库存 3，不补算摄入；人物总结写
    「吃完了」，结果仍是提前结束；终局与份量进入下一次观察进度层该调用的公开结果。"""
    engine = engine_factory()
    stage_day_plan(engine)
    food = _food(engine)
    _stage_holding(engine, food.product_id, 4.0)
    action, started = _start_eating(engine, food, "meal")
    assert (action["available"], action["quantity"], action["duration"]) == (True, "1 份", duration_phrase(1500))
    meal = engine.state.activities[started.activity_id]
    assert (meal.planned_duration_seconds, meal.data["opened"], _held(engine, food.product_id)) == (
        1500, {food.product_id: 1.0}, 3.0)
    at = wake_by_mail(engine, offset=300)
    advance_next(engine)
    assert engine.state.current_time == at
    name = activity_name(engine, meal.activity_id)
    _, ended = _round(engine, "meal-end", "conclude_activity", conclude_request(name, summary="吃完了"))
    assert ended.status == "completed"
    outcome = [{"activity": name, "outcome": "提前结束", "items": _items(food, "0.2 份", "0.8 份", "3 份"),
                "note": None}]
    assert ended.data["outcome"] == outcome
    meal = engine.state.activities[meal.activity_id]
    assert (meal.status, meal.data["consumed"], meal.data["discarded"], meal.data["opened"]) == (
        ActivityStatus.CANCELLED, {food.product_id: 0.2}, {food.product_id: 0.8}, {food.product_id: 1.0})
    assert _held(engine, food.product_id) == 3.0
    eaten, expected, gained = _intake(engine, meal.activity_id, food.energy_kcal_per_unit)
    assert eaten == pytest.approx(0.2) and gained == pytest.approx(expected) and gained > 0
    assert last_result(engine.observe(context(engine, "meal-seen")))["details"]["outcome"] == outcome


def test_pause_keeps_the_opened_remainder_and_resume_eats_it_after_another_meal_took_the_stock(
        engine_factory) -> None:
    """持有 1.5 份：开吃 A 取 1 份（库存 0.5），吃到一半暂停，剩余 0.5 份留在 A 里别处不能用；再开吃 B 只能取手边的 0.5 份
    （时长 750 秒），吃完后库存为 0；恢复 A 不再核库存，吃完 A 的开吃份量，两餐都以完成收尾、丢弃为 0。

    回归：旧口径下 B 按一份核库存后吃掉 1 份，恢复 A 推进到结算时抛出未捕获的 PRODUCT_NOT_HELD（basket.py 的缺货路径）。
    """
    engine = engine_factory()
    stage_day_plan(engine)
    food = _food(engine)
    _stage_holding(engine, food.product_id, 1.5)
    _, first = _start_eating(engine, food, "meal-a")
    wake_by_mail(engine, offset=750)
    advance_next(engine)
    name_a = activity_name(engine, first.activity_id)
    _round(engine, "meal-a-pause", "pause_activity", PauseActivityRequest(task=[name_a], summary=["先停一下。"]))
    paused = engine.state.activities[first.activity_id]
    assert (paused.status, paused.data["consumed"], _held(engine, food.product_id)) == (
        ActivityStatus.PAUSED, {food.product_id: 0.5}, 0.5)
    action, second = _start_eating(engine, food, "meal-b")
    assert (action["quantity"], action["duration"]) == ("0.5 份", duration_phrase(750))
    other = engine.state.activities[second.activity_id]
    assert (other.planned_duration_seconds, other.data["opened"], _held(engine, food.product_id)) == (
        750, {food.product_id: 0.5}, 0.0)
    _run_out(engine, second.activity_id, "meal-b")
    name_b = activity_name(engine, second.activity_id)
    assert advance_until_ready(engine)
    _, done_b = _round(engine, "meal-b-end", "conclude_activity", conclude_request(name_b))
    assert done_b.data["outcome"] == [{"activity": name_b, "outcome": "完成",
                                       "items": _items(food, "0.5 份", "0 份", "0 份"), "note": None}]
    _, resumed = _round(engine, "meal-a-resume", "report_status",
                        ReportStatusRequest(task=[name_a], summary=["接着吃。"]))
    assert resumed.status == "completed", resumed.error_code
    _run_out(engine, first.activity_id, "meal-a")
    assert engine.state.activities[first.activity_id].status == ActivityStatus.COMPLETED
    _, done_a = _round(engine, "meal-a-end", "conclude_activity", conclude_request(name_a))
    assert done_a.data["outcome"] == [{"activity": name_a, "outcome": "完成",
                                       "items": _items(food, "1 份", "0 份", "0 份"), "note": None}]
    assert engine.state.activities[first.activity_id].data["discarded"] == {food.product_id: 0.0}
    assert _held(engine, food.product_id) == 0.0
    assert _intake(engine, first.activity_id, food.energy_kcal_per_unit)[0] == pytest.approx(1.0)


def test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy(engine_factory) -> None:
    """持有 0.25 份时食用可用、受理：工具层的数量 0.25 份与时长 375 秒同一口径，时长为 max(60, round(1500 × 0.25))，
    热量按 0.25 份结算（第六章「不足一份的食物无法消费」的回归：固定一份时该动作不可用并以 PRODUCT_NOT_HELD 拒绝）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    food = _food(engine)
    _stage_holding(engine, food.product_id, 0.25)
    efficiency = food_utilization_efficiency(engine.state.agents[1].body.sleep_deficit_hours)
    action, started = _start_eating(engine, food, "quarter")
    assert (action["available"], action["reason"], action["quantity"], action["duration"]) == (
        True, None, "0.25 份", duration_phrase(375))
    meal = engine.state.activities[started.activity_id]
    request = meal.data["request"]
    assert (meal.planned_duration_seconds, request["duration_seconds"], request["items"]) == (
        375, 375, [{"product_id": food.product_id, "quantity": 0.25}])
    assert meal.data["food_energy_kcal_per_hour"] * 375 / 3600 == pytest.approx(
        0.25 * food.energy_kcal_per_unit * efficiency)
    _run_out(engine, meal.activity_id, "quarter")
    eaten, expected, gained = _intake(engine, meal.activity_id, food.energy_kcal_per_unit)
    assert eaten == pytest.approx(0.25) and gained == pytest.approx(expected) and gained > 0
    assert _held(engine, food.product_id) == 0.0
    assert advance_until_ready(engine)
    name = activity_name(engine, meal.activity_id)
    _, done = _round(engine, "quarter-end", "conclude_activity", conclude_request(name))
    assert done.data["outcome"] == [{"activity": name, "outcome": "完成",
                                     "items": _items(food, "0.25 份", "0 份", "0 份"), "note": None}]


def test_experiment_cutoff_truncation_discards_nothing(engine_factory) -> None:
    """实验截止截断正在进行的进食：已吃的份量照常结算，开吃份量的剩余不丢弃（不写 discarded），其他份数不动。"""
    engine = engine_factory()
    _stage_final_interval(engine, 600)
    stage_day_plan(engine)
    food = _food(engine)
    _stage_holding(engine, food.product_id, 4.0)
    _, started = _start_eating(engine, food, "cutoff")
    _run_out(engine, started.activity_id, "cutoff")
    meal = engine.state.activities[started.activity_id]
    assert (meal.status, meal.data["consumed"], meal.data["opened"]) == (
        ActivityStatus.TRUNCATED, {food.product_id: 0.4}, {food.product_id: 1.0})
    assert "discarded" not in meal.data and _held(engine, food.product_id) == 3.0


def test_unfinished_browse_concludes_without_a_browse_result(engine_factory) -> None:
    """浏览没做完就结束：收尾结果写「提前结束，未取得浏览结果」，不写浏览得到的商品与购买；做完的浏览收尾不写终局。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = _round(engine, "browse", "start_activity", start_activity("浏览", "商品目录"))
    assert started.status == "in_progress", started.error_code
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    name = activity_name(engine, started.activity_id)
    _, ended = _round(engine, "browse-end", "conclude_activity", conclude_request(name, summary="逛完了。"))
    assert ended.data["outcome"] == [{"activity": name, "outcome": "提前结束", "items": [],
                                      "note": "未取得浏览结果"}]
    details = last_result(engine.observe(context(engine, "browse-seen")))["details"]
    assert details == {"outcome": ended.data["outcome"]}
    _, again = _round(engine, "browse-again", "start_activity", start_activity("浏览", "商品目录"))
    _run_out(engine, again.activity_id, "browse-again")
    assert advance_until_ready(engine)
    _, done = _round(engine, "browse-done", "conclude_activity",
                     conclude_request(activity_name(engine, again.activity_id)))
    assert done.status == "completed" and "outcome" not in done.data


def test_portion_duration_and_meal_portion_share_one_basis() -> None:
    """份量与时长同一口径：食用取 min(1, 持有)，时长 max(60, round(整份 × 份量))；使用为一件与目录时长。"""
    assert [portion_duration(1500, portion) for portion in (1.0, 0.25, 0.5, 0.01)] == [1500, 375, 750, 60]
    food = _product(duration=1500)
    assert meal_portion(food, "eat", 4.0) == (1.0, 1500)
    assert meal_portion(food, "eat", 0.25) == (0.25, 375)
    assert meal_portion(food, "eat", 0.0) == (0.0, 60)
    tool = _product(product_id="item-dry", use_modes=("use",), consumable=False, kcal=0, duration=600)
    assert meal_portion(tool, "use", 0.3) == (1.0, 600)


def test_meal_duration_is_validated_against_the_scaled_portion() -> None:
    """进食时长校验按份量折算：0.25 份的整份 1500 秒食品只能是 375 秒。"""
    products = {"food-1": _product(duration=1500)}
    quarter = ConsumeRequest(items=(ConsumeItem(product_id="food-1", quantity=0.25),), use_mode="eat",
                             duration_seconds=375)
    assert meal_duration(products, quarter) == 375
    with pytest.raises(BusinessRuleError, match="MEAL_DURATION_MUST_BE_LONGEST"):
        meal_duration(products, quarter.model_copy(update={"duration_seconds": 1500}))


def test_open_meal_moves_the_portion_out_of_stock_only_for_eating() -> None:
    """开吃把本次份量从手边库存移入 opened，其他地点与其他份数不动；使用不开封。"""
    agent = _agent(inventory={"home": {"food-1": 4.0}, "office": {"food-1": 2.0}})
    opened_agent, opened = open_meal(agent, _consume(quantity=1))
    assert opened == {"food-1": 1.0}
    assert opened_agent.inventory == {"home": {"food-1": 3.0}, "office": {"food-1": 2.0}}
    assert open_meal(agent, _consume(use_mode="use")) == (agent, {})


def test_conclusion_outcome_names_failure_and_skips_other_activities() -> None:
    """终局只按结算状态：失败写「失败」；进食以外、做完的浏览与其他活动没有终局项；非进食活动不写丢弃。"""
    food = _use_activity(_consume(), status=ActivityStatus.FAILED, ended_at=300, elapsed_seconds=300)
    food.data.update(opened={"food-1": 1.0}, consumed={"food-1": 0.2})
    ended = discard_opened(food)
    assert ended.data["discarded"] == {"food-1": 0.8}
    row = conclusion_outcome(ended, "吃饭", {"food-1": "面"}, _agent(inventory={"home": {"food-1": 2.0}}))
    assert row == {"activity": "吃饭", "outcome": "失败", "items": [
        {"product": "面", "eaten": "0.2 份", "discarded": "0.8 份", "remaining": "2 份"}], "note": None}
    use = _use_activity(_consume(use_mode="use"), status=ActivityStatus.CANCELLED, ended_at=0)
    assert discard_opened(use) is use
    assert conclusion_outcome(use, "用", {}, _agent()) is None
