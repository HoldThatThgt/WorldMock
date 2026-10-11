"""@brief 曝光轮转：按每个对象的「上次呈现时刻」给分区内最久未呈现的对象留固定名额（docs/design/04-simulation-engine.md
「出行、内容与目录浏览」，docs/design/04-simulation-engine.md）。

本模块是**纯函数模块**：不写世界状态、不做 IO、不调模型。它同时是「呈现顺序与名额」
的唯一实现处，只读输入、只回结果——`last_presented_at` 的落库由引擎侧承担
（`docs/design/09-io-formats.md`「世界、人物与活动」的 `presentation_state`），因此这里绝不出现
任何状态写入，也不产生下架事件、不改目录、不改对象状态。

三条判据边界（`docs/design/06-agent.md`「提示与工具」）：

- **判据是每个对象各自的「上次呈现时刻」**，不是互动次数、不是任何偏好估计量，也不是人物级的共享偏移量或
  分区游标。合同明文禁止把 `exposure_count` 一类计数当作「受欢迎程度」参与轮转，所以池成员的行里**根本没有**
  互动计数字段：想用也用不上，用了就取不到值。
- **换新名额由场景常量导出**：`k = round(candidate_presentation_limit ×
  rotation_quota_ratio)`，不得写死；`rotation_stale_seconds` 只作「长期未呈现」的阈值，
  「从未呈现」恒算长期未呈现，也恒是最久未呈现的。
- **最大未呈现间隔由上界函数给出精确形式** `⌈N ÷ k⌉ ÷ (D × 可达率)`，连续松弛
  `N ÷ (k × D)` 只是 Little 定律的均值改写、对任何策略都成立，不作断言口径。

**界来自纪律而非公式**：每次呈现的换新名额按上次呈现时刻从早到晚放行分区内长期未呈现的 `min(k, N)` 个
（从未呈现者最先，同一时刻按排序键），被放行者随即成为刚呈现过的对象；于是一个长期未呈现的对象前面至多排着
分区内其余对象，最多等 `⌈N ÷ k⌉` 次呈现就被放行。界按分区成立：`N` 与 `k` 取该分区自己的池规模与名额，
别的分区呈现了多少对象不影响本分区（不设共享偏移量）。换新名额只受子类条数上限约束、不做标签相似度去重，
名额不超过子类上限时从不被挡，界因此精确成立。

**反塌缩不是覆盖率承诺**：`ROTATION_SUBTYPE_CAP_IN_WINDOW` 的标签 Jaccard 贪心去重
保证「同一子类在呈现窗口内不超过三条」，它不保证「每个对象都会被呈现」；覆盖率由
轮转名额配额负责，两者不得互相替代。

**单次呈现上限与换新名额是守恒关系**：换新名额从 `candidate_presentation_limit` 中划出，
因此「呈现条数」这一总量不因轮转而放大——轮转只改顺序与名额分配。

**轮转非破坏性**：本模块的每个函数都不接收也不返回目录或对象状态，输入池是只读视图，
输出只命名「本次呈现了哪些」与「哪些本次不呈现」（`ROTATION_DEFERRED`，非排除、不改状态）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: 换新名额比例的契约缺省值（docs/design/06-agent.md「提示与工具」）。
DEFAULT_ROTATION_QUOTA_RATIO = 0.15
#: 「本次不呈现」的原因码（docs/design/06-agent.md「提示与工具」）。
ROTATION_DEFERRED = "ROTATION_DEFERRED"
#: 同一子类在呈现窗口内的条数上限（反塌缩去重的可断言形式）。
ROTATION_SUBTYPE_CAP_IN_WINDOW = 3
#: 标签 Jaccard 去重的相似度阈值：达到即视为同一题材，超出子类上限即顺延。
SUBTYPE_JACCARD_THRESHOLD = 0.6
#: 每日决策点数的缺省统计入参；上界口径见 docs/design/06-agent.md。
#: 它只作上界函数的缺省入参，实测值应逐分区传入，不得当作真值硬编码判断。
MEASURED_DAILY_DECISION_POINTS = 75


@dataclass(frozen=True, slots=True)
class RotationMember:
    """@brief 参与轮转的池成员只读视图（由调用方装配，本模块既不构造也不修改）。

    刻意只含排序键与去重所需的字段：互动计数字段不在其中，轮转因此不可能把它当判据。
    """

    #: 对象标识（留痕与顺延清单的身份）。
    object_id: str
    #: 子类标签，反塌缩去重按它计数。
    subtype: str
    #: 画像标签，用于标签 Jaccard 相似度。
    tags: tuple[str, ...] = ()
    #: 完整稳定排序键（candidates.OrderedCandidate.order_key：非零口味、−口味×注意力、−相关度、哈希决胜键）。
    order_key: tuple = ()


@dataclass(frozen=True, slots=True)
class RotationConfig:
    """@brief 场景层轮转常量（全部来自场景，本模块不设第二套缺省）。"""

    #: 单次呈现上限，换新名额从其中划出（`candidate_presentation_limit`）。
    presentation_limit: int
    #: 换新名额比例（`rotation_quota_ratio`）。
    quota_ratio: float = DEFAULT_ROTATION_QUOTA_RATIO
    #: 「长期未呈现」的判定阈值秒数（`rotation_stale_seconds`，无缺省、场景必须声明）。
    stale_seconds: int = 0
    #: 本次判定的模拟时刻（纪元秒），由环境的单一共享时钟给出，不用墙钟。
    now: int = 0
    #: 该分区每天可用决策点数（上界函数入参，不进呈现）。
    daily_decision_points: int = MEASURED_DAILY_DECISION_POINTS
    #: 该分区可达率：池只在人物身处该地点时可达，有效决策点为 `daily_decision_points × reachability`。
    reachability: float = 1.0


@dataclass(frozen=True, slots=True)
class RotationDecision:
    """@brief 一次轮转的纯函数结果；不含任何世界状态写入指令，也不含下架语义。"""

    #: 本次呈现序列（长度不超过 `presentation_limit`），逐项标出是否来自换新名额。
    presented: tuple[tuple[str, bool], ...]
    #: 本次被换新名额放行的对象标识（子集于 `presented`，按上次呈现时刻从早到晚）。
    rotated_in: tuple[str, ...]
    #: 本次因名额已满而未呈现的对象标识（原因码恒为 `ROTATION_DEFERRED`），按排序键。
    deferred: tuple[str, ...]
    #: `{子类: 因该子类已达反塌缩上限而顺延的条数}`，只作审计计数，不进提示。
    capped_counts: dict[str, int]
    #: 反塌缩顺延的总条数（等于 `capped_counts` 各项之和）。
    capped_total: int


@dataclass(frozen=True, slots=True)
class BoundedWaitFinding:
    """@brief 一个分区的最坏未呈现间隔对照；对应报告键 `rotation_wait_interval`。"""

    pool_size: int
    quota_slots: int
    reachability: float
    effective_daily_points: float
    worst_case_wait_days: float


def rotation_quota_slots(presentation_limit: int, quota_ratio: float) -> int:
    """@brief 由场景常量导出换新名额 `k = round(上限 × 比例)`，至少一名额且不超过单次上限。

    名额必须由比例导出，任何写死的 `k` 都违反 docs/design/06-agent.md「提示与工具」；上限为 20、
    比例为 0.15 时给出 3。名额为 0 会让长期未呈现的对象永远不出现，因此下限恒为 1。

    @param presentation_limit 单次呈现上限（`candidate_presentation_limit`）。
    @param quota_ratio 换新名额比例（`rotation_quota_ratio`）。
    @return 换新名额条数。
    """
    return max(1, min(presentation_limit, round(presentation_limit * quota_ratio)))


def _ordering_key(member: RotationMember) -> tuple:
    """@brief 稳定全序键：完整排序键（docs/design/06-agent.md「提示与工具」），对象标识只作排序键完全相同时的兜底。"""
    return (tuple(member.order_key), member.object_id)


def _is_stale(member: RotationMember, now: int, last_presented_at: dict[str, int], stale_seconds: int) -> bool:
    """@brief 判定「长期未呈现」：从未呈现恒为真，其余按上次呈现时刻距现在是否达到阈值。"""
    last = last_presented_at.get(member.object_id)
    return last is None or now - last >= stale_seconds


def staleness_order(pool: Sequence[RotationMember], last_presented_at: dict[str, int],
                    now: int, stale_seconds: int) -> list[RotationMember]:
    """@brief 分区内长期未呈现的成员，按上次呈现时刻从早到晚排列（从未呈现最先，同一时刻按排序键）。

    @param pool 一个检索分区的只读成员序列。
    @param last_presented_at 按对象标识记录的上次呈现时刻；缺失表示从未呈现。
    @param now 本次判定的模拟时刻。
    @param stale_seconds 「长期未呈现」的阈值秒数。
    @return 换新名额的候选次序。
    """
    stale = [member for member in pool if _is_stale(member, now, last_presented_at, stale_seconds)]
    return sorted(stale, key=lambda member: (member.object_id in last_presented_at,
                                             last_presented_at.get(member.object_id, 0), _ordering_key(member)))


def _subtype_jaccard(member: RotationMember, kept: Sequence[RotationMember]) -> float:
    """@brief 与本次已收条目中最高的标签 Jaccard 相似度；任一侧标签为空时取 0。"""
    left = set(member.tags)
    if not left:
        return 0.0
    best = 0.0
    for other in kept:
        if other.subtype != member.subtype:
            continue
        right = set(other.tags)
        if not right:
            continue
        best = max(best, len(left & right) / len(left | right))
    return best


def _subtype_full(member: RotationMember, kept: Sequence[RotationMember]) -> bool:
    """@brief 同子类条数是否已达窗口上限。"""
    return sum(1 for other in kept if other.subtype == member.subtype) >= ROTATION_SUBTYPE_CAP_IN_WINDOW


def _capped_by_windows(member: RotationMember, kept: Sequence[RotationMember]) -> bool:
    """@brief 反塌缩判定：同子类条数已达上限，或与已收同子类条目标签 Jaccard 超过阈值。"""
    if _subtype_full(member, kept):
        return True
    return _subtype_jaccard(member, kept) > SUBTYPE_JACCARD_THRESHOLD


def rotation_worst_case_wait_days(pool_size: int, quota_slots: int, daily_decision_points: int,
                                  reachability: float) -> float:
    """@brief 最大未呈现间隔的精确上界 `⌈N ÷ k⌉ ÷ (D × 可达率)`，单位为天。

    只接受精确形式：连续松弛 `N ÷ (k × D)` 是 Little 定律的均值改写，对「永不轮到某对象」
    的策略同样成立，不能当上界断言，故不在此提供。

    @param pool_size 分区池规模 `N`。
    @param quota_slots 每次呈现的换新名额 `k`。
    @param daily_decision_points 该分区每天决策点数 `D`。
    @param reachability 该分区可达率，取值 `(0, 1]`。
    @return 最坏等待天数；池为空或无可达决策点时按无穷大处理。
    """
    if pool_size <= 0:
        return 0.0
    slots = max(1, quota_slots)
    effective_points = daily_decision_points * reachability
    if effective_points <= 0:
        return float("inf")
    rounds = -(-pool_size // slots)
    return rounds / effective_points


def quota_slots_for_target_wait(pool_size: int, target_wait_days: float, daily_decision_points: int,
                                reachability: float, presentation_limit: int) -> int:
    """@brief 由目标等待反解换新名额：`k_min = ⌈N ÷ (W × D × 可达率)⌉`，并按单次上限封顶。

    它正是上界函数的逆：`⌈N ÷ k⌉ ÷ (D × f) ≤ W` 等价于 `k ≥ N ÷ (W × D × f)`。
    目标等待 `W` 本身没有文献依据（只能是场景参数），因此本函数只做换算，不提供缺省。

    @param pool_size 分区池规模 `N`。
    @param target_wait_days 目标最坏等待天数 `W`。
    @param daily_decision_points 该分区每天决策点数 `D`。
    @param reachability 该分区可达率，取值 `(0, 1]`。
    @param presentation_limit 单次呈现上限，反解结果不得超过它。
    @return 满足目标的换新名额下限，至少 1、至多单次呈现上限。
    """
    fraction = target_wait_days * daily_decision_points * reachability
    if pool_size <= 0 or fraction <= 0:
        return 1
    return max(1, min(presentation_limit, -(-pool_size // int(fraction))))


def quota_ratio_for_target_wait(pool_size: int, target_wait_days: float, daily_decision_points: int,
                                reachability: float, presentation_limit: int) -> float:
    """@brief 把反解出的名额换算回场景常量口径的比例 `k ÷ 单次呈现上限`。

    @param pool_size 分区池规模 `N`。
    @param target_wait_days 目标最坏等待天数 `W`。
    @param daily_decision_points 该分区每天决策点数 `D`。
    @param reachability 该分区可达率。
    @param presentation_limit 单次呈现上限。
    @return 供 `rotation_quota_ratio` 使用的比例，取值 `(0, 1]`。
    """
    slots = quota_slots_for_target_wait(pool_size, target_wait_days, daily_decision_points,
                                        reachability, presentation_limit)
    return slots / presentation_limit


def wait_bound_finding(pool_size: int, quota_slots: int, daily_decision_points: int,
                       reachability: float) -> BoundedWaitFinding:
    """@brief 组装一个分区的等待对照（报告键 `rotation_wait_interval` 的逐分区取值）。

    @param pool_size 分区池规模 `N`。
    @param quota_slots 每次呈现的换新名额 `k`。
    @param daily_decision_points 该分区每天决策点数 `D`。
    @param reachability 该分区可达率。
    @return 含实测口径输入与闭式上界的报告行。
    """
    return BoundedWaitFinding(
        pool_size=pool_size,
        quota_slots=max(1, quota_slots),
        reachability=reachability,
        effective_daily_points=daily_decision_points * reachability,
        worst_case_wait_days=rotation_worst_case_wait_days(pool_size, quota_slots,
                                                           daily_decision_points, reachability),
    )


def _quota_batch(stale: Sequence[RotationMember], quota: int, blocked: set[str]) -> list[RotationMember]:
    """@brief 本批换新名额：按上次呈现时刻从早到晚取长期未呈现者，至多 quota 个。

    只受子类条数上限约束（不做标签相似度去重）：名额不超过子类上限时从不被挡，最久未呈现者一定被放行，
    `⌈N ÷ k⌉` 的界因此精确成立。被上限挡下的成员只记进 `blocked`，状态不改，下一次仍按上次呈现时刻排在前面。
    """
    batch: list[RotationMember] = []
    for member in stale:
        if len(batch) >= quota:
            break
        if _subtype_full(member, batch):
            blocked.add(member.object_id)
            continue
        batch.append(member)
    return batch


def subtype_capacity(subtype_count: int) -> int:
    """@brief 反塌缩下窗口能容纳的条数上限：子类数 × 每子类上限。

    池里子类太少时，反塌缩上限比单次呈现上限更紧——此时窗口因此变短，
    这是「同一子类不超过三条」的必然后果，不是名额被吞。轮转不得为了凑满窗口
    而破坏该上限，否则反塌缩保证失效。

    @param subtype_count 池中不同子类的个数。
    @return 窗口条数上限，至少为 0。
    """
    return max(0, subtype_count) * ROTATION_SUBTYPE_CAP_IN_WINDOW


def _ranked_batch(ordered: Sequence[RotationMember], kept: Sequence[RotationMember],
                  limit: int, blocked: set[str]) -> list[RotationMember]:
    """@brief 其余名额按排序键取件：逐条过反塌缩去重，被挡下者顺延、不当场出局。

    窗口受单次呈现上限与反塌缩容量双重约束：池内子类过少时窗口因此变短，这是
    「同一子类不超过三条」的直接后果，不靠降级上限去凑满。取件按排序键**由优到劣
    多轮推进**：一轮里某子类已占满就顺延到下一轮再考虑它，因此一个被压住的头部条目
    不会把它后面的全部条目一起挡住。被挡下的成员标识写进 `blocked` 供留痕。

    @param ordered 按排序键由优到劣排列的池成员。
    @param kept 已由换新名额收下的成员。
    @param limit 单次呈现上限（场景常量）。
    @param blocked 收集因反塌缩上限而未收下的成员标识。
    @return 本批收下的成员，按排序键由优到劣。
    """
    window = min(limit, subtype_capacity(len({member.subtype for member in ordered})))
    taken = {member.object_id for member in kept}
    ranked = [member for member in ordered if member.object_id not in taken]
    chosen: list[RotationMember] = []
    while ranked and len(kept) + len(chosen) < window:
        remaining: list[RotationMember] = []
        for member in ranked:
            if len(kept) + len(chosen) >= window or _capped_by_windows(member, [*kept, *chosen]):
                blocked.add(member.object_id)
                remaining.append(member)
                continue
            chosen.append(member)
        if len(remaining) == len(ranked):
            break  # 一轮没有任何进展即停：剩余条目全被上限挡住，窗口只能缩短
        ranked = remaining
    return chosen


def subtype_cap_counts(kept: Sequence[RotationMember]) -> dict[str, int]:
    """@brief 统计本次呈现窗口内各子类的条数，供反塌缩断言与报告读取。

    @param kept 本次呈现的成员序列。
    @return `{子类: 条数}`；窗口内任何取值都不得超过 `ROTATION_SUBTYPE_CAP_IN_WINDOW`。
    """
    counts: dict[str, int] = {}
    for member in kept:
        counts[member.subtype] = counts.get(member.subtype, 0) + 1
    return counts


def rotate_candidates(pool: Sequence[RotationMember], last_presented_at: dict[str, int],
                      config: RotationConfig) -> RotationDecision:
    """@brief 一次曝光轮转：按换新名额与排序键给出本次呈现序列，只读池与时刻映射。

    换新名额先按每个对象的上次呈现时刻从早到晚取长期未呈现者（从未呈现最先，staleness_order），其余名额按排序键取，
    取件时用标签 Jaccard 贪心去重压住同一子类的堆叠。呈现条数不超过
    `config.presentation_limit`，且窗口内同一子类不超过 `ROTATION_SUBTYPE_CAP_IN_WINDOW` 条；
    本次未进序列的池成员以 `ROTATION_DEFERRED` 记入 `deferred`——那只是「本次不呈现」，
    不改对象状态、不写目录、不产生下架事件，故呈现条数加顺延条数恒等于池规模。
    不设游标：同一池、同一组上次呈现时刻与同一时刻给出同一结果。

    @param pool 一个检索分区的只读成员序列（空池合法：返回空结果）。
    @param last_presented_at 按对象标识记录的「上次呈现时刻」（纪元秒），缺失表示从未呈现。
    @param config 场景层常量。
    @return 本次呈现序列、换新成员与顺延清单。
    """
    if not pool:
        return RotationDecision((), (), (), {}, 0)
    ordered = sorted(pool, key=_ordering_key)
    quota = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
    blocked: set[str] = set()
    turned = _quota_batch(staleness_order(ordered, last_presented_at, config.now, config.stale_seconds),
                          quota, blocked)
    selected = _ranked_batch(ordered, turned, config.presentation_limit, blocked)
    kept = [*turned, *selected]
    chosen_ids = {member.object_id for member in kept}
    # 顺延清单是池的补集：轮转只改顺序与名额，所以「没显示」永远可数、可区分，不静默消失。
    deferred = tuple(member.object_id for member in ordered if member.object_id not in chosen_ids)
    # 反塌缩留痕的口径是「本次想过要收、却因同一子类条数达上限而顺延」的成员，按子类聚合。
    capped: dict[str, int] = {}
    for member in ordered:
        if member.object_id in blocked and member.object_id not in chosen_ids:
            capped[member.subtype] = capped.get(member.subtype, 0) + 1
    turn_ids = {member.object_id for member in turned}
    return RotationDecision(
        presented=tuple((member.object_id, member.object_id in turn_ids) for member in kept),
        rotated_in=tuple(member.object_id for member in turned),
        deferred=deferred,
        capped_counts=capped,
        capped_total=sum(capped.values()),
    )
