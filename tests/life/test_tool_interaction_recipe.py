"""正式发布料理的原生购买、配送、制作与食用完整动作链。"""

from __future__ import annotations

from datetime import datetime

from tests.life.conftest import conclude_request, stage_day_plan, submit_round
from tests.life.routing_fixtures import act, run_until, stage
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.models import ActivityStatus, StartActivityRequest


def _start(target, way, source=None):
    """使用现行完整请求，不向世界提交数量、价格或内部身份。"""
    return StartActivityRequest(items=[{"target": target, "way": way, "from": source, "to": None}],
                                answering=None, prefer="最快拿到" if way in {"到店", "远程下单", "购买"} else None)


def _prepare(engine):
    """正式人物于正常营业时间在家，前置事实经持久化提交，不预先注入食材或产物。"""
    stage_day_plan(engine)
    now = int(datetime.fromisoformat("2026-01-05T12:30:00+08:00").timestamp())

    def change(state):
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        person = state.agents[1]
        person.location_id, person.body_settled_at, person.next_decision_at = "home", now, now
        person.inventory = {"home": {}}

    stage(engine, change)


def _conclude(engine, activity_id, label, summary="完成活动。"):
    """经真实边界结束并收尾活动，返回系统确认的物品变化。"""
    run_until(engine, lambda current: current.state.activities[activity_id].status != ActivityStatus.ACTIVE,
              limit=120)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids, limit=120)
    activity = engine.state.activities[activity_id]
    if pending_conclusions(engine.state, 1):
        _, results = submit_round(engine, {1: ("conclude_activity",
                                              conclude_request(activity.name, summary=summary))}, label)
        return next(result for result in results if result.agent_id == 1)
    raise AssertionError("Completed activity must remain available for conclusion")


def test_published_recipe_debits_input_and_creates_edible_output(engine_factory):
    """食材包付款后真实交付；制作扣一包增一份菜，随后食用成品，输入/输出及证据守恒。"""
    engine = engine_factory()
    _prepare(engine)
    kit = engine.rules.items["ingredient-vegetable-kit"]
    output = engine.rules.items[engine.scenario.recipe_output_bindings["prepared-meal-vegetable-stir-fry"]]
    assert kit.inventory_unit == output.inventory_unit == "item"
    supply = next(item for item in engine.scenario.supplies if item.product_id == kit.product_id
                  and "delivery" in item.fulfillment_modes and "home" in item.delivery_destination_ids)
    store = engine.rules.locations[supply.location_id]
    bought = act(engine, "recipe-purchase", _start(kit.name, "远程下单", store.name))
    assert bought.status == "completed", bought.model_dump()
    assert engine.state.agents[1].inventory["home"].get(kit.product_id, 0) == 0
    assert engine.state.agents[1].inventory["home"].get(output.product_id, 0) == 0
    run_until(engine, lambda current: current.state.agents[1].inventory["home"].get(kit.product_id) == 1, limit=120)
    created = act(engine, "recipe-create", _start(f"持有物：{kit.name}", "制作"))
    assert created.status == "in_progress", created.model_dump()
    assert engine.state.agents[1].inventory["home"][kit.product_id] == 1
    concluded = _conclude(engine, created.activity_id, "recipe-created")
    assert engine.state.agents[1].inventory["home"][kit.product_id] == 0
    assert engine.state.agents[1].inventory["home"][output.product_id] == 1
    rows = concluded.data["outcome"][0]["items"]
    assert rows == [{"product": kit.name, "consumed": "1 份", "produced": "0 份", "remaining": "0 份"},
                    {"product": output.name, "consumed": "0 份", "produced": "1 份", "remaining": "1 份"}]
    ate = act(engine, "recipe-eat", _start(f"持有物：{output.name}", "食用"))
    assert ate.status == "in_progress", ate.model_dump()
    _conclude(engine, ate.activity_id, "recipe-eaten")
    assert engine.state.agents[1].inventory["home"][output.product_id] == 0
    assert engine.state.activities[ate.activity_id].data["consumed"][output.product_id] == 1


def test_recipe_without_real_input_is_rejected_and_narration_cannot_create_food(engine_factory):
    """无食材时制作拒绝；普通活动总结宣称已做出饭也不新增食物。"""
    engine = engine_factory()
    _prepare(engine)
    kit = engine.rules.items["ingredient-vegetable-kit"]
    output = engine.scenario.recipe_output_bindings["prepared-meal-vegetable-stir-fry"]
    rejected = act(engine, "recipe-empty", _start(f"持有物：{kit.name}", "制作"))
    assert rejected.status == "rejected"
    assert engine.state.agents[1].inventory["home"].get(output, 0) == 0
    waited = act(engine, "recipe-wait", _start("自己", "等待"))
    assert waited.status == "in_progress"
    done = _conclude(engine, waited.activity_id, "recipe-false-narration", summary="我做好了炒饭。")
    assert done.data["outcome"][0]["items"] == []
    assert done.data["outcome"][0]["note"] == "No item changes."
    assert engine.state.agents[1].inventory["home"].get(output, 0) == 0
