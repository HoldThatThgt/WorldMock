"""@brief 统一候选契约：五类可选对象走同一套生成、资格、排序三段式。

商品、生活活动、内容、游戏与任务共用本模块，任何一类都不得另起一条独立链路
（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「候选契约与呈现规则」的「统一候选契约」）。三段
的形状固定：

- **生成**：按四个获取来源合并候选——当前地点常驻对象（`on_site`）、浏览或激活
  过且仍在时效内的对象（`browsed`）、在当前位置手边持有的对象（`carried`）、由画像
  激活集在本次场景中激活的对象（`activated`）。输出未收窄的候选全集，并逐来源给
  出命中计数。
- **资格**：逐条排除理由对应闭集里的一个稳定原因码；保留项加被排除项的计数等于
  候选全集的规模（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「两条排除纪律」）。生活活动窗口按人物
  当前地点施加地点类资格（`apply_location_kinds`，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ，裁决 S8）。
- **排序**：排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键）（定点整数与
  `sha256(场景|人物|日期|对象)`，裁决 S4）；相关度由调用方按人物自己的事实与目录事实给出，
  决胜键使零口味时的顺序不退化为对象标识的字母序，且随日期变化。

**两条纪律在本模块内被硬编码为形状**：

1. 只有世界事实可以排除——软偏好（口味权重、注意力先验）只出现在排序键里，零权
   重的对象仍然留在候选内并排在非零权重之后，绝不因偏好出局。
2. 「没显示」与「不存在」可区分——被排除的每一条都带闭集内的原因码。`ROTATION_
   DEFERRED` 只表示本次呈现名额已满，它不是排除：被推迟的对象仍是保留项。

**候选是派生量不是状态**：本模块是纯函数，不写世界状态、不做 IO、不调模型。候选
身份只由（冻结目录摘要、画像摘要、世界状态版本、选择器版本）决定，不含墙钟时间、
缓存或模型输出；分桶与摘要哈希统一用 `hashlib`，不用受 `PYTHONHASHSEED` 影响的
内建 `hash()`（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「统一候选契约」）。

闭集词表（`CandidateKind`、`CandidateSource`、`ExclusionReason`）、排序键字段名与
游标口径的权威定义在 `docs/CONTRACTS.md#contracts-worldmock-接口合同`「候选与呈现合同」，本模块只照抄，不另起词。

字段面与行为规则不一致时以合同为准；本模块的 docstring 用中文，代码、日志与抛错
用英文。
"""

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

#: 候选类别闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「候选对象与来源」）。
CandidateKind = Literal["product", "lifestyle", "content", "game", "task"]

#: 候选来源闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「候选对象与来源」）。
CandidateSource = Literal["on_site", "browsed", "carried", "activated"]

#: 排除原因码闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「排除原因码」）。
ExclusionReason = Literal[
    "ELIGIBILITY_LOCATION_ABSENT",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",
    "ELIGIBILITY_CLOSED_HOURS",
    "ELIGIBILITY_OPENING_UNKNOWN",
    "ELIGIBILITY_OUT_OF_STOCK",
    "ELIGIBILITY_DELISTED",
    "ELIGIBILITY_RESOURCE_CONFLICT",
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",
    "ELIGIBILITY_DISABLED",
    "ROTATION_DEFERRED",
]

#: 候选类别闭集的可枚举形式（成员与顺序与 `CandidateKind` 逐字一致）。
CANDIDATE_KINDS: tuple[CandidateKind, ...] = ("product", "lifestyle", "content", "game", "task")
#: 候选来源闭集及生成合并顺序（成员与顺序与 `CandidateSource` 逐字一致）。
CANDIDATE_SOURCES: tuple[CandidateSource, ...] = ("on_site", "browsed", "carried", "activated")
#: 排除原因码闭集的可枚举形式（成员与顺序与 `ExclusionReason` 逐字一致）。
EXCLUSION_REASONS: tuple[ExclusionReason, ...] = (
    "ELIGIBILITY_LOCATION_ABSENT",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",
    "ELIGIBILITY_CLOSED_HOURS",
    "ELIGIBILITY_OPENING_UNKNOWN",
    "ELIGIBILITY_OUT_OF_STOCK",
    "ELIGIBILITY_DELISTED",
    "ELIGIBILITY_RESOURCE_CONFLICT",
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",
    "ELIGIBILITY_DISABLED",
    "ROTATION_DEFERRED",
)

