"""@brief 实际候选依世界事实准入，按相关度与稳定哈希排序。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from worldmock.catalog.affordances import engine_affordances
from worldmock.footprints.models import sha256_json
from worldmock.life.models import ActivityKind, DERIVED_DURATION_CAP_SECONDS
from worldmock.catalog.models import Location
from worldmock.exchange.types import PLACE_KIND_ENVIRONMENTS

#: 候选类别闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「候选对象与来源」）。
CandidateKind = Literal["product", "lifestyle", "task"]

#: 候选来源闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「候选对象与来源」）。
CandidateSource = Literal["on_site", "carried", "activated"]

#: 排除原因码闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「排除原因码」）。
ExclusionReason = Literal[
    "ELIGIBILITY_LOCATION_ABSENT",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",
    "ELIGIBILITY_RESOURCE_CONFLICT",
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",
    "ELIGIBILITY_DISABLED",
    "ROTATION_DEFERRED",
]

#: 候选类别闭集的可枚举形式（成员与顺序与 `CandidateKind` 逐字一致）。
CANDIDATE_KINDS: tuple[CandidateKind, ...] = ("product", "lifestyle", "task")
#: 候选来源闭集及生成合并顺序（成员与顺序与 `CandidateSource` 逐字一致）。
CANDIDATE_SOURCES: tuple[CandidateSource, ...] = ("on_site", "carried", "activated")
#: 排除原因码闭集的可枚举形式（成员与顺序与 `ExclusionReason` 逐字一致）。
EXCLUSION_REASONS: tuple[ExclusionReason, ...] = (
    "ELIGIBILITY_LOCATION_ABSENT",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",
    "ELIGIBILITY_RESOURCE_CONFLICT",
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",
    "ELIGIBILITY_DISABLED",
    "ROTATION_DEFERRED",
)

#: 排序键版本（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「排序键与游标」）：只参与游标身份，升版使在途游标失效。
#: 2 起排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键）（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。
ORDER_KEY_VERSION = 3
#: 本轮可取候选的时间窗（秒），截至世界当前时刻加该值；候选只在此窗内自足。
CANDIDATE_WINDOW_SECONDS = 86_400
#: 候选类别 → 活动类型（资源占用判据按注册表取该类型的显式占用）。
CANDIDATE_ACTIVITY_KINDS: dict[str, ActivityKind | None] = {
    "product": ActivityKind.CONSUME,
    "lifestyle": ActivityKind.LIFESTYLE,
    "task": ActivityKind.WORK,
}




@dataclass(frozen=True, slots=True)
class LocationView:
    """@brief 一个地点的可行性门视图：地点类闭集、营业时段与所需活动时长。"""

    location_id: str
    location_kinds: frozenset[str]
    #: 启动活动所需的最短时长（秒）：短于该值的候选无法受理。
    minimum_seconds: int = 0
    #: 启动活动允许的最长时长（秒）：长于该值的候选无法受理。
    maximum_seconds: int = DERIVED_DURATION_CAP_SECONDS

    @classmethod
    def from_config(cls, config: Location) -> "LocationView":
        """@brief 活动环境从唯一登记地点类型映射派生。"""
        return cls(location_id=config.location_id, location_kinds=frozenset({PLACE_KIND_ENVIRONMENTS[config.kind]}))


@dataclass(frozen=True, slots=True)
class CandidateEntry:
    """@brief 一个可选对象：身份、类别、地点要求、时长窗口与入池的目录级事实。"""

    object_id: str
    kind: CandidateKind
    #: 具体地点实例要求；None 表示不限定实例（与地点类判定并列，取交集即可行）。
    required_location_id: str | None = None
    #: 该条目需要哪些地点类；空集或含 anywhere 表示恒可行。
    location_kinds: frozenset[str] = field(default_factory=frozenset)
    #: 时长窗口（秒）。
    minimum_seconds: int = 0
    maximum_seconds: int = DERIVED_DURATION_CAP_SECONDS
    #: 目录级库存：0 表示缺货（世界事实）。
    #: 目录级下架（墓碑语义：保留可读、拒绝新场景引用）。
    #: 在本人启用集内；False 即不在启用集（世界事实）。
    enabled: bool = True
    #: 口味权重的查找键（缺省取 object_id）。
    #: 条目声明的代谢当量：生活活动等按条目查表的活动在此给出；None 表示按活动类型查表。
    met: float | None = None


@dataclass(frozen=True, slots=True)
class CandidateInputs:
    """@brief 一次取候选的全部只读输入：世界状态、人物状态、场景事实与画像权重。"""

    #: 人物此刻所在的地点视图；None 表示人物不在任何可判定地点。
    location: LocationView | None
    #: 全部地点的可行性门视图（键为地点标识）：判定营业时段与时长上限时按条目要求的
    #: 地点取用，缺席时回退到人物当前地点。
    locations: Mapping[str, LocationView]
    #: 各实际来源的候选条目，键取 `CandidateSource`。
    sources: Mapping[CandidateSource, Sequence[CandidateEntry]]
    #: 当前所有 ACTIVE 活动的显式个人资源并集。
    occupied_resources: frozenset[str] = field(default_factory=frozenset)
    #: 当前地点本地时刻（自午夜起的小时数，含小数）；None 表示不判定营业时段。
    #: 判定时长越界用的当前时刻（UTC 纪元秒）。
    current_time: int = 0
    #: 候选自足的窗口末端（UTC 纪元秒）。
    window_end_time: int = 0
    #: 单次呈现上限（场景常量 `candidate_presentation_limit`）。
    presentation_limit: int = 20
    #: 轮转常量视图（`semantics.CandidateConstants`：换新名额比例与「长期未呈现」阈值）；
    #: None 表示本次不做轮转裁剪。字段只读不写，因此不在此另立协议类型。
    rotation_constants: Any = None
    #: 是否要求条目声明的具体地点实例（人必须站在该地点）；目录浏览时为 False。
    require_location_instance: bool = True
    #: 是否按人物当前地点判定地点类资格（生活活动窗口，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）：目录浏览面的
    #: 其余类别不判定营业时段与地点实例，生活活动窗口仍按当前地点的地点类排除不相容的条目。
    apply_location_kinds: bool = False
    #: 按对象标识记录的「上次呈现时刻」（纪元秒）：缺失表示从未呈现（轮转判据）。
    last_presented_at: Mapping[str, int] = field(default_factory=dict)
    #: 轮转层本轮推迟呈现的对象标识：这些对象不呈现，但**不是排除**。
    withheld_object_ids: frozenset[str] = frozenset()
    #: 相关度（对象标识 → 整数分，缺项为 0）：只取人物自己的事实与目录事实（life.relevance）。
    relevance: Mapping[str, int] = field(default_factory=dict)
    #: 决胜键 `sha256(场景|人物|日期|对象)` 的前三段：场景身份、人物编号与场景时区的本地日期（YYYY-MM-DD）。
    scenario_id: str = ""
    agent_id: int = 0
    local_date: str = ""
    state_version: int = 0
    catalog_digest: str = ""
    profile_digest: str = ""


@dataclass(frozen=True, slots=True)
class GeneratedCandidate:
    """@brief 生成段的产物：一个候选条目及其来源集合与排序键原料。"""

    entry: CandidateEntry
    sources: tuple[CandidateSource, ...]


@dataclass(frozen=True, slots=True)
class Exclusion:
    """@brief 一个被排除项：身份、类别与闭集内的稳定原因码。"""

    object_id: str
    kind: CandidateKind
    reason: ExclusionReason

    def as_row(self) -> dict[str, str]:
        """@brief 返回真实世界事实排除原因。"""
        return {"object_id": self.object_id, "kind": self.kind, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class OrderedCandidate:
    """@brief 排序段的一行：对象 + 完整稳定排序键（游标即上一页最后一条的该键）。"""

    object_id: str
    kind: CandidateKind
    #: 相关度（整数分，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）：只在口味 × 注意力相同时区分先后。
    relevance: int = 0
    #: 决胜键 `sha256(场景|人物|日期|对象)` 的十六进制摘要。
    tiebreak: str = ""

    def order_key(self) -> tuple[int, str]:
        """@brief 候选排序只比较真实相关度和稳定哈希。"""
        return -self.relevance, self.tiebreak

    def as_row(self) -> dict[str, Any]:
        """@brief 返回候选实际身份、类别及稳定排序原料。"""
        return {"object_id": self.object_id, "kind": self.kind,
                "relevance": self.relevance, "tiebreak": self.tiebreak}


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """@brief 一次取候选的完整返回体：三个分区 + 顺序段 + 游标身份。"""

    #: 生成段的候选全集，按（类别, 对象标识）稳定排序。
    generated: tuple[GeneratedCandidate, ...]
    #: 资格段的保留项，按稳定排序键排序（含被轮转推迟的项，推迟只影响呈现）。
    retained: tuple[OrderedCandidate, ...]
    #: 资格段的被排除项，按（类别, 对象标识）稳定排序。
    excluded: tuple[Exclusion, ...]
    #: 每个来源的命中计数（生成段证据）。
    source_counts: dict[str, int]
    #: 每个原因码的排除计数（资格段证据）。
    exclusion_counts: dict[str, int]
    #: 被轮转推迟呈现的对象标识（不是排除，对象状态与目录都不变）。
    deferred: tuple[str, ...]
    #: 本次呈现条数（不超过单次呈现上限）。
    returned: int
    #: 该分区过滤后的候选总数（保留项规模，只用于计数与下推）。
    total: int
    #: 下一页游标；最后一页为 None。
    next_cursor: str | None
    #: 排序键口径版本，参与游标身份。
    order_key_version: int


def recall(inputs: CandidateInputs) -> CandidatePage:
    """@brief 三段式取候选：生成（四来源合并）→ 资格（硬过滤带原因码）→ 排序（定点键）。

    返回体同时带三段证据：生成段的逐来源命中计数、资格段的逐原因码排除计数、排序段
    的完整稳定排序键与下一页游标。被轮转推迟的对象只计入 `deferred`，不进 `excluded`。
    """
    generated = generate_candidates(inputs)
    retained, excluded = filter_candidates(generated, inputs)
    ordered = sorted((_ordered(item, inputs) for item in retained), key=OrderedCandidate.order_key)
    deferred = tuple(item.object_id for item in ordered
                     if item.object_id in inputs.withheld_object_ids)
    presented = [item for item in ordered if item.object_id not in inputs.withheld_object_ids]
    limit = max(1, int(inputs.presentation_limit))
    return CandidatePage(
        generated=generated, retained=tuple(ordered), excluded=tuple(excluded),
        source_counts=source_counts(generated), exclusion_counts=exclusion_counts(excluded),
        deferred=deferred, returned=min(len(presented), limit), total=len(ordered),
        next_cursor=_cursor(inputs, presented, limit),
        order_key_version=ORDER_KEY_VERSION)


def generate_candidates(inputs: CandidateInputs) -> tuple[GeneratedCandidate, ...]:
    """@brief 生成段：按四个来源合并候选全集，同一对象取来源并集（第一个来源定序）。"""
    merged: dict[tuple[str, str], tuple[CandidateEntry, list[CandidateSource]]] = {}
    for source in CANDIDATE_SOURCES:
        for entry in inputs.sources.get(source, ()):
            key = (entry.kind, entry.object_id)
            if key in merged:
                merged[key][1].append(source)
            else:
                merged[key] = (entry, [source])
    rows = []
    for (_, _), (entry, sources) in merged.items():
        rows.append(GeneratedCandidate(
            entry=entry, sources=tuple(sources)))
    rows.sort(key=lambda item: (item.entry.kind, item.entry.object_id))
    return tuple(rows)


def filter_candidates(generated: Sequence[GeneratedCandidate],
                      inputs: CandidateInputs) -> tuple[list[GeneratedCandidate], list[Exclusion]]:
    """@brief 资格段：逐条按世界事实硬过滤，保留项与被排除项（带原因码）分开返回。"""
    retained: list[GeneratedCandidate] = []
    excluded: list[Exclusion] = []
    for item in generated:
        reason = _exclusion_reason(item, inputs)
        if reason is None:
            retained.append(item)
        else:
            excluded.append(Exclusion(item.entry.object_id, item.entry.kind, reason))
    excluded.sort(key=lambda item: (item.kind, item.object_id))
    return retained, excluded


def source_counts(generated: Sequence[GeneratedCandidate]) -> dict[str, int]:
    """@brief 生成段的逐来源命中计数（参与对象出现在其每一个来源里）。"""
    counts = {source: 0 for source in CANDIDATE_SOURCES}
    for item in generated:
        for source in item.sources:
            counts[source] += 1
    return counts


def exclusion_counts(excluded: Sequence[Exclusion]) -> dict[str, int]:
    """@brief 资格段的逐原因码排除计数（每个原因码都出键，缺席计 0）。"""
    counts = {reason: 0 for reason in EXCLUSION_REASONS}
    for item in excluded:
        counts[item.reason] += 1
    return counts


def cursor_identity(inputs: CandidateInputs, cursor: str | None) -> str:
    """@brief 游标身份：只由（冻结目录摘要、世界状态版本、排序键版本、游标）四项决定。"""
    return sha256_json({"catalog_digest": inputs.catalog_digest,
                    "world_state_version": int(inputs.state_version),
                    "order_key_version": ORDER_KEY_VERSION,
                    "cursor": cursor})


def _cursor(inputs: CandidateInputs, presented: Sequence[OrderedCandidate],
            limit: int) -> str | None:
    """@brief 下一页游标：上一页最后一条的完整稳定排序键；最后一页为空。"""
    if len(presented) <= limit or not presented:
        return None
    last = presented[min(len(presented), limit) - 1]
    return cursor_identity(inputs, json.dumps(list(last.order_key()), separators=(",", ":")))


def tiebreak_digest(scenario_id: str, agent_id: int, local_date: str, object_id: str) -> str:
    """@brief 决胜键 `sha256(场景|人物|日期|对象)`（十六进制）：同分时的先后只由这四项决定，跨日期变化，与对象标识的
    字母序无关（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。"""
    text = f"{scenario_id}|{agent_id}|{local_date}|{object_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ordered_candidate(entry: CandidateEntry, inputs: CandidateInputs) -> OrderedCandidate:
    """@brief 一个候选条目的排序段一行：定点口味与注意力、相关度（缺项为 0）与哈希决胜键；排序段与呈现窗口共用。"""
    return OrderedCandidate(object_id=entry.object_id, kind=entry.kind,
                            relevance=int(inputs.relevance.get(entry.object_id, 0)),
                            tiebreak=tiebreak_digest(inputs.scenario_id, inputs.agent_id, inputs.local_date,
                                                     entry.object_id))


def _ordered(item: GeneratedCandidate, inputs: CandidateInputs) -> OrderedCandidate:
    """@brief 生成段产物投影为排序段一行（口味与注意力与生成段同一口径，另加相关度与决胜键）。"""
    return ordered_candidate(item.entry, inputs)








def _exclusion_reason(item: GeneratedCandidate,
                      inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 资格级联：返回第一个命中的世界事实原因码，全部通过则为 None。

    只有世界事实可以排除——口味权重与注意力先验在本函数里完全不被读到。
    """
    entry = item.entry
    for check in (_location_reason, _enabled_reason, _resource_reason, _duration_reason):
        reason = check(entry, inputs)
        if reason is not None:
            return reason
    return None


