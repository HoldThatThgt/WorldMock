"""@brief 从固定事件截止点发布不可变足迹、活动与素材来源。"""

from __future__ import annotations
from worldmock.recording_engine.projections.presentation import timeline_labels

from worldmock.recording_engine.projections.presentation.timeline_labels import timeline_sources, window_days
import hashlib
import importlib.util
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.atomic_write import atomic_write
from worldmock.recording_engine.projections.activities.app_usage import (
    activity_snapshots,
    append_activity_segment,
    project_app_usage,
    validate_activity_prefix,
)
from worldmock.recording_engine.projections.presentation.display_wording import DisplayNames
from worldmock.recording_engine.projections.activities.experiences import project_verified_experiences
from worldmock.recording_engine.validation.frozen_environment import frozen_display_names
from worldmock.recording_engine.projections.activities.location_stays import project_location_stays
from worldmock.recording_engine.events.models import AssetRef, Event
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.export.post_run import post_run_view
from worldmock.recording_engine.projections.commerce.purchases import project_purchase_facts
from worldmock.recording_engine.reports.quality import build_quality_report
from worldmock.recording_engine.storage.store import FootprintStore

#: 导出清单的结构号：工件是六份，含地点停留；活动种类含查询；进行中的 interact 活动行带 played_count；
#: 模型响应与失败事件的 request 为 null（请求只在请求事件存一次）；顶层带 lineage；结构号不符的导出直接拒绝，不迁移。
EXPORT_SCHEMA_VERSION = 8
#: 导出清单 artifacts 的键集。
EXPORT_ARTIFACTS = frozenset({"events", "activities", "verified_experiences", "purchases", "app_usage",
                              "location_stays"})


def write_jsonl(path: Path, records: list[dict]) -> dict:
    """@brief 写入规范 JSONL，并返回实际字节和行数身份。"""
    raw = b"".join(canonical_json(record) + b"\n" for record in records)
    atomic_write(path, raw)
    return {"path": path.name, "rows": len(records), "sha256": hashlib.sha256(raw).hexdigest()}


