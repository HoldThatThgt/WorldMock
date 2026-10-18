import pytest
from types import SimpleNamespace
from typing import get_args

from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (context)  # noqa: F401
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    DECISION_STARTED_FIELDS,
    EventType,
    WAKE_REASONS_FIELD,
    validate_decision_rhythm,
    validate_decision_started,
)

#: 事件类型冻结枚举（footprints/models.py EventType Literal 的运行时视图）。
EVENT_TYPES = get_args(EventType)
from worldmock.life.schedule import WAKE_TRIGGERS
WAKE_REASON_NAMES = frozenset(trigger.name for trigger in WAKE_TRIGGERS)

_SPEC_WAKE_REASONS = frozenset({
    "planned_end", "felt_band_change", "delivery_arrival", "reconsideration_interval", "rejection_feedback",
    "session_open", "duty_boundary", "progress_report_due", "no_active_activity", "reminder_due", "app_push",
})

#: 规格事件类型冻结枚举 14 类（`docs/design/05-recording-engine.md`「事件类型枚举」逐字）。
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
    from worldmock.life.schedule import WAKE_TRIGGERS

    assert WAKE_REASON_NAMES == {trigger.name for trigger in WAKE_TRIGGERS}




def test_decision_rhythm_accepts_offered_and_silent_boundaries():
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True,
                              WAKE_REASONS_FIELD: ["session_open", "planned_end", "session_open"]})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: False, WAKE_REASONS_FIELD: []})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True})


def test_legacy_activity_progress_without_new_fields_passes():
    validate_decision_rhythm({})
    validate_decision_rhythm({"activity": {"kind": "sleep"}, "body_settlement": {}})


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


def test_new_wake_reasons_are_accepted_by_validation():
    """时间与能量规格第一章规则 6：追加在 progress_report_due 之后的两员属于冻结闭集；个人事务规格 2.5 追加在
    plan_reminder_due 之后的 errand_reminder_due 同样属于冻结闭集（漏登词表时带它的事件落不了库）。"""
    validate_decision_rhythm({WAKE_REASONS_FIELD: ["no_active_activity"]})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True,
                              WAKE_REASONS_FIELD: ["reminder_due"]})
    validate_decision_rhythm({DECISION_OFFERED_FIELD: True,
                              WAKE_REASONS_FIELD: ["progress_report_due", "no_active_activity"]})


def _with_pending_reasons(engine, reasons):
    """把人物 1 的待处理唤醒原因改为给定列表，其余世界状态沿用引擎当前状态。"""
    state = engine.context.state.model_copy(deep=True)
    state.agents[1] = state.agents[1].model_copy(update={"pending_wake_reasons": reasons}, deep=True)
    return state


def test_unknown_wake_reason_is_rejected_on_decision_started_write(engine_factory):
    """decision_started 的唤醒原因在写入时经闭集校验：未知原因被拒，新成员照常写入。"""
    from worldmock.life.decision_start import defer_decision_starts

    engine = engine_factory()
    contexts = [context(engine, "round-1")]
    with pytest.raises(ValueError, match="frozen wake reasons"):
        defer_decision_starts(contexts, "round-1", _with_pending_reasons(engine, ["bogus"]))
    for reasons in (["no_active_activity"], ["reminder_due"]):
        starts = defer_decision_starts(contexts, "round-1", _with_pending_reasons(engine, reasons))
        assert starts[1]["decision_id"] == contexts[0].decision_id
        assert starts[1]["data"][WAKE_REASONS_FIELD] == reasons






#: 一条合法的 decision_started.data（闭集四个字段）。
_STARTED = {"round_id": "round", "round_order": 0, "world_state_version": 3, "wake_reasons": ["session_open"]}


def test_decision_started_data_is_the_closed_four_field_set():
    """decision_started.data 恰为 round_id、round_order、world_state_version、wake_reasons 四个字段。"""
    assert DECISION_STARTED_FIELDS == frozenset(_STARTED)
    validate_decision_started(dict(_STARTED))
    for broken in ({key: value for key, value in _STARTED.items() if key != "round_order"},
                   {**_STARTED, "note": "打算"}, {}):
        with pytest.raises(ValueError, match="must be exactly round_id, round_order, world_state_version, "
                                             "wake_reasons"):
            validate_decision_started(broken)


@pytest.mark.parametrize("value", ["有点饿，先吃点东西。", None, ""])
def test_retired_decision_statement_field_is_rejected_on_event_drafts(value, caplog):
    """带退役字段 decision_statement 的 decision_started 在真实事件入口以英文错误拒绝，不论取值。"""
    from worldmock.footprints.models import EventDraft

    with pytest.raises(ValueError, match="decision_started.data carries the retired field decision_statement"):
        EventDraft(event_type="decision_started", occurred_at=1, data={**_STARTED, "decision_statement": value})

    assert "retired field decision_statement" in caplog.text


def test_persisted_decision_started_is_validated_when_reading_events():
    """读取持久事件复用闭集校验：旧运行带自述或缺字段的 decision_started 不被接受。"""
    from worldmock.footprints.models import Event, EventDraft

    payload = EventDraft(event_type="decision_started", occurred_at=1, data=dict(_STARTED)).model_dump()
    payload.update(run_id="run", event_seq=1, recorded_at="2026-09-20T00:00:00Z",
                   wall_elapsed_ms=0, state_version_before=0, state_version_after=0)
    assert Event.model_validate(payload).data == _STARTED
    payload["data"] = {**_STARTED, "decision_statement": "我想先歇一会儿。"}
    with pytest.raises(ValueError, match="retired field decision_statement"):
        Event.model_validate(payload)
    payload["data"] = {"round_id": "round"}
    with pytest.raises(ValueError, match="must be exactly"):
        Event.model_validate(payload)


def test_other_event_types_are_not_bound_by_the_decision_started_set():
    """闭集只约束 decision_started：其他事件的 data 由各自领域约定。"""
    from worldmock.footprints.models import EventDraft

    event = EventDraft(event_type="observation", occurred_at=1, data={"decision_statement": "旧字段名"})
    assert event.data == {"decision_statement": "旧字段名"}


def test_store_revalidates_decision_started_before_persisting_mutated_draft(tmp_path):
    """草稿创建后被加入退役字段也不能通过真实存储入口写入。"""
    from worldmock.footprints.models import EventDraft
    from worldmock.footprints.store import FootprintStore

    store = FootprintStore.create(tmp_path / "run", {"run_id": "run"}, {"version": 0, "current_time": 1})
    try:
        draft = EventDraft(event_type="decision_started", occurred_at=store.state["current_time"],
                           data=dict(_STARTED))
        draft.data["decision_statement"] = "我想先歇一会儿。"
        previous = store.last_event_seq
        with pytest.raises(ValueError, match="retired field decision_statement"):
            store.append_evidence([draft])
        assert store.last_event_seq == previous
    finally:
        store.close()
