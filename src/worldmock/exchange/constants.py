"""@brief 由规则版本绑定的固定仿真常数。"""

from decimal import Decimal
from types import MappingProxyType



RULES_VERSION = 4
WALKING_METERS_PER_SECOND = 1.2
DRIVING_METERS_PER_SECOND = 7.0
DRIVING_FARE_MINIMUM_FEN = 500
DRIVING_FARE_FEN_PER_METER = Decimal("0.2")
DELIVERY_METERS_PER_SECOND = 5.0
DELIVERY_PREP_SECONDS = 600
DELIVERY_FEE_FEN = 500
#: 零食与食品每件提供的千卡；只在商品真正收到时进账。
GOODS_ENERGY_KCAL = MappingProxyType({"snack": 200, "meal": 600})
#: 饮料与商品每次实际收货减少的放松需要。
GOODS_RELAXATION_RELIEF = 0.10
MAP_MIN_NODES = 2
MAP_EDGE_LENGTH_M = (200, 2000)
#: 任何一次出行的用时上限，也是地图上任意两地在最差天气下必须走得到的时限。
TRAVEL_MAX_SECONDS = 86400
#: 固定等概率天气及其对全部出行方式的速度系数。
WEATHER_FACTORS = {"sunny": 1.0, "cloudy": 1.0, "light_rain": 0.85, "heavy_rain": 0.7, "snow": 0.6}
PUSH_HOURS_LOCAL = range(8, 22)
PUSH_SUMMARY_MAX_CHARS = 200
def MAP_EXTRA_EDGES(node_count: int) -> int:
    """@brief 返回生成图在树外加入的边数。"""
    return (node_count - 1) // 2