def export_run(store: FootprintStore, cutoff: int, destination: Path) -> dict:
    """@brief 一遍分页读取截止前缀：逐条校验、照旧编码写入 events.jsonl 并哈希，内存里只留事后读取视图；全部工件与素材验证成功后原子发布新目录，不读截止点之后的事件。"""
    events = store.iter_events(cutoff)
    if cutoff == 0:
        logging.error("Cannot export an empty event range")
        raise IntegrityError("Cannot export an empty event range")
    destination = Path(destination).resolve()
    if destination.exists():
        raise IntegrityError("Export destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    try:
        kept, events_artifact = _write_events(events, stage / "events.jsonl")
        manifest = _write_export(store, kept, events_artifact, stage)
        atomic_write(stage / "manifest.json", canonical_json(manifest))
        stage.rename(destination)
    except Exception:
        logging.exception("Footprint export failed before publication")
        shutil.rmtree(stage)
        raise
    return manifest


def _write_events(events: Iterator[Event], path: Path) -> tuple[list[Event], dict]:
    """@brief 逐条写入规范 JSONL（与 write_jsonl 同一编码）并边写边哈希；返回事后读取视图与工件身份。"""
    digest, rows, kept = hashlib.sha256(), 0, []
    with path.open("xb") as stream:
        for event in events:
            line = canonical_json(event.model_dump()) + b"\n"
            stream.write(line)
            digest.update(line)
            rows += 1
            kept.append(post_run_view(event))
        stream.flush()
        os.fsync(stream.fileno())
    return kept, {"path": path.name, "rows": rows, "sha256": digest.hexdigest()}


def _write_export(store: FootprintStore, events: list[Event], events_artifact: dict, stage: Path) -> dict:
    """@brief 固定六份主文件（事件、活动、已核验经历、购买、app 使用、地点停留）和全部外部素材，并绑定原始运行身份；
    events 是截止前缀的事后读取视图。"""
    # 显示名称在复制素材之前从运行目录冻结的环境目录取一次，质量报告与时间线投影共用这一张表。
    names = frozen_display_names(store.run_dir, store.manifest)
    activities = reconstruct_activities(events)
    manifest = _export_identity(store.manifest, events)
    rows = [event.model_dump() for event in events]
    manifest["artifacts"] = {
        "events": events_artifact,
        "activities": write_jsonl(stage / "activities.jsonl", activities),
        "verified_experiences": write_jsonl(
            stage / "verified-experiences.jsonl", _verified_experience_rows(store.manifest["agent_ids"], events)),
        "purchases": write_jsonl(stage / "purchases.jsonl", project_purchase_facts(rows)),
        "app_usage": write_jsonl(stage / "app-usage.jsonl", project_app_usage(rows)),
        "location_stays": write_jsonl(stage / "location-stays.jsonl", project_location_stays(rows)),
    }
    references = {}
    for value in store.manifest["environment_assets"].values():
        reference = AssetRef.model_validate(value)
        atomic_write(stage / "assets" / reference.sha256, store.assets.read(reference))
        references[reference.sha256] = reference.model_dump()
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
    quality = canonical_json(build_quality_report(events, activities, store.manifest, names))
    atomic_write(stage / "quality.json", quality)
    manifest["quality_report"] = {"path": "quality.json", "size_bytes": len(quality),
                                  "sha256": hashlib.sha256(quality).hexdigest()}
    manifest["timeline"] = _write_timeline_projections(store, events, stage, manifest["daily_coverage"], names)
    return manifest


def _verified_experience_rows(agent_ids: list[int], events: list[Event]) -> list[dict]:
    """@brief 离线从完整已提交截止前缀导出每个人的全部规范经历。"""
    cutoff = events[-1].event_seq
    rows = [fact for agent_id in sorted(int(item) for item in agent_ids)
            for fact in project_verified_experiences(events, agent_id=agent_id, cutoff_seq=cutoff)]
    return sorted(rows, key=lambda item: (item["event_seq"], item["agent_id"], item["kind"],
                                          item.get("goods_id", "")))


def _write_timeline_projections(store: FootprintStore, events: list[Event], stage: Path, days: list[str],
                                names: DisplayNames) -> list[dict]:
    """@brief 由截止前缀的同一份事件一次取出进度与订单，逐人逐日投影 timeline/<agent>-<date>.md 并哈希绑定。

    投影调用 scripts/render_day.py 的 day_rows 与 day_markdown（单一实现，不重复）；截止之后的事件不进时间线。
    名称取运行目录里冻结的环境目录；每个人物每个覆盖日一个文件，列入导出清单（sha256 + 字节数）。
    """
    module = timeline_labels._load_render_day()
    progress, orders = timeline_sources(events)
    zone = store.manifest["window"]["timezone"]
    records = []
    for agent_id in sorted(int(item) for item in store.manifest["agent_ids"]):
        bodies = [body for body in progress if body.get("agent_id") == agent_id]
        for day in days:
            moment = date.fromisoformat(day)
            markdown = module.day_markdown(agent_id, moment, module.day_rows(bodies, orders, names, moment, zone))
            payload = markdown.encode("utf-8")
            relative = f"timeline/{agent_id}-{day}.md"
            atomic_write(stage / relative, payload)
            records.append({"agent_id": agent_id, "date": day, "path": relative,
                            "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return records




def _export_identity(run: dict, events: list[Event]) -> dict:
    """@brief 根据实际截止时间声明覆盖范围，保留未完成实验的明确状态，并带运行清单的 lineage。"""
    zone = ZoneInfo(run["window"]["timezone"])
    start = datetime.fromtimestamp(run["window"]["start"], zone)
    end = datetime.fromtimestamp(events[-1].occurred_at, zone)
    if end <= start:
        raise IntegrityError("Export requires a positive confirmed time window")
    days = [day.isoformat() for day in window_days({**run["window"], "end": events[-1].occurred_at})]
    ended = [event for event in events if event.event_type == "run_ended"]
    saved = (len(ended) == 1 and ended[0].occurred_at == run["window"]["end"]
             and events[-1].event_type == "checkpoint_created" and events[-1].event_seq > ended[0].event_seq)
    failed = any(event.event_type == "run_failed" for event in events)
    complete = end.timestamp() == run["window"]["end"] and saved and not failed
    return {"schema_version": EXPORT_SCHEMA_VERSION, "committed": True, "run_id": run["run_id"],
            "source_group_id": run["source_group_id"],
            "source_run_ids": sorted({event.run_id for event in events}),
            "cutoff_event_seq": events[-1].event_seq, "experiment_completed": complete,
            "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": zone.key},
            "daily_coverage": days, "lineage": run["lineage"]}


def reconstruct_activities(events: list[Event]) -> list[dict]:
    """@brief 只读取截止范围中的权威活动快照，进度区间避免把暂停算作活动。"""
    validate_activity_prefix([event.model_dump(mode="json") for event in events])
    activities = {}
    segments = {}
    sources = {}
    for event in events:
        for snapshot in activity_snapshots(event.data):
            identity = snapshot["activity_id"]
            if snapshot is event.data.get("activity") and event.activity_id != identity:
                raise IntegrityError("Event activity reference disagrees with its snapshot")
            previous = activities.get(identity)
            if previous and snapshot["elapsed_seconds"] < previous["elapsed_seconds"]:
                raise IntegrityError("Activity progress cannot move backwards")
            activities[identity] = dict(snapshot)
            activity_sources = sources.setdefault(identity, [])
            if event.event_id not in activity_sources:
                activity_sources.append(event.event_id)
            if event.event_type == "activity_progress":
                _append_segment(segments.setdefault(identity, []), event)
    return [_activity_record(value, segments.get(key, []), sources[key]) for key, value in activities.items()]


def _append_segment(segments: list[dict], event: Event) -> None:
    """@brief 与 app 导出使用同一真实区间合并算法。"""
    append_activity_segment(segments, event.model_dump(mode="json"))


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