def _location_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 地点不可用：人不在指定地点实例，或地点类交集为空。

    目录浏览面（不判定营业时段、不要求地点实例）只在 apply_location_kinds 时判定地点类（生活活动窗口）。
    """
    if not inputs.require_location_instance and not inputs.apply_location_kinds:
        return None
    place = inputs.location
    if place is None:
        return "ELIGIBILITY_LOCATION_ABSENT"
    if (inputs.require_location_instance and entry.required_location_id is not None
            and entry.required_location_id != place.location_id):
        return "ELIGIBILITY_LOCATION_ABSENT"
    if not place.location_kinds or "anywhere" in place.location_kinds:
        return "ELIGIBILITY_LOCATION_KIND_MISMATCH"
    needed = entry.location_kinds
    if not needed or "anywhere" in needed:
        return None
    if needed & place.location_kinds:
        return None
    return "ELIGIBILITY_LOCATION_KIND_MISMATCH"


def _entry_location(entry: CandidateEntry, inputs: CandidateInputs) -> LocationView | None:
    """@brief 判定该条目所用地点：具体要求的地点实例优先，否则取人物当前地点。"""
    if entry.required_location_id is not None:
        return inputs.locations.get(entry.required_location_id)
    return inputs.location








def _enabled_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 不在本人启用集内：人物未启用该条目。"""
    return None if entry.enabled else "ELIGIBILITY_DISABLED"


