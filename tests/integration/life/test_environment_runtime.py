"""真实冻结环境中的观察、七种动作、共同活动和恢复行为。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    advance_next, conclude_request, context, pause_request, report_request, stage_day_plan, start_activity,
)
from worldmock.errors import ProtocolError
from worldmock.life.models import StartActivityRequest
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.world_query import QueryWorldRequest


from tests.life.environment_runtime import act, events, reach
from tests.life.environment_runtime import finish_activity


def test_startup_has_only_initial_session_wake_and_frozen_input_identity(engine_factory):
    engine = engine_factory()
    person = engine.context.state.agents[1]
    assert person.pending_wake_reasons == ["session_open"]
    assert person.body.relaxation_need == 0.2
    assert person.body.social_need == 0.2
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    assert len([row for row in events(engine, "environment_event")
                if row.data.get("kind") == "time_trigger" and row.data["key"] == "run_start"]) == 1
    assert engine.context.store.manifest["persona_sha256"] == engine.context.inputs.persona_sha256
    assert engine.context.store.manifest["config_sha256"] == engine.context.inputs.config_sha256
    assert set(engine.context.store.manifest["environment_identity"]) == {
        "environment_id", "environment_version", "catalog_content_sha256", "map_sha256", "rules_version"}


def test_public_layers_validate_and_contain_four_felt_signals(engine_factory):
    engine = engine_factory()
    data = engine.observe(context(engine, "initial-observation"))
    for key in ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer"):
        validate_layer(data[key])
    assert list(data["environment_layer"]["body"]) == ["appetite", "sleepiness", "relaxation", "social"]
    assert data["environment_layer"]["location"]["name"] == "家"
    assert all(isinstance(value, str) for value in data["environment_layer"]["body"].values())
    assert not any(row["kind"] == "person" for row in data["tool_layer"]["targets"])
    assert set(data["known_activity_actions"]) == {
        "walk", "drive", "take_taxi", "take_bus", "purchase", "sleep", "interact"}


@pytest.mark.parametrize("action,mode", [
    ("walk", "walking"), ("drive", "driving"), ("take_taxi", "taxi"), ("take_bus", "bus")])
def test_each_travel_action_uses_complete_path_actual_departure_fare_and_arrival(engine_factory, action, mode):
    engine = engine_factory()
    _, result = act(engine, f"trip-{action}", start_activity(action, "公司"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.travel_mode == mode
    assert activity.resource_claims == {"first"}
    assert activity.data["path"][0] == "home" and activity.data["path"][-1] == "office"
    assert activity.data["distance_meters"] == engine.context.catalog.shortest_m("home", "office")
    paid = 200000 - engine.context.state.accounts["agent:1"]
    assert paid >= 0 and engine.context.state.accounts["external:scenario"] == -200000 + paid
    assert paid == 0 if action == "walk" else paid > 0
    reach(engine, activity.planned_end_at)
    final = engine.context.state.activities[result.activity_id]
    assert final.status == "completed" and final.ended_at == activity.planned_end_at
    assert engine.context.state.agents[1].location_id == "office"
    assert final.route_progress_meters == activity.data["distance_meters"]
    assert engine.context.state.accounts["agent:1"] == 200000 - paid
    terminal = [row for row in events(engine, "action_result") if row.action_id == f"action-trip-{action}"][-1]
    assert terminal.data["status"] == "completed"


def test_sleep_ignores_every_intermediate_external_boundary_and_freezes_needs(engine_factory):
    engine = engine_factory()
    _, result = act(engine, "sleep", start_activity("sleep", "家"))
    assert result.status == "in_progress", result.error_code
    before = engine.context.state.agents[1].body.model_copy(deep=True)
    activity = engine.context.state.activities[result.activity_id]
    boundaries = 0
    while engine.context.state.current_time < activity.planned_end_at:
        advance_next(engine)
        boundaries += 1
        person = engine.context.state.agents[1]
        assert person.body.relaxation_need == before.relaxation_need
        assert person.body.social_need == before.social_need
        if engine.context.state.current_time < activity.planned_end_at:
            assert engine.scheduling_state().ready_agent_ids == ()
            assert person.pending_wake_reasons == []
    assert boundaries > 1
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "planned_end" in engine.context.state.agents[1].pending_wake_reasons


@pytest.mark.parametrize("action", ["wait", "chat", "Sleep", "travel_to", "collect_parcel"])
def test_retired_and_unknown_actions_leave_no_partial_activity_and_write_feedback(engine_factory, action):
    engine = engine_factory()
    _, result = act(engine, f"bad-{action}", start_activity(action, "家"))
    assert result.status == "rejected"
    assert result.data["items"][0]["error_code"] == "ACTION_NOT_OFFERED"
    assert not engine.context.state.activities
    assert engine.context.state.accounts["agent:1"] == 200000
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(0.25)
    assert events(engine, "action_rejected")


def test_two_slot_group_starts_travel_and_app_interaction_atomically(engine_factory):
    engine = engine_factory()
    request = StartActivityRequest(items=[{"action": "walk", "target": "公司"},
                                          {"action": "interact", "target": "哔哩哔哩"}])
    _, result = act(engine, "parallel", request)
    assert result.status == "in_progress", result.error_code
    activities = list(engine.context.state.activities.values())
    assert len(activities) == 2
    assert {next(iter(item.resource_claims)) for item in activities} == {"first", "second"}
    assert len({item.started_at for item in activities}) == 1
    before = engine.context.state.agents[1].body
    advance_next(engine)
    now = engine.context.state.current_time
    after = engine.context.state.agents[1].body
    hours = (now - activities[0].started_at) / 3600
    assert after.social_need == pytest.approx(before.social_need + 0.06 * hours)
    relief = activities[1].data["relaxation_relief_per_hour"]
    assert after.relaxation_need == pytest.approx(max(0, before.relaxation_need + (0.08 - relief) * hours))


@pytest.mark.parametrize("items", [
    [{"action": "interact", "target": "哔哩哔哩"}, {"action": "interact", "target": "抖音"}],
    [{"action": "walk", "target": "公司"}, {"action": "take_bus", "target": "五里公园"}],
    [{"action": "sleep", "target": "家"}, {"action": "interact", "target": "哔哩哔哩"}],
    [{"action": "walk", "target": "公司"}, {"action": "interact", "target": "不存在"}],
])
def test_invalid_group_rejects_all_items_before_occupancy_or_funds_change(engine_factory, items):
    engine = engine_factory()
    _, result = act(engine, "invalid-group", StartActivityRequest(items=items))
    assert result.status == "rejected"
    assert not engine.context.state.activities
    assert not engine.context.state.deliveries
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}


def test_pause_resume_and_conclude_keep_actual_shared_content_progress(engine_factory):
    engine = engine_factory(mutate=lambda persona, config: config.update(progress_report_interval_seconds=60))
    _, result = act(engine, "media", start_activity("interact", "Keep"))
    activity_id = result.activity_id
    activity = engine.context.state.activities[activity_id]
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    if engine.context.state.activities[activity_id].status == "completed":
        pytest.fail("Fixture must expose an interaction longer than the first settlement boundary")
    before = engine.context.state.activities[activity_id]
    _, paused = act(engine, "pause", pause_request(before.name), tool="pause_activity")
    assert paused.status == "completed", paused.error_code
    position = engine.context.state.agents[1].content_progress[before.data["content_id"]].position_seconds
    _, resumed = act(engine, "resume", report_request(before.name), tool="report_status")
    assert resumed.status == "completed", resumed.error_code
    current = engine.context.state.activities[activity_id]
    assert current.status == "active" and current.media_position_seconds == position
    assert current.data["draw_index"] == activity.data["draw_index"]
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    resumed_position = engine.context.state.activities[activity_id].media_position_seconds
    assert resumed_position > position
    _, ended = act(engine, "conclude", conclude_request(current.name), tool="conclude_activity")
    assert ended.status == "completed", ended.error_code
    final = engine.context.state.activities[activity_id]
    assert final.status == "cancelled" and final.concluded_at == engine.context.state.current_time
    assert engine.context.state.agents[1].content_progress[before.data["content_id"]].position_seconds == resumed_position
    assert [row.data["kind"] for row in events(engine, "environment_event")
            if row.activity_id == activity_id and row.data.get("kind") in {
                "activity_paused", "activity_resumed", "activity_concluded"}] == [
                    "activity_paused", "activity_resumed", "activity_concluded"]


def test_query_keeps_world_random_state_and_occupancy_unchanged(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "query-preview"))
    before = engine.context.state.model_dump(mode="json")
    request = QueryWorldRequest(name=None, action="interact", category=None, available_only=False, offset=0)
    from worldmock.life.world_query import query_world
    response = query_world(observed, request)
    assert response["total_count"] > 0
    assert engine.context.state.model_dump(mode="json") == before


def test_action_id_replay_is_idempotent_and_parameter_conflict_is_protocol_error(engine_factory):
    engine = engine_factory()
    request = start_activity("interact", "美团")
    _, result = act(engine, "stable", request, action_id="same-action")
    reach(engine, engine.context.state.activities[result.activity_id].planned_end_at)
    original = engine.context.state.model_dump(mode="json")
    decision = context(engine, "replay")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "same-action", "start_activity", request))
    replay = engine.commit_decision_round("replay")[0]
    assert replay.status == "completed"
    assert len(engine.context.state.activities) == len(original["activities"]) == 1
    decision = context(engine, "conflict")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(decision, "same-action", "start_activity", start_activity("interact", "家")))
    engine.abort_decision_round("conflict", "expected conflict")


def test_same_second_six_rejections_stop_without_advancing_clock(engine_factory):
    engine = engine_factory()
    now = engine.context.state.current_time
    for index in range(5):
        _, result = act(engine, f"reject-{index}", start_activity("interact", "Missing"))
        assert result.status == "rejected"
    with pytest.raises(ProtocolError, match="decision_budget_exhausted"):
        act(engine, "reject-final", start_activity("interact", "Missing"))
    assert engine.context.state.current_time == now
    with pytest.raises(ProtocolError, match="decision_budget_exhausted"):
        engine.scheduling_state()
    assert events(engine, "run_failed")


def test_abort_and_stale_context_never_execute_the_uncommitted_action(engine_factory):
    engine = engine_factory()
    decision = context(engine, "abort")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "never-run", "start_activity", start_activity("sleep", "家")))
    engine.abort_decision_round("abort", "test failure")
    assert not engine.context.state.activities
    assert engine.context.state.processed_actions["never-run"].result["status"] == "failed"
    with pytest.raises(ProtocolError):
        engine.observe(decision)


def test_wrong_observed_time_cannot_read_world(engine_factory):
    engine = engine_factory()
    decision = context(engine, "wrong-time")
    decision = type(decision)(decision.run_id, decision.round_id, decision.decision_id, decision.agent_id,
        datetime.fromtimestamp(engine.context.state.current_time + 1, ZoneInfo(engine.context.config.timezone)),
        decision.world_state_version)
    with pytest.raises(ProtocolError, match="time is stale"):
        engine.observe(decision)


def test_paused_interaction_requires_its_actual_start_location_to_resume(engine_factory):
    engine = engine_factory(mutate=lambda persona, config: config.update(progress_report_interval_seconds=60))
    _, result = act(engine, "original-media", start_activity("interact", "Keep"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    media = engine.context.state.activities[result.activity_id]
    _, paused = act(engine, "pause-before-trip", pause_request(media.name), "pause_activity")
    assert paused.status == "completed"
    _, trip = act(engine, "away", start_activity("walk", "公司"))
    finish_activity(engine, trip, "away")
    before = engine.context.state.agents[1].content_progress[media.data["content_id"]]
    _, refused = act(engine, "wrong-location", report_request(media.name), "report_status")
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert engine.context.state.activities[media.activity_id].status == "paused"
    assert engine.context.state.agents[1].content_progress[media.data["content_id"]] == before


def test_completed_interaction_keeps_its_slot_until_actual_conclusion(engine_factory):
    engine = engine_factory()
    _, result = act(engine, "finished-browse", start_activity("interact", "美团"))
    activity = engine.context.state.activities[result.activity_id]
    reach(engine, activity.planned_end_at)
    _, refused = act(engine, "pending-blocks", start_activity("interact", "Keep"))
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert len(engine.context.state.activities) == 1
    _, concluded = act(engine, "release-slot", conclude_request(activity.name), "conclude_activity")
    assert concluded.status == "completed"
    _, next_activity = act(engine, "new-media", start_activity("interact", "Keep"))
    assert next_activity.status == "in_progress"


def _two_station_world(tmp_path, first_m, second_m):
    """夹具副本加第二个驿站，并声明两个驿站各自到家的距离。"""
    from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows

    root = copy_environment(tmp_path / "station-input")
    rows = catalog_rows(root, "location.jsonl")
    rows.append({"accepts_parcels": True, "app_ids": [], "branch_label": "二店", "kind": "parcel_station",
                 "location_id": "parcel-station-2", "name": "快递驿站"})
    write_catalog_rows(root, "location.jsonl", sorted(rows, key=lambda row: row["location_id"]))
    write_catalog_rows(root, "map.jsonl", [{"a": "home", "b": "parcel-station", "length_m": first_m},
                                          {"a": "home", "b": "parcel-station-2", "length_m": second_m}])
    return root


@pytest.mark.parametrize("first_m,second_m,nearest", [(9000, 300, "parcel-station-2"), (300, 9000, "parcel-station"),
                                                      (700, 700, "parcel-station")])
def test_courier_orders_go_to_the_station_nearest_home_by_declared_distance(
        engine_factory, tmp_path, first_m, second_m, nearest):
    """最近的驿站按新地图的最短距离选，同距取较小的地点身份。"""
    engine = engine_factory(_two_station_world(tmp_path, first_m, second_m))
    catalog = engine.context.catalog
    assert (catalog.shortest_m("home", "parcel-station"), catalog.shortest_m("home", "parcel-station-2")) == (
        first_m, second_m)
    assert catalog.nearest_parcel_station("home") == nearest
    _, result = act(engine, "declared-courier", start_activity("purchase", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    order = next(iter(engine.context.state.deliveries.values()))
    assert (order.fulfillment, order.destination_id) == ("station_pickup", nearest)


def test_trip_record_and_arrival_event_carry_the_declared_distance(engine_factory, tmp_path):
    """行程记录的距离等于声明值：受理结果、到达后的活动和提交的事件都是 12,600 米的直达路径。"""
    from tests.life.environment_inputs import copy_environment, write_catalog_rows

    root = copy_environment(tmp_path / "trip-input")
    write_catalog_rows(root, "map.jsonl", [{"a": "home", "b": "office", "length_m": 12600}])
    engine = engine_factory(root)
    _, result = act(engine, "declared-walk", start_activity("walk", "公司"))
    assert result.status == "in_progress", result.error_code
    started = result.data["activities"][0]
    assert (started["data"]["distance_meters"], started["data"]["path"]) == (12600, ["home", "office"])
    assert started["planned_duration_seconds"] == 12353
    reach(engine, started["planned_end_at"])
    final = engine.context.state.activities[result.activity_id]
    assert final.status == "completed" and final.route_progress_meters == 12600
    assert engine.context.state.agents[1].location_id == "office"
    terminal = [row for row in events(engine, "action_result") if row.action_id == "action-declared-walk"][-1]
    assert terminal.data["status"] == "completed"
    recorded = [row for row in events(engine) if row.activity_id == result.activity_id
                and isinstance(row.data.get("activity"), dict)]
    assert recorded and all(row.data["activity"]["data"]["distance_meters"] == 12600 for row in recorded)
