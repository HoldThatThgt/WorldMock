"""库存专属对象经真实观察、模型协议和结算进入下一次决策。"""

from __future__ import annotations

import copy
import json

import pytest

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.test_decision_context_roundtrip import _person, _RoundDispatcher
from tests.integration.test_life_agent_protocol import _ModelResponse, _context as _protocol_context, _observations
from tests.life.conftest import context
from tests.life.test_category_runtime import (
    _INPUT_OBJECT,
    _OUTPUT_CATEGORY,
    _OUTPUT_OBJECT,
    _advance_to_completion,
    _engine,
    _fixture,
    _read_json,
    _stage_open_market,
    _write_json,
)
from worldmock.categories import refresh_manifest_identity


def _recipe_path(tmp_path, coefficient=1, required_location=None):
    """复用真实配方夹具，给输入输出中文名称并让输出继承已定义的耐用品使用规则。"""
    path = _fixture(tmp_path, coefficient=coefficient)
    directory = path.parent.parent / "catalog"
    contract_path = directory / "category-contract.json"
    contract = _read_json(contract_path)
    contract["pairings"] = [row for row in contract["pairings"]
                            if row["target_category_id"] != _OUTPUT_CATEGORY]
    _write_json(contract_path, contract)
    objects_path = directory / "objects.jsonl"
    objects = [json.loads(line) for line in objects_path.read_text().splitlines()]
    for item in objects:
        if item["object_id"] == _INPUT_OBJECT:
            item["name"] = "密封工具盒"
            if required_location is not None:
                item["values"]["absolute"]["required_location_id"] = required_location
        elif item["object_id"] == _OUTPUT_OBJECT:
            item["name"] = "拆封工具盒"
    objects_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in objects))
    refresh_manifest_identity(directory)
    return path


def _use_request(product_id, quantity=1, mode="use", duration=300):
    """形成供真实模型响应携带的闭集消费参数。"""
    return {"items": [{"product_id": product_id, "quantity": quantity}],
            "use_mode": mode, "duration_seconds": duration}


async def _model_consume(engine, round_id, payload):
    """只替换远程模型传输，保留观察、自述、动作登记、结束与世界结算真实路径。"""
    environment = LifeEnv()
    environment.engine = engine
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observation = await environment.observe(current)
    dispatcher = _RoundDispatcher()
    dispatcher.responses[1] = _ModelResponse(
        "provider-action", "consume", json.dumps(payload), reasoning_content="Use the held item.")
    person = _person(environment, current, dispatcher)
    request = ReactLoopRequest(0, current.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation}])
    before = engine.state.model_dump(mode="json")
    await person.run_react_loop(request)
    assert engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 3
    assert "tools" not in dispatcher.requests[0]
    assert dispatcher.requests[1]["messages"][1] == dispatcher.requests[2]["messages"][1]
    assert "inventory_items" not in dispatcher.requests[1]["messages"][1]["content"]
    receipt = json.loads(dispatcher.requests[2]["messages"][-1]["content"])
    assert receipt["data"] == {}
    assert "registered for later settlement" in receipt["observation"]
    functions = {row["function"]["name"]: row["function"] for row in dispatcher.requests[1]["tools"]}
    return observation, functions, engine.commit_decision_round(current.round_id)[0]


