"""@brief 单人目录、容器绑定与内存地图的严格不可变合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Mapping

from pydantic import Field, StrictBool, StrictInt, field_validator

from worldmock.foundation.models import DeepFrozenModel, require_finite_number
from worldmock.resource_engine.map.graph import Graph
from worldmock.foundation.errors import reject_value
from worldmock.exchange_engine.definitions.types import GoodsCategory
from worldmock.state.model.memory import MemoryCategory

if TYPE_CHECKING:
    from worldmock.resource_engine.catalog.callback_models import CallbackRule, CallbackState

_LOGGER = logging.getLogger(__name__)

PositiveInteger = Annotated[StrictInt, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]
NonEmptyString = Annotated[str, Field(strict=True, pattern=r"\S")]
NonEmptyTravelMethods = Annotated[tuple[NonEmptyString, ...], Field(min_length=1)]
#: 功能 app 的动作绑定只有购买；出行绑定写在 travel_method_ids。
FunctionAction = Literal["purchase"]
Exertion = Literal["idle", "light", "moderate", "heavy"]
ReliefLevel = Literal["none", "low", "medium", "high"]
#: 完成效果补能的档位，与同名商品类别同热量。
FoodTier = Literal["snack", "meal"]
#: 完成效果一次缓解的档位，与内容行顶层同名的缓解档位同一套，不收 none。
ReliefTier = Literal["low", "medium", "high"]
OpeningIntervals = tuple[tuple[NonEmptyString, NonEmptyString], ...]
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def channel_key(name: str) -> str:
    """@brief 名称去掉全部空白后不分大小写比较，不做其他归一化。"""
    return "".join(name.split()).casefold()


class CatalogFile(DeepFrozenModel):
    """@brief 清单登记的一份目录文件字节身份。"""

    #: 相对 catalog 根的文件路径。
    path: NonEmptyString
    #: 文件内容的十六进制 SHA-256 摘要。
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    #: 文件真实字节长度。
    size_bytes: NonNegativeInteger


class CatalogManifest(DeepFrozenModel):
    """@brief 目录版本、规则版本和完整文件集合的发布身份。"""

    #: 目录文件格式版本号。
    format_version: Literal[7]
    #: 环境包唯一身份。
    environment_id: NonEmptyString
    #: 环境包发布版本号。
    environment_version: NonEmptyString
    #: 适用的引擎规则版本号。
    rules_version: StrictInt
    #: 按路径升序登记的目录文件表。
    files: tuple[CatalogFile, ...]
    #: 全部在场文件与去自引用清单的摘要。
    catalog_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)


def require_declared_category(value):
    """@brief 显式记忆类别必须是类别名；不覆盖只有省略这一种写法。"""
    if value is None:
        reject_value("A declared memory category must be a category name.")
    return value


class App(DeepFrozenModel):
    """@brief 内容 app，或功能 app 的购买绑定与出行方式绑定。"""

    #: app 唯一身份。
    app_id: NonEmptyString
    #: app 展示名称。
    name: NonEmptyString
    #: 角色区分内容 app 与功能 app。
    role: Literal["content", "function"]
    #: 功能 app 绑定的购买动作。
    actions: tuple[FunctionAction, ...] = ()
    #: 功能 app 绑定的出行方式身份元组。
    travel_method_ids: tuple[NonEmptyString, ...] = ()
    #: 记忆类别覆盖（docs/design/06-agent.md「记忆的类别」规则 0）；省略表示按推导规则。
    memory_category: MemoryCategory | None = None

    #: 显式记忆类别必须是类别名，缺省才表示按推导规则。
    _declared_category = field_validator("memory_category", mode="before")(require_declared_category)


class TravelMethod(DeepFrozenModel):
    """@brief 数据集定义的一种出行方式：身份、名称、基础速度、起步价、每公里价格、体力档位与同城通用性。"""

    #: 出行方式唯一身份。
    travel_method_id: NonEmptyString
    #: 出行方式显示名称。
    name: NonEmptyString
    #: 基础移动速度（米/秒）。
    speed_meters_per_second: int | float
    #: 起步价（分）。
    start_price_fen: NonNegativeInteger
    #: 每公里加价（分）。
    price_per_km_fen: NonNegativeInteger
    #: 出行体力消耗档位。
    exertion: Exertion
    #: 是否可用于同城自由街区任意起终点；为假时仅限显式列出它的声明边。
    city_wide: StrictBool = True

    @field_validator("speed_meters_per_second", mode="before")
    @classmethod
    def require_positive_speed(cls, value):
        """@brief 基础速度是大于 0 的有限 JSON 数值，整数与小数原样保留，布尔和字符串不接受。"""
        if require_finite_number(value, "speed_meters_per_second") <= 0:
            _LOGGER.error("Invalid speed_meters_per_second: %r", value)
            reject_value("speed_meters_per_second must be greater than 0.")
        return value


def require_declared_schedule(value):
    """@brief 显式营业时段声明必须是对象；地点行与内容前提共用，缺省才表示不限时段。"""
    if value is None:
        reject_value("A declared opening schedule must be an object.")
    return value


def require_declared_state(value):
    """@brief 显式凭的状态必须是状态名；不凭状态只有省略这一种写法。"""
    if value is None:
        reject_value("A declared consumed state must be a state name.")
    return value


class OpeningHours(DeepFrozenModel):
    """@brief 每周营业时段与按日期替换的特殊营业时段；地点行、声明边与内容前提共用它的求值。"""

    #: 按星期简写索引的营业时段映射。
    weekly: dict[str, OpeningIntervals]
    #: 按规范 ISO 日期整日覆盖的特殊营业时段映射。
    exceptions: dict[str, OpeningIntervals] = Field(default_factory=dict)

    def opening_intervals(self, local_date: date) -> OpeningIntervals:
        """@brief 特殊日期整日替换（可以为空），否则取该星期的时段，没写的星期为空。"""
        key = local_date.isoformat()
        if key in self.exceptions:
            return self.exceptions[key]
        return self.weekly.get(WEEKDAYS[local_date.weekday()], ())

    def is_open(self, local_moment: datetime) -> bool:
        """@brief 按已经换算为运行时区的本地时刻检查半开时段：秒不参与，24:00 按 1440 分钟计。"""
        minute = local_moment.hour * 60 + local_moment.minute
        for starts, ends in self.opening_intervals(local_moment.date()):
            start = int(starts[:2]) * 60 + int(starts[3:])
            end = int(ends[:2]) * 60 + int(ends[3:])
            if start <= minute < end:
                return True
        return False


class Location(DeepFrozenModel):
    """@brief 显式地点、城市归属、住宿、驿站与可睡资格以及内容 app 绑定。"""

    #: 地点唯一身份。
    location_id: NonEmptyString
    #: 地点所属城市身份。
    city_id: NonEmptyString
    #: 地点主体名称。
    name: NonEmptyString
    #: 同名分店标签；省略表示无分店后缀。
    branch_label: NonEmptyString | None = None
    #: 地点类型。
    kind: NonEmptyString
    #: 实际售货地点自己的评分，其他地点省略。
    rating: int | float | None = None
    #: 地点绑定的内容 app 身份元组。
    app_ids: tuple[NonEmptyString, ...]
    #: 是否为人物自有住处。
    is_home: StrictBool = False
    #: 是否具备代收快递驿站资格。
    accepts_parcels: StrictBool = False
    #: 停留时是否可开始睡眠。
    sleepable: StrictBool = False
    #: 商业住宿入睡时需核验与扣减的回调状态名。
    sleep_consumes_state: NonEmptyString | None = None
    #: 地点营业时段；省略表示全天开放。
    opens: OpeningHours | None = None

    #: 显式营业声明必须是对象，缺省才表示全天营业。
    _declared_schedule = field_validator("opens", mode="before")(require_declared_schedule)
    #: 显式住宿状态必须是状态名，缺省才表示不凭状态。
    _declared_sleep_state = field_validator("sleep_consumes_state", mode="before")(require_declared_state)

    @field_validator("rating", mode="before")
    @classmethod
    def require_store_rating(cls, value):
        """@brief 商铺评分允许整数和小数，范围含两端；只有省略字段才表示没有评分。"""
        if not 1 <= require_finite_number(value, "rating") <= 5:
            _LOGGER.error("Invalid store rating: %r", value)
            reject_value("Store rating must be between 1 and 5.")
        return value

    @property
    def display_name(self) -> str:
        """@brief 分店显示名统一使用全角括号。"""
        return self.name if self.branch_label is None else f"{self.name}（{self.branch_label}）"

    def opening_intervals(self, local_date: date) -> OpeningIntervals:
        """@brief 没写营业时段即全天，其余取营业时段对象当天的时段。"""
        return (("00:00", "24:00"),) if self.opens is None else self.opens.opening_intervals(local_date)

    def is_open(self, local_moment: datetime) -> bool:
        """@brief 没写营业时段即全天营业，其余由营业时段对象按本地时刻判断。"""
        return self.opens is None or self.opens.is_open(local_moment)


class Goods(DeepFrozenModel):
    """@brief 固定价格的四类商品，恰好绑定地点类型或内容 app 一侧。"""

    #: 商品唯一身份。
    goods_id: NonEmptyString
    #: 商品展示名称。
    name: NonEmptyString
    #: 商品四类枚举。
    category: GoodsCategory
    #: 商品固定单价（分）。
    price_fen: PositiveInteger
    #: 线下售卖该商品的地点类型元组。
    store_kinds: tuple[NonEmptyString, ...] = ()
    #: 线上售卖该商品的内容 app 身份元组。
    app_ids: tuple[NonEmptyString, ...] = ()
    #: 是否为即时电子履约（仅线上商品可声明）。
    instant: StrictBool = False


class SummarySource(DeepFrozenModel):
    """@brief 摘要的真实来源地址或合成说明。"""

    #: 来源类别。
    kind: Literal["real", "synthetic"]
    #: 真实来源的完整链接。
    url: Annotated[str, Field(strict=True)] | None
    #: 合成摘要的作者说明。
    notice: Annotated[str, Field(strict=True)] | None


class ContentSeries(DeepFrozenModel):
    """@brief 连续内容所属系列与唯一集号。"""

    #: 所属系列标题。
    title: NonEmptyString
    #: 系列内正整数集号。
    episode: PositiveInteger


def require_declared_prerequisite(value):
    """@brief 显式内容前提必须是对象；没有前提只有省略这一种写法。"""
    if value is None:
        reject_value("A declared content prerequisite must be an object.")
    return value


class ContentPrerequisite(DeepFrozenModel):
    """@brief app 内容能开始的城市、地点类型与时段条件；已声明条件都要满足。"""

    #: 允许开始的城市身份元组。
    city_ids: tuple[NonEmptyString, ...] = ()
    #: 允许开始的地点类型元组。
    location_kinds: tuple[NonEmptyString, ...] = ()
    #: 允许开始的本地营业时段；省略表示不限时间。
    opens: OpeningHours | None = None

    #: 显式时段声明必须是对象，缺省才表示不限时间。
    _declared_schedule = field_validator("opens", mode="before")(require_declared_schedule)


def require_declared_tier(value):
    """@brief 显式完成效果成员必须是档位名；没有这项效果只有省略这一种写法。"""
    if value is None:
        reject_value("A declared completion effect tier must be a tier name.")
    return value


class CompletionEffects(DeepFrozenModel):
    """@brief 地点内容做完时结算一次的效果：补能与两项需要的一次缓解，成员只写档位名。"""

    #: 完成补能档位。
    food: FoodTier | None = None
    #: 一次放松需要缓解档位。
    relaxation_relief: ReliefTier | None = None
    #: 一次社交需要缓解档位。
    social_relief: ReliefTier | None = None

    #: 成员写成 null 拒绝，省略才表示没有这项效果。
    _declared_tiers = field_validator("food", "relaxation_relief", "social_relief", mode="before")(
        require_declared_tier)


def require_declared_effects(value):
    """@brief 显式完成效果必须是对象；做完没有效果只有省略这一种写法。"""
    if value is None:
        reject_value("A declared completion effect must be an object.")
    return value


def require_declared_travel_methods(value):
    """@brief 显式声明的出行方式列表必须是非空数组；省略才表示同城自由街区边。"""
    if value is None:
        _LOGGER.error("Declared travel_method_ids on MapEdge must not be null")
        reject_value("A declared travel method list must be a non-empty array.")
    return value


class Content(DeepFrozenModel):
    """@brief 内容容器、实际时长、体力消耗与独立心理缓解档位，app 内容的可选前提，地点内容凭的状态与完成效果。"""

    #: 内容唯一身份。
    content_id: NonEmptyString
    #: 内容展示标题。
    title: NonEmptyString
    #: 体力消耗档位。
    exertion: Exertion
    #: 是否允许整 hour 推送或自动接续。
    pushable: StrictBool
    #: 每小时放松缓解档位。
    relaxation_relief: ReliefLevel
    #: 每小时社交缓解档位。
    social_relief: ReliefLevel
    #: 内容摘要文本。
    summary: str = Field(strict=True)
    #: 摘要来源说明。
    summary_source: SummarySource
    #: 计划持续秒数（至少 60 秒）。
    duration_seconds: Annotated[StrictInt, Field(ge=60)]
    #: 绑定的内容 app 身份元组。
    app_ids: tuple[NonEmptyString, ...] = ()
    #: 绑定的地点类型元组。
    location_kinds: tuple[NonEmptyString, ...] = ()
    #: 所属系列及集号。
    series: ContentSeries | None = None
    #: app 内容的开始前提。
    prerequisite: ContentPrerequisite | None = None
    #: 地点内容开始时扣除的状态名。
    consumes_state: NonEmptyString | None = None
    #: 地点内容完成时一次性结算的效果。
    completion_effects: CompletionEffects | None = None
    #: 记忆类别覆盖（docs/design/06-agent.md「记忆的类别」规则 0）；省略表示按推导规则。
    memory_category: MemoryCategory | None = None
    #: 是否可重复消费；为假时完成一轮即耗尽。
    repeatable: StrictBool = True

    #: 显式内容前提必须是对象，缺省才表示没有前提。
    _declared_prerequisite = field_validator("prerequisite", mode="before")(require_declared_prerequisite)
    #: 显式凭的状态必须是状态名，缺省才表示不凭状态。
    _declared_state = field_validator("consumes_state", mode="before")(require_declared_state)
    #: 显式完成效果必须是对象，缺省才表示做完没有效果。
    _declared_effects = field_validator("completion_effects", mode="before")(require_declared_effects)
    #: 显式记忆类别必须是类别名，缺省才表示按推导规则。
    _declared_category = field_validator("memory_category", mode="before")(require_declared_category)


class MapEdge(DeepFrozenModel):
    """@brief 两个规范端点之间的无向正长度连接及可选通行方式、状态扣减与发车时段。"""

    #: 字典序较小的端点地点身份。
    a: NonEmptyString
    #: 字典序较大的端点地点身份。
    b: NonEmptyString
    #: 两端之间的整数米数距离。
    length_m: PositiveInteger
    #: 限定允许通行的出行方式身份元组；省略表示同城自由街区边。
    travel_method_ids: NonEmptyTravelMethods | None = None
    #: 出发受理时核验并扣减 1 点的回调状态名。
    consumes_state: NonEmptyString | None = None
    #: 限定声明边允许出发的本地发车检票时段。
    opens: OpeningHours | None = None

    #: 显式限制出行方式必须是非空数组。
    _declared_travel_methods = field_validator("travel_method_ids", mode="before")(require_declared_travel_methods)
    #: 显式凭的状态必须是状态名，缺省才表示不凭状态。
    _declared_state = field_validator("consumes_state", mode="before")(require_declared_state)
    #: 显式营业声明必须是对象，缺省才表示全天开放。
    _declared_schedule = field_validator("opens", mode="before")(require_declared_schedule)

    def is_open(self, local_moment: datetime) -> bool:
        """@brief 没写发车时段即全天开放，其余由营业时段对象按本地时刻判断。"""
        return self.opens is None or self.opens.is_open(local_moment)


class MapGraph(DeepFrozenModel):
    """@brief 加载期生成的内存图，不含发布结构号或独立种子声明。"""

    #: 升序排序的全部地点身份。
    nodes: tuple[NonEmptyString, ...]
    #: 升序排序的全部图边。
    edges: tuple[MapEdge, ...]


@dataclass(frozen=True)
class Catalog:
    """@brief 已登记对象、回调与冻结派生索引。"""

    #: 清单字节身份。
    manifest: CatalogManifest
    #: 目录根。
    source_dir: Path
    #: app 身份索引。
    apps: Mapping[str, App]
    #: 全量地点身份索引。
    locations: Mapping[str, Location]
    #: 商品身份索引。
    goods: Mapping[str, Goods]
    #: 内容身份索引。
    contents: Mapping[str, Content]
    #: 内存地图。
    map_data: MapGraph
    #: 内存地图规范摘要。
    map_sha256: str
    #: 确定性最短路查询。
    graph: Graph
    #: 各地点实际商品。
    goods_by_store: Mapping[str, tuple[str, ...]]
    #: app 直接供应的商品。
    goods_by_app: Mapping[str, tuple[str, ...]]
    #: 各 app 绑定地点。
    stores_by_app: Mapping[str, tuple[str, ...]]
    #: 各地点绑定内容 app。
    apps_by_store: Mapping[str, tuple[str, ...]]
    #: 各 app 的内容。
    contents_by_app: Mapping[str, tuple[str, ...]]
    #: 各地点类型的内容。
    contents_by_location_kind: Mapping[str, tuple[str, ...]]
    #: 按集号排序的系列内容。
    contents_by_series: Mapping[str, tuple[str, ...]]
    #: 数据集定义的出行方式身份索引，不含引擎自带的步行。
    travel_methods: Mapping[str, TravelMethod]
    #: 绑定购买的功能 app 候选，只有 purchase 一个键。
    apps_by_action: Mapping[str, tuple[str, ...]]
    #: 各出行方式（walking 与每个数据集身份）绑定的功能 app，按身份升序。
    apps_by_travel_method: Mapping[str, tuple[str, ...]]
    #: 通用状态声明。
    callback_states: Mapping[str, CallbackState]
    #: 回调规则声明。
    callback_rules: Mapping[str, CallbackRule]

    def food_states(self) -> frozenset[str]:
        """@brief 食品状态：至少被一条 completion_effects.food 不为空的内容凭着的回调状态。"""
        return frozenset(content.consumes_state for content in self.contents.values()
                         if content.consumes_state is not None and content.completion_effects is not None
                         and content.completion_effects.food is not None)

    def kit_states(self) -> frozenset[str]:
        """@brief 食材包状态：reset_on_goods 非空的食品状态；只有凭它们的活动写所用食材包。"""
        return frozenset(name for name in self.food_states() if self.callback_states[name].reset_on_goods)

    def shortest_m(self, origin: str, destination: str) -> int:
        """@brief 全部登记地点参与寻路的最短距离。"""
        return self.graph.distance_meters(origin, destination)

    def nearest_parcel_station(self, home_location_id: str) -> str:
        """@brief 按住处同城最短距离与地点身份确定最近驿站。"""
        from worldmock.resource_engine.registration.errors import RegistrationError

        home_city = self.locations[home_location_id].city_id
        stations = [place.location_id for place in self.locations.values()
                    if place.accepts_parcels and place.city_id == home_city]
        if not stations:
            _LOGGER.error("No parcel station in city %s for home %s", home_city, home_location_id)
            raise RegistrationError("PARCEL_STATION_REQUIRED")
        return min(stations, key=lambda key: (self.shortest_m(home_location_id, key), key))
