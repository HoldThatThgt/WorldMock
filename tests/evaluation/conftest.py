from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from worldmock.footprints.export import export_run
from worldmock.footprints.models import Event, EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore


@dataclass(frozen=True)
class EvaluationFixture:
    scenario_path: Path
    labelkit_root: Path
    worldmock_root: Path
    human_root: Path
    output_root: Path


@pytest.fixture
def evaluation_fixture(tmp_path: Path) -> EvaluationFixture:
    return make_evaluation_fixture(tmp_path, days=1)


def make_evaluation_fixture(tmp_path: Path, days: int = 1) -> EvaluationFixture:
    base_path = Path(__file__).parents[2] / "scenarios/evaluation.json"
    scenario = json.loads(base_path.read_text(encoding="utf-8"))
    start = datetime(2026, 1, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=days)
    scenario["scenario_id"] = f"test-{days}-day-evaluation"
    scenario["window"] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "Asia/Shanghai",
    }
    scenario["bootstrap"]["repetitions"] = 100
    scenario["special_dates"] = {
        key: value
        for key, value in scenario["special_dates"].items()
        if start.date() <= datetime.fromisoformat(key).date() < end.date()
    }
    labelkit_root = tmp_path / "labelkit"
    expected = _write_labelkit(labelkit_root, start, days)
    scenario["expected_labelkit"] = expected
    scenario_path = tmp_path / "scenario.json"
    _write_json(scenario_path, scenario)
    worldmock_root = tmp_path / "worldmock"
    human_root = tmp_path / "human"
    _write_canonical(worldmock_root, scenario, "world-run", 9)
    _write_canonical(human_root, scenario, "human-run", 10)
    return EvaluationFixture(
        scenario_path,
        labelkit_root,
        worldmock_root,
        human_root,
        tmp_path / "evaluation-output",
    )


def make_review_records(output: Path, review_root: Path, candidate_pattern: bool = False) -> list[Path]:
    index = json.loads((output / "blind-review-pack.json").read_text(encoding="utf-8"))
    key = json.loads((output / "unblinding-key.json").read_text(encoding="utf-8"))
    review_root.mkdir()
    paths = []
    for descriptor in index["packets"]:
        slot = descriptor["reviewer_slot"]
        packet_path = output / "review-packets" / f"{slot}.json"
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        ratings = _ratings(packet, key, slot, candidate_pattern)
        record = {
            "schema_version": "review-record-1",
            "pack_id": index["pack_id"],
            "reviewer_slot": slot,
            "reviewer_kind": "model",
            "reviewer_session_id": f"session-{slot}",
            "model": {"provider": "test-provider", "name": "test-model"},
            "prompt_sha256": hashlib.sha256(f"prompt:{slot}".encode()).hexdigest(),
            "packet_sha256": descriptor["sha256"],
            "raw_response": f"Raw response for {slot}",
            "ratings": ratings,
        }
        path = review_root / f"{slot}.json"
        _write_json(path, record)
        paths.append(path)
    return paths


def _ratings(packet: dict[str, Any], key: dict[str, Any], slot: str, pattern: bool) -> list[dict[str, Any]]:
    ratings = []
    for index, item in enumerate(packet["items"]):
        desired = "left_candidate" if not pattern or index % 2 == 0 else "right_candidate"
        mapping = key["reviewer_mappings"][slot][item["item_id"]]
        choice = "left" if mapping["left"] == desired else "right"
        dimensions = {
            name: {
                "choice": choice if item["judgeability"][name]["judgeable"] else "unjudgeable",
                "rationale": "The packet evidence supports this assessment.",
            }
            for name in packet["dimensions"]
        }
        ratings.append({"item_id": item["item_id"], "dimensions": dimensions})
    return ratings


