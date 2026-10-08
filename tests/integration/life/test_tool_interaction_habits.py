"""明确声明的当前习惯链只靠真实购买收货、动作内容、出行和联系形成足迹。"""

import json
from pathlib import Path

import pytest

from tests import engine_fixture
from tests.life.conftest import conclude_request, stage_day_plan
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.models import SendMessageActionRequest, StartActivityRequest


FIXTURE = json.loads((Path(__file__).resolve().parents[3] / 'tests' / "fixtures/tool_interaction_habits.json").read_text(encoding="utf-8"))
CASES = tuple(FIXTURE["cases"])


@pytest.fixture
def engine_factory(engine_factory, monkeypatch):
    """在隔离目录明确发布本测试的商品、动作与内容，再通过正式登记加载。"""
    original = engine_fixture._mutated_scenario
    def declared(source, mutate, root):
        path = original(source, mutate, root)
        catalog = root / "catalog"
        for name, key in (("goods", "catalog_goods_declarations"), ("actions", "catalog_action_declarations"),
                          ("contents", "catalog_content_declarations")):
            target = catalog / f"{name}.json"
            document = json.loads(target.read_text(encoding="utf-8"))
            document[name].extend(FIXTURE[key])
            if name == "contents":
                work = next(row for row in document[name] if row["content_id"] == "topic-sales")
                document[name].append({**work, "content_id": "habit-work-2", "title": "人物二工作内容"})
            singular = {"goods": "goods", "actions": "action", "contents": "content"}[name]
            document[name].sort(key=lambda row: row[f"{singular}_id"])
            target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        refresh_manifest_identity(catalog)
        return path
    monkeypatch.setattr(engine_fixture, "_mutated_scenario", declared)
    return engine_factory


def _configure(document, case):
    """起点、通讯录与分配工作都是明确场景声明，无持有物或旧字段。"""
    document.update(start_at=case["preconditions"]["at"], messages=[], payrolls=[], bills=[])
    if "end_at" in case["preconditions"]:
        document["end_at"] = case["preconditions"]["end_at"]
    for agent in document["agents"]:
        agent.update(FIXTURE["scenario_agent_declarations"][str(agent["agent_id"])])


def _prepare(engine, case):
    """只明确当前地点并同步停留会话，所有商品必须由后续真实购买取得。"""
    state = engine.context.state.model_copy(deep=True)
    state.agents[case["agent_id"]].location_id = case["preconditions"]["location_id"]
    state = sessions.end_all(state)
    names = {row.location_id: row.display_name for row in engine.context.scenario.locations}
    for identity in state.agents:
        state = sessions.open_stay(state, identity, names)
    state.version += 1
    engine.context.store.commit(state.model_dump(mode="json"), [EventDraft(event_type="environment_event",
        occurred_at=state.current_time, visibility="system", data={"kind": "test_habit_place"})])
    engine.context.state = state
    for identity in state.agents:
        stage_day_plan(engine, identity)


def _request(engine, item, identity):
    """目录身份只用于夹具，人物请求明确动作名、应用名、支付名及社交对象。"""
    catalog = engine.context.semantics.catalog
    app = catalog.apps[item["app_id"]].name if item.get("app_id") else None
    if item["kind"] == "purchase":
        payment = catalog.apps[item["payment_app_id"]].name if item["payment_app_id"] else None
        return request(catalog.goods[item["goods_id"]].name, app, "购买", payment=payment)
    if item["kind"] == "travel":
        place = catalog.locations[item["object_id"]].display_name
        return request(f"地点：{place}", None, item["way"])
    if item["kind"] == "self":
        return request("自己", None, item["way"])
    action = catalog.actions[item["action_id"]]
    profile = next(row for row in engine.context.scenario.agents if row.agent_id == identity)
    contact = next((row for row in profile.contacts if row.contact_id == item["contact_id"]), None)
    return request(action.name, app, to=f"联系人：{contact.salutation}" if contact else None)


def _finish(engine, identities, owner, complete):
    """有限动作自然完成；持续娱乐到真实决策边界后由人物明确收尾。"""
    started = min(engine.context.state.activities[key].started_at for key in identities)
    def ended():
        activities = [engine.context.state.activities[key] for key in identities]
        finite = [row for row in activities if row.data.get("category") != "entertainment"]
        done = all(row.status != "active" for row in finite) if complete and finite else (
            engine.context.state.current_time > started)
        return owner in engine.scheduling_state().ready_agent_ids and done
    pump(engine, ended)
    names = [engine.context.state.activities[key].name for key in identities]
    result = perform(engine, f"habit-conclude-{engine.context.state.version}",
                     conclude_request(*names), "conclude_activity", owner)
    assert result.error_code is None, result.data
    assert all(engine.context.state.activities[key].elapsed_seconds > 0 for key in identities)
    return result


