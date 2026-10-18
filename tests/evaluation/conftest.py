from __future__ import annotations

from tests import data_paths

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from worldmock.life.activity_control import open_activities
from worldmock.footprints.export import export_run
from worldmock.footprints.models import Event, EventDraft, canonical_json
from tests.data_paths import ENVIRONMENT_ROOT
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
    base_path = Path(__file__).parent / "fixtures/scenario.json"
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


_CANONICAL_CACHE: dict[tuple, Path] = {}


def _write_canonical(root: Path, scenario: dict[str, Any], run_id: str, hour: int,
                     activity_type: str = "interact") -> None:
    """通过当前引擎和冻结环境真实生成、结算并导出，不拼装旧运行格式。"""
    import shutil
    import tomlkit
    cache_key = (scenario["window"]["start"], scenario["window"]["end"], run_id, activity_type)
    if cache_key in _CANONICAL_CACHE:
        shutil.copytree(_CANONICAL_CACHE[cache_key], root)
        return
    from worldmock.catalog.identity import refresh_manifest_identity
    from worldmock.environment import load_environment
    from tests.frozen_inputs import fixture_metadata
    from worldmock.life.engine import LifeEngine
    from tests.integration.runtime_fixtures import current_run_identity

    environment = root.with_name(root.name + "-environment")
    shutil.copytree(ENVIRONMENT_ROOT, environment)
    config = tomlkit.parse((environment / "config.toml").read_text(encoding="utf-8"))
    config.update(start_time=datetime.fromisoformat(scenario["window"]["start"]),
                  stop_time=datetime.fromisoformat(scenario["window"]["end"]),
                  activity_feedback_seconds=86400, reconsideration_interval_seconds=86400,
                  progress_report_interval_seconds=86400)
    (environment / "config.toml").write_text(tomlkit.dumps(config), encoding="utf-8", newline="\n")
    persona = tomlkit.parse((environment / "persona.toml").read_text(encoding="utf-8"))
    persona.pop("work_schedule", None)
    (environment / "persona.toml").write_text(tomlkit.dumps(persona), encoding="utf-8", newline="\n")
    contents_path = environment / "catalog/content-catalog.jsonl"
    contents = [json.loads(line) for line in contents_path.read_text(encoding="utf-8").splitlines()]
    contents = [row for row in contents if "douyin" not in row.get("app_ids", []) or row["content_id"] == "c-long"]
    next(row for row in contents if row["content_id"] == "c-long")["duration_seconds"] = 86400
    contents_path.write_bytes(b"".join(canonical_json(row) + b"\n" for row in contents))
    refresh_manifest_identity(environment / "catalog")
    inputs = load_environment(environment, metadata_loader=fixture_metadata)
    identity = current_run_identity(inputs, run_id)
    from worldmock.run_setup import stage_model_identity
    stages = stage_model_identity(inputs.config.model, inputs.model_metadata, same_second_continuation=False)
    engine = LifeEngine.create(inputs, root.with_name(root.name + "-source"), identity, stages, {})
    try:
        _complete_engine(engine, activity_type)
        ref = engine.context.store.assets.put_json({"test": "actual engine"}, {"kind": "test_agent"})
        checkpoint = engine.checkpoint({f"agent:{inputs.persona.agent_id}": ref})
        export_run(engine.context.store, checkpoint.last_event_seq, root)
    finally:
        engine.close()
    if run_id == "human-run":
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 1
        for name, path in (("verified_experiences", "verified-experiences.jsonl"),
                           ("purchases", "purchases.jsonl"), ("app_usage", "app-usage.jsonl"),
                           ("location_stays", "location-stays.jsonl")):
            manifest["artifacts"].pop(name)
            (root / path).unlink()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cache = root.with_name(root.name + "-cache")
    shutil.copytree(root, cache)
    _CANONICAL_CACHE[cache_key] = cache


def _complete_engine(engine, activity_type):
    from worldmock.life.models import PlanDayRequest
    from worldmock.life.day_planning import agents_missing_plan
    count = 0
    while engine.context.state.current_time < engine.context.state.end_time:
        for agent_id in agents_missing_plan(engine.context.state, engine.context.config.timezone):
            engine.register_day_plan(agent_id, PlanDayRequest(goals=()), [], [])
        if engine.midnight_rhythm_pending:
            engine.decide_midnight_rhythm()
        scheduling = engine.scheduling_state()
        if scheduling.ready_agent_ids:
            _engine_decision(engine, count, activity_type)
            count += 1
        else:
            engine.advance(int((scheduling.next_event_at - scheduling.current_time).total_seconds()),
                           scheduling.next_event_at)


def _engine_decision(engine, count, activity_type):
    from agentsociety2.society import DecisionContext, DirectToolCall
    from worldmock.life.activity_control import pending_conclusions
    from tests.life.conftest import tool_of
    from worldmock.life.models import active_activities, ConcludeActivityRequest, ReportStatusRequest
    agent_id = engine.context.persona.agent_id
    state = engine.context.state
    at = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    context = DecisionContext(state.run_id, f"round-{count}", f"decision-{count}", agent_id, at, state.version)
    engine.begin_decision_round([context])
    engine.observe(context)
    pending = pending_conclusions(open_activities(state, agent_id))
    active = active_activities(state, agent_id)
    if pending:
        tool = "conclude_activity"
        request = ConcludeActivityRequest(items=[{"activity_name": item.name, "summary": "完成测试消费。",
            "memories": [], "views": []} for item in pending], errands=[])
    elif active:
        tool = "report_status"
        request = ReportStatusRequest(items=[{"activity_name": item.name, "summary": "继续测试消费。"}
            for item in active], errands=[])
    else:
        assert activity_type in {"sleep", "interact", "query"}, activity_type
        request = _idle_request(state, activity_type)
        tool = tool_of(request)
    engine.submit_action(DirectToolCall(context, f"{context.decision_id}:action:0001", tool, request))
    results = engine.commit_decision_round(context.round_id)
    assert all(row.error_code is None for row in results), results


#: 「query」导出里依次做的两次查询：柠檬茶有 app 卖，环境从中抽一个；纸巾没有任何 app 卖，手机上没有 app。
_QUERIED = ("柠檬茶", "纸巾")


def _idle_request(state, activity_type):
    """没有活动时开始的那一项：睡觉、打开抖音，或先依次查询柠檬茶与纸巾、之后睡觉。"""
    from tests.life.conftest import query, sleep, use_app
    done = sum(item.kind == "query" for item in state.activities.values())
    if activity_type == "query" and done < len(_QUERIED):
        return query(_QUERIED[done])
    return use_app("抖音") if activity_type == "interact" else sleep()


def _world_activity(run_id: str, sequence: int, start: datetime,
                    activity_type: str = "interact") -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        "media_position_seconds": 0,
        "route_progress_meters": 0,
        "next_interaction_at": None,
        "destination_id": None,
        "travel_method_id": None,
        "resource_claims": ["first"] if activity_type in {"travel", "sleep"} else ["second"],
        "boundary_type": "planned_end",
        "interruption_reason": None,
        "data": {"action": "interact", "app_id": "douyin",
                 "content_id": "c-long",
                 "start_position_seconds": 0, "pinned_by_push_id": None, "draw_index": 0, "played": []}
                if activity_type == "interact" else {"action": activity_type},
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