def _item_ids(function):
    """读取模型实际收到的物品身份枚举。"""
    return function["parameters"]["properties"]["items"]["items"]["properties"]["product_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("coefficient", "quantity"), [(1, 1), (0.5, 2)])
async def test_inventory_only_recipe_survives_real_model_roundtrip(tmp_path, coefficient, quantity):
    """模型能消费配方输入，并在下一轮用输出；请求份数按规则缩放，不被原始库存量误拒。"""
    path = _recipe_path(tmp_path, coefficient)
    engine = _engine(tmp_path, path, "model-recipe")
    _stage_open_market(engine)
    try:
        observed, functions, started = await _model_consume(
            engine, "recipe-input", _use_request(_INPUT_OBJECT, quantity))
        assert observed["scene"]["inventory"] == ["密封工具盒"]
        assert observed["inventory_items"] == [{
            "product_id": _INPUT_OBJECT, "name": "密封工具盒", "use_modes": ["use"],
            "use_duration_seconds": 300, "required_location_id": None,
        }]
        assert _item_ids(functions["consume"])["enum"] == [_INPUT_OBJECT]
        assert f"{_INPUT_OBJECT}: 密封工具盒" in _item_ids(functions["consume"])["description"]
        assert _item_ids(functions["purchase"])["enum"]
        assert {_INPUT_OBJECT, _OUTPUT_OBJECT}.isdisjoint(_item_ids(functions["purchase"])["enum"])
        assert started.status.value == "in_progress"
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1}
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}

        after, next_functions, next_started = await _model_consume(
            engine, "recipe-output", _use_request(_OUTPUT_OBJECT))
        assert after["scene"]["inventory"] == ["拆封工具盒"]
        assert [row["product_id"] for row in after["inventory_items"]] == [_OUTPUT_OBJECT]
        assert _item_ids(next_functions["consume"])["enum"] == [_OUTPUT_OBJECT]
        assert f"{_OUTPUT_OBJECT}: 拆封工具盒" in _item_ids(next_functions["consume"])["description"]
        assert {_INPUT_OBJECT, _OUTPUT_OBJECT}.isdisjoint(_item_ids(next_functions["purchase"])["enum"])
        assert next_started.status.value == "in_progress"
        _advance_to_completion(engine, next_started.activity_id)
        assert "package_opened" in engine.state.agents[1].active_effects
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_insufficient_recipe_inventory_is_rejected_by_real_settlement(tmp_path):
    """模型可登记请求，但环境按实际配方用量原子拒绝不足库存。"""
    path = _recipe_path(tmp_path)
    engine = _engine(tmp_path, path, "model-shortage")
    try:
        inventory = copy.deepcopy(engine.state.agents[1].inventory)
        accounts = dict(engine.state.accounts)
        _, _, result = await _model_consume(engine, "recipe-shortage", _use_request(_INPUT_OBJECT, 2))
        assert result.status.value == "rejected"
        assert result.error_code == "PRODUCT_NOT_HELD"
        assert engine.state.agents[1].inventory == inventory
        assert engine.state.accounts == accounts
        assert engine.state.activities == {}
    finally:
        engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [1, 2])
async def test_held_food_can_be_eaten_without_a_visible_purchase_offer(tmp_path, quantity):
    """已持有食品不依赖商家报价可见期，真实进食扣减库存，超量由环境无损拒绝。"""
    path = _recipe_path(tmp_path)
    scenario = _read_json(path)
    product_id = scenario["products"][0]["product_id"]
    scenario["agents"][0]["initial_inventory"] = {"home": {product_id: 1}}
    _write_json(path, scenario)
    engine = _engine(tmp_path, path, "model-food")
    try:
        product = engine.rules.product(product_id)
        inventory = copy.deepcopy(engine.state.agents[1].inventory)
        observed, functions, started = await _model_consume(
            engine, "held-food", _use_request(
                product_id, quantity, mode="eat", duration=product.use_duration_seconds))
        assert product_id not in {row["product_id"] for row in observed["products"]}
        assert _item_ids(functions["consume"])["enum"] == [product_id]
        assert f"{product_id}: {product.name}" in _item_ids(functions["consume"])["description"]
        if quantity == 1:
            assert started.status.value == "in_progress"
            _advance_to_completion(engine, started.activity_id)
            assert engine.state.agents[1].inventory["home"][product_id] == 0
        else:
            assert started.status.value == "rejected"
            assert started.error_code == "PRODUCT_NOT_HELD"
            assert engine.state.agents[1].inventory == inventory
            assert engine.state.activities == {}
    finally:
        engine.close()


