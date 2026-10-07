"""@brief 由规则版本绑定的固定仿真常数。"""

from decimal import Decimal
from types import MappingProxyType

from worldmock.exchange.types import ActionCategory, GoodsCategory


RULES_VERSION = 2
FUEL_DRIVING_DAYS = 7
GOODS_ENERGY_KCAL = MappingProxyType({
    GoodsCategory.MEAL: 600, GoodsCategory.SNACK: 200, GoodsCategory.DRINK: 100,
})
#: 非食品收货每条订单行固定效果：娱乐缓解量、情绪脉冲或可驾驶天数。
GOODS_RECEIPT_EFFECTS = MappingProxyType({
    GoodsCategory.LEISURE: 0.10, GoodsCategory.DAILY: 0.05, GoodsCategory.FUEL: FUEL_DRIVING_DAYS,
})
#: 类别的固定代谢当量；布尔键表示是否绑定 app。
CATEGORY_MET = MappingProxyType({
    ActionCategory.ENTERTAINMENT: MappingProxyType({False: 1.0, True: 1.0}),
    ActionCategory.WORK_STUDY: MappingProxyType({False: 1.5, True: 1.5}),
    ActionCategory.SOCIAL: MappingProxyType({False: 1.5, True: 1.3}),
    ActionCategory.EXERCISE: MappingProxyType({False: 5.0, True: 5.0}),
    ActionCategory.DAILY_CARE: MappingProxyType({False: 2.0, True: 2.0}),
})
WALKING_METERS_PER_SECOND = 1.2
DRIVING_METERS_PER_SECOND = 7.0
DRIVING_FARE_MINIMUM_FEN = 500
DRIVING_FARE_FEN_PER_METER = Decimal("0.2")
DELIVERY_METERS_PER_SECOND = 5.0
DELIVERY_PREP_SECONDS = 600
DELIVERY_FEE_FEN = 500
MAP_MIN_NODES = 2
MAP_EDGE_LENGTH_M = (200, 2000)
MAP_GENERATOR_RULE_VERSION = 1
PUSH_HOURS_LOCAL = range(8, 22)
PUSH_SUMMARY_MAX_CHARS = 200
CONTENT_MIN_SECONDS = 60
ENTERTAINMENT_RELIEF_PER_HOUR = 0.3


#: 社交需要按清醒、有效交谈、有效 app 社交与已读消息结算。
SOCIAL_NEED_AWAKE_RATE = 0.04
SOCIAL_NEED_CONVERSATION_RATE = 0.5
SOCIAL_NEED_REMOTE_RATE = 0.4
SOCIAL_NEED_MESSAGE_RELIEF = 0.05
#: 对方不在的社交秒数采用较小缓解量。
CHAT_RELIEF_DECLINED_PER_HOUR = 0.2
#: 固定事件对情绪施加的单次脉冲。
MOOD_PULSES = MappingProxyType({
    "message_received": 0.1, "discomfort_hourly": -0.1, "goal_progressed": 0.05, "service_refused": -0.05,
})
MOOD_HALFLIFE_MINUTES = 45.0
MOOD_HALFLIFE_NEUROTICISM_FACTOR = 0.3
MOOD_PULSE_NEUROTICISM_FACTOR = 0.5
MOOD_DISCOMFORT_TRIGGER_HOURS = 2.0
#: 运动类别按实际小时结算的情绪增量。
LIFESTYLE_HEALTH_MOOD_PER_HOUR = 0.1


def MAP_EXTRA_EDGES(node_count: int) -> int:
    """@brief 返回生成图在树外加入的边数。"""
    return (node_count - 1) // 2
