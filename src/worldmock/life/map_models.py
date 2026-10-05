"""@brief 冻结地图、地点、门店供给与时态声明的严格数据模型。"""

from __future__ import annotations
from worldmock.life import models

from worldmock.errors import reject_value
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StrictInt, model_validator

from worldmock.base_models import FrozenModel
from worldmock.life.models import (TravelMode)

from worldmock.life.retrieval import LOCATION_KINDS
#: 地图文件结构版本：数据集与三个目录共用；3 起固定生成种子与逐店品类声明哈希。
#: 旧版本直接拒绝，不保留兼容解析（docs/CONTRACTS.md#contracts-worldmock-接口合同「冻结地图与场景引用」）。
MAP_SCHEMA_VERSION = 3








class TemporalDeclaration(FrozenModel):
    """@brief 具有生效区间、核对日期与来源的数值声明。"""

    effective_from: datetime
    effective_to: datetime | None
    reviewed_on: date
    still_valid_on: date
    source: models.DataSource

    @model_validator(mode="after")
    def validate_temporal(self) -> "TemporalDeclaration":
        """@brief 拒绝无时区、倒置区间和不一致的核对日期。"""
        models._require_source_aware(self.effective_from)
        if self.effective_to is not None:
            models._require_source_aware(self.effective_to)
            if self.effective_to <= self.effective_from:
                reject_value("Declaration effective interval must be positive")
        if self.reviewed_on > self.still_valid_on:
            reject_value("Declaration review date cannot follow its validity date")
        if self.reviewed_on > self.source.fact_cutoff:
            reject_value("Declaration review date cannot follow its source fact cutoff")
        return self




class OpeningCalendar(TemporalDeclaration):
    """@brief 按周声明并由明确日期例外覆盖的营业日历。"""

    weekly: dict[str, tuple[models.OpeningInterval, ...]]
    exceptions: dict[str, tuple[models.OpeningInterval, ...]]

    @model_validator(mode="after")
    def validate_calendar(self) -> "OpeningCalendar":
        """@brief 一周必须完整声明，日期必须规范且每天区间不得重叠。"""
        if set(self.weekly) != {str(day) for day in range(7)}:
            reject_value("Opening calendar must explicitly declare all seven weekdays")
        for key in self.exceptions:
            try:
                if date.fromisoformat(key).isoformat() != key:
                    reject_value("Opening exception date must use ISO format")
            except ValueError:
                reject_value("Opening exception date must use ISO format")
        for values in (*self.weekly.values(), *self.exceptions.values()):
            ordered = sorted(values, key=lambda value: value.opens)
            if any(first.closes > second.opens for first, second in zip(ordered, ordered[1:])):
                reject_value("Opening intervals must not overlap")
        _check_weekly_overlap(self.weekly)
        return self


def _check_weekly_overlap(weekly: dict[str, tuple[models.OpeningInterval, ...]]) -> None:
    """@brief 同时核验跨午夜区间与次日营业区间是否重叠。"""
    for day in range(7):
        previous = weekly[str((day - 1) % 7)]
        current = weekly[str(day)]
        if any(left.closes - 1440 > right.opens for left in previous for right in current):
            reject_value("Weekly opening intervals overlap across midnight")


class QuoteConfig(TemporalDeclaration):
    """@brief 一个供给的报价版本和有效期。"""

    price_version: str = Field(pattern=r"\S")
    price_fen: int = Field(gt=0)


def validate_declarations(values: tuple[TemporalDeclaration, ...], label: str) -> None:
    """@brief 拒绝同一对象的时态声明重叠，允许显式未知间隙。"""
    ordered = sorted(values, key=lambda item: item.effective_from)
    for first, second in zip(ordered, ordered[1:]):
        if first.effective_to is None or first.effective_to > second.effective_from:
            reject_value(f"{label} effective intervals overlap")


class BrandConfig(FrozenModel):
    """@brief 来源明确时才记录的可选品牌身份。"""

    brand_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    source: models.DataSource


