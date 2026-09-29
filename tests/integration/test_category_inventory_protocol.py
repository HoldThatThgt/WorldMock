"""库存对象经人物可读动作和真实结算进入下一轮观察。"""

from __future__ import annotations

import json
import os
import copy

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.envs.life_env import LifeEnv
from tests.integration.test_interaction_menu_roundtrip import _action_schema, _person
from tests.integration.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import conclude_pending, context, start_arguments
from tests.life.test_category_runtime import (
    _INPUT_OBJECT, _OUTPUT_CATEGORY, _OUTPUT_OBJECT, _advance_to_completion,
    _engine, _fixture, _read_json, _write_json,
)
from worldmock.categories import refresh_manifest_identity
from worldmock.life import prompt_capacity

#: 单人隔离场景里身边没有能给的人：给予动作标不可用，原因与受理的拒绝一字不差（worldmock.life.giving.handover_refusal）。
_NOBODY_GIVE = ("consume", "给予", False, "要和对方在同一个地方才能给")


class _Dispatcher:
    """只替换模型传输，保持人物、环境和事件结算真实；世界请求登记即结束本轮，没有 finish。"""

    model_name = "deepseek-flash"

    def __init__(self, arguments: dict) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _ModelResponse("provider-action", "start_activity", json.dumps(arguments, ensure_ascii=False)),
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
    """让真实人物按冻结观察中的持有物名称提交它自用的那项动作（配方为 obtain 的「制作」，否则 consume 的「使用」；
    另一项是给予）。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)
    environment = LifeEnv()
    environment.engine = engine
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observation = await environment.observe(current)
    binding = next(row for row in observation["interaction_bindings"]
                   if row["kind"] == "holding" and row["object_id"] == product_id)
    [offered] = [item for item in binding["actions"] if item["label"] != "给予"]
    choice = start_arguments([binding["object"]], [offered["verb"]], [offered["label"]])
    dispatcher = _Dispatcher(choice)
    person = _person(environment, current, dispatcher)
    before = engine.state.model_dump(mode="json")
    request = ReactLoopRequest(0, current.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation},
    ])
    await person.run_react_loop(request)
    assert engine.state.model_dump(mode="json") == before
    assert len(dispatcher.requests) == 1
    action_request = dispatcher.requests[0]
    assert set(_action_schema(action_request)["parameters"]["properties"]) == (
        {"targets", "actions", "ways", "from", "to", "answering", "prefer"})
    assert binding["object"] in action_request["messages"][1]["content"]
    assert product_id not in action_request["messages"][1]["content"]
    return observation, engine.commit_decision_round(round_id)[0]


def _offered(observation: dict, name: str) -> list[tuple]:
    """工具层中该持有物目标列出的（动词，方式，可用，原因）；目标名称唯一。"""
    [target] = [row for row in observation["tool_layer"]["targets"] if row["name"] == name]
    assert target["kind"] == "product"
    return [(item["action"], item["way"], item["available"], item["reason"]) for item in target["actions"]]


@pytest.mark.asyncio
async def test_held_durable_recipe_survives_real_model_and_engine_roundtrip(tmp_path, monkeypatch):
    """人物只选持有物与方式：配方输入列 obtain 的「制作」，领域规则完成配方转换；用完当秒收尾后，下一轮的产物
    列 consume 的「使用」（docs/dev/SPEC-time-and-energy.md 第五章「请求形状与菜单」）。"""
    path = _recipe_path(tmp_path)
    engine = _engine(tmp_path, path, "readable-recipe")
    try:
        before, started = await _use_held_item(engine, "recipe-input", _INPUT_OBJECT, monkeypatch)
        assert _offered(before, "持有物：密封工具盒") == [("obtain", "制作", True, None), _NOBODY_GIVE]
        assert started.status.value == "in_progress"
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1}
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
        assert conclude_pending(engine, "recipe-input-conclude").status.value == "completed"
        after, next_started = await _use_held_item(engine, "recipe-output", _OUTPUT_OBJECT, monkeypatch)
        assert _offered(after, "持有物：拆封工具盒") == [("consume", "使用", True, None), _NOBODY_GIVE]
        assert all(row["name"] != "持有物：密封工具盒" for row in after["tool_layer"]["targets"])
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
        assert [(action["verb"], action["label"], action.get("use_mode")) for action in held["actions"]] == [
            ("consume", "食用", "eat"), ("consume", "给予", None)]
        assert _offered(observed, held["object"]) == [("consume", "食用", True, None), _NOBODY_GIVE]
    finally:
        engine.close()


@pytest.mark.parametrize("required_location", [None, "home", "office"])
def test_held_durable_preserves_location_use_rule(tmp_path, required_location):
    """地点条件仍由领域结算判断：只能在别处用的持有物照常列出「制作」，标不可用并写明点名地点名称的原因（与私有
    绑定的 note 同一文本）；工具层不承载地点编号（docs/dev/SPEC-persona-prompt.md 第一章「工具层」）。"""
    path = _recipe_path(tmp_path, required_location=required_location)
    engine = _engine(tmp_path, path, f"location-{required_location}")
    try:
        current = context(engine, "location-observation")
        engine.begin_decision_round([current])
        observed = engine.observe(current)
        held = next(row for row in observed["interaction_bindings"]
                    if row["kind"] == "holding" and row["object_id"] == _INPUT_OBJECT)
        assert [(action["verb"], action["label"], action["canonical_action"], action.get("use_mode"))
                for action in held["actions"]] == [("obtain", "制作", "consume", "use"),
                                                   ("consume", "给予", "consume", None)]
        [(verb, way, available, reason), give] = _offered(observed, held["object"])
        assert give == _NOBODY_GIVE
        assert (verb, way) == ("obtain", "制作")
        if required_location == "office":
            office = next(item.name for item in engine.scenario.locations if item.location_id == "office")
            assert available is False and office in reason
            assert held["actions"][0]["note"] == reason
        else:
            assert (available, reason) == (True, None)
        tool_layer = json.dumps(observed["tool_layer"], ensure_ascii=False)
        assert "product_id" not in tool_layer and "required_location_id" not in tool_layer
        assert "office" not in tool_layer
    finally:
        engine.close()
