"""交互菜单按映射后的内部动作准入；人物只用五个动词与方式短语，旧的细分动作短语不留兼容入口（第五章）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from worldmock.catalog.affordances import START_ACTIVITY_ACTIONS
from worldmock.errors import IntegrityError
from worldmock.life.vocabulary import ACTION_WAYS, CONTENT_WAYS, LIFE_VERBS, PURCHASE_WAYS, USE_WAYS, VERB_WAY_SEPARATOR, WAYS
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.interaction_menu import InteractionMenu, activity_phrase, binding_action, menu_allows_action, merged_selection, public_action
from tests.life.conftest import select_start, start_arguments
from worldmock.life.models import REQUEST_MODELS
from worldmock.life.world_setup import load_scenario


def _bindings() -> list[dict]:
    """本轮私有绑定：一个地点（步行、驾车）与「自己」（等待、睡觉）。"""
    return [
        {"object": "地点：办公室", "kind": "location", "object_id": "office",
         "actions": [binding_action("步行", "travel_to", travel_mode="walking"),
                     binding_action("驾车", "travel_to", travel_mode="driving")]},
        {"object": "自己", "kind": "self", "object_id": "agent:1",
         "actions": [binding_action("等待", "wait"), binding_action("睡觉", "sleep")]},
    ]


def test_every_way_maps_to_one_verb_and_covers_every_internal_action():
    """方式闭集覆盖全部内部标准动作与五个动词；各类专门方式都回到各自的内部动作，旧短语不在闭集中。"""
    assert "submit" not in START_ACTIVITY_ACTIONS
    assert "message" not in START_ACTIVITY_ACTIONS
    assert {canonical for _, canonical in WAYS.values()} == START_ACTIVITY_ACTIONS
    assert {verb for verb, _ in WAYS.values()} == set(LIFE_VERBS)
    assert LIFE_VERBS == ("travel", "interact", "obtain", "consume", "sleep")
    for action, way in ACTION_WAYS.items():
        assert WAYS[way][1] == action
    assert {WAYS[way] for way in TRAVEL_WAYS.values()} == {("travel", "travel_to")}
    assert {WAYS[way] for way in PURCHASE_WAYS.values()} == {("obtain", "purchase")}
    assert {WAYS[way] for way in USE_WAYS.values()} == {("consume", "consume")}
    assert WAYS["制作"] == ("obtain", "consume")
    assert WAYS["睡觉"] == ("sleep", "sleep") and WAYS["等待"] == ("interact", "wait")
    assert set(CONTENT_WAYS.values()) == {"接着看", "从头再看", "接着听", "从头再听"}
    assert {WAYS[way] for way in CONTENT_WAYS.values()} == {("interact", "watch")}
    for retired in ("步行前往", "驾车前往", "到店购买", "配送到当前位置", "travel_to", "wait"):
        assert retired not in WAYS


def test_binding_action_projects_verb_and_way_publicly_and_keeps_execution_private():
    """私有绑定带内部动作与执行参数；公开只给「动词：方式」与说明；方式与内部动作不符是完整性错误。"""
    drive = binding_action("驾车", "travel_to", travel_mode="driving")
    assert drive == {"verb": "travel", "label": "驾车", "canonical_action": "travel_to",
                     "travel_mode": "driving", "note": None}
    assert public_action(drive) == {"action": f"travel{VERB_WAY_SEPARATOR}驾车", "note": None}
    assert public_action({**drive, "note": "需要先结束或暂停手上的事"}) == {
        "action": "travel：驾车", "note": "需要先结束或暂停手上的事"}
    delivery = binding_action("远程下单", "purchase", fulfillment="delivery")
    assert delivery["fulfillment"] == "delivery" and delivery["travel_mode"] is None
    assert public_action(delivery) == {"action": "obtain：远程下单", "note": None}
    cook = binding_action("制作", "consume", use_mode="use")
    assert cook["use_mode"] == "use" and public_action(cook)["action"] == "obtain：制作"
    assert public_action(binding_action("接着看", "watch"))["action"] == "interact：接着看"
    with pytest.raises(IntegrityError):
        binding_action("步行", "wait")
    assert activity_phrase(drive) == "驾车前往"
    assert activity_phrase(binding_action("步行", "travel_to", travel_mode="walking")) == "步行前往"
    assert activity_phrase(binding_action("从头再看", "watch")) == "从头再看"


def test_merged_selection_combines_one_order_or_one_meal_only():
    """两项同为方式相同的取得合成一单，同为方式相同的使用合成一餐；到店与远程下单不合成一单，「制作」一次只做一件、
    不合并；其余两项各自开始。"""
    assert merged_selection(["watch"], ["接着看"])
    assert merged_selection(["purchase", "purchase"], ["到店", "到店"])
    assert merged_selection(["purchase", "purchase"], ["远程下单", "远程下单"])
    assert not merged_selection(["purchase", "purchase"], ["到店", "远程下单"])
    assert merged_selection(["consume", "consume"], ["食用", "食用"])
    assert not merged_selection(["consume", "consume"], ["制作", "制作"])
    assert not merged_selection(["consume", "consume"], ["食用", "使用"])
    assert not merged_selection(["travel_to", "watch"], ["驾车", "接着听"])
    assert not merged_selection(["work", "consume"], ["工作", "食用"])


def test_scenario_menus_admit_only_current_start_actions():
    """场景菜单使用当前内部闭集，内容提交和消息发送是独立工具。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    for menu in scenario.interaction_menus_by_location_kind.values():
        assert set(menu.allowed_actions) <= START_ACTIVITY_ACTIONS
        assert {"wait", "sleep"} <= set(menu.allowed_actions)
    assert {"submit_work", "send_message"} <= set(REQUEST_MODELS)


