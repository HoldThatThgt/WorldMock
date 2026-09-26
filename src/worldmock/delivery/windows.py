"""@brief 从不可变事件导出构造动作、意图和 dreaming 的证据窗口。"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json
from worldmock.footprints.validation import validate_export
from worldmock.life.narration import render_scene

#: 动作/意图窗口永不进入的私有事件：决策自述仅 dreaming 任务可读（docs/dev/SPEC-life-realism.md「两阶段决策」「呈现与导出」）。
PRIVATE_EVENTS = {"model_request", "model_response", "memory_updated", "decision_started"}
#: dreaming 专属可读的私有载体：当日 decision_started.data.decision_statement 与观察 scene。
DREAMING_PRIVATE_EVENTS = PRIVATE_EVENTS - {"decision_started"}


def prepare_windows(export_dir: Path) -> dict[str, list[dict]]:
    """@brief 逐次行为保留重复记录，并给不同训练目标设定真实知识截止。"""
    manifest, events, _ = validate_export(export_dir)
    indexed = {event["event_id"]: event for event in events}
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
            source = (manifest, export_dir, indexed)
            action, intent = _action_windows(event, by_action, by_decision, source)
            records["action"].append(action)
            records["intent"].append(intent)
    records["dreaming"] = _dream_windows(events, manifest, export_dir, indexed)
    if any(not rows for rows in records.values()):
        raise IntegrityError("Export has no complete evidence windows for a required training task")
    return records


def _action_windows(event: dict, actions: dict, decisions: dict, source: tuple) -> tuple[dict, dict]:
    """@brief 动作答案与动作前输入分开；意图可以读取已经发生的实际结果。"""
    manifest, directory, indexed = source
    decision_rows, action_rows = _validated_action_groups(event, actions, decisions)
    prompt_source = _decision_prompt_source(event, decision_rows)
    previous = [row for row in decision_rows
                if row["event_seq"] < event["event_seq"] and row["visibility"] == "agent"
                and row["event_type"] not in PRIVATE_EVENTS | {"action_requested"}]
    if not previous:
        raise IntegrityError("Action training requires a preceding visible observation")
    outcomes = [row for row in action_rows if row["event_type"] not in PRIVATE_EVENTS | {"action_requested"}]
    facts = {"request": event, "outcomes": outcomes}
    prerequisite = _prerequisite_state(previous, previous)
    action = _record("action", previous, facts, manifest, prerequisite)
    _attach_decision_prompt(action, prompt_source)
    _attach_evidence(action, previous, indexed, directory)
    observed = sorted(previous + [event] + [row for row in outcomes if row["visibility"] == "agent"],
                      key=lambda row: row["event_seq"])
    intent = _record("intent", observed, facts, manifest, prerequisite)
    _attach_decision_prompt(intent, prompt_source)
    _attach_evidence(intent, observed, indexed, directory)
    return action, intent


def _decision_prompt_source(action: dict, rows: list[dict]) -> dict:
    """@brief 选择动作前实际发送的最后一份人物行动请求，并核对目标工具。"""
    candidates = [row for row in rows if row["event_type"] == "model_request"
                  and row["event_seq"] < action["event_seq"] and row["agent_id"] == action["agent_id"]
                  and row["data"].get("phase") == "request" and not row["data"].get("purpose")]
    if not candidates:
        _invalid_action_group("Action training has no recorded decision prompt")
    row = max(candidates, key=lambda item: item["event_seq"])
    request = row["data"].get("request")
    if not isinstance(request, dict):
        _invalid_action_group("Action training has an invalid recorded decision prompt")
    messages, tools = request.get("messages"), request.get("tools")
    if (not isinstance(messages, list) or not messages or not isinstance(tools, list)
            or any(not isinstance(message, dict) for message in messages)):
        _invalid_action_group("Action training has an incomplete recorded decision prompt")
    names = {item.get("function", {}).get("name") for item in tools if isinstance(item, dict)
             and isinstance(item.get("function"), dict)}
    if action["data"].get("tool_name") not in names:
        _invalid_action_group("Action training prompt does not expose the requested tool")
    return row


def _attach_decision_prompt(record: dict, source: dict) -> None:
    """@brief 将实际请求及原事件摘要绑定到窗口，原始目标仍留在独立 target。"""
    request = source["data"]["request"]
    record["input"]["decision_prompt"] = request
    record["provenance"]["decision_prompt_event_id"] = source["event_id"]
    record["provenance"]["decision_prompt_sha256"] = hashlib.sha256(canonical_json(request)).hexdigest()


def _validated_action_groups(event: dict, actions: dict, decisions: dict) -> tuple[list[dict], list[dict]]:
    """@brief 拒绝跨人物复用动作或决策身份形成的证据分组。"""
    agent_id = event["agent_id"]
    if agent_id is None:
        _invalid_action_group("Action request must identify its agent")
    decision_rows = decisions[event["decision_id"]]
    action_rows = actions[event["action_id"]]
    _validate_action_group_agent(agent_id, decision_rows, "decision")
    _validate_action_group_agent(agent_id, action_rows, "action")
    return decision_rows, action_rows


def _validate_action_group_agent(agent_id: int, rows: list[dict], group: str) -> None:
    """@brief 允许无人物系统事实，但人物可见事实只属于请求人物。"""
    foreign = any(row["agent_id"] not in {agent_id, None} for row in rows)
    unowned_visible = any(row["visibility"] == "agent" and row["agent_id"] != agent_id for row in rows)
    if foreign or unowned_visible:
        _invalid_action_group(f"Action training {group} identity crosses agent boundaries")


def _invalid_action_group(message: str) -> None:
    """@brief 记录并拒绝跨人物动作训练来源。"""
    logging.error("%s", message)
    raise IntegrityError(message)


def _record(task: str, events: list[dict], facts: dict, manifest: dict, prerequisite: dict) -> dict:
    """@brief 构造可直接交给 LabelKit 的四字段记录与独立事实目标。"""
    identities = [event["event_id"] for event in events]
    sources = list(dict.fromkeys(identities + ([prerequisite["event_id"]] if prerequisite["event_id"] else [])))
    cutoff = events[-1]
    input_data = {"events": [], "window_start": events[0]["occurred_at"],
                  "window_end": cutoff["occurred_at"], "knowledge_cutoff_event_seq": cutoff["event_seq"],
                  "activity_ids": sorted({event["activity_id"] for event in events if event["activity_id"]}),
                  "prerequisite_state": prerequisite, "activity_boundaries": _activity_boundaries(events),
                  "allowed_evidence_event_ids": sources,
                  "boundary_type": "decision" if task != "dreaming" else "natural_day"}
    if task == "dreaming":
        input_data["mode"] = facts["mode"]
    provenance = {"run_id": manifest["run_id"], "source_group_id": manifest["source_group_id"],
                  "source_event_ids": sources, "cutoff_event_seq": cutoff["event_seq"],
                  "model_identity": manifest["frozen_run_manifest"]["model_identity"],
                  "window_id": _window_identity(task, input_data)}
    return {"task": task, "input": input_data, "target": facts, "provenance": provenance}


def _visible_event(event: dict, directory: Path) -> dict:
    """@brief 展开人物当时真正可见的文本素材，保留二进制素材的模态引用。"""
    result = {key: event[key] for key in ("event_id", "event_type", "occurred_at", "agent_id")}
    result["data"] = _public_event_data(event)
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


def _public_event_data(event: dict) -> dict:
    """@brief 证据行仅提供人物可见语义，私有状态保留在认证导出。

    conclude_activity.memories 属于记忆写入：意图与 dreaming 窗口输入中的动作请求裁去 request.memories，
    只保留 task 与 summary；动作窗口的目标仍保留完整请求（docs/dev/SPEC-time-and-energy.md 第九章）。
    """
    data = event["data"]
    kind = event["event_type"]
    if kind == "observation":
        scene = data.get("scene")
        if isinstance(scene, dict):
            scene = render_scene(scene)
        return {"scene": scene} if isinstance(scene, str) else {}
    if kind == "decision_started":
        statement = data.get("decision_statement")
        return {"decision_statement": statement} if isinstance(statement, str) else {}
    if kind == "action_requested":
        public = {key: data[key] for key in ("tool_name", "request") if key in data}
        if data.get("tool_name") == "conclude_activity" and isinstance(public.get("request"), dict):
            public["request"] = {key: value for key, value in public["request"].items() if key != "memories"}
        return public
    if kind in {"action_result", "action_accepted", "action_rejected", "activity_progress"}:
        return {key: data[key] for key in ("status", "error_code", "message") if key in data}
    return {}


def _attach_evidence(record: dict, events: list[dict], indexed: dict, directory: Path) -> None:
    """@brief 将观察已暴露的真实结果并入输入、引用闭集和来源。"""
    cutoff = record["input"]["knowledge_cutoff_event_seq"]
    expanded = _expand_observed_results(events, indexed, cutoff)
    identities = [event["event_id"] for event in expanded]
    prerequisite = record["input"]["prerequisite_state"]
    if prerequisite["event_id"]:
        identities.append(prerequisite["event_id"])
    allowed = list(dict.fromkeys(identities))
    record["input"]["events"] = [_visible_event(event, directory) for event in expanded]
    record["input"]["allowed_evidence_event_ids"] = allowed
    record["input"]["activity_ids"] = sorted(
        {event["activity_id"] for event in expanded if event["activity_id"]}
    )
    record["input"]["activity_boundaries"] = _activity_boundaries(expanded)
    prompt_event_id = record["provenance"].get("decision_prompt_event_id")
    sources = allowed + ([prompt_event_id] if prompt_event_id else [])
    record["provenance"]["source_event_ids"] = sources
    record["provenance"]["window_id"] = _window_identity(record["task"], record["input"])


def _window_identity(task: str, input_data: dict) -> str:
    """@brief 仅由模型可见输入生成公开窗口身份，避免承诺未来目标。"""
    return hashlib.sha256(canonical_json([task, input_data])).hexdigest()


def _expand_observed_results(events: list[dict], indexed: dict, cutoff: int) -> list[dict]:
    """@brief 只解析当前可见观察明示暴露的先前动作结果。"""
    expanded = {event["event_id"]: event for event in events}
    for observation in events:
        if observation["event_type"] != "observation":
            continue
        references = observation["data"].get("recent_action_results", [])
        if not isinstance(references, list):
            _invalid_observed_result("Observed action result references must be a list")
        for reference in references:
            source = _validated_observed_result(observation, reference, indexed, cutoff)
            expanded[source["event_id"]] = source
    return sorted(expanded.values(), key=lambda event: event["event_seq"])


def _validated_observed_result(observation: dict, reference: object, indexed: dict, cutoff: int) -> dict:
    """@brief 核对观察摘要与截止前同人物可见的权威结果。"""
    if observation["visibility"] != "agent" or observation["agent_id"] is None:
        _invalid_observed_result("Observed action result source must be an agent-visible observation")
    if not isinstance(reference, dict) or not isinstance(reference.get("event_id"), str):
        _invalid_observed_result("Observed action result reference has no event identity")
    source = indexed.get(reference["event_id"])
    if (source is None or source["event_type"] != "action_result" or source["visibility"] != "agent"
            or source["agent_id"] != observation["agent_id"]):
        _invalid_observed_result("Observed action result does not resolve to a visible same-agent result")
    if source["event_seq"] >= observation["event_seq"] or source["event_seq"] > cutoff:
        _invalid_observed_result("Observed action result is outside the current knowledge cutoff")
    if reference != _result_summary(source):
        _invalid_observed_result("Observed action result summary disagrees with its canonical event")
    return source


def _result_summary(event: dict) -> dict:
    """@brief 从权威动作结果重建人物观察中的精确摘要。"""
    return {"event_id": event["event_id"], "action_id": event["action_id"],
            "decision_id": event["decision_id"], "occurred_at": event["occurred_at"],
            "status": event["data"].get("status"), "error_code": event["data"].get("error_code"),
            "message": event["data"].get("message"), "result": event["data"]}


def _invalid_observed_result(message: str) -> None:
    """@brief 记录且拒绝无法证明属于当前知识窗口的结果。"""
    logging.error("%s", message)
    raise IntegrityError(message)


def _dream_windows(events: list[dict], manifest: dict, directory: Path, indexed: dict) -> list[dict]:
    """@brief 每个人物自然日都覆盖归纳、反思和显式假设，不接触未来事实。

    决策自述载体例外进入：dreaming 窗口可含当日 decision_started.data.
    decision_statement 与观察 scene（docs/dev/SPEC-life-realism.md「两阶段决策」「呈现与导出」）；model_request/
    model_response/memory_updated 仍排除，动作与意图窗口输入不变。
    """
    zone = ZoneInfo(manifest["window"]["timezone"])
    days = defaultdict(list)
    for event in events:
        if (event["visibility"] == "agent" and event["agent_id"] is not None
                and event["event_type"] not in DREAMING_PRIVATE_EVENTS):
            date = event_day(event, manifest, zone)
            days[(event["agent_id"], date)].append(event)
    records = []
    for key, rows in days.items():
        history = [event for event in events if event["agent_id"] == key[0] and event["visibility"] == "agent"]
        prerequisite = _prerequisite_state(rows, history)
        for mode in ("induction", "reflection", "counterfactual"):
            result = _record("dreaming", rows, {"mode": mode, "environment_replay": None}, manifest, prerequisite)
            result["input"].update(person_id=key[0], date=key[1])
            if mode == "counterfactual":
                result["input"]["counterfactual"] = {"world_kind": "observed", "environment_replay": None}
            _attach_evidence(result, rows, indexed, directory)
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
    represented = latest["event_id"] == events[0]["event_id"]
    return {"availability": "observed", "event_id": latest["event_id"],
            "observed_at": latest["occurred_at"],
            "state": None if represented else _public_event_data(latest)}


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
