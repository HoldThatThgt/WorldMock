from __future__ import annotations

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


REAL_LEGACY = Path(
    "/Users/atishoo/Project/Dataset-Person/archive/white-collar-12-weeks-deepseek16-glm52"
)
FORMAL_SCENARIO = Path(__file__).parents[2] / "scenarios/evaluation.json"


def test_real_labelkit_delivery_is_certified_and_conserved() -> None:
    scenario = load_scenario(FORMAL_SCENARIO)
    view = adapt_labelkit(REAL_LEGACY, scenario)

    assert len(view.events) == 16_320
    assert len(view.activities) == 4_380
    assert len(view.days) == 84
    assert view.mapping_report()["dropped_events"] == 0
    assert view.artifacts["repaired_stream"].sha256 == (
        "835eae012007f0841e0c831ed696aa3caea4cd77fb552032917cc6a834a58009"
    )
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
    _write_canonical(root, json.loads(evaluation_fixture.scenario_path.read_text()), "end-run", 21)
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


def test_consume_category_uses_actual_closed_use_mode_for_evidence_and_duration() -> None:
    mapping = {"consume": "product_use"}
    assert worldmock_activity_category("consume", {"request": {"use_mode": "eat"}}, mapping) == "eat"
    assert worldmock_activity_category("consume", {"request": {"use_mode": "use"}}, mapping) == "product_use"
    with pytest.raises(EvaluationError, match="use_mode must be eat or use"):
        worldmock_activity_category("consume", {"request": {"use_mode": "drink"}}, mapping)

    events = [
        _public_event(1, "action_requested", {
            "tool_name": "consume",
            "request": {"product_id": "food-private", "use_mode": "eat"},
        }, "eat-action"),
        _public_event(2, "action_requested", {
            "tool_name": "consume",
            "request": {"product_id": "umbrella-private", "use_mode": "use"},
        }, "use-action"),
    ]
    evidence = worldmock_public_evidence(events, [], mapping)
    assert [(item.category, item.state) for item in evidence] == [
        ("eat", "requested"),
        ("product_use", "requested"),
    ]


def test_worldmock_activity_duration_uses_the_same_consume_category(evaluation_fixture) -> None:
    scenario = load_scenario(evaluation_fixture.scenario_path)
    delivery = VerifiedDelivery(
        "world-run", "world-dataset", scenario.window, Path("events.jsonl"),
        Path("activities.jsonl"), {}, {}, ("world-run",),
    )
    row, _ = _world_activity("world-run", 1, scenario.window.start + timedelta(hours=9))
    row.update(activity_type="consume", data={"request": {"use_mode": "use"}})

    product_use = _adapt_world_activity(row, 1, delivery, scenario)
    row["data"]["request"]["use_mode"] = "eat"
    eating = _adapt_world_activity(row, 1, delivery, scenario)

    assert product_use.category == "product_use"
    assert eating.category == "eat"
    assert product_use.duration_seconds == eating.duration_seconds == 7_200


def test_purchase_payment_uses_canonical_resource_changes_and_preserves_actual_states() -> None:
    request = _public_event(1, "action_requested", {
        "tool_name": "purchase",
        "request": {"product_id": "PRODUCT_CANARY", "fulfillment": "delivery"},
    }, "purchase-action")
    result_data = {
        "status": "completed",
        "data": {
            "order": {
                "order_id": "order-private",
                "status": "pending",
                "fulfillment": "delivery",
                "product_name": "RAW_PRODUCT_CANARY",
            },
            "resource_changes": {"account:buyer": -500, "account:merchant": 500},
        },
        "resource_changes": {"account:buyer": -500, "account:merchant": 500},
    }
    result = _public_event(2, "action_result", result_data, "purchase-action")
    delivered = _public_event(3, "environment_event", {
        "kind": "delivery_arrival",
        "order": {"order_id": "order-private", "status": "delivered", "fulfillment": "delivery"},
    })

    evidence = worldmock_public_evidence([request, result, delivered], [], {})

    assert [(item.interaction, item.state) for item in evidence] == [
        ("order", "requested"),
        ("order", "pending"),
        ("pay", "paid"),
        ("deliver", "delivered"),
    ]
    assert {item.object_key for item in evidence} == {"order-private"}
    without_canonical = dict(result_data)
    without_canonical.pop("resource_changes")
    no_payment = worldmock_public_evidence(
        [request, _public_event(2, "action_result", without_canonical, "purchase-action")], [], {}
    )
    assert all(item.interaction != "pay" for item in no_payment)