def test_selection_resolves_only_offered_verb_and_way_and_menu_judges_internal_actions():
    """（对象，动词，方式）只按本轮绑定映射：内部动作名、表外短语、动词不符都不解析；旧短语「步行前往」只作方式同义表
    （docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析 ）在目标列出的方式里恰好命中「步行」时受理，不是方式闭集的成员；菜单按映射后的
    内部动作判定。"""
    bindings = _bindings()
    walked = select_start(bindings, start_arguments(["地点：办公室"], ["travel"], ["步行"]))
    assert [row["selected_action"]["canonical_action"] for row in walked] == ["travel_to"]
    assert walked[0]["selected_action"]["travel_mode"] == "walking" and walked[0]["object_id"] == "office"
    [synonym] = select_start(bindings, start_arguments(["办公室"], ["travel"], ["步行前往"]))
    assert (synonym["object"], synonym["selected_action"]["label"]) == ("地点：办公室", "步行")
    assert "步行前往" not in WAYS
    pair = select_start(bindings, start_arguments(["地点：办公室", "自己"], ["travel", "interact"], ["驾车", "等待"]))
    assert [row["selected_action"]["label"] for row in pair] == ["驾车", "等待"]
    for targets, verbs, ways in (
            (["地点：办公室"], ["travel"], ["走着去办公室"]), (["地点：办公室"], ["travel"], ["travel_to"]),
            (["地点：公园"], ["travel"], ["步行"]),

            (["自己", "自己", "自己"], ["sleep"] * 3, ["睡觉"] * 3), ([], [], [])):
        assert select_start(bindings, start_arguments(targets, verbs, ways)) is None
    assert select_start(None, start_arguments(["自己"], ["sleep"], ["睡觉"])) is None
    assert select_start(bindings, {"items": "自己", "answering": None, "prefer": None}) is None
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    assert not menu_allows_action(menu, "start_activity", ["travel_to"])
    assert not menu_allows_action(menu, "start_activity", ["wait", "travel_to"])
    assert not menu_allows_action(menu, "start_activity", [])
    assert menu_allows_action(menu, "start_activity", ["wait"])
    assert menu_allows_action(menu, "submit_work", [])
    with pytest.raises(ValueError):
        InteractionMenu(allowed_actions=("sleep", "submit", "wait"))
    with pytest.raises(ValueError):
        InteractionMenu(allowed_actions=("travel_to", "wait"))
