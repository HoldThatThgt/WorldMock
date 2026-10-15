"""真实冻结环境中的观察、七种动作、共同活动和恢复行为。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    MAIN_TOOL, advance_next, advance_until_ready, conclude_request, context, pause_request, purchase, report_request,
    sleep,
    stage_day_plan, target_actions, tool_of, travel, use_app,
)
from tests.life.current_world import stage_moment
from tests.life.prerequisite_inputs import (
    OFFICE, app_content, dingtalk_environment, example_window, moment, weekday_hours,
)
from worldmock.errors import ProtocolError
from worldmock.life.models import StartMainActivityRequest
from worldmock.life.prompt_schemas import validate_layer


from tests.life.environment_runtime import act, act_group, events, reach
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
    assert data["known_activity_actions"] == ["travel", "purchase", "sleep", "interact", "query"]


@pytest.mark.parametrize("method,identity,seconds,fare,exertion", [
    ("步行", "walking", 1919, 0, "walking"), ("驾车", "driving", 329, 500, "light"),
    ("打车", "taxi", 329, 1400, "light"), ("公共交通", "bus", 461, 200, "light")],
    ids=["walking", "driving", "taxi", "bus"])
def test_each_travel_method_uses_complete_path_actual_departure_fare_and_arrival(
        engine_factory, method, identity, seconds, fare, exertion):
    """夹具的家到公司是 1,957 米、当天小雨（系数 0.85）：四种出行方式的用时按各自速度乘天气系数上取整，车费取较大值。"""
    engine = engine_factory()
    action = identity
    assert engine.context.state.weather_by_date == {"2026-01-05": "light_rain"}
    _, result = act(engine, f"trip-{action}", travel("公司", method))
    assert result.status == "in_progress", result.error_code
    assert result.activity_id == f"activity-action-trip-{action}"
    activity = engine.context.state.activities[result.activity_id]
    # 出行只有 travel 一个动作：活动保存所选出行方式的身份、它的代谢当量键，行程身份以出行方式身份结尾
    assert activity.data["action"] == "travel" and activity.travel_method_id == identity
    assert (activity.kind, activity.data["exertion"]) == ("travel", exertion)
    assert activity.data["trip_id"] == f"home->office:{identity}"
    assert activity.name.startswith(f"{method}前往公司（")
    assert activity.resource_claims == {"first"}
    assert activity.data["path"][0] == "home" and activity.data["path"][-1] == "office"
    assert activity.data["distance_meters"] == engine.context.catalog.shortest_m("home", "office") == 1957
    assert activity.planned_duration_seconds == seconds
    paid = 200000 - engine.context.state.accounts["agent:1"]
    assert paid == fare and engine.context.state.accounts["external:scenario"] == -200000 + paid
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
    _, result = act(engine, "sleep", sleep())
    assert result.status == "in_progress", result.error_code
    before = engine.context.state.agents[1].body.model_copy(deep=True)
    activity = engine.context.state.activities[result.activity_id]
    assert activity.resource_claims == {"first"}
    assert activity.planned_duration_seconds == activity.data["declared_duration_seconds"] == 450 * 60
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


@pytest.mark.parametrize("action", ["wait", "chat", "Sleep", "travel_to", "collect_parcel", "walk", "drive",
                                    "take_taxi", "take_bus"])
def test_retired_and_unknown_actions_leave_no_partial_activity_and_write_feedback(engine_factory, action):
    engine = engine_factory()
    # 宿主之外直接登记的请求：类型是请求模型而动作在枚举之外，环境用同一个请求模型复核后按请求形状拒绝
    request = StartMainActivityRequest.model_construct(action=action, target="家", detail="测试休息")
    _, result = act(engine, f"bad-{action}", request)
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    assert result.message == "The value is outside the declared allowed values."
    assert result.data["fix"] == "Correct every reported parameter."
    [item] = result.data["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert item["written"] == action and item["allowed_values"] == ["travel", "interact", "sleep"]
    assert not engine.context.state.activities
    assert engine.context.state.accounts["agent:1"] == 200000
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(0.25)
    assert events(engine, "action_rejected")


def test_two_slot_group_starts_travel_and_app_interaction_atomically(engine_factory):
    # 单独出发时世界里只有这一项活动，它占哪个槽位只取决于槽位规则：出行占槽位 1，槽位 2 留给同组的副活动
    alone = engine_factory()
    _, departed = act(alone, "alone", travel("公司"))
    assert departed.status == "in_progress", departed.error_code
    assert alone.context.state.activities[departed.activity_id].resource_claims == {"first"}
    # 同一决策里两个开始工具各登记一条：整组核验后两项活动同一时刻开始，各占一个槽位
    engine = engine_factory()
    started_at = engine.context.state.current_time
    _, results = act_group(engine, "parallel", travel("公司"), use_app("哔哩哔哩"))
    assert [item.status for item in results] == ["in_progress", "in_progress"], [item.error_code for item in results]
    assert [item.activity_id for item in results] == ["activity-action-parallel-1", "activity-action-parallel-2"]
    activities = engine.context.state.activities
    assert set(activities) == {item.activity_id for item in results}
    trip, media = (activities[item.activity_id] for item in results)
    # 出行由 start_main_activity 放进槽位 1，内容 app 由 start_side_activity 放进槽位 2，两项同一时刻开始
    assert (trip.kind, trip.data["action"], trip.resource_claims) == ("travel", "travel", {"first"})
    assert trip.travel_method_id == "walking"
    assert (media.kind, media.data["app_id"], media.resource_claims) == ("interact", "bilibili", {"second"})
    assert trip.started_at == media.started_at == started_at
    assert trip.status == media.status == "active"
    assert (trip.origin_action_id, media.origin_action_id) == ("action-parallel-1", "action-parallel-2")
    for item in results:
        assert item.message == "Activities started"
        assert [row["activity_id"] for row in item.data["activities"]] == [item.activity_id]
    before = engine.context.state.agents[1].body
    advance_next(engine)
    now = engine.context.state.current_time
    after = engine.context.state.agents[1].body
    hours = (now - trip.started_at) / 3600
    assert after.social_need == pytest.approx(before.social_need + 0.06 * hours)
    relief = media.data["relaxation_relief_per_hour"]
    assert after.relaxation_need == pytest.approx(max(0, before.relaxation_need + (0.08 - relief) * hours))


@pytest.mark.parametrize("main,side,refusal,paths", [
    (sleep, lambda: use_app("哔哩哔哩"),
     ("RESOURCE_CONFLICT", "Sleep requires the second activity slot to be empty.",
      "Submit start_main_activity alone to sleep."),
     [["tool_calls", 0, "function", "arguments", "target"], ["tool_calls", 1, "function", "arguments", "target"]]),
    (lambda: travel("公司"), lambda: use_app("不存在"),
     ("ACTION_REQUEST_INVALID",
      "No known object named 不存在 supports this action; changing spelling cannot create an object.",
      "Use an exact known name or a candidate."),
     [["tool_calls", 1, "function", "arguments", "target", 0]]),
], ids=["sleep-with-app", "travel-with-unknown-app"])
def test_invalid_group_rejects_all_items_before_occupancy_or_funds_change(engine_factory, main, side, refusal, paths):
    engine = engine_factory()
    random_state = engine.context.state.random_state
    _, results = act_group(engine, "invalid-group", main(), side())
    # 整组只给一份拒绝：两条动作的结果内容相同，原因带追加句，明细只落在负责的调用上
    code, reason, fix = refusal
    assert [item.status for item in results] == ["rejected", "rejected"]
    assert [item.error_code for item in results] == [code, code]
    assert results[0].message == results[1].message == reason + " Neither start request was started."
    assert results[0].data == results[1].data and results[0].data["fix"] == fix
    assert [item["path"] for item in results[0].data["items"]] == paths
    assert len(events(engine, "action_rejected")) == 2
    assert not engine.context.state.activities
    assert not engine.context.state.deliveries
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    assert engine.context.state.random_state == random_state


@pytest.mark.parametrize("first,second", [
    (lambda: use_app("哔哩哔哩"), lambda: use_app("抖音")),
    (lambda: travel("公司"), lambda: travel("五里公园", "公共交通")),
], ids=["two-side-calls", "two-main-calls"])
def test_same_start_tool_twice_in_one_decision_is_a_protocol_fault_and_starts_nothing(engine_factory, first, second):
    engine = engine_factory()
    decision = context(engine, "same-tool")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    request = first()
    engine.submit_action(DirectToolCall(decision, "action-same-tool-1", tool_of(request), request))
    # 同一槽位的两项不再能写进一个请求：同一个开始工具在一个决策里登记第二次是协议故障，不是业务拒绝
    request = second()
    with pytest.raises(ProtocolError, match="Agent already submitted its decision round action"):
        engine.submit_action(DirectToolCall(decision, "action-same-tool-2", tool_of(request), request))
    engine.abort_decision_round("same-tool", "expected fault")
    assert not engine.context.state.activities
    assert not engine.context.state.deliveries
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    assert set(engine.context.state.processed_actions) == {"action-same-tool-1"}
    assert engine.context.state.processed_actions["action-same-tool-1"].result["status"] == "failed"


def test_pause_resume_and_conclude_keep_actual_shared_content_progress(engine_factory):
    engine = engine_factory(mutate=lambda persona, config: config.update(progress_report_interval_seconds=60))
    _, result = act(engine, "media", use_app("Keep"))
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


def test_observation_carries_no_world_query_keys(engine_factory):
    """真实观察不再带查询的两个私有键；工具层的可调用索引没有 query_categories，goods 仍列出样例环境的四个类别；
    工具层通过 Schema。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "no-world-query"))
    assert "world_query_bindings" not in observed and "world_query_page_size" not in observed
    index = observed["tool_layer"]["callable_index"]
    assert "query_categories" not in index
    assert {row["category"] for row in index["goods"]} == {"零食", "食品", "饮料", "商品"}
    validate_layer(observed["tool_layer"])


