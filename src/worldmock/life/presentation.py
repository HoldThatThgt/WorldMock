"""@brief 候选契约与曝光轮转的运行期接线：装候选池、取候选、定呈现窗口。

本模块是 `life.candidates`（统一候选契约：生成 → 资格 → 排序三段）与 `life.rotation`
（曝光轮转）在运行期的唯一装配处。它把世界状态与冻结目录装成候选池，经候选契约产出
保留项与被排除项，再按场景声明的换新名额调 `rotate_candidates` 决定本次呈现顺序。本
模块本身**不写世界状态**：`last_presented_at` 由引擎在提交路径写入
（docs/CONTRACTS.md#contracts-worldmock-接口合同「派生量字段」的 `presentation_state`）。

三类可选对象（product / lifestyle / task）共用入口 `present`，返回体形
状一致：`PresentationWindow` 携带 `items`（本次呈现行）、`total`（该分区过滤后的候选总
数）、`next_cursor`（下一页游标，最后一页为 None）、`excluded`（带闭集原因码的被排除
项）与 `presented` / `deferred`（引擎落库与审计用）。行形状由 `life.observe` 侧按品类给
出，本模块只做装配、资格与窗口裁剪。

**只有世界事实可以排除**：排除只发生在 `candidates.filter_candidates` 内；口味权重与注
意力先验只进排序键，零权重条目仍然留在候选里并排在非零权重之后
（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「两条排除纪律」）。

**呈现顺序由轮转决定**：首屏的换新名额来自 `rotate_candidates`，按每个对象的上次呈现时刻
留给该分区内最久未呈现的对象，因此它们能与排序键头名并列进窗；不设人物级共享偏移量，也不设
分区游标（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。本次未进窗的对象以 `ROTATION_DEFERRED` 记入
排除项——合同明文规定它是闭集成员、计入排除计数，但不改对象状态、不写目录。

**排序键**：（非零口味，−口味×注意力，−相关度，哈希决胜键），由 `candidates.ordered_candidate`
唯一给出；相关度由调用方按人物自己的事实与目录事实算好传入（`life.relevance`）。

**两级上限分开**：`candidate_pool_limit` 只进 `total`，`candidate_presentation_limit` 才
是单次呈现条数上限（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「两级上限」）。

**两个观察面共用一条链路**：呈现窗口与观察投影（`observe`）从同
一组 `*_entries` 装配候选；区别只在是否施加地点、营业、占用这三类当下的可行性事实——
呈现窗口是目录浏览（菜单级视角），观察是「此刻能不能做」。生活活动窗口另按当前地点施加
地点类资格（地点类不符的条目不进窗口，排除项带 `ELIGIBILITY_LOCATION_KIND_MISMATCH`）。

**禁止向量检索**：本模块与两个纯函数模块都不含 ANN 索引、embedding 或浮点距离比较；检
索分区键取条目属性，全序由整数排序键给出（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「检索」）。
"""

from __future__ import annotations
from worldmock.life.models import SameMomentPresentation, WorldState

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
from worldmock.life.models import DERIVED_DURATION_CAP_SECONDS, active_activities
from worldmock.life.readable import local_date

#: 活动品类摘要里的食品下钻键：候选类别仍是 product（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选对象与来源」）。
FOOD_CATEGORY = "foods"
#: 工作合同的下钻键。

@dataclass(frozen=True, slots=True)
class PoolEntry:
    """@brief 池成员：一个可选对象及其目录级事实与呈现行（只读，无交互计数字段）。"""

    object_id: str
    kind: CandidateKind
    row: dict[str, Any]
    #: 下钻键：活动品类摘要的键（`foods` 覆盖食品）。
    category: str = ""
    #: 排序键的口味查找键（缺省取对象标识）。
    minimum_seconds: int = 0
    maximum_seconds: int = DERIVED_DURATION_CAP_SECONDS
    required_location_id: str | None = None
    location_kinds: frozenset[str] = frozenset()
    #: 目录级库存；0 表示缺货（世界事实）。
    #: 生活活动按条目声明的代谢当量。
    met: float | None = None
    #: 目录级下架（墓碑语义：保留可读、拒绝新场景引用）。
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
                              enabled=self.enabled, met=self.met)


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
    """@brief 地点环境使用已登记类型，不读取营业日历。"""
    return {item.location_id: LocationView.from_config(item) for item in context.scenario.locations}


