"""@brief 由规则版本绑定的固定仿真常数。"""

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from worldmock.exchange.types import ContentKind, FoodCategory


@dataclass(frozen=True)
class FoodConstants:
    """@brief 每份食品的固定热量和食用时长。"""

    #: 每份食品的千卡能量。
    energy_kcal: int
    #: 每份食品的食用秒数。
    duration_seconds: int


RULES_VERSION = 1
FOOD_CONSTANTS = MappingProxyType({
    FoodCategory.SNACK: FoodConstants(200, 300),
    FoodCategory.DRINK: FoodConstants(100, 180),
    FoodCategory.MEAL: FoodConstants(600, 1200),
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
APP_USE_DEFAULT_SECONDS = 1800
CONTENT_RELIEF_PER_HOUR = MappingProxyType({
    ContentKind.ARTICLE: 0.2, ContentKind.IMAGE_TEXT: 0.2, ContentKind.SHORT_VIDEO: 0.3,
    ContentKind.LONG_VIDEO: 0.4, ContentKind.LIVE: 0.3,
})


def MAP_EXTRA_EDGES(node_count: int) -> int:
    """@brief 返回生成图在树外加入的边数。"""
    return (node_count - 1) // 2
