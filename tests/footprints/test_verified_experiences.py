"""已提交进度到人物已核验经历的真实存储边界。"""

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.experiences import (
    project_verified_experiences,
    recent_verified_experiences,
    validate_verified_experiences,
)
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [1, 2]},
        {"version": 0, "current_time": 100},
    )
    yield value
    value.close()


def _progress(kind, activity_id, agent_id=1, **fields):
    activity = {"activity_id": activity_id, "kind": kind, **fields.pop("activity", {})}
    return EventDraft(
        event_type="activity_progress", occurred_at=100, agent_id=agent_id,
        activity_id=activity_id,
        data={"interval_start": 90, "interval_end": 100, "activity": activity, **fields},
    )


def test_recent_window_only_projects_own_committed_progress_before_observation(store, monkeypatch):
    drafts = [
        _progress("wait", "wait-1"),
        _progress("wait", "other-wait", agent_id=2),
        _progress("consume", "food-1", consumption={
            "use_mode": "eat", "location_id": "restaurant",
            "items": [{"product_id": "rice", "interval_quantity": 0.3, "cumulative_quantity": 0.3}],
        }),
        _progress("watch", "watch-1", activity={
            "target_id": "podcast", "data": {"revealed": {
                "position_before_seconds": 0, "position_after_seconds": 10}},
        }),
        _progress("travel", "travel-1", activity={
            "status": "completed", "destination_id": "office", "location_id": "home",
            "data": {"arrival": {"location_id": "office", "access_status": "allowed"}},
        }),
    ]
    committed = store.commit(store.state, drafts)
    observation = store.commit(store.state, [EventDraft(
        event_type="observation", occurred_at=100, agent_id=1,
    )])[0]
    expected = project_verified_experiences(committed, agent_id=1, cutoff_seq=observation.event_seq - 1)
    monkeypatch.setattr(store, "read_events", lambda _: pytest.fail("observation scanned all events"))
    recent = recent_verified_experiences(store, 1, observation.event_seq - 1)
    assert recent == expected
    assert [row["kind"] for row in recent] == ["waited", "consumed", "played", "arrived"]
    assert [row["source_event_id"] for row in recent] == [event.event_id for event in committed if event.agent_id == 1]
    assert validate_verified_experiences(recent, 1, 100) == recent


def test_consumed_intervals_are_increments_and_projection_is_idempotent(store):
    first = _progress("consume", "food-1", consumption={
        "use_mode": "eat", "location_id": "restaurant",
        "items": [{"product_id": "rice", "interval_quantity": 0.3, "cumulative_quantity": 0.3}],
    })
    second = _progress("consume", "food-1", consumption={
        "use_mode": "eat", "location_id": "restaurant",
        "items": [{"product_id": "rice", "interval_quantity": 0.2, "cumulative_quantity": 0.5}],
    })
    events = store.commit(store.state, [first, second])
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=2)
    assert [row["interval_quantity"] for row in facts] == [0.3, 0.2]
    assert sum(row["interval_quantity"] for row in facts) == 0.5
    assert project_verified_experiences(events + events[-1:], agent_id=1, cutoff_seq=2) == facts


def test_missing_settlement_evidence_is_an_error_not_a_guessed_fact(store):
    event = store.commit(store.state, [_progress("consume", "food-1", consumption={
        "use_mode": "eat", "location_id": "restaurant", "items": [{"product_id": "rice"}],
    })])[0]
    with pytest.raises(IntegrityError, match="quantity"):
        project_verified_experiences([event], agent_id=1, cutoff_seq=1)


def test_reference_window_rejects_other_person_and_future_time(store):
    event = store.commit(store.state, [_progress("wait", "wait-1")])[0]
    fact = project_verified_experiences([event], agent_id=1, cutoff_seq=1)[0]
    with pytest.raises(IntegrityError):
        validate_verified_experiences([{**fact, "agent_id": 2}], 1, 100)
    with pytest.raises(IntegrityError):
        validate_verified_experiences([{**fact, "occurred_at": 101}], 1, 100)


def test_observed_window_rejects_invalid_boundary_duplicates_and_reordering(store):
    events = store.commit(store.state, [_progress("wait", "first"), _progress("wait", "second")])
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=2)
    with pytest.raises(IntegrityError, match="window"):
        validate_verified_experiences(facts, 0, 100)
    with pytest.raises(IntegrityError, match="duplicated"):
        validate_verified_experiences(facts + facts[:1], 1, 100)
    with pytest.raises(IntegrityError, match="order"):
        validate_verified_experiences(list(reversed(facts)), 1, 100)