def current_location(context: PresentationContext) -> LocationView | None:
    """@brief 人物当前地点的可行性门视图；不在任何可声明地点时为 None。"""
    return location_views(context).get(context.agent.location_id)




def _order_key(entry: PoolEntry, inputs: CandidateInputs) -> tuple[int, str]:
    """@brief 该条目的完整稳定排序键（游标即上一页最后一条的该键），与候选契约排序段同一口径。"""
    return candidates.ordered_candidate(entry.as_candidate(), inputs).order_key()


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


def _member(entry: PoolEntry, inputs: CandidateInputs) -> rotation.RotationMember:
    """@brief 池条目投影为轮转成员（子类按内容品类细分，供反塌缩去重使用；排序键与候选契约同一口径）。

    反塌缩去重的相似度对只由「同子类」的条目构成，所以子类粒度必须与题材粒度相称：
    品类级标签把同品类的全部条目放进一个去重桶，桶内条目并没有可比的题材标签，
    Jaccard 因此没有信号，子类上限会退化成整类上限。本模块取「品类 + 条目前缀」两段
    标签：前缀由对象标识在首个数字串之前的部分给出（`movie-0001` 的前缀是 `movie`），
    于是同一子类的条目必然同类，缺失题材标签时也不会互相压制。
    """
    label = str(entry.row.get("kind") or entry.kind)
    return rotation.RotationMember(object_id=entry.object_id,
                                   subtype=subtype_of(entry.kind, label, entry.object_id),
                                   tags=entry.tags, order_key=_order_key(entry, inputs))


def rotate_window(entries: tuple[PoolEntry, ...], inputs: CandidateInputs
                  ) -> rotation.RotationDecision:
    """@brief 对候选池跑一次曝光轮转：换新名额按每个对象的上次呈现时刻留给该分区内最久未呈现者。

    判据只有本分区各对象自己的 `last_presented_at`（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「曝光轮转」）
    ：不用人物级共享
    偏移量，也不设分区游标，别的分区呈现了多少对象不影响本分区，`⌈N ÷ k⌉ ÷ D` 的界按分区成立。这里绝不把互动
    次数当判据——池成员视图里根本没有计数字段。

    @param entries 该分区的池条目。
    @param inputs 候选输入（换新名额比例与呈现上限经场景常量给出）。
    @return 本次呈现序列、换入清单与顺延清单。
    """
    constants = inputs.rotation_constants
    config = rotation.RotationConfig(
        presentation_limit=int(inputs.presentation_limit),
        quota_ratio=float(constants.rotation_quota_ratio),
        stale_seconds=int(constants.rotation_stale_seconds),
        now=int(inputs.current_time))
    pool = tuple(_member(entry, inputs) for entry in entries)
    return rotation.rotate_candidates(pool, dict(inputs.last_presented_at), config)


def _deferred(entries: tuple[PoolEntry, ...], presented: frozenset[str],
              kind: CandidateKind) -> tuple[Exclusion, ...]:
    """@brief 本次未进窗且未被资格段排除的池条目：以 `ROTATION_DEFERRED` 计入排除项。

    推迟只影响本次呈现：它不改对象状态、不写目录、不产生下架事件。已经被资格段排除的
    条目不再重复计入（一个条目只能有一个原因码），因此排除项计数与保留项计数之和恒等于
    候选全集规模（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「两条排除纪律」）。

    @param entries 该分区的池条目。
    @param presented 本次进窗的对象标识集合。
    @param kind 该分区的候选类别。
    @return 带 `ROTATION_DEFERRED` 的被排除项，按对象标识升序。
    """
    already = {entry.object_id for entry in entries if entry.object_id in presented}
    return tuple(Exclusion(entry.object_id, kind, "ROTATION_DEFERRED")
                 for entry in sorted(entries, key=lambda item: item.object_id)
                 if entry.object_id not in already)


