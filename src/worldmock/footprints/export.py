"""@brief 从固定事件截止点发布不可变足迹、活动与素材来源。"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import Event, canonical_json
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.store import FootprintStore


def write_jsonl(path: Path, records: list[dict]) -> dict:
    """@brief 写入规范 JSONL，并返回实际字节和行数身份。"""
    raw = b"".join(canonical_json(record) + b"\n" for record in records)
    atomic_write(path, raw)
    return {"path": path.name, "rows": len(records), "sha256": hashlib.sha256(raw).hexdigest()}


def export_run(store: FootprintStore, cutoff: int, destination: Path) -> dict:
    """@brief 所有事件与素材验证成功后原子发布新目录，不读截止点之后的世界状态。"""
    events = store.read_events(cutoff)
    if not events:
        raise IntegrityError("Cannot export an empty event range")
    destination = Path(destination).resolve()
    if destination.exists():
        raise IntegrityError("Export destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    try:
        manifest = _write_export(store, events, stage)
        atomic_write(stage / "manifest.json", canonical_json(manifest))
        stage.rename(destination)
    except Exception:
        logging.exception("Footprint export failed before publication")
        shutil.rmtree(stage)
        raise
    return manifest


def _write_export(store: FootprintStore, events: list[Event], stage: Path) -> dict:
    """@brief 固定主文件、活动视图和全部外部素材，并绑定原始运行身份。"""
    activities = reconstruct_activities(events)
    manifest = _export_identity(store.manifest, events)
    manifest["artifacts"] = {
        "events": write_jsonl(stage / "events.jsonl", [event.model_dump() for event in events]),
        "activities": write_jsonl(stage / "activities.jsonl", activities),
    }
    references = {}
    if manifest["experiment_completed"]:
        checkpoint_event = next(event for event in reversed(events) if event.event_type == "checkpoint_created")
        checkpoint = store.get_checkpoint(checkpoint_event.data["checkpoint_id"])
        manifest["checkpoint"] = checkpoint.model_dump()
        for reference in checkpoint.snapshots.values():
            atomic_write(stage / "assets" / reference.sha256, store.assets.read(reference))
            references[reference.sha256] = reference.model_dump()
    for event in events:
        for reference in event.artifact_refs:
            content = store.assets.read(reference)
            atomic_write(stage / "assets" / reference.sha256, content)
            references[reference.sha256] = reference.model_dump()
    manifest["asset_index"] = write_jsonl(stage / "asset-index.jsonl", list(references.values()))
    manifest["frozen_run_manifest"] = store.manifest
    quality = canonical_json(build_quality_report(events, activities, store.manifest))
    atomic_write(stage / "quality.json", quality)
    manifest["quality_report"] = {"path": "quality.json", "size_bytes": len(quality),
                                  "sha256": hashlib.sha256(quality).hexdigest()}
    return manifest


def _export_identity(run: dict, events: list[Event]) -> dict:
    """@brief 根据实际截止时间声明覆盖范围，保留未完成实验的明确状态。"""
    zone = ZoneInfo(run["window"]["timezone"])
    start = datetime.fromtimestamp(run["window"]["start"], zone)
    end = datetime.fromtimestamp(events[-1].occurred_at, zone)
    if end <= start:
        raise IntegrityError("Export requires a positive confirmed time window")
    days = []
    cursor = start.date()
    while cursor < end.date() or (cursor == end.date() and end.time().isoformat() != "00:00:00"):
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    ended = [event for event in events if event.event_type == "run_ended"]
    saved = (len(ended) == 1 and ended[0].occurred_at == run["window"]["end"]
             and events[-1].event_type == "checkpoint_created" and events[-1].event_seq > ended[0].event_seq)
    failed = any(event.event_type == "run_failed" for event in events)
    complete = end.timestamp() == run["window"]["end"] and saved and not failed
    return {"schema_version": 1, "committed": True, "run_id": run["run_id"],
            "source_group_id": run.get("source_group_id", run["run_id"]),
            "source_run_ids": sorted({event.run_id for event in events}),
            "cutoff_event_seq": events[-1].event_seq, "experiment_completed": complete,
            "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": zone.key},
            "daily_coverage": days}


def reconstruct_activities(events: list[Event]) -> list[dict]:
    """@brief 只读取截止范围中的权威活动快照，进度区间避免把暂停算作活动。"""
    activities = {}
    segments = {}
    sources = {}
    for event in events:
        snapshot = event.data.get("activity")
        if snapshot is None:
            continue
        identity = snapshot["activity_id"]
        if event.activity_id != identity:
            raise IntegrityError("Event activity reference disagrees with its snapshot")
        previous = activities.get(identity)
        if previous and snapshot["elapsed_seconds"] < previous["elapsed_seconds"]:
            raise IntegrityError("Activity progress cannot move backwards")
        activities[identity] = dict(snapshot)
        sources.setdefault(identity, []).append(event.event_id)
        if event.event_type == "activity_progress":
            _append_segment(segments.setdefault(identity, []), event)
    return [_activity_record(value, segments.get(key, []), sources[key]) for key, value in activities.items()]


def _append_segment(segments: list[dict], event: Event) -> None:
    """@brief 合并连续已执行区间，拒绝时间重叠与未来进度。"""
    start, end = event.data["interval_start"], event.data["interval_end"]
    if end <= start or end > event.occurred_at or (segments and start < segments[-1]["ended_at"]):
        raise IntegrityError("Activity progress interval is invalid")
    if segments and start == segments[-1]["ended_at"]:
        segments[-1]["ended_at"] = end
    else:
        segments.append({"started_at": start, "ended_at": end})


def _activity_record(snapshot: dict, segments: list[dict], sources: list[str]) -> dict:
    """@brief 输出完整活动和实际执行区间，终态不由计划时长推测。"""
    result = dict(snapshot)
    result["activity_type"] = result.pop("kind")
    result["segments"] = segments
    result["source_event_ids"] = sources
    duration = sum(segment["ended_at"] - segment["started_at"] for segment in segments)
    if duration != result["elapsed_seconds"]:
        raise IntegrityError("Activity intervals do not account for its executed duration")
    return result
