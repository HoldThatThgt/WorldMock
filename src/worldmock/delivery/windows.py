"""@brief 从不可变事件导出构造动作、意图和 dreaming 的证据窗口。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json
from worldmock.footprints.validation import validate_export

PRIVATE_EVENTS = {"model_request", "model_response", "memory_updated", "decision_started"}


def prepare_windows(export_dir: Path) -> dict[str, list[dict]]:
    """@brief 逐次行为保留重复记录，并给不同训练目标设定真实知识截止。"""
    manifest, events, _ = validate_export(export_dir)
    by_action = defaultdict(list)
    by_decision = defaultdict(list)
    for event in events:
        if event["action_id"]:
            by_action[event["action_id"]].append(event)
        if event["decision_id"]:
            by_decision[event["decision_id"]].append(event)
    records = {"action": [], "intent": [], "dreaming": []}
    for event in events:
        if event["event_type"] == "action_requested":
            action, intent = _action_windows(event, by_action, by_decision, (manifest, export_dir))
            records["action"].append(action)
            records["intent"].append(intent)
    records["dreaming"] = _dream_windows(events, manifest, export_dir)
    if any(not rows for rows in records.values()):
        raise IntegrityError("Export has no complete evidence windows for a required training task")
    return records


def _action_windows(event: dict, actions: dict, decisions: dict, source: tuple) -> tuple[dict, dict]:
    """@brief 动作答案与动作前输入分开；意图可以读取已经发生的实际结果。"""
    manifest, directory = source
    previous = [row for row in decisions[event["decision_id"]]
                if row["event_seq"] < event["event_seq"] and row["visibility"] == "agent"
                and row["event_type"] not in PRIVATE_EVENTS | {"action_requested"}]
    if not previous:
        raise IntegrityError("Action training requires a preceding visible observation")
    outcomes = [row for row in actions[event["action_id"]] if row["event_type"] != "action_requested"]
    facts = {"request": event, "outcomes": outcomes}
    prerequisite = _prerequisite_state(previous, previous)
    action = _record("action", previous, facts, manifest, prerequisite)
    target_ids = [event["event_id"]] + [row["event_id"] for row in outcomes]
    action["provenance"]["source_event_ids"] = list(dict.fromkeys(
        action["provenance"]["source_event_ids"] + target_ids))
    action["provenance"]["target_event_ids"] = target_ids
    action["input"]["events"] = [_visible_event(row, directory) for row in previous]
    observed = sorted(previous + [event] + [row for row in outcomes if row["visibility"] == "agent"],
                      key=lambda row: row["event_seq"])
    intent = _record("intent", observed, facts, manifest, prerequisite)
    intent["provenance"]["source_event_ids"] = list(dict.fromkeys(
        intent["provenance"]["source_event_ids"] + target_ids))
    intent["input"]["events"] = [_visible_event(row, directory) for row in observed]
    return action, intent


def _record(task: str, events: list[dict], facts: dict, manifest: dict, prerequisite: dict) -> dict:
    """@brief 构造可直接交给 LabelKit 的四字段记录与独立事实目标。"""
    identities = [event["event_id"] for event in events]
    sources = list(dict.fromkeys(identities + ([prerequisite["event_id"]] if prerequisite["event_id"] else [])))
    cutoff = events[-1]
    return {"task": task,
            "input": {"events": [], "window_start": events[0]["occurred_at"],
                      "window_end": cutoff["occurred_at"], "knowledge_cutoff_event_seq": cutoff["event_seq"],
                      "activity_ids": sorted({event["activity_id"] for event in events if event["activity_id"]}),
                      "prerequisite_state": prerequisite, "activity_boundaries": _activity_boundaries(events),
                      "boundary_type": "decision" if task != "dreaming" else "natural_day"},
            "target": facts,
            "provenance": {"run_id": manifest["run_id"], "source_group_id": manifest["source_group_id"],
                           "source_event_ids": sources, "cutoff_event_seq": manifest["cutoff_event_seq"],
                           "model_identity": manifest["frozen_run_manifest"]["model_identity"],
                           "window_id": hashlib.sha256(canonical_json([task, identities, facts])).hexdigest()}}


def _visible_event(event: dict, directory: Path) -> dict:
    """@brief 展开人物当时真正可见的文本素材，保留二进制素材的模态引用。"""
    result = {key: event[key] for key in ("event_id", "event_type", "occurred_at", "agent_id", "data")}
    result["materials"] = []
    for reference in event["artifact_refs"]:
        raw = (directory / "assets" / reference["sha256"]).read_bytes()
        if len(raw) != reference["size_bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise IntegrityError("Visible material does not match its event reference")
        material = {"reference": reference}
        if reference["media_type"].startswith("text/") or reference["media_type"] == "application/json":
            material["content"] = raw.decode("utf-8")
        result["materials"].append(material)
    return result


def _dream_windows(events: list[dict], manifest: dict, directory: Path) -> list[dict]:
    """@brief 每个人物自然日都覆盖归纳、反思和显式假设，不接触未来事实。"""
    zone = ZoneInfo(manifest["window"]["timezone"])
    days = defaultdict(list)
    for event in events:
        if (event["visibility"] == "agent" and event["agent_id"] is not None
                and event["event_type"] not in PRIVATE_EVENTS):
            date = event_day(event, manifest, zone)
            days[(event["agent_id"], date)].append(event)
    records = []
    for key, rows in days.items():
        history = [event for event in events if event["agent_id"] == key[0] and event["visibility"] == "agent"]
        prerequisite = _prerequisite_state(rows, history)
        for mode in ("induction", "reflection", "counterfactual"):
            result = _record("dreaming", rows, {"mode": mode, "environment_replay": None}, manifest, prerequisite)
            result["input"].update(mode=mode, person_id=key[0], date=key[1],
                                   events=[_visible_event(row, directory) for row in rows])
            if mode == "counterfactual":
                result["input"]["counterfactual"] = {"world_kind": "observed", "environment_replay": None}
            records.append(result)
    return records


def event_day(event: dict, manifest: dict, zone: ZoneInfo) -> str:
    """@brief 排他实验终点的结算事件归入最后一天，不产生额外空白自然日。"""
    end = int(datetime.fromisoformat(manifest["window"]["end"]).timestamp())
    occurred_at = event["occurred_at"]
    if occurred_at == end:
        occurred_at -= 1
    return datetime.fromtimestamp(occurred_at, zone).date().isoformat()


def _prerequisite_state(events: list[dict], history: list[dict]) -> dict:
    """@brief 只使用窗口开始前已经观察过的状态，缺失时明确未观察。"""
    known = [event for event in history if event["event_type"] == "observation"
             and event["event_seq"] <= events[0]["event_seq"]]
    if not known:
        return {"availability": "unobserved", "event_id": None, "observed_at": None, "state": None}
    latest = max(known, key=lambda event: event["event_seq"])
    return {"availability": "observed", "event_id": latest["event_id"],
            "observed_at": latest["occurred_at"], "state": latest["data"]}


def _activity_boundaries(events: list[dict]) -> list[dict]:
    """@brief 保留窗口内真正观察到的完成和截断原因，不从未来目标推测边界。"""
    records = {}
    for event in events:
        activity = event["data"].get("activity")
        if activity is None or activity["status"] not in {"completed", "cancelled", "failed", "truncated"}:
            continue
        records[activity["activity_id"]] = {"activity_id": activity["activity_id"], "status": activity["status"],
                                           "boundary_type": activity["boundary_type"],
                                           "ended_at": activity["ended_at"], "source_event_id": event["event_id"]}
    return list(records.values())