def test_projection_rejects_uncommitted_origin_conflict_and_invalid_cutoff(store):
    events = store.commit(store.state, [_progress("wait", "first"), _progress("wait", "second")])
    with pytest.raises(IntegrityError, match="boundary"):
        project_verified_experiences(events, agent_id=0, cutoff_seq=2)
    with pytest.raises(IntegrityError, match="cutoff"):
        project_verified_experiences(events, agent_id=1, cutoff_seq=1)
    with pytest.raises(IntegrityError, match="ordered"):
        project_verified_experiences(list(reversed(events)), agent_id=1, cutoff_seq=2)
    changed = events[0].model_copy(update={"data": {"activity": {"kind": "watch"}}})
    with pytest.raises(IntegrityError, match="conflicts"):
        project_verified_experiences([events[0], changed], agent_id=1, cutoff_seq=2)
    untrusted = events[0].model_copy(update={"source_kind": "model"})
    with pytest.raises(IntegrityError, match="provenance"):
        project_verified_experiences([untrusted], agent_id=1, cutoff_seq=2)


def test_recent_experience_reader_rejects_uncommitted_cutoff_and_bad_query(store):
    store.commit(store.state, [_progress("wait", "first")])
    with pytest.raises(IntegrityError, match="cutoff"):
        recent_verified_experiences(store, 1, store.last_event_seq + 1)
    with pytest.raises(IntegrityError, match="boundary"):
        store.read_recent_experience_sources(1, store.last_event_seq + 1, 0)


def test_recent_reader_returns_no_experience_from_another_person(store):
    store.commit(store.state, [_progress("wait", "other", agent_id=2)])
    assert recent_verified_experiences(store, 1, store.last_event_seq) == []


def test_zero_playback_and_unfinished_travel_do_not_become_completed_facts(store):
    events = store.commit(store.state, [
        _progress("watch", "watch", activity={
            "target_id": "podcast", "data": {"revealed": {
                "position_before_seconds": 10, "position_after_seconds": 10}},
        }),
        _progress("travel", "route", activity={"status": "active", "destination_id": "office"}),
    ])
    assert project_verified_experiences(events, agent_id=1, cutoff_seq=2) == []
    backwards = events[0].model_copy(update={"data": {
        **events[0].data, "activity": {"kind": "watch", "target_id": "podcast", "data": {
            "revealed": {"position_before_seconds": 10, "position_after_seconds": 9}}},
    }})
    with pytest.raises(IntegrityError, match="backwards"):
        project_verified_experiences([backwards], agent_id=1, cutoff_seq=2)
    missing_arrival = events[1].model_copy(update={"data": {
        **events[1].data, "activity": {"kind": "travel", "status": "completed",
                                      "destination_id": "office", "location_id": "home"},
    }})
    with pytest.raises(IntegrityError, match="arrival"):
        project_verified_experiences([missing_arrival], agent_id=1, cutoff_seq=2)


def test_arrival_snapshot_at_origin_still_projects_arrived(store):
    """出行快照 location_id 是出发地；到达事实只取 arrival.location_id，且须等于目的地。"""
    arrived = _progress("travel", "route", activity={
        "status": "completed", "destination_id": "office", "location_id": "home",
        "data": {"arrival": {"location_id": "office", "access_status": "allowed"}},
    })
    event = store.commit(store.state, [arrived])[0]
    facts = project_verified_experiences([event], agent_id=1, cutoff_seq=event.event_seq)
    assert [(row["kind"], row["location_id"], row["access_status"]) for row in facts] == [
        ("arrived", "office", "allowed")]
    elsewhere = event.model_copy(update={"data": {**event.data, "activity": {
        **event.data["activity"],
        "data": {"arrival": {"location_id": "cafe", "access_status": "allowed"}},
    }}})
    with pytest.raises(IntegrityError, match="arrival location"):
        project_verified_experiences([elsewhere], agent_id=1, cutoff_seq=event.event_seq)


@pytest.mark.parametrize("count", [8, 40])
def test_recent_window_returns_latest_five_before_cutoff_with_more_history(store, count):
    """最近窗口明确排除旧经历、其他人物以及截止之后的新经历。"""
    own = store.commit(store.state, [_progress("wait", f"own-{index}") for index in range(count)])
    store.commit(store.state, [_progress("wait", "other", agent_id=2)])
    cutoff = store.last_event_seq
    store.commit(store.state, [_progress("wait", "after-cutoff")])
    facts = recent_verified_experiences(store, 1, cutoff)
    assert [row["activity_id"] for row in facts] == [f"own-{index}" for index in range(count - 5, count)]
    assert [row["source_event_id"] for row in facts] == [event.event_id for event in own[-5:]]
    assert [row["event_seq"] for row in facts] == list(range(count - 4, count + 1))
