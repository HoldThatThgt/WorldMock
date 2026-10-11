"""恢复边界必须拒绝与实际目录、内容位置及回调登记不符的持久状态。"""

from copy import deepcopy

import pytest

from tests.life.conftest import travel, use_app
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act
from worldmock.errors import IntegrityError
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.callbacks import validate_callback_states


def started(engine_factory, request=None):
    engine = engine_factory()
    _, result = act(engine, "actual-start", request or use_app("哔哩哔哩"))
    assert result.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    return engine, state, state.activities[result.activity_id]


@pytest.mark.parametrize("field,value", [
    ("accepted_event_id", ""), ("action", "purchase"), ("app_id", "unknown"), ("app_id", "amap"),
    ("content_id", "unknown"), ("content_id", "parcel-service"), ("exertion", "vigorous"),
    ("relaxation_relief_per_hour", -0.1), ("social_relief_per_hour", -0.1),
    ("start_position_seconds", True), ("start_position_seconds", float("inf")),
    ("start_position_seconds", -1), ("draw_index", True), ("draw_index", -1),
    ("pinned_by_push_id", ""), ("pinned_by_push_id", 1), ("played", {}), ("played", ["unknown"]),
    ("played", [{"content_id": "c-course"}]),
    ("played", [{"content_id": "unknown", "from_position": 0, "to_position": 1}]),
    ("played", [{"content_id": "parcel-service", "from_position": 0, "to_position": 1}]),
    ("played", [{"content_id": "c-course", "from_position": True, "to_position": 1800}]),
    ("played", [{"content_id": "c-course", "from_position": 1800, "to_position": 1800}]),
    ("played", [{"content_id": "c-course", "from_position": 0, "to_position": 1}]),
])
def test_content_restore_rejects_invalid_identity_progress_effects_and_consumed_segments(engine_factory, field, value):
    engine, state, activity = started(engine_factory)
    activity.data[field] = value
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


@pytest.mark.parametrize("field", ["content_id", "app_id"])
def test_content_restore_requires_both_explicit_container_fields(engine_factory, field):
    engine, state, activity = started(engine_factory)
    del activity.data[field]
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


@pytest.mark.parametrize("field,value", [
    ("agent_id", 2), ("location_id", "unknown"), ("destination_id", "office"),
    ("media_position_seconds", True), ("media_position_seconds", -1),
    ("media_position_seconds", 100000),
])
def test_content_restore_rejects_wrong_person_location_and_absolute_position(engine_factory, field, value):
    engine, state, activity = started(engine_factory)
    state.activities[activity.activity_id] = activity.model_copy(update={field: value})
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


def test_completed_segments_remain_valid_only_at_the_actual_content_end(engine_factory):
    engine, state, activity = started(engine_factory)
    activity.data["played"] = [{"content_id": "c-course", "from_position": 120, "to_position": 1800}]
    validate_activity_targets(state, engine.context.catalog, engine.context.persona)
    assert activity.data["played"][0]["to_position"] == engine.context.catalog.contents["c-course"].duration_seconds


@pytest.mark.parametrize("field,value", [
    ("draw_index", 0), ("app_id", "bilibili"), ("exertion", "light"),
    ("relaxation_relief_per_hour", 0.1), ("social_relief_per_hour", 0.1),
])
def test_platform_browse_restore_requires_its_actual_zero_effect_snapshot(engine_factory, field, value):
    engine, state, activity = started(engine_factory, use_app("美团"))
    activity.data[field] = value
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


@pytest.mark.parametrize("field,value", [
    ("trip_id", "unknown"), ("path", ["home", "office"]), ("distance_meters", 1),
    ("origin_id", "office"), ("destination_id", "home"),
])
def test_frozen_travel_must_match_original_graph_route(engine_factory, field, value):
    engine, state, activity = started(engine_factory, travel("公司"))
    if field == "path" and activity.data[field] == value:
        value = list(reversed(value))
    activity.data[field] = value
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


def callback_input(tmp_path):
    root = copy_environment(tmp_path / "input")
    state = {"state": "stock", "initial": 3, "cap": 3, "reset_on_goods": []}
    rule = {"rule_id": "daily", "trigger": {"class": "action", "key": "drive"},
            "effect": {"type": "state_delta", "state": "stock", "delta": -1, "once_per": "local_day"},
            "notify": {"when": {"state": "stock", "below": 1}, "channel": "app_push",
                       "app_id": "bilibili", "text": "库存不足"}}
    write_catalog_rows(root, "callbacks.jsonl", [state, rule])
    return root


@pytest.mark.parametrize("changes,reason", [
    ({"callback_states": {}}, "state set differs"),
    ({"callback_states": {"stock": True}}, "outside its registered domain"),
    ({"callback_states": {"stock": 4}}, "outside its registered domain"),
    ({"callback_once_keys": {"invalid"}}, "daily application identity or date is invalid"),
    ({"callback_once_keys": {"unknown:2026-01-05"}}, "daily application identity or date is invalid"),
    ({"callback_once_keys": {"daily:invalid"}}, "daily application identity or date is invalid"),
    ({"callback_once_keys": {"daily:20260105"}}, "daily application identity or date is invalid"),
    ({"callback_reminder_days": {"unknown": "2026-01-05"}}, "reminder identity or date is invalid"),
    ({"callback_reminder_days": {"daily": "invalid"}}, "reminder identity or date is invalid"),
])
def test_callback_restore_requires_exact_registered_states_daily_keys_and_reminders(engine_factory, tmp_path,
                                                                                  changes, reason):
    engine = engine_factory(callback_input(tmp_path))
    state = engine.context.state.model_copy(deep=True)
    state.agents[1] = state.agents[1].model_copy(update=deepcopy(changes))
    with pytest.raises(IntegrityError, match=reason):
        validate_callback_states(state, engine.context.catalog)


def test_callback_restore_accepts_real_daily_and_reminder_dates(engine_factory, tmp_path):
    engine = engine_factory(callback_input(tmp_path))
    state = engine.context.state.model_copy(deep=True)
    state.agents[1].callback_once_keys = {"daily:2026-01-05"}
    state.agents[1].callback_reminder_days = {"daily": "2026-01-05"}
    validate_callback_states(state, engine.context.catalog)
    assert state.agents[1].callback_states == {"stock": 3}