def _purchase(engine, case, item):
    """资金变化、订单与唯一本人收货分别核对；付款与订单状态不能冒充收货。"""
    owner = case["agent_id"]
    account = engine.context.state.agents[owner].balance_account_id
    before = engine.context.state.accounts[account]
    result = perform(engine, f"habit-buy-{engine.context.state.version}", _request(engine, item, owner), identity=owner)
    assert result.error_code is None, result.data
    [order] = result.data["orders"]
    goods = engine.context.semantics.catalog.goods[item["goods_id"]]
    assert order["goods_id"] == goods.goods_id and order["quantity"] == 1
    assert order["unit_price_fen"] == goods.price_fen
    assert order["app_id"] == item["app_id"] and order["payment_app_id"] == item["payment_app_id"]
    assert before - engine.context.state.accounts[account] == order["total_price_fen"]
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    receipts = [row for row in events if row.data.get("kind") == "goods_received"
                and row.data["order_id"] == order["order_id"]]
    assert len(receipts) == 1 and receipts[0].agent_id == owner
    assert receipts[0].data["goods_id"] == goods.goods_id


def _step(engine, case, item):
    """每一步经过正式原生入口，验证本人可追溯内容、位置、消息或收货证据。"""
    owner = case["agent_id"]
    if item["kind"] == "purchase":
        _purchase(engine, case, item)
        return
    if item["kind"] == "message":
        profile = next(row for row in engine.context.scenario.agents if row.agent_id == owner)
        contact = next(row for row in profile.contacts if row.contact_id == item["object_id"])
        value = SendMessageActionRequest(recipient_name=f"联系人：{contact.salutation}", text=item["text"])
        result = perform(engine, f"habit-message-{engine.context.state.version}", value, "send_message", owner)
        assert result.error_code is None, result.data
        assert any(row.text == item["text"] and row.recipient_id == contact.contact_id
                   for row in engine.context.state.agents[owner].outbox.values())
        return
    values = item["items"] if item["kind"] == "parallel" else [item]
    requests = [_request(engine, step, owner) for step in values]
    value = StartActivityRequest(items=[row.items[0] for row in requests], answering=None)
    result = perform(engine, f"habit-start-{engine.context.state.version}", value, identity=owner)
    assert result.error_code is None, result.data
    identities = [result.activity_id] if result.activity_id else [row["activity_id"] for row in result.data["activities"]]
    _finish(engine, identities, owner, item.get("complete", True))
    for identity in identities:
        activity = engine.context.state.activities[identity]
        if activity.kind == "action":
            assert activity.data["action_id"] in engine.context.semantics.catalog.actions
            consumed = [event.data["content"] for event in engine.context.store.read_agent_events("activity_progress", owner)
                        if event.activity_id == identity]
            assert consumed and all(row["position_after_seconds"] > row["position_before_seconds"] for row in consumed)
            assert sum(row["position_after_seconds"] - row["position_before_seconds"] for row in consumed) == activity.elapsed_seconds
        if activity.kind == "travel":
            assert engine.context.state.agents[owner].location_id == activity.data["destination_id"]


def test_every_frozen_habit_has_a_complete_explicit_native_fixture():
    """原九条编译习惯全部有当前显式链，播客与游戏由目录动作和内容表达。"""
    declared = {(agent["agent_id"], json.dumps(habit, sort_keys=True, ensure_ascii=False))
                for agent in FIXTURE["compiled_personas"] for habit in agent["habits"]}
    actual = {(case["agent_id"], json.dumps(case["habit"], sort_keys=True, ensure_ascii=False))
              for case in CASES if case.get("habit")}
    assert actual == declared and len(declared) == 9
    assert len(CASES) == len({row["name"] for row in CASES}) == 24
    assert all(row["chain"] and row["preconditions"] for row in CASES)
    forbidden = {"from", "from_app", "prefer", "food_id", "work_topic_ids", "inventory"}
    current = {key: value for key, value in FIXTURE.items() if key != "compiled_personas"}
    assert not forbidden.intersection(_keys(current))


def _keys(value):
    """夹具当前声明的字段集合，不把人设散文解释成世界字段。"""
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(row) for row in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(row) for row in value))
    return set()


@pytest.mark.parametrize("case", CASES, ids=[row["name"] for row in CASES])
def test_declared_habit_runs_with_real_prerequisites_and_settlement(engine_factory, case):
    """双人物原生链经过真实前置、付款、收货、内容和地点结算。"""
    engine = engine_factory("multi-person.json", mutate=lambda document: _configure(document, case))
    _prepare(engine, case)
    for item in case["chain"]:
        _step(engine, case, item)
    events = engine.context.store.read_agent_events("action_result", case["agent_id"])
    assert events and not engine.context.store.read_agent_events("run_failed", case["agent_id"])
    assert not any(row.data.get("status") == "rejected" for row in events)