def _next_cursor(inputs: CandidateInputs, key: tuple[int, str] | None,
                 has_more: bool) -> str | None:
    """@brief 下一页游标：上一页最后一条的完整稳定排序键；最后一页为空。

    游标是（排序键, 游标身份）的 JSON 对象：前半段是可定位的有序键，后半段由
    `cursor_identity` 给出——身份只由（冻结目录摘要、世界状态版本、排序键版本、游标）
    决定，不含模型输出、会话状态或墙钟时间。游标不承载轮转偏移：轮转只看各对象的上次呈现时刻，
    同一世界状态版本下序列不变。
    """
    if not has_more or key is None:
        return None
    raw = json.dumps(list(key), separators=(",", ":"))
    return json.dumps({"key": list(key), "identity": candidates.cursor_identity(inputs, raw)},
                      separators=(",", ":"))


def _page_bounds(ids: tuple[str, ...], keys: dict[str, tuple[int, str]], cursor: str | None,
                 inputs: CandidateInputs) -> tuple[int, int]:
    """@brief 本次下推在有序候选里的起止位置：游标给起点，页长由单次呈现上限给出。

    游标承载上一页最后一条的完整稳定排序键，因此起点取「序列里该键之后的第一个位置」（按各对象的排序键定位，
    决胜键是哈希而不是对象标识）；游标不在本次序列里时退到末尾，绝不从头重来（重复读比读得更少更难发现）。

    @param ids 本次呈现口径下的完整有序对象标识序列。
    @param keys 各对象的完整稳定排序键。
    @param cursor 上一页游标；None 表示首屏。
    @param inputs 一次取候选的全部只读输入（页长取单次呈现上限）。
    @return `(起点, 终点)` 的半开区间。
    """
    limit = max(1, int(inputs.presentation_limit))
    if cursor is None or not cursor.strip():
        return (0, limit)
    key = cursor_key(cursor)
    position = next((index for index, entry_id in enumerate(ids) if keys[entry_id] == key), len(ids) - 1)
    anchor = min(position + 1, len(ids))
    return (anchor, anchor + limit)


