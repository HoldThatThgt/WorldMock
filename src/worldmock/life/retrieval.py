"""@brief 确定性检索：条目属性倒排索引、facet 计数与静态可达性判定。

本模块是「检索：确定性索引与 facet」（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则）与「素材可达性」
（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像）的纯函数实现，被准入校验与静态检查脚本共用。三条
硬约束决定了它的形状：

- **索引必须确定性**：倒排索引（标签 → 对象标识）与 facet 计数都是纯文本、可 diff、
  可进溯源的结构。本模块只用 `dict[str, tuple[str, ...]]`、`tuple` 与 `int`，不含
  浮点距离、不含随机初始化、不含进程内缓存，因此同一输入逐字节同输出。
- **禁止向量检索与近似最近邻**：近似最近邻的结果不确定、聚类初始化带随机、浮点距离
  比较不稳定，三条各自破坏「同输入逐字节相同输出」。稀疏词项检索可用，因为它可枚举、
  可 diff。
- **分区键取条目属性而非地点**：地点是可行性门，条目属性是检索分区键，两件事必须
  分开。地点类只参与可行性判定（交集非空），不参与分区，分区数因此不被地点取值数封顶。

**反塌缩，不是覆盖率**：`reachable` 只回答「该条目是否至少属于一个可检索分区、且该
分区在场景里存在非空的地点可行组合」。它不要求任何条目被呈现过，也不承诺它在任何
有限运行窗口内被呈现——它只保证「从未可达」这个状态本身可被判假，且判定不依赖任何
运行期互动统计量（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「素材可达性」）。

**触发条件**：单一品类池规模超过场景常量 `retrieval_index_threshold` 时才建立倒排与
facet；未超过时按品类下推。两条路径的查询入口与分区键完全相同，`INDEXED` 只记录本次
走的是哪条路径，因此池变大时不必推倒重来（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「检索」）。

模块的 docstring 用中文，代码、日志与抛错用英文。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Literal

#: 检索分区的三个维度名（闭集，全流程同名同义）。
FacetName = Literal["genre", "language", "duration_bucket"]

#: 分区维度的枚举形态，顺序即分区键的字段顺序（确定性的一部分）。
FACET_NAMES: tuple[FacetName, ...] = ("genre", "language", "duration_bucket")

#: 地点类取值取自生活活动地点类的单一封闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「地点类词表」）。
#: 引擎包 `worldmock.data` 的 `affordances.toml` 定义闭集，环境素材仅声明其中的地点类。
LOCATION_KINDS: tuple[str, ...] = ("home", "office", "outdoor", "fitness", "venue", "anywhere")

#: 恒可行的地点类：声明它表示该条目在任意地点均可受理。
ANYWHERE_KIND = "anywhere"

#: 时长段的落格边界（秒，前开后闭的左闭区间）：[0, 3600)、[3600, 7200)、[7200, +∞)。
#: 取整数值而非浮点，避免任何浮点比较参与分区判定。
DURATION_BUCKET_BOUNDS: tuple[int, ...] = (3600, 7200)

#: 单一品类池超过该规模即启用倒排与 facet（docs/CONTRACTS.md#contracts-worldmock-接口合同「场景常量」的缺省值）。
DEFAULT_RETRIEVAL_INDEX_THRESHOLD = 1000

#: 检索路径的两种取值：索引命中与线性扫描（同入口、同结果，只用于留痕）。
RetrievalPath = Literal["INDEXED", "SCANNED"]

#: 生活活动语料的相对路径。
LIFESTYLE_RELATIVE = "lifestyle/items.json"


@dataclass(frozen=True, slots=True)
class RetrievalEntry:
    """@brief 一个可检索条目的分区侧事实：分区键、地点类与可 diff 的标签。"""

    object_id: str
    source: str
    genre: tuple[str, ...] = ()
    language: str = ""
    duration_seconds: int | None = None
    location_kinds: frozenset[str] = frozenset()

    @property
    def partition(self) -> tuple[str, str, str] | None:
        """@brief 该条目的完整检索分区键；任一维度缺省时返回 None（落不进任何分区）。"""
        missing = self.missing_dimensions
        if missing:
            return None
        return (self.genre[0], self.language, duration_bucket(self.duration_seconds))

    @property
    def missing_dimensions(self) -> tuple[str, ...]:
        """@brief 缺省的分区维度名（空表示分区键齐备），逐维度给出证据。"""
        missing = []
        if not self.genre:
            missing.append("genre")
        if not self.language:
            missing.append("language")
        if duration_bucket(self.duration_seconds) == "":
            missing.append("duration_bucket")
        return tuple(missing)

    def facet_values(self, name: FacetName) -> tuple[str, ...]:
        """@brief 该条目在某维度上的取值；`genre` 是多值维度，其余单值。"""
        if name == "genre":
            return self.genre
        if name == "language":
            return (self.language,) if self.language else ()
        bucket = duration_bucket(self.duration_seconds)
        return (bucket,) if bucket else ()

    def as_row(self) -> dict[str, Any]:
        """@brief 纯文本可枚举的行形态（进报告与溯源，不含任何浮点距离）。"""
        return {
            "object_id": self.object_id,
            "source": self.source,
            "partition": list(self.partition) if self.partition is not None else None,
            "missing_dimensions": list(self.missing_dimensions),
            "location_kinds": sorted(self.location_kinds),
        }


@dataclass(frozen=True, slots=True)
class RetrievalIndex:
    """@brief 倒排索引与 facet 计数：标签 → 有序对象标识，全部为纯文本结构。"""

    threshold: int
    #: 维度 -> 取值 -> 该取值下的有序对象标识。
    postings: dict[str, dict[str, tuple[str, ...]]]
    #: 维度 -> 取值 -> 该取值下的条目数（facet 计数的基线）。
    facet_counts: dict[str, dict[str, int]]
    size: int
    path: RetrievalPath


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """@brief 一次检索的全部过滤条件；空元组表示该维度不设限。"""

    genre: tuple[str, ...] = ()
    language: tuple[str, ...] = ()
    duration_bucket: tuple[str, ...] = ()
    location_kinds: frozenset[str] = frozenset()


def duration_bucket(duration_seconds: int | None) -> str:
    """@brief 把时长落格为时长段的闭集取值；缺省或非正时长落不进任何格。

    落格用整数比较，不引入浮点，因此同一时长在任何进程里都落进同一格。
    """
    if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool):
        return ""
    if duration_seconds <= 0:
        return ""
    if duration_seconds < DURATION_BUCKET_BOUNDS[0]:
        return "under-1h"
    if duration_seconds < DURATION_BUCKET_BOUNDS[1]:
        return "1-2h"
    return "over-2h"


def genre_values(raw: Any) -> tuple[str, ...]:
    """@brief 规范题材取值：去空、去重、按字典序，保证分区键身份稳定。"""
    if not isinstance(raw, (list, tuple)):
        return ()
    seen = {str(item).strip() for item in raw if isinstance(item, str) and item.strip()}
    return tuple(sorted(seen))




def build_index(entries: Sequence[RetrievalEntry],
                threshold: int = DEFAULT_RETRIEVAL_INDEX_THRESHOLD) -> RetrievalIndex:
    """@brief 建立倒排索引与 facet 计数；池未达阈值时只标路径，结构仍可用。

    @param entries: 一个品类池的全部条目（调用方负责保证同一池同一品类）。
    @param threshold: 场景常量 `retrieval_index_threshold`，池规模严格超过它才走索引路径。
    @return: 纯文本的倒排结构；`path` 记录本次走的是索引还是线性扫描。
    """
    indexed = len(entries) > threshold
    postings: dict[str, dict[str, tuple[str, ...]]] = {name: {} for name in FACET_NAMES}
    counts: dict[str, dict[str, int]] = {name: {} for name in FACET_NAMES}
    if not indexed:
        return RetrievalIndex(threshold=threshold, postings=postings, facet_counts=counts,
                              size=len(entries), path="SCANNED")
    buckets: dict[str, dict[str, list[str]]] = {name: {} for name in FACET_NAMES}
    for entry in entries:
        for name in FACET_NAMES:
            for value in entry.facet_values(name):
                buckets[name].setdefault(value, []).append(entry.object_id)
                counts[name][value] = counts[name].get(value, 0) + 1
    for name in FACET_NAMES:
        postings[name] = {value: tuple(sorted(ids)) for value, ids in buckets[name].items()}
    return RetrievalIndex(threshold=threshold, postings=postings, facet_counts=counts,
                          size=len(entries), path="INDEXED")


def _matches(entry: RetrievalEntry, query: RetrievalQuery) -> bool:
    """@brief 条目是否满足全部维度过滤（单值维度取交集，地点类取交集）。"""
    if query.genre and not set(query.genre) & set(entry.genre):
        return False
    if query.language and entry.language not in query.language:
        return False
    if query.duration_bucket and duration_bucket(entry.duration_seconds) not in query.duration_bucket:
        return False
    if query.location_kinds and not feasible_kinds(query.location_kinds, entry.location_kinds):
        return False
    return True


@lru_cache(maxsize=8)
def place_kinds(corpora_root: Any, relative: str
                ) -> Mapping[str, frozenset[str]]:
    """@brief 读取显式地点活动环境；缺文件、缺声明和活动侧通配符均拒绝。"""
    path = corpora_root / relative
    if not path.is_file():
        logging.error("Location corpus is missing: %s", relative)
        raise ValueError("location corpus is missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    places: dict[str, frozenset[str]] = {}
    for location in document["locations"]:
        if not isinstance(location, dict):
            logging.error("Location activity kinds declaration is invalid")
            raise ValueError("location activity kinds declaration is invalid")
        location_id = location.get("location_id")
        declared = location.get("location_kinds")
        if (not isinstance(location_id, str) or not location_id.strip() or location_id in places
                or not isinstance(declared, list) or not declared
                or not all(isinstance(kind, str) and kind in LOCATION_KINDS for kind in declared)
                or ANYWHERE_KIND in declared or len(set(declared)) != len(declared)):
            logging.error("Location activity kinds declaration is invalid")
            raise ValueError("location activity kinds declaration is invalid")
        places[location_id] = frozenset(declared)
    return MappingProxyType(places)


@lru_cache(maxsize=8)
def item_kinds(corpora_root: Any, relative: str = LIFESTYLE_RELATIVE
               ) -> Mapping[str, frozenset[str]]:
    """@brief 生活活动语料的地点类声明表：活动标识 -> 该条目需要的地点类集合。

    条目侧的声明是「我需要哪些地点类」，与地点侧的「我满足哪些地点类」同名同义
    （docs/CONTRACTS.md#contracts-worldmock-接口合同「地点类词表」）。条目声明含 `anywhere` 表示恒可行。

    :param corpora_root: 语料根目录。
    :param relative: 生活活动语料的相对路径。
    :return: 活动标识到所需地点类集合的只读映射；未声明条目不在表内。
    """
    path = corpora_root / relative
    if not path.is_file():
        return MappingProxyType({})
    document = json.loads(path.read_text(encoding="utf-8"))
    kinds: dict[str, frozenset[str]] = {}
    for item in document.get("items", []):
        if not isinstance(item, dict):
            continue
        declared = item.get("location_kinds")
        if isinstance(declared, list) and declared:
            activity_id = str(item.get("activity_id") or "")
            if activity_id:
                kinds[activity_id] = frozenset(str(kind) for kind in declared)
    return MappingProxyType(kinds)


def indexed_ids(index: RetrievalIndex, query: RetrievalQuery) -> frozenset[str] | None:
    """@brief 倒排下推得到的候选标识集；无维度可下推或索引未启用时返回 None。

    下推只做集合收窄，不改变语义：命中集是 `_matches` 的超集，最终仍由
    `_matches` 逐条判定，因此索引路径与扫描路径结果逐条相同。
    """
    if index.path != "INDEXED" or not index.postings:
        return None
    narrowed: frozenset[str] | None = None
    conditions: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("genre", query.genre), ("language", query.language),
        ("duration_bucket", query.duration_bucket))
    for name, values in conditions:
        if not values:
            continue
        hits: set[str] = set()
        for value in values:
            hits.update(index.postings.get(name, {}).get(value, ()))
        narrowed = frozenset(hits) if narrowed is None else narrowed & hits
    return narrowed


def search(entries: Sequence[RetrievalEntry], query: RetrievalQuery,
           index: RetrievalIndex | None = None) -> tuple[RetrievalEntry, ...]:
    """@brief 按过滤条件返回有序结果；索引在场时走倒排下推，否则线性扫描。

    两条路径结果逐条相同（索引只是加速结构，不是第二套语义）。返回顺序恒按
    对象标识字典序，因此同一查询在同一冻结目录下返回同一有序结果。
    """
    candidates = indexed_ids(index, query) if index is not None else None
    selected = [entry for entry in entries
                if (candidates is None or entry.object_id in candidates)
                and _matches(entry, query)]
    return tuple(sorted(selected, key=lambda entry: entry.object_id))


def facet_counts(entries: Sequence[RetrievalEntry], query: RetrievalQuery
                 ) -> dict[str, dict[str, int]]:
    """@brief 给定当前过滤条件，返回各维度可选值与计数（facet 计数）。

    @param entries: 待统计的条目池。
    @param query: 当前过滤条件；`location_kinds` 作为可行性门参与收窄。
    @return: 维度 -> 取值 -> 计数，取值按字典序（纯文本、可 diff）。
    """
    counts: dict[str, dict[str, int]] = {name: {} for name in FACET_NAMES}
    for entry in entries:
        if not _matches(entry, query):
            continue
        for name in FACET_NAMES:
            for value in entry.facet_values(name):
                counts[name][value] = counts[name].get(value, 0) + 1
    return {name: dict(sorted(values.items())) for name, values in counts.items()}


def feasible_kinds(place_kinds: Iterable[str], item_kinds: Iterable[str]) -> bool:
    """@brief 只有活动侧的 anywhere 表示无地点限制，地点必须有显式环境。"""
    item = {str(kind) for kind in item_kinds}
    place = {str(kind) for kind in place_kinds}
    if not place or ANYWHERE_KIND in place:
        return False
    return not item or ANYWHERE_KIND in item or bool(item & place)


def _partition_rows(entries: Sequence[RetrievalEntry]) -> dict[tuple[str, str, str],
                                                               list[RetrievalEntry]]:
    """@brief 按完整分区键归组条目；分区键缺省的条目不进任何分区。"""
    groups: dict[tuple[str, str, str], list[RetrievalEntry]] = {}
    for entry in entries:
        partition = entry.partition
        if partition is None:
            continue
        groups.setdefault(partition, []).append(entry)
    return groups


def reachable(entry: RetrievalEntry, place_kinds: Iterable[frozenset[str]]) -> bool:
    """@brief 该条目是否至少属于一个可检索分区，且该分区在场景里可行。

    @param entry: 待判定条目。
    @param place_kinds: 场景全部地点的地点类集合。
    @return: 分区键齐备、且存在一个地点与之交集非空时为 True。
    """
    if entry.partition is None:
        return False
    return any(feasible_kinds(place, entry.location_kinds) for place in place_kinds)


def reachability_report(entries: Sequence[RetrievalEntry],
                        place_kinds: Iterable[frozenset[str]],
                        threshold: int = DEFAULT_RETRIEVAL_INDEX_THRESHOLD) -> dict[str, Any]:
    """@brief 逐条目与逐分区给出可达性结论与证据（准入报告与静态检查共用）。

    这是**反塌缩**要求，不是覆盖率要求：报告只回答「从未可达」能否被判假，
    不报告任何条目是否已被呈现过，也不使用任何运行期互动统计量。

    @param entries: 待判定的条目池（通常是该场景可引用的全部内容条目）。
    @param place_kinds: 场景全部地点的地点类集合。
    @param threshold: 场景常量 `retrieval_index_threshold`（写进报告留痕）。
    @return: 含逐条目结论、逐分区结论与不可达清单的报告字典（纯文本、可 diff）。
    """
    places = tuple(frozenset(kinds) for kinds in place_kinds)
    indexed = build_index(entries, threshold)
    rows = []
    unreachable = []
    for entry in sorted(entries, key=lambda item: item.object_id):
        ok = reachable(entry, places)
        row = entry.as_row()
        row["reachable"] = ok
        rows.append(row)
        if not ok:
            unreachable.append(row)
    partitions = []
    for partition, members in sorted(_partition_rows(entries).items()):
        feasible = any(feasible_kinds(place, member.location_kinds)
                       for member in members for place in places)
        partitions.append({"partition": list(partition), "size": len(members),
                           "feasible": feasible})
    return {
        "index_path": indexed.path,
        "index_threshold": threshold,
        "index_size": indexed.size,
        "facet_counts": facet_counts(entries, RetrievalQuery()),
        "entry_count": len(entries),
        "partition_count": len(partitions),
        "partitions": partitions,
        "entries": rows,
        "unreachable": unreachable,
    }
