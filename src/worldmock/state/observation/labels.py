"""@brief 按职责保存现有定义与纯计算。"""



#: 动作在人物可见内容中的固定中文标签；出行的说法来自出行方式名称。
ACTION_LABELS = {"purchase": "购买", "sleep": "睡觉", "interact": "进行", "query": "查询"}
from worldmock.exchange_engine.definitions.constants import WALKING_TRAVEL_METHOD_ID
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS

ACTIVITY_KIND_LABELS = {"sleep": "睡觉", "travel": "出行", "interact": "互动", "purchase": "购买", "query": "查询"}

ACTIVITY_DOING_LABELS = {"sleep": "睡觉", "travel": "赶路", "interact": "互动", "purchase": "买东西",
                         "query": "查询"}

ENGLISH_KIND_NAMES = frozenset({*ACTIVITY_KIND_LABELS, *ACTIVITY_DOING_LABELS, *ACTIVITY_ACTIONS,
                              WALKING_TRAVEL_METHOD_ID})
