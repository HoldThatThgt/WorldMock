"""人物习惯的显式现行原生动作与依赖链，不解析任意散文日程。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests import engine_fixture as engine_fixtures
from tests.catalog.conftest import read_payload, write_payload
from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.conftest import conclude_request, context, stage_day_plan, submit_round
from tests.life.routing_fixtures import _keep_busy, act, place, run_until, stage
from worldmock.life.models import ActivityKind, ActivityStatus, SendMessageActionRequest, StartActivityRequest
from worldmock.life.work import work_totals


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "tool_interaction_habits.json").read_text(
    encoding="utf-8"))
CASES = tuple(FIXTURE["cases"])


@pytest.fixture
def engine_factory(engine_factory, monkeypatch):
    """@brief 在隔离的真实目录声明当前食品，重新发布身份后由原生引擎装配。"""
    original = engine_fixtures._mutated_scenario

    def declared_catalog(source, mutate, root):
        scenario_path = original(source, mutate, root)
        catalog = root / "catalog"
        foods = read_payload(catalog, "foods.json")
        foods["foods"].extend(FIXTURE["catalog_food_declarations"])
        foods["foods"].sort(key=lambda row: row["food_id"])
        write_payload(catalog, "foods.json", foods)
        return scenario_path

    monkeypatch.setattr(engine_fixtures, "_mutated_scenario", declared_catalog)
    return engine_factory


def _configure(document, case):
    """@brief 当前场景显式声明联系、工作与人物文字，不翻译已退休字段。"""
    if "end_at" in case["preconditions"]:
        document["end_at"] = case["preconditions"]["end_at"]
    for agent in document["agents"]:
        agent.update(FIXTURE["scenario_agent_declarations"][str(agent["agent_id"])])


def _prepare(engine, case):
    """@brief 仅声明时间、地点与空库存；食物必须后续真实购买，地点会话同步重建。"""
    now = int(datetime.fromisoformat(case["preconditions"]["at"]).timestamp())

    def change(state):
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
            agent.inventory = {agent.location_id: {}}
            agent.body.energy_kcal = 900
    stage(engine, change)
    place(engine, case["agent_id"], case["preconditions"]["location_id"], {})
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    assert case["preconditions"]["inventory"] == {}
    assert case["preconditions"]["notifications"] == []


def _request(engine, item):
    """@brief 显式目录身份转为当前工具的可读名称和方式，应用使用只以应用为对象。"""
    kind, identity = item["kind"], item["object_id"]
    rules = engine.context.rules
    if kind in {"purchase", "consume"}:
        name = rules.foods[identity].name
        name = f"持有物：{name}" if kind == "consume" else name
    elif kind == "travel":
        name = rules.locations[identity].display_name
    elif kind == "lifestyle":
        name = next(row.name for row in engine.context.lifestyle_items if row.activity_id == identity)
    elif kind == "work":
        observed = engine.observe(context(engine, f"habit-work-name-{engine.context.state.version}", item["agent_id"]))
        name = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == identity)
    elif kind == "self":
        name = "自己"
    elif kind == "call":
        config = next(agent for agent in engine.context.scenario.agents if agent.agent_id == item["agent_id"])
        name = f"联系人：{next(row.salutation for row in config.contacts if row.contact_id == identity)}"
    else:
        assert kind == "app"
        name = f"应用：{rules.apps[identity].name}"
    source = None
    if "from_app" in item:
        source = rules.apps[item["from_app"]].name
    elif "from" in item:
        source = rules.locations[item["from"]].display_name
    return {"target": name, "way": item["way"], "from": source, "to": None}


def _finish(engine, identities, agent_id, complete=True):
    """@brief 在真实完成或反馈边界收尾，保存实际经过秒数与本次结算结果。"""
    started = min(engine.context.state.activities[key].started_at for key in identities)
    condition = lambda current: agent_id in current.scheduling_state().ready_agent_ids and (
        all(current.context.state.activities[key].status != ActivityStatus.ACTIVE for key in identities) if complete
        else current.context.state.current_time > started)
    run_until(engine, condition, limit=160)
    names = [engine.context.state.activities[key].name for key in identities
             if engine.context.state.activities[key].concluded_at is None]
    label = f"habit-conclude-{engine.context.state.version}-{agent_id}"
    requests = {other: _keep_busy(engine, other, label) for other in engine.scheduling_state().ready_agent_ids
                if other != agent_id}
    requests[agent_id] = ("conclude_activity", conclude_request(*names, summary="完成本次习惯动作。"))
    _, results = submit_round(engine, requests, label)
    result = next(row for row in results if row.agent_id == agent_id)
    assert result.status == "completed", result.model_dump()
    assert all(engine.context.state.activities[key].elapsed_seconds > 0 for key in identities)
    assert all(engine.context.state.activities[key].concluded_at is not None for key in identities)
    return result


def _start(engine, case, item):
    """@brief 真实登记请求，持续活动经原生边界结算后返回。"""
    request = StartActivityRequest(items=[_request(engine, item)], answering=None,
                                  prefer=item.get("prefer", "最快拿到") if item["kind"] == "purchase" else None)
    result = act(engine, f"habit-{case['name']}-{engine.context.state.version}", request, case["agent_id"])
    assert result.status in {"completed", "in_progress"}, result.model_dump()
    identities = ([result.activity_id] if result.activity_id else
                  [row["activity_id"] for row in result.data.get("activities", [])])
    if identities:
        _finish(engine, identities, case["agent_id"], item.get("complete", True))
    return result, identities


def _purchase(engine, case, item):
    """@brief 固定食品价、付款和实际交付形成食用前置，不提前注入持有物。"""
    agent = engine.context.state.agents[case["agent_id"]]
    destination = agent.location_id
    food = engine.context.rules.foods[item["object_id"]]
    assert agent.inventory.get(destination, {}).get(food.food_id, 0) == 0
    before_balance = engine.context.state.accounts[agent.balance_account_id]
    result, _ = _start(engine, case, item)
    [order] = result.data["orders"]
    assert order["food_id"] == food.food_id and order["quantity"] == 1
    assert order["unit_price_fen"] == food.price_fen
    expected_fee = 500 if item["way"] == "远程下单" else 0
    assert order["delivery_fee_fen"] == expected_fee
    assert order["total_price_fen"] == food.price_fen + expected_fee
    assert engine.context.state.accounts[agent.balance_account_id] == before_balance - order["total_price_fen"]
    delivery = engine.context.state.deliveries[order["order_id"]]
    assert delivery.destination_id == destination and delivery.food_id == food.food_id
    if "from" in item:
        assert delivery.store_id == item["from"] and delivery.app_id is None
    if "from_app" in item:
        assert delivery.app_id == item["from_app"]
        assert delivery.store_id in engine.context.semantics.catalog.foods_by_store
        assert food.food_id in engine.context.semantics.catalog.foods_by_store[delivery.store_id]
    if item.get("prefer") == "到手价最低":
        assert item["way"] == "购买" and delivery.fulfillment.value == "pickup"
        assert delivery.store_id == destination and delivery.app_id is None
        assert order["total_price_fen"] < food.price_fen + 500
    if item["way"] == "远程下单":
        assert engine.context.state.agents[case["agent_id"]].inventory.get(destination, {}).get(food.food_id, 0) == 0
        run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.context.state.agents[case["agent_id"]].inventory[destination][food.food_id] == 1


def _message(engine, case, item):
    """@brief 分享与约饭沿真实联系人消息方式，保留收件人、正文和发送证据。"""
    config = next(agent for agent in engine.context.scenario.agents if agent.agent_id == case["agent_id"])
    contact = next(row for row in config.contacts if row.contact_id == item["object_id"])
    request = SendMessageActionRequest(recipient_name=f"联系人：{contact.salutation}", text=item["text"])
    result = act(engine, f"habit-message-{engine.context.state.version}", request, case["agent_id"], "send_message")
    assert result.status == "completed", result.model_dump()
    assert any(row.text == item["text"] and row.recipient_id == contact.contact_id
               for row in engine.context.state.agents[case["agent_id"]].outbox.values())


def _assert_app_progress(engine, activity, item):
    """@brief 只核对本次应用活动认证消费区间，不借另一活动的全局游标冒充观看。"""
    assert activity.kind == ActivityKind.USE_APP and activity.target_id == item["object_id"]
    progress = [row.data["content"] for row in engine.context.store.read_agent_events("activity_progress", activity.agent_id)
                if row.activity_id == activity.activity_id]
    assert progress and all(row["app_id"] == item["object_id"] for row in progress)
    assert all(row["content_id"] in engine.context.semantics.catalog.contents_by_app[item["object_id"]]
               for row in progress)
    assert sum(row["position_after_seconds"] - row["position_before_seconds"] for row in progress) == (
        activity.elapsed_seconds)
    if item.get("complete", True):
        assert activity.elapsed_seconds == 1800


def _step(engine, case, item):
    """@brief 每一步调用当前原生工具，确认实际持有物、应用、工时或地点证据。"""
    if item["kind"] == "purchase":
        _purchase(engine, case, item)
        return
    if item["kind"] == "message":
        _message(engine, case, item)
        return
    before = engine.context.state.agents[case["agent_id"]].model_copy(deep=True)
    _, identities = _start(engine, case, item)
    current = engine.context.state.agents[case["agent_id"]]
    activity = engine.context.state.activities[identities[0]]
    if item["kind"] == "consume":
        food, destination = item["object_id"], before.location_id
        assert before.inventory[destination][food] == 1
        assert current.inventory[destination][food] == 0
        assert activity.data["consumed"][food] == 1
    elif item["kind"] == "travel":
        assert current.location_id == item["object_id"]
    elif item["kind"] == "app":
        _assert_app_progress(engine, activity, item)
    elif item["kind"] == "work":
        config = next(agent for agent in engine.context.scenario.agents if agent.agent_id == case["agent_id"])
        assert item["object_id"] in config.work_topic_ids
        assert activity.kind == ActivityKind.WORK and activity.target_id == item["object_id"]
        assert work_totals(engine.context.state, case["agent_id"], item["object_id"]) == activity.elapsed_seconds > 0
    elif item["kind"] == "self" and item["way"] == "睡觉":
        assert activity.kind == ActivityKind.SLEEP
    elif item["kind"] == "lifestyle":
        assert activity.kind == ActivityKind.LIFESTYLE and activity.target_id == item["object_id"]
    elif item["kind"] == "call":
        assert activity.kind == ActivityKind.CALL and activity.data["contact_id"] == item["object_id"]


def test_every_frozen_habit_has_a_complete_explicit_native_fixture():
    """@brief 原编译卡九条声明全部有账：七条真实回放，两条只因冻结能力移除而退休。"""
    declared = {(agent["agent_id"], json.dumps(habit, sort_keys=True, ensure_ascii=False))
                for agent in FIXTURE["compiled_personas"] for habit in agent["habits"]}
    retired = {(row["agent_id"], json.dumps(row["habit"], sort_keys=True, ensure_ascii=False))
               for row in FIXTURE["retired_declarations"]}
    actual = {(case["agent_id"], json.dumps(case["habit"], sort_keys=True, ensure_ascii=False))
              for case in CASES if "habit" in case}
    assert actual.isdisjoint(retired) and actual | retired == declared
    assert len(declared) == 9 and len(actual) == 7 and len(retired) == 2
    assert {name for row in FIXTURE["retired_declarations"] for name in row["case_names"]} == {
        "commute-podcast", "late-game", "weekend-game"}
    assert all(row["removed_contract"] for row in FIXTURE["retired_declarations"])
    assert len(CASES) == len({row["name"] for row in CASES}) == 21
    assert all(row["chain"] and row["preconditions"] for row in CASES)
    scenario = json.loads((ENVIRONMENT_ROOT / FIXTURE["scenario"]).read_text(encoding="utf-8"))
    _configure(scenario, CASES[0])
    personas = {agent["agent_id"]: agent for agent in scenario["agents"]}
    for row in CASES:
        assert all(step["kind"] and step["object_id"] for step in row["chain"])
        if "background_declaration" in row:
            source = personas[row["agent_id"]]
            assert row["background_declaration"] in source["background_story"] + source["health_habits"]


@pytest.mark.parametrize("case", CASES, ids=[row["name"] for row in CASES])
def test_declared_habit_runs_with_real_prerequisites_and_settlement(engine_factory, case):
    """@brief 双人物引擎回放保留声明及其付款、交付、食用、地点和内容依赖。"""
    engine = engine_factory("multi-person.json", mutate=lambda document: _configure(document, case))
    _prepare(engine, case)
    for item in case["chain"]:
        _step(engine, case, item)
    events = engine.context.store.read_agent_events("action_result", case["agent_id"])
    assert events and not engine.context.store.read_agent_events("run_failed", case["agent_id"])
    assert not any(row.data.get("status") == "rejected" for row in events)
