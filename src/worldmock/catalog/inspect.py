"""@brief 目录只读查询：边聚合、对象影响面与溯源链。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.catalog.models import (
    Catalog,
    CatalogError,
    ConvertEdge,
    DrainEdge,
    Edge,
    ExchangeObject,
    ObjectKind,
    Provenance,
    ProvenanceType,
    SourceEdge,
    TransferEdge,
)


@dataclass(frozen=True)
class EdgeHit:
    """@brief 一条触及某资源的边及其宿主对象。"""

    object_id: str
    object_kind: ObjectKind
    edge: Edge
    relation: str


@dataclass(frozen=True)
class LineageEntry:
    """@brief 溯源链上的一站：某数值部分来自哪个来源、落在哪些路径。"""

    part: str
    type: ProvenanceType
    source: str
    artifact: str | None
    method: str | None
    paths: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class LineageReport:
    """@brief 一个交换对象的完整溯源链。"""

    object_id: str
    object_version: int
    entries: tuple[LineageEntry, ...]


def edges_of(catalog: Catalog, resource_id: str) -> tuple[EdgeHit, ...]:
    """@brief 聚合全部给某资源注水或放水的边（含已墓碑对象的边，保留可读）。

    :param catalog: 冻结目录视图。
    :param resource_id: 目标资源 id。
    :returns: 按（对象 id、边序）稳定排序的边命中元组。
    :raises CatalogError: 资源 id 不在注册表中。
    """
    _require_resource(catalog, resource_id)
    hits = [
        hit
        for object_id, obj in sorted(catalog.objects.items())
        for hit in _object_edge_hits(obj, resource_id, object_id)
    ]
    return tuple(hits)


def objects_affecting(catalog: Catalog, resource_id: str) -> tuple[str, ...]:
    """@brief 返回影响某资源的全部对象 id（含已墓碑对象，按字典序）。

    :param catalog: 冻结目录视图。
    :param resource_id: 目标资源 id。
    :returns: 排序去重后的对象 id 元组。
    :raises CatalogError: 资源 id 不在注册表中。
    """
    _require_resource(catalog, resource_id)
    affecting = {
        object_id
        for object_id, obj in catalog.objects.items()
        if any(endpoint == resource_id for edge in obj.edges for endpoint in _edge_resource_ids(edge))
    }
    return tuple(sorted(affecting))


def lineage(catalog: Catalog, object_id: str) -> LineageReport:
    """@brief 展开一个对象的溯源链：每个数值部分来自哪个来源。

    :param catalog: 冻结目录视图。
    :param object_id: 目标对象 id。
    :returns: 对象版本与逐条溯源记录。
    :raises CatalogError: 对象 id 不在目录中。
    """
    obj = catalog.objects.get(object_id)
    if obj is None:
        raise CatalogError("INSPECT_OBJECT_UNKNOWN", f"object {object_id} not in catalog")
    entries = tuple(_lineage_entry(record) for record in obj.provenance)
    return LineageReport(
        object_id=obj.object_id,
        object_version=obj.version,
        entries=entries,
    )


def _lineage_entry(record: Provenance) -> LineageEntry:
    """@brief 把模型溯源记录投影为只读溯源链节点。"""
    return LineageEntry(
        part=record.part,
        type=record.type,
        source=record.source,
        artifact=record.artifact,
        method=record.method,
        paths=record.paths,
        note=record.note,
    )


def _require_resource(catalog: Catalog, resource_id: str) -> None:
    """@brief 查询前要求资源 id 已注册。"""
    if resource_id not in catalog.resources:
        raise CatalogError("INSPECT_RESOURCE_UNKNOWN", f"resource {resource_id} not in catalog")


def _object_edge_hits(obj: ExchangeObject, resource_id: str, object_id: str) -> list[EdgeHit]:
    """@brief 产出单个对象中触及目标资源的边命中。"""
    hits: list[EdgeHit] = []
    for edge in obj.edges:
        for endpoint, relation in _edge_relations(edge):
            if endpoint == resource_id:
                hits.append(
                    EdgeHit(
                        object_id=object_id,
                        object_kind=obj.object_kind,
                        edge=edge,
                        relation=relation,
                    )
                )
    return hits


def _edge_relations(edge: Edge) -> tuple[tuple[str, str], ...]:
    """@brief 返回边触及的（资源 id, 关系）对。"""
    if isinstance(edge, TransferEdge):
        return ((edge.from_account, "transfer_from"), (edge.to_account, "transfer_to"))
    if isinstance(edge, SourceEdge):
        return ((edge.resource, "source"),)
    if isinstance(edge, DrainEdge):
        return ((edge.resource, "drain"),)
    if isinstance(edge, ConvertEdge):
        return ((edge.from_endpoint, "convert_from"), (edge.to_endpoint, "convert_to"))
    return ()


def _edge_resource_ids(edge: Edge) -> tuple[str, ...]:
    """@brief 返回一条边触及的全部资源 id。"""
    return tuple(endpoint for endpoint, _ in _edge_relations(edge))