#: 排序键版本（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「排序键与游标」）：只参与游标身份，升版使在途游标失效。
#: 2 起排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键）（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。
ORDER_KEY_VERSION = 2
#: 定点份额度：四位小数，与 `taste_weights` 的口径一致。
FIXED_POINT_SCALE = 10_000
#: 本轮可取候选的时间窗（秒），截至世界当前时刻加该值；候选只在此窗内自足。
CANDIDATE_WINDOW_SECONDS = 86_400
#: 候选类别 → 活动类型（资源占用判据按注册表取该类型的显式占用）。
CANDIDATE_ACTIVITY_KINDS: dict[str, ActivityKind | None] = {
    "product": ActivityKind.CONSUME,
    "lifestyle": ActivityKind.LIFESTYLE,
    "content": ActivityKind.WATCH,
    "game": ActivityKind.PLAY_GAME,
    "task": ActivityKind.WORK,
}


def fixed_point(value: float) -> int:
    """@brief 把软偏好数值折算为四位小数的定点整数（与 `taste_weights` 同口径）。"""
    return int(round(float(value) * FIXED_POINT_SCALE))


@dataclass(frozen=True, slots=True)
class LocationView:
    """@brief 一个地点的可行性门视图：地点类闭集、营业时段与所需活动时长。"""

    location_id: str
    location_kinds: frozenset[str]
    opening_status: str = "unknown"
    #: 启动活动所需的最短时长（秒）：短于该值的候选无法受理。
    minimum_seconds: int = 0
    #: 启动活动允许的最长时长（秒）：长于该值的候选无法受理。
    maximum_seconds: int = DERIVED_DURATION_CAP_SECONDS

    @classmethod
    def from_config(cls, config: Location, status: str) -> "LocationView":
        """@brief 地点活动环境取显式声明，营业状态取统一日历解释结果。"""
        return cls(location_id=config.location_id,
                   location_kinds=frozenset(config.location_kinds), opening_status=status)


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
    stock: int = 1
    #: 目录级下架（墓碑语义：保留可读、拒绝新场景引用）。
    retired: bool = False
    #: 在本人启用集内；False 即不在启用集（世界事实）。
    enabled: bool = True
    #: 口味权重的查找键（缺省取 object_id）。
    bucket: str | None = None
    #: 条目声明的代谢当量：生活活动等按条目查表的活动在此给出；None 表示按活动类型查表。
    met: float | None = None
    #: 内容条目的内容品类（占用按它判定：播客只占 ears）；非内容条目为 None。
    content_kind: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateInputs:
    """@brief 一次取候选的全部只读输入：世界状态、人物状态、场景事实与画像权重。"""

    #: 人物此刻所在的地点视图；None 表示人物不在任何可判定地点。
    location: LocationView | None
    #: 全部地点的可行性门视图（键为地点标识）：判定营业时段与时长上限时按条目要求的
    #: 地点取用，缺席时回退到人物当前地点。
    locations: Mapping[str, LocationView]
    #: 四个来源各自的候选条目，键取 `CandidateSource`。
    sources: Mapping[CandidateSource, Sequence[CandidateEntry]]
    #: 当前所有 ACTIVE 活动的显式个人资源并集。
    occupied_resources: frozenset[str] = field(default_factory=frozenset)
    #: 当前地点本地时刻（自午夜起的小时数，含小数）；None 表示不判定营业时段。
    local_hour: float | None = 0.0
    #: 判定时长越界用的当前时刻（UTC 纪元秒）。
    current_time: int = 0
    #: 候选自足的窗口末端（UTC 纪元秒）。
    window_end_time: int = 0
    #: 口味权重（画像编译 v2 的 `taste_weights`，键为对象标识）；缺项与 0 同义。
    taste_weights: Mapping[str, Any] = field(default_factory=dict)
    #: 注意力先验（检索分区键 → 数值）；缺项与 0 同义。
    attention_priors: Mapping[str, Any] = field(default_factory=dict)
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
    taste_weight: int
    attention_prior: int


