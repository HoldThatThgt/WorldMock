"""@brief 环境对象、绑定和发布图的严格不可变数据合同。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import Field, StrictInt

from worldmock.base_models import DeepFrozenModel
from worldmock.exchange.types import (
    ContentKind, FoodCategory, LocationKind, PLACE_KIND_ENVIRONMENTS, SummarySourceKind,
)

PositiveInteger = Annotated[StrictInt, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]
NonEmptyString = Annotated[str, Field(strict=True, pattern=r"\S")]


def channel_key(name: str) -> str:
    """@brief 应用名称去掉全部空白后不分大小写比较，不做其他归一化。"""
    return "".join(name.split()).casefold()


class CatalogFile(DeepFrozenModel):
    """@brief 清单登记的一份目录文件字节身份。"""

    path: NonEmptyString
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    size_bytes: NonNegativeInteger


class CatalogManifest(DeepFrozenModel):
    """@brief 目录版本、规则版本和完整文件集合的发布身份。"""

    format_version: Literal[1]
    environment_id: NonEmptyString
    environment_version: NonEmptyString
    rules_version: StrictInt
    files: tuple[CatalogFile, ...]
    catalog_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)


class App(DeepFrozenModel):
    """@brief 应用只有内部身份和可读名称。"""

    app_id: NonEmptyString
    name: NonEmptyString


class Location(DeepFrozenModel):
    """@brief 场所类型及食品店铺的应用绑定。"""

    location_id: NonEmptyString
    name: NonEmptyString
    branch_label: NonEmptyString | None = None
    kind: LocationKind
    app_ids: tuple[NonEmptyString, ...]

    @property
    def display_name(self) -> str:
        """@brief 在声明了分店标签时返回带标签的场所显示名。"""
        return self.name if self.branch_label is None else f"{self.name}（{self.branch_label}）"


class Food(DeepFrozenModel):
    """@brief 固定类别、固定单价和按店铺类型完整绑定的食品。"""

    food_id: NonEmptyString
    name: NonEmptyString
    category: FoodCategory
    price_fen: PositiveInteger
    store_ids: tuple[NonEmptyString, ...] | Literal["all"]


class SummarySource(DeepFrozenModel):
    """@brief 摘要的真实来源地址或合成说明。"""

    kind: SummarySourceKind
    url: str | None
    notice: str | None


class ContentSeries(DeepFrozenModel):
    """@brief 连续内容所属系列及集数。"""

    title: NonEmptyString
    episode: PositiveInteger


class Content(DeepFrozenModel):
    """@brief 应用内容及其完整摘要、时长和绑定。"""

    content_id: NonEmptyString
    title: NonEmptyString
    kind: ContentKind
    summary: str = Field(strict=True)
    summary_source: SummarySource
    duration_seconds: PositiveInteger
    app_ids: tuple[NonEmptyString, ...]
    series: ContentSeries | None = None


class WorkTopic(DeepFrozenModel):
    """@brief 按实际工时推进的工作主题。"""

    topic_id: NonEmptyString
    topic: NonEmptyString
    target_hours: float = Field(gt=0, strict=True, allow_inf_nan=False)

    @property
    def target_seconds(self) -> int:
        """@brief 按冻结规则把目标小时换算为整秒。"""
        return round(self.target_hours * 3600)


class MapEdge(DeepFrozenModel):
    """@brief 两个规范端点之间的无向正长度连接。"""

    a: NonEmptyString
    b: NonEmptyString
    length_m: PositiveInteger


class MapGraph(DeepFrozenModel):
    """@brief 已发布的生成图模型，不包含运行期最短路查询。"""

    schema_version: Literal[1]
    seed: StrictInt
    generator_rule_version: Literal[1]
    nodes: tuple[NonEmptyString, ...]
    edges: tuple[MapEdge, ...]


class AppsFile(DeepFrozenModel):
    """@brief 应用文件的唯一根字段。"""

    apps: tuple[App, ...]


class LocationsFile(DeepFrozenModel):
    """@brief 地点文件的唯一根字段。"""

    locations: tuple[Location, ...]


class FoodsFile(DeepFrozenModel):
    """@brief 食品文件的唯一根字段。"""

    foods: tuple[Food, ...]


class ContentsFile(DeepFrozenModel):
    """@brief 内容文件的唯一根字段。"""

    contents: tuple[Content, ...]


class WorkTopicsFile(DeepFrozenModel):
    """@brief 工作主题文件的唯一根字段。"""

    work_topics: tuple[WorkTopic, ...]


class CatalogData(DeepFrozenModel):
    """@brief 登记前尚未建立身份索引的原始对象集合。"""

    apps: tuple[App, ...]
    locations: tuple[Location, ...]
    foods: tuple[Food, ...]
    contents: tuple[Content, ...]
    work_topics: tuple[WorkTopic, ...]
    map_data: MapGraph


@dataclass(frozen=True)
class Catalog:
    """@brief 已通过登记校验的对象映射及冻结派生索引。"""

    #: 目录版本和字节身份。
    manifest: CatalogManifest
    #: 正在读取的目录根。
    source_dir: Path
    #: 按内部身份索引的应用。
    apps: Mapping[str, App]
    #: 按内部身份索引的地点。
    locations: Mapping[str, Location]
    #: 按内部身份索引的食品。
    foods: Mapping[str, Food]
    #: 按内部身份索引的内容。
    contents: Mapping[str, Content]
    #: 按内部身份索引的工作主题。
    work_topics: Mapping[str, WorkTopic]
    #: 已发布图的严格模型。
    map_data: MapGraph
    #: 发布图原始字节摘要。
    map_sha256: str
    #: 每种食品店铺类型拥有的食品身份。
    menus_by_kind: Mapping[LocationKind, tuple[str, ...]]
    #: 每个地点拥有的食品身份，非食品地点为空。
    foods_by_store: Mapping[str, tuple[str, ...]]
    #: 每个应用绑定的店铺身份。
    stores_by_app: Mapping[str, tuple[str, ...]]
    #: 每个应用绑定的内容身份。
    contents_by_app: Mapping[str, tuple[str, ...]]
    #: 每个地点绑定的应用身份。
    apps_by_store: Mapping[str, tuple[str, ...]]

    def location_kinds(self, kind: LocationKind | str) -> tuple[str, ...]:
        """@brief 从地点类型派生生活活动的环境类型。"""
        return (PLACE_KIND_ENVIRONMENTS[LocationKind(kind)],)
