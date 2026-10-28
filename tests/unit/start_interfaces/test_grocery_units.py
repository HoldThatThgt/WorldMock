"""凭状态的地点单元：未开放的单元不列在工具层、私有绑定里 locked 为真；照写名称仍能解析到它、交给受理拒绝；名称解析
给出的候选与列出的名称都不含它；未开放的拒绝文字，以及单元被占排在未开放之前
（docs/design/04-simulation-engine.md「出行、内容与查询」，06-agent.md「提示与工具」）。

都读真实冻结观察与真实受理上下文；夹具造不出的近名对象用一个手写对象。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import MAIN_TOOL, advance_until_ready, context, do_at, pause_request, travel
from tests.life.environment_inputs import grocery_environment
from tests.life.environment_runtime import act
from worldmock.simulation_engine.activities.lifecycle.activity_control import open_activities
from worldmock.state.interaction.actions import binding_action
from worldmock.simulation_engine.actions.admission import Blocked, Call, Context, group_refusal
from worldmock.simulation_engine.observation.wording.guidance import Refusal
from worldmock.simulation_engine.activities.content.location_activities import (
    location_units,
    locked_refusal,
    unit_binding,
    unit_locked,
)
from worldmock.state.interaction.requests import StartMainActivityRequest
from worldmock.simulation_engine.actions.name_resolution import NameBook, Option, Target, name_book, resolve_start
from worldmock.simulation_engine.world.world_updates import place_names

#: 收到扑克牌能开放「做饭」时，照写它得到的原因。
LOCKED_REASON = "做饭 is not available now; receiving 扑克牌 makes it available."


def grocery_engine(engine_factory, tmp_path, initial=0, goods=("playing-cards",)):
    """grocery_environment 副本上的真实引擎：人在家，groceries 的初值是 initial。"""
    return engine_factory(grocery_environment(tmp_path / "groceries", initial=initial, goods=goods))


def home_rows(observed) -> tuple[list, list, list]:
    """家在工具层的各行 detail，在 catalog_bindings 与 interaction_bindings 里各行的（detail，locked）。"""
    public = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "家")
    private = [[(action["detail"], action["locked"]) for action in row["actions"] if action["action"] == "interact"]
               for key in ("catalog_bindings", "interaction_bindings")
               for row in observed[key] if row["object"] == "家"]
    return [row["detail"] for row in public["actions"] if row["action"] == "interact"], *private


def test_a_locked_unit_is_left_out_of_the_tool_layer_but_kept_in_the_private_bindings(engine_factory, tmp_path):
    """状态为 0：家在工具层只列「测试闲聊」「测试休息」，两份私有绑定照旧含「做饭」且 locked 为真，其余单元为假；
    私有绑定里它的可用性说明就是未开放的原因。"""
    engine = grocery_engine(engine_factory, tmp_path)
    observed = engine.observe(context(engine, "look"))
    listed, catalog, bound = home_rows(observed)
    assert listed == ["测试闲聊", "测试休息"]
    assert catalog == bound == [("测试闲聊", False), ("测试休息", False), ("做饭", True)]
    home = next(row for row in observed["interaction_bindings"] if row["object"] == "家")
    assert next(action for action in home["actions"] if action["detail"] == "做饭")["note"] == LOCKED_REASON


def test_an_open_unit_is_listed_like_any_other_unit(engine_factory, tmp_path):
    """状态为 1：「做饭」照常按单元键升序列出，行的字段、时长和说明与别的单元同样写，说明只写供一餐，不提状态。"""
    engine = grocery_engine(engine_factory, tmp_path, initial=1)
    observed = engine.observe(context(engine, "look"))
    listed, catalog, bound = home_rows(observed)
    assert listed == ["测试闲聊", "测试休息", "做饭"]
    assert catalog == bound == [("测试闲聊", False), ("测试休息", False), ("做饭", False)]
    home = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "家")
    cook = next(row for row in home["actions"] if row["detail"] == "做饭")
    assert cook == {"action": "interact", "detail": "做饭", "available": True, "reason": None, "price": None,
                    "duration": "约 1 小时", "quantity": None, "note": "供一餐"}


def test_unit_locked_reads_only_the_state_of_the_moment(engine_factory, tmp_path):
    """只有写了 consumes_state 的单元会未开放，且只看本人此刻这个状态的值；locked 原样写进单元的私有动作行。"""
    engine = grocery_engine(engine_factory, tmp_path)
    agent = engine.context.state.agents[1]
    units = {unit.name: unit for unit in location_units(engine.context.catalog, "home")}
    opened = agent.model_copy(update={"callback_states": {"groceries": 1}})
    assert [unit_locked(unit.contents, agent) for unit in units.values()] == [False, False, True]
    assert [unit_locked(unit.contents, opened) for unit in units.values()] == [False, False, False]
    assert unit_binding(units["做饭"], True)["locked"] is True


def test_the_name_of_a_locked_unit_still_resolves(engine_factory, tmp_path):
    """照写未开放的单元名：名称解析找到这一行，交给受理。"""
    engine = grocery_engine(engine_factory, tmp_path)
    outcome = resolve_start(name_book(engine.observe(context(engine, "names"))), MAIN_TOOL, do_at("家", "做饭"))
    assert outcome.failures == ()
    [row] = outcome.rows
    assert (row["object_id"], row["selected_action"]["detail"]) == ("home", "做饭")
    assert row["selected_action"]["content_ids"] == ["cook"] and row["selected_action"]["locked"] is True


def _detail_item(engine) -> dict:
    """写错的单元名「做饭吧」（与「做饭」最接近）这一项名称失败的明细。"""
    outcome = resolve_start(name_book(engine.observe(context(engine, "typo"))), MAIN_TOOL, do_at("家", "做饭吧"))
    [failure] = outcome.failures
    return failure.item.payload()


@pytest.mark.parametrize("initial,offered", [(0, []), (1, ["做饭"])], ids=["locked", "open"])
def test_a_misspelt_name_never_offers_a_locked_unit(engine_factory, tmp_path, initial, offered):
    """写错的名称与未开放的「做饭」最接近：候选里没有它，允许值也只有列出的两个单元；开放之后它是唯一的候选。"""
    item = _detail_item(grocery_engine(engine_factory, tmp_path, initial=initial))
    assert item["error_code"] == "NAME_NOT_FOUND"
    assert item["allowed_values"] == ["测试闲聊", "测试休息", *offered]
    assert [candidate["arguments"]["detail"] for candidate in item["candidates"]] == offered


@pytest.mark.parametrize("initial,offered", [(0, ["测试闲聊", "测试休息"]), (1, ["测试闲聊", "测试休息", "做饭"])],
                         ids=["locked", "open"])
def test_an_action_the_place_does_not_offer_lists_no_locked_unit(engine_factory, tmp_path, initial, offered):
    """在家写「步行去家」：家不提供出行，候选是家列出的单元，没有未开放的「做饭」。"""
    engine = grocery_engine(engine_factory, tmp_path, initial=initial)
    outcome = resolve_start(name_book(engine.observe(context(engine, "travel"))), MAIN_TOOL, travel("家"))
    [failure] = outcome.failures
    assert failure.item.error_code == "ACTION_NOT_OFFERED"
    assert [candidate.arguments["detail"] for candidate in failure.item.candidates] == offered


def _place_option(detail: str, locked: bool, blocked: str | None = None) -> Option:
    """手写的一条地点单元行。"""
    row = {**binding_action("interact", detail), "note": blocked, "content_ids": [detail], "episode_titles": [],
           "locked": locked}
    return Option("interact", detail, blocked, None, (), row)


def test_a_near_object_candidate_never_copies_a_locked_unit():
    """对象名写错、近名对象是一个地点：候选照抄所写 detail 对上的那一行，未开放时改抄此刻可行的第一行。"""
    options = (_place_option("做饭", True, LOCKED_REASON), _place_option("测试休息", False))
    row = {"object": "厨房小屋", "kind": "location", "object_id": "kitchen",
           "actions": [dict(item.row) for item in options]}
    book = NameBook((Target("厨房小屋", "location", "kitchen", options, True, row),), "厨房小屋", "厨房小屋")
    request = StartMainActivityRequest.model_validate({"action": "interact", "target": "厨房小", "detail": "做饭"})
    [failure] = resolve_start(book, MAIN_TOOL, request).failures
    assert failure.item.error_code == "NAME_NOT_FOUND"
    assert [candidate.arguments for candidate in failure.item.candidates] == [
        {"action": "interact", "target": "厨房小屋", "detail": "测试休息"}]


def _refusal(engine, request) -> Blocked | None:
    """按引擎受理时的同一份上下文，核验从此刻冻结观察解析出的这条开始调用。"""
    observed = engine.observe(context(engine, f"check-{engine.context.state.current_time}"))
    outcome = resolve_start(name_book(observed), MAIN_TOOL, request)
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    current = Context(engine.context.state, 1, open_activities(engine.context.state, 1), place_names(engine.context.catalog), names, engine.context.inputs)
    return group_refusal(current, [Call(MAIN_TOOL, request.action, outcome.rows)])


@pytest.mark.parametrize("goods,reason", [
    (("playing-cards",), LOCKED_REASON),
    (("tissue", "playing-cards"), "做饭 is not available now; receiving 扑克牌 or 纸巾 makes it available."),
    ((), "做饭 is not available now."),
], ids=["one-goods", "two-goods-in-goods-id-order", "no-reset-goods"])
def test_a_locked_unit_is_refused_with_the_goods_that_open_it(engine_factory, tmp_path, goods, reason):
    """未开放的拒绝：码 ACTIVITY_PRECONDITION_UNMET，原因写单元名；reset_on_goods 非空时写收到哪些商品能开放它，
    商品名按 goods_id 升序（playing-cards 在 tissue 之前）以 or 相连，为空时只写不可用；没有下一步和改法，
    未满足条件是 callback_state。"""
    engine = grocery_engine(engine_factory, tmp_path, goods=goods)
    expected = Refusal("ACTIVITY_PRECONDITION_UNMET", reason, unmet=("callback_state",))
    assert _refusal(engine, do_at("家", "做饭")) == Blocked(expected, (MAIN_TOOL,))
    action = next(unit_binding(unit, True) for unit in location_units(engine.context.catalog, "home")
                  if unit.name == "做饭")
    assert locked_refusal(engine.context.catalog, engine.context.state.agents[1], action) == expected


def test_an_open_unit_is_not_refused(engine_factory, tmp_path):
    """状态为 1 时「做饭」整组成立，locked_refusal 为 None。"""
    engine = grocery_engine(engine_factory, tmp_path, initial=1)
    assert _refusal(engine, do_at("家", "做饭")) is None


def test_a_held_unit_reports_the_paused_activity_before_it_reports_locked(engine_factory, tmp_path):
    """开始「做饭」（状态 1 到 0）再暂停：此刻它既被暂停的活动占着又未开放，照写它得到单元被占的拒绝，点名那项活动。"""
    engine = grocery_engine(engine_factory, tmp_path, initial=1)
    _, cook = act(engine, "cook", do_at("家", "做饭"))
    assert cook.status == "in_progress", cook.error_code
    assert advance_until_ready(engine)
    name = engine.context.state.activities[cook.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    assert engine.context.state.agents[1].callback_states == {"groceries": 0}
    held = Refusal("RESOURCE_CONFLICT", f"做饭 is held by the paused activity {name}.",
                   f"Resume {name} with report_status, or conclude it.", ("content_unit",))
    assert _refusal(engine, do_at("家", "做饭")) == Blocked(held, (MAIN_TOOL,))