def _write_labelkit(root: Path, start: datetime, days: int) -> dict[str, Any]:
    out = root / "out"
    repaired = root / "repaired"
    out.mkdir(parents=True)
    repaired.mkdir()
    main_rows, stream_rows = _labelkit_rows(start, days)
    raw_main = _write_jsonl(out / "white-collar-12-weeks.jsonl", main_rows)
    raw_stream = _write_jsonl(out / "white-collar-12-weeks.stream.jsonl", stream_rows)
    raw_report = _write_json(out / "white-collar-12-weeks.report.json", {"status": "complete"})
    repaired_main = _write_jsonl(repaired / "white-collar-12-weeks.jsonl", main_rows)
    repaired_stream = _write_jsonl(repaired / "white-collar-12-weeks.stream.jsonl", stream_rows)
    run_id = "legacy-test-run"
    digest = hashlib.sha256(b"legacy-test-delivery").hexdigest()
    manifest = _legacy_manifest(run_id, digest, raw_main, raw_stream, raw_report)
    _write_json(out / "white-collar-12-weeks.manifest.json", manifest)
    repair = {
        "source": {"main_sha256": raw_main["sha256"], "stream_sha256": raw_stream["sha256"]},
        "repaired": {
            "main_sha256": repaired_main["sha256"],
            "stream_sha256": repaired_stream["sha256"],
        },
        "frames": len(stream_rows),
    }
    repair_identity = _write_json(repaired / "white-collar-12-weeks.repair-report.json", repair)
    audit = _legacy_audit(run_id, digest, len(main_rows), len(stream_rows))
    audit_identity = _write_json(repaired / "white-collar-12-weeks.final-audit.json", audit)
    return {
        "run_id": run_id,
        "delivery_digest": digest,
        "main_rows": len(main_rows),
        "stream_rows": len(stream_rows),
        "raw_main_sha256": raw_main["sha256"],
        "raw_stream_sha256": raw_stream["sha256"],
        "repaired_main_sha256": repaired_main["sha256"],
        "repaired_stream_sha256": repaired_stream["sha256"],
        "repair_report_sha256": repair_identity["sha256"],
        "final_audit_sha256": audit_identity["sha256"],
    }


