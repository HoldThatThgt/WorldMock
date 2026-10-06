"""素材可达性准入测试：分区键齐备与分区地点可行两条规则的正反例。

反例按 tests/catalog/test_category_admission.py 的既有形状驱动（生成有限人工样本再定向破坏），断言对应稳定错误码出现在违例列表中（fail-closed）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldmock.catalog.admission import (
    CATEGORY_PARTITION_KEY_MISSING,
    CATEGORY_PARTITION_LOCATION_INFEASIBLE,
    validate_world_categories,
)
from worldmock.catalog.loader import load_catalog

from tests.catalog.admission_fixtures import (
    LOCATIONS_PATH,
    write_category_catalog,
    write_category_corpora,
)


@pytest.fixture(scope="module")
def artificial_catalog_dir(tmp_path_factory):
    """@brief 生成只含五个食品对象的人工目录。"""
    root = tmp_path_factory.mktemp("category-admission")
    return write_category_catalog(root / "catalog")


@pytest.fixture(scope="module")
def artificial_catalog(artificial_catalog_dir):
    """@brief 用真实加载器编译人工目录。"""
    return load_catalog(artificial_catalog_dir)


def _write_corpora(tmp_path: Path) -> Path:
    """@brief 即时生成被检人工语料，不复制正式环境。"""
    return write_category_corpora(tmp_path / "corpora")


def _mutate_json(root: Path, relative: str, mutate) -> None:
    """@brief 读取-修改-写回一个语料 JSON 文件。"""
    path = root / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes(result) -> set[str]:
    return {violation.code for violation in result.violations}


def test_artificial_corpora_satisfy_reachability(artificial_catalog, tmp_path):
    """有限人工条目同时通过分区键和地点可行性准入。"""
    result = validate_world_categories(artificial_catalog, _write_corpora(tmp_path), LOCATIONS_PATH)
    assert result.ok, [f"{v.code}: {v.detail}" for v in result.violations]


@pytest.mark.parametrize("field", ["genre", "language"])
def test_content_item_missing_partition_dimension_is_unreachable(artificial_catalog, tmp_path, field):
    """条目的检索分区键任一缺省即不可达并阻断启动。"""
    root = _write_corpora(tmp_path)
    _mutate_json(root, "movies/items.json",
                 lambda document: document[0].__setitem__(field, [] if field == "genre" else ""))
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def test_content_item_missing_duration_dimension_is_unreachable(artificial_catalog, tmp_path):
    """时长段由 duration_seconds 落格，缺失即落不进任何分区。"""
    root = _write_corpora(tmp_path)

    def drop_duration(document):
        document[0].pop("duration_seconds", None)
        document[0].pop("estimated_read_seconds", None)

    _mutate_json(root, "novels/items.json", drop_duration)
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def test_read_corpus_duration_falls_back_to_read_seconds(artificial_catalog, tmp_path):
    """阅读类语料以 estimated_read_seconds 落格，不算分区键缺省。"""
    root = _write_corpora(tmp_path)

    def drop_read_seconds(document):
        document[0].pop("estimated_read_seconds", None)

    _mutate_json(root, "jokes/items.json", drop_read_seconds)
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def _declare(document: dict, entry_key: str, kinds: list[str]) -> None:
    """@brief 把一份语料文档里所有条目的地点类声明整体改写为给定取值（列表文档直接传 None）。"""
    entries = document if entry_key is None else document[entry_key]
    for entry in entries:
        entry["location_kinds"] = list(kinds)


def test_partition_with_no_feasible_place_is_unreachable(artificial_catalog, tmp_path):
    """分区内没有任何条目的地点类与场景地点相交时，该分区恒不可行。

    人工条目使用独立分区；把全部地点改为 venue、该分区改为 home，
    保证失败来自地点集合无交集。
    """
    root = _write_corpora(tmp_path)
    _mutate_json(root, LOCATIONS_PATH,
                 lambda document: _declare(document, "locations", ["venue"]))
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["home"]))
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE in _codes(result)


def test_location_covering_the_partition_keeps_it_feasible(artificial_catalog, tmp_path):
    """只要有一个地点覆盖该分区所需的地点类，分区即判可行。"""
    root = _write_corpora(tmp_path)

    def declare_home_only(document):
        _declare(document, "locations", ["venue"])
        document["locations"][0]["location_kinds"] = ["home"]

    _mutate_json(root, LOCATIONS_PATH, declare_home_only)
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["home"]))
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE not in _codes(result)


def test_anywhere_partition_is_always_feasible(artificial_catalog, tmp_path):
    """声明 anywhere 的条目在任意地点恒可行，分区不被判不可行。"""
    root = _write_corpora(tmp_path)
    _mutate_json(root, LOCATIONS_PATH,
                 lambda document: _declare(document, "locations", ["venue"]))
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["anywhere"]))
    result = validate_world_categories(artificial_catalog, root, LOCATIONS_PATH)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE not in _codes(result)


def test_market_location_is_covered_by_place_projection(artificial_catalog, tmp_path):
    """承载全部商品的 `market` 地点在场景地点集内（不得游离在校验之外）。"""
    from worldmock.life.retrieval import place_kinds

    root = _write_corpora(tmp_path)
    places = place_kinds(root, LOCATIONS_PATH)
    assert "market" in places
    assert places["market"] == frozenset({"venue"})
    result = validate_world_categories(artificial_catalog, _write_corpora(tmp_path), LOCATIONS_PATH)
    assert result.ok
