"""组合与单项启动都受当前资源和每个子项菜单约束。"""

import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_menu import menu_action_error
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import StartActivityRequest, StartParallelActivitiesRequest


def _watch():
    return StartActivityRequest(activity_type="watch", target_id="visible-podcast",
                                        planned_duration_seconds=120, playback_speed=1)


def _wait():
    return StartActivityRequest(activity_type="wait", planned_duration_seconds=120)


@pytest.mark.parametrize("resource", ["attention", "device"])
def test_single_playback_rejects_either_occupied_media_resource(resource):
    """即便上层保留启动入口，点名播放仍不能绕过已占用注意力或设备。"""
    assert start_resource_error("start_targeted_activity", _watch(), frozenset({resource})) == (
        "activity resources conflict with the visible active activities")
    assert start_resource_error("start_targeted_activity", _watch(), frozenset({"position"})) == ""


@pytest.mark.parametrize("tools,kinds,expected", [
    (["start_basic_activity", "start_targeted_activity"], ["wait", "watch"], "start_parallel_activities"),
    (["start_parallel_activities", "start_basic_activity"], ["wait", "watch"], "start_targeted_activity"),
    (["start_parallel_activities", "start_basic_activity", "start_targeted_activity"], ["wait"],
     "start_targeted_activity"),
])
def test_batch_cannot_grant_a_tool_or_kind_missing_from_child_menu(tools, kinds, expected):
    """组合工具自身的许可不能替代每一子工具及其活动类型的许可。"""
    menu = InteractionMenu(allowed_tools=tuple(sorted(set(tools) | PROTECTED_INTERACTION_TOOLS)),
                           allowed_activity_kinds=tuple(sorted(set(kinds) | PROTECTED_ACTIVITY_KINDS)))
    batch = StartParallelActivitiesRequest(activities=(_wait(), _watch()))
    assert menu_action_error(menu, "start_parallel_activities", batch, {}, ("wait",)) == (
        f"interaction menu denies {expected}")
