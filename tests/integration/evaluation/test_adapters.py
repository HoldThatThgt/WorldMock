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
    worldmock_activity_action,
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


def test_actual_export_duration_is_conserved_and_right_boundary_is_accepted(
    evaluation_fixture,
) -> None:
    scenario = load_scenario(evaluation_fixture.scenario_path)
    view = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)

    activity = view.activities[0]
    assert activity.duration_seconds == (activity.ended_at - activity.started_at).total_seconds()
    assert activity.duration_seconds > 0
    assert set(view.days[0].activity_ids) == {item.activity_id for item in view.activities}
    assert view.days[0].event_count == sum(
        scenario.window.contains(event.occurred_at) for event in view.events)


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
    assert view.events
    assert view.activities[0].ended_at == scenario.window.end
    assert view.activities[0].duration_seconds == 86400
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


def _legacy_action_row(sequence, action, timestamp, fields):
    return {"actionInfo": {"timestamp": timestamp, **fields}, "_meta": {
        "id": sequence, "generation": {"sequence_class": action}, "stream": {"member_ids": []}}}


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

    assert [(item.action, item.interaction, item.state) for item in evidence] == [
        ("purchase", "order", "declared"),
        ("travel", "travel", "declared"),
    ]
    assert all(item.object_key is None for item in evidence)
    assert {item.session_key for item in evidence} == {"order-session", "route-session"}
    public = json.dumps([item.field_values() for item in evidence])
    assert all(canary not in public for canary in ("BRAND_CANARY", "ITEM_CANARY", "PLACE_CANARY"))


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


def test_graph_travel_result_projects_trip_identity_and_full_path(engine_factory):
    """真实引擎图行程直接进入公开证据，身份只来自实际行程。"""
    from tests.life.environment_runtime import act, events
    from tests.life.conftest import travel
    from worldmock.life.models import PlanDayRequest

    engine = engine_factory()
    engine.register_day_plan(1, PlanDayRequest(goals=()), [], [])
    _, result = act(engine, "drive-office", travel("公司", "驾车"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    snapshot = activity.model_dump(mode="json")
    assert snapshot["location_id"] == "home" and snapshot["destination_id"] == "office"
    assert snapshot["data"]["path"][0] == "home" and snapshot["data"]["path"][-1] == "office"
    assert snapshot["data"]["distance_meters"] > 0
    evidence = worldmock_public_evidence(events(engine), [])
    route = [row for row in evidence if row.object_type == "route"]
    assert {row.state for row in route} == {"requested", "started", "paid"}
    assert {row.action for row in route} == {"travel"}
    assert all(row.attributes == {} for row in route)
    assert {row.object_key for row in route} == {snapshot["data"]["trip_id"]}
    assert not any(key in json.dumps(snapshot) for key in ("longitude", "latitude", "coordinate", "trajectory"))


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


def test_actual_progress_segments_exclude_the_paused_gap(evaluation_fixture):
    """实际片段累计两小时，开始到结束三小时的暂停空档不能计入时长。"""
    scenario = load_scenario(evaluation_fixture.scenario_path)
    delivery = VerifiedDelivery("world-run", "worldmock:world-run", scenario.window, Path("events"),
                                Path("activities"), {}, {}, ("world-run",))
    row, _ = _world_activity("world-run", 1, scenario.window.start + timedelta(hours=9))
    activity = _adapt_world_activity(row, 1, delivery, scenario)
    assert (activity.ended_at - activity.started_at).total_seconds() == 10800
    assert activity.duration_seconds == 7200
    assert activity.action == "interact"


@pytest.mark.parametrize("app_id,channel,duration_kind,domains", [
    ("meituan", "digital_interaction", "world_internet_session", ("internet_use",)),
    (None, "physical_activity", "world_activity", ()),
], ids=["with-app", "without-app"])
def test_query_with_an_app_is_a_digital_interaction_and_without_one_a_physical_activity(
        evaluation_fixture, app_id, channel, duration_kind, domains):
    """查询是自带 app 的活动：带 app 的查询那几分钟算在手机上，是数字交互；没有 app 的查询是现实活动。"""
    scenario = load_scenario(evaluation_fixture.scenario_path)
    delivery = VerifiedDelivery("world-run", "worldmock:world-run", scenario.window, Path("events"),
                                Path("activities"), {}, {}, ("world-run",))
    row, _ = _world_activity("world-run", 1, scenario.window.start + timedelta(hours=9), "query")
    row["data"] = {"action": "query", "app_id": app_id}
    activity = _adapt_world_activity(row, 1, delivery, scenario)
    assert activity.action == "query"
    assert (activity.channels, activity.duration_kind, activity.official_domains) == ((channel,), duration_kind,
                                                                                      domains)
