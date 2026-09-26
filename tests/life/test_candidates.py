"""统一候选契约三段式：生成、资格、排序与两条排除纪律（docs/dev/SPEC-life-runtime.md「候选契约与呈现规则」）。

覆盖四类断言：三段形状与返回体一致、同一输入逐字节确定、软偏好只改序不出局、
被排除项必带闭集内原因码且计数守恒。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from worldmock.life.candidates import (
    CANDIDATE_KINDS,
    CANDIDATE_SOURCES,
    CANDIDATE_WINDOW_SECONDS,
    EXCLUSION_REASONS,
    FIXED_POINT_SCALE,
    ORDER_KEY_VERSION,
    CandidateEntry,
    CandidateInputs,
    ExclusionReason,
    LocationView,
    recall,
    cursor_identity,
    exclusion_counts,
    filter_candidates,
    fixed_point,
    generate_candidates,
    source_counts,
)
from worldmock.life.scenario import BodyConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "src" / "worldmock" / "life" / "candidates.py"

#: 与合同逐字核对过的闭集（本测试自己写死，改动 token 即失败）。
CONTRACT_KINDS = ("product", "lifestyle", "content", "game", "task")
CONTRACT_SOURCES = ("on_site", "browsed", "carried", "activated")
CONTRACT_REASONS = (
    "ELIGIBILITY_LOCATION_ABSENT",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",
    "ELIGIBILITY_CLOSED_HOURS",
    "ELIGIBILITY_OPENING_UNKNOWN",
    "ELIGIBILITY_OUT_OF_STOCK",
    "ELIGIBILITY_DELISTED",
    "ELIGIBILITY_RESOURCE_CONFLICT",
    "ELIGIBILITY_ENERGY_INSUFFICIENT",
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",
    "ELIGIBILITY_DISABLED",
    "ROTATION_DEFERRED",
)


def _dump(value):
    """把返回体折成可 JSON 编码、可逐字节比较的形态（slots 数据类逐字段取）。"""
    if dataclasses.is_dataclass(value):
        return {item.name: _dump(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted(str(item) for item in value)
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _frame(page) -> str:
    """返回体的规范 JSON 文本：同输入两次取候选必须逐字节相同。"""
    return json.dumps(_dump(page), sort_keys=True)


def _body() -> BodyConfig:
    """与正式场景同形的身体配置（能量成本函数的最小完整声明）。"""
    return BodyConfig.model_validate({
        "mass_kg": 65, "initial_energy_kcal": 1600, "maximum_energy_kcal": 2400,
        "initial_sleep_debt_hours": 0,
        "basal_kcal_per_hour": 65, "awake_sleep_debt_per_hour": 0.125,
        "sleep_recovery_per_hour": 0.125, "entertainment_need_per_awake_hour": 0.02,
        "hunger_threshold_kcal": 800, "emergency_threshold_kcal": 400,
        "work_efficiency_factors": [
            {"input_resource": "sleep_debt_hours",
             "curve": [{"input_value": 0, "output_value": 1.0},
                       {"input_value": 16, "output_value": 0.5}]},
            {"input_resource": "energy_kcal",
             "curve": [{"input_value": 0, "output_value": 0.5},
                       {"input_value": 400, "output_value": 0.8},
                       {"input_value": 800, "output_value": 1.0}]}],
        "food_efficiency_curve": [{"input_value": 0, "output_value": 1.0},
                                  {"input_value": 16, "output_value": 0.5}],
        "activity_met": {"wait": 1.0, "sleep": 1.0, "work": 1.5, "watch": 1.2,
                         "play_game": 1.3, "travel:driving": 1.5, "travel:walking": 2.5,
                         "read_mail": 1.1, "browse": 1.2, "consume": 1.3,
                         "lifestyle:walking-trip": 3.0},
        "entertainment_relief_per_hour": {"watch": 0.15, "play_game": 0.2},
        "work_notes_multiplier": 1.2, "source_kind": "test"})


def _inputs(**overrides) -> CandidateInputs:
    """一份五类齐全的基准输入：四个来源各带条目，全部默认可行。"""
    defaults = dict(
        location=LocationView(location_id="market",
                              location_kinds=frozenset({"outdoor", "venue"}),
                              opening_status="open",
                              minimum_seconds=60, maximum_seconds=14_400),
        locations={},
        sources={
            "on_site": (CandidateEntry(object_id="food-1", kind="product",
                                       minimum_seconds=600, maximum_seconds=3600),),
            "browsed": (CandidateEntry(object_id="movie-0001", kind="content", bucket="feature_film",
                                       minimum_seconds=600, maximum_seconds=3600),),
            "carried": (CandidateEntry(object_id="trip-1", kind="lifestyle",
                                       minimum_seconds=600, maximum_seconds=3600),),
            "activated": (CandidateEntry(object_id="task-1", kind="task",
                                         minimum_seconds=600, maximum_seconds=3600),
                          CandidateEntry(object_id="game-1", kind="game",
                                         minimum_seconds=600, maximum_seconds=3600)),
        },
        occupied_resources=frozenset(), local_hour=12.0, current_time=1_700_000_000,
        window_end_time=1_700_000_000 + CANDIDATE_WINDOW_SECONDS,
        energy_kcal=1600.0, body=_body(),
        taste_weights={"food-1": 0.5, "movie-0001": 0.25, "trip-1": 0.75,
                       "game-1": 0.1, "task-1": 0.9},
        attention_priors={"food-1": 0.4, "feature_film": 0.8, "trip-1": 0.3,
                          "game-1": 0.6, "task-1": 0.2},
        presentation_limit=20, state_version=7,
        catalog_digest="a" * 64, profile_digest="b" * 64)
    defaults.update(overrides)
    return CandidateInputs(**defaults)


# ---------------------------------------------------------------------------
# 闭集：与合同逐字一致
# ---------------------------------------------------------------------------

class TestClosedSets:
    """候选类别、来源与排除原因码必须与合同逐字一致。"""

    def test_literal_members_match_contract(self) -> None:
        assert tuple(CANDIDATE_KINDS) == CONTRACT_KINDS
        assert tuple(CANDIDATE_SOURCES) == CONTRACT_SOURCES
        assert tuple(EXCLUSION_REASONS) == CONTRACT_REASONS

    def test_exclusion_reason_literal_equals_enumerated_tuple(self) -> None:
        from typing import get_args as literal_args

        assert literal_args(ExclusionReason) == CONTRACT_REASONS
        assert tuple(EXCLUSION_REASONS) == literal_args(ExclusionReason)

    def test_order_key_version_is_int(self) -> None:
        assert isinstance(ORDER_KEY_VERSION, int)
        assert FIXED_POINT_SCALE == 10_000


# ---------------------------------------------------------------------------
# 三段形状与返回体一致
# ---------------------------------------------------------------------------

class TestThreeStageShape:
    """五类对象共用一套三段式，返回体形状一致且分段证据齐备。"""

    def test_return_body_exposes_all_three_stages(self) -> None:
        page = recall(_inputs())
        assert page.source_counts
        assert set(page.source_counts) == set(CONTRACT_SOURCES)
        assert sum(page.source_counts.values()) >= len(page.generated)
        assert page.retained and page.excluded == ()
        assert page.total == len(page.retained)
        assert page.returned == len(page.retained)
        assert page.order_key_version == ORDER_KEY_VERSION

    def test_every_kind_reaches_ordered_candidates(self) -> None:
        page = recall(_inputs())
        kinds = {item.kind for item in page.retained}
        assert kinds == set(CONTRACT_KINDS)

    @pytest.mark.parametrize("kind", CONTRACT_KINDS)
    def test_single_kind_input_yields_same_top_level_keys(self, kind: str) -> None:
        inputs = _inputs(sources={source: tuple(item for item in entries if item.kind == kind)
                                  for source, entries in _inputs().sources.items()})
        page = recall(inputs)
        keys = {item.name for item in dataclasses.fields(page)}
        assert keys == {item.name for item in dataclasses.fields(recall(_inputs()))}
        assert [item.kind for item in page.retained] == [kind]
        assert sum(page.source_counts.values()) == 1

    def test_generated_counts_candidates_without_eligible_restriction(self) -> None:
        inputs = _inputs(sources={"on_site": (
            CandidateEntry(object_id="food-1", kind="product", stock=0,
                           minimum_seconds=600, maximum_seconds=3600),)})
        generated = generate_candidates(inputs)
        assert [item.entry.object_id for item in generated] == ["food-1"]
        assert source_counts(generated) == {"on_site": 1, "browsed": 0, "carried": 0,
                                           "activated": 0}

    def test_object_hit_by_two_sources_is_one_candidate_with_two_sources(self) -> None:
        entry = CandidateEntry(object_id="food-1", kind="product",
                               minimum_seconds=600, maximum_seconds=3600)
        inputs = _inputs(sources={"on_site": (entry,), "carried": (entry,)})
        generated = generate_candidates(inputs)
        assert len(generated) == 1
        assert generated[0].sources == ("on_site", "carried")

    def test_filter_returns_retained_and_excluded_separately(self) -> None:
        inputs = _inputs(sources={"on_site": (
            CandidateEntry(object_id="food-1", kind="product",
                           minimum_seconds=600, maximum_seconds=3600),
            CandidateEntry(object_id="food-2", kind="product", stock=0,
                           minimum_seconds=600, maximum_seconds=3600))})
        retained, excluded = filter_candidates(generate_candidates(inputs), inputs)
        assert [item.entry.object_id for item in retained] == ["food-1"]
        assert [(item.object_id, item.reason) for item in excluded] == [
            ("food-2", "ELIGIBILITY_OUT_OF_STOCK")]
        assert excluded[0].as_row() == {"object_id": "food-2", "kind": "product",
                                        "reason": "ELIGIBILITY_OUT_OF_STOCK"}


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------

class TestDeterminism:
    """候选是派生量：同输入逐字节相同，且不依赖墙钟、缓存或哈希种子。"""

    def test_same_input_twice_is_byte_identical(self) -> None:
        assert _frame(recall(_inputs())) == _frame(recall(_inputs()))

    def test_wall_clock_does_not_enter_the_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = _frame(recall(_inputs()))
        monkeypatch.setattr("time.time", lambda: 999_999_999.0)
        assert _frame(recall(_inputs())) == before

    def test_hash_seed_does_not_change_the_result(self) -> None:
        # 子进程必须导入本检出的 src（PYTHONPATH 用平台分隔符）；否则会回落到已安装的另一份 worldmock。
        script = (
            "import worldmock.life.candidates as module;"
            "from tests.life.test_candidates import _frame, _inputs;"
            "print(module.__file__);"
            "print(_frame(module.recall(_inputs())))")
        digests = []
        for seed in ("0", "31337"):
            completed = subprocess.run([sys.executable, "-c", script],
                                       cwd=PROJECT_ROOT, capture_output=True, text=True,
                                       env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin",
                                            "PYTHONPATH": os.pathsep.join(("src", "."))})
            assert completed.returncode == 0, completed.stderr
            module_file, digest = completed.stdout.strip().splitlines()
            assert Path(module_file).resolve() == MODULE_PATH.resolve()
            digests.append(digest)
        assert digests[0] == digests[1] != ""

    def test_module_uses_hashlib_and_never_the_builtin_hash(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        builtins = {node.func.id for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert "hash" not in builtins
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        assert "hashlib" in modules

    def test_module_does_not_read_the_clock_or_randomness(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not modules & {"time", "datetime", "random", "os"}
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id in {"open", "random", "input"}
                       for node in ast.walk(tree))

    def test_cursor_identity_covers_the_four_declared_terms(self) -> None:
        base = _inputs()
        baseline = cursor_identity(base, "k")
        assert cursor_identity(base, "k") == baseline
        assert cursor_identity(_inputs(state_version=8), "k") != baseline
        assert cursor_identity(_inputs(catalog_digest="c" * 64), "k") != baseline
        assert cursor_identity(base, "other") != baseline

    def test_order_key_is_a_total_order_and_a_cursor_source(self) -> None:
        inputs = _inputs(presentation_limit=2)
        page = recall(inputs)
        assert page.returned == 2
        assert page.next_cursor is not None
        small = recall(_inputs(presentation_limit=20))
        assert small.next_cursor is None

    def test_cursor_is_the_full_ordering_key_of_the_last_row(self) -> None:
        inputs = _inputs(presentation_limit=1)
        page = recall(inputs)
        expected = cursor_identity(inputs, json.dumps(list(page.retained[0].order_key()),
                                                      separators=(",", ":")))
        assert page.next_cursor == expected


# ---------------------------------------------------------------------------
# 软偏好只改序，不出局
# ---------------------------------------------------------------------------

class TestSoftPreferenceOnlyReorders:
    """口味权重与注意力先验只参与排序键，绝不作为入选条件。"""

    def test_zero_taste_weight_entries_survive_and_rank_last(self) -> None:
        entries = tuple(CandidateEntry(object_id=f"food-{index}", kind="product",
                                       minimum_seconds=600, maximum_seconds=3600)
                        for index in range(4))
        inputs = _inputs(sources={"on_site": entries},
                         taste_weights={"food-0": 0.5, "food-1": 0.25},
                         attention_priors={"food-0": 0.1, "food-1": 0.1})
        page = recall(inputs)
        ids = [item.object_id for item in page.retained]
        assert sorted(ids) == [f"food-{index}" for index in range(4)]
        assert set(ids[:2]) == {"food-0", "food-1"}
        assert ids[2:] == ["food-2", "food-3"]
        assert page.excluded == ()

    def test_zero_weight_sorts_after_every_non_zero_weight(self) -> None:
        entries = (
            CandidateEntry(object_id="food-high-attention", kind="product",
                           minimum_seconds=600, maximum_seconds=3600),
            CandidateEntry(object_id="food-zero-taste-huge-attention", kind="product",
                           minimum_seconds=600, maximum_seconds=3600),)
        inputs = _inputs(
            sources={"on_site": entries},
            taste_weights={"food-high-attention": 0.0001},
            attention_priors={"food-high-attention": 0.0001,
                              "food-zero-taste-huge-attention": 1.0})
        page = recall(inputs)
        assert [item.object_id for item in page.retained] == [
            "food-high-attention", "food-zero-taste-huge-attention"]
        assert page.retained[1].taste_weight == 0

    def test_flat_attention_keeps_every_entry_of_the_pool(self) -> None:
        entries = tuple(CandidateEntry(object_id=f"movie-{index:04d}", kind="content",
                                       bucket=f"kind-{index}", minimum_seconds=600,
                                       maximum_seconds=3600)
                        for index in range(6))
        inputs = _inputs(sources={"browsed": entries}, taste_weights={},
                         attention_priors={f"kind-{index}": 0.0 for index in range(6)})
        page = recall(inputs)
        assert len(page.retained) == 6
        assert all(item.taste_weight == 0 and item.attention_prior == 0
                   for item in page.retained)

    def test_missing_taste_entries_are_not_dropped(self) -> None:
        inputs = _inputs(taste_weights={}, attention_priors={})
        page = recall(inputs)
        assert len(page.retained) == 5
        assert sorted(item.kind for item in page.retained) == sorted(CONTRACT_KINDS)


# ---------------------------------------------------------------------------
# 每条排除都带原因码，且计数守恒
# ---------------------------------------------------------------------------

class TestExclusionDiscipline:
    """每个被排除项携带闭集内原因码；保留项计数加排除项计数等于候选全集规模。"""

    @pytest.mark.parametrize(("entry", "reason"), [
        (CandidateEntry(object_id="a", kind="product", required_location_id="office",
                        minimum_seconds=600, maximum_seconds=3600),
         "ELIGIBILITY_LOCATION_ABSENT"),
        (CandidateEntry(object_id="b", kind="content",
                        location_kinds=frozenset({"fitness"}),
                        minimum_seconds=600, maximum_seconds=3600),
         "ELIGIBILITY_LOCATION_KIND_MISMATCH"),
        (CandidateEntry(object_id="c", kind="product", stock=0,
                        minimum_seconds=600, maximum_seconds=3600),
         "ELIGIBILITY_OUT_OF_STOCK"),
        (CandidateEntry(object_id="d", kind="product", retired=True,
                        minimum_seconds=600, maximum_seconds=3600),
         "ELIGIBILITY_DELISTED"),
        (CandidateEntry(object_id="e", kind="lifestyle", enabled=False,
                        minimum_seconds=600, maximum_seconds=3600),
         "ELIGIBILITY_DISABLED"),
        (CandidateEntry(object_id="f", kind="task", minimum_seconds=600,
                        maximum_seconds=60),
         "ELIGIBILITY_DURATION_OUT_OF_RANGE"),
    ])
    def test_world_fact_exclusions_carry_their_code(self, entry: CandidateEntry,
                                                    reason: str) -> None:
        inputs = _inputs(sources={"on_site": (entry,)},
                         window_end_time=1_700_000_000 + CANDIDATE_WINDOW_SECONDS)
        page = recall(inputs)
        assert [item.reason for item in page.excluded] == [reason]
        assert page.retained == ()

    def test_closed_hours_excludes_product_at_a_shut_merchant(self) -> None:
        page = recall(_inputs(local_hour=3.0, location=dataclasses.replace(
            _inputs().location, opening_status="closed")))
        assert page.retained == ()
        assert {item.reason for item in page.excluded} == {"ELIGIBILITY_CLOSED_HOURS"}

    def test_resource_conflict_is_reachable(self) -> None:
        busy = recall(_inputs(occupied_resources=frozenset({"position", "attention", "device", "hands"})))
        assert busy.retained == ()
        assert {item.reason for item in busy.excluded} == {"ELIGIBILITY_RESOURCE_CONFLICT"}

    def test_energy_insufficiency_is_reachable(self) -> None:
        long_entry = CandidateEntry(object_id="movie-long", kind="content",
                                    minimum_seconds=3600, maximum_seconds=14_400)
        drained = recall(_inputs(sources={"browsed": (long_entry,)}, energy_kcal=0.0))
        assert drained.retained == ()
        assert {item.reason for item in drained.excluded} == {
            "ELIGIBILITY_ENERGY_INSUFFICIENT"}

    def test_lifestyle_met_comes_from_the_entry_and_gates_energy(self) -> None:
        entry = CandidateEntry(object_id="trip-1", kind="lifestyle", met=3.0,
                               minimum_seconds=3600, maximum_seconds=14_400)
        drained = recall(_inputs(sources={"carried": (entry,)}, energy_kcal=0.0))
        assert {item.reason for item in drained.excluded} == {
            "ELIGIBILITY_ENERGY_INSUFFICIENT"}

    def test_abundant_energy_does_not_exclude_a_long_candidate(self) -> None:
        entry = CandidateEntry(object_id="movie-long", kind="content",
                               minimum_seconds=3600, maximum_seconds=14_400)
        page = recall(_inputs(sources={"browsed": (entry,)}, energy_kcal=1600.0))
        assert [item.object_id for item in page.retained] == ["movie-long"]

    def test_business_hours_are_judged_at_the_entry_own_place(self) -> None:
        entry = CandidateEntry(object_id="food-1", kind="product",
                               required_location_id="night-shop",
                               minimum_seconds=600, maximum_seconds=3600)
        inputs = _inputs(sources={"on_site": (entry,)}, local_hour=23.5,
                         location=LocationView(location_id="night-shop",
                                               location_kinds=frozenset({"venue"}),
                                               opening_status="open"),
                         locations={"night-shop": LocationView(
                             location_id="night-shop", location_kinds=frozenset({"venue"}),
                             opening_status="open")})
        assert [item.object_id for item in recall(inputs).retained] == ["food-1"]
        shut = _inputs(sources={"on_site": (entry,)}, local_hour=3.0,
                       location=LocationView(location_id="night-shop",
                                             location_kinds=frozenset({"venue"}),
                                             opening_status="closed"),
                       locations={"night-shop": LocationView(
                           location_id="night-shop", location_kinds=frozenset({"venue"}),
                           opening_status="closed")})
        assert {item.reason for item in recall(shut).excluded} == {
            "ELIGIBILITY_CLOSED_HOURS"}

    def test_entry_place_declared_duration_caps_the_candidate(self) -> None:
        entry = CandidateEntry(object_id="movie-long", kind="content",
                               required_location_id="home",
                               minimum_seconds=600, maximum_seconds=3600)
        place = LocationView(location_id="home", location_kinds=frozenset({"home"}),
                             opening_status="open",
                             minimum_seconds=60, maximum_seconds=300)
        inputs = _inputs(sources={"browsed": (entry,)}, location=place,
                         locations={"home": place})
        assert {item.reason for item in recall(inputs).excluded} == {
            "ELIGIBILITY_DURATION_OUT_OF_RANGE"}
        roomy = _inputs(sources={"browsed": (entry,)}, location=place,
                        locations={"home": LocationView(
                            location_id="home", location_kinds=frozenset({"home"}),
                            opening_status="open",
                            minimum_seconds=60, maximum_seconds=7200)})
        assert [item.object_id for item in recall(roomy).retained] == ["movie-long"]

    def test_energy_recovering_kind_is_never_excluded_for_energy(self) -> None:
        # 进食与睡眠按域内口径是能量恢复活动，能量不构成它们的排除理由。
        entry = CandidateEntry(object_id="food-1", kind="product",
                               minimum_seconds=3600, maximum_seconds=14_400)
        page = recall(_inputs(sources={"on_site": (entry,)}, energy_kcal=0.0))
        assert [item.object_id for item in page.retained] == ["food-1"]

    def test_missing_location_excludes_everything_with_a_stable_code(self) -> None:
        page = recall(_inputs(location=None))
        assert page.retained == ()
        assert {item.reason for item in page.excluded} == {"ELIGIBILITY_LOCATION_ABSENT"}

    def test_anywhere_only_waives_activity_location_requirement(self) -> None:
        """活动侧 anywhere 可用；地点缺失环境或滥用通配符不能解除限制。"""
        entry = CandidateEntry(object_id="task-any", kind="task", location_kinds=frozenset({"anywhere"}),
                               minimum_seconds=600, maximum_seconds=3600)
        kept = recall(_inputs(sources={"on_site": (entry,)}))
        assert [item.object_id for item in kept.retained] == ["task-any"]
        assert kept.excluded == ()
        for kinds in (frozenset(), frozenset({"anywhere"})):
            invalid = LocationView("invalid", kinds, opening_status="open")
            rejected = recall(_inputs(sources={"on_site": (entry,)}, location=invalid))
            assert rejected.retained == ()
            assert {item.reason for item in rejected.excluded} == {"ELIGIBILITY_LOCATION_KIND_MISMATCH"}
        fitness = CandidateEntry(object_id="task-gym", kind="task", location_kinds=frozenset({"fitness"}),
                                 minimum_seconds=600, maximum_seconds=3600)
        rejected = recall(_inputs(sources={"on_site": (fitness,)}))
        assert {item.reason for item in rejected.excluded} == {"ELIGIBILITY_LOCATION_KIND_MISMATCH"}

    def test_every_exclusion_reason_is_inside_the_closed_set(self) -> None:
        inputs = _inputs(location=None, occupied_resources=frozenset({"position", "attention", "device", "hands"}))
        page = recall(inputs)
        assert page.excluded
        for item in page.excluded:
            assert item.reason in CONTRACT_REASONS
        assert set(page.exclusion_counts) == set(CONTRACT_REASONS)

    def test_retained_plus_excluded_equals_the_generated_pool(self) -> None:
        inputs = _inputs(sources={
            "on_site": (CandidateEntry(object_id="food-1", kind="product",
                                       minimum_seconds=600, maximum_seconds=3600),
                        CandidateEntry(object_id="food-2", kind="product", stock=0,
                                       minimum_seconds=600, maximum_seconds=3600)),
            "browsed": (CandidateEntry(object_id="movie-0001", kind="content",
                                       retired=True, minimum_seconds=600,
                                       maximum_seconds=3600),),
            "carried": (CandidateEntry(object_id="trip-1", kind="lifestyle",
                                       enabled=False, minimum_seconds=600,
                                       maximum_seconds=3600),)})
        page = recall(inputs)
        assert len(page.generated) == 4
        assert len(page.retained) + len(page.excluded) == len(page.generated)
        assert sum(page.exclusion_counts.values()) == len(page.excluded)
        assert page.exclusion_counts["ELIGIBILITY_OUT_OF_STOCK"] == 1
        assert page.exclusion_counts["ELIGIBILITY_DELISTED"] == 1
        assert page.exclusion_counts["ELIGIBILITY_DISABLED"] == 1

    def test_exclusion_counts_key_every_closed_reason_even_at_zero(self) -> None:
        counts = exclusion_counts(())
        assert set(counts) == set(CONTRACT_REASONS)
        assert all(value == 0 for value in counts.values())


# ---------------------------------------------------------------------------
# 退役只走墓碑语义
# ---------------------------------------------------------------------------

class TestTombstoneSemantics:
    """目录条目退役保留可读、拒绝新场景引用：不删除条目、不改既有足迹的解释。"""

    def test_delisted_entry_is_excluded_from_candidates_and_still_readable(
            self, tmp_path: Path) -> None:
        """同一份墓碑目录：下架条目在候选里带 `ELIGIBILITY_DELISTED` 出局，在目录里仍可读。"""
        from tests.categories.conftest import raw_object, write_catalog
        from worldmock.catalog import CatalogEntryKind, ObjectDeclaration, add_object, deprecate
        from worldmock.catalog.loader import load_catalog

        directory = write_catalog(tmp_path / "catalog")
        add_object(directory, ObjectDeclaration.model_validate(raw_object("food-soda")))
        deprecate(directory, CatalogEntryKind.OBJECT, "food-soda", "retired")
        catalog = load_catalog(directory)
        assert catalog.tombstoned_ids() == frozenset({"food-soda"})

        # 「保留可读」一侧：墓碑不动条目本身，原始行与解析后的条目都还在
        raw = [json.loads(line) for line in
               (tmp_path / "catalog" / "objects.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "food-soda" in {row["object_id"] for row in raw}
        assert catalog.objects["food-soda"].object_id == "food-soda"
        assert catalog.objects["food-soda"].version == 1

        # 「拒绝新场景引用」一侧在候选契约里的落点：退役是一个排除理由，不是删除
        retired = CandidateEntry(object_id="food-soda", kind="product", retired=True,
                                 minimum_seconds=600, maximum_seconds=3600)
        live = CandidateEntry(object_id="food-milk", kind="product",
                              minimum_seconds=600, maximum_seconds=3600)
        page = recall(_inputs(sources={"on_site": (live, retired)}))
        assert [item.object_id for item in page.retained] == ["food-milk"]
        assert [(item.object_id, item.reason) for item in page.excluded] == [
            ("food-soda", "ELIGIBILITY_DELISTED")]
        assert page.exclusion_counts["ELIGIBILITY_DELISTED"] == 1
        assert len(page.retained) + len(page.excluded) == len(page.generated) == 2
        # 出局的条目仍在目录里可读：排除是候选侧的事，不是目录侧的删除
        assert "food-soda" in catalog.objects

    def test_runtime_has_no_path_that_deletes_a_catalog_entry(self) -> None:
        """契约层与生活层都不含删除条目的通路：退役只追加墓碑。"""
        catalog_modules = (
            "admission.py", "affordances.py", "compiler.py", "inspect.py", "loader.py", "models.py",
        )
        runtime_paths = list((PROJECT_ROOT / "src/worldmock/life").rglob("*.py"))
        runtime_paths.extend(PROJECT_ROOT / "src/worldmock/catalog" / name for name in catalog_modules)
        sources = {path: path.read_text(encoding="utf-8") for path in sorted(runtime_paths)
                   if "__pycache__" not in path.parts}
        assert sources
        for path, source in sources.items():
            tree = ast.parse(source)
            called = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
            assert not {"unlink", "rmtree", "remove"} & called, path
            # 也不存在「下架事件」这类事件类型：退役不等于产生一条删除事件
            assert "entry_deleted" not in source and "object_delisted" not in source, path


# ---------------------------------------------------------------------------
# 轮转推迟不是排除
# ---------------------------------------------------------------------------

class TestRotationDeferralIsNotExclusion:
    """`ROTATION_DEFERRED` 只表示本次名额已满：不改变对象、不写目录、不算排除。"""

    def test_withheld_objects_stay_retained_and_uncounted_as_excluded(self) -> None:
        inputs = _inputs(withheld_object_ids=frozenset({"game-1"}))
        page = recall(inputs)
        assert "game-1" in page.deferred
        assert "game-1" in {item.object_id for item in page.retained}
        assert "game-1" not in {item.object_id for item in page.excluded}
        assert page.exclusion_counts["ROTATION_DEFERRED"] == 0
        assert page.total == len(page.retained)

    def test_deferral_only_leaves_the_presented_head(self) -> None:
        baseline = recall(_inputs())
        withheld = recall(_inputs(withheld_object_ids=frozenset({"task-1"})))
        assert withheld.returned == baseline.returned - 1
        assert withheld.excluded == baseline.excluded
        assert withheld.generated == baseline.generated


# ---------------------------------------------------------------------------
# 排序键
# ---------------------------------------------------------------------------

class TestOrdering:
    """主键是口味 × 注意力先验，决胜键是对象标识，保证全序。"""

    def test_primary_key_is_taste_times_attention(self) -> None:
        entries = (CandidateEntry(object_id="a", kind="product", minimum_seconds=600,
                                  maximum_seconds=3600),
                   CandidateEntry(object_id="b", kind="product", minimum_seconds=600,
                                  maximum_seconds=3600))
        inputs = _inputs(sources={"on_site": entries},
                         taste_weights={"a": 0.2, "b": 0.9},
                         attention_priors={"a": 0.9, "b": 0.2})
        page = recall(inputs)
        assert page.retained[0].taste_weight * page.retained[0].attention_prior == 2000 * 9000
        assert [item.object_id for item in page.retained] == ["a", "b"]

    def test_object_id_breaks_ties(self) -> None:
        entries = tuple(CandidateEntry(object_id=name, kind="content", bucket="feature_film",
                                       minimum_seconds=600, maximum_seconds=3600)
                        for name in ("zeta", "alpha", "mid"))
        inputs = _inputs(sources={"browsed": entries},
                         taste_weights={name: 0.5 for name in ("zeta", "alpha", "mid")},
                         attention_priors={"feature_film": 0.5})
        page = recall(inputs)
        assert [item.object_id for item in page.retained] == ["alpha", "mid", "zeta"]

    def test_order_key_is_json_serialisable_integers_only(self) -> None:
        page = recall(_inputs())
        for item in page.retained:
            key = item.order_key()
            assert all(isinstance(part, (int, str)) for part in key)
            assert json.loads(json.dumps(list(key))) == list(key)

    def test_fixed_point_matches_taste_weight_precision(self) -> None:
        assert fixed_point(0.3333) == 3333
        assert fixed_point(0.1667) == 1667
        assert fixed_point(0.75) == 7500
        assert fixed_point(0.0) == 0
