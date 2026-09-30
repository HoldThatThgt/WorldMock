"""曝光轮转模块的判据、界与反塌缩测试（docs/VERIFICATION.md「工程门禁」的「候选与呈现一致性」）。

断言口径取自 `docs/dev/SPEC-life-runtime.md`「曝光轮转」「禁止永久退役」两节与
`docs/dev/SPEC-dayrun-fidelity.md` 5.1：轮转以每个对象的上次呈现时刻判定，换新名额留给该分区内最久未呈现的对象，
不设共享偏移量或分区游标，`⌈N ÷ k⌉` 的上界按分区成立。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, fields, replace

import pytest

from worldmock.life.rotation import (
    MEASURED_DAILY_DECISION_POINTS,
    ROTATION_DEFERRED,
    ROTATION_SUBTYPE_CAP_IN_WINDOW,
    RotationConfig,
    RotationDecision,
    RotationMember,
    quota_ratio_for_target_wait,
    quota_slots_for_target_wait,
    rotate_candidates,
    rotation_quota_slots,
    rotation_worst_case_wait_days,
    staleness_order,
    subtype_cap_counts,
    subtype_capacity,
    wait_bound_finding,
)

_DAY = 86_400
_STALE = 7 * _DAY
#: 契约缺省单次呈现上限（docs/CONTRACTS.md「场景常量」）。
_LIMIT = 20
#: 两次呈现之间的模拟间隔（秒）：远小于「长期未呈现」阈值，刚呈现过的对象在一轮扫描里都不再算长期未呈现。
_STEP = 60


def _key(index: int) -> tuple:
    """@brief 零口味条目的排序键：相关度相同，决胜键随序号递增（与候选契约的四段排序键同形）。"""
    return (1, 0, 0, f"{index:03d}")


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
    order_key: tuple = ()

    def as_member(self) -> RotationMember:
        """@brief 丢掉互动计数，转成轮转真正接收的池成员。"""
        return RotationMember(object_id=self.object_id, subtype=self.subtype,
                              tags=self.tags, order_key=self.order_key)


def _member(index: int, subtype: str = "film", tags: tuple[str, ...] | None = None,
            key: tuple | None = None) -> RotationMember:
    """@brief 造一条池成员；标签缺省按序号唯一，避免去重干扰界断言；排序键缺省随序号递增。"""
    return RotationMember(object_id=f"ob-{index:03d}", subtype=subtype,
                          tags=(f"tag-{index}",) if tags is None else tags,
                          order_key=_key(index) if key is None else key)


def _members(count: int, subtype: str = "film") -> list[RotationMember]:
    """@brief 造 `count` 条同子类池成员。"""
    return [_member(index, subtype) for index in range(count)]


def _pool(count: int, subtypes: int = 16, offset: int = 0) -> list[RotationMember]:
    """@brief 造一个标签互不相同、子类足够多的池，使反塌缩去重不干扰界断言。

    `subtypes=16` 时反塌缩容量为 48，超过单次呈现上限，窗口因此是上限本身。
    """
    return [_member(offset + index, f"kind-{index % subtypes}") for index in range(count)]


def _config(limit: int = _LIMIT, ratio: float = 0.15, now: int = 1_000_000) -> RotationConfig:
    """@brief 造一份场景层常量（阈值缺省取一周，与场景文档口径一致）。"""
    return RotationConfig(presentation_limit=limit, quota_ratio=ratio, stale_seconds=_STALE, now=now)


def _sweep(pool, config: RotationConfig, rounds: int, last: dict[str, int] | None = None):
    """@brief 连续轮转若干次，每次把本次呈现的对象按当时时刻留痕（引擎在提交路径写 last_presented_at 的同一口径），
    返回逐对象的「首次呈现轮次」表（值越小越早出现）与各轮的换新名额。"""
    stamps = dict(last or {})
    first: dict[str, int] = {}
    quotas: list[tuple[str, ...]] = []
    for step in range(rounds):
        now = config.now + step * _STEP
        decision = rotate_candidates(pool, stamps, replace(config, now=now))
        quotas.append(decision.rotated_in)
        for object_id, _ in decision.presented:
            first.setdefault(object_id, step)
            stamps[object_id] = now
    return first, quotas


def test_worst_case_unpresented_interval_is_bounded_by_exact_form() -> None:
    """核心验收：连续 ⌈N ÷ k⌉ 次呈现后池内每个对象至少被呈现一次（精确界，非连续松弛）。"""
    for pool_size in (7, 20, 33, 100, 990):
        pool = _pool(pool_size)
        config = _config()
        slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
        rounds = math.ceil(pool_size / slots)
        first, _ = _sweep(pool, config, rounds)
        assert set(first) == {member.object_id for member in pool}, (pool_size, slots, rounds)
        # 实测最坏间隔（轮）不超过闭式上界 ⌈N ÷ k⌉，换算成天亦不超过上界函数。
        assert max(first.values()) < rounds
        measured = rounds / MEASURED_DAILY_DECISION_POINTS
        bound = rotation_worst_case_wait_days(pool_size, slots, MEASURED_DAILY_DECISION_POINTS, 1.0)
        assert measured <= bound


def test_bound_holds_per_partition_regardless_of_other_partitions() -> None:
    """界按分区成立：两个分区各按自己的上次呈现时刻轮转，互不共享偏移量；一个分区呈现了多少对象（哪怕整池刚刚
    呈现过）都不改变另一个分区的换新名额与覆盖轮数。"""
    config = _config()
    slots = rotation_quota_slots(config.presentation_limit, config.quota_ratio)
    small, large = _pool(45, offset=0), _pool(130, offset=500)
    rounds_small, rounds_large = math.ceil(len(small) / slots), math.ceil(len(large) / slots)
    alone_small, alone_quotas = _sweep(small, config, rounds_small)
    busy = {member.object_id: config.now for member in large}
    together, quotas = _sweep(small, config, rounds_small, last=busy)
    assert set(alone_small) == set(together) == {member.object_id for member in small}
    assert (alone_small, alone_quotas) == (together, quotas)
    assert max(together.values()) < rounds_small
    first_large, _ = _sweep(large, config, rounds_large)
    assert set(first_large) == {member.object_id for member in large}
    assert max(first_large.values()) < rounds_large


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
    first, _ = _sweep(pool, config, rounds, last=last)
    assert set(stale) <= set(first)


def test_quota_goes_to_the_longest_unpresented_members_first() -> None:
    """换新名额按上次呈现时刻从早到晚放行：从未呈现最先，其后是最早呈现的；刚呈现过的不占名额。"""
    pool = _pool(12)
    now = 1_000_000
    last = {"ob-000": now - 10 * _DAY, "ob-001": now - 40 * _DAY, "ob-002": now - 20 * _DAY,
            "ob-003": now - 60, "ob-004": now - 8 * _DAY}
    last.update({f"ob-{index:03d}": now - 30 for index in range(5, 11)})
    decision = rotate_candidates(pool, last, _config(now=now))
    # 只有 ob-011 从未呈现；其余长期未呈现者按上次呈现时刻从早到晚：ob-001（40 天）、ob-002（20 天）。
    assert decision.rotated_in == ("ob-011", "ob-001", "ob-002")
    assert [item.object_id for item in staleness_order(pool, last, now, _STALE)] == [
        "ob-011", "ob-001", "ob-002", "ob-000", "ob-004"]
    assert "ob-003" not in decision.rotated_in  # 刚呈现过，不占换新名额
    after = {**last, **{object_id: now for object_id in decision.rotated_in}}
    assert rotate_candidates(pool, after, _config(now=now + _STEP)).rotated_in == ("ob-000", "ob-004")


def test_equally_stale_members_are_released_in_order_key_order() -> None:
    """同样久未呈现（同一时刻或都从未呈现）的成员按排序键放行，不按对象标识的字母序。"""
    pool = [_member(index, f"kind-{index}", key=(1, 0, 0, digest))
            for index, digest in enumerate(("f3", "0a", "c9", "5e", "81"))]
    decision = rotate_candidates(pool, {}, _config())
    assert decision.rotated_in == ("ob-001", "ob-003", "ob-004")
    same = {member.object_id: 100 for member in pool}
    assert rotate_candidates(pool, same, _config()).rotated_in == ("ob-001", "ob-003", "ob-004")


def test_rotation_has_no_cursor_and_depends_only_on_the_stamps() -> None:
    """不设游标：返回体没有游标字段，同一池、同一组上次呈现时刻与同一时刻给出同一结果；只改上次呈现时刻，
    换新名额随之移到下一批最久未呈现的对象。"""
    assert "next_cursor" not in {item.name for item in fields(RotationDecision)}
    pool = _pool(40)
    config = _config()
    first = rotate_candidates(pool, {}, config)
    assert first == rotate_candidates(pool, {}, config)
    stamped = {object_id: config.now for object_id, _ in first.presented}
    second = rotate_candidates(pool, stamped, replace(config, now=config.now + _STEP))
    assert set(second.rotated_in).isdisjoint(object_id for object_id, _ in first.presented)


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


def test_quota_slots_derive_from_scenario_constants_never_hardcoded() -> None:
    """换新名额必须由（单次上限 × 比例）导出：默认给 3，且恒在 [1, 上限] 内。"""
    assert rotation_quota_slots(20, 0.15) == 3
    assert rotation_quota_slots(20, 1.0) == 20
    assert rotation_quota_slots(20, 0.001) == 1  # 名额不得为零，否则有些对象永远不出现
    assert rotation_quota_slots(2, 0.15) == 1


def test_rotation_criterion_is_last_presented_at_not_exposure_count() -> None:
    """判据是「上次呈现时刻」：互动计数取任意值都不改变轮转结果。"""
    base = [_CountedMember(object_id=f"ob-{index:03d}", exposure_count=index, order_key=_key(index))
            for index in range(30)]
    # 只改互动计数，池成员的其余字段逐一相同；久未呈现者是计数最低的那个。
    hot = [replace(member, exposure_count=100_000) for member in base]
    cold = [replace(member, exposure_count=0) for member in base]
    last = {member.object_id: 999_999 for member in base}
    last["ob-000"] = 1_000_000 - 30 * _DAY
    first = rotate_candidates([m.as_member() for m in base], last, _config())
    second = rotate_candidates([m.as_member() for m in hot], last, _config())
    third = rotate_candidates([m.as_member() for m in cold], last, _config())
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
    decision = rotate_candidates(pool, last, _config(now=now))
    assert set(decision.rotated_in) == {"ob-000", "ob-004", "ob-005"}
    assert "ob-003" not in decision.rotated_in  # 刚呈现过，不占换新名额
    after = rotate_candidates(pool, {**last, "ob-000": now, "ob-004": now, "ob-005": now}, _config(now=now))
    assert after.rotated_in == ("ob-002", "ob-001")  # 越过阈值者按上次呈现时刻从早到晚


def test_quota_is_capped_by_subtype_count_only_not_by_tag_similarity() -> None:
    """换新名额只受子类条数上限约束，不做标签相似度去重（否则最久未呈现者会被同题材的另一对象挡下，`⌈N ÷ k⌉` 的界
    不再精确）：三个同子类、同一组标签的从未呈现对象一起拿到三个换新名额；标签相似度去重只作用于按排序键取的其余名额。"""
    now = 1_000_000
    similar = [_member(index, "film", tags=("comedy", "romance")) for index in range(3)]
    fresh = [_member(index, f"kind-{index}") for index in range(3, 12)]
    decision = rotate_candidates([*similar, *fresh], {member.object_id: now - 60 for member in fresh},
                                 _config(now=now))
    assert decision.rotated_in == ("ob-000", "ob-001", "ob-002")
    assert [object_id for object_id, rotated in decision.presented if rotated] == ["ob-000", "ob-001", "ob-002"]
    assert len(decision.presented) == 12 and not decision.deferred


def test_rotation_is_non_destructive_and_defers_without_delisting() -> None:
    """非破坏性：轮转前后池内容逐字节相同；未呈现者只说 ROTATION_DEFERRED，无下架语义。"""
    pool = _pool(60)
    before = copy.deepcopy(pool)
    config = _config()
    decision = rotate_candidates(pool, {}, config)
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
        decision = rotate_candidates(_pool(size), {}, _config())
        assert len(decision.presented) <= _LIMIT
    # 子类足够多时窗口填满到上限；子类不足时窗口受反塌缩容量约束而变短。
    assert len(rotate_candidates(_pool(500), {}, _config()).presented) == _LIMIT
    assert subtype_capacity(5) == 15
    assert subtype_capacity(0) == 0
    # 全部名额都给换新时窗口同样是上限，且清一色来自换新名额。
    all_rotated = rotate_candidates(_pool(500), {}, _config(ratio=1.0))
    assert len(all_rotated.presented) == _LIMIT
    assert len(all_rotated.rotated_in) == _LIMIT
    # 池只有四个子类时，反塌缩容量 12 比单次上限 20 更紧，窗口因此是 12。
    narrow = rotate_candidates(_pool(500, subtypes=4), {}, _config())
    assert len(narrow.presented) == subtype_capacity(4) == 12


def test_subclass_cap_in_top_window() -> None:
    """反塌缩：同一子类在呈现窗口内不超过 3 条；换新名额同样受子类条数上限约束。"""
    pool = _members(30, "drama")
    decision = rotate_candidates(pool, {}, _config())
    assert len(decision.presented) == ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert len(decision.deferred) == 27
    assert not decision.capped_total  # 换新名额先占满窗口，没有别的子类可取而顺延

    multi = pool + [_member(100 + index, f"kind-{index}") for index in range(30)]
    for ratio, expected in ((0.15, _LIMIT), (0.5, _LIMIT)):
        decision = rotate_candidates(multi, {}, _config(ratio=ratio))
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
    decision = rotate_candidates(pool, {}, _config())
    counts = subtype_cap_counts([by_id[object_id] for object_id, _ in decision.presented])
    assert sum(counts.values()) == _LIMIT  # 八个子类 × 上限 3 = 24，够填满 20 条窗口
    assert max(counts.values()) <= ROTATION_SUBTYPE_CAP_IN_WINDOW

    all_recent = {member.object_id: 1_000_000 for member in pool}
    ranked = rotate_candidates(pool, all_recent, _config())
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


def test_empty_pool_returns_nothing() -> None:
    """空池合法：不呈现、不顺延、没有换新名额，不抛错也不兜底造数据。"""
    decision = rotate_candidates([], {}, _config())
    assert decision.presented == () and decision.deferred == ()
    assert decision.rotated_in == () and decision.capped_total == 0
    assert staleness_order([], {}, 0, _STALE) == []


def test_missing_tags_do_not_produce_a_similarity_signal() -> None:
    """标签为空时不产生相似度：去重只能依据子类计数，不能凭空判同。"""
    pool = [_member(index, "news", tags=(), key=(0, -(1000 - index) * 10, 0, f"{index:03d}"))
            for index in range(10)]
    pool += [_member(100 + index, f"kind-{index}") for index in range(10)]
    decision = rotate_candidates(pool, {}, _config())
    by_id = {member.object_id: member for member in pool}
    kept = [by_id[object_id] for object_id, _ in decision.presented]
    # 无标签的新闻条目同样受子类上限约束：窗口里只留三条，其余顺延且留痕。
    assert subtype_cap_counts(kept)["news"] == ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert decision.capped_counts["news"] == len(pool) // 2 - ROTATION_SUBTYPE_CAP_IN_WINDOW
    assert decision.capped_total == decision.capped_counts["news"]
    assert decision.rotated_in


def test_rotation_is_deterministic_across_runs() -> None:
    """同输入同输出：两次调用逐字节相同，只由输入决定。"""
    pool = _pool(120)
    last = {f"ob-{index:03d}": 1_000_000 - index * _DAY for index in range(0, 120, 7)}
    first = rotate_candidates(pool, last, _config())
    second = rotate_candidates(pool, last, _config())
    assert first == second
