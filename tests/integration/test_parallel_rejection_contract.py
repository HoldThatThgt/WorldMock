"""start_activity 的整组合同：（对象，动词，方式）经本轮私有绑定映射为内部动作后由地点菜单逐项判定；并行准入
整组拒绝，原因与下一步经下一次观察呈现给人物（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「请求形状与菜单」
「并行准入」「动作照常列出，做错了再说明」）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.agents.life_menu import menu_action_error
from custom.envs.life_env import LifeEnv
from tests.integration.test_interaction_menu_roundtrip import _decide
from tests.integration.test_life_agent_protocol import _WAIT_CHOICE, _offer, _visible_object
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan, start_request, start_arguments)  # noqa: F401
from worldmock.life import prompt_capacity
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.models import StartActivityRequest
from tests.integration.test_life_agent_protocol import _observations

_PRODUCT = "牛肉面"
#: 完整冻结观察中的动作与商品都能解析，地点菜单仍逐项审查整个请求。
_OBSERVATION = _observations()[0]["data"]
_OBSERVATION["actions"] = [
    {"action_id": "feed", "name": "刷推荐", "category": "entertainment", "manner": "phone_continuous",
     "app_ids": ["douyin"], "communication_mode": None},
    {"action_id": "course", "name": "学课程", "category": "work_study", "manner": "other_device",
     "app_ids": ["bilibili"], "communication_mode": None}]
_OBSERVATION["interaction_bindings"] = [
    _visible_object("自己", "self", "self", _offer("interact", "等待", "wait"), _offer("sleep", "睡觉", "sleep"))[1],
    {"object": _PRODUCT, "kind": "supply", "object_id": "beef-noodles", "goods_id": "beef-noodles",
     "actions": [_offer("obtain", "购买", "purchase")]}]
_MENU = InteractionMenu(allowed_actions=("sleep", "wait"))
_DENIED = "interaction menu denies start_activity"


def _menu_error(targets: list[str], actions: list[str], ways: list[str]) -> str:
    """按本轮私有绑定解析所选各项的内部动作，再由冻结的地点菜单判定整组。"""
    request = start_request(targets=targets, actions=actions, ways=ways)
    outcome = resolve_start(name_book(_OBSERVATION), request)
    selected = None if outcome.failures else list(outcome.rows)
    assert selected is not None
    return menu_action_error(_MENU, "start_activity",
                             [item["selected_action"]["canonical_action"] for item in selected])


@pytest.mark.parametrize("target,verb,way", [
    ("刷推荐", "interact", "进行"), ("学课程", "interact", "进行"), (_PRODUCT, "obtain", "购买"),
])
def test_location_menu_denies_actions_outside_its_closed_set(target: str, verb: str, way: str) -> None:
    """@brief 人物能点名对象，仍不能绕过地点已冻结的动作允许集合：方式经私有绑定映射为内部动作后判定。"""
    assert _menu_error([target], [verb], [way]) == _DENIED


@pytest.mark.parametrize("verb,way", [("interact", "等待"), ("sleep", "睡觉")])
def test_protected_self_actions_remain_usable(verb: str, way: str) -> None:
    """@brief 自身的等待和睡觉是地点菜单必须保留的动作。"""
    assert _menu_error(["自己"], [verb], [way]) == ""


@pytest.mark.parametrize("wait_first", [True, False])
def test_menu_denies_the_whole_group_when_one_item_is_outside_it(wait_first: bool) -> None:
    """@brief 一个请求整组算一个生活动作：两项中有一项在菜单外，整组被菜单拒绝，与组内先后无关。"""
    pair = [("自己", "interact", "等待"), ("刷推荐", "interact", "进行")]
    targets, actions, ways = (list(column) for column in zip(*(pair if wait_first else pair[::-1])))
    assert _menu_error(targets, actions, ways) == _DENIED


def _world(engine) -> tuple:
    """@brief 整组拒绝要保持不变的世界事实：位置、账户余额、内容进度与活动。"""
    agent = engine.context.state.agents[1]
    return agent.location_id, dict(engine.context.state.accounts), dict(engine.context.state.activities), dict(agent.content_progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("drive_first", [True, False])
async def test_conflicting_pair_is_rejected_as_a_whole_and_explained_to_the_person(
        _engine_factory, monkeypatch, drive_first: bool) -> None:  # noqa: F811
    """@brief 人物经真实工具面同时选驾车去办公室与刷推荐（都占眼和手）：整组以 RESOURCE_CONFLICT 拒绝，与组内先后
    无关，两项都不开始，位置、余额、活动和内容进度不变；同一秒的下一轮观察在进度层该调用的结果里写明原因与下一步，并进入
    发给人物模型的内容（原因与下一步不含内部身份）。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)
    engine = _engine_factory(mutate=lambda document: document["agents"][0].update(
        owns_car=True, initial_driving_days=7))
    stage_day_plan(engine)
    environment = LifeEnv()
    environment.engine = engine
    first = context(engine, "conflicting-pair")
    engine.begin_decision_round([first])
    observed = await environment.observe(first)
    office = next(row for row in observed["interaction_bindings"] if row["object_id"] == "office")
    app = next(row for row in observed["interaction_bindings"]
                if row["kind"] == "action" and row["object"] == "刷推荐")
    pair = [(office["object"], "travel", "驾车"), (app["object"], "interact", "进行")]
    targets, actions, ways = (list(column) for column in zip(*(pair if drive_first else pair[::-1])))
    payload = start_arguments(targets, actions, ways, app=[None if way == "驾车" else "抖音" for way in ways])
    before = _world(engine)
    await _decide(environment, first, observed, "start_activity",
                  payload)
    [result] = engine.commit_decision_round(first.round_id)

    assert (result.status.value, result.error_code) == ("rejected", "RESOURCE_CONFLICT")
    assert _world(engine) == before
    reason, next_step = result.data["reason"], result.data["next_step"]
    assert next_step == "Choose only one of the conflicting activities."
    assert all(row["object"].split("：", 1)[-1] in reason for row in (office, app))
    for text in (reason, next_step):
        assert office["object_id"] not in text and app["object_id"] not in text and "agent:" not in text
    second = context(engine, "conflict-explained")
    engine.begin_decision_round([second])
    shown = await environment.observe(second)
    latest = [event for event in shown["progress_layer"]["events"] if event["type"] == "tool_call"][-1]
    assert (latest["tool"], latest["arguments"]) == ("start_activity",
                                                     payload)
    assert (latest["result"]["status"], latest["result"]["error_code"]) == ("rejected", "RESOURCE_CONFLICT")
    assert (latest["result"]["details"]["reason"], latest["result"]["details"]["next_step"]) == (reason, next_step)
    requests = await _decide(environment, second, shown, "start_activity", _WAIT_CHOICE)
    assert reason in requests[0]["messages"][1]["content"]
    assert next_step in requests[0]["messages"][1]["content"]
