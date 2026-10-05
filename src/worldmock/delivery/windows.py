"""@brief 从不可变事件导出构造动作、意图和 dreaming 的证据窗口。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json, sha256_json
from worldmock.footprints.validation import validate_export
from worldmock.life.control_items import CONTROL_TOOLS, control_request
from worldmock.life.prompt_schemas import USER_LAYER_NAMES

#: 任何窗口输入都不进入的私有事件（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「交付」）。
PRIVATE_EVENTS = {"model_request", "model_response", "memory_updated", "decision_started"}
#: dreaming 私有事件集等于私有事件集：dreaming 读 self_note 与实际请求中的层 JSON，请求本身不作窗口事件。
DREAMING_PRIVATE_EVENTS = PRIVATE_EVENTS
#: 观察事件中环境构建、人物可见的层原文；私有绑定、看法偏好与证据键不进入窗口输入。
OBSERVATION_LAYER_KEYS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer")
#: 观察进度层来源中可作为窗口证据展开的人物可见事件类型；其余来源（动作请求与私有事件）不展开。
PROGRESS_EVIDENCE_EVENTS = {"action_result", "environment_event"}


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
    """@brief 选取决策提示：取该动作之前最后一份不带 purpose 的请求，并核对其工具含该动作的工具名。

    不满足是完整性故障，不回退到更早的请求。截断重试的请求照此被选中；就寝反思的请求晚于动作登记，事件序号在动作
    之后，不会被选中（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 「身份与判定」）。
    """
    candidates = [row for row in rows if row["event_type"] == "model_request"
                  and row["event_seq"] < action["event_seq"] and row["agent_id"] == action["agent_id"]
                  and row["data"].get("phase") == "request" and not row["data"].get("purpose")]
    if not candidates:
        raise IntegrityError("Action training has no recorded decision prompt")
    row = max(candidates, key=lambda item: item["event_seq"])
    request = row["data"].get("request")
    if not isinstance(request, dict):
        raise IntegrityError("Action training has an invalid recorded decision prompt")
    messages, tools = request.get("messages"), request.get("tools")
    if (not isinstance(messages, list) or not messages or not isinstance(tools, list)
            or any(not isinstance(message, dict) for message in messages)):
        raise IntegrityError("Action training has an incomplete recorded decision prompt")
    names = {item.get("function", {}).get("name") for item in tools if isinstance(item, dict)
             and isinstance(item.get("function"), dict)}
    if action["data"].get("tool_name") not in names:
        raise IntegrityError("Action training prompt does not expose the requested tool")
    return row


def _attach_decision_prompt(record: dict, source: dict) -> None:
    """@brief 将实际请求及原事件摘要绑定到窗口，原始目标仍留在独立 target。"""
    request = source["data"]["request"]
    record["input"]["decision_prompt"] = request
    record["provenance"]["decision_prompt_event_id"] = source["event_id"]
    record["provenance"]["decision_prompt_sha256"] = sha256_json(request)


def _decision_groups(events: list[dict]) -> dict[str, list[dict]]:
    """@brief 按决策身份分组事件（含私有的模型请求），供窗口选取实际行动请求。"""
    groups = defaultdict(list)
    for event in events:
        if event["decision_id"]:
            groups[event["decision_id"]].append(event)
    return groups


def _attach_decision_layers(record: dict, rows: list[dict], decisions: dict) -> None:
    """@brief dreaming 读实际请求中的层 JSON（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「交付」）。

    窗口内每个动作请求取同人物、同决策、动作之前最后一份不带 purpose 且工具含该动作工具名的请求（与动作窗口同一
    prompt 权威与选取规则 _decision_prompt_source：截断重试照此被选中，就寝反思请求不会被选中），把其首个 user
    的六层 JSON 连同所属动作请求写入输入；请求事件身份与层 JSON 的 canonical 摘要写入来源。须在 _attach_evidence 之前调用。
    """
    layers, sources = [], []
    for row in rows:
        if row["event_type"] != "action_requested":
            continue
        source = _decision_prompt_source(row, decisions.get(row["decision_id"], []))
        value = _request_layers(source["data"]["request"])
        layers.append({"action_event_id": row["event_id"], "layers": value})
        sources.append({"event_id": source["event_id"], "sha256": sha256_json(value)})
    record["input"]["decision_layers"] = layers
    record["provenance"]["decision_layer_sources"] = sources


def _request_layers(request: dict) -> list[dict]:
    """@brief 实际行动请求首个 user 的六层 JSON：顶层数组，层序为工具层、人设层、记忆层、待办层、环境层、进度层。"""
    user = next((message for message in request["messages"] if message.get("role") == "user"), None)
    content = user.get("content") if user is not None else None
    try:
        value = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError:
        value = None
    names = tuple(layer.get("layer") for layer in value if isinstance(layer, dict)) if isinstance(value, list) else ()
    if names != USER_LAYER_NAMES or len(value) != len(USER_LAYER_NAMES):
        raise IntegrityError("Dreaming requires the six-layer JSON of the recorded action request")
    return value


def _validated_action_groups(event: dict, actions: dict, decisions: dict) -> tuple[list[dict], list[dict]]:
    """@brief 拒绝跨人物复用动作或决策身份形成的证据分组。"""
    agent_id = event["agent_id"]
    if agent_id is None:
        raise IntegrityError("Action request must identify its agent")
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
        raise IntegrityError(f"Action training {group} identity crosses agent boundaries")




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

    观察只提供原样保存的工具层、待办层（含个人事务行）、环境层与记忆层，不重新渲染；动作请求带人物回复正文 self_note。
    结束活动写下的记忆与看法属于记忆写入：意图与 dreaming 窗口输入中三个控制工具的请求经 control_items 读出后
    按现行形状只留每项的 activity_name 与 summary（items[].memories 与 items[].views 裁去），请求级 errands 恒不带出；
    缺 errands 的旧形状记录经 control_items 以完整性故障拒绝。投影写进新字典，不原地修改原事件，动作窗口的目标仍是
    同一事件、保留完整请求（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第九章，
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章，
    docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-交付边界 ）。其余事件（含个人事务的六种 environment_event）的公开投影为空对象。
    """
    data = event["data"]
    kind = event["event_type"]
    if kind == "observation":
        return {key: data[key] for key in OBSERVATION_LAYER_KEYS if key in data}
    if kind == "action_requested":
        public = {key: data[key] for key in ("tool_name", "request", "self_note") if key in data}
        if data.get("tool_name") in CONTROL_TOOLS and "request" in public:
            public["request"] = control_request(data["tool_name"], public["request"], memories=False)
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
    layer_event_ids = [item["event_id"] for item in record["provenance"].get("decision_layer_sources", [])]
    sources = allowed + ([prompt_event_id] if prompt_event_id else []) + layer_event_ids
    record["provenance"]["source_event_ids"] = list(dict.fromkeys(sources))
    record["provenance"]["window_id"] = _window_identity(record["task"], record["input"])


