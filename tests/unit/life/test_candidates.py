"""@brief 商品候选使用世界事实准入，相关度只排序，哈希决定稳定次序。"""

from dataclasses import asdict, replace
import json
from typing import get_args

import pytest

from worldmock.life.candidates import (
    CANDIDATE_KINDS, CANDIDATE_SOURCES, EXCLUSION_REASONS, CandidateEntry, CandidateInputs,
    CandidateSource, LocationView,
    cursor_identity, generate_candidates, recall, tiebreak_digest,
)


def candidate_inputs(entries=(), **changes):
    """@brief 构造不依赖完整引擎的真实候选输入。"""
    home = LocationView("home", frozenset({"home"}))
    inputs = CandidateInputs(location=home, locations={"home": home}, sources={"on_site": entries},
                             current_time=100, window_end_time=2000, scenario_id="fixture", agent_id=1,
                             local_date="2026-10-07", state_version=3, catalog_digest="catalog-current")
    return replace(inputs, **changes)


def test_candidates_have_only_current_product_kind():
    assert CANDIDATE_KINDS == ("product",)
    assert CANDIDATE_SOURCES == get_args(CandidateSource) == ("on_site", "activated")
    assert EXCLUSION_REASONS == ("ELIGIBILITY_LOCATION_ABSENT", "ELIGIBILITY_LOCATION_KIND_MISMATCH",
                                "ELIGIBILITY_RESOURCE_CONFLICT", "ELIGIBILITY_DURATION_OUT_OF_RANGE",
                                "ELIGIBILITY_DISABLED", "ROTATION_DEFERRED")


def test_all_actual_kinds_are_retained_without_occupancy():
    entries = tuple(CandidateEntry(kind, kind) for kind in CANDIDATE_KINDS)
    page = recall(candidate_inputs(entries))
    assert len(page.generated) == len(page.retained) == page.total == 1
    assert page.excluded == ()
    assert {row.kind for row in page.retained} == set(CANDIDATE_KINDS)


@pytest.mark.parametrize("entry,changes,reason", (
    (CandidateEntry("work", "product", required_location_id="office"), {}, "ELIGIBILITY_LOCATION_ABSENT"),
    (CandidateEntry("sport", "product", location_kinds=frozenset({"fitness"})), {},
     "ELIGIBILITY_LOCATION_KIND_MISMATCH"),
    (CandidateEntry("meal", "product", enabled=False), {}, "ELIGIBILITY_DISABLED"),
    (CandidateEntry("sport", "product", minimum_seconds=1500), {"window_end_time": 1000},
     "ELIGIBILITY_DURATION_OUT_OF_RANGE"),
))
def test_actual_world_constraints_are_reported(entry, changes, reason):
    page = recall(candidate_inputs((entry,), **changes))
    assert page.retained == () and len(page.excluded) == 1
    assert page.excluded[0].reason == reason
    assert sum(page.exclusion_counts.values()) == len(page.generated)


def test_zero_second_purchase_candidates_do_not_occupy_activity_resources():
    entry = CandidateEntry("meal", "product")
    assert recall(candidate_inputs((entry,), occupied_resources=frozenset({"use_app"}))).total == 1
    assert recall(candidate_inputs((entry,), occupied_resources=frozenset({"work", "social", "exercise", "use_app", "travel"}))).total == 1


def test_generation_merges_actual_sources_without_duplicates():
    entry = CandidateEntry("meal", "product")
    inputs = candidate_inputs(sources={"on_site": (entry,), "activated": (entry,)})
    generated = generate_candidates(inputs)
    assert len(generated) == 1 and generated[0].sources == ("on_site", "activated")
    page = recall(inputs)
    assert page.source_counts == {"on_site": 1, "activated": 1} and page.total == 1


def test_relevance_changes_order_without_changing_membership():
    entries = tuple(CandidateEntry(identity, "product") for identity in ("first", "last"))
    neutral = recall(candidate_inputs(entries))
    changed = recall(candidate_inputs(entries, relevance={"last": 2}))
    assert changed.retained[0].object_id == "last"
    assert {row.object_id for row in neutral.retained} == {row.object_id for row in changed.retained}
    assert changed.excluded == neutral.excluded == ()


def test_rotation_deferred_objects_remain_eligible():
    entries = tuple(CandidateEntry(identity, "product") for identity in ("first", "last"))
    page = recall(candidate_inputs(entries, withheld_object_ids=frozenset({"last"})))
    assert page.total == 2 and page.returned == 1 and page.deferred == ("last",)
    assert page.excluded == ()


def test_candidates_and_cursor_are_deterministic():
    entries = tuple(CandidateEntry(identity, "product") for identity in ("first", "last", "middle"))
    inputs = candidate_inputs(entries, presentation_limit=1)
    first, second = recall(inputs), recall(inputs)
    assert json.dumps(asdict(first), sort_keys=True, default=sorted) == json.dumps(
        asdict(second), sort_keys=True, default=sorted)
    assert first.next_cursor is not None
    assert cursor_identity(inputs, "first") != cursor_identity(replace(inputs, state_version=4), "first")
    assert tiebreak_digest("fixture", 1, "2026-10-07", "first") == first.retained[
        next(i for i, row in enumerate(first.retained) if row.object_id == "first")].tiebreak