@dataclass(frozen=True, slots=True)
class Exclusion:
    """@brief 一个被排除项：身份、类别与闭集内的稳定原因码。"""

    object_id: str
    kind: CandidateKind
    reason: ExclusionReason

    def as_row(self) -> dict[str, str]:
        """@brief 被排除项的返回体行（合同形状：object_id、kind、reason）。"""
        return {"object_id": self.object_id, "kind": self.kind, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class OrderedCandidate:
    """@brief 排序段的一行：对象 + 完整稳定排序键（游标即上一页最后一条的该键）。"""

    object_id: str
    kind: CandidateKind
    taste_weight: int
    attention_prior: int
    #: 相关度（整数分，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）：只在口味 × 注意力相同时区分先后。
    relevance: int = 0
    #: 决胜键 `sha256(场景|人物|日期|对象)` 的十六进制摘要。
    tiebreak: str = ""

    def order_key(self) -> tuple[int, int, int, str]:
        """@brief 完整稳定排序键：非零口味在前，其后口味 × 注意力降序、相关度降序，决胜键为哈希。

        首项是「口味权重非零」的存在标记（0 在前），它保证零权重对象排在全部非零
        权重对象之后——这是「软偏好只改序、不出局」在排序键上的落点，不是第二把
        主键。主键本身按口味 × 注意力降序取负值，相关度同样取负值，比较结果全为整数；
        决胜键不是对象标识，零口味时的顺序因此不退化为字母序，且随本地日期变化。
        """
        return (0 if self.taste_weight else 1,
                -(self.taste_weight * self.attention_prior), -self.relevance, self.tiebreak)

    def as_row(self) -> dict[str, Any]:
        """@brief 保留项的返回体行（含完整稳定排序键的原料：定点口味与注意力、相关度与决胜键）。"""
        return {"object_id": self.object_id, "kind": self.kind,
                "taste_weight": self.taste_weight,
                "attention_prior": self.attention_prior,
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
            entry=entry, sources=tuple(sources),
            taste_weight=_taste_weight(entry, inputs),
            attention_prior=_attention_prior(entry, inputs)))
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
                            taste_weight=_taste_weight(entry, inputs),
                            attention_prior=_attention_prior(entry, inputs),
                            relevance=int(inputs.relevance.get(entry.object_id, 0)),
                            tiebreak=tiebreak_digest(inputs.scenario_id, inputs.agent_id, inputs.local_date,
                                                     entry.object_id))


def _ordered(item: GeneratedCandidate, inputs: CandidateInputs) -> OrderedCandidate:
    """@brief 生成段产物投影为排序段一行（口味与注意力与生成段同一口径，另加相关度与决胜键）。"""
    return ordered_candidate(item.entry, inputs)


def _bucket(entry: CandidateEntry) -> str:
    """@brief 检索分区键：条目属性（口味查找键），不取地点。"""
    return entry.bucket if entry.bucket is not None else entry.object_id


def _taste_weight(entry: CandidateEntry, inputs: CandidateInputs) -> int:
    """@brief 口味权重（定点整数）：缺项一律 0，不做缺省抬升。"""
    key = _bucket(entry) if entry.kind == "product" else entry.object_id
    raw = inputs.taste_weights.get(key, inputs.taste_weights.get(_bucket(entry), 0))
    return fixed_point(raw)


def _attention_prior(entry: CandidateEntry, inputs: CandidateInputs) -> int:
    """@brief 注意力先验（定点整数）：缺项一律 0，不做缺省抬升。"""
    raw = inputs.attention_priors.get(_bucket(entry), 0)
    return fixed_point(raw)


def _exclusion_reason(item: GeneratedCandidate,
                      inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 资格级联：返回第一个命中的世界事实原因码，全部通过则为 None。

    只有世界事实可以排除——口味权重与注意力先验在本函数里完全不被读到。
    """
    entry = item.entry
    for check in (_location_reason, _hours_reason, _stock_reason, _retired_reason,
                  _enabled_reason, _resource_reason, _duration_reason):
        reason = check(entry, inputs)
        if reason is not None:
            return reason
    return None


def _location_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 地点不可用：人不在指定地点实例，或地点类交集为空。

    目录浏览面（不判定营业时段、不要求地点实例）只在 apply_location_kinds 时判定地点类（生活活动窗口）。
    """
    if inputs.local_hour is None and not inputs.require_location_instance and not inputs.apply_location_kinds:
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


def _hours_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 统一营业三态判定，未知资料不能解释为全天开放。"""
    if inputs.local_hour is None:
        return None
    place = _entry_location(entry, inputs)
    if place is None:
        return "ELIGIBILITY_LOCATION_ABSENT"
    if place.opening_status == "unknown":
        return "ELIGIBILITY_OPENING_UNKNOWN"
    return None if place.opening_status == "open" else "ELIGIBILITY_CLOSED_HOURS"


def _stock_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 库存为零：目录级库存耗尽。"""
    return "ELIGIBILITY_OUT_OF_STOCK" if entry.stock <= 0 else None


def _retired_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 目录级下架：墓碑条目保留可读但拒绝新场景引用。"""
    return "ELIGIBILITY_DELISTED" if entry.retired else None


def _enabled_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 不在本人启用集内：人物未启用该条目。"""
    return None if entry.enabled else "ELIGIBILITY_DISABLED"


def _resource_reason(entry: CandidateEntry, inputs: CandidateInputs) -> ExclusionReason | None:
    """@brief 候选与全部 ACTIVE 活动按同一注册表的显式占用交集判定（内容条目按内容品类的条件占用）。"""
    kind = CANDIDATE_ACTIVITY_KINDS[entry.kind]
    claims = engine_affordances().resource_claims(kind.value, content_kind=entry.content_kind)
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
    "FIXED_POINT_SCALE",
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
    "fixed_point",
    "generate_candidates",
    "ordered_candidate",
    "source_counts",
    "tiebreak_digest",
]
