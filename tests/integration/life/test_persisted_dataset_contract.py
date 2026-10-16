"""恢复边界必须拒绝与实际目录、内容位置及回调登记不符的持久状态。"""

from copy import deepcopy

import pytest

from tests.life.conftest import advance_next, travel, use_app
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act
from worldmock.errors import IntegrityError
from worldmock.life.activity_validation import _CONTENT_FIELDS, validate_activity_targets
from worldmock.life.callbacks import validate_callback_states
from worldmock.life.travel import persisted_trip


def started(engine_factory, request=None):
    engine = engine_factory()
    _, result = act(engine, "actual-start", request or use_app("哔哩哔哩"))
    assert result.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    activity = state.activities[result.activity_id].model_copy(deep=True)
    state.activities[result.activity_id] = activity
    return engine, state, activity


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


@pytest.mark.parametrize("shape", ["content-fields", "retired-seven-keys"])
def test_interact_without_content_is_rejected_at_restore(engine_factory, shape):
    """每一项 interact 都有内容：真实开始一项 app 使用后，把快照改成没有内容的两种形状（带内容时的键集而 content_id
    为空；已退役的七个键），恢复时都以 Persisted activity target is invalid 拒绝。"""
    engine, state, activity = started(engine_factory)
    assert set(activity.data) == _CONTENT_FIELDS
    if shape == "content-fields":
        activity.data["content_id"] = None
    else:
        activity.data = {"action": "interact", "app_id": "bilibili", "content_id": None, "exertion": "idle",
                         "relaxation_relief_per_hour": 0.0, "social_relief_per_hour": 0.0,
                         "accepted_event_id": activity.data["accepted_event_id"]}
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


@pytest.mark.parametrize("field,value", [
    ("trip_id", "unknown"), ("path", ["home", "office"]), ("distance_meters", 1),
    ("origin_id", "office"), ("destination_id", "home"), ("exertion", "light"), ("exertion", None),
    ("weather_factor", 0.5),
])
def test_frozen_travel_must_match_original_graph_route(engine_factory, field, value):
    engine, state, activity = started(engine_factory, travel("公司"))
    assert activity.data["exertion"] == "walking"
    if field == "path" and activity.data[field] == value:
        value = list(reversed(value))
    activity.data[field] = value
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


@pytest.mark.parametrize("departed,persisted", [("步行", "driving"), ("步行", "boat"), ("步行", None), ("步行", "步行"),
                                                ("驾车", "taxi"), ("驾车", "walking"), ("打车", "driving")])
def test_frozen_travel_must_keep_the_travel_method_it_departed_with(engine_factory, departed, persisted):
    """恢复时按活动保存的出行方式身份重算行程：换成别的方式、没登记的身份、名称或空值，行程身份或用时对不上就拒绝；
    速度和档位都相同的驾车与打车也不能互换，因为行程身份带着出行方式身份。"""
    engine, state, activity = started(engine_factory, travel("公司", departed))
    validate_activity_targets(state, engine.context.catalog, engine.context.persona)
    activity.travel_method_id = persisted
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


TRAVEL_PLAN_FAULT = "^Persisted travel plan does not match the published graph$"


def test_frozen_trip_recomputes_to_the_plan_accepted_at_departure(engine_factory):
    """受理时冻结的行程按同一出行方式和同一天气系数重算，得到出发时的同一份计划：行程身份、路径、米数、用时和已付的车费。"""
    engine = engine_factory()
    payer = engine.context.state.agents[1].balance_account_id
    balance = engine.context.state.accounts[payer]
    _, result = act(engine, "drive", travel("公司", "驾车"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    trip = persisted_trip(engine.context.catalog, activity)
    assert (trip.trip_id, trip.origin_id, trip.destination_id) == ("home->office:driving", "home", "office")
    assert (list(trip.path), trip.distance_meters) == (activity.data["path"], activity.data["distance_meters"])
    assert trip.duration_seconds == activity.planned_duration_seconds
    assert trip.fare_fen == balance - engine.context.state.accounts[payer] > 0


@pytest.mark.parametrize("tamper", [
    pytest.param(lambda activity: setattr(activity, "travel_method_id", "taxi"), id="travel-method"),
    pytest.param(lambda activity: activity.data.update(exertion="walking"), id="exertion"),
])
def test_tampered_frozen_trip_stops_with_the_published_graph_fault(engine_factory, tamper):
    """驾车出发后把保存的出行方式身份换成打车，或把代谢当量键换成步行的：重算核对报规定的故障原文；
    引擎推进这趟行程时同样以这条故障停下，不照改过的状态继续走。"""
    engine, _, activity = started(engine_factory, travel("公司", "驾车"))
    assert (activity.travel_method_id, activity.data["exertion"]) == ("driving", "light")
    tamper(activity)
    with pytest.raises(IntegrityError, match=TRAVEL_PLAN_FAULT):
        persisted_trip(engine.context.catalog, activity)
    tamper(engine.context.state.activities[activity.activity_id])
    with pytest.raises(IntegrityError, match=TRAVEL_PLAN_FAULT):
        advance_next(engine)


def callback_input(tmp_path):
    root = copy_environment(tmp_path / "input")
    state = {"state": "stock", "initial": 3, "cap": 3, "reset_on_goods": []}
    rule = {"rule_id": "daily", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
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
