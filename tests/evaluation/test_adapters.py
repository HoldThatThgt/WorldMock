from __future__ import annotations

from worldmock.evaluation.adapters import parse_world_event
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.evaluation.conftest import _world_activity, _write_canonical
from worldmock.evaluation.adapters import _adapt_world_activity, adapt_labelkit, adapt_worldmock
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.public_evidence import (
    labelkit_public_evidence,
    worldmock_activity_category,
    worldmock_public_evidence,
)
from worldmock.evaluation.scenario import load_scenario
from worldmock.evaluation.validation import VerifiedDelivery
from worldmock.footprints.models import Event


def test_labelkit_delivery_is_certified_and_conserved(evaluation_fixture) -> None:
    scenario = load_scenario(evaluation_fixture.scenario_path)
    view = adapt_labelkit(evaluation_fixture.labelkit_root, scenario)

    assert view.events
    assert view.activities
    assert len(view.days) == 1
    assert view.mapping_report()["dropped_events"] == 0
    assert view.artifacts["repaired_stream"].sha256 == scenario.expected_labelkit[
        "repaired_stream_sha256"]
    assert {item.calendar_date for item in view.days} == set(scenario.window.days)


def test_worldmock_paused_gap_is_not_counted_and_right_boundary_is_accepted(
    evaluation_fixture,
) -> None:
    scenario = load_scenario(evaluation_fixture.scenario_path)
    view = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    activity = view.activities[0]
    assert (activity.ended_at - activity.started_at).total_seconds() == 10_800
    assert activity.duration_seconds == 7_200
    day = view.days[0]
    assert day.slot_categories[60] == "none"
    assert len(view.events) == 4
    assert day.event_count == 2


def test_canonical_hash_tampering_is_rejected(evaluation_fixture) -> None:
    path = evaluation_fixture.worldmock_root / "events.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    scenario = load_scenario(evaluation_fixture.scenario_path)

    with pytest.raises(EvaluationError, match="SHA-256 mismatch") as captured:
        adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    assert captured.value.code == "hash_mismatch"