def cursor_key(cursor: str) -> tuple[int, str]:
    """@brief 解出游标承载的完整稳定排序键（相关度整数与非空哈希决胜键）；载荷形状不合法即报错，不静默从头开始。"""
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError as error:
        logging.error("Candidate cursor is not valid JSON")
        raise ValueError("candidate cursor is not valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"key", "identity"}:
        logging.error("Candidate cursor fields do not match the generated shape")
        raise ValueError("candidate cursor fields do not match the generated shape")
    values, identity = payload["key"], payload["identity"]
    if (not isinstance(values, list) or len(values) != 2
            or type(values[0]) is not int
            or not isinstance(values[1], str) or not values[1].strip()
            or not isinstance(identity, str) or not identity.strip()):
        logging.error("Candidate cursor contains an invalid order key or identity")
        raise ValueError("candidate cursor payload is not a stable order key")
    return (values[0], values[1])


def present(entries: tuple[PoolEntry, ...], inputs: CandidateInputs, kind: CandidateKind,
            cursor: str | None) -> PresentationWindow:
    """@brief 三段式取候选后裁出呈现窗口：首屏按轮转重排，后续页按同一序列下推。

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
    keys = {entry.object_id: _order_key(entry, inputs) for entry in retained}
    ordered = _ordered_ids(tuple(retained), keys, inputs)
    start, stop = _page_bounds(ordered, keys, cursor, inputs)
    shown = list(ordered[start:stop])
    excluded = _exclusions(retained, page.excluded, shown, kind)
    rows = tuple(index[object_id].row for object_id in shown if object_id in index)
    cursor_after = keys[shown[-1]] if shown else None
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


def _ordered_ids(entries: tuple[PoolEntry, ...], keys: dict[str, tuple[int, str]],
                 inputs: CandidateInputs) -> tuple[str, ...]:
    """@brief 本次呈现口径下的完整有序序列：轮转换新名额 + 排序键补位 + 反塌缩去重。

    池为空时返回空序列。轮转常量缺席（纯候选调用）时退化为纯排序键序列——候选集身份
    仍只由（冻结目录摘要、画像摘要、世界状态版本、选择器版本）四项决定。换新名额放在序列最前，
    不参与反塌缩顺延（它们计入各子类的条数），所以最久未呈现的对象一定进窗。

    @param entries 该分区的池条目。
    @param keys 各对象的完整稳定排序键。
    @param inputs 一次取候选的全部只读输入。
    @return 完整有序对象标识序列（覆盖池内全部条目，无重、无漏）。
    """
    if not entries:
        return ()
    ranked = tuple(sorted((entry.object_id for entry in entries), key=keys.__getitem__))
    if inputs.rotation_constants is None:
        return ranked
    fresh = rotate_window(entries, inputs).rotated_in
    taken = set(fresh)
    rest = tuple(item for item in ranked if item not in taken)
    return (*fresh, *_diversified(entries, rest, inputs, fresh))


def _diversified(entries: tuple[PoolEntry, ...], sorted_ids: tuple[str, ...],
                 inputs: CandidateInputs, seeded: tuple[str, ...] = ()) -> tuple[str, ...]:
    """@brief 反塌缩：稳定排序键序列里同一子类在窗口内优先不超过 `ROTATION_SUBTYPE_CAP_IN_WINDOW` 条。

    它是**顺序纪律而不是出局条件**：优先放行子类条数未满上限的条目，被压下的条目原样
    顺延到本序列末尾，因此序列长度不变、候选集身份不变、条目状态不变。窗口因此始终能
    填满到单次呈现上限，被推迟的条目仍以 `ROTATION_DEFERRED` 逐条留痕（「没显示」与
    「不存在」可区分）。

    @param entries 该分区的池条目。
    @param sorted_ids 稳定排序键下的完整对象序列（不含已由换新名额放在最前的对象）。
    @param inputs 一次取候选的全部只读输入。
    @param seeded 已放在序列最前的换新名额对象：只计入各子类条数，不再重排。
    @return 重塑后的有序对象标识序列（长度与 sorted_ids 相同）。
    """
    index = {entry.object_id: entry for entry in entries}
    cap = rotation.ROTATION_SUBTYPE_CAP_IN_WINDOW
    preferred: list[str] = []
    overflow: list[str] = []
    counts: dict[str, int] = {}
    for object_id in seeded:
        subtype = _member(index[object_id], inputs).subtype
        counts[subtype] = counts.get(subtype, 0) + 1
    for object_id in sorted_ids:
        subtype = _member(index[object_id], inputs).subtype
        if counts.get(subtype, 0) < cap:
            counts[subtype] = counts.get(subtype, 0) + 1
            preferred.append(object_id)
        else:
            overflow.append(object_id)
    return tuple((*preferred, *overflow))


def entry_sources(entries: tuple[PoolEntry, ...], on_site: frozenset[str] = frozenset()) -> dict:
    """@brief 生成段只区分在场与目录菜单启用来源。

    @param entries 该分区的池条目。
    @param on_site 当前地点常驻对象的标识集合。
    @return 键取 `CandidateSource` 的来源映射，值为候选条目序列。
    """
    activated = tuple(entry.as_candidate() for entry in entries
                    if entry.object_id not in on_site)
    return {"on_site": tuple(entry.as_candidate() for entry in entries
                             if entry.object_id in on_site),
            "activated": activated}


def candidate_inputs(context: PresentationContext, sources: dict, moment: bool = True,
                     last_presented_at: dict[str, int] | None = None,
                     relevance: dict[str, int] | None = None) -> CandidateInputs:
    """@brief 组装一次取候选的全部只读输入（世界状态、人物状态与画像权重）。

    只有世界事实进资格段；口味权重取人物持久化映射、注意力先验取场景声明的画像，相关度由调用方按人物自己的事实与
    目录事实算好（life.relevance），三者都只进排序键。`moment` 区分两个观察面：观察投影问「此刻能不能做」，因此
    施加全部可行性事实（地点实例与地点类、营业时段、资源占用）；目录浏览面是菜单级视角，只施加目录级事实（库存、
    下架、启用集、时长窗口），不判定营业时段与地点实例，不把「现在正在忙」当成条目不存在。生活活动窗口另按
    人物当前地点判定地点类（调用方置 `apply_location_kinds`，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。
    能量不构成排除（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「删除的机制」）。决胜键取场景、人物与场景时区的本地日期。

    @param context 只读世界切片。
    @param sources 生成段的实际来源映射（`entry_sources` 的产物）。
    @param moment 是否施加当下可行性事实（地点、营业时段、资源占用）。
    @param last_presented_at 按对象标识记录的上次呈现时刻（纪元秒），即轮转的判据。
    @param relevance 相关度（对象标识 → 整数分），缺省为全 0。
    @return 候选契约的完整输入。
    """
    constants = context.semantics.candidate_constants
    now = int(context.state.current_time)
    occupied = frozenset(claim for item in active_activities(context.state, context.agent.agent_id)
                         for claim in item.resource_claims) if moment else frozenset()
    return CandidateInputs(
        location=current_location(context),
        locations=location_views(context), sources=sources,
        occupied_resources=occupied,
        current_time=now,
        window_end_time=now + candidates.CANDIDATE_WINDOW_SECONDS,
        presentation_limit=int(constants.presentation_limit),
        rotation_constants=constants, require_location_instance=moment,
        last_presented_at=dict(last_presented_at or {}), relevance=dict(relevance or {}),
        scenario_id=str(context.scenario.scenario_id), agent_id=int(context.agent.agent_id),
        local_date=local_date(now, context.scenario.timezone),
        state_version=int(context.state.version),
        catalog_digest=str(context.semantics.catalog.manifest.catalog_content_sha256),
        profile_digest=str(context.scenario.scenario_id))


def stage_presentation(state: WorldState, pending: dict[int, SameMomentPresentation]) -> WorldState:
    """@brief 把本轮各人物观察的呈现留痕写入世界状态中的本秒暂存（决策轮提交路径）。

    观察边界不允许同版本改状态（`store._validate_state` 只允许同版本追加事件），因此观察先把留痕
    记在引擎内存，随本轮提交按人物写入 `same_moment_presentation`；同一秒已有暂存的人物合并对象时刻，
    保留该秒首轮置顶的浏览结果（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「同一秒内的呈现与「刚才」」）。

    @param state 本轮提交的世界状态。
    @param pending 本轮观察产生的人物→呈现暂存；空字典表示没有留痕。
    @return 写入暂存后的世界状态（没有留痕时即原状态）。
    """
    if not pending:
        return state
    staging = dict(state.same_moment_presentation)
    for agent_id, entry in pending.items():
        earlier = staging.get(agent_id)
        staging[agent_id] = entry if earlier is None else SameMomentPresentation(
            stamps={**earlier.stamps, **entry.stamps})
    return state.model_copy(update={"same_moment_presentation": staging}, deep=True)

def flush_presentation(state: WorldState) -> WorldState:
    """@brief 时钟推进时把本秒暂存的呈现留痕逐人并入 `presentation_state` 并清空暂存。

    这里只覆盖派生量字段，不改目录、不改对象状态、不产生下架事件；多人同轮观察各自保留留痕，
    后观察者不覆盖前者。

    @param state 推进前的世界状态。
    @return 并入留痕后的世界状态（没有暂存时即原状态）。
    """
    if not state.same_moment_presentation:
        return state
    presented = dict(state.presentation_state)
    for agent_id, entry in state.same_moment_presentation.items():
        presented[agent_id] = {**presented.get(agent_id, {}), **entry.stamps}
    return state.model_copy(update={"presentation_state": presented, "same_moment_presentation": {}}, deep=True)
