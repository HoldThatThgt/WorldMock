"""@brief 目录只读查询的正确性测试。"""

from __future__ import annotations

import pytest

from worldmock.catalog.inspect import edges_of, lineage, objects_affecting
from worldmock.catalog.models import CatalogError, ObjectKind, ProvenanceType


def test_edges_of_aggregates_all_relations(loaded_catalog) -> None:
    """@brief 能量池聚合全部食品的 source 边。"""
    hits = edges_of(loaded_catalog, "energy_kcal")
    assert [(hit.object_id, hit.relation) for hit in hits] == [
        ("food-milk", "source"),
        ("food-soda", "source"),
    ]
    assert all(hit.edge.type == "source" for hit in hits)


def test_edges_of_ignores_placeholder_endpoints(loaded_catalog) -> None:
    """@brief 占位端点不触及类型级资源：money_fen 只被角色实例触碰。"""
    hits = edges_of(loaded_catalog, "money_fen")
    assert hits == ()
    assert objects_affecting(loaded_catalog, "money_fen") == ()


def test_objects_affecting_returns_sorted_ids(loaded_catalog) -> None:
    """@brief 影响能量的全部对象按字典序返回。"""
    assert objects_affecting(loaded_catalog, "energy_kcal") == (
        "food-milk",
        "food-soda",
    )


def test_objects_affecting_can_be_empty(loaded_catalog) -> None:
    """@brief 无人触及的资源返回空元组。"""
    assert objects_affecting(loaded_catalog, "sleep_debt_hours") == ()


def test_lineage_lists_every_part(loaded_catalog) -> None:
    """@brief 溯源链逐部分展开来源分类、出处与字段路径。"""
    report = lineage(loaded_catalog, "food-milk")
    assert report.object_id == "food-milk"
    assert report.object_version == 1
    assert [(entry.part, entry.type) for entry in report.entries] == [
        ("consume energy edge", ProvenanceType.FACT),
        ("purchase price edge", ProvenanceType.ANCHORED),
        ("stock", ProvenanceType.SCENARIO),
        ("legacy timing/serving fields", ProvenanceType.SCENARIO),
    ]
    assert report.entries[0].source == "openfoodfacts:product/6923644266066"
    assert report.entries[0].paths == ("edges[1].amount",)
    assert report.entries[1].method == "point-in-distribution p60"


def test_lineage_contract_object(loaded_catalog) -> None:
    """@brief 合同对象的溯源链覆盖结算公式、验收门槛与效率参数。"""
    report = lineage(loaded_catalog, "work-ami")
    assert [entry.part for entry in report.entries] == [
        "settlement formula",
        "acceptance gates",
        "efficiency params",
    ]
    assert report.entries[2].paths == ("efficiency",)


def test_edges_of_unknown_resource_raises(loaded_catalog) -> None:
    """@brief 查询未注册资源以稳定错误码失败。"""
    with pytest.raises(CatalogError) as excinfo:
        edges_of(loaded_catalog, "calories")
    assert excinfo.value.code == "INSPECT_RESOURCE_UNKNOWN"


def test_objects_affecting_unknown_resource_raises(loaded_catalog) -> None:
    """@brief 影响面查询同样要求资源已注册。"""
    with pytest.raises(CatalogError) as excinfo:
        objects_affecting(loaded_catalog, "calories")
    assert excinfo.value.code == "INSPECT_RESOURCE_UNKNOWN"


def test_lineage_unknown_object_raises(loaded_catalog) -> None:
    """@brief 溯源查询要求对象已在目录中。"""
    with pytest.raises(CatalogError) as excinfo:
        lineage(loaded_catalog, "ghost")
    assert excinfo.value.code == "INSPECT_OBJECT_UNKNOWN"
