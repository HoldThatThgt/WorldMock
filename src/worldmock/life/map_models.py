"""@brief 冻结地图、地点、门店供给与时态声明的严格数据模型。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from worldmock.life.models import FrozenModel, TravelMode

LOCATION_KINDS = frozenset({"home", "office", "outdoor", "fitness", "venue", "anywhere"})


def reject(message: str) -> None:
    """@brief 记录英文拒绝原因并中止不完整的地图声明。"""
    logging.error(message)
    raise ValueError(message)


def require_aware(value: datetime) -> None:
    """@brief 拒绝没有明确时区的地图时间。"""
    if value.tzinfo is None or value.utcoffset() is None:
        reject("Map timestamps must be timezone-aware")


class DataSource(FrozenModel):
    """@brief 一份事实或场景假设的来源、许可与适用边界。"""

    kind: Literal["fact", "scenario"]
    url: str = Field(pattern=r"\S")
    retrieved_at: datetime
    license: str = Field(pattern=r"\S")
    notice: str = Field(pattern=r"\S")
    fact_cutoff: date
    applicability: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_source(self) -> "DataSource":
        """@brief 来源取得时间必须明确时区。"""
        require_aware(self.retrieved_at)
        if self.fact_cutoff > self.retrieved_at.date():
            reject("Source fact cutoff cannot follow retrieval date")
        return self


class TemporalDeclaration(FrozenModel):
    """@brief 具有生效区间、核对日期与来源的数值声明。"""

    effective_from: datetime
    effective_to: datetime | None
    reviewed_on: date
    still_valid_on: date
    source: DataSource

    @model_validator(mode="after")
    def validate_temporal(self) -> "TemporalDeclaration":
        """@brief 拒绝无时区、倒置区间和不一致的核对日期。"""
        require_aware(self.effective_from)
        if self.effective_to is not None:
            require_aware(self.effective_to)
            if self.effective_to <= self.effective_from:
                reject("Declaration effective interval must be positive")
        if self.reviewed_on > self.still_valid_on:
            reject("Declaration review date cannot follow its validity date")
        if self.reviewed_on > self.source.fact_cutoff:
            reject("Declaration review date cannot follow its source fact cutoff")
        return self


class OpeningInterval(FrozenModel):
    """@brief 当地自然日起算的营业分钟区间；结束超过午夜表示跨日。"""

    opens: int = Field(ge=0, lt=1440)
    closes: int = Field(gt=0, le=2880)

    @model_validator(mode="after")
    def validate_interval(self) -> "OpeningInterval":
        """@brief 营业区间必须正向且最多延续二十四小时。"""
        if not self.opens < self.closes <= self.opens + 1440:
            reject("Opening interval must be positive and at most one day")
        return self


class OpeningCalendar(TemporalDeclaration):
    """@brief 按周声明并由明确日期例外覆盖的营业日历。"""

    weekly: dict[str, tuple[OpeningInterval, ...]]
    exceptions: dict[str, tuple[OpeningInterval, ...]]

    @model_validator(mode="after")
    def validate_calendar(self) -> "OpeningCalendar":
        """@brief 一周必须完整声明，日期必须规范且每天区间不得重叠。"""
        if set(self.weekly) != {str(day) for day in range(7)}:
            reject("Opening calendar must explicitly declare all seven weekdays")
        for key in self.exceptions:
            try:
                if date.fromisoformat(key).isoformat() != key:
                    reject("Opening exception date must use ISO format")
            except ValueError:
                reject("Opening exception date must use ISO format")
        for values in (*self.weekly.values(), *self.exceptions.values()):
            ordered = sorted(values, key=lambda value: value.opens)
            if any(first.closes > second.opens for first, second in zip(ordered, ordered[1:])):
                reject("Opening intervals must not overlap")
        _check_weekly_overlap(self.weekly)
        return self


def _check_weekly_overlap(weekly: dict[str, tuple[OpeningInterval, ...]]) -> None:
    """@brief 同时核验跨午夜区间与次日营业区间是否重叠。"""
    for day in range(7):
        previous = weekly[str((day - 1) % 7)]
        current = weekly[str(day)]
        if any(left.closes - 1440 > right.opens for left in previous for right in current):
            reject("Weekly opening intervals overlap across midnight")


class QuoteConfig(TemporalDeclaration):
    """@brief 一个供给的报价版本和有效期。"""

    price_version: str = Field(pattern=r"\S")
    price_fen: int = Field(gt=0)


def validate_declarations(values: tuple[TemporalDeclaration, ...], label: str) -> None:
    """@brief 拒绝同一对象的时态声明重叠，允许显式未知间隙。"""
    ordered = sorted(values, key=lambda item: item.effective_from)
    for first, second in zip(ordered, ordered[1:]):
        if first.effective_to is None or first.effective_to > second.effective_from:
            reject(f"{label} effective intervals overlap")


class BrandConfig(FrozenModel):
    """@brief 来源明确时才记录的可选品牌身份。"""

    brand_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    source: DataSource


class LocationConfig(FrozenModel):
    """@brief 单一权威地点定义，明确区分菜单分类、业态与活动环境。"""

    location_id: str = Field(pattern=r"\S")
    name: str = Field(pattern=r"\S")
    kind: str = Field(pattern=r"\S")
    business_kind: str = Field(pattern=r"\S")
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    location_kinds: tuple[str, ...] = Field(min_length=1)
    opening_calendars: tuple[OpeningCalendar, ...]
    required_qualifications: tuple[str, ...]
    brand: BrandConfig | None
    source: DataSource
    affordances: dict[str, Any]

    @model_validator(mode="after")
    def validate_location(self) -> "LocationConfig":
        """@brief 活动环境必须显式合法且不得重复，营业声明不得重叠。"""
        kinds = set(self.location_kinds)
        if len(kinds) != len(self.location_kinds) or not kinds <= LOCATION_KINDS:
            reject("Location activity kinds must be unique supported values")
        if "anywhere" in kinds:
            reject("Location activity kinds cannot use the activity-only anywhere value")
        validate_names(self.required_qualifications, "Location qualifications")
        validate_declarations(self.opening_calendars, "Opening calendar")
        return self


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
    source: DataSource

    @model_validator(mode="after")
    def validate_supply(self) -> "SupplyConfig":
        """@brief 报价身份、资格与履约范围必须唯一且相互一致。"""
        validate_declarations(self.quotes, "Supply quote")
        validate_names(tuple(quote.price_version for quote in self.quotes), "Quote versions")
        validate_names(self.fulfillment_modes, "Fulfillment modes")
        validate_names(self.delivery_destination_ids, "Delivery destinations")
        validate_names(self.required_qualifications, "Supply qualifications")
        if bool(self.delivery_destination_ids) != ("delivery" in self.fulfillment_modes):
            reject("Delivery support must match its explicit destination scope")
        return self


def validate_names(values: tuple[str, ...], label: str) -> None:
    """@brief 拒绝空白和重复身份。"""
    if len(values) != len(set(values)) or any(not value.strip() for value in values):
        reject(f"{label} must contain unique nonblank values")


def validate_price_versions(supplies: tuple[SupplyConfig, ...], changes) -> None:
    """@brief 同一供给的所有声明与预排调价版本必须终身唯一，不能重复授权旧报价。"""
    seen = {(supply.supply_id, quote.price_version) for supply in supplies for quote in supply.quotes}
    for change in changes:
        identity = change.supply_id, change.price_version
        if identity in seen:
            reject("Supply price versions must be unique across declarations and scheduled changes")
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
    source: DataSource

    @model_validator(mode="after")
    def validate_route(self) -> "RouteConfig":
        """@brief 状态与距离时长必须一致，路线方式必须匹配路由配置。"""
        metrics = (self.distance_meters, self.duration_seconds, self.fare_fen)
        if self.status == "reachable" and any(value is None for value in metrics):
            reject("Reachable route requires distance, duration, and fare")
        if self.status != "reachable" and any(value is not None for value in metrics):
            reject("Non-reachable route cannot contain reachable metrics")
        if self.status == "unknown":
            if self.response_path is not None or self.response_sha256 is not None or not self.reason:
                reject("Unknown route requires a reason and no response claim")
        elif self.response_path is None or self.response_sha256 is None:
            reject("Known route status requires a frozen response")
        if self.origin_id == self.destination_id:
            reject("Route endpoints must differ")
        expected = "foot" if self.mode == TravelMode.WALKING else "car"
        if self.routing_profile != expected or not self.source_url.startswith(self.routing_instance + "/"):
            reject("Route mode, profile, and routing instance disagree")
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

    schema_version: Literal[1]
    dataset_id: str = Field(pattern=r"\S")
    version: int = Field(ge=1)
    coordinate_system: Literal["WGS84"]
    timezone: str = Field(pattern=r"\S")
    bounds: tuple[float, float, float, float]
    prepared_at: datetime
    source: DataSource
    locations_file: FileReference
    supplies_file: FileReference
    routes_file: FileReference
    route_modes: tuple[TravelMode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> "MapDatasetDeclaration":
        """@brief 发布时区、选区边界、方式和时间必须明确有效。"""
        require_aware(self.prepared_at)
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            reject("Map timezone is invalid")
        west, south, east, north = self.bounds
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
            reject("Map coordinate bounds are invalid")
        validate_names(self.route_modes, "Map route modes")
        return self


class MapDataset(MapDatasetDeclaration):
    """@brief 通过文件身份和关系准入后的完整只读地图。"""

    locations: tuple[LocationConfig, ...]
    supplies: tuple[SupplyConfig, ...]
    routes: tuple[RouteConfig, ...]