def _labelkit_rows(start: datetime, days: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    main_rows = []
    stream_rows = []
    for index in range(days):
        occurred = start + timedelta(days=index, hours=9)
        sequence_id = f"sequence-{index}"
        event_id = f"legacy-event-{index}"
        main_rows.append(_legacy_main(sequence_id, event_id))
        stream_rows.append(_legacy_event(sequence_id, event_id, occurred))
    return main_rows, stream_rows


def _legacy_main(sequence_id: str, event_id: str) -> dict[str, Any]:
    return {
        "bundleName": "com.example.browser",
        "_meta": {
            "id": sequence_id,
            "source": {"file": "synthetic"},
            "stream": {
                "member_count": 1,
                "member_ids": [event_id],
                "members": [{"index": 0, "id": event_id, "label": "app"}],
            },
            "generation": {"sequence_class": "weak_no_intent", "pattern": "none"},
        },
    }


def _legacy_event(sequence_id: str, event_id: str, occurred: datetime) -> dict[str, Any]:
    milliseconds = int(occurred.timestamp() * 1000)
    return {
        "payload": {
            "bundleName": "com.example.browser",
            "dataName": "appUsageEvent",
            "duration": 3_600_000,
            "startTime": milliseconds,
        },
        "_meta": {
            "event": {
                "event_id": event_id,
                "owner_sequence_id": sequence_id,
                "actor": "user",
                "timestamp": occurred.isoformat(),
            }
        },
    }


def _legacy_manifest(
    run_id: str,
    digest: str,
    main: dict[str, Any],
    stream: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "delivery_digest": digest,
        "artifacts_committed": True,
        "main": main,
        "stream": stream,
        "report": report,
    }


def _legacy_audit(run_id: str, digest: str, main_rows: int, stream_rows: int) -> dict[str, Any]:
    return {
        "status": "pass",
        "mode": "full",
        "summary": {
            "delivery": {"run_id": run_id, "delivery_digest": digest},
            "rows": {"main_rows": main_rows, "stream_rows": stream_rows},
        },
        "finding_counts": {},
        "findings": [],
    }


#: 夹具活动类型的占用（docs/dev/SPEC-time-and-energy.md 第五章「并行准入」的占用表）；数字交互只按活动类型判定。
ACTIVITY_CLAIMS = {"browse": ["eyes", "hands"], "wait": ["idle"]}


def _write_canonical(root: Path, scenario: dict[str, Any], run_id: str, hour: int,
                     activity_type: str = "browse") -> None:
    start = datetime.fromisoformat(scenario["window"]["start"])
    end = datetime.fromisoformat(scenario["window"]["end"])
    frozen = {"run_id": run_id, "source_group_id": run_id, "agent_ids": [1],
              "window": {"start": int(start.timestamp()), "end": int(end.timestamp()),
                         "timezone": scenario["window"]["timezone"]}}
    store = FootprintStore.create(root.with_name(root.name + "-source"), frozen,
                                  {"version": 0, "current_time": int(start.timestamp())})
    for day_offset in range((end.date() - start.date()).days):
        activity_start = start + timedelta(days=day_offset, hours=hour)
        activity, rows = _world_activity(run_id, store.last_event_seq + 1, activity_start, activity_type)
        for index, row in enumerate(rows):
            snapshot = {key: value for key, value in activity.items()
                        if key not in {"activity_type", "segments", "source_event_ids"}}
            snapshot.update(kind=activity["activity_type"])
            if index == 0:
                snapshot.update(status="active", ended_at=None, elapsed_seconds=3600,
                                last_settled_at=row["occurred_at"])
            draft = EventDraft.model_validate({key: value for key, value in row.items()
                                               if key in EventDraft.model_fields})
            draft.data["activity"] = snapshot
            state = {"version": store.state["version"] + 1, "current_time": row["occurred_at"]}
            store.commit(state, [draft])
    state = {"version": store.state["version"] + 1, "current_time": int(end.timestamp())}
    store.commit(state, [EventDraft(event_type="run_ended", occurred_at=state["current_time"],
                                   visibility="system")])
    reference = store.assets.put_json(state, {"kind": "test"})
    checkpoint = store.checkpoint(state, {"agent:1": reference, "life": reference})
    export_run(store, checkpoint.last_event_seq, root)
    if run_id == "human-run":
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["artifacts"].pop("verified_experiences")
        (root / "verified-experiences.jsonl").unlink()
        manifest_path.write_text(json.dumps(manifest))
    store.close()


def _world_activity(run_id: str, sequence: int, start: datetime,
                    activity_type: str = "browse") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    first_start = int(start.timestamp())
    first_end = first_start + 3_600
    second_start = first_end + 3_600
    second_end = second_start + 3_600
    activity_id = f"activity-{sequence}"
    event_ids = [f"event-{sequence}", f"event-{sequence + 1}"]
    activity = {
        "activity_id": activity_id,
        "agent_id": 1,
        "activity_type": activity_type,
        "status": "completed",
        "started_at": first_start,
        "ended_at": second_end,
        "planned_end_at": second_end,
        "last_settled_at": second_end,
        "elapsed_seconds": 7_200,
        "planned_duration_seconds": 7_200,
        "effective_work_seconds": 0,
        "media_position_seconds": 0,
        "route_progress_meters": 0,
        "next_interaction_at": None,
        "target_id": None,
        "destination_id": None,
        "travel_mode": None,
        "playback_speed": 1,
        "resource_claims": list(ACTIVITY_CLAIMS[activity_type]),
        "boundary_type": "planned_end",
        "interruption_reason": None,
        "data": {},
        "segments": [
            {"started_at": first_start, "ended_at": first_end},
            {"started_at": second_start, "ended_at": second_end},
        ],
        "source_event_ids": event_ids,
    }
    events = [
        _progress_event(run_id, sequence, event_ids[0], activity_id, first_start, first_end),
        _progress_event(run_id, sequence + 1, event_ids[1], activity_id, second_start, second_end),
    ]
    return activity, events


def _progress_event(
    run_id: str, sequence: int, event_id: str, activity_id: str, start: int, end: int
) -> dict[str, Any]:
    caused_by = [] if sequence % 2 else [f"event-{sequence - 1}"]
    event = Event(
        run_id=run_id,
        event_id=event_id,
        event_seq=sequence,
        event_type="activity_progress",
        occurred_at=end,
        agent_id=1,
        activity_id=activity_id,
        caused_by=caused_by,
        data={"interval_start": start, "interval_end": end},
        visibility="agent",
        source_kind="environment",
        recorded_at="2026-09-15T00:00:00+08:00",
        wall_elapsed_ms=0,
        state_version_before=sequence - 1,
        state_version_after=sequence,
    )
    return event.model_dump(mode="json")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    path.write_bytes(raw)
    return {"path": str(path.resolve()), "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}


def _write_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
