"""@brief 候选契约与曝光轮转的运行期接线：装候选池、取候选、定呈现窗口。

本模块是 `life.candidates`（统一候选契约：生成 → 资格 → 排序三段）与 `life.rotation`
（曝光轮转）在运行期的唯一装配处。它把世界状态与冻结目录装成候选池，经候选契约产出
保留项与被排除项，再按场景声明的换新名额调 `rotate_candidates` 决定本次呈现顺序。本
模块本身**不写世界状态**：`last_presented_at` 由引擎在提交路径写入
（docs/CONTRACTS.md「派生量字段」的 `presentation_state`）。

五类可选对象（product / lifestyle / content / game / task）共用入口 `present`，返回体形
状一致：`PresentationWindow` 携带 `items`（本次呈现行）、`total`（该分区过滤后的候选总
数）、`next_cursor`（下一页游标，最后一页为 None）、`excluded`（带闭集原因码的被排除
项）与 `presented` / `deferred`（引擎落库与审计用）。行形状由 `life.observe` 侧按品类给
出，本模块只做装配、资格与窗口裁剪。

**只有世界事实可以排除**：排除只发生在 `candidates.filter_candidates` 内；口味权重与注
意力先验只进排序键，零权重条目仍然留在候选里并排在非零权重之后
（docs/dev/SPEC-life-runtime.md「两条排除纪律」）。

**呈现顺序由轮转决定**：首屏的换新名额来自 `rotate_candidates`，长期未呈现的对象因此能
与排序键头名并列进窗；本次未进窗的对象以 `ROTATION_DEFERRED` 记入排除项——合同明文规定
它是闭集成员、计入排除计数，但不改对象状态、不写目录。

**两级上限分开**：`candidate_pool_limit` 只进 `total`，`candidate_presentation_limit` 才
是单次呈现条数上限（docs/dev/SPEC-life-runtime.md「两级上限」）。

**两个观察面共用一条链路**：呈现窗口与观察投影（`observe`）从同
一组 `*_entries` 装配候选；区别只在是否施加地点、营业、占用这三类当下的可行性事实——
呈现窗口是目录浏览（菜单级视角），观察是「此刻能不能做」。

**禁止向量检索**：本模块与两个纯函数模块都不含 ANN 索引、embedding 或浮点距离比较；检
索分区键取条目属性，全序由整数排序键给出（docs/dev/SPEC-life-runtime.md「检索」）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from worldmock.life import candidates, rotation
from worldmock.life.candidates import (
    CandidateEntry,
    CandidateInputs,
    CandidateKind,
    Exclusion,
    LocationView,
)
from worldmock.life.models import ActivityKind, active_activities
from worldmock.life.projection import hour_of_day
from worldmock.life.opening_hours import opening_status

#: 活动品类摘要里的食品下钻键：候选类别仍是 product（docs/CONTRACTS.md「候选对象与来源」）。
FOOD_CATEGORY = "foods"
#: 游戏会话与工作合同的下钻键。
GAME_CATEGORY = "game_session"
TASK_CATEGORY = "work_contract"

#: 候选类别 → 活动类型（反塌缩子类标签所需）。
ACTIVITY_OF_KIND: dict[str, ActivityKind] = {
    "product": ActivityKind.CONSUME,
    "lifestyle": ActivityKind.LIFESTYLE,
    "content": ActivityKind.WATCH,
    "game": ActivityKind.PLAY_GAME,
    "task": ActivityKind.WORK,
}


@dataclass(frozen=True, slots=True)
class PoolEntry:
    """@brief 池成员：一个可选对象及其目录级事实与呈现行（只读，无交互计数字段）。"""

    object_id: str
    kind: CandidateKind
    row: dict[str, Any]
    #: 下钻键：活动品类摘要的键（`foods` 覆盖商品，内容取内容品类）。
    category: str = ""
    #: 排序键的口味查找键（缺省取对象标识）。
    bucket: str | None = None
    minimum_seconds: int = 0
    maximum_seconds: int = 86_400
    required_location_id: str | None = None
    location_kinds: frozenset[str] = frozenset()
    #: 目录级库存；0 表示缺货（世界事实）。
    stock: int = 1
    #: 生活活动按条目声明的代谢当量。
    met: float | None = None
    #: 目录级下架（墓碑语义：保留可读、拒绝新场景引用）。
    retired: bool = False
    #: 在本人启用集内（世界事实，不是偏好推断）。
    enabled: bool = True
    #: 题材标签（轮转的标签 Jaccard 去重用）。
    tags: tuple[str, ...] = ()

    def as_candidate(self) -> CandidateEntry:
        """@brief 投影为候选契约的条目形状（口味权重由 `CandidateInputs` 提供）。"""
        return CandidateEntry(object_id=self.object_id, kind=self.kind,
                              required_location_id=self.required_location_id,
                              location_kinds=self.location_kinds,
                              minimum_seconds=self.minimum_seconds,
                              maximum_seconds=self.maximum_seconds,
                              stock=self.stock, retired=self.retired,
                              enabled=self.enabled, bucket=self.bucket, met=self.met)


@dataclass(frozen=True, slots=True)
class RankedMember:
    """@brief 轮转协议的池成员视图：排序键特征与去重标签，不含任何交互计数。"""

    object_id: str
    subtype: str
    tags: tuple[str, ...]
    rank_features: tuple[int, int]


@dataclass(frozen=True, slots=True)
class PresentationWindow:
    """@brief 一次呈现窗口：本次呈现行、分区总数、下一页游标与被排除项。"""

    items: tuple[dict[str, Any], ...]
    total: int
    next_cursor: str | None
    excluded: tuple[Exclusion, ...]
    #: 本次呈现的对象标识（引擎据此在提交路径写 `last_presented_at`）。
    presented: tuple[str, ...] = ()
    #: 本次因名额已满而推迟呈现的对象标识（原因码恒为 `ROTATION_DEFERRED`）。
    deferred: tuple[str, ...] = ()
    #: 生成段的候选全集规模与逐来源命中计数（审计证据，不进提示）。
    generated: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    #: 资格段的逐原因码排除计数。
    exclusion_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PresentationContext:
    """@brief 一次呈现所需的只读世界切片（引擎在观察或下钻边界给出）。"""

    state: Any
    scenario: Any
    semantics: Any
    agent: Any


def location_views(context: PresentationContext) -> dict[str, LocationView]:
    """@brief 地点活动环境与营业日历由同一个冻结声明解释。"""
    return {item.location_id: LocationView.from_config(item, opening_status(
        item, context.state.current_time, context.scenario.timezone,
        context.state.opening_exceptions.get(item.location_id))) for item in context.scenario.locations}


def current_location(context: PresentationContext) -> LocationView | None:
    """@brief 人物当前地点的可行性门视图；不在任何可声明地点时为 None。"""
    return location_views(context).get(context.agent.location_id)


def local_hour(context: PresentationContext) -> float:
    """@brief 人物当前地点的本地时刻（自午夜起的小时数，含小数）。"""
    return hour_of_day(int(context.state.current_time), context.scenario.timezone)


def _rank_features(entry: PoolEntry, inputs: CandidateInputs) -> tuple[int, int]:
    """@brief 排序键特征（定点整数）：口味权重与注意力先验，缺项一律与 0 同义。"""
    key = entry.bucket if entry.kind == "product" and entry.bucket else entry.object_id
    weight = candidates.fixed_point(inputs.taste_weights.get(key, 0.0))
    prior = candidates.fixed_point(
        inputs.attention_priors.get(entry.bucket or entry.object_id, 0.0))
    return (weight, prior)


def _order_key(entry: PoolEntry, inputs: CandidateInputs) -> tuple[int, int, str]:
    """@brief 该条目的完整稳定排序键（游标即上一页最后一条的该键）。"""
    weight, prior = _rank_features(entry, inputs)
    return candidates.OrderedCandidate(object_id=entry.object_id, kind=entry.kind,
                                       taste_weight=weight,
                                       attention_prior=prior).order_key()





def subtype_of(kind: CandidateKind, category: str = "", object_id: str = "") -> str:
    """@brief 反塌缩去重的子类标签：候选类别 + 品类 + 对象标识的稳定前缀。

    子类粒度必须与题材粒度相称：只用类别会让「同一子类不超过三条」退化成整类上限，
    只用对象标识又会让每条自成一类、上限永不生效。因此取三段稳定标签——候选类别、
    该条目所属品类、以及在首个数字串之前的前缀（`movie-0001` 给 `movie`）。

    @param kind 候选类别。
    @param category 该条目所属的品类或内容品类（可省略）。
    @param object_id 对象标识；前缀由它在首个数字串之前的部分给出。
    @return 供 `rotate_candidates` 去重使用的子类标签。
    """
    label = f"{kind}:{category}" if category else kind
    return f"{label}:{_object_prefix(object_id)}" if object_id else label


def _object_prefix(object_id: str) -> str:
    """@brief 对象标识在首个数字串之前的前缀；无前缀时取整个标识。"""
    head = object_id.rstrip("0123456789").rstrip("-_")
    return head or object_id


def _member(entry: PoolEntry, inputs: CandidateInputs) -> RankedMember:
    """@brief 池条目投影为轮转成员（子类按内容品类细分，供反塌缩去重使用）。

    反塌缩去重的相似度对只由「同子类」的条目构成，所以子类粒度必须与题材粒度相称：
    品类级标签把同品类的全部条目放进一个去重桶，桶内条目并没有可比的题材标签，
    Jaccard 因此没有信号，子类上限会退化成整类上限。本模块取「品类 + 条目前缀」两段
    标签：前缀由对象标识在首个数字串之前的部分给出（`movie-0001` 的前缀是 `movie`），
    于是同一子类的条目必然同类，缺失题材标签时也不会互相压制。
    """
    label = str(entry.row.get("kind") or entry.kind)
    return RankedMember(object_id=entry.object_id,
                        subtype=subtype_of(entry.kind, label, entry.object_id),
                        tags=entry.tags, rank_features=_rank_features(entry, inputs))


def rotate_window(entries: tuple[PoolEntry, ...], inputs: CandidateInputs
                  ) -> rotation.RotationDecision:
    """@brief 对候选池跑一次曝光轮转：换新名额只给「长期未呈现」者，游标单向绕池。

    游标取「该人物已留痕的对象数」对池规模取模：它随每次呈现单调前进，因此换新名额沿池
    单向绕回，单对象的最坏等待间隔有界（docs/dev/SPEC-life-runtime.md「曝光轮转」的
    `⌈N ÷ k⌉ ÷ D`）。这里绝不把互动次数当判据——池成员视图里根本没有计数字段。

    @param entries 该分区的池条目。
    @param inputs 候选输入（换新名额比例与呈现上限经场景常量给出）。
    @return 本次呈现序列、换入清单、顺延清单与下一次游标。
    """
    constants = inputs.rotation_constants
    config = rotation.RotationConfig(
        presentation_limit=int(inputs.presentation_limit),
        quota_ratio=float(constants.rotation_quota_ratio),
        stale_seconds=int(constants.rotation_stale_seconds),
        now=int(inputs.current_time))
    pool = tuple(_member(entry, inputs) for entry in entries)
    cursor = int(inputs.rotation_offset) % len(pool) if pool else 0
    decision = rotation.rotate_candidates(pool, dict(inputs.last_presented_at), cursor, config)
    if not decision.rotated_in:
        return decision
    # 换新名额由轮转决定，但窗口顺序与条数上限归本模块：把放行清单与其余条目的排序键
    # 序列合并成完整序列，再用轮转的「上限」给定窗口长度（docs/dev/SPEC-life-runtime.md
    # 「两级上限」：池上限只进计数，单次呈现上限才是窗口长度）。
    fresh = decision.rotated_in
    dropped = set(fresh)
    ranked = sorted(entries, key=lambda entry: _order_key(entry, inputs))
    limit = max(1, int(inputs.presentation_limit))
    kept = [object_id for object_id in fresh]
    kept.extend(entry.object_id for entry in ranked
                if entry.object_id not in dropped and len(kept) < limit)
    return rotation.RotationDecision(
        presented=tuple((object_id, True) for object_id in kept),
        rotated_in=fresh,
        deferred=tuple(entry.object_id for entry in sorted(
            entries, key=lambda item: item.object_id) if entry.object_id not in set(kept)),
        next_cursor=decision.next_cursor, capped_counts=decision.capped_counts,
        capped_total=decision.capped_total)


def _deferred(entries: tuple[PoolEntry, ...], presented: frozenset[str],
              kind: CandidateKind) -> tuple[Exclusion, ...]:
    """@brief 本次未进窗且未被资格段排除的池条目：以 `ROTATION_DEFERRED` 计入排除项。

    推迟只影响本次呈现：它不改对象状态、不写目录、不产生下架事件。已经被资格段排除的
    条目不再重复计入（一个条目只能有一个原因码），因此排除项计数与保留项计数之和恒等于
    候选全集规模（docs/dev/SPEC-life-runtime.md「两条排除纪律」）。

    @param entries 该分区的池条目。
    @param presented 本次进窗的对象标识集合。
    @param kind 该分区的候选类别。
    @return 带 `ROTATION_DEFERRED` 的被排除项，按对象标识升序。
    """
    already = {entry.object_id for entry in entries if entry.object_id in presented}
    return tuple(Exclusion(entry.object_id, kind, "ROTATION_DEFERRED")
                 for entry in sorted(entries, key=lambda item: item.object_id)
                 if entry.object_id not in already)


def _next_cursor(inputs: CandidateInputs, key: tuple[int, int, str] | None,
                 has_more: bool) -> str | None:
    """@brief 下一页游标：上一页最后一条的完整稳定排序键；最后一页为空。

    游标是（排序键, 游标身份）的 JSON 对象：前半段是可定位的有序键，后半段由
    `cursor_identity` 给出——身份只由（冻结目录摘要、世界状态版本、排序键版本、游标）
    决定，不含模型输出、会话状态或墙钟时间。
    """
    if not has_more or key is None:
        return None
    raw = json.dumps(list(key), separators=(",", ":"))
    return json.dumps({"key": list(key), "offset": int(inputs.rotation_offset),
                       "identity": candidates.cursor_identity(inputs, raw)},
                      separators=(",", ":"))


def _page_bounds(ids: tuple[str, ...], cursor: str | None,
                 inputs: CandidateInputs) -> tuple[int, int]:
    """@brief 本次下推在有序候选里的起止位置：游标给起点，页长由单次呈现上限给出。

    游标承载上一页最后一条的完整稳定排序键，因此起点取「序列里该键之后的第一个位置」；
    游标不在本次序列里时退到末尾，绝不从头重来（重复读比读得更少更难发现）。

    @param ids 本次呈现口径下的完整有序对象标识序列。
    @param cursor 上一页游标；None 表示首屏。
    @param inputs 一次取候选的全部只读输入（页长取单次呈现上限）。
    @return `(起点, 终点)` 的半开区间。
    """
    limit = max(1, int(inputs.presentation_limit))
    if cursor is None or not cursor.strip():
        return (0, limit)
    key, _offset = _cursor_key(cursor)
    index = {entry_id: position for position, entry_id in enumerate(ids)}
    anchor = min(index.get(key[2], len(ids) - 1) + 1, len(ids))
    return (anchor, anchor + limit)


def cursor_offset(cursor: str | None) -> int:
    """@brief 游标承载的轮转偏移：下推续页必须沿用同一偏移，否则序列会重新决定。"""
    if cursor is None or not cursor.strip():
        return 0
    _key, offset = _cursor_key(cursor)
    return offset


def _cursor_key(cursor: str) -> tuple[tuple[int, int, str], int]:
    """@brief 解出游标承载的排序键与轮转偏移；载荷形状不合法即报错，不静默从头开始。"""
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError as error:
        logging.error("Candidate cursor is not valid JSON")
        raise ValueError("candidate cursor is not valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"key", "offset", "identity"}:
        logging.error("Candidate cursor fields do not match the generated shape")
        raise ValueError("candidate cursor fields do not match the generated shape")
    values, offset, identity = payload["key"], payload["offset"], payload["identity"]
    if (not isinstance(values, list) or len(values) != 3
            or any(type(value) is not int for value in values[:2])
            or not isinstance(values[2], str) or not values[2].strip()
            or type(offset) is not int or offset < 0
            or not isinstance(identity, str) or not identity.strip()):
        logging.error("Candidate cursor contains an invalid order key, offset or identity")
        raise ValueError("candidate cursor payload is not a stable order key")
    return ((values[0], values[1], values[2]), offset)


def present(entries: tuple[PoolEntry, ...], inputs: CandidateInputs, kind: CandidateKind,
            cursor: str | None) -> PresentationWindow:
    """@brief 三段式取候选后裁出呈现窗口：首屏按轮转重排，后续页按同一序列下推。

    续页必须沿用上一页游标里承载的轮转偏移（`cursor_offset`），否则序列会被重新决定，
    下推出现重复或遗漏。

    呈现序列只由（池、排序键、上次呈现时刻、世界状态版本）决定：它先按轮转为「长期未
    呈现」的对象留出换新名额，其余名额按排序键取件，再做反塌缩去重。同一世界状态版本
    下同一分区得到同一序列，因此游标下推不重不漏。

    @param entries 该分区的候选池（生成段的未收窄全集）。
    @param inputs 一次取候选的全部只读输入。
    @param kind 该分区的候选类别。
    @param cursor 上一页游标；None 表示首屏（首屏提交空游标）。
    @return 本次呈现窗口（含资格段被排除项与轮转推迟项）。
    """
    page = candidates.recall(inputs)
    index = {entry.object_id: entry for entry in entries}
    retained = [entry for entry in entries
                if entry.object_id not in {item.object_id for item in page.excluded}]
    ordered = _ordered_ids(tuple(retained), inputs)
    start, stop = _page_bounds(ordered, cursor, inputs)
    shown = list(ordered[start:stop])
    excluded = _exclusions(retained, page.excluded, shown, kind)
    rows = tuple(index[object_id].row for object_id in shown if object_id in index)
    cursor_after = (_order_key(index[shown[-1]], inputs) if shown else None)
    return PresentationWindow(
        items=rows, total=len(entries),
        next_cursor=_next_cursor(inputs, cursor_after, stop < len(ordered)),
        excluded=excluded, presented=tuple(shown),
        deferred=tuple(item.object_id for item in excluded
                       if item.reason == "ROTATION_DEFERRED"),
        source_counts=page.source_counts, exclusion_counts=page.exclusion_counts,
        generated=len(page.generated))


def _exclusions(entries: tuple[PoolEntry, ...], filtered: tuple[Exclusion, ...],
                shown: list[str], kind: CandidateKind) -> tuple[Exclusion, ...]:
    """@brief 本页的排除项：资格段排除项加推迟项，逐条只有一个原因码、互不重复。

    进窗的条目不再计入排除项（它本次被看见了），因此「本次条数 + 排除项数」与分区规模
    的关系可逐条核对：被排除项与进窗项互斥，且并集覆盖池内全部条目。

    @param entries 该分区的池条目。
    @param filtered 资格段的被排除项（带闭集原因码）。
    @param shown 本次进窗的对象标识序列。
    @param kind 该分区的候选类别。
    @return 本页全部被排除项，按（类别, 对象标识）稳定排序。
    """
    seen = set(shown)
    excluded = list(filtered)
    excluded.extend(_deferred(entries, frozenset(seen), kind))
    return tuple(sorted(excluded, key=lambda item: (item.kind, item.object_id)))


def _ordered_ids(entries: tuple[PoolEntry, ...], inputs: CandidateInputs) -> tuple[str, ...]:
    """@brief 本次呈现口径下的完整有序序列：轮转换新名额 + 排序键补位 + 反塌缩去重。

    池为空时返回空序列。轮转常量缺席（纯候选调用）时退化为纯排序键序列——候选集身份
    仍只由（冻结目录摘要、画像摘要、世界状态版本、选择器版本）四项决定。

    @param entries 该分区的池条目。
    @param inputs 一次取候选的全部只读输入。
    @return 完整有序对象标识序列（覆盖池内全部条目，无重、无漏）。
    """
    if not entries:
        return ()
    ranked = tuple(entry.object_id for entry in sorted(
        entries, key=lambda entry: _order_key(entry, inputs)))
    if inputs.rotation_constants is None:
        return ranked
    decision = rotate_window(entries, inputs)
    fresh = set(decision.rotated_in)
    ordered = (*decision.rotated_in, *(item for item in ranked if item not in fresh))
    return _diversified(entries, ordered, inputs)


def _diversified(entries: tuple[PoolEntry, ...], sorted_ids: tuple[str, ...],
                 inputs: CandidateInputs) -> tuple[str, ...]:
    """@brief 反塌缩：稳定排序键序列里同一子类在窗口内优先不超过 `ROTATION_SUBTYPE_CAP_IN_WINDOW` 条。

    它是**顺序纪律而不是出局条件**：优先放行子类条数未满上限的条目，被压下的条目原样
    顺延到本序列末尾，因此序列长度不变、候选集身份不变、条目状态不变。窗口因此始终能
    填满到单次呈现上限，被推迟的条目仍以 `ROTATION_DEFERRED` 逐条留痕（「没显示」与
    「不存在」可区分）。

    @param entries 该分区的池条目。
    @param sorted_ids 稳定排序键下的完整对象序列。
    @param inputs 一次取候选的全部只读输入。
    @return 重塑后的有序对象标识序列（长度与输入相同）。
    """
    index = {entry.object_id: entry for entry in entries}
    cap = rotation.ROTATION_SUBTYPE_CAP_IN_WINDOW
    preferred: list[str] = []
    overflow: list[str] = []
    counts: dict[str, int] = {}
    for object_id in sorted_ids:
        subtype = _member(index[object_id], inputs).subtype
        if counts.get(subtype, 0) < cap:
            counts[subtype] = counts.get(subtype, 0) + 1
            preferred.append(object_id)
        else:
            overflow.append(object_id)
    return tuple((*preferred, *overflow))


def entry_sources(entries: tuple[PoolEntry, ...], on_site: frozenset[str] = frozenset(),
                  carried: frozenset[str] = frozenset()) -> dict:
    """@brief 生成段的四来源：在场 ∪ 浏览时效内 ∪ 随身持有 ∪ 画像激活集。

    @param entries 该分区的池条目。
    @param on_site 当前地点常驻对象的标识集合。
    @param carried 当前手边持有对象的标识集合。
    @return 键取 `CandidateSource` 的来源映射，值为候选条目序列。
    """
    browsed = tuple(entry.as_candidate() for entry in entries
                    if entry.object_id not in on_site and entry.object_id not in carried)
    return {"on_site": tuple(entry.as_candidate() for entry in entries
                             if entry.object_id in on_site),
            "browsed": browsed,
            "carried": tuple(entry.as_candidate() for entry in entries
                             if entry.object_id in carried),
            "activated": ()}


def candidate_inputs(context: PresentationContext, sources: dict,
                     moment: bool = True, rotation_offset: int = 0,
                     last_presented_at: dict[str, int] | None = None) -> CandidateInputs:
    """@brief 组装一次取候选的全部只读输入（世界状态、人物状态与画像权重）。

    只有世界事实进资格段；口味权重取人物持久化映射、注意力先验取场景声明的画像，
    两者都只进排序键。`moment` 区分两个观察面：观察投影问「此刻能不能做」，因此施加
    全部可行性事实（地点、营业时段、资源占用）；目录下钻是菜单级视角，只施加
    目录级事实（库存、下架、启用集、时长窗口、地点类与具体地点实例），不把「现在正在
    忙」当成条目不存在。能量不构成排除（docs/dev/SPEC-time-and-energy.md「删除的机制」）。

    @param context 只读世界切片。
    @param sources 生成段的四来源映射（`entry_sources` 的产物）。
    @param moment 是否施加当下可行性事实（地点、营业时段、资源占用）。
    @param rotation_offset 该人物在该分区的轮转游标位置（沿池单向绕回，只前进）。
    @param last_presented_at 按对象标识记录的上次呈现时刻（纪元秒），即轮转的判据。
    @return 候选契约的完整输入。
    """
    constants = context.semantics.candidate_constants
    now = int(context.state.current_time)
    profile = next((item for item in context.scenario.agents
                    if item.agent_id == context.agent.agent_id), None)
    motivation = getattr(profile, "motivation", None)
    occupied = frozenset(claim for item in active_activities(context.state, context.agent.agent_id)
                         for claim in item.resource_claims) if moment else frozenset()
    return CandidateInputs(
        location=current_location(context),
        locations=location_views(context), sources=sources,
        occupied_resources=occupied,
        local_hour=(local_hour(context) if moment else None), current_time=now,
        window_end_time=now + candidates.CANDIDATE_WINDOW_SECONDS,
        taste_weights=dict(context.agent.taste_weights),
        attention_priors=dict(getattr(motivation, "attention_prior", {}) or {}),
        presentation_limit=int(constants.presentation_limit),
        rotation_constants=constants, require_location_instance=moment,
        rotation_offset=int(rotation_offset), last_presented_at=dict(last_presented_at or {}),
        state_version=int(context.state.version),
        catalog_digest=str(context.state.content_manifest_sha256),
        profile_digest=str(context.scenario.scenario_id))