def test_action_id_replay_is_idempotent_and_parameter_conflict_is_protocol_error(engine_factory):
    engine = engine_factory()
    request = use_app("Keep")
    _, result = act(engine, "stable", request, action_id="same-action")
    reach(engine, engine.context.state.activities[result.activity_id].planned_end_at)
    original = engine.context.state.model_dump(mode="json")
    decision = context(engine, "replay")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "same-action", tool_of(request), request))
    replay = engine.commit_decision_round("replay")[0]
    assert replay.status == "completed"
    assert len(engine.context.state.activities) == len(original["activities"]) == 1
    decision = context(engine, "conflict")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(decision, "same-action", tool_of(request), use_app("哔哩哔哩")))
    engine.abort_decision_round("conflict", "expected conflict")


def test_same_second_six_rejections_stop_without_advancing_clock(engine_factory):
    engine = engine_factory()
    now = engine.context.state.current_time
    for index in range(5):
        _, result = act(engine, f"reject-{index}", use_app("Missing"))
        assert result.status == "rejected"
    with pytest.raises(ProtocolError, match="decision_budget_exhausted"):
        act(engine, "reject-final", use_app("Missing"))
    assert engine.context.state.current_time == now
    with pytest.raises(ProtocolError, match="decision_budget_exhausted"):
        engine.scheduling_state()
    assert events(engine, "run_failed")


