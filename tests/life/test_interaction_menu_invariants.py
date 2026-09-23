"""交互菜单只约束可读活动动作，不接收过时的内容提交动作。"""

from __future__ import annotations

from pathlib import Path

import pytest

from worldmock.catalog.affordances import START_ACTIVITY_ACTIONS
from worldmock.life.interaction_menu import (
    InteractionMenu, action_label, canonical_action, menu_allows_action, purchase_action_label,
)
from worldmock.life.models import REQUEST_MODELS, StartActivityRequest
from worldmock.life.scenario import load_scenario


def test_every_visible_action_round_trips_to_a_canonical_menu_action():
    """人物可读短语覆盖当前全部启动动作，路线方式分别可选。"""
    assert "submit" not in START_ACTIVITY_ACTIONS
    assert "message" not in START_ACTIVITY_ACTIONS
    for action in START_ACTIVITY_ACTIONS - {"travel_to", "purchase"}:
        assert canonical_action(action_label(action)) == action
    assert canonical_action("步行前往") == canonical_action("驾车前往") == "travel_to"
    assert canonical_action(purchase_action_label("pickup")) == "purchase"
    assert canonical_action(purchase_action_label("delivery")) == "purchase"
    assert canonical_action("使用") == "consume"


def test_scenario_menus_admit_only_current_start_actions():
    """场景菜单使用当前闭集，内容提交和消息发送是独立工具。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    for menu in scenario.interaction_menus_by_location_kind.values():
        assert set(menu.allowed_actions) <= START_ACTIVITY_ACTIONS
        assert {"wait", "sleep"} <= set(menu.allowed_actions)
    assert {"submit_work", "send_message"} <= set(REQUEST_MODELS)


def test_menu_checks_semantic_route_choice_without_accepting_internal_action_name():
    """人物请求只能使用可读动作短语；菜单仍按内部闭集准入。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    denied = StartActivityRequest(targets=["地点：办公室"], actions=["步行前往"])
    allowed = StartActivityRequest(targets=["自己"], actions=["等待"])
    assert not menu_allows_action(menu, "start_activity", denied)
    assert menu_allows_action(menu, "start_activity", allowed)
    assert menu_allows_action(menu, "submit_work", object())
    with pytest.raises(ValueError):
        InteractionMenu(allowed_actions=("sleep", "submit", "wait"))
