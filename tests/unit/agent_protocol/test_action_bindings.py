"""仅五个现行动作可登记为私有绑定的动作行，指名商品绑定只允许实际购买。"""
import pytest
from worldmock.life.action_contract import ACTIVITY_ACTIONS, START_TOOLS, binding_action, start_tool
from worldmock.life.name_resolution import named_supply_row, sleep_row


@pytest.mark.parametrize("action", ["travel", "purchase", "sleep", "interact", "query"])
def test_binding_row_carries_action_detail_and_note(action):
    """动作行恒有动作、detail 与不可用备注三个键；detail 是调用里要写的名称。"""
    assert binding_action(action) == {"action": action, "detail": None, "note": None}
    assert binding_action(action, "步行") == {"action": action, "detail": "步行", "note": None}
    assert set(ACTIVITY_ACTIONS) == {"travel", "purchase", "sleep", "interact", "query"}


@pytest.mark.parametrize("action", ["walk", "drive", "take_taxi", "take_bus", "wait", "do", "travel_to", "call", ""])
def test_retired_actions_are_not_admitted_as_binding_rows(action):
    """四个旧出行动作和已退役动作都不能作为可提交的动作行。"""
    with pytest.raises(ValueError, match="Unknown activity action"):
        binding_action(action)
    with pytest.raises(ValueError, match="Unknown activity action"):
        start_tool("location", action)


@pytest.mark.parametrize("kind,action,tool", [
    ("location", "travel", "start_main_activity"), ("location", "interact", "start_main_activity"),
    ("location", "sleep", "start_main_activity"), ("supply", "purchase", "start_side_activity"),
    ("named_supply", "purchase", "start_side_activity"), ("app", "interact", "start_side_activity"),
])
def test_action_row_belongs_to_one_start_tool_by_object_kind(kind, action, tool):
    """地点的出行、做事和睡眠属于主工具，商品的购买和内容 app 的使用属于副工具。"""
    assert start_tool(kind, action) == tool and tool in START_TOOLS


def test_named_product_requests_preserve_full_name_and_only_purchase():
    row = named_supply_row("牛肉面")
    assert row["object"] == "牛肉面" and row["object_id"] is None and row["kind"] == "named_supply"
    assert row["actions"] == [{"action": "purchase", "detail": None, "note": None}]
    assert row["selected_action"] == row["actions"][0]


def test_sleep_row_carries_only_the_parsed_minutes():
    """睡眠不经绑定行：解析结果只带分钟数，不绑定任何地点。"""
    row = sleep_row(470)
    assert row["minutes"] == 470 and row["object_id"] is None and row["kind"] == "sleep"
    assert row["selected_action"] == {"action": "sleep", "detail": None, "note": None}
