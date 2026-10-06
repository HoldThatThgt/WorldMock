"""@brief 系统拥有的地点、食品、内容和摘要来源闭集。"""

from enum import StrEnum
from types import MappingProxyType


class LocationKind(StrEnum):
    """@brief 登记地点所用的八种场所类型。"""

    HOME = "home"
    OFFICE = "office"
    PARK = "park"
    FITNESS = "fitness"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    CONVENIENCE_STORE = "convenience_store"
    SUPERMARKET = "supermarket"


class FoodCategory(StrEnum):
    """@brief 食品的三种固定能量与食用时长类别。"""

    SNACK = "snack"
    DRINK = "drink"
    MEAL = "meal"


class ContentKind(StrEnum):
    """@brief 应用可绑定的五种内容类型。"""

    ARTICLE = "article"
    IMAGE_TEXT = "image_text"
    SHORT_VIDEO = "short_video"
    LONG_VIDEO = "long_video"
    LIVE = "live"


class SummarySourceKind(StrEnum):
    """@brief 内容摘要的真实来源或合成声明。"""

    REAL = "real"
    SYNTHETIC = "synthetic"


FOOD_STORE_KINDS = frozenset({
    LocationKind.RESTAURANT, LocationKind.CAFE, LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET,
})
PLACE_KIND_ENVIRONMENTS = MappingProxyType({
    LocationKind.HOME: "home", LocationKind.OFFICE: "office", LocationKind.PARK: "outdoor",
    LocationKind.FITNESS: "fitness", LocationKind.RESTAURANT: "venue", LocationKind.CAFE: "venue",
    LocationKind.CONVENIENCE_STORE: "venue", LocationKind.SUPERMARKET: "venue",
})
FOOD_CATEGORY_LABELS = MappingProxyType({
    FoodCategory.SNACK: "零食", FoodCategory.DRINK: "饮品", FoodCategory.MEAL: "正餐",
})
CONTENT_KIND_LABELS = MappingProxyType({
    ContentKind.ARTICLE: "文章", ContentKind.IMAGE_TEXT: "图文", ContentKind.SHORT_VIDEO: "短视频",
    ContentKind.LONG_VIDEO: "长视频", ContentKind.LIVE: "实况",
})

START_ACTIVITY_ACTIONS = frozenset({
    "travel_to", "work", "purchase", "consume", "use_app", "chat", "call", "read", "do", "wait", "sleep",
})
