"""@brief 系统拥有的地点、商品、动作、使用方式、用途和内容闭集。"""

from enum import StrEnum
from types import MappingProxyType


class LocationKind(StrEnum):
    """@brief 登记地点所用的十种场所类型。"""

    HOME = "home"
    OFFICE = "office"
    PARK = "park"
    FITNESS = "fitness"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    CONVENIENCE_STORE = "convenience_store"
    SUPERMARKET = "supermarket"
    GAS_STATION = "gas_station"
    PARCEL_STATION = "parcel_station"


class GoodsCategory(StrEnum):
    """@brief 商品的六种固定收货效果类别。"""

    MEAL = "meal"
    SNACK = "snack"
    DRINK = "drink"
    LEISURE = "leisure"
    DAILY = "daily"
    FUEL = "fuel"


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


class CommunicationMode(StrEnum):
    """@brief 绑定应用的社交动作明确选择文字或语音路径。"""

    TEXT = "text"
    VOICE = "voice"


class SummarySourceKind(StrEnum):
    """@brief 内容摘要的真实来源或合成声明。"""

    REAL = "real"
    SYNTHETIC = "synthetic"


MANNER_DEVICE = MappingProxyType({
    AppUseManner.PHONE_CONTINUOUS: "phone",
    AppUseManner.OTHER_DEVICE: "other",
    AppUseManner.OFFLINE_ASSISTED: "phone",
})
ACTION_CATEGORY_LABELS = MappingProxyType({
    ActionCategory.ENTERTAINMENT: "娱乐", ActionCategory.WORK_STUDY: "工作与学习",
    ActionCategory.SOCIAL: "社交", ActionCategory.EXERCISE: "运动", ActionCategory.DAILY_CARE: "日常照护",
})
APP_USE_MANNER_LABELS = MappingProxyType({
    AppUseManner.PHONE_CONTINUOUS: "持续使用手机 app",
    AppUseManner.OTHER_DEVICE: "通过其他设备使用 app",
    AppUseManner.OFFLINE_ASSISTED: "借助 app 安排或记录线下行为",
})
STORE_KINDS = frozenset({
    LocationKind.RESTAURANT, LocationKind.CAFE, LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET,
    LocationKind.GAS_STATION,
})
GOODS_CATEGORY_STORE_KINDS = MappingProxyType({
    GoodsCategory.MEAL: frozenset({
        LocationKind.RESTAURANT, LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET,
    }),
    GoodsCategory.SNACK: STORE_KINDS - {LocationKind.GAS_STATION},
    GoodsCategory.DRINK: STORE_KINDS - {LocationKind.GAS_STATION},
    GoodsCategory.LEISURE: frozenset({LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET}),
    GoodsCategory.DAILY: frozenset({LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET}),
    GoodsCategory.FUEL: frozenset({LocationKind.GAS_STATION}),
})
PLACE_KIND_ENVIRONMENTS = MappingProxyType({
    LocationKind.HOME: "home", LocationKind.OFFICE: "office", LocationKind.PARK: "outdoor",
    LocationKind.FITNESS: "fitness", LocationKind.RESTAURANT: "venue", LocationKind.CAFE: "venue",
    LocationKind.CONVENIENCE_STORE: "venue", LocationKind.SUPERMARKET: "venue",
    LocationKind.GAS_STATION: "venue", LocationKind.PARCEL_STATION: "venue",
})
GOODS_CATEGORY_LABELS = MappingProxyType({
    GoodsCategory.MEAL: "正餐", GoodsCategory.SNACK: "零食", GoodsCategory.DRINK: "饮品",
    GoodsCategory.LEISURE: "休闲用品", GoodsCategory.DAILY: "日常用品",
    GoodsCategory.FUEL: "燃油",
})
START_ACTIVITY_ACTIONS = frozenset({
    "travel_to", "sleep", "wait", "purchase", "do", "collect_parcel",
})
