"""多对象选择使用同一可读动作，并受本轮地点菜单剪裁。"""

from __future__ import annotations

import pytest

from custom.agents.life_menu import menu_action_error
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import StartActivityRequest


@pytest.mark.parametrize("action", ["观看", "游玩", "配送到当前位置"])
def test_location_menu_denies_actions_outside_its_closed_set(action: str) -> None:
    """@brief 人物能点名对象，仍不能绕过地点已冻结的动作允许集合。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    request = StartActivityRequest(targets=["可读对象"], actions=[action])
    assert menu_action_error(menu, "start_activity", request) == "interaction menu denies start_activity"


@pytest.mark.parametrize("action", ["等待", "睡觉"])
def test_protected_self_actions_remain_usable(action: str) -> None:
    """@brief 自身的等待和睡觉是地点菜单必须保留的动作。"""
    menu = InteractionMenu(allowed_actions=("sleep", "wait"))
    request = StartActivityRequest(targets=["自己"], actions=[action])
    assert menu_action_error(menu, "start_activity", request) == ""
