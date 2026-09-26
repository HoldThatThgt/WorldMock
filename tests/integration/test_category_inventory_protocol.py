"""库存对象经人物可读动作和真实结算进入下一轮观察。"""

from __future__ import annotations

import json
import os
import copy

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.envs.life_env import LifeEnv
from tests.integration.test_interaction_menu_roundtrip import _action_schema, _person
from tests.integration.test_life_agent_protocol import _DecisionStatementResponse, _ModelResponse
from tests.life.conftest import conclude_pending, context
from tests.life.test_category_runtime import (
    _INPUT_OBJECT, _OUTPUT_CATEGORY, _OUTPUT_OBJECT, _advance_to_completion,
    _engine, _fixture, _read_json, _write_json,
)
from worldmock.categories import refresh_manifest_identity
from worldmock.life import prompt_capacity


class _Dispatcher:
    """只替换模型传输，保持人物、环境和事件结算真实；世界请求登记即结束本轮，没有 finish。"""

    model_name = "deepseek-flash"

    def __init__(self, target: str, action: str) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _DecisionStatementResponse("我使用眼前的物品。"),
            _ModelResponse("provider-action", "start_activity",
                           json.dumps({"targets": [target], "actions": [action]}, ensure_ascii=False)),
        ]

    async def call(self, **request):
        """保存真实请求后返回模型协议响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def _recipe_path(tmp_path, required_location=None):
    """给隔离配方输入输出中文名称，并声明输出可继续使用。"""
    path = _fixture(tmp_path)
    directory = path.parent.parent / "catalog"
    contract_path = directory / "category-contract.json"
    contract = _read_json(contract_path)
    contract["pairings"] = [row for row in contract["pairings"]
                            if row["target_category_id"] != _OUTPUT_CATEGORY]
    _write_json(contract_path, contract)
    objects_path = directory / "objects.jsonl"
    objects = [json.loads(line) for line in objects_path.read_text(encoding="utf-8").splitlines()]
    for item in objects:
        if item["object_id"] == _INPUT_OBJECT:
            item["name"] = "密封工具盒"
            if required_location is not None:
                item["values"]["absolute"]["required_location_id"] = required_location
        elif item["object_id"] == _OUTPUT_OBJECT:
            item["name"] = "拆封工具盒"
    objects_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in objects),
                            encoding="utf-8")
    refresh_manifest_identity(directory)
    return path


async def _use_held_item(engine, round_id: str, product_id: str, monkeypatch):
    """让真实人物按冻结观察中的持有物名称提交一次使用动作。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)
    environment = LifeEnv()
    environment.engine = engine
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observation = await environment.observe(current)
    binding = next(row for row in observation["interaction_bindings"]
                   if row["kind"] == "holding" and row["object_id"] == product_id)
    dispatcher = _Dispatcher(binding["object"], "使用")
    person = _person(environment, current, dispatcher)
    before = engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, current.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 2
    action_request = dispatcher.requests[1]
    assert set(_action_schema(action_request)["parameters"]["properties"]) == {"targets", "actions"}
    assert binding["object"] in action_request["messages"][1]["content"]
    assert product_id not in action_request["messages"][1]["content"]
    return observation, engine.commit_decision_round(round_id)[0]


@pytest.mark.asyncio
async def test_held_durable_recipe_survives_real_model_and_engine_roundtrip(tmp_path, monkeypatch):
    """人物只选持有物与使用，领域规则完成配方转换；用完当秒收尾后，下一轮提供产物。"""
    path = _recipe_path(tmp_path)
    engine = _engine(tmp_path, path, "readable-recipe")
    try:
        before, started = await _use_held_item(engine, "recipe-input", _INPUT_OBJECT, monkeypatch)
        assert before["scene"]["inventory"] == ["密封工具盒"]
        assert {"object": "持有物：密封工具盒", "actions": ["使用"], "preconditions_met": True} \
            in before["facts"]["interactive_objects"]
        assert started.status.value == "in_progress"
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1}
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
        assert conclude_pending(engine, "recipe-input-conclude").status.value == "completed"
        after, next_started = await _use_held_item(engine, "recipe-output", _OUTPUT_OBJECT, monkeypatch)
        assert after["scene"]["inventory"] == ["拆封工具盒"]
        assert any(row["object"] == "持有物：拆封工具盒" for row in after["facts"]["interactive_objects"])
        assert next_started.status.value == "in_progress"
        _advance_to_completion(engine, next_started.activity_id)
        assert "package_opened" in engine.state.agents[1].active_effects
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
    finally:
        engine.close()


def test_held_food_is_offered_for_eating_without_a_purchase_quote(tmp_path):
    """手边食品的食用动作来自库存，不取决于可见门店报价。"""
    path = _recipe_path(tmp_path)
    scenario = _read_json(path)
    product_id = scenario["products"][0]["product_id"]
    scenario["agents"][0]["initial_inventory"] = {"home": {product_id: 1}}
    _write_json(path, scenario)
    engine = _engine(tmp_path, path, "readable-food")
    try:
        current = context(engine, "food-observation")
        engine.begin_decision_round([current])
        observed = engine.observe(current)
        held = next(row for row in observed["interaction_bindings"]
                    if row["kind"] == "holding" and row["object_id"] == product_id)
        assert [action["label"] for action in held["actions"]] == ["食用"]
        assert held["object"] in {row["object"] for row in observed["facts"]["interactive_objects"]}
        assert all(row["object"] != held["object"] for row in observed["facts"]["products"])
    finally:
        engine.close()


@pytest.mark.parametrize("required_location", [None, "home", "office"])
def test_held_durable_preserves_location_use_rule(tmp_path, required_location):
    """地点条件仍由领域结算判断，模型看到的动作不承载地点编号。"""
    path = _recipe_path(tmp_path, required_location=required_location)
    engine = _engine(tmp_path, path, f"location-{required_location}")
    try:
        current = context(engine, "location-observation")
        engine.begin_decision_round([current])
        observed = engine.observe(current)
        held = next(row for row in observed["interaction_bindings"]
                    if row["kind"] == "holding" and row["object_id"] == _INPUT_OBJECT)
        assert [action["label"] for action in held["actions"]] == ["使用"]
        assert all("product_id" not in row for row in observed["facts"]["inventory"])
        assert all("required_location_id" not in row for row in observed["facts"]["interactive_objects"])
    finally:
        engine.close()