def test_daily_coverage_cannot_replace_a_missing_day(evaluation_fixture) -> None:
    manifest_path = evaluation_fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["daily_coverage"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scenario = load_scenario(evaluation_fixture.scenario_path)

    with pytest.raises(EvaluationError, match="daily coverage") as captured:
        adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    assert captured.value.code == "invalid_input"


def test_partial_worldmock_export_is_rejected_even_when_committed(evaluation_fixture) -> None:
    manifest_path = evaluation_fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["experiment_completed"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scenario = load_scenario(evaluation_fixture.scenario_path)

    with pytest.raises(EvaluationError, match="experiment_completed") as captured:
        adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    assert captured.value.code == "invalid_input"


def test_checkpoint_record_hash_is_recomputed(evaluation_fixture) -> None:
    manifest_path = evaluation_fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checkpoint"]["state"]["version"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scenario = load_scenario(evaluation_fixture.scenario_path)

    with pytest.raises(EvaluationError, match="record hash") as captured:
        adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    assert captured.value.code == "hash_mismatch"


def test_activity_segment_elapsed_mismatch_is_rejected(evaluation_fixture) -> None:
    activity_path = evaluation_fixture.worldmock_root / "activities.jsonl"
    row = json.loads(activity_path.read_text(encoding="utf-8").splitlines()[0])
    row["elapsed_seconds"] += 1
    raw = json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    activity_path.write_bytes(raw)
    _refresh_artifact_hash(evaluation_fixture.worldmock_root, "activities", raw, 1)
    scenario = load_scenario(evaluation_fixture.scenario_path)

    with pytest.raises(EvaluationError, match="authoritative events") as captured:
        adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    assert captured.value.code == "invalid_input"


def test_actual_activity_progress_at_exclusive_end_is_kept_without_an_extra_day(evaluation_fixture, tmp_path):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    root = tmp_path / "ending-activity"
    _write_canonical(root, json.loads(evaluation_fixture.scenario_path.read_text(encoding="utf-8")), "end-run", 21)
    view = adapt_worldmock(root, scenario)

    assert len(view.days) == 1
    assert len(view.events) == 4
    assert view.activities[0].ended_at == scenario.window.end
    assert view.activities[0].duration_seconds == 7200
    assert any(event.event_family == "activity_progress" and event.occurred_at == scenario.window.end
               for event in view.events)


def test_evaluation_uses_complete_export_quality_authentication(evaluation_fixture):
    path = evaluation_fixture.worldmock_root / "quality.json"
    path.write_text('{}')

    with pytest.raises(EvaluationError, match="quality report"):
        adapt_worldmock(evaluation_fixture.worldmock_root, load_scenario(evaluation_fixture.scenario_path))


def test_passing_manifest_cannot_override_failed_final_audit(evaluation_fixture) -> None:
    audit_path = evaluation_fixture.labelkit_root / "repaired/white-collar-12-weeks.final-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["status"] = "fail"
    raw = json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    audit_path.write_bytes(raw)
    scenario_path = evaluation_fixture.scenario_path
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    import hashlib

    scenario["expected_labelkit"]["final_audit_sha256"] = hashlib.sha256(raw).hexdigest()
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    with pytest.raises(EvaluationError, match="did not pass") as captured:
        adapt_labelkit(evaluation_fixture.labelkit_root, load_scenario(scenario_path))

    assert captured.value.code == "audit_failed"


def test_legacy_action_declarations_never_claim_completion_or_object_identity() -> None:
    timestamp = 1_767_600_000_000
    rows = [
        _legacy_action_row(
            "order-session", "food_order", timestamp,
            {"fulfillmentMode": "delivery", "brandName": "BRAND_CANARY", "items": ["ITEM_CANARY"]},
        ),
        _legacy_action_row(
            "route-session", "navigation", timestamp + 1,
            {"travelMode": "transit", "destinationName": "PLACE_CANARY"},
        ),
    ]

    evidence = labelkit_public_evidence(
        rows, [], {"order-session": "purchase", "route-session": "travel"}
    )

    assert [(item.category, item.interaction, item.state) for item in evidence] == [
        ("purchase", "order", "declared"),
        ("travel", "travel", "declared"),
    ]
    assert all(item.object_key is None for item in evidence)
    assert {item.session_key for item in evidence} == {"order-session", "route-session"}
    public = json.dumps([item.field_values() for item in evidence])
    assert all(canary not in public for canary in ("BRAND_CANARY", "ITEM_CANARY", "PLACE_CANARY"))


@pytest.mark.parametrize("category", ["entertainment", "work_study", "social", "exercise", "daily_care"])
def test_action_category_is_the_actual_closed_category_and_ignores_claims(category):
    assert worldmock_activity_category("action", {"category": category}, {"action": "ignored"}) == category
    for retired in ("eat", "work", "fitness", "product_use"):
        with pytest.raises(EvaluationError, match="fixed category"):
            worldmock_activity_category("action", {"category": retired}, {})
    for retired in ("consume", "use_app", "lifestyle", "read_mail"):
        with pytest.raises(EvaluationError, match="closed set"):
            worldmock_activity_category(retired, {}, {})


def test_worldmock_activity_duration_uses_the_same_action_category(evaluation_fixture):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    delivery = VerifiedDelivery("world-run", "world-dataset", scenario.window, Path("events.jsonl"),
                                Path("activities.jsonl"), {}, {}, ("world-run",))
    row, _ = _world_activity("world-run", 1, scenario.window.start + timedelta(hours=9))
    row["data"].update(category="exercise", app_id=None, manner=None)
    exercise = _adapt_world_activity(row, 1, delivery, scenario)
    row["data"].update(category="daily_care")
    care = _adapt_world_activity(row, 1, delivery, scenario)
    assert exercise.category == "exercise" and care.category == "daily_care"
    assert exercise.duration_seconds == care.duration_seconds == 7200


@pytest.mark.parametrize("kind,app,manner,claims,digital", [
    ("action", "douyin", "phone_continuous", [], True),
    ("action", "weixin", "offline_assisted", ["social"], True),
    ("action", "computer", "other_device", ["work_study"], True),
    ("action", None, None, ["use_app"], False),
    ("travel", None, None, ["travel", "use_app"], False),
    ("wait", None, None, ["wait"], False),
])
def test_digital_interaction_uses_actual_app_in_action_snapshot(kind,app,manner,claims,digital):
    from types import SimpleNamespace
    from worldmock.evaluation.models import TimeWindow
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    window = TimeWindow(start, start + timedelta(days=1), "UTC")
    scenario = SimpleNamespace(window=window, official_domain_map={},
                               worldmock_activity_map={"travel": "travel", "wait": "waiting"})
    delivery = VerifiedDelivery("world-run", "world-dataset", window, Path("events.jsonl"),
                                Path("activities.jsonl"), {}, {}, ("world-run",))
    row, _ = _world_activity("world-run", 1, start + timedelta(hours=9), activity_type="wait")
    row.update(activity_type=kind, resource_claims=claims,
               data={"category":"work_study", "app_id":app, "manner":manner})
    session = _adapt_world_activity(row, 1, delivery, scenario)
    assert session.channels == (("digital_interaction",) if digital else ("physical_activity",))
    assert session.duration_kind == ("world_internet_session" if digital else "world_activity")
    assert ("internet_use" in session.official_domains) is digital
    assert session.source_channels == tuple(sorted(claims))


def test_start_items_follow_actual_action_category_and_order_without_guessing_names():
    def requested(way,target):
        return _public_event(1,"action_requested",{"tool_name":"start_activity","request":{
            "items":[{"target":target,"way":way,"app":None,"to":None,"pay_app":None}],"answering":None}},"start")
    started = _public_event(2,"action_result",{"status":"in_progress","data":{"activity":{
        "kind":"action","status":"active","data":{"category":"daily_care","content_id":"real-content", "app_id":None}}}},"start")
    ordered = _public_event(2,"action_result",{"status":"completed","data":{"orders":[
        {"order_id":"real-order","status":"pending","fulfillment":"delivery"}]}},"start")
    def shapes(rows):
        return [(item.category,item.interaction,item.state,item.object_key)
                for item in worldmock_public_evidence([parse_world_event(row) for row in rows],[],{})]
    assert shapes([requested("进行","听电影"),started]) == [
        ("daily_care","do","requested","real-content"),("daily_care","do","started","real-content")]
    assert shapes([requested("购买","饭"),ordered]) == [
        ("purchase","order","requested","real-order"),("purchase","order","pending","real-order")]
    assert shapes([requested("进行","办公")]) == []


def test_purchase_payment_uses_canonical_resource_changes_and_preserves_actual_states():
    request = _public_event(1,"action_requested",{"tool_name":"start_activity","request":{
        "items":[{"target":"午餐","way":"购买","app":"购物","to":None,"pay_app":"支付"}],"answering":None}},"purchase")
    payload = {"orders":[{"order_id":"o1","status":"pending","fulfillment":"delivery",
                           "product_name":"RAW_PRODUCT_CANARY"}],
               "resource_changes":{"account:buyer":-500,"account:merchant":500}}
    result = _public_event(2,"action_result",{"status":"completed","data":payload},"purchase")
    delivered = _public_event(3,"environment_event",{"kind":"delivery_arrival","orders":[
        {"order_id":"o1","status":"delivered","fulfillment":"delivery"}]})
    received = _public_event(4,"environment_event",{"kind":"goods_received","order_id":"o1","category":"meal"})
    evidence = worldmock_public_evidence([parse_world_event(row) for row in [request,result,delivered,received]],[],{})
    assert [(item.interaction,item.state) for item in evidence] == [
        ("order","requested"),("order","pending"),("pay","paid"),("deliver","delivered"),("receive","received")]
    assert {item.object_key for item in evidence} == {"o1"}
    assert "RAW_PRODUCT_CANARY" not in json.dumps([item.field_values() for item in evidence])
    for changes in ({}, {"account:buyer":0}, {"account:buyer":True}, {"stock:goods":-1}):
        alternative = _public_event(2,"action_result",{"status":"completed","data":{**payload,"resource_changes":changes}},"purchase")
        rows = worldmock_public_evidence([parse_world_event(row) for row in [request,alternative]],[],{})
        assert all(item.interaction != "pay" for item in rows)


def test_control_and_action_results_use_object_state_instead_of_receipt_status():
    activity = {"activity_id":"activity-private","activity_type":"action","status":"active",
                "data":{"category":"entertainment","content_id":"content-private","app_id":None}}
    name = "刷推荐（1月5日 09:00 起）"
    events = []
    for index,tool in enumerate(("report_status","pause_activity")):
        key = f"control-{index}"
        events += [_public_event(1+index*2,"action_requested",{"tool_name":tool,"request":{
            "items":[{"activity_name":name,"summary":"RAW_SUMMARY_CANARY"}],"errands":[]}},key),
            _public_event(2+index*2,"action_result",{"status":"completed","data":{
                "activity_ids":["activity-private"]}},key,"activity-private")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in events],[activity],{})
    assert {(item.interaction,item.state) for item in evidence} == {
        ("report","requested"),("report","started"),("pause","requested"),("pause","paused")}
    assert {item.object_type for item in evidence} == {"activity"}
    assert {item.object_key for item in evidence} == {"activity-private"}
    assert "RAW_SUMMARY_CANARY" not in json.dumps([item.field_values() for item in evidence])


def test_mail_and_work_content_keep_public_facts_without_raw_text_or_failure_inference():
    events = [_public_event(1,"environment_event",{"kind":"mail_arrival","message_id":"message-private",
                                                  "subject":"SUBJECT_CANARY"}),
              _public_event(2,"environment_event",{"kind":"work_content_completed","content_id":"content-private",
                                                  "title":"BODY_CANARY"})]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in events],[],{})
    assert [(item.category,item.interaction,item.state,item.object_type) for item in evidence] == [
        ("work_study","notify","received","message"),("work_study","work","completed","content")]
    assert not {"expired","failed","rejected"} & {item.state for item in evidence}
    public = json.dumps([item.field_values() for item in evidence])
    assert "SUBJECT_CANARY" not in public and "BODY_CANARY" not in public


