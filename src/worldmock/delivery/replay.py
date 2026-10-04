"""@brief 把实际完成的反事实环境分支绑定为可追溯的 dreaming 训练窗口。"""

from __future__ import annotations

from worldmock.footprints.models import sha256_json
import hashlib
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.delivery.windows import (PRIVATE_EVENTS, _attach_decision_layers, _attach_evidence,
                                       _decision_groups, _prerequisite_state, _record, event_day)
from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json
from worldmock.footprints.validation import validate_export


def replay_windows(source_dir: Path, replay_dir: Path) -> list[dict]:
    """@brief 认证父子完整导出、共同前缀和一次实际干预，再逐自然日建立重演证据。"""
    source, parent_events, _ = validate_export(source_dir)
    branch, events, _ = validate_export(replay_dir)
    indexed = {event["event_id"]: event for event in events}
    checkpoint = _validate_parent(source, parent_events, branch, events)
    intervention = _validate_intervention(branch, events, checkpoint)
    history = [event for event in events if event["agent_id"] == intervention["agent_id"]
               and event["visibility"] == "agent"]
    records, days, decisions = [], defaultdict(list), _decision_groups(events)
    zone = ZoneInfo(branch["window"]["timezone"])
    for event in events:
        if (event["run_id"] == branch["run_id"] and event["event_seq"] > intervention["event_seq"]
                and event["agent_id"] == intervention["agent_id"] and event["visibility"] == "agent"
                and event["event_type"] not in PRIVATE_EVENTS):
            date = event_day(event, branch, zone)
            days[date].append(event)
    for date, rows in days.items():
        record = _replay_record(rows, intervention, (branch, replay_dir, checkpoint, indexed, decisions), date,
                                _prerequisite_state(rows, history))
        records.append(record)
    if not records:
        raise IntegrityError("Counterfactual replay has no actual visible result")
    return records


def _validate_parent(parent: dict, original: list[dict], branch: dict, events: list[dict]) -> dict:
    """@brief 父检查点之前必须是逐字相同的已认证事件，拒绝同名但无关的分支。"""
    frozen = branch["frozen_run_manifest"]
    if (frozen.get("branch_kind") != "counterfactual" or frozen.get("parent_run_id") != parent["run_id"]
            or branch["source_group_id"] != parent["source_group_id"] or branch["run_id"] == parent["run_id"]):
        raise IntegrityError("Replay does not belong to the supplied source run")
    checkpoints = [row for row in original if row["event_type"] == "checkpoint_created"
                   and row["data"].get("checkpoint_id") == frozen.get("parent_checkpoint_id")]
    if len(checkpoints) != 1:
        raise IntegrityError("Replay parent checkpoint is missing or duplicated")
    checkpoint = checkpoints[0]
    cutoff = checkpoint["event_seq"]
    if events[:cutoff] != original[:cutoff]:
        raise IntegrityError("Replay parent event prefix differs from the original export")
    return checkpoint


def _validate_intervention(branch: dict, events: list[dict], checkpoint: dict) -> dict:
    """@brief 重建严格干预摘要并验证全部条件来源位于父检查点可见范围。"""
    candidates = [row for row in events if row["run_id"] == branch["run_id"]
                  and row["data"].get("kind") == "counterfactual_intervention"]
    if len(candidates) != 1:
        raise IntegrityError("Replay requires exactly one applied intervention")
    event, data = candidates[0], candidates[0]["data"]
    request = {"kind": data["intervention_kind"], "agent_id": event["agent_id"],
               "balance_fen": data["after_balance_fen"], "hypothesis": data["hypothesis"],
               "source_event_ids": data["source_event_ids"]}
    payload = {"tool_name": "counterfactual_intervention", "request": request}
    digest = sha256_json(payload)
    if digest != data["intervention_sha256"] or digest != branch["frozen_run_manifest"]["intervention_sha256"]:
        raise IntegrityError("Replay intervention does not match the frozen branch")
    visible = {row["event_id"] for row in events[:checkpoint["event_seq"]]
               if row["visibility"] == "agent" and row["agent_id"] in {None, event["agent_id"]}}
    if not data["source_event_ids"] or not set(data["source_event_ids"]) <= visible:
        raise IntegrityError("Replay intervention sources are outside the parent knowledge boundary")
    if event["occurred_at"] != checkpoint["occurred_at"] or event["event_seq"] <= checkpoint["event_seq"]:
        raise IntegrityError("Replay intervention was not applied at its parent checkpoint")
    return event


def _replay_record(rows: list[dict], intervention: dict, source: tuple, date: str, prerequisite: dict) -> dict:
    """@brief 真实分支结果可标为分支内事实，原世界从未发生的结果仍不可冒充原事实。"""
    manifest, directory, checkpoint, indexed, decisions = source
    data = intervention["data"]
    result_ids = [row["event_id"] for row in rows]
    binding = {"run_id": manifest["run_id"], "parent_run_id": manifest["frozen_run_manifest"]["parent_run_id"],
               "parent_checkpoint_id": checkpoint["data"]["checkpoint_id"],
               "intervention_event_id": intervention["event_id"], "intervention_sha256": data["intervention_sha256"],
               "source_event_ids": data["source_event_ids"], "result_event_ids": result_ids,
               "final_checkpoint_id": manifest["checkpoint"]["checkpoint_id"],
               "export_manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()}
    record = _record("dreaming", rows, {"mode": "counterfactual", "environment_replay": binding},
                     manifest, prerequisite)
    record["input"].update(mode="counterfactual", date=date, person_id=intervention["agent_id"],
                            boundary_type="counterfactual_replay",
                            counterfactual={"hypothesis": data["hypothesis"], "world_kind": "resimulated",
                                            "branch_run_id": manifest["run_id"],
                                            "balance_fen": data["after_balance_fen"]})
    _attach_decision_layers(record, rows, decisions)
    _attach_evidence(record, rows, indexed, directory)
    record["provenance"]["source_event_ids"] = list(dict.fromkeys(
        record["provenance"]["source_event_ids"] + data["source_event_ids"] + [intervention["event_id"]]))
    return record
