import pytest

from worldmock.footprints.models import (
    CHAT_INVITE_KIND,
    DECISION_OFFERED_FIELD,
    INNER_MONOLOGUE_FIELD,
    MESSAGE_ARRIVAL_KIND,
    WAKE_REASON_NAMES,
    WAKE_REASONS_FIELD,
    validate_decision_rhythm,
    validate_inner_monologue,
)

_SPEC_WAKE_REASONS = frozenset({
    "planned_end", "felt_band_change", "message_arrival", "delivery_arrival",
    "task_available", "task_due_soon", "work_review_result", "plan_block_boundary",
    "reconsideration_interval", "location_hours_change", "person_arrived", "person_left",
    "activity_infeasible", "rejection_feedback", "wake_from_sleep", "run_start",
})


def test_wake_reason_vocabulary_is_the_frozen_spec_set():
    assert WAKE_REASON_NAMES == _SPEC_WAKE_REASONS


def test_wake_reason_vocabulary_matches_the_engine_side_enum():
    from worldmock.life.schedule import WakeReason

    assert WAKE_REASON_NAMES == {reason.value for reason in WakeReason}


def test_arrival_kind_constants_match_the_spec_names():
    assert MESSAGE_ARRIVAL_KIND == "message_arrival"
    assert CHAT_INVITE_KIND == "chat_invite"


def test_decision_rhythm_accepts_offered_and_silent_boundaries():
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True,
                              WAKE_REASONS_FIELD: ["run_start", "planned_end", "run_start"]})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: False, WAKE_REASONS_FIELD: []})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True})


def test_legacy_activity_progress_without_new_fields_passes():
    validate_decision_rhythm({})
    validate_decision_rhythm({"activity": {"kind": "work"}, "body_settlement": {}})


@pytest.mark.parametrize("offered", ["true", 1, 0, None, [], 1.0])
def test_non_boolean_decision_offered_is_rejected(offered):
    with pytest.raises(ValueError):
        validate_decision_rhythm({DECISION_OFFERED_FIELD: offered})


@pytest.mark.parametrize("reasons", [
    ["planned_end", "nonsense"],
    [7],
    [[]],
    "planned_end",
    {"planned_end": True},
])
def test_wake_reasons_outside_the_frozen_vocabulary_are_rejected(reasons):
    with pytest.raises(ValueError):
        validate_decision_rhythm({DECISION_OFFERED_FIELD: True, WAKE_REASONS_FIELD: reasons})


def test_inner_monologue_field_type_contract():
    validate_inner_monologue({INNER_MONOLOGUE_FIELD: "有点饿，先吃点东西。"})
    validate_inner_monologue({"phase": "monologue"})
    validate_inner_monologue({})

    with pytest.raises(ValueError):
        validate_inner_monologue({INNER_MONOLOGUE_FIELD: 123})
    with pytest.raises(ValueError):
        validate_inner_monologue({INNER_MONOLOGUE_FIELD: ["打算"]})
