"""素材可达性准入测试：分区键齐备与分区地点可行两条规则的正反例。

反例按 tests/catalog/test_category_admission.py 的既有形状驱动（复制真实语料的
被检子集再定向破坏），断言对应稳定错误码出现在违例列表中（fail-closed）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from worldmock.catalog.admission import (
    CATEGORY_PARTITION_KEY_MISSING,
    CATEGORY_PARTITION_LOCATION_INFEASIBLE,
    validate_world_categories,
)
from worldmock.catalog.loader import load_catalog

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "corpora"
#: 可达性校验读取的语料文件闭集（复制进临时树的最小集合）。
CHECKED_FILES = [
    "meals/items.json",
    "zhangjiang-map/locations.json",
    "scene-stock/home-pantry.json",
    "scenario-contacts/contacts.json",
    "scenario-work/materials.json",
    "lifestyle/items.json",
    "movies/items.json",
    "drama/items.json",
    "podcasts/items.json",
    "novels/items.json",
    "feed/items.json",
    "jokes/items.json",
    "sintel/media-identity.json",
    "manifest.json",
]


@pytest.fixture(scope="module")
def real_catalog():
    """@brief 真实冻结目录视图。"""
    return load_catalog(ROOT / "catalog")


def _copy_corpora(tmp_path: Path) -> Path:
    """@brief 复制被检语料子集到临时树，返回临时语料根。"""
    root = tmp_path / "corpora"
    for relative in CHECKED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CORPORA / relative, target)
    return root


def _mutate_json(root: Path, relative: str, mutate) -> None:
    """@brief 读取-修改-写回一个语料 JSON 文件。"""
    path = root / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes(result) -> set[str]:
    return {violation.code for violation in result.violations}


def test_real_corpora_satisfy_reachability(real_catalog):
    """真实目录 + 真实语料：可达性两条规则全绿。"""
    result = validate_world_categories(real_catalog, CORPORA)
    assert result.ok, [f"{v.code}: {v.detail}" for v in result.violations]


@pytest.mark.parametrize("field", ["genre", "language"])
def test_content_item_missing_partition_dimension_is_unreachable(real_catalog, tmp_path, field):
    """条目的检索分区键任一缺省即不可达并阻断启动。"""
    root = _copy_corpora(tmp_path)
    _mutate_json(root, "movies/items.json",
                 lambda document: document[0].__setitem__(field, [] if field == "genre" else ""))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def test_content_item_missing_duration_dimension_is_unreachable(real_catalog, tmp_path):
    """时长段由 duration_seconds 落格，缺失即落不进任何分区。"""
    root = _copy_corpora(tmp_path)

    def drop_duration(document):
        document[0].pop("duration_seconds", None)
        document[0].pop("estimated_read_seconds", None)

    _mutate_json(root, "novels/items.json", drop_duration)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def test_read_corpus_duration_falls_back_to_read_seconds(real_catalog, tmp_path):
    """阅读类语料以 estimated_read_seconds 落格，不算分区键缺省。"""
    root = _copy_corpora(tmp_path)

    def drop_read_seconds(document):
        document[0].pop("estimated_read_seconds", None)

    _mutate_json(root, "jokes/items.json", drop_read_seconds)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_KEY_MISSING in _codes(result)


def _declare(document: dict, entry_key: str, kinds: list[str]) -> None:
    """@brief 把一份语料文档里所有条目的地点类声明整体改写为给定取值（列表文档直接传 None）。"""
    entries = document if entry_key is None else document[entry_key]
    for entry in entries:
        entry["location_kinds"] = list(kinds)


def test_partition_with_no_feasible_place_is_unreachable(real_catalog, tmp_path):
    """分区内没有任何条目的地点类与场景地点相交时，该分区恒不可行。

    条目侧声明取自内容语料条目（与 `_check_content` 同一池）；地点侧声明取自
    地点语料。两条声明今天都不存在（内容品类的地点类声明尚未补齐），因此本
    用例显式声明两侧以驱动该分支。
    """
    root = _copy_corpora(tmp_path)
    _mutate_json(root, "zhangjiang-map/locations.json",
                 lambda document: _declare(document, "locations", ["venue"]))
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["home"]))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE in _codes(result)


def test_location_covering_the_partition_keeps_it_feasible(real_catalog, tmp_path):
    """只要有一个地点覆盖该分区所需的地点类，分区即判可行。"""
    root = _copy_corpora(tmp_path)

    def declare_home_only(document):
        _declare(document, "locations", ["venue"])
        document["locations"][0]["location_kinds"] = ["home"]

    _mutate_json(root, "zhangjiang-map/locations.json", declare_home_only)
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["home"]))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE not in _codes(result)


def test_anywhere_partition_is_always_feasible(real_catalog, tmp_path):
    """声明 anywhere 的条目在任意地点恒可行，分区不被判不可行。"""
    root = _copy_corpora(tmp_path)
    _mutate_json(root, "zhangjiang-map/locations.json",
                 lambda document: _declare(document, "locations", ["venue"]))
    _mutate_json(root, "movies/items.json",
                 lambda document: _declare(document, None, ["anywhere"]))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_PARTITION_LOCATION_INFEASIBLE not in _codes(result)


def test_market_location_is_covered_by_place_projection(real_catalog):
    """承载全部商品的 `market` 地点在场景地点集内（不得游离在校验之外）。"""
    from worldmock.life.retrieval import place_kinds

    places = place_kinds(CORPORA)
    assert "market" in places
    # market 未声明地点类时按恒可行处理：它至少不构成任何分区的可行性缺口。
    result = validate_world_categories(real_catalog, CORPORA)
    assert result.ok
