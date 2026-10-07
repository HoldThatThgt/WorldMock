from __future__ import annotations


from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import worldmock.evaluation.metrics as metrics_module
from worldmock.evaluation.adapters import adapt_human_reference, adapt_labelkit, adapt_worldmock, build_days
from worldmock.evaluation.aggregate_reference import evaluate_official_reference
from worldmock.evaluation.metrics import (
    evaluate_metrics,
    finite_sample_burstiness,
    interval_memory,
    shared_support,
)
from worldmock.evaluation.models import PublicEvidence
from worldmock.evaluation.scenario import load_scenario


FORMAL_SCENARIO = Path(__file__).parent / "fixtures/scenario.json"


def test_required_metrics_run_on_arbitrary_one_day_window(evaluation_fixture) -> None:
    scenario, left, right = _views(evaluation_fixture)

    first = evaluate_metrics(left, right, scenario)
    second = evaluate_metrics(left, right, scenario)

    assert first == second
    assert first["eligible_days"] == 1
    assert first["eligible_blocks"] == 1
    assert set(first["metrics"]) == {
        "week_hour_rhythm",
        "weekday_weekend",
        "activity_volume_duration",
        "burstiness_memory",
        "category_diversity_transition",
        "ten_minute_day_distance",
    }
    assert first["metrics"]["week_hour_rhythm"]["confidence_interval"]["status"] == (
        "insufficient_blocks"
    )


def test_shared_support_uses_observation_semantics_and_reports_denominators(
    evaluation_fixture,
) -> None:
    _, left, right = _views(evaluation_fixture)

    support = shared_support(left, right)

    assert support["comparable"] is True
    assert support["shared_channels"] == ["digital_interaction"]
    assert support["shared_categories"] == ["entertainment"]
    assert support["coverage"]["right"]["private_events_excluded"] == 2
    assert support["coverage"]["left"]["total_activities"] == 1


def test_physical_only_candidate_is_not_comparable(evaluation_fixture) -> None:
    _, left, right = _views(evaluation_fixture)
    activities = tuple(replace(item, channels=("physical_activity",)) for item in right.activities)
    events = tuple(replace(item, channel="physical_activity") for item in right.events)
    physical = replace(right, activities=activities, events=events)

    support = shared_support(left, physical)

    assert support["comparable"] is False
    assert support["shared_channels"] == []
    assert support["shared_categories"] == []


def test_finite_sample_burstiness_and_interval_memory_have_known_limits() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    regular = [base + timedelta(seconds=value) for value in (0, 1, 2, 3)]
    cumulative = [base + timedelta(seconds=value) for value in (0, 1, 3, 6, 10)]

    assert finite_sample_burstiness(regular) == pytest.approx(-1.0)
    assert interval_memory(cumulative) == pytest.approx(1.0)
    assert interval_memory(regular) is None


def test_official_reference_quantizes_actual_segments_without_pause_gap(
    evaluation_fixture,
) -> None:
    scenario, left, right = _views(evaluation_fixture)

    result = evaluate_official_reference(left, right, scenario)
    internet = result["domains"]["internet_use"]

    assert internet["left"]["observed"]["participant_minutes"] == 60
    assert internet["right"]["observed"]["participant_minutes"] == 120
    assert internet["right"]["observed"]["participant_minutes"] != 180
    assert internet["pair_alignment"]["result"] == "right"
    assert result["domains"]["personal_essential"]["left"]["status"] == "unsupported"


def test_zero_elapsed_without_progress_never_counts_wall_clock_as_activity(evaluation_fixture) -> None:
    scenario, _, right = _views(evaluation_fixture)
    activity = replace(right.activities[0], elapsed_seconds=0)

    days = build_days((), (activity,), scenario)

    assert activity.duration_seconds == 0
    assert all(value == "none" for value in days[0].slot_categories)


def test_matching_human_reference_uses_same_date_and_support_mask(evaluation_fixture) -> None:
    scenario, left, right = _views(evaluation_fixture)
    human = adapt_human_reference(evaluation_fixture.human_root, scenario)

    result = evaluate_metrics(left, right, scenario, human)

    assert result["human_reference"]["status"] == "ok"
    assert result["human_reference"]["shared_channels"] == ["digital_interaction"]
    assert result["human_reference"]["same_date_hamming"]["right_distance"] is not None


def test_formal_window_uses_natural_week_blocks_and_ten_thousand_resamples(monkeypatch) -> None:
    """正式窗口先形成十二个完整自然周，再以周块执行一万次重抽样。"""
    scenario = load_scenario(FORMAL_SCENARIO)
    blocks = metrics_module.date_blocks(scenario)
    flattened = tuple(day for block in blocks for day in block)

    assert len(blocks) == 12
    assert all(len(block) == 7 for block in blocks)
    assert all(block[0].weekday() == 0 and block[-1].weekday() == 6 for block in blocks)
    assert flattened == scenario.window.days

    left = tuple(float(index) for index in range(84))
    right = tuple(0.0 for _ in range(84))
    block_values = metrics_module._block_mean_differences(left, right, scenario)
    observed: dict[str, object] = {}

    class RecordingGenerator:
        """记录重抽样总体与输出形状。"""

        def integers(self, low, high, size):
            observed.update({"low": low, "high": high, "size": size})
            return metrics_module.np.zeros(size, dtype=int)

    monkeypatch.setattr(metrics_module.np.random, "default_rng", lambda seed: RecordingGenerator())
    interval = metrics_module._bootstrap_interval("formal-week-blocks", block_values, scenario)

    assert len(block_values) == 12
    assert observed == {"low": 0, "high": 12, "size": (10_000, 12)}
    assert interval["eligible_blocks"] == 12
    assert interval["repetitions"] == 10_000


def test_public_support_intersects_each_category_field_and_reports_missing_denominators(
    evaluation_fixture,
) -> None:
    _, left, right = _views(evaluation_fixture)
    at = left.window.start + timedelta(hours=10)
    left_item = PublicEvidence(
        at, "purchase", "order", "order", "declared", "left-order",
        {"fulfillment_mode": "delivery"}, ("left-event",), "left-session",
    )
    right_items = (
        PublicEvidence(
            at, "purchase", "order", "order", "requested", None, {},
            ("right-event-one",), "right-session",
        ),
        PublicEvidence(
            at + timedelta(minutes=1), "purchase", "order", "order", None, None, {},
            ("right-event-two",), "right-session",
        ),
    )

    support = shared_support(
        replace(left, public_evidence=(left_item,)),
        replace(right, public_evidence=right_items),
    )

    assert support["public_fields_by_category"]["purchase"] == [
        "interaction", "object_type", "state",
    ]
    assert support["public_field_coverage"]["left"]["purchase"]["object_ref"] == {
        "present": 1,
        "eligible": 1,
    }
    assert support["public_field_coverage"]["right"]["purchase"]["state"] == {
        "present": 1,
        "eligible": 2,
    }
    assert "fulfillment_mode" not in support["public_fields_by_category"]["purchase"]

    right_with_identity = replace(right_items[0], object_key="right-order")
    identity_support = shared_support(
        replace(left, public_evidence=(left_item,)),
        replace(right, public_evidence=(right_with_identity,)),
    )
    assert "object_ref" in identity_support["public_fields_by_category"]["purchase"]


def _views(evaluation_fixture):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    left = adapt_labelkit(evaluation_fixture.labelkit_root, scenario)
    right = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    return scenario, left, right
