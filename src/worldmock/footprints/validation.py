"""@brief 在生产消费者入口认证完整足迹导出及全部来源。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.ledger import validate_account_history
from worldmock.footprints.models import AssetRef, Checkpoint, Event, canonical_json
from worldmock.footprints.quality import build_quality_report


def validate_export(directory: Path) -> tuple[dict, list[dict], list[dict]]:
    """@brief 只有完整终态、检查点和所有素材均可信时才返回消费材料。"""
    root = directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    _validate_identity(manifest)
    events = _read_artifact(root, manifest["artifacts"]["events"], "events.jsonl")
    activities = _read_artifact(root, manifest["artifacts"]["activities"], "activities.jsonl")
    experiences = _read_artifact(
        root, manifest["artifacts"]["verified_experiences"], "verified-experiences.jsonl")
    indexed = _read_artifact(root, manifest["asset_index"], "asset-index.jsonl")
    records = [Event.model_validate(row, strict=True) for row in events]
    checkpoint = _validate_checkpoint(manifest, records)
    _validate_events(manifest, records)
    _validate_assets(root, records, checkpoint, indexed)
    validate_account_history(records, checkpoint.state)
    from worldmock.footprints.export import _verified_experience_rows, reconstruct_activities

    if reconstruct_activities(records) != activities:
        raise IntegrityError("Export activities disagree with their authoritative events")
    if _verified_experience_rows(manifest["frozen_run_manifest"]["agent_ids"], records) != experiences:
        raise IntegrityError("Export verified experiences disagree with committed events")
    _validate_quality(root, manifest, records, activities)
    return manifest, events, activities


def _validate_quality(root: Path, manifest: dict, events: list[Event], activities: list[dict]) -> None:
    """@brief 质量报告必须绑定真实字节，并能从同一冻结事件完整重算。"""
    descriptor = manifest.get("quality_report", {})
    path = root / "quality.json"
    if descriptor.get("path") != path.name or path.is_symlink() or not path.is_file():
        raise IntegrityError("Export quality report path is invalid")
    raw = path.read_bytes()
    if (len(raw) != descriptor.get("size_bytes")
            or hashlib.sha256(raw).hexdigest() != descriptor.get("sha256")):
        raise IntegrityError("Export quality report bytes do not match its manifest")
    expected = build_quality_report(events, activities, manifest["frozen_run_manifest"])
    if json.loads(raw) != expected:
        raise IntegrityError("Export quality report disagrees with authoritative events")


def _validate_identity(manifest: dict) -> None:
    """@brief 冻结运行、覆盖范围和提交状态必须与正式清单相符。"""
    if manifest.get("schema_version") != 1 or manifest.get("committed") is not True:
        raise IntegrityError("Export manifest is not a committed supported version")
    if manifest.get("experiment_completed") is not True:
        raise IntegrityError("Production delivery requires a completed experiment")
    frozen = manifest["frozen_run_manifest"]
    if manifest["run_id"] != frozen["run_id"] or manifest["source_group_id"] != frozen["source_group_id"]:
        raise IntegrityError("Export run identity disagrees with the frozen manifest")
    zone = ZoneInfo(frozen["window"]["timezone"])
    expected = {name: datetime.fromtimestamp(frozen["window"][name], zone).isoformat()
                for name in ("start", "end")}
    expected["timezone"] = zone.key
    if manifest["window"] != expected or frozen["window"]["end"] <= frozen["window"]["start"]:
        raise IntegrityError("Export window disagrees with the frozen experiment")
    start, end = (datetime.fromisoformat(expected[name]) for name in ("start", "end"))
    days, cursor = [], start
    while cursor < end:
        days.append(cursor.date().isoformat())
        cursor = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time(), zone)
    if manifest["daily_coverage"] != days:
        raise IntegrityError("Export day coverage is inconsistent")
    if set(manifest["artifacts"]) != {"events", "activities", "verified_experiences"}:
        raise IntegrityError("Export artifact set is invalid")


def _read_artifact(root: Path, descriptor: dict, name: str) -> list[dict]:
    """@brief 固定相对文件、真实字节摘要与行数共同确定导出身份。"""
    path = root / name
    if descriptor["path"] != name or path.is_symlink() or not path.is_file():
        raise IntegrityError("Export artifact path is invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise IntegrityError("Export artifact hash is invalid")
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != descriptor["rows"]:
        raise IntegrityError("Export artifact row count is invalid")
    return rows


def _validate_checkpoint(manifest: dict, events: list[Event]) -> Checkpoint:
    """@brief 完整共同检查点必须是导出的最终事件并绑定同一世界。"""
    checkpoint = Checkpoint.model_validate(manifest["checkpoint"])
    payload = checkpoint.model_dump(exclude={"record_sha256"})
    frozen = manifest["frozen_run_manifest"]
    if hashlib.sha256(canonical_json(payload)).hexdigest() != checkpoint.record_sha256:
        raise IntegrityError("Export checkpoint record hash is invalid")
    if checkpoint.manifest_sha256 != hashlib.sha256(canonical_json(frozen)).hexdigest():
        raise IntegrityError("Export checkpoint references a different manifest")
    if (checkpoint.run_id != manifest["run_id"] or checkpoint.current_time != frozen["window"]["end"]
            or checkpoint.current_time != checkpoint.state["current_time"]
            or checkpoint.state_version != checkpoint.state["version"]):
        raise IntegrityError("Export checkpoint world identity is inconsistent")
    expected = {f"agent:{identity}" for identity in frozen["agent_ids"]} | {"life"}
    if set(checkpoint.snapshots) != expected:
        raise IntegrityError("Export checkpoint participants are incomplete")
    if not events or checkpoint.last_event_seq != manifest["cutoff_event_seq"]:
        raise IntegrityError("Export checkpoint does not match its event cutoff")
    last = events[-1]
    if (last.event_type != "checkpoint_created" or last.data.get("checkpoint_id") != checkpoint.checkpoint_id
            or last.event_seq != checkpoint.last_event_seq or last.occurred_at != checkpoint.current_time
            or last.run_id != checkpoint.run_id or last.state_version_before != checkpoint.state_version
            or last.state_version_after != checkpoint.state_version):
        raise IntegrityError("Export final checkpoint event is inconsistent")
    return checkpoint


def _validate_events(manifest: dict, events: list[Event]) -> None:
    """@brief 核对原始全序、来源运行、因果回链和唯一实验结束。"""
    window = manifest["frozen_run_manifest"]["window"]
    seen, sources, ended = set(), set(), []
    last_time, current_started = window["start"], False
    for sequence, event in enumerate(events, start=1):
        if event.event_seq != sequence or event.event_id in seen:
            raise IntegrityError("Export event order or identity is invalid")
        if not last_time <= event.occurred_at <= window["end"] or not set(event.caused_by) <= seen:
            raise IntegrityError("Export event time or causal references are invalid")
        if event.run_id == manifest["run_id"]:
            current_started = True
        elif current_started:
            raise IntegrityError("Export returns to an ancestor run after the branch begins")
        if event.event_type == "run_failed":
            raise IntegrityError("Export includes a failed run")
        if event.event_type == "run_ended":
            ended.append(event)
        last_time = event.occurred_at
        seen.add(event.event_id)
        sources.add(event.run_id)
    if len(ended) != 1 or ended[0].occurred_at != window["end"]:
        raise IntegrityError("Export does not have one confirmed experiment end")
    if sorted(sources) != manifest["source_run_ids"] or len(events) != manifest["cutoff_event_seq"]:
        raise IntegrityError("Export source runs or cutoff disagree with its events")


def _validate_assets(root: Path, events: list[Event], checkpoint: Checkpoint, indexed: list[dict]) -> None:
    """@brief 每个事件及人物快照引用必须存在于索引并通过实际内容验证。"""
    references = [AssetRef.model_validate(value) for value in indexed]
    by_hash = {value.sha256: value for value in references}
    if len(by_hash) != len(references):
        raise IntegrityError("Export asset index repeats an identity")
    required = list(checkpoint.snapshots.values())
    required.extend(reference for event in events for reference in event.artifact_refs)
    for reference in required:
        indexed_ref = by_hash.get(reference.sha256)
        if indexed_ref is None or indexed_ref.size_bytes != reference.size_bytes:
            raise IntegrityError("Export source asset is missing from its index")
    for reference in references:
        path = root / "assets" / reference.sha256
        if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
            raise IntegrityError("Export asset file is missing or symbolic")
        raw = path.read_bytes()
        if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise IntegrityError("Export source asset content is invalid")
