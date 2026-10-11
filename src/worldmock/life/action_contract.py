"""@brief 四个生活动作、两个开始工具及其固定槽位的唯一声明。"""

import logging

#: 全部动作：工具层 Schema、query_world 的 action 筛选与私有绑定的动作行都读它。
ACTIVITY_ACTIONS = ("travel", "purchase", "sleep", "interact")
#: start_main_activity 的 action 枚举，开始占槽位 1 的活动。
MAIN_ACTIVITY_ACTIONS = ("travel", "interact", "sleep")
#: start_side_activity 的 action 枚举，开始占槽位 2 的活动。
SIDE_ACTIVITY_ACTIONS = ("purchase", "interact")
#: 两个开始工具，按此顺序进入 REQUEST_MODELS。
START_TOOLS = ("start_main_activity", "start_side_activity")
#: 四个固定出行动作：出行活动的 data.action、回调动作触发与功能 app 绑定仍按它们区分出行方式。
TRAVEL_ACTIONS = ("walk", "drive", "take_taxi", "take_bus")
#: 出行动作与实际行程方式使用明确的一对一映射。
TRAVEL_ACTION_MODES = {"walk": "walking", "drive": "driving", "take_taxi": "taxi", "take_bus": "bus"}
#: 出行动作的固定中文名：travel 的 detail 按它解析到四种固定方式，出行活动名也用它。
TRAVEL_ACTION_NAMES = {"walk": "步行", "drive": "驾车", "take_taxi": "打车", "take_bus": "公共交通"}
#: 动作在人物可见内容中的固定中文标签；出行的说法来自出行方式名称。
ACTION_LABELS = {"purchase": "购买", "sleep": "睡觉", "interact": "进行"}


def activity_slot(action: str, app_id: str | None) -> str:
    """@brief 活动占用的槽位：出行、睡眠和在地点做事占槽位 1，购买和用内容 app 占槽位 2。

    :param action: 活动 data.action 记下的动作：出行为 travel 或四个出行动作之一，其余为动作本身。
    :param app_id: interact 活动所用的内容 app；在地点做事时为 None。
    """
    main = action == "travel" or action in TRAVEL_ACTIONS or action == "sleep"
    if main or action == "purchase":
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