def test_abort_and_stale_context_never_execute_the_uncommitted_action(engine_factory):
    engine = engine_factory()
    decision = context(engine, "abort")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "never-run", MAIN_TOOL, sleep()))
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
    _, result = act(engine, "original-media", use_app("Keep"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    media = engine.context.state.activities[result.activity_id]
    _, paused = act(engine, "pause-before-trip", pause_request(media.name), "pause_activity")
    assert paused.status == "completed"
    _, trip = act(engine, "away", travel("公司"))
    finish_activity(engine, trip, "away")
    before = engine.context.state.agents[1].content_progress[media.data["content_id"]]
    _, refused = act(engine, "wrong-location", report_request(media.name), "report_status")
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert engine.context.state.activities[media.activity_id].status == "paused"
    assert engine.context.state.agents[1].content_progress[media.data["content_id"]] == before


def test_completed_interaction_keeps_its_slot_until_actual_conclusion(engine_factory):
    engine = engine_factory()
    _, result = act(engine, "finished-keep", use_app("Keep"))
    activity = engine.context.state.activities[result.activity_id]
    reach(engine, activity.planned_end_at)
    _, refused = act(engine, "pending-blocks", use_app("Keep"))
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert len(engine.context.state.activities) == 1
    _, concluded = act(engine, "release-slot", conclude_request(activity.name), "conclude_activity")
    assert concluded.status == "completed"
    _, next_activity = act(engine, "new-media", use_app("Keep"))
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
    _, result = act(engine, "declared-courier", purchase("平台笔记本"))
    assert result.status == "in_progress", result.error_code
    order = next(iter(engine.context.state.deliveries.values()))
    assert (order.fulfillment, order.destination_id) == ("station_pickup", nearest)


def test_trip_record_and_arrival_event_carry_the_declared_distance(engine_factory, tmp_path):
    """行程记录的距离等于声明值：受理结果、到达后的活动和提交的事件都是 12,600 米的直达路径。"""
    from tests.life.environment_inputs import copy_environment, write_catalog_rows

    root = copy_environment(tmp_path / "trip-input")
    write_catalog_rows(root, "map.jsonl", [{"a": "home", "b": "office", "length_m": 12600}])
    engine = engine_factory(root)
    _, result = act(engine, "declared-walk", travel("公司"))
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


#: 四个体力档位各一种的数据集出行方式，都免费。
GRADED_METHODS = [("cable", "缆车", 3, 0, 0, "idle"), ("ebike", "电动车", 6.5, 0, 0, "light"),
                  ("bike", "自行车", 4.5, 0, 0, "moderate"), ("row", "划船", 2, 0, 0, "heavy")]


@pytest.mark.parametrize("method,app,met", [
    ("步行", None, 3.5), ("缆车", None, 1.0), ("电动车", None, 1.5), ("自行车", None, 2.0), ("划船", None, 5.0),
    ("步行", "哔哩哔哩", 3.5), ("缆车", "哔哩哔哩", 1.5), ("自行车", "Keep", 5.0), ("划船", "Keep", 5.0)])
def test_travel_energy_follows_the_methods_exertion_and_the_higher_parallel_activity(
        engine_factory, tmp_path, method, app, met):
    """出行期间已提交的身体结算按该出行方式档位的代谢当量算额外消耗，步行按 3.5；
    路上同时用内容 app 时取两者里较高的代谢当量（哔哩哔哩的内容是 light，Keep 的慢跑是 heavy），基础代谢只扣一次。"""
    from tests.life.environment_inputs import copy_environment, replace_travel_methods, travel_method_row

    root = replace_travel_methods(copy_environment(tmp_path / "graded"),
                                  [travel_method_row(*row) for row in GRADED_METHODS], ("walking",))
    engine = engine_factory(root)
    if app is None:
        _, trip = act(engine, "trip", travel("公司", method))
        started = [trip]
    else:
        _, started = act_group(engine, "trip", travel("公司", method), use_app(app))
    assert [item.status for item in started] == ["in_progress"] * len(started), [item.error_code for item in started]
    identities = {item.activity_id for item in started}
    # 推进到第一次决策机会或其中一项活动结束为止，这段时间里组内的活动都在进行
    while not engine.scheduling_state().ready_agent_ids and all(
            engine.context.state.activities[key].status == "active" for key in identities):
        advance_next(engine)
    settlements = [row.data["body_settlement"] for row in events(engine, "environment_event")
                   if row.data.get("kind") == "body_settlement" and set(row.data["activity_ids"]) == identities]
    assert settlements and sum(row["duration_seconds"] for row in settlements) > 0
    for row in settlements:
        hours = row["duration_seconds"] / 3600
        assert row["basal_energy_kcal"] == pytest.approx(65 * hours)
        assert row["extra_energy_kcal"] == pytest.approx((met - 1) * 65 * hours)


def _on_the_road_home(engine_factory, tmp_path, *contents):
    """17:35 从办公室步行回家，推进到路上的第一次决策机会（每 60 秒一次播报）：此刻人物在路上、没有待收尾的活动。"""
    engine = engine_factory(dingtalk_environment(tmp_path / "input", *contents),
                            mutate=example_window(progress_report_interval_seconds=60))
    stage_moment(engine, moment(12, 17, 35), "office")
    _, departure = act(engine, "leave", travel("家", "步行"))
    assert departure.status == "in_progress", departure.error_code
    assert advance_until_ready(engine)
    state = engine.context.state
    assert state.activities[departure.activity_id].status == "active"
    assert moment(12, 17, 35) < state.current_time < moment(12, 18)
    assert [session.kind for session in state.sessions.values() if session.status == "active"] == ["transit"]
    return engine, departure


def test_person_on_the_road_cannot_open_an_app_whose_content_needs_a_place(engine_factory, tmp_path):
    """已经在路上的人物没有停留地点，既不算在出发地也不算在目的地：带地点前提的钉钉不可用，提交被同一句原因拒绝。"""
    engine, departure = _on_the_road_home(engine_factory, tmp_path)
    before = engine.context.state.model_copy(deep=True)
    observed, refused = act(engine, "on-the-road", use_app("钉钉"))
    reason = f"Nothing in 钉钉 can start here now: it requires presence at {OFFICE}."
    row = target_actions(observed, "钉钉")["interact"]
    assert (row["available"], row["reason"], row["duration"], row["note"]) == (False, reason, None, None)
    assert refused.status == "rejected" and refused.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert refused.message == reason and refused.data["unmet"] == ["content_location"]
    assert refused.data["next_step"] == f"Go to {OFFICE}."
    after = engine.context.state
    assert after.sessions == before.sessions and after.accounts == before.accounts
    assert after.random_state == before.random_state
    assert set(after.activities) == {departure.activity_id}
    assert after.activities[departure.activity_id].status == "active"


@pytest.mark.parametrize("hours,accepted", [(("17:00", "20:00"), True), (("08:00", "10:00"), False)])
def test_time_only_app_content_follows_the_clock_on_the_road(engine_factory, tmp_path, hours, accepted):
    """只带时段前提的内容在路上照常按时刻判定：时段开着就能开始，不在时段内就不能，原因里没有地点。"""
    reminder = app_content("dingtalk-reminder", "日报提醒", {"opens": weekday_hours(hours)})
    engine, _ = _on_the_road_home(engine_factory, tmp_path, reminder)
    now = engine.context.state.current_time
    observed, result = act(engine, "on-the-road", use_app("钉钉"))
    row = target_actions(observed, "钉钉")["interact"]
    if accepted:
        assert (row["available"], row["reason"], row["duration"]) == (True, None, "每条约 1 分钟")
        assert result.status == "in_progress", result.error_code
        activity = engine.context.state.activities[result.activity_id]
        assert activity.data["content_id"] == "dingtalk-reminder" and activity.started_at == now
        return
    reason = "Nothing in 钉钉 can start now: it is available 08:00–10:00 today."
    assert (row["available"], row["reason"]) == (False, reason)
    assert result.status == "rejected" and result.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert result.message == reason and result.data["unmet"] == ["content_time"]
    assert result.data["next_step"] == "Choose another action."
