"""曝光轮转模块的判据、界与反塌缩测试（docs/VERIFICATION.md「工程门禁」的「候选与呈现一致性」）。

断言口径取自 `docs/dev/SPEC-life-runtime.md`「曝光轮转」「禁止永久退役」两节。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace

import pytest

from worldmock.life.rotation import (
    MEASURED_DAILY_DECISION_POINTS,
    ROTATION_DEFERRED,
    ROTATION_SUBTYPE_CAP_IN_WINDOW,
    RotationConfig,
    RotationMember,
    quota_ratio_for_target_wait,
    quota_slots_for_target_wait,
    rotate_candidates,
    rotation_cursor_after,
    rotation_indices,
    rotation_quota_slots,
    rotation_worst_case_wait_days,
    subtype_cap_counts,
    subtype_capacity,
    wait_bound_finding,
)

_DAY = 86_400
_STALE = 7 * _DAY
#: 契约缺省单次呈现上限（docs/CONTRACTS.md「场景常量」）。
_LIMIT = 20


#: 排序键特征：口味权重与注意力先验同档，使全序由对象标识的决胜键给出。
_RANK = (100, 10)


@dataclass(frozen=True)
class _CountedMember:
    """@brief 额外携带互动计数的探针成员，用于证明轮转对互动计数无感。

    `exposure_count` 只存在于这个测试探针里，不在 `RotationMember` 的字段面上；
    探针转成池成员时该字段被丢弃，所以轮转无从读到它。
    """

    object_id: str
    exposure_count: int
    subtype: str = "film"
    tags: tuple[str, ...] = ()
    rank_features: tuple[int, int] = _RANK

    def as_member(self) -> RotationMember:
        """@brief 丢掉互动计数，转成轮转真正接收的池成员。"""
        return RotationMember(object_id=self.object_id, subtype=self.subtype,
                              tags=self.tags, rank_features=self.rank_features)


def _member(index: int, subtype: str = "film", tags: tuple[str, ...] | None = None,
            rank: tuple[int, int] = _RANK) -> RotationMember:
    """@brief 造一条池成员；标签缺省按序号唯一，避免去重干扰界断言。"""
    return RotationMember(object_id=f"ob-{index:03d}", subtype=subtype,
                          tags=(f"tag-{index}",) if tags is None else tags, rank_features=rank)


def _members(count: int, subtype: str = "film", tags: tuple[str, ...] | None = None,
             rank: tuple[int, int] = _RANK) -> list[RotationMember]:
    """@brief 造 `count` 条同子类池成员。"""
    return [_member(index, subtype, tags, rank) for index in range(count)]


def _pool(count: int, subtypes: int = 16) -> list[RotationMember]:
    """@brief 造一个标签互不相同、子类足够多的池，使反塌缩去重不干扰界断言。

    `subtypes=16` 时反塌缩容量为 48，超过单次呈现上限，窗口因此是上限本身。
    """
    return [_member(index, f"kind-{index % subtypes}") for index in range(count)]


def _config(limit: int = _LIMIT, ratio: float = 0.15, now: int = 1_000_000) -> RotationConfig:
    """@brief 造一份场景层常量（阈值缺省取一周，与场景文档口径一致）。"""
    return RotationConfig(presentation_limit=limit, quota_ratio=ratio, stale_seconds=_STALE, now=now)


def _sweep(pool, last, cursor, config, rounds):
    """@brief 连续轮转若干次，返回逐轮的「首次呈现轮次」表（值越小越早出现）。"""
    first: dict[str, int] = {}
    for step in range(rounds):
        decision = rotate_candidates(pool, last, cursor, config)
        for object_id, _ in decision.presented:
            first.setdefault(object_id, step)
        cursor = decision.next_cursor
    return first


def test_worst_case_unpresented_interval_is_bounded_by_exact_form() -> None:
    """核心验收：连续 ⌈N ÷ k⌉ 次呈现后池内每个对象至少被呈现一次（精确界，非连续松弛）。"""
    for pool_size in (7, 20, 33, 100, 990):
        pool = _pool(pool_size)
        config = _config()
        slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
        rounds = math.ceil(pool_size / slots)
        first = _sweep(pool, {}, 0, config, rounds)
        assert set(first) == {member.object_id for member in pool}, (pool_size, slots, rounds)
        # 实测最坏间隔（轮）不超过闭式上界 ⌈N ÷ k⌉，换算成天亦不超过上界函数。
        assert max(first.values()) < rounds
        measured = rounds / MEASURED_DAILY_DECISION_POINTS
        bound = rotation_worst_case_wait_days(pool_size, slots, MEASURED_DAILY_DECISION_POINTS, 1.0)
        assert measured <= bound


def test_bound_holds_when_the_pool_is_partly_fresh() -> None:
    """真正在比的是「久未被呈现」那一批：池里大部分刚呈现过时界同样成立。"""
    pool = _pool(120)
    now = 1_000_000
    config = _config(now=now)
    last = {member.object_id: now for member in pool}
    stale = [member.object_id for member in pool if member.object_id < "ob-010"]
    for object_id in stale:
        last[object_id] = now - 30 * _DAY
    slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
    rounds = math.ceil(len(stale) / slots)
    first = _sweep(pool, last, 0, config, rounds)
    assert set(stale) <= set(first)


def test_cursor_is_advanced_by_exactly_one_quota_per_presentation() -> None:
    """游标推进量恒为 min(k, N)：既不回折，也不因池的呈现结果而漂移。"""
    config = _config()
    slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
    # 池只有一个成员时游标停在零（与池规模同余），其余按 min(k, N) 前进对池规模取模。
    for size in (1, 2, 3, 7, 100):
        step = 0 if size == 1 else min(slots, size) % size
        pool = _pool(size)
        for cursor in range(size):
            assert rotate_candidates(pool, {}, cursor, config).next_cursor == (cursor + step) % size


def test_exact_bound_is_not_the_continuous_relaxation() -> None:
    """精确界与连续松弛是两个量：N 不被 k 整除时前者严格更大，断言必须用前者。"""
    pool_size, slots, points = 100, 3, 75
    exact = rotation_worst_case_wait_days(pool_size, slots, points, 1.0)
    relaxed = pool_size / (slots * points)
    assert exact > relaxed
    assert math.isclose(exact, math.ceil(pool_size / slots) / points, rel_tol=0, abs_tol=0)


#: docs/EXPERIMENT.md「决策点节奏」记录的实测区间：中位 71、范围 9–112.5、保守取值 60。
_MEASURED_INTERVAL = (9.0, 112.5)
_MEASURED_MEDIAN = 71.0
_MEASURED_CONSERVATIVE = 60.0


@pytest.mark.parametrize("points", [9.0, 60.0, 71.0, 112.5])
def test_wait_bound_uses_the_measured_decision_point_interval(points: float) -> None:
    """上界函数按传入的 `D` 计算：区间端点到保守取值都能取值，且区间有实际宽度。"""
    lower, upper = _MEASURED_INTERVAL
    assert lower <= _MEASURED_CONSERVATIVE <= _MEASURED_MEDIAN <= upper
    pool_size, slots = 6750, 3
    rounds = math.ceil(pool_size / slots)
    bound = rotation_worst_case_wait_days(pool_size, slots, points, 1.0)
    assert math.isclose(bound, rounds / points, rel_tol=0, abs_tol=0)


def test_changing_the_decision_point_count_changes_the_wait_in_proportion() -> None:
    """`D` 不是可以硬编码的天数：改 `D` 即改等待，且倍数关系精确。"""
    lower, upper = _MEASURED_INTERVAL
    pool_size, slots = 100, 3
    slow = rotation_worst_case_wait_days(pool_size, slots, lower, 1.0)
    fast = rotation_worst_case_wait_days(pool_size, slots, upper, 1.0)
    assert slow > fast
    assert math.isclose(slow / fast, upper / lower, rel_tol=1e-12)
    # 区间高端与低端相差一个数量级以上：口径取哪个端点，结论就真的不同
    assert not math.isclose(slow, fast) and slow > 10 * fast
    # 只升高决策点数得到的最坏等待单调变短（`D` 是唯一变量）
    waits = [rotation_worst_case_wait_days(pool_size, slots, points, 1.0)
             for points in (lower, _MEASURED_CONSERVATIVE, _MEASURED_MEDIAN, upper)]
    assert waits == sorted(waits, reverse=True)


def test_wait_bound_default_is_inside_the_measured_interval() -> None:
    """缺省入参落在实测区间内部：缺省值是观测口径下的一个点，不越出区间。"""
    lower, upper = _MEASURED_INTERVAL
    assert lower <= MEASURED_DAILY_DECISION_POINTS <= upper
    finding = wait_bound_finding(200, 3, MEASURED_DAILY_DECISION_POINTS, 1.0)
    assert finding.effective_daily_points == MEASURED_DAILY_DECISION_POINTS
    assert math.isclose(finding.worst_case_wait_days,
                        math.ceil(200 / 3) / MEASURED_DAILY_DECISION_POINTS,
                        rel_tol=0, abs_tol=0)
    # 换成区间的另一端，同一分区的结论随之改变：口径是实测区间而不是单一估计值
    other = wait_bound_finding(200, 3, int(upper), 1.0)
    assert other.worst_case_wait_days < finding.worst_case_wait_days


def test_rotation_cursor_only_moves_forward_and_wraps_once_per_cycle() -> None:
    """单向绕回：游标每次严格前进 min(k, N) 并对池规模取模，绝不回折。"""
    pool_size = 17
    quota = 3
    cursor = 0
    positions = [cursor]
    for _ in range(40):
        nxt = rotation_cursor_after(cursor, quota, pool_size)
        assert nxt == (cursor + quota) % pool_size  # 只有 (cursor + k) % N 一条路径
        assert nxt != cursor
        cursor = nxt
        positions.append(cursor)
    # 绕回发生在且仅在跨过环末时；序列的前进量恒为正。
    assert positions[:6] == [0, 3, 6, 9, 12, 15]
    assert positions[6] == 1  # 17 之后再走 3 即绕回 1，而不是折返 14
    assert len({(positions[i + 1] - positions[i]) % pool_size for i in range(len(positions) - 1)}) == 1


def test_rotation_indices_wrap_forward_without_revisiting() -> None:
    """取件位置同样单向：跨过环末继续向前，一轮内不重复。"""
    assert rotation_indices(15, 3, 17) == (15, 16, 0)
    assert len(set(rotation_indices(11, 3, 17))) == 3
    assert rotation_indices(0, 3, 0) == ()
    assert rotation_indices(5, 20, 4) == (1, 2, 3, 0)


def test_quota_slots_derive_from_scenario_constants_never_hardcoded() -> None:
    """换新名额必须由（单次上限 × 比例）导出：默认给 3，且恒在 [1, 上限] 内。"""
    assert rotation_quota_slots(20, 0.15) == 3
    assert rotation_quota_slots(20, 1.0) == 20
    assert rotation_quota_slots(20, 0.001) == 1  # 名额不得为零，否则有些对象永远不出现
    assert rotation_quota_slots(2, 0.15) == 1


def test_rotation_criterion_is_last_presented_at_not_exposure_count() -> None:
    """判据是「上次呈现时刻」：互动计数取任意值都不改变轮转结果。"""
    base = [_CountedMember(object_id=f"ob-{index:03d}", exposure_count=index) for index in range(30)]
    # 只改互动计数，池成员的其余字段逐一相同；久未呈现者是计数最低的那个。
    hot = [replace(member, exposure_count=100_000) for member in base]
    cold = [replace(member, exposure_count=0) for member in base]
    last = {member.object_id: 999_999 for member in base}
    last["ob-000"] = 1_000_000 - 30 * _DAY
    first = rotate_candidates([m.as_member() for m in base], last, 0, _config())
    second = rotate_candidates([m.as_member() for m in hot], last, 0, _config())
    third = rotate_candidates([m.as_member() for m in cold], last, 0, _config())
    assert first == second == third
    # 久未呈现者一定进换新名额，哪怕它计数最低、别的计数很高。
    assert "ob-000" in first.rotated_in
    assert first.rotated_in == ("ob-000",)
    # 池成员本身就没有互动计数字段：把它塞进去只会失败，不会改变判据。
    with pytest.raises(TypeError):
        RotationMember(object_id="ob-000", subtype="film", exposure_count=7)


def test_never_presented_members_are_treated_as_longest_unpresented() -> None:
    """从未呈现恒算「长期未呈现」，其优先级高于最后一次呈现刚好越过阈值者。"""
    pool = _pool(6)
    now = 1_000_000
    last = {"ob-001": now - _STALE, "ob-002": now - _STALE - 1, "ob-003": now - 1}
    decision = rotate_candidates(pool, last, 0, _config(now=now))
    assert set(decision.rotated_in) >= {"ob-000"}
    assert "ob-003" not in decision.rotated_in  # 刚呈现过，不占换新名额


def test_rotation_is_non_destructive_and_defers_without_delisting() -> None:
    """非破坏性：轮转前后池内容逐字节相同；未呈现者只说 ROTATION_DEFERRED，无下架语义。"""
    pool = _pool(60)
    before = copy.deepcopy(pool)
    config = _config()
    decision = rotate_candidates(pool, {}, 0, config)
    assert pool == before  # 输入序列未被修改（元素与顺序逐字节相同）
    assert decision.deferred  # 名额之外的对象如实列出，不静默消失
    assert len(decision.presented) + len(decision.deferred) == len(pool)
    assert ROTATION_DEFERRED == "ROTATION_DEFERRED"
    assert not any(item == "ELIGIBILITY_DELISTED" for item in decision.deferred)
    # 轮转结果里没有目录、对象状态或事件字段：返回体本身就是「只改顺序与名额」的证据。
    assert set(RotationConfig.__dataclass_fields__) == {
        "presentation_limit", "quota_ratio", "stale_seconds", "now",
        "daily_decision_points", "reachability"}


def test_presentation_count_never_exceeds_scenario_limit() -> None:
    """名额守恒：呈现条数不超过单次呈现上限，轮转不放大总量。"""
    for size in (5, 20, 21, 500):
        decision = rotate_candidates(_pool(size), {}, 0, _config())
        assert len(decision.presented) <= _LIMIT
    # 子类足够多时窗口填满到上限；子类不足时窗口受反塌缩容量约束而变短。
    assert len(rotate_candidates(_pool(500), {}, 0, _config()).presented) == _LIMIT
    assert subtype_capacity(5) == 15
    assert subtype_capacity(0) == 0
    # 全部名额都给换新时窗口同样是上限，且清一色来自绕回取件。
    all_rotated = rotate_candidates(_pool(500), {}, 0, _config(ratio=1.0))
    assert len(all_rotated.presented) == _LIMIT
    assert len(all_rotated.rotated_in) == _LIMIT
    # 池只有四个子类时，反塌缩容量 12 比单次上限 20 更紧，窗口因此是 12。
    narrow = rotate_candidates(_pool(500, subtypes=4), {}, 0, _config())
    assert len(narrow.presented) == subtype_capacity(4) == 12


def test_subclass_cap_in_top_window() -> None:
    """反塌缩：同一子类在呈现窗口内不超过 3 条。"""
    pool = _members(30, "drama")
    decision = rotate_candidates(pool, {}, 0, _config())
    assert len(decision.presented) == ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert len(decision.deferred) == 27
    assert not decision.capped_total  # 换新名额先占满窗口，没有别的子类可取而顺延

    multi = pool + [_member(100 + index, f"kind-{index}") for index in range(30)]
    for ratio, expected in ((0.15, _LIMIT), (0.5, _LIMIT)):
        decision = rotate_candidates(multi, {}, 0, _config(ratio=ratio))
        by_id = {member.object_id: member for member in multi}
        kept = [by_id[object_id] for object_id, _ in decision.presented]
        counts = subtype_cap_counts(kept)
        assert counts.get("drama", 0) <= ROTATION_SUBTYPE_CAP_IN_WINDOW
        # 子类够多时窗口仍填满到单次上限，靠的是把「本批不能收」的条目顺延而不是缩短窗口。
        assert len(decision.presented) == expected
        assert decision.capped_total > 0
        assert "drama" in decision.capped_counts  # 被压住的子类如实留痕


def test_multi_subtype_pool_keeps_cap_and_fills_window() -> None:
    """子类够多时窗口填满且每个子类都不超上限：反塌缩不是靠缩短窗口换来的。"""
    pool = _pool(200, subtypes=8)
    by_id = {member.object_id: member for member in pool}
    decision = rotate_candidates(pool, {}, 0, _config())
    counts = subtype_cap_counts([by_id[object_id] for object_id, _ in decision.presented])
    assert sum(counts.values()) == _LIMIT  # 八个子类 × 上限 3 = 24，够填满 20 条窗口
    assert max(counts.values()) <= ROTATION_SUBTYPE_CAP_IN_WINDOW

    all_recent = {member.object_id: 1_000_000 for member in pool}
    ranked = rotate_candidates(pool, all_recent, 0, _config())
    assert not ranked.rotated_in  # 全池都刚呈现过，换新名额为空
    ranked_counts = subtype_cap_counts([by_id[object_id] for object_id, _ in ranked.presented])
    assert sum(ranked_counts.values()) == _LIMIT
    assert max(ranked_counts.values()) <= ROTATION_SUBTYPE_CAP_IN_WINDOW


def test_reachability_discount_scales_worst_case_wait_proportionally() -> None:
    """可达率折扣：可达率 0.2 时最坏等待约为可达率 1.0 时的五倍。"""
    full = rotation_worst_case_wait_days(6750, 3, 75, 1.0)
    discounted = rotation_worst_case_wait_days(6750, 3, 75, 0.2)
    assert full == 30.0
    assert discounted == 150.0
    assert math.isclose(discounted / full, 5.0, rel_tol=1e-12)


def test_discounted_reachability_stretches_measured_wait_by_the_same_factor() -> None:
    """实测口径同样成立：可达率 0.2 的分区，池内全覆盖所需天数约为可达率 1.0 的五倍。"""
    pool = _pool(120)
    config = _config()
    slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
    rounds = math.ceil(len(pool) / slots)
    reachable = wait_bound_finding(len(pool), slots, MEASURED_DAILY_DECISION_POINTS, 1.0)
    limited = wait_bound_finding(len(pool), slots, MEASURED_DAILY_DECISION_POINTS, 0.2)
    assert math.isclose(limited.worst_case_wait_days / reachable.worst_case_wait_days, 5.0, rel_tol=1e-12)
    # 一遍全池覆盖的实测天数等于上界；可达率只影响它折算成「天」的尺度。
    assert rounds / MEASURED_DAILY_DECISION_POINTS == reachable.worst_case_wait_days


def test_wait_bound_finding_reports_effective_decision_points() -> None:
    """报告键口径：有效决策点为 D × 可达率，上界按有效决策点给出。"""
    finding = wait_bound_finding(990, 3, 11, 0.5)
    assert finding.effective_daily_points == 5.5
    assert finding.worst_case_wait_days == 60.0
    assert wait_bound_finding(0, 3, 11, 1.0).worst_case_wait_days == 0.0
    assert math.isinf(rotation_worst_case_wait_days(10, 3, 0, 0.0))


def test_quota_inversion_from_target_wait_round_trips() -> None:
    """参数推导：k = ⌈N ÷ (W × D × f)⌉，代回上界函数必满足目标等待。"""
    pool_size, points, limit = 6750, 75, 20
    slots = quota_slots_for_target_wait(pool_size, 30.0, points, 1.0, limit)
    assert slots == 3
    assert rotation_worst_case_wait_days(pool_size, slots, points, 1.0) <= 30.0
    assert quota_ratio_for_target_wait(pool_size, 30.0, points, 1.0, limit) == 0.15
    # 可达率掉到 0.2 后同样的目标需要五倍名额；再掉到 0.1 则被单次呈现上限封顶。
    assert quota_slots_for_target_wait(pool_size, 30.0, points, 0.2, limit) == 5 * slots
    assert quota_slots_for_target_wait(pool_size, 30.0, points, 0.1, limit) == limit
    assert quota_slots_for_target_wait(0, 30.0, points, 1.0, limit) == 1
    assert quota_slots_for_target_wait(pool_size, 0.0, points, 1.0, limit) == 1


def test_empty_pool_keeps_cursor_and_returns_nothing() -> None:
    """空池合法：不呈现、不顺延、游标归零，不抛错也不兜底造数据。"""
    decision = rotate_candidates([], {}, 7, _config())
    assert decision.presented == () and decision.deferred == ()
    assert decision.next_cursor == 0 and decision.rotated_in == ()
    assert rotation_indices(3, 3, 0) == ()
    assert rotation_cursor_after(4, 3, 0) == 0


def test_missing_tags_do_not_produce_a_similarity_signal() -> None:
    """标签为空时不产生相似度：去重只能依据子类计数，不能凭空判同。"""
    pool = [_member(index, "news", tags=(), rank=(1000 - index, 10)) for index in range(10)]
    pool += [_member(100 + index, f"kind-{index}") for index in range(10)]
    decision = rotate_candidates(pool, {}, 0, _config())
    by_id = {member.object_id: member for member in pool}
    kept = [by_id[object_id] for object_id, _ in decision.presented]
    # 无标签的新闻条目同样受子类上限约束：窗口里只留三条，其余顺延且留痕。
    assert subtype_cap_counts(kept)["news"] == ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert decision.capped_counts["news"] == len(pool) // 2 - ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert decision.capped_total == decision.capped_counts["news"]
    for cursor in (0, 1, 5):
        assert rotate_candidates(pool, {}, cursor, _config()).rotated_in


def test_rotation_is_deterministic_across_runs() -> None:
    """同输入同输出：两次调用逐字节相同，游标推进也只由输入决定。"""
    pool = _pool(120)
    last = {f"ob-{index:03d}": 1_000_000 - index * _DAY for index in range(0, 120, 7)}
    first = rotate_candidates(pool, last, 5, _config())
    second = rotate_candidates(pool, last, 5, _config())
    assert first == second
    assert first.next_cursor == second.next_cursor
