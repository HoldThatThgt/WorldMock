"""@brief 五个生活动作、两个开始工具及其固定槽位的唯一声明。"""

import logging

#: 全部动作：工具层 Schema 与私有绑定的动作行都读它；新动作加在末尾。
ACTIVITY_ACTIONS = ("travel", "purchase", "sleep", "interact", "query")
#: start_main_activity 的 action 枚举，开始占槽位 1 的活动。
MAIN_ACTIVITY_ACTIONS = ("travel", "interact", "sleep")
#: start_side_activity 的 action 枚举，开始占槽位 2 的活动。
SIDE_ACTIVITY_ACTIONS = ("purchase", "interact", "query")
#: 自带 app 的活动：私有数据里有 app_id 的活动（人物点名使用的内容 app 与查询所在的 app）。
APP_BOUND_ACTIONS = ("interact", "query")
#: 两个开始工具，按此顺序进入 REQUEST_MODELS。
START_TOOLS = ("start_main_activity", "start_side_activity")
#: 动作在人物可见内容中的固定中文标签；出行的说法来自出行方式名称。
ACTION_LABELS = {"purchase": "购买", "sleep": "睡觉", "interact": "进行", "query": "查询"}


def activity_slot(action: str, app_id: str | None) -> str:
    """@brief 活动占用的槽位：出行、睡眠和在地点做事占槽位 1，购买、查询和用内容 app 占槽位 2。

    :param action: 活动 data.action 记下的动作，五个生活动作之一。
    :param app_id: interact 活动所用的内容 app；在地点做事时为 None。
    """
    main = action in ("travel", "sleep")
    if main or action in ("purchase", "query"):
        return "first" if main else "second"
    if action == "interact":
        return "first" if app_id is None else "second"
    logging.error("Unknown activity action: %s", action)
    raise ValueError("Unknown activity action")


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
