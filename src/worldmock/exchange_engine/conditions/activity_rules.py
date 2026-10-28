"""@brief 按职责保存现有定义与纯计算。"""

import logging



#: 全部动作：工具层 Schema 与私有绑定的动作行都读它；新动作加在末尾。
ACTIVITY_ACTIONS = ("travel", "purchase", "sleep", "interact", "query")

#: start_main_activity 的 action 枚举，开始占槽位 1 的活动。
MAIN_ACTIVITY_ACTIONS = ("travel", "interact", "sleep")

#: start_side_activity 的 action 枚举，开始占槽位 2 的活动。
SIDE_ACTIVITY_ACTIONS = ("purchase", "interact", "query")

#: 自带 app 的活动：私有数据里有 app_id 的活动（人物点名使用的内容 app 与查询所在的 app）。
APP_BOUND_ACTIONS = ("interact", "query")



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