class LocationConfig(FrozenModel):
    """@brief 单一权威地点定义，明确区分菜单分类、业态与活动环境。

    branch_label 是可选的分店标签，用来区分同名地点：显示名为「名称（branch_label）」。场景选图后的运行时地点
    已把它并入 name（map_data.select_map），给模型与导出的地点名一律是显示名。
    """

    location_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    branch_label: str | None = Field(default=None, pattern=r"\S")
    kind: str = Field(pattern=r"\S")
    business_kind: str = Field(pattern=r"\S")
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    location_kinds: tuple[str, ...] = Field(min_length=1)
    opening_calendars: tuple[OpeningCalendar, ...]
    required_qualifications: tuple[str, ...]
    brand: BrandConfig | None
    source: models.DataSource
    affordances: dict[str, Any]

    @model_validator(mode="after")
    def validate_location(self) -> "LocationConfig":
        """@brief 活动环境必须显式合法且不得重复，营业声明不得重叠。"""
        kinds = set(self.location_kinds)
        if len(kinds) != len(self.location_kinds) or not kinds <= set(LOCATION_KINDS):
            reject_value("Location activity kinds must be unique supported values")
        if "anywhere" in kinds:
            reject_value("Location activity kinds cannot use the activity-only anywhere value")
        validate_names(self.required_qualifications, "Location qualifications")
        validate_declarations(self.opening_calendars, "Opening calendar")
        return self

    @property
    def display_name(self) -> str:
        """@brief 显示名：有分店标签时为「名称（branch_label）」，否则就是名称。"""
        return self.name if self.branch_label is None else f"{self.name}（{self.branch_label}）"


class SupplyConfig(FrozenModel):
    """@brief 地点、商品与卖方账户共同定义的一条独立门店供给。"""

    supply_id: str = Field(pattern=r"\S")
    location_id: str = Field(pattern=r"\S")
    product_id: str = Field(pattern=r"\S")
    seller_account_id: str = Field(pattern=r"\S")
    quotes: tuple[QuoteConfig, ...]
    initial_stock: int = Field(ge=0)
    fulfillment_modes: tuple[Literal["pickup", "delivery"], ...] = Field(min_length=1)
    delivery_destination_ids: tuple[str, ...]
    delivery_delay_seconds: int = Field(ge=0)
    required_qualifications: tuple[str, ...]
    source: models.DataSource

    @model_validator(mode="after")
    def validate_supply(self) -> "SupplyConfig":
        """@brief 报价身份、资格与履约范围必须唯一且相互一致。"""
        validate_declarations(self.quotes, "Supply quote")
        validate_names(tuple(quote.price_version for quote in self.quotes), "Quote versions")
        validate_names(self.fulfillment_modes, "Fulfillment modes")
        validate_names(self.delivery_destination_ids, "Delivery destinations")
        validate_names(self.required_qualifications, "Supply qualifications")
        if bool(self.delivery_destination_ids) != ("delivery" in self.fulfillment_modes):
            reject_value("Delivery support must match its explicit destination scope")
        return self


def validate_names(values: tuple[str, ...], label: str) -> None:
    """@brief 拒绝空白和重复身份。"""
    if len(values) != len(set(values)) or any(not value.strip() for value in values):
        reject_value(f"{label} must contain unique nonblank values")


def validate_price_versions(supplies: tuple[SupplyConfig, ...], changes) -> None:
    """@brief 同一供给的所有声明与预排调价版本必须终身唯一，不能重复授权旧报价。"""
    seen = {(supply.supply_id, quote.price_version) for supply in supplies for quote in supply.quotes}
    for change in changes:
        identity = change.supply_id, change.price_version
        if identity in seen:
            reject_value("Supply price versions must be unique across declarations and scheduled changes")
        seen.add(identity)


class RouteConfig(FrozenModel):
    """@brief 冻结可达或不可达路由，保留实际路由实例与方式依据。"""

    route_id: str = Field(pattern=r"\S")
    origin_id: str = Field(pattern=r"\S")
    destination_id: str = Field(pattern=r"\S")
    mode: TravelMode
    status: Literal["reachable", "unreachable", "unknown"]
    distance_meters: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    duration_seconds: int | None = Field(default=None, gt=0)
    fare_fen: int | None = Field(default=None, ge=0)
    fare_source_kind: Literal["scenario"]
    response_path: str | None = Field(default=None, pattern=r"\S")
    response_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reason: str | None = Field(default=None, pattern=r"\S")
    source_url: str = Field(pattern=r"\S")
    routing_instance: str = Field(pattern=r"\S")
    routing_profile: Literal["foot", "car"]
    source: models.DataSource

    @model_validator(mode="after")
    def validate_route(self) -> "RouteConfig":
        """@brief 状态与距离时长必须一致，路线方式必须匹配路由配置。"""
        metrics = (self.distance_meters, self.duration_seconds, self.fare_fen)
        if self.status == "reachable" and any(value is None for value in metrics):
            reject_value("Reachable route requires distance, duration, and fare")
        if self.status != "reachable" and any(value is not None for value in metrics):
            reject_value("Non-reachable route cannot contain reachable metrics")
        if self.status == "unknown":
            if self.response_path is not None or self.response_sha256 is not None or not self.reason:
                reject_value("Unknown route requires a reason and no response claim")
        elif self.response_path is None or self.response_sha256 is None:
            reject_value("Known route status requires a frozen response")
        if self.origin_id == self.destination_id:
            reject_value("Route endpoints must differ")
        expected = "foot" if self.mode == TravelMode.WALKING else "car"
        if self.routing_profile != expected or not self.source_url.startswith(self.routing_instance + "/"):
            reject_value("Route mode, profile, and routing instance disagree")
        return self


