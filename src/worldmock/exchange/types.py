"""@brief 系统拥有的地点、商品、动作、使用方式、用途和内容闭集。"""

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


class GoodsCategory(StrEnum):
    """@brief 商品的五种固定收货效果类别。"""

    MEAL = "meal"
    SNACK = "snack"
    DRINK = "drink"
    LEISURE = "leisure"
    DAILY = "daily"


class ActionCategory(StrEnum):
    """@brief 数据集动作的五种固定行为类别。"""

    ENTERTAINMENT = "entertainment"
    WORK_STUDY = "work_study"
    SOCIAL = "social"
    EXERCISE = "exercise"
    DAILY_CARE = "daily_care"


class AppUseManner(StrEnum):
    """@brief 动作绑定 app 时的三种使用方式。"""

    PHONE_CONTINUOUS = "phone_continuous"
    OTHER_DEVICE = "other_device"
    OFFLINE_ASSISTED = "offline_assisted"


class AppPurpose(StrEnum):
    """@brief 系统动作可点名的三种 app 用途。"""

    NAVIGATION = "navigation"
    SHOPPING = "shopping"
    PAYMENT = "payment"


class SummarySourceKind(StrEnum):
    """@brief 内容摘要的真实来源或合成声明。"""

    REAL = "real"
    SYNTHETIC = "synthetic"


MANNER_DEVICE = MappingProxyType({
    AppUseManner.PHONE_CONTINUOUS: "phone",
    AppUseManner.OTHER_DEVICE: "other",
    AppUseManner.OFFLINE_ASSISTED: "phone",
})
STORE_KINDS = frozenset({
    LocationKind.RESTAURANT, LocationKind.CAFE, LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET,
})
GOODS_CATEGORY_STORE_KINDS = MappingProxyType({
    GoodsCategory.MEAL: frozenset({
        LocationKind.RESTAURANT, LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET,
    }),
    GoodsCategory.SNACK: STORE_KINDS,
    GoodsCategory.DRINK: STORE_KINDS,
    GoodsCategory.LEISURE: frozenset({LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET}),
    GoodsCategory.DAILY: frozenset({LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET}),
})
PLACE_KIND_ENVIRONMENTS = MappingProxyType({
    LocationKind.HOME: "home", LocationKind.OFFICE: "office", LocationKind.PARK: "outdoor",
    LocationKind.FITNESS: "fitness", LocationKind.RESTAURANT: "venue", LocationKind.CAFE: "venue",
    LocationKind.CONVENIENCE_STORE: "venue", LocationKind.SUPERMARKET: "venue",
})
GOODS_CATEGORY_LABELS = MappingProxyType({
    GoodsCategory.MEAL: "正餐", GoodsCategory.SNACK: "零食", GoodsCategory.DRINK: "饮品",
    GoodsCategory.LEISURE: "休闲用品", GoodsCategory.DAILY: "日常用品",
})
START_ACTIVITY_ACTIONS = frozenset({
    "travel_to", "sleep", "wait", "purchase", "do",
})
