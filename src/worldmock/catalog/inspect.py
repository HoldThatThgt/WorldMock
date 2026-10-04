"""@brief 编译目录的资源影响面与对象来源只读查询。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.categories import ProducerType, ProvenanceType, SourceRecord
from worldmock.catalog.models import (
    Catalog,
    CategoryError,
    CompiledEdge,
    DrainEdge,
    ExchangeObject,
    ObjectKind,
    RuleEdge,
    SourceEdge,
    TransferEdge,
)


@dataclass(frozen=True)
class EdgeHit:
    """@brief 一条触及指定资源的编译边及其宿主对象。"""

    object_id: str
    object_kind: ObjectKind
    edge: CompiledEdge
    relation: str


@dataclass(frozen=True)
class LineageEntry:
    """@brief 对象来源链中的一条可审核记录。"""

    source_id: str
    type: ProvenanceType
    producer: ProducerType
    source: str
    artifact: str | None
    method: str | None
    paths: tuple[str, ...]
    note: str | None


@dataclass(frozen=True)
class LineageReport:
    """@brief 一个 raw 对象版本的完整来源链。"""

    object_id: str
    object_version: int
    entries: tuple[LineageEntry, ...]


def edges_of(catalog: Catalog, resource_id: str) -> tuple[EdgeHit, ...]:
    """@brief 聚合全部触及指定资源的字面边与 typed rule 引用边。"""
    _require_resource(catalog, resource_id)
    hits = [
        hit
        for object_id, obj in sorted(catalog.objects.items())
        for hit in _object_edge_hits(catalog, obj, resource_id, object_id)
    ]
    return tuple(hits)


def objects_affecting(catalog: Catalog, resource_id: str) -> tuple[str, ...]:
    """@brief 返回影响指定资源的全部对象身份，按字典序。"""
    return tuple(sorted({hit.object_id for hit in edges_of(catalog, resource_id)}))


def lineage(catalog: Catalog, object_id: str) -> LineageReport:
    """@brief 展开一个 raw 对象的版本与来源链。"""
    obj = catalog.raw_objects.get(object_id)
    if obj is None:
        raise CategoryError("INSPECT_OBJECT_UNKNOWN", f"object {object_id} not in catalog")
    return LineageReport(
        object_id=obj.object_id,
        object_version=obj.version,
        entries=tuple(_lineage_entry(record) for record in obj.provenance),
    )


def _lineage_entry(record: SourceRecord) -> LineageEntry:
    """@brief 把来源模型投影为稳定只读报告。"""
    return LineageEntry(
        source_id=record.source_id,
        type=record.type,
        producer=record.producer,
        source=record.source,
        artifact=record.artifact,
        method=record.method,
        paths=record.paths,
        note=record.note,
    )


def _require_resource(catalog: Catalog, resource_id: str) -> None:
    """@brief 查询前要求资源已注册。"""
    if resource_id not in catalog.resources:
        raise CategoryError("INSPECT_RESOURCE_UNKNOWN", f"resource {resource_id} not in catalog")


def _object_edge_hits(
    catalog: Catalog,
    obj: ExchangeObject,
    resource_id: str,
    object_id: str,
) -> list[EdgeHit]:
    """@brief 返回单对象中触及目标资源的边。"""
    hits: list[EdgeHit] = []
    for edge in obj.edges:
        relation = _edge_relation(catalog, edge, resource_id)
        if relation is not None:
            hits.append(EdgeHit(object_id, obj.object_kind, edge, relation))
    return hits


def _edge_relation(catalog: Catalog, edge: CompiledEdge, resource_id: str) -> str | None:
    """@brief 返回边对目标资源的关系，不触及时返回空。"""
    if isinstance(edge, SourceEdge) and edge.resource == resource_id:
        return "source"
    if isinstance(edge, DrainEdge) and edge.resource == resource_id:
        return "drain"
    if isinstance(edge, RuleEdge):
        rule = catalog.exchange_rules.require(edge.rule_id)
        return "rule" if rule.resource_id == resource_id else None
    if isinstance(edge, TransferEdge):
        return "transfer" if edge.resource == resource_id else None
    return None
