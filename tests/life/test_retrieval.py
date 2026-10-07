"""确定性检索与素材可达性的行为测试（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「检索」、
docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「素材可达性」）。

覆盖三组性质：分区键与 facet 计数的确定性（纯文本、可 diff、无浮点距离）、
索引路径与扫描路径结果逐条相同、可达性判定只回答「从未可达」能否被判假。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from worldmock.life.retrieval import (
    FACET_NAMES,
    RetrievalEntry,
    RetrievalQuery,
    build_index,
    duration_bucket,
    facet_counts,
    feasible_kinds,
    genre_values,
    indexed_ids,
    item_kinds,
    place_kinds,
    reachability_report,
    reachable,
    search,
)

def _entry(object_id: str, genre: tuple[str, ...] = ("剧情",), language: str = "zh",
           duration: int | None = 600, kinds: frozenset[str] = frozenset()) -> RetrievalEntry:
    """@brief 构造一个分区键齐备的检索条目（测试夹具）。"""
    return RetrievalEntry(object_id=object_id, source="fixture", genre=genre,
                          language=language, duration_seconds=duration, location_kinds=kinds)


def test_duration_bucket_returns_closed_set_values():
    """时长段取闭集三值，边界按左闭右开落格。"""
    assert duration_bucket(1) == "under-1h"
    assert duration_bucket(3599) == "under-1h"
    assert duration_bucket(3600) == "1-2h"
    assert duration_bucket(7199) == "1-2h"
    assert duration_bucket(7200) == "over-2h"


@pytest.mark.parametrize("value", [None, 0, -1, True, "600"])
def test_duration_bucket_rejects_missing_or_non_positive(value):
    """缺省、非正时长与非整数输入落不进任何格（判定为不可达，而不是默认落格）。"""
    assert duration_bucket(value) == ""


def test_genre_values_normalises_order_and_duplicates():
    """题材取值去空、去重并按字典序，保证分区键身份稳定。"""
    assert genre_values(["b", "a", "b", " ", ""]) == ("a", "b")
    assert genre_values("剧情") == ()
    assert genre_values(None) == ()




def test_entry_partition_reports_missing_dimensions():
    """任一分区键缺省的条目落不进任何分区，并逐维度给出证据。"""
    assert _entry("a").partition == ("剧情", "zh", "under-1h")
    missing_genre = _entry("b", genre=())
    missing_language = _entry("c", language="")
    missing_duration = _entry("d", duration=None)
    assert missing_genre.partition is None
    assert missing_genre.missing_dimensions == ("genre",)
    assert missing_language.missing_dimensions == ("language",)
    assert missing_duration.missing_dimensions == ("duration_bucket",)
    assert _entry("e", genre=(), language="", duration=None).missing_dimensions == (
        "genre", "language", "duration_bucket")


def test_build_index_path_follows_threshold():
    """池未达阈值走扫描路径，严格超过阈值才建倒排；两条路径入口与分区键相同。"""
    small = tuple(_entry(f"movie-{index:04d}") for index in range(10))
    large = tuple(_entry(f"movie-{index:04d}") for index in range(11))
    assert build_index(small, threshold=10).path == "SCANNED"
    assert build_index(large, threshold=10).path == "INDEXED"


def test_build_index_postings_are_sorted_plain_text():
    """倒排结构是纯文本：维度到取值的映射，取值到有序对象标识元组。"""
    entries = tuple(_entry(f"movie-{index:04d}", genre=("剧情",) if index % 2 else ("历史",))
                    for index in range(12))
    index = build_index(entries, threshold=10)
    assert set(index.postings) == set(FACET_NAMES)
    assert index.postings["genre"]["剧情"] == ("movie-0001", "movie-0003", "movie-0005",
                                               "movie-0007", "movie-0009", "movie-0011")
    assert index.facet_counts["language"] == {"zh": 12}
    assert index.path == "INDEXED"


def test_index_holds_no_float_distance_field():
    """禁止近似最近邻：索引结构里不得出现浮点距离字段（可枚举、可 diff）。"""
    entries = tuple(_entry(f"movie-{index:04d}") for index in range(12))
    index = build_index(entries, threshold=10)
    payload = {"postings": index.postings, "facet_counts": index.facet_counts}
    assert index.path == "INDEXED"

    def walk(node):
        if isinstance(node, dict):
            return all(walk(value) for value in node.values())
        if isinstance(node, (list, tuple)):
            return all(walk(value) for value in node)
        return isinstance(node, (str, int, frozenset))

    assert walk(payload), "index structures must stay plain text and integers"


def test_search_filters_each_dimension_independently():
    """每个维度各自收窄：题材、语言、时长段、地点类任一不匹配即落选。"""
    entries = (_entry("keep", genre=("剧情",), language="zh", duration=600,
                      kinds=frozenset({"home"})),
               _entry("genre", genre=("历史",), language="zh", duration=600,
                      kinds=frozenset({"home"})),
               _entry("language", genre=("剧情",), language="cs", duration=600,
                      kinds=frozenset({"home"})),
               _entry("duration", genre=("剧情",), language="zh", duration=9000,
                      kinds=frozenset({"home"})),
               _entry("place", genre=("剧情",), language="zh", duration=600,
                      kinds=frozenset({"outdoor"})))
    query = RetrievalQuery(genre=("剧情",), language=("zh",), duration_bucket=("under-1h",),
                           location_kinds=frozenset({"home"}))
    assert [item.object_id for item in search(entries, query)] == ["keep"]


def test_place_kinds_rejects_malformed_entries(tmp_path):
    """地点语料里的非映射条目与无标识条目必须明确拒绝。"""
    path = tmp_path / "zhangjiang-map" / "locations.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"locations": [
        "not-a-mapping",
        {"name": "no id"},
        {"location_id": "home", "location_kinds": ["home"]},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="activity kinds declaration"):
        place_kinds(tmp_path, "zhangjiang-map/locations.json")


def test_item_kinds_skips_malformed_entries(tmp_path):
    """生活活动语料里的非映射条目被跳过，不产生空键。"""
    path = tmp_path / "lifestyle"
    path.mkdir(parents=True)
    (path / "items.json").write_text(json.dumps({"items": [
        "not-a-mapping",
        {"activity_id": "jogging", "location_kinds": ["outdoor"]},
    ]}), encoding="utf-8")
    assert dict(item_kinds(tmp_path)) == {"jogging": frozenset({"outdoor"})}


def test_search_returns_same_result_with_and_without_index():
    """索引只是加速结构：同一查询在索引路径与扫描路径下返回同一有序结果。"""
    entries = tuple(_entry(f"movie-{index:04d}",
                           genre=("剧情", "历史") if index % 3 == 0 else ("剧情",),
                           language="zh" if index % 2 == 0 else "cs")
                    for index in range(30))
    query = RetrievalQuery(genre=("剧情",), language=("zh",), duration_bucket=("under-1h",))
    scanned = search(entries, query)
    indexed = search(entries, query, build_index(entries, threshold=5))
    assert [item.object_id for item in scanned] == [item.object_id for item in indexed]
    assert [item.object_id for item in scanned] == sorted(item.object_id for item in scanned)


def test_search_is_repeatable_and_cursor_free():
    """同一（条目集, 查询）重复检索返回逐字节同一结果，不依赖进程内缓存。"""
    entries = tuple(_entry(f"movie-{index:04d}") for index in range(14))
    query = RetrievalQuery(genre=("剧情",))
    first = search(entries, query, build_index(entries, threshold=5))
    second = search(entries, query, build_index(entries, threshold=5))
    assert [item.as_row() for item in first] == [item.as_row() for item in second]


def test_indexed_ids_is_none_when_scanned_and_narrows_when_indexed():
    """未启用的索引不参与下推；启用后下推只做集合收窄。"""
    entries = tuple(_entry(f"movie-{index:04d}",
                           genre=("剧情",) if index % 2 == 0 else ("历史",))
                    for index in range(12))
    scanned = build_index(entries, threshold=100)
    assert indexed_ids(scanned, RetrievalQuery(genre=("剧情",))) is None
    narrowed = indexed_ids(build_index(entries, threshold=5), RetrievalQuery(genre=("剧情",)))
    assert narrowed == frozenset(f"movie-{index:04d}" for index in range(0, 12, 2))


def test_facet_counts_reflect_current_filters():
    """facet 计数按当前过滤条件给出各维度可选值与计数，取值有序。"""
    entries = (_entry("a", genre=("剧情",), language="zh", duration=600),
               _entry("b", genre=("剧情",), language="cs", duration=4000),
               _entry("c", genre=("历史",), language="zh", duration=4000))
    counts = facet_counts(entries, RetrievalQuery())
    assert counts["language"] == {"cs": 1, "zh": 2}
    assert counts["duration_bucket"] == {"1-2h": 2, "under-1h": 1}
    assert counts["genre"] == {"剧情": 2, "历史": 1}
    narrowed = facet_counts(entries, RetrievalQuery(language=("zh",)))
    assert narrowed["genre"] == {"剧情": 1, "历史": 1}
    assert narrowed["duration_bucket"] == {"1-2h": 1, "under-1h": 1}


def test_feasible_kinds_follows_anywhere_and_intersection():
    """地点类交集：活动侧 anywhere 恒可行；地点侧必须具有实际活动环境。"""
    assert feasible_kinds({"anywhere"}, {"venue"}) is False
    assert feasible_kinds({"home"}, {"anywhere"}) is True
    assert feasible_kinds({"home", "office"}, {"home"}) is True
    assert feasible_kinds({"office"}, {"home"}) is False
    assert feasible_kinds({"home"}, frozenset()) is True


def test_reachable_requires_partition_and_feasible_place():
    """可达性 = 分区键齐备 且 存在地点与之交集非空。"""
    places = (frozenset({"home"}), frozenset({"office"}))
    assert reachable(_entry("a", kinds=frozenset({"home"})), places) is True
    assert reachable(_entry("b", kinds=frozenset({"outdoor"})), places) is False
    assert reachable(_entry("c", genre=()), places) is False


def test_reachability_report_lists_unreachable_entries_and_partitions():
    """报告逐条目与逐分区给出结论；不可达条目与不可行分区都被列出。

    两条腿粒度不同：条目不可达 = 分区键缺省 或 该条目自身的地点类在场景里无处可行；
    分区不可行 = 分区内没有任何一条条目的地点类可行。因此下面 `c` 与 `d` 各自独占
    一个分区（分区被列为不可行），而 `b` 只是落不进任何分区。
    """
    entries = (_entry("a", kinds=frozenset({"home"})),
               _entry("b", genre=(), kinds=frozenset({"home"})),
               _entry("c", genre=("历史",), kinds=frozenset({"outdoor"})),
               _entry("d", genre=("奇幻",), kinds=frozenset({"venue"})))
    report = reachability_report(entries, (frozenset({"home"}), frozenset({"office"})),
                                 threshold=1000)
    assert {row["object_id"] for row in report["unreachable"]} == {"b", "c", "d"}
    assert report["entry_count"] == 4
    assert report["index_path"] == "SCANNED"
    infeasible = [row["partition"] for row in report["partitions"] if not row["feasible"]]
    assert infeasible == [["历史", "zh", "under-1h"], ["奇幻", "zh", "under-1h"]]


def test_partition_stays_feasible_when_one_member_is_feasible():
    """分区可行性是分区级判据：只要有一条条目可行，分区就不判不可行。"""
    entries = (_entry("a", kinds=frozenset({"home"})),
               _entry("c", kinds=frozenset({"outdoor"})))
    report = reachability_report(entries, (frozenset({"home"}),))
    assert all(row["feasible"] for row in report["partitions"])
    assert {row["object_id"] for row in report["unreachable"]} == {"c"}


def test_reachability_report_is_plain_text_and_bytes_stable():
    """报告是可 diff 的纯文本结构：同一输入重建逐字节相同。"""
    entries = (_entry("a", kinds=frozenset({"home"})), _entry("b"))
    places = (frozenset({"home"}),)
    first = reachability_report(entries, places)
    second = reachability_report(entries, places)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_reachability_does_not_promise_exposure():
    """反塌缩不是覆盖率：报告只含可达性结论，不含任何曝光/互动统计量。"""
    report = reachability_report((_entry("a"),), (frozenset({"anywhere"}),))
    assert set(report) == {"index_path", "index_threshold", "index_size", "facet_counts",
                           "entry_count", "partition_count", "partitions", "entries",
                           "unreachable"}
    assert set(report["entries"][0]) == {"object_id", "source", "partition",
                                         "missing_dimensions", "location_kinds", "reachable"}


def test_reachable_entry_row_is_serialisable():
    """逐条目行可直接进报告与溯源（分区键为列表或 None）。"""
    row = _entry("a", kinds=frozenset({"home"})).as_row()
    assert row["partition"] == ["剧情", "zh", "under-1h"]
    assert row["location_kinds"] == ["home"]
    assert json.dumps(row, ensure_ascii=False)


def test_place_kinds_rejects_undeclared_location_environment(tmp_path):
    """地点环境缺失或为空必须拒绝，不自动使用 anywhere。"""
    path = tmp_path / "zhangjiang-map" / "locations.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"locations": [
        {"location_id": "home", "location_kinds": ["home"]},
        {"location_id": "market"},
        {"location_id": "office", "location_kinds": []},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="activity kinds declaration"):
        place_kinds(tmp_path, "zhangjiang-map/locations.json")


def test_place_kinds_missing_file_is_rejected(tmp_path):
    """地点语料缺失必须拒绝。"""
    with pytest.raises(ValueError, match="location corpus is missing"):
        place_kinds(tmp_path, "zhangjiang-map/locations.json")


def test_item_kinds_reads_lifestyle_declarations(tmp_path):
    """生活活动条目侧声明读成活动标识到地点类集合的映射。"""
    path = tmp_path / "lifestyle"
    path.mkdir(parents=True)
    (path / "items.json").write_text(json.dumps({"items": [
        {"activity_id": "jogging", "location_kinds": ["outdoor", "fitness"]},
        {"activity_id": "gaming"},
    ]}), encoding="utf-8")
    kinds = item_kinds(tmp_path)
    assert kinds["jogging"] == frozenset({"outdoor", "fitness"})
    assert "gaming" not in kinds
    assert dict(item_kinds(tmp_path / "absent")) == {}


# ---------------------------------------------------------------------------
# 禁止向量检索与近似最近邻（常驻断言）
# ---------------------------------------------------------------------------

#: 近似最近邻与向量检索的实现库：出现在依赖表或调用点即违规。
_FORBIDDEN_VECTOR_PACKAGES = frozenset({
    "faiss", "faiss_cpu", "chromadb", "chroma", "annoy", "hnswlib", "nmslib",
    "milvus", "pymilvus", "qdrant_client", "weaviate", "pinecone", "pgvector", "pysparnn",
    "sentence_transformers", "open_clip", "towhee", "usearch", "docarray",
})
#: 依赖表里出现的词形（不在标识符里裸搜，避免与 `scanned` 这类领域词混淆）。
_FORBIDDEN_DEPENDENCY_WORDS = _FORBIDDEN_VECTOR_PACKAGES | {
    "scann", "sentence-transformers", "voyager", "clip-"}
#: 浮点距离函数的函数名：检索结构里出现即意味着比较口径不稳定。
_FORBIDDEN_DISTANCE_CALLS = frozenset({
    "cosine", "cosine_similarity", "cosine_distance", "euclidean", "euclidean_distance",
    "l2_distance", "inner_product", "dot_product", "norm", "sqrt", "exp", "log",
    "softmax", "topk", "top_k", "argpartition",
})


def _production_files() -> list[Path]:
    """@brief 两个生产源码根下的全部 Python 文件（稳定排序，排除缓存目录）。"""
    root = Path(__file__).resolve().parents[2]
    files: list[Path] = []
    for name in ("src/worldmock", "custom"):
        files.extend(path for path in sorted((root / name).rglob("*.py"))
                     if "__pycache__" not in path.parts)
    return files


def test_production_code_imports_no_vector_search_or_ann_library():
    """常驻断言：生产代码既不依赖也不导入任何向量检索/近似最近邻库。"""
    files = _production_files()
    assert len(files) > 50, "the production tree must be non-trivial for this gate to mean anything"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        roots = {name.split(".")[0].lower() for name in modules}
        assert not roots & _FORBIDDEN_VECTOR_PACKAGES, (path, roots)


def test_worldmock_dependencies_declare_no_vector_search_library():
    """常驻断言：本仓库自己的依赖声明里没有向量检索/近似最近邻库。

    判定范围是**本仓库声明的依赖**（根 `pyproject.toml` 与 LabelKit 的声明）。
    上游依赖的可选代码不属于本仓库依赖声明或实际检索路径。
    """
    root = Path(__file__).resolve().parents[2]
    declarations = [root / "pyproject.toml", root / "vendor" / "labelkit" / "pyproject.toml"]
    readable = [path for path in declarations if path.is_file()]
    assert readable, "at least the project dependency declaration must exist"
    for path in readable:
        text = path.read_text(encoding="utf-8").lower()
        found = {word for word in _FORBIDDEN_DEPENDENCY_WORDS if word in text}
        assert not found, (path, found)


def test_retrieval_structures_compare_no_float_distances():
    """常驻断言：检索链路上没有浮点距离比较，也没有随机初始化与近似近邻调用。"""
    root = Path(__file__).resolve().parents[2] / "src" / "worldmock" / "life"
    for name in ("retrieval", "candidates", "presentation", "rotation"):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        called |= {node.func.id for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not called & _FORBIDDEN_DISTANCE_CALLS, (name, called & _FORBIDDEN_DISTANCE_CALLS)
        imported = {(node.module or "").split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.Import) for alias in node.names}
        assert not imported & {"random", "numpy", "scipy", "sklearn", "torch", "math"}, name
    # 检索索引本身只由纯文本倒排与整数计数构成，逐字段核对一遍
    index = build_index(tuple(_entry(f"movie-{position:04d}") for position in range(12)),
                        threshold=10)
    assert index.path == "INDEXED"
    for dimension, postings in index.postings.items():
        assert dimension in FACET_NAMES
        for token, ids in postings.items():
            assert isinstance(token, str)
            assert isinstance(ids, tuple) and all(isinstance(item, str) for item in ids)
    assert all(isinstance(value, int) for counts in index.facet_counts.values()
               for value in counts.values())
