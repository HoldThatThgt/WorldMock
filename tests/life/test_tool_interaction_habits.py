"""正式人物习惯的显式原生动作与前后依赖链，不解析任意散文日程。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.life.conftest import conclude_request, context, stage_day_plan, submit_round
from tests.life.routing_fixtures import _keep_busy, act, run_until, stage
from worldmock.life.models import ActivityKind, ActivityStatus, SendMessageActionRequest, StartActivityRequest


FIXTURE = json.loads(Path("tests/fixtures/tool_interaction_habits.json").read_text(encoding="utf-8"))
CASES = tuple(FIXTURE["cases"])


def _prepare(engine, case):
    """@brief 仅声明明确的时间、地点、通知与空库存；所有食物须后续真实购买。"""
    now = int(datetime.fromisoformat(case["preconditions"]["at"]).timestamp())

    def change(state):
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
            agent.inventory = {agent.location_id: {}}
            agent.body.energy_kcal = 900
        person = state.agents[case["agent_id"]]
        person.location_id = case["preconditions"]["location_id"]
        person.inventory = {person.location_id: {}}
    stage(engine, change)
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    assert case["preconditions"]["inventory"] == {}
    assert case["preconditions"]["notifications"] == []


def _request(engine, item):
    """@brief 夹具的显式身份只转成正式对象名称和现行公开方式。"""
    kind, identity = item["kind"], item["object_id"]
    if kind in {"purchase", "consume"}:
        name = engine.rules.items[identity].name
        name = f"持有物：{name}" if kind == "consume" else name
    elif kind == "travel":
        name = engine.rules.locations[identity].name
    elif kind == "lifestyle":
        name = next(row.name for row in engine.lifestyle_items if row.activity_id == identity)
    elif kind == "game":
        name = engine.rules.game(identity).title
    elif kind == "work":
        observed = engine.observe(context(engine, f"habit-work-name-{engine.state.version}", item["agent_id"]))
        name = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == identity)
    elif kind == "self":
        name = "自己"
    elif kind == "call":
        config = next(agent for agent in engine.scenario.agents if agent.agent_id == item["agent_id"])
        name = f"联系人：{next(row.salutation for row in config.contacts if row.contact_id == identity)}"
    else:
        assert kind == "media"
        name = engine.rules.content_titles[identity]
    source = engine.rules.locations[item["from"]].name if item.get("from") else None
    return {"target": name, "way": item["way"], "from": source, "to": None}


def _finish(engine, identities, agent_id, complete=True):
    """@brief 在真实完成或反馈边界收尾，避免等待助手自动替本人收尾而丢失结算结果。"""
    started = min(engine.state.activities[key].started_at for key in identities)
    condition = lambda current: agent_id in current.scheduling_state().ready_agent_ids and (
        all(current.state.activities[key].status != ActivityStatus.ACTIVE for key in identities) if complete
        else current.state.current_time > started)
    run_until(engine, condition, limit=160)
    names = [engine.state.activities[key].name for key in identities
             if engine.state.activities[key].concluded_at is None]
    label = f"habit-conclude-{engine.state.version}-{agent_id}"
    requests = {other: _keep_busy(engine, other, label) for other in engine.scheduling_state().ready_agent_ids
                if other != agent_id}
    requests[agent_id] = ("conclude_activity", conclude_request(*names, summary="完成本次习惯动作。"))
    _, results = submit_round(engine, requests, label)
    result = next(row for row in results if row.agent_id == agent_id)
    assert result.status == "completed", result.model_dump()
    assert all(engine.state.activities[key].elapsed_seconds > 0 for key in identities)
    assert all(engine.state.activities[key].concluded_at is not None for key in identities)
    return result


def _start(engine, case, item):
    """@brief 真实登记单项或并行请求，持续活动须经边界结算后返回。"""
    items = item["items"] if item["kind"] == "parallel" else [item]
    request = StartActivityRequest(items=[_request(engine, row) for row in items], answering=None,
                                  prefer=item.get("prefer", "最快拿到")
                                  if any(row["kind"] == "purchase" for row in items) else None)
    result = act(engine, f"habit-{case['name']}-{engine.state.version}", request, case["agent_id"])
    assert result.status in {"completed", "in_progress"}, result.model_dump()
    identities = ([result.activity_id] if result.activity_id else
                  [row["activity_id"] for row in result.data.get("activities", [])])
    if identities:
        _finish(engine, identities, case["agent_id"], item.get("complete", True))
    return result, identities


def _purchase(engine, case, item):
    """@brief 付款、独立门店减库存与交付形成消费前置；不提前注入持有物。"""
    agent = engine.state.agents[case["agent_id"]]
    destination = agent.location_id
    assert agent.inventory.get(destination, {}).get(item["object_id"], 0) == 0
    before_balance = engine.state.accounts[agent.balance_account_id]
    before_stock = dict(engine.state.supply_stock)
    result, _ = _start(engine, case, item)
    [order] = result.data["orders"]
    assert order["product_id"] == item["object_id"]
    if item.get("from"):
        assert order["location_id"] == item["from"]
    if item.get("prefer") == "到手价最低":
        prices = [engine.state.supply_quotes[supply.supply_id].price_fen for supply in engine.scenario.supplies
                  if supply.product_id == item["object_id"] and "delivery" in supply.fulfillment_modes]
        assert order["total_price_fen"] == min(prices)
    assert engine.state.accounts[agent.balance_account_id] == before_balance - order["total_price_fen"]
    assert engine.state.supply_stock[order["supply_id"]] == before_stock[order["supply_id"]] - 1
    if item["way"] == "远程下单":
        assert engine.state.agents[case["agent_id"]].inventory.get(destination, {}).get(item["object_id"], 0) == 0
        run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.agents[case["agent_id"]].inventory[destination][item["object_id"]] == 1


def _message(engine, case, item):
    """@brief 分享和约饭沿真实联系人消息方式，保留收件人、正文与发送证据。"""
    config = next(agent for agent in engine.scenario.agents if agent.agent_id == case["agent_id"])
    contact = next(row for row in config.contacts if row.contact_id == item["object_id"])
    request = SendMessageActionRequest(recipient_name=f"联系人：{contact.salutation}", text=item["text"])
    result = act(engine, f"habit-message-{engine.state.version}", request, case["agent_id"], "send_message")
    assert result.status == "completed", result.model_dump()
    assert any(row.text == item["text"] and row.recipient_id == contact.contact_id
               for row in engine.state.agents[case["agent_id"]].outbox.values())


def _step(engine, case, item):
    """@brief 每个预写步骤执行既有原生工具，确认实际库存、内容或地点证据。"""
    if item["kind"] == "purchase":
        _purchase(engine, case, item)
        return
    if item["kind"] == "message":
        _message(engine, case, item)
        return
    before = engine.state.agents[case["agent_id"]].model_copy(deep=True)
    _, identities = _start(engine, case, item)
    current = engine.state.agents[case["agent_id"]]
    if item["kind"] == "consume":
        product, place = item["object_id"], before.location_id
        assert before.inventory[place][product] == 1
        assert current.inventory[place][product] == 0
        assert engine.state.activities[identities[0]].data["consumed"][product] == 1
    elif item["kind"] == "travel":
        assert current.location_id == item["object_id"]
    elif item["kind"] == "media":
        assert current.content_progress[item["object_id"]].position_seconds > 0
        if item.get("complete", True):
            assert current.content_progress[item["object_id"]].position_seconds == (
                engine.rules.content(item["object_id"]).duration_seconds)
    elif item["kind"] == "work":
        activity = engine.state.activities[identities[0]]
        assert item["object_id"] in before.known_task_ids & before.visible_task_ids
        assert activity.effective_work_seconds > 0
    elif item["kind"] == "self" and item["way"] == "睡觉":
        assert engine.state.activities[identities[0]].kind == ActivityKind.SLEEP
    elif item["kind"] == "parallel":
        assert {engine.state.activities[key].kind for key in identities} == {ActivityKind.TRAVEL, ActivityKind.WATCH}
        podcast = next(row["object_id"] for row in item["items"] if row["kind"] == "media")
        assert current.location_id == "office" and current.content_progress[podcast].position_seconds > 0
        assert current.content_progress[podcast].position_seconds == engine.rules.content(podcast).duration_seconds


def test_every_frozen_habit_has_a_complete_explicit_native_fixture():
    """@brief 两张真实编译人物卡的逐条声明必须恰好登记，不以关键词或空列表冒充完成。"""
    expected = {(agent["agent_id"], json.dumps(habit, sort_keys=True, ensure_ascii=False))
                for agent in FIXTURE["compiled_personas"] for habit in agent["habits"]}
    actual = {(case["agent_id"], json.dumps(case["habit"], sort_keys=True, ensure_ascii=False))
              for case in CASES if "habit" in case}
    assert expected == actual and len(expected) == 9
    assert len(CASES) == len({row["name"] for row in CASES})
    assert all(row["chain"] and row["preconditions"] for row in CASES)
    scenario = json.loads(Path(FIXTURE["scenario"]).read_text(encoding="utf-8"))
    personas = {agent["agent_id"]: agent for agent in scenario["agents"]}
    for row in CASES:
        assert all(step["kind"] and step.get("object_id", step.get("items")) for step in row["chain"])
        if "background_declaration" in row:
            source = personas[row["agent_id"]]
            assert row["background_declaration"] in source["background_story"] + source["health_habits"]


@pytest.mark.parametrize("case", CASES, ids=[row["name"] for row in CASES])
def test_declared_habit_runs_with_real_prerequisites_and_settlement(engine_factory, case):
    """@brief 同一正式双人物引擎回放每条习惯及其购买、库存、地点和内容前后依赖。"""
    def configure(document):
        if "end_at" in case["preconditions"]:
            document["end_at"] = case["preconditions"]["end_at"]
    engine = engine_factory("multi-person.json", mutate=configure)
    _prepare(engine, case)
    for item in case["chain"]:
        _step(engine, case, item)
    events = engine.store.read_agent_events("action_result", case["agent_id"])
    assert events and not engine.store.read_agent_events("run_failed", case["agent_id"])
    assert not any(row.data.get("status") == "rejected" for row in events)
