import pytest
from typing import get_args

from worldmock.footprints.models import (
    CHAT_INVITE_KIND,
    DECISION_OFFERED_FIELD,
    EventType,
    DECISION_STATEMENT_FIELD,
    MESSAGE_ARRIVAL_KIND,
    WAKE_REASON_NAMES,
    WAKE_REASONS_FIELD,
    validate_decision_rhythm,
    validate_decision_statement,
)

#: 事件类型冻结枚举（footprints/models.py EventType Literal 的运行时视图）。
EVENT_TYPES = get_args(EventType)

_SPEC_WAKE_REASONS = frozenset({
    "planned_end", "felt_band_change", "message_arrival", "delivery_arrival",
    "task_available", "task_due_soon", "work_review_result", "commitment_threatened",
    "reconsideration_interval", "location_hours_change", "person_arrived", "person_left",
    "activity_infeasible", "rejection_feedback", "discovery_result", "wake_from_sleep", "run_start",
    "progress_report_due",
})

#: 规格事件类型冻结枚举 14 类（`docs/CONTRACTS.md`「事件类型枚举」逐字）。
_SPEC_EVENT_TYPES = (
    "observation", "decision_started", "model_request", "model_response", "action_requested",
    "action_accepted", "action_rejected", "activity_progress", "action_result",
    "environment_event", "memory_updated", "checkpoint_created", "run_ended", "run_failed",
)


def test_event_type_vocabulary_is_the_frozen_spec_set():
    """GAP-2：事件类型冻结集合逐字等于规格 14 类（含顺序与拼写）。"""
    assert tuple(EVENT_TYPES) == _SPEC_EVENT_TYPES
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES)) == 14


def test_event_type_rejects_anything_outside_the_frozen_set():
    from worldmock.footprints.models import EventDraft

    with pytest.raises(ValueError):
        EventDraft(event_type="agent_thought", occurred_at=1)


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


def test_decision_statement_field_type_contract():
    validate_decision_statement({DECISION_STATEMENT_FIELD: "有点饿，先吃点东西。"})
    validate_decision_statement({"phase": "decision_statement"})
    validate_decision_statement({})

    with pytest.raises(ValueError):
        validate_decision_statement({DECISION_STATEMENT_FIELD: 123})
    with pytest.raises(ValueError):
        validate_decision_statement({DECISION_STATEMENT_FIELD: ["打算"]})


@pytest.mark.parametrize("value", [None, 123, True, ["打算"], {"text": "打算"}])
def test_decision_statement_validation_is_wired_into_event_drafts(value, caplog):
    """真实事件入口拒绝不合法自述，独立校验函数不能代替生产接线。"""
    from worldmock.footprints.models import EventDraft

    with pytest.raises(ValueError, match="decision_statement must be a string"):
        EventDraft(event_type="decision_started", occurred_at=1, data={DECISION_STATEMENT_FIELD: value})

    assert "Decision statement evidence must be a string" in caplog.text


def test_persisted_decision_statement_is_validated_when_reading_events():
    """读取持久事件复用自述校验，不接受错误类型的导入证据。"""
    from worldmock.footprints.models import Event, EventDraft

    payload = EventDraft(event_type="decision_started", occurred_at=1).model_dump()
    payload.update(run_id="run", event_seq=1, recorded_at="2026-09-20T00:00:00Z",
                   wall_elapsed_ms=0, state_version_before=0, state_version_after=0)
    assert Event.model_validate(payload).data == {}
    payload["data"] = {DECISION_STATEMENT_FIELD: ["invalid"]}

    with pytest.raises(ValueError, match="decision_statement must be a string"):
        Event.model_validate(payload)


def test_direct_engine_decision_can_omit_statement_without_fabrication():
    """未经过模型的引擎决策不制造自述字段。"""
    from worldmock.footprints.models import EventDraft

    event = EventDraft(event_type="decision_started", occurred_at=1, data={"round_id": "round"})
    assert event.data == {"round_id": "round"}


def test_store_revalidates_decision_statement_before_persisting_mutated_draft(tmp_path):
    """草稿创建后被破坏的字段也不能通过真实存储入口写入。"""
    from worldmock.footprints.models import EventDraft
    from worldmock.footprints.store import FootprintStore

    store = FootprintStore.create(tmp_path / "run", {"run_id": "run"}, {"version": 0, "current_time": 1})
    try:
        draft = EventDraft(event_type="decision_started", occurred_at=store.state["current_time"],
                           data={DECISION_STATEMENT_FIELD: "我想先歇一会儿。"})
        draft.data[DECISION_STATEMENT_FIELD] = ["invalid"]
        previous = store.last_event_seq
        with pytest.raises(ValueError, match="decision_statement must be a string"):
            store.commit(store.state, [draft])
        assert store.last_event_seq == previous
    finally:
        store.close()