def test_control_and_game_results_use_object_state_instead_of_receipt_status() -> None:
    activity = {
        "activity_id": "activity-private",
        "activity_type": "browse",
        "target_id": "content-private",
        "travel_mode": None,
        "data": {},
    }
    active = {"status": "active", "data": {}}
    paused = {"status": "paused", "data": {}}
    events = [
        _public_event(1, "action_requested", {
            "tool_name": "continue_activity", "request": {"activity_id": "activity-private"},
        }, "continue-action"),
        _public_event(2, "action_result", {
            "status": "completed", "data": {"activity": active},
        }, "continue-action", "activity-private"),
        _public_event(3, "action_requested", {
            "tool_name": "set_activity_state",
            "request": {"activity_id": "activity-private", "operation": "pause"},
        }, "pause-action"),
        _public_event(4, "action_result", {
            "status": "completed", "data": {"activity": paused},
        }, "pause-action", "activity-private"),
        _public_event(5, "action_requested", {
            "tool_name": "start_targeted_activity",
            "request": {"activity_type": "play_game", "target_id": "game-private"},
        }, "game-action"),
        _public_event(6, "action_result", {
            "status": "in_progress", "data": {"activity": {"status": "active", "data": {}}},
        }, "game-action"),
    ]

    evidence = worldmock_public_evidence(events, [activity], {"browse": "browsing"})
    states = {(item.interaction, item.state) for item in evidence}

    assert ("continue", "started") in states
    assert ("pause", "paused") in states
    assert ("play", "started") in states
    assert ("continue", "completed") not in states
    assert ("pause", "completed") not in states
    controls = [item for item in evidence if item.interaction in {"continue", "pause"}]
    assert {item.object_type for item in controls} == {"activity"}
    assert {item.object_key for item in controls} == {"activity-private"}

    terminal = list(events)
    terminal[-1] = _public_event(6, "action_result", {
        "status": "completed", "data": {"activity": {"status": "completed", "data": {}}},
    }, "game-action")
    terminal_states = worldmock_public_evidence(terminal, [activity], {"browse": "browsing"})
    assert ("play", "completed") in {(item.interaction, item.state) for item in terminal_states}


def test_mail_and_work_deadline_keep_public_facts_without_raw_text_or_failure_inference() -> None:
    events = [
        _public_event(1, "environment_event", {
            "kind": "mail_arrival", "message_id": "message-private", "subject": "SUBJECT_CANARY",
        }),
        _public_event(2, "action_requested", {
            "tool_name": "start_targeted_activity",
            "request": {"activity_type": "read_mail", "target_id": "message-private"},
        }, "mail-action"),
        _public_event(3, "action_result", {
            "status": "completed",
            "data": {"activity": {"status": "completed", "data": {"revealed": {"body": "BODY_CANARY"}}}},
        }, "mail-action", "mail-activity"),
        _public_event(4, "action_requested", {
            "tool_name": "submit_work", "request": {"task_id": "task-private"},
        }, "work-action"),
        _public_event(5, "action_result", {
            "status": "completed",
            "data": {"work_review": {"accepted": True}, "payment_fen": 1000},
            "resource_changes": {"account:employer": -1000, "account:worker": 1000},
        }, "work-action"),
        _public_event(6, "environment_event", {"kind": "task_expired", "task_id": "task-private"}),
    ]

    evidence = worldmock_public_evidence(events, [], {})
    states = {(item.interaction, item.state) for item in evidence}

    assert {"received", "requested", "read"} <= {item.state for item in evidence}
    assert {("submit", "accepted"), ("pay", "paid"), ("deadline", "reached")} <= states
    assert not {"expired", "failed", "rejected"} & {item.state for item in evidence}
    public = json.dumps([item.field_values() for item in evidence])
    assert "SUBJECT_CANARY" not in public and "BODY_CANARY" not in public


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


def _refresh_artifact_hash(root: Path, name: str, raw: bytes, rows: int) -> None:
    import hashlib

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][name]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["artifacts"][name]["rows"] = rows
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
