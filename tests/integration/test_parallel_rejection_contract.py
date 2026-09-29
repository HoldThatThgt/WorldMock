"""start_activity 的整组合同：（对象，动词，方式）经本轮私有绑定映射为内部动作后由地点菜单逐项判定；并行准入
整组拒绝，原因与下一步经下一次观察呈现给人物（docs/dev/SPEC-time-and-energy.md 第五章「请求形状与菜单」
「并行准入」「动作照常列出，做错了再说明」）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.agents.life_menu import menu_action_error
from custom.envs.life_env import LifeEnv
from tests.integration.test_interaction_menu_roundtrip import _decide
from tests.integration.test_life_agent_protocol import _WAIT_CHOICE, _offer, _visible_object
from tests.life.conftest import (context, engine_factory as _engine_factory, stage_day_plan, start_request,
                                 start_arguments)  # noqa: F401
from worldmock.life import prompt_capacity
from worldmock.life.interaction_menu import InteractionMenu, resolve_selection

_MILK = "便利店出售的牛奶"
#: 本轮私有绑定：游戏的游玩与观看、内容的接着看、供给的远程下单都在只含等待与睡觉的菜单之外。
_BINDINGS = [binding for _, binding in (
    _visible_object("自己", "self", "self", _offer("interact", "等待", "wait"), _offer("sleep", "睡觉", "sleep")),
    _visible_object("游戏：象棋", "game", "game-1", _offer("interact", "游玩", "play")),
    _visible_object("游戏：象棋直播", "game", "stream-1", _offer("interact", "观看", "watch")),
    _visible_object("内容：纪录片", "content", "content-1", _offer("interact", "接着看", "watch")),
    _visible_object(_MILK, "supply", "supply-1", _offer("obtain", "远程下单", "purchase", fulfillment="delivery")),
)]
_MENU = InteractionMenu(allowed_actions=("sleep", "wait"))
_DENIED = "interaction menu denies start_activity"


def _menu_error(targets: list[str], actions: list[str], ways: list[str]) -> str:
    """按本轮私有绑定解析所选各项的内部动作，再由冻结的地点菜单判定整组。"""
    request = start_request(targets=targets, actions=actions, ways=ways)
    selected = resolve_selection(_BINDINGS, request.targets, request.actions, request.ways)
    assert selected is not None
    return menu_action_error(_MENU, "start_activity",
                             [item["selected_action"]["canonical_action"] for item in selected])


@pytest.mark.parametrize("target,verb,way", [
    ("游戏：象棋", "interact", "游玩"), ("游戏：象棋直播", "interact", "观看"),
    ("内容：纪录片", "interact", "接着看"), (_MILK, "obtain", "远程下单"),
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
    pair = [("自己", "interact", "等待"), ("游戏：象棋", "interact", "游玩")]
    targets, actions, ways = (list(column) for column in zip(*(pair if wait_first else pair[::-1])))
    assert _menu_error(targets, actions, ways) == _DENIED


def _world(engine) -> tuple:
    """@brief 整组拒绝要保持不变的世界事实：位置、账户余额、库存与活动。"""
    agent = engine.state.agents[1]
    return agent.location_id, dict(engine.state.accounts), agent.inventory, dict(engine.state.activities)


@pytest.mark.asyncio
@pytest.mark.parametrize("drive_first", [True, False])
async def test_conflicting_pair_is_rejected_as_a_whole_and_explained_to_the_person(
        _engine_factory, monkeypatch, drive_first: bool) -> None:  # noqa: F811
    """@brief 人物经真实工具面同时选驾车去办公室与游玩（都占眼和手）：整组以 RESOURCE_CONFLICT 拒绝，与组内先后
    无关，两项都不开始，位置、余额、库存不变；同一秒的下一轮观察在进度层该调用的结果里写明原因与下一步，并进入
    发给人物模型的内容（原因与下一步不含内部身份）。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_000_000)
    engine = _engine_factory()
    stage_day_plan(engine)
    environment = LifeEnv()
    environment.engine = engine
    first = context(engine, "conflicting-pair")
    engine.begin_decision_round([first])
    observed = await environment.observe(first)
    office = next(row for row in observed["interaction_bindings"] if row["object_id"] == "office")
    game = next(row for row in observed["interaction_bindings"]
                if row["kind"] == "game" and any(action["label"] == "游玩" for action in row["actions"]))
    pair = [(office["object"], "travel", "驾车"), (game["object"], "interact", "游玩")]
    targets, actions, ways = (list(column) for column in zip(*(pair if drive_first else pair[::-1])))
    before = _world(engine)
    await _decide(environment, first, observed, "start_activity",
                  start_arguments(targets, actions, ways))
    [result] = engine.commit_decision_round(first.round_id)

    assert (result.status.value, result.error_code) == ("rejected", "RESOURCE_CONFLICT")
    assert _world(engine) == before
    reason, next_step = result.data["reason"], result.data["next_step"]
    assert next_step == "一次只选其中一件"
    assert all(row["object"].split("：", 1)[1] in reason for row in (office, game))
    for text in (reason, next_step):
        assert office["object_id"] not in text and game["object_id"] not in text and "agent:" not in text
    second = context(engine, "conflict-explained")
    engine.begin_decision_round([second])
    shown = await environment.observe(second)
    latest = [event for event in shown["progress_layer"]["events"] if event["type"] == "tool_call"][-1]
    assert (latest["tool"], latest["arguments"]) == ("start_activity",
                                                     start_arguments(targets, actions, ways))
    assert (latest["result"]["status"], latest["result"]["error_code"]) == ("rejected", "RESOURCE_CONFLICT")
    assert (latest["result"]["details"]["reason"], latest["result"]["details"]["next_step"]) == (reason, next_step)
    requests = await _decide(environment, second, shown, "start_activity", _WAIT_CHOICE)
    assert reason in requests[0]["messages"][1]["content"]
    assert next_step in requests[0]["messages"][1]["content"]