class FileReference(FrozenModel):
    """@brief 冻结地图内部文件的路径与内容身份。"""

    path: str = Field(pattern=r"\S")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MapDatasetReference(FileReference):
    """@brief 场景选择的一版冻结地图。"""

    dataset_id: str = Field(pattern=r"\S")
    version: int = Field(ge=1)


class LocationReference(FrozenModel):
    """@brief 场景选择的地点身份，不复制定义。"""

    location_id: str = Field(pattern=r"\S")


class SupplyReference(FrozenModel):
    """@brief 场景选择的供给与可选库存初值。"""

    supply_id: str = Field(pattern=r"\S")
    initial_stock: int | None = Field(default=None, ge=0)


class MapDatasetDeclaration(FrozenModel):
    """@brief 地图发布身份、区域、来源及三个冻结目录文件。"""

    schema_version: Literal[MAP_SCHEMA_VERSION]
    dataset_id: str = Field(pattern=r"\S")
    version: int = Field(ge=1)
    random_seed: StrictInt
    category_assignment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    coordinate_system: Literal["WGS84"]
    timezone: str = Field(pattern=r"\S")
    bounds: tuple[float, float, float, float]
    prepared_at: datetime
    source: models.DataSource
    locations_file: FileReference
    supplies_file: FileReference
    routes_file: FileReference
    route_modes: tuple[TravelMode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> "MapDatasetDeclaration":
        """@brief 发布时区、选区边界、方式和时间必须明确有效。"""
        models._require_source_aware(self.prepared_at)
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            reject_value("Map timezone is invalid")
        west, south, east, north = self.bounds
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
            reject_value("Map coordinate bounds are invalid")
        validate_names(self.route_modes, "Map route modes")
        return self


class MapDataset(MapDatasetDeclaration):
    """@brief 通过文件身份和关系准入后的完整只读地图。"""

    locations: tuple[LocationConfig, ...]
    supplies: tuple[SupplyConfig, ...]
    routes: tuple[RouteConfig, ...]
    category_assignment: "CategoryAssignment"


class StoreAssortment(FrozenModel):
    """@brief 一家门店完整的品类、商品、价格定位和来源声明。"""

    location_id: str = Field(pattern=r"\S")
    category_ids: tuple[str, ...]
    object_ids: tuple[str, ...]
    price_position_percent: StrictInt = Field(ge=5, le=95)
    source: models.DataSource

    @model_validator(mode="after")
    def validate_assortment(self) -> "StoreAssortment":
        """@brief 每个选择身份必须唯一，门店不能发布空商品集合。"""
        validate_names(self.category_ids, "Store categories")
        validate_names(self.object_ids, "Store objects")
        if not self.category_ids and not self.object_ids:
            reject_value("Selling locations require a nonempty assortment")
        return self


class CategoryAssignment(FrozenModel):
    """@brief 地点类型的品类准入和逐店完整商品集合。"""

    kinds: dict[str, tuple[str, ...]]
    locations: tuple[StoreAssortment, ...]
    source: models.DataSource

    @model_validator(mode="after")
    def validate_assignment(self) -> "CategoryAssignment":
        """@brief 拒绝重复门店和品类，空集合必须由作者显式声明。"""
        validate_names(tuple(item.location_id for item in self.locations), "Assortment locations")
        for kind, values in self.kinds.items():
            if not kind.strip():
                reject_value("Assortment kind must be nonblank")
            validate_names(values, "Kind admission categories")
        return self


MapDataset.model_rebuild()
