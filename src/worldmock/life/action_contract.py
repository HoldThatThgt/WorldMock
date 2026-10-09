"""@brief 七个生活动作及其固定活动名额的唯一声明。"""

from enum import StrEnum
import logging


class ActivityAction(StrEnum):
    """@brief 人物可以请求的完整动作闭集。"""

    WALK = "walk"
    DRIVE = "drive"
    TAKE_TAXI = "take_taxi"
    TAKE_BUS = "take_bus"
    PURCHASE = "purchase"
    SLEEP = "sleep"
    INTERACT = "interact"


#: 动作顺序固定，工具、验证和足迹均从此处读取。
ACTIVITY_ACTIONS = tuple(action.value for action in ActivityAction)
#: 第一名额只供出行和睡眠，第二名额只供购买和交互。
FIRST_SLOT_ACTIONS = frozenset({"walk", "drive", "take_taxi", "take_bus", "sleep"})
#: 四个出行动作的闭集。
TRAVEL_ACTIONS = frozenset({"walk", "drive", "take_taxi", "take_bus"})
#: 动作与实际行程方式使用明确的一对一映射。
TRAVEL_ACTION_MODES = {"walk": "walking", "drive": "driving", "take_taxi": "taxi", "take_bus": "bus"}
#: 动作在人物可见内容中的固定中文标签。
ACTION_LABELS = {"walk": "步行", "drive": "驾车", "take_taxi": "打车", "take_bus": "公共交通",
                 "purchase": "购买", "sleep": "睡觉", "interact": "进行"}


def activity_slot(action: str) -> str:
    """@brief 已知动作的名额由动作定义固定，未知动作直接拒绝。"""
    if action not in ACTIVITY_ACTIONS:
        raise ValueError("Unknown activity action")
    return "first" if action in FIRST_SLOT_ACTIONS else "second"


def binding_action(action: str) -> dict:
    """@brief 已登记动作绑定只保存具体动作与可用性备注。"""
    if action not in ACTIVITY_ACTIONS:
        logging.error("Unknown activity action: %s", action)
        raise ValueError("Unknown activity action")
    return {"action": action, "note": None}