@pytest.mark.parametrize("required_location", [None, "home", "office"])
def test_inventory_use_contract_keeps_location_and_mode_boundaries(tmp_path, required_location):
    """异地持有物不进入本地使用合同，地点和使用方式继续收窄工具。"""
    path = _recipe_path(tmp_path, required_location=required_location)
    scenario = _read_json(path)
    scenario["agents"][0]["initial_inventory"]["office"] = {_OUTPUT_OBJECT: 1}
    _write_json(path, scenario)
    engine = _engine(tmp_path, path, "model-location")
    try:
        current = context(engine, "inventory-location")
        engine.begin_decision_round([current])
        observed = engine.observe(current)
        assert [row["product_id"] for row in observed["inventory_items"]] == [_INPUT_OBJECT]
        assert observed["available_inventory"] == {_INPUT_OBJECT: 1}
        agent = LifeAgent()
        agent.prepare_react_loop(ReactLoopRequest(0, current.observed_at, [
            {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
        functions = {row["function"]["name"]: row["function"]
                     for row in agent.build_react_tool_schemas(readonly=False)}
        if required_location == "office":
            assert "consume" not in functions
        else:
            assert _item_ids(functions["consume"])["enum"] == [_INPUT_OBJECT]
            assert functions["consume"]["parameters"]["properties"]["use_mode"]["enum"] == ["use"]
            error = agent.validate_react_decisions(
                [ReactDecision("", "consume", _use_request(_INPUT_OBJECT, mode="eat"), "")], False)
            assert "current local inventory" in error
    finally:
        engine.close()


def _prepare_observation(data):
    """通过人物入口校验给定观察，不绕过工具准备合同。"""
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _protocol_context().observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": data}]))
    return agent


@pytest.mark.parametrize("malformation", ["missing", "not-list", "not-object", "missing-field", "extra-field",
                                         "duplicate", "empty"])
def test_inventory_contract_requires_complete_rows_without_purchase_fallback(malformation, caplog):
    """即使商品报价完整，库存合同缺失、残缺或重复也必须报错并留日志。"""
    data = _observations()[0]["data"]
    assert data["products"][0]["product_id"] in data["available_inventory"]
    if malformation == "missing":
        data.pop("inventory_items")
    elif malformation == "not-list":
        data["inventory_items"] = {}
    elif malformation == "not-object":
        data["inventory_items"] = [None]
    elif malformation == "missing-field":
        data["inventory_items"][0].pop("required_location_id")
    elif malformation == "extra-field":
        data["inventory_items"][0]["price_version"] = "price-v1"
    elif malformation == "duplicate":
        data["inventory_items"].append(dict(data["inventory_items"][0]))
    else:
        data["inventory_items"] = []
    with pytest.raises(RuntimeError, match="inventory|held product contract"):
        _prepare_observation(data)
    assert any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("product_id", "", "identity or name"),
    ("name", " ", "identity or name"),
    ("use_modes", [], "use modes"),
    ("use_modes", "eat", "use modes"),
    ("use_modes", ["other"], "use modes"),
    ("use_modes", ["eat", "eat"], "use modes"),
    ("use_modes", [{}], "use modes"),
    ("use_duration_seconds", 0, "duration"),
    ("use_duration_seconds", True, "duration"),
    ("use_duration_seconds", 1.5, "duration"),
    ("required_location_id", 5, "required location"),
    ("required_location_id", "", "required location"),
])
def test_inventory_contract_rejects_invalid_use_semantics(field, value, message, caplog):
    """库存合同中的名称、模式、时长与地点不允许含无效值。"""
    data = _observations()[0]["data"]
    data["inventory_items"][0][field] = value
    with pytest.raises(RuntimeError, match=message):
        _prepare_observation(data)
    assert any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), True, "1"])
def test_inventory_contract_rejects_invalid_observed_quantities(quantity, caplog):
    """观察中的持有量必须是有限数值，不能伪造可使用物品。"""
    data = _observations()[0]["data"]
    data["available_inventory"]["product-visible"] = quantity
    with pytest.raises(RuntimeError, match="available inventory entry"):
        _prepare_observation(data)
    assert any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize("quantity", [0, -1, 101])
def test_consume_quantity_still_obeys_the_request_model_range(quantity):
    """把实际用量交给环境不取消消费请求本身的份数范围。"""
    agent = _prepare_observation(_observations()[0]["data"])
    result = agent.validate_react_decisions([
        ReactDecision("", "consume", _use_request("product-visible", quantity, mode="eat"), "")], False)
    assert "invalid consume arguments" in result