def test_parallel_travel_and_action_project_two_actual_activities():
    requested = _public_event(1,"action_requested",{"tool_name":"start_activity","request":{
        "items":[{"target":"公园","way":"步行","app":None,"to":None,"pay_app":None},
                 {"target":"刷推荐","way":"进行","app":"哔哩哔哩","to":None,"pay_app":None}],
        "answering":None}},"parallel")
    result = _public_event(2,"action_result",{"status":"in_progress","data":{"activities":[
        {"kind":"travel","travel_mode":"walking","status":"active","data":{"trip_id":"route-private"}},
        {"kind":"action","status":"active","data":{"category":"entertainment","content_id":"content-private",
            "app_id":"bilibili","manner":"phone_continuous"}}],"activity_results":[{},{}]}},"parallel")
    evidence = worldmock_public_evidence([parse_world_event(row) for row in [requested,result]],[],{})
    assert {(item.interaction,item.state,item.object_key) for item in evidence} == {
        ("travel","requested","route-private"),("travel","started","route-private"),
        ("view","requested","content-private"),("view","started","content-private")}
    assert all("公园" not in json.dumps(item.field_values(),ensure_ascii=False) for item in evidence)


def _legacy_action_row(
    sequence_id: str, sequence_class: str, timestamp: int, action: dict[str, Any]
) -> dict[str, Any]:
    """构造一条只带声明的 Legacy 主记录。"""
    return {
        "actionInfo": {"timestamp": timestamp, **action},
        "_meta": {
            "id": sequence_id,
            "generation": {"sequence_class": sequence_class},
            "stream": {"member_ids": [f"event-{sequence_id}"]},
        },
    }