def _window_identity(task: str, input_data: dict) -> str:
    """@brief 仅由模型可见输入生成公开窗口身份，避免承诺未来目标。"""
    return sha256_json([task, input_data])


def _expand_observed_results(events: list[dict], indexed: dict, cutoff: int) -> list[dict]:
    """@brief 按观察 progress_event_ids 引用展开进度层呈现过的先前结果与环境事件。"""
    expanded = {event["event_id"]: event for event in events}
    for observation in events:
        if observation["event_type"] != "observation":
            continue
        references = observation["data"].get("progress_event_ids", [])
        if not isinstance(references, list):
            raise IntegrityError("Observed progress event references must be a list")
        for reference in references:
            source = _validated_observed_result(observation, reference, indexed, cutoff)
            if source is not None:
                expanded[source["event_id"]] = source
    return sorted(expanded.values(), key=lambda event: event["event_seq"])


def _validated_observed_result(observation: dict, reference: object, indexed: dict, cutoff: int) -> dict | None:
    """@brief 核对进度来源为截止前同人物可见的结果或环境事件；动作请求与私有来源不展开，返回 None。"""
    if observation["visibility"] != "agent" or observation["agent_id"] is None:
        raise IntegrityError("Observed progress source must be an agent-visible observation")
    if not isinstance(reference, str):
        raise IntegrityError("Observed progress reference has no event identity")
    source = indexed.get(reference)
    if source is None:
        raise IntegrityError("Observed progress reference does not resolve to an exported event")
    if source["event_type"] in PRIVATE_EVENTS | {"action_requested"}:
        return None
    if (source["event_type"] not in PROGRESS_EVIDENCE_EVENTS or source["visibility"] != "agent"
            or source["agent_id"] != observation["agent_id"]):
        raise IntegrityError("Observed progress source is not a visible same-agent result or event")
    if source["event_seq"] >= observation["event_seq"] or source["event_seq"] > cutoff:
        raise IntegrityError("Observed progress source is outside the current knowledge cutoff")
    return source




def _dream_windows(events: list[dict], manifest: dict, directory: Path, indexed: dict) -> list[dict]:
    """@brief 每个人物自然日都覆盖归纳、反思和显式假设，不接触未来事实。

    dreaming 输入读动作请求的 self_note 与实际行动请求中的层 JSON（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「交付」）；
    私有事件（含 decision_started、model_request、model_response、memory_updated）不作窗口事件。
    """
    zone = ZoneInfo(manifest["window"]["timezone"])
    days = defaultdict(list)
    decisions = _decision_groups(events)
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
            _attach_decision_layers(result, rows, decisions)
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
