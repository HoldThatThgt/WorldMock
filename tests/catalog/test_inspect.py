"""@brief 编译边影响面与 raw 对象来源查询测试。"""

from __future__ import annotations

import pytest

from worldmock.catalog import CatalogError
from worldmock.catalog.inspect import edges_of, lineage, objects_affecting


def test_edges_of_includes_purchase_rule_and_energy_edges(loaded_catalog) -> None:
    """购买与食品能量边分别归入明确资源域。"""
    money = edges_of(loaded_catalog, "money_fen")
    energy = edges_of(loaded_catalog, "energy_kcal")
    assert [(hit.object_id, hit.relation) for hit in money] == [("milk", "rule")]
    assert [(hit.object_id, hit.relation) for hit in energy] == [("milk", "source")]


def test_objects_affecting_is_sorted_and_unique(loaded_catalog) -> None:
    """资源影响对象身份返回稳定排序去重结果。"""
    assert objects_affecting(loaded_catalog, "money_fen") == ("milk",)


def test_lineage_reports_new_source_identity_and_producer(loaded_catalog) -> None:
    """来源查询保留 source_id、生产者与对象版本。"""
    report = lineage(loaded_catalog, "milk")
    assert report.object_version == 1
    assert report.entries[0].source_id == "object-values"
    assert report.entries[0].producer == "human"
    assert report.entries[0].paths == ("/values",)


@pytest.mark.parametrize("function", [edges_of, objects_affecting])
def test_unknown_resource_rejected(loaded_catalog, function) -> None:
    """资源影响查询要求资源已经注册。"""
    with pytest.raises(CatalogError) as excinfo:
        function(loaded_catalog, "unknown")
    assert excinfo.value.code == "INSPECT_RESOURCE_UNKNOWN"


def test_unknown_object_lineage_rejected(loaded_catalog) -> None:
    """来源查询要求 raw 对象存在。"""
    with pytest.raises(CatalogError) as excinfo:
        lineage(loaded_catalog, "unknown")
    assert excinfo.value.code == "INSPECT_OBJECT_UNKNOWN"