def _public_event(
    sequence: int,
    event_type: str,
    data: dict[str, Any],
    action_id: str | None = None,
    activity_id: str | None = None,
) -> dict[str, Any]:
    """构造通过生产 Event 模型校验的公开投影输入。"""
    event = Event(
        run_id="public-test-run",
        event_id=f"public-event-{sequence}",
        event_seq=sequence,
        event_type=event_type,
        occurred_at=int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()) + sequence,
        agent_id=1,
        action_id=action_id,
        activity_id=activity_id,
        data=data,
        recorded_at="2026-01-05T00:00:00+00:00",
        wall_elapsed_ms=0,
        state_version_before=sequence - 1,
        state_version_after=sequence,
    )
    return event.model_dump(mode="json")


def test_graph_travel_result_projects_trip_identity_and_full_path():
    """@brief 生产图计划的真实活动快照进入事件和公开证据，不调用导出。"""
    from tests.life.test_travel_graph import travel_case
    from worldmock.footprints.timeline_labels import _load_render_day
    from worldmock.life.activity_titles import TitleIndex, activity_snapshot
    from worldmock.life.observe import _location_catalog
    from worldmock.life.world_setup import load_runtime_scenario

    _, _, state, trip = travel_case("driving")
    snapshot = activity_snapshot(state.activities["travel-1"], TitleIndex({}, {}, {}))
    request = _public_event(1, "action_requested", {"tool_name": "start_activity", "request": {
        "items": [{"target": "地点：公司", "way": "驾车", "app": None, "to": None, "pay_app": None}],
        "answering": None}}, "action-1")
    result = _public_event(2, "action_result", {"status": "in_progress", "data": {
        "activity": snapshot, "resource_changes": {"account:agent:1": -trip.fare_fen,
                                                    "account:transport:service": trip.fare_fen}}}, "action-1")
    semantics, _ = load_runtime_scenario(Path(__file__).resolve().parents[1] /
                                        "fixtures/environment/scenarios/single-person.json")
    observation = _public_event(3, "observation", {"locations": _location_catalog(semantics.scenario)})
    names = _load_render_day().name_index([observation])
    assert names["locations"]["home"] == "家" and names["locations"]["office"] == "公司"
    assert snapshot["location_id"] == "home" and snapshot["destination_id"] == "office"
    assert snapshot["data"]["path"] == ["home", "noodle-1", "office"]
    assert snapshot["data"]["distance_meters"] == 3274
    artifact = json.dumps([request, result, observation, names])
    assert not any(key in artifact for key in ("route_id", "longitude", "latitude", "coordinate", "trajectory"))
    evidence = worldmock_public_evidence([parse_world_event(row) for row in (request, result)], [], {})
    assert {(row.category, row.state, row.object_type, row.object_key) for row in evidence} == {
        ("travel", "requested", "route", trip.trip_id), ("travel", "started", "route", trip.trip_id),
        ("travel", "paid", "route", trip.trip_id)}


def _refresh_artifact_hash(root: Path, name: str, raw: bytes, rows: int) -> None:
    import hashlib

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][name]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["artifacts"][name]["rows"] = rows
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("source_kind,visibility", [
    (source, visibility) for source in ("environment", "model", "scenario", "external")
    for visibility in ("agent", "evaluation", "system") if (source, visibility) != ("environment", "agent")
])
def test_activity_intervals_reject_non_environment_or_private_progress(source_kind, visibility, caplog):
    """@brief 即使完整事件和正时长区间有效，私有或非环境记录也不能填充评估占用秒数。"""
    from worldmock.evaluation.adapters import _world_interval

    _, rows = _world_activity("run", 1, datetime(2026, 1, 5, 9, tzinfo=timezone.utc))
    row = dict(rows[0], source_kind=source_kind, visibility=visibility)
    parsed = parse_world_event(row)
    with pytest.raises(EvaluationError, match="agent-visible environment event") as captured:
        _world_interval(parsed)
    assert captured.value.code == "invalid_input"
    assert "agent-visible environment event" in caplog.text
