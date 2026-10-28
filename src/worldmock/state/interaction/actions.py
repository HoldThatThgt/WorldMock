"""@brief 按职责保存现有定义与纯计算。"""

import logging

from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
#: 两个开始工具，按此顺序进入 REQUEST_MODELS。
START_TOOLS = ("start_main_activity", "start_side_activity")



def start_tool(kind: str, action: str) -> str:
    """@brief 动作行属于哪个开始工具：地点的出行、做事和睡眠属于主工具，商品购买和内容 app 属于副工具。"""
    if action not in ACTIVITY_ACTIONS:
        logging.error("Unknown activity action: %s", action)
        raise ValueError("Unknown activity action")
    return START_TOOLS[0] if kind == "location" else START_TOOLS[1]



def binding_action(action: str, detail: str | None = None) -> dict:
    """@brief 私有绑定的一条动作行：动作、调用里 detail 要写的名称与可用性备注。"""
    if action not in ACTIVITY_ACTIONS:
        logging.error("Unknown activity action: %s", action)
        raise ValueError("Unknown activity action")
    return {"action": action, "detail": detail, "note": None}
