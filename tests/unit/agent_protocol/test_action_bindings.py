"""仅七个实际动作可登记，指名商品绑定只允许实际购买。"""
import pytest
from worldmock.life.action_contract import ACTIVITY_ACTIONS, activity_slot, binding_action
from worldmock.life.name_resolution import named_supply_row

@pytest.mark.parametrize("action", ["walk", "drive", "take_taxi", "take_bus", "purchase", "sleep", "interact"])
def test_binding_uses_one_of_exactly_seven_current_actions(action):
    assert binding_action(action) == {"action": action, "note": None}
    assert activity_slot(action) in {"first", "second"}
    assert set(ACTIVITY_ACTIONS) == {"walk", "drive", "take_taxi", "take_bus", "purchase", "sleep", "interact"}

@pytest.mark.parametrize("action", ["wait", "do", "travel_to", "call", "set_alarm", ""])
def test_retired_actions_are_not_admitted(action):
    with pytest.raises(ValueError, match="Unknown activity action"):
        binding_action(action)

def test_named_product_requests_preserve_full_name_and_only_purchase():
    row = named_supply_row("牛肉面")
    assert row["object"] == "牛肉面"
    assert row["actions"] == [{"action": "purchase", "note": None}]
