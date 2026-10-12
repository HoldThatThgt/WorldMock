"""@brief 单人目录、容器绑定与内存地图的严格不可变合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Mapping

from pydantic import Field, StrictBool, StrictInt, field_validator

from worldmock.base_models import DeepFrozenModel, require_finite_number
from worldmock.catalog.graph import Graph
from worldmock.errors import reject_value
from worldmock.exchange.types import GoodsCategory

if TYPE_CHECKING:
    from worldmock.catalog.callback_models import CallbackRule, CallbackState

PositiveInteger = Annotated[StrictInt, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]
NonEmptyString = Annotated[str, Field(strict=True, pattern=r"\S")]
#: 功能 app 的动作绑定只有购买；出行绑定写在 travel_method_ids。
FunctionAction = Literal["purchase"]
Exertion = Literal["idle", "light", "moderate", "heavy"]
ReliefLevel = Literal["none", "low", "medium", "high"]
OpeningIntervals = tuple[tuple[NonEmptyString, NonEmptyString], ...]
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def channel_key(name: str) -> str:
    """@brief 名称去掉全部空白后不分大小写比较，不做其他归一化。"""
    return "".join(name.split()).casefold()


class CatalogFile(DeepFrozenModel):
    """@brief 清单登记的一份目录文件字节身份。"""

    path: NonEmptyString
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    size_bytes: NonNegativeInteger


class CatalogManifest(DeepFrozenModel):
    """@brief 目录版本、规则版本和完整文件集合的发布身份。"""

    format_version: Literal[5]
    environment_id: NonEmptyString
    environment_version: NonEmptyString
    rules_version: StrictInt
    files: tuple[CatalogFile, ...]
    catalog_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)


class App(DeepFrozenModel):
    """@brief 内容浏览入口，或功能 app 的购买绑定与出行方式绑定。"""

    app_id: NonEmptyString
    name: NonEmptyString
    role: Literal["content", "function"]
    actions: tuple[FunctionAction, ...] = ()
    travel_method_ids: tuple[NonEmptyString, ...] = ()
    browse_duration_seconds: Annotated[StrictInt, Field(ge=60)] | None = None


class TravelMethod(DeepFrozenModel):
    """@brief 数据集定义的一种出行方式：身份、名称、基础速度、起步价、每公里价格与体力档位。"""

    travel_method_id: NonEmptyString
    name: NonEmptyString
    speed_meters_per_second: int | float
    start_price_fen: NonNegativeInteger
    price_per_km_fen: NonNegativeInteger
    exertion: Exertion

    @field_validator("speed_meters_per_second", mode="before")
    @classmethod
    def require_positive_speed(cls, value):
        """@brief 基础速度是大于 0 的有限 JSON 数值，整数与小数原样保留，布尔和字符串不接受。"""
        if require_finite_number(value, "speed_meters_per_second") <= 0:
            reject_value("speed_meters_per_second must be greater than 0.")
        return value


def require_declared_schedule(value):
    """@brief 显式营业时段声明必须是对象；地点行与内容前提共用，缺省才表示不限时段。"""
    if value is None:
        reject_value("A declared opening schedule must be an object.")
    return value


class OpeningHours(DeepFrozenModel):
    """@brief 每周营业时段与按日期替换的特殊营业时段；地点行与内容前提共用它的求值。"""

    weekly: dict[str, OpeningIntervals]
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
    """@brief 显式地点、住宿、驿站与可睡资格以及内容 app 绑定。"""

    location_id: NonEmptyString
    name: NonEmptyString
    branch_label: NonEmptyString | None = None
    kind: NonEmptyString
    app_ids: tuple[NonEmptyString, ...]
    is_home: StrictBool = False
    accepts_parcels: StrictBool = False
    sleepable: StrictBool = False
    opens: OpeningHours | None = None

    #: 显式营业声明必须是对象，缺省才表示全天营业。
    _declared_schedule = field_validator("opens", mode="before")(require_declared_schedule)

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

    goods_id: NonEmptyString
    name: NonEmptyString
    category: GoodsCategory
    price_fen: PositiveInteger
    store_kinds: tuple[NonEmptyString, ...] = ()
    app_ids: tuple[NonEmptyString, ...] = ()


class SummarySource(DeepFrozenModel):
    """@brief 摘要的真实来源地址或合成说明。"""

    kind: Literal["real", "synthetic"]
    url: Annotated[str, Field(strict=True)] | None
    notice: Annotated[str, Field(strict=True)] | None


class ContentSeries(DeepFrozenModel):
    """@brief 连续内容所属系列与唯一集号。"""

    title: NonEmptyString
    episode: PositiveInteger


def require_declared_prerequisite(value):
    """@brief 显式内容前提必须是对象；没有前提只有省略这一种写法。"""
    if value is None:
        reject_value("A declared content prerequisite must be an object.")
    return value


class ContentPrerequisite(DeepFrozenModel):
    """@brief app 内容能开始的地点类型与时段条件；两项都写时都要满足。"""

    location_kinds: tuple[NonEmptyString, ...] = ()
    opens: OpeningHours | None = None

    #: 显式时段声明必须是对象，缺省才表示不限时间。
    _declared_schedule = field_validator("opens", mode="before")(require_declared_schedule)


class Content(DeepFrozenModel):
    """@brief 内容容器、实际时长、体力消耗与独立心理缓解档位，以及 app 内容的可选前提。"""

    content_id: NonEmptyString
    title: NonEmptyString
    exertion: Exertion
    pushable: StrictBool
    relaxation_relief: ReliefLevel
    social_relief: ReliefLevel
    summary: str = Field(strict=True)
    summary_source: SummarySource
    duration_seconds: Annotated[StrictInt, Field(ge=60)]
    app_ids: tuple[NonEmptyString, ...] = ()
    location_kinds: tuple[NonEmptyString, ...] = ()
    series: ContentSeries | None = None
    prerequisite: ContentPrerequisite | None = None

    #: 显式内容前提必须是对象，缺省才表示没有前提。
    _declared_prerequisite = field_validator("prerequisite", mode="before")(require_declared_prerequisite)


class MapEdge(DeepFrozenModel):
    """@brief 两个规范端点之间的无向正长度连接。"""

    a: NonEmptyString
    b: NonEmptyString
    length_m: PositiveInteger


class MapGraph(DeepFrozenModel):
    """@brief 加载期生成的内存图，不含发布结构号或独立种子声明。"""

    nodes: tuple[NonEmptyString, ...]
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

    def shortest_m(self, origin: str, destination: str) -> int:
        """@brief 全部登记地点参与寻路的最短距离。"""
        return self.graph.distance_meters(origin, destination)

    def nearest_parcel_station(self, home_location_id: str) -> str:
        """@brief 按住处最短距离与地点身份确定最近驿站。"""
        from worldmock.catalog.errors import RegistrationError

        stations = [place.location_id for place in self.locations.values() if place.accepts_parcels]
        if not stations:
            raise RegistrationError("PARCEL_STATION_REQUIRED")
        return min(stations, key=lambda key: (self.shortest_m(home_location_id, key), key))