def _resource_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 候选与全部 ACTIVE 活动按同一注册表的显式占用交集判定（内容条目按内容品类的条件占用）。"""
    kind = CANDIDATE_ACTIVITY_KINDS[entry.kind]
    claims = engine_affordances().resource_claims(kind.value)
    return "ELIGIBILITY_RESOURCE_CONFLICT" if claims & inputs.occupied_resources else None


def _duration_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 时长越界：候选时长短于受理下限或长于受理上限（含窗口与地点上限）。"""
    bounds = _duration_bounds(entry, inputs)
    if bounds is None:
        return "ELIGIBILITY_DURATION_OUT_OF_RANGE"
    return None


def _duration_bounds(entry: CandidateEntry, inputs: CandidateInputs) -> int | None:
    """@brief 返回可行的最大时长（秒）；不可行时返回 None。

    上限取条目声明、所在地点声明与本次时间窗三者的最小值；下限取条目声明与地点
    声明的最大值。上限小于下限即不可行。
    """
    place = _entry_location(entry, inputs)
    low = max(0, int(entry.minimum_seconds))
    high = int(entry.maximum_seconds)
    if place is not None:
        low = max(low, int(place.minimum_seconds))
        high = min(high, int(place.maximum_seconds))
    high = min(high, int(inputs.window_end_time) - int(inputs.current_time))
    if high < low:
        return None
    return max(0, high)


__all__ = [
    "CANDIDATE_KINDS",
    "CANDIDATE_SOURCES",
    "CANDIDATE_WINDOW_SECONDS",
    "EXCLUSION_REASONS",
    "ORDER_KEY_VERSION",
    "CandidateEntry",
    "CandidateInputs",
    "CandidateKind",
    "CandidatePage",
    "CandidateSource",
    "Exclusion",
    "ExclusionReason",
    "GeneratedCandidate",
    "LocationView",
    "OrderedCandidate",
    "recall",
    "cursor_identity",
    "exclusion_counts",
    "filter_candidates",
    "generate_candidates",
    "ordered_candidate",
    "source_counts",
    "tiebreak_digest",
]
