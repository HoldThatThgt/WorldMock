"""@brief LabelKit、WorldMock 与真人轨迹的确定性共同适配。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.evaluation.errors import EvaluationError
from worldmock.footprints.models import Event
from worldmock.evaluation.io import read_jsonl
from worldmock.evaluation.models import (
    ActivitySession,
    DatasetView,
    DayRecord,
    EvaluationScenario,
    EvidenceEvent,
    JsonObject,
    PublicEvidence,
    parse_datetime,
    parse_utc_seconds,
)
from worldmock.evaluation.public_evidence import (
    labelkit_public_evidence,
    worldmock_activity_category,
    worldmock_public_evidence,
)
from worldmock.evaluation.validation import (
    VerifiedDelivery,
    validate_canonical_delivery,
    validate_labelkit_delivery,
)


_LABELKIT_SOURCE_CHANNELS = {
    "appUsageEvent": "application",
    "screenMemoryEvent": "screen",
    "pasteBoardEvent": "clipboard",
    "notificationEvent": "notification",
    "publicTimePeriodEvent": "context_time",
    "commutePeriodEvent": "context_commute",
    "poiEvent": "context_location",
}
_LABELKIT_COMMON_CHANNELS = {
    "application": "digital_interaction",
    "screen": "digital_interaction",
    "clipboard": "digital_interaction",
    "notification": "notification",
    "context_time": "temporal_context",
    "context_commute": "mobility_context",
    "context_location": "mobility_context",
}
_PRIVATE_WORLD_EVENTS = {"decision_started", "model_request", "model_response", "memory_updated"}
_WORLD_SOURCE_CHANNELS = {
    "observation": "observation",
    "decision_started": "decision",
    "model_request": "model",
    "model_response": "model",
    "action_requested": "action",
    "action_accepted": "action",
    "action_rejected": "action",
    "activity_progress": "activity",
    "action_result": "action",
    "environment_event": "environment",
    "memory_updated": "memory",
    "checkpoint_created": "lifecycle",
    "run_ended": "lifecycle",
    "run_failed": "lifecycle",
}
#: 记为数字交互的活动类型：占用重列前声明 device 占用的五类（工作、观看、游戏、读信、浏览），保持既有分类
#: （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」重列占用后，数字交互改按活动类型判定）。
_DIGITAL_ACTIVITY_TYPES = frozenset({"work", "watch", "play_game", "read_mail", "browse"})
_RIGHT_BOUNDARY_EVENTS = {"checkpoint_created", "run_ended", "run_failed"}


def adapt_labelkit(root: Path, scenario: EvaluationScenario) -> DatasetView:
    """@brief 认证并适配旧 LabelKit 修复交付。"""
    delivery = validate_labelkit_delivery(root, scenario)
    main_rows = read_jsonl(delivery.activity_path)
    stream_rows = read_jsonl(delivery.event_path)
    sequences = _index_labelkit_sequences(main_rows, scenario)
    events, owner_order = _adapt_labelkit_events(stream_rows, delivery, sequences)
    _validate_labelkit_members(sequences, owner_order, events)
    activities = _labelkit_activities(sequences, owner_order, events, scenario)
    public = labelkit_public_evidence(
        main_rows, stream_rows, {identity: value[1] for identity, value in sequences.items()}
    )
    _validate_event_days(events, scenario)
    _validate_public_window(public, scenario, allow_right_boundary=False)
    days = build_days(events, activities, scenario)
    validation = dict(delivery.evidence)
    validation.update({
        "input_events": len(stream_rows),
        "input_activities": len(main_rows),
        "public_evidence": len(public),
    })
    return DatasetView(
        "labelkit",
        delivery.dataset_id,
        delivery.run_id,
        scenario.window,
        tuple(events),
        tuple(activities),
        tuple(days),
        delivery.artifacts,
        validation,
        public,
    )


def _index_labelkit_sequences(
    rows: list[JsonObject], scenario: EvaluationScenario
) -> dict[str, tuple[JsonObject, str]]:
    """@brief 索引序列并冻结每个序列的共同类别。"""
    indexed: dict[str, tuple[JsonObject, str]] = {}
    for line_number, row in enumerate(rows, start=1):
        meta = _mapping(row.get("_meta"), f"main line {line_number} _meta")
        sequence_id = _text(meta.get("id"), f"main line {line_number} _meta.id")
        if sequence_id in indexed:
            raise EvaluationError("duplicate_id", "LabelKit sequence IDs must be unique")
        category = _labelkit_category(meta, scenario)
        _validate_sequence_members(meta, line_number)
        indexed[sequence_id] = (row, category)
    return indexed


def _labelkit_category(meta: Mapping[str, Any], scenario: EvaluationScenario) -> str:
    """@brief 按冻结 closed mapping 映射旧序列类别。"""
    generation = _mapping(meta.get("generation"), "LabelKit generation metadata")
    sequence_class = _text(generation.get("sequence_class"), "LabelKit sequence_class")
    pattern = _text(generation.get("pattern"), "LabelKit pattern")
    if sequence_class == "habit_no_intent":
        return scenario.labelkit_pattern_map.get(pattern, "unknown")
    return scenario.labelkit_class_map.get(sequence_class, "unknown")


def _validate_sequence_members(meta: Mapping[str, Any], line_number: int) -> None:
    """@brief 验证主序列成员描述。"""
    stream = _mapping(meta.get("stream"), f"main line {line_number} stream")
    member_ids = stream.get("member_ids")
    members = stream.get("members")
    if not isinstance(member_ids, list) or not all(isinstance(item, str) and item for item in member_ids):
        raise EvaluationError("invalid_input", "LabelKit member_ids must be non-empty strings")
    if len(member_ids) != len(set(member_ids)) or stream.get("member_count") != len(member_ids):
        raise EvaluationError("invalid_input", "LabelKit sequence member count is invalid")
    if not isinstance(members, list) or [item.get("id") for item in members if isinstance(item, dict)] != member_ids:
        raise EvaluationError("invalid_input", "LabelKit sequence members do not match member_ids")


def _adapt_labelkit_events(
    rows: list[JsonObject],
    delivery: VerifiedDelivery,
    sequences: Mapping[str, tuple[JsonObject, str]],
) -> tuple[list[EvidenceEvent], dict[str, list[str]]]:
    """@brief 逐行适配旧流事件并保留 owner 内顺序。"""
    events: list[EvidenceEvent] = []
    owner_order: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=1):
        event, owner = _adapt_labelkit_event(row, line_number, delivery, sequences)
        if event.source_event_id in seen:
            raise EvaluationError("duplicate_id", "LabelKit event IDs must be unique")
        seen.add(event.source_event_id)
        owner_order[owner].append(event.source_event_id)
        events.append(event)
    return events, owner_order


def _adapt_labelkit_event(
    row: JsonObject,
    line_number: int,
    delivery: VerifiedDelivery,
    sequences: Mapping[str, tuple[JsonObject, str]],
) -> tuple[EvidenceEvent, str]:
    """@brief 适配一个旧流事件。"""
    payload = _mapping(row.get("payload"), f"stream line {line_number} payload")
    meta = _mapping(row.get("_meta"), f"stream line {line_number} _meta")
    source = _mapping(meta.get("event"), f"stream line {line_number} _meta.event")
    event_id = _text(source.get("event_id"), f"stream line {line_number} event_id")
    owner = _text(source.get("owner_sequence_id"), f"stream line {line_number} owner_sequence_id")
    if owner not in sequences:
        raise EvaluationError("dangling_reference", "LabelKit event owner does not exist")
    if source.get("actor") != "user":
        raise EvaluationError("identity_mismatch", "LabelKit event actor must be user")
    occurred = parse_datetime(source.get("timestamp"), f"stream line {line_number} timestamp")
    data_name = _text(payload.get("dataName"), f"stream line {line_number} dataName")
    source_channel = _LABELKIT_SOURCE_CHANNELS.get(data_name)
    if source_channel is None:
        raise EvaluationError("unknown_event", f"unsupported LabelKit dataName: {data_name}")
    channel = _LABELKIT_COMMON_CHANNELS[source_channel]
    ended = _labelkit_event_end(payload, data_name, occurred, line_number)
    _validate_labelkit_payload_time(payload, data_name, occurred, line_number)
    category = sequences[owner][1]
    event = EvidenceEvent(
        delivery.dataset_id,
        event_id,
        "user",
        occurred,
        ended,
        data_name,
        channel,
        category,
        owner,
        None,
        None,
        (),
        None,
        None,
        {},
        None,
        f"{delivery.event_path}:{line_number}",
        source_channel,
        "agent",
        "external",
    )
    return event, owner


def _labelkit_event_end(payload: Mapping[str, Any], data_name: str, start: datetime, line_number: int) -> datetime:
    """@brief 按旧 payload 的声明字段计算事件终点。"""
    if data_name == "appUsageEvent":
        duration = _nonnegative_int(payload.get("duration"), f"stream line {line_number} duration")
        return start + timedelta(milliseconds=duration)
    if data_name == "screenMemoryEvent":
        return _milliseconds_time(payload.get("updateTime"), f"stream line {line_number} updateTime")
    if data_name == "poiEvent":
        return _milliseconds_time(payload.get("endTime"), f"stream line {line_number} endTime")
    return start


def _validate_labelkit_payload_time(
    payload: Mapping[str, Any], data_name: str, occurred: datetime, line_number: int
) -> None:
    """@brief 验证 payload 起点与冻结事件时间一致。"""
    fields = {
        "appUsageEvent": "startTime",
        "screenMemoryEvent": "createTime",
        "pasteBoardEvent": "timestamp",
        "notificationEvent": "messageTime",
        "publicTimePeriodEvent": "timestamp",
        "commutePeriodEvent": "timestamp",
        "poiEvent": "startTime",
    }
    parsed = _milliseconds_time(payload.get(fields[data_name]), f"stream line {line_number} payload time")
    if abs((parsed - occurred).total_seconds()) > 0.001:
        raise EvaluationError("time_mismatch", "LabelKit payload time does not match event metadata")


def _milliseconds_time(value: Any, field_name: str) -> datetime:
    """@brief 把纪元毫秒解析为 UTC 时间。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError("invalid_time", f"{field_name} must be integer epoch milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, ZoneInfo("UTC"))
    except (OverflowError, OSError, ValueError) as exc:
        raise EvaluationError("invalid_time", f"{field_name} is outside supported range") from exc


def _nonnegative_int(value: Any, field_name: str) -> int:
    """@brief 验证非负整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationError("invalid_input", f"{field_name} must be a non-negative integer")
    return value


def _validate_labelkit_members(
    sequences: Mapping[str, tuple[JsonObject, str]],
    owner_order: Mapping[str, list[str]],
    events: list[EvidenceEvent],
) -> None:
    """@brief 验证主数据与事件流成员一一对应。"""
    expected: list[str] = []
    for sequence_id, (row, _) in sequences.items():
        stream = row["_meta"]["stream"]
        member_ids = stream["member_ids"]
        if owner_order.get(sequence_id) != member_ids:
            raise EvaluationError("member_mismatch", "LabelKit owner event order does not match main sequence")
        expected.extend(member_ids)
    actual = [event.source_event_id for event in events]
    if len(expected) != len(actual) or set(expected) != set(actual):
        raise EvaluationError("conservation_failed", "LabelKit main and stream event membership differs")


def _labelkit_activities(
    sequences: Mapping[str, tuple[JsonObject, str]],
    owner_order: Mapping[str, list[str]],
    events: list[EvidenceEvent],
    scenario: EvaluationScenario,
) -> list[ActivitySession]:
    """@brief 从权威 owner 边界构造不会重复帧的活动会话。"""
    event_by_id = {event.source_event_id: event for event in events}
    activities: list[ActivitySession] = []
    for sequence_id, (row, category) in sequences.items():
        members = [event_by_id[event_id] for event_id in owner_order[sequence_id]]
        session_members = [event for event in members if event.channel == "digital_interaction"] or members
        starts = [event.occurred_at for event in session_members]
        ends = [event.ended_at or event.occurred_at for event in session_members]
        channels = tuple(sorted({event.channel for event in members}))
        source_channels = tuple(sorted({event.source_channel for event in members}))
        domains = set(scenario.official_domain_map.get(category, ()))
        if "application" in source_channels:
            domains.add("internet_use")
        activities.append(
            ActivitySession(
                sequence_id,
                "user",
                category,
                min(starts),
                max(ends),
                "observed",
                channels,
                _optional_text(row.get("bundleName")),
                "observed_digital_session",
                tuple(sorted(domains)),
                (),
                f"{row['_meta']['source'].get('file', '')}#sequence={sequence_id}",
                ((min(starts), max(ends)),),
                source_channels,
            )
        )
    return sorted(activities, key=lambda item: (item.started_at, item.activity_id))


def _validate_event_days(events: Iterable[EvidenceEvent], scenario: EvaluationScenario) -> None:
    """@brief 验证旧数据的实际事件覆盖全部声明自然日。"""
    dates: set[date] = set()
    for event in events:
        if not scenario.window.contains(event.occurred_at):
            raise EvaluationError("invalid_input", "event occurs outside the declared window")
        if event.ended_at and event.ended_at > scenario.window.end:
            raise EvaluationError("invalid_input", "event ends outside the declared window")
        dates.add(scenario.window.localize(event.occurred_at).date())
    if dates != set(scenario.window.days):
        raise EvaluationError("invalid_input", "LabelKit event dates do not cover the declared window")


def adapt_worldmock(root: Path, scenario: EvaluationScenario) -> DatasetView:
    """@brief 认证并适配 WorldMock 固定导出。"""
    return _adapt_canonical(root, scenario, "worldmock")


def adapt_human_reference(root: Path, scenario: EvaluationScenario) -> DatasetView:
    """@brief 认证并适配使用相同观察合同的真人轨迹。"""
    return _adapt_canonical(root, scenario, "human_reference")


def _adapt_canonical(root: Path, scenario: EvaluationScenario, source_kind: str) -> DatasetView:
    """@brief 适配 WorldMock 共同交付格式。"""
    delivery = validate_canonical_delivery(root, scenario, source_kind)
    event_rows = [parse_world_event(row) for row in read_jsonl(delivery.event_path)]
    activity_rows = read_jsonl(delivery.activity_path)
    activities = _adapt_world_activities(activity_rows, delivery, scenario)
    activity_index = {item.activity_id: item for item in activities}
    events = _adapt_world_events(event_rows, delivery, scenario, activity_index)
    _validate_world_links(events, activities, delivery)
    if source_kind == "worldmock":
        _validate_world_completion(events, scenario, delivery)
    public = worldmock_public_evidence(event_rows, activity_rows, scenario.worldmock_activity_map)
    _validate_public_window(public, scenario, allow_right_boundary=True)
    days = build_days(events, activities, scenario)
    validation = dict(delivery.evidence)
    validation.update({
        "input_events": len(event_rows),
        "input_activities": len(activity_rows),
        "public_evidence": len(public),
    })
    return DatasetView(
        source_kind,
        delivery.dataset_id,
        delivery.run_id,
        scenario.window,
        tuple(events),
        tuple(activities),
        tuple(days),
        delivery.artifacts,
        validation,
        public,
    )


def _validate_public_window(
    evidence: Sequence[PublicEvidence], scenario: EvaluationScenario, allow_right_boundary: bool
) -> None:
    """@brief 拒绝窗口外公开证据，仅允许认证运行的右端收尾。"""
    for item in evidence:
        at_right = allow_right_boundary and item.occurred_at == scenario.window.end
        if not scenario.window.contains(item.occurred_at) and not at_right:
            raise EvaluationError("invalid_input", "public evidence occurs outside the declared window")


def _adapt_world_events(
    rows: list[Event],
    delivery: VerifiedDelivery,
    scenario: EvaluationScenario,
    activity_index: Mapping[str, ActivitySession],
) -> list[EvidenceEvent]:
    """@brief 按冻结 Event 模型适配 WorldMock 事件。"""
    events: list[EvidenceEvent] = []
    seen: set[str] = set()
    current_run_started = False
    for expected_seq, row in enumerate(rows, start=1):
        run_id = row.run_id
        if run_id not in delivery.accepted_run_ids:
            raise EvaluationError("identity_mismatch", "WorldMock event run_id is not declared by manifest")
        if current_run_started and run_id != delivery.run_id:
            raise EvaluationError("invalid_order", "WorldMock ancestor events must form a prefix")
        current_run_started = current_run_started or run_id == delivery.run_id
        event = _adapt_world_event(row, expected_seq, delivery, activity_index)
        if event.source_event_id in seen:
            raise EvaluationError("duplicate_id", "WorldMock event IDs must be unique")
        at_right = event.occurred_at == scenario.window.end and (
            delivery.evidence.get("complete_export_authenticated") is True
            or event.event_family in _RIGHT_BOUNDARY_EVENTS
        )
        if not scenario.window.contains(event.occurred_at) and not at_right:
            raise EvaluationError("invalid_input", "WorldMock event occurs outside the declared window")
        if event.ended_at is not None and event.ended_at > scenario.window.end:
            raise EvaluationError("invalid_input", "WorldMock event ends outside the declared window")
        seen.add(event.source_event_id)
        events.append(event)
    return events


def _adapt_world_event(
    parsed: Event,
    expected_seq: int,
    delivery: VerifiedDelivery,
    activity_index: Mapping[str, ActivitySession],
) -> EvidenceEvent:
    """@brief 适配一个严格 WorldMock Event。"""
    _require_world_event_identity(parsed, expected_seq, delivery)
    activity = activity_index.get(parsed.activity_id) if parsed.activity_id else None
    source_channel = _WORLD_SOURCE_CHANNELS[parsed.event_type]
    channel = _world_common_channel(parsed, activity)
    category = activity.category if activity else "unknown"
    return EvidenceEvent(
        delivery.dataset_id,
        parsed.event_id,
        _person_id(parsed.agent_id),
        parse_utc_seconds(parsed.occurred_at, "WorldMock occurred_at"),
        _world_event_end(parsed.data),
        parsed.event_type,
        channel,
        category,
        parsed.activity_id,
        parsed.action_id,
        parsed.decision_id,
        tuple(parsed.caused_by),
        parsed.state_version_before,
        parsed.state_version_after,
        _resource_changes(parsed.data),
        _optional_text(parsed.data.get("status")),
        f"{delivery.event_path}:{expected_seq}",
        source_channel,
        parsed.visibility,
        parsed.source_kind,
        _optional_text(parsed.data.get("checkpoint_id")),
    )


def parse_world_event(row: JsonObject) -> Event:
    """@brief 使用生产 Event 模型拒绝缺失和未知字段。"""
    try:
        return Event.model_validate(row)
    except ValidationError as exc:
        raise EvaluationError("invalid_input", "WorldMock event does not match Event schema") from exc


def _require_world_event_identity(event: Event, expected_seq: int, delivery: VerifiedDelivery) -> None:
    """@brief 验证 Event 模式身份和文件顺序。"""
    if event.run_id not in delivery.accepted_run_ids:
        raise EvaluationError("identity_mismatch", "WorldMock event run_id is not declared by manifest")
    if event.event_seq != expected_seq:
        raise EvaluationError("invalid_order", "WorldMock event_seq must be contiguous from one")


def _world_common_channel(event: Event, activity: ActivitySession | None) -> str:
    """@brief 把内部事件映射到公平的共同观察语义。"""
    if event.event_type in _PRIVATE_WORLD_EVENTS or event.source_kind == "model":
        return "private_model"
    if event.visibility == "system":
        return "private_system"
    if activity is not None and "digital_interaction" in activity.channels:
        return "digital_interaction"
    if activity is not None:
        return "physical_activity"
    if event.event_type in _RIGHT_BOUNDARY_EVENTS:
        return "lifecycle"
    return "unmatched_visible"


def _world_event_end(data: Mapping[str, Any]) -> datetime | None:
    """@brief 读取可选的 UTC 秒事件终点。"""
    value = data.get("ended_at")
    return parse_utc_seconds(value, "WorldMock data.ended_at") if value is not None else None


def _resource_changes(data: Mapping[str, Any]) -> dict[str, float]:
    """@brief 读取确定性数值资源变化。"""
    value = data.get("resource_changes", {})
    if not isinstance(value, dict) or not all(isinstance(key, str) and key for key in value):
        raise EvaluationError("invalid_input", "WorldMock resource_changes must be an object")
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value.values()):
        raise EvaluationError("invalid_input", "WorldMock resource changes must be numeric")
    return {key: float(item) for key, item in value.items()}


def _adapt_world_activities(
    rows: list[JsonObject], delivery: VerifiedDelivery, scenario: EvaluationScenario
) -> list[ActivitySession]:
    """@brief 适配 Life activity JSONL。"""
    activities: list[ActivitySession] = []
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=1):
        activity = _adapt_world_activity(row, line_number, delivery, scenario)
        if activity.activity_id in seen:
            raise EvaluationError("duplicate_id", "WorldMock activity IDs must be unique")
        seen.add(activity.activity_id)
        activities.append(activity)
    return sorted(activities, key=lambda item: (item.started_at, item.activity_id))


def _adapt_world_activity(
    row: JsonObject, line_number: int, delivery: VerifiedDelivery, scenario: EvaluationScenario
) -> ActivitySession:
    """@brief 适配一个 Life activity。"""
    _require_activity_fields(row, line_number)
    activity_id = _text(row.get("activity_id"), f"activity line {line_number} activity_id")
    activity_type = _text(row.get("activity_type"), f"activity line {line_number} activity_type")
    started = parse_utc_seconds(row.get("started_at"), f"activity line {line_number} started_at")
    elapsed = _nonnegative_number(row.get("elapsed_seconds"), f"activity line {line_number} elapsed_seconds")
    segments = _activity_segments(row.get("segments"), started, elapsed, line_number)
    ended = _activity_end(row, started, segments, line_number)
    if not delivery.window.contains(started) or ended > delivery.window.end:
        raise EvaluationError("invalid_input", "WorldMock activity is outside the declared window")
    _validate_planned_end(row, started, line_number)
    category = worldmock_activity_category(
        activity_type, row.get("data"), scenario.worldmock_activity_map
    )
    resources = _string_list(row.get("resource_claims"), f"activity line {line_number} resource_claims")
    digital = activity_type in _DIGITAL_ACTIVITY_TYPES
    channels = ("digital_interaction",) if digital else ("physical_activity",)
    duration_kind = "world_internet_session" if digital else "world_activity"
    domains = set(scenario.official_domain_map.get(category, ()))
    if digital:
        domains.add("internet_use")
    return ActivitySession(
        activity_id,
        _person_id(row.get("agent_id")),
        category,
        started,
        ended,
        _text(row.get("status"), f"activity line {line_number} status"),
        channels,
        None,
        duration_kind,
        tuple(sorted(domains)),
        _string_list(row.get("source_event_ids"), f"activity line {line_number} source_event_ids"),
        f"{delivery.activity_path}:{line_number}",
        segments,
        tuple(sorted(resources)),
    )


def _require_activity_fields(row: JsonObject, line_number: int) -> None:
    """@brief 验证固定活动导出的必需字段。"""
    required = {
        "activity_id", "agent_id", "activity_type", "status", "started_at", "planned_end_at",
        "ended_at", "elapsed_seconds", "boundary_type", "interruption_reason", "resource_claims",
        "last_settled_at", "planned_duration_seconds", "effective_work_seconds",
        "media_position_seconds", "route_progress_meters", "next_interaction_at", "target_id",
        "destination_id", "travel_mode", "playback_speed", "data", "segments", "source_event_ids",
    }
    missing = sorted(required - row.keys())
    if missing:
        fields = ", ".join(missing)
        raise EvaluationError("invalid_input", f"activity line {line_number} is missing fields: {fields}")
    _optional_text(row.get("boundary_type"))
    _optional_text(row.get("interruption_reason"))
    _string_list(row.get("source_event_ids"), f"activity line {line_number} source_event_ids")


def _activity_segments(
    value: Any, started: datetime, elapsed: float, line_number: int
) -> tuple[tuple[datetime, datetime], ...]:
    """@brief 验证活动实际运行区间并核对累计时长。"""
    if not isinstance(value, list):
        raise EvaluationError("invalid_input", "WorldMock activity segments must be an array")
    segments = tuple(_activity_segment(item, line_number) for item in value)
    previous_end = started
    for segment_start, segment_end in segments:
        if segment_start < started or segment_start < previous_end:
            raise EvaluationError("invalid_order", "WorldMock activity segments overlap or precede start")
        previous_end = segment_end
    actual = sum((end - start).total_seconds() for start, end in segments)
    if actual != elapsed:
        raise EvaluationError("time_mismatch", "WorldMock activity segments do not match elapsed_seconds")
    return segments


def _activity_segment(value: Any, line_number: int) -> tuple[datetime, datetime]:
    """@brief 解析一个正时长活动区间。"""
    segment = _mapping(value, f"activity line {line_number} segment")
    start = parse_utc_seconds(segment.get("started_at"), "activity segment started_at")
    end = parse_utc_seconds(segment.get("ended_at"), "activity segment ended_at")
    if end <= start:
        raise EvaluationError("invalid_time", "WorldMock activity segment must have positive duration")
    return start, end


def _activity_end(
    row: JsonObject,
    started: datetime,
    segments: tuple[tuple[datetime, datetime], ...],
    line_number: int,
) -> datetime:
    """@brief 读取会话终点，暂停空档不计入实际活动时长。"""
    ended_value = row.get("ended_at")
    ended = parse_utc_seconds(ended_value, f"activity line {line_number} ended_at") if ended_value is not None else None
    status = _text(row.get("status"), f"activity line {line_number} status")
    terminal = {"completed", "cancelled", "failed", "truncated"}
    if status in terminal and ended is None:
        raise EvaluationError("invalid_input", "terminal WorldMock activity must declare ended_at")
    effective_end = ended or (segments[-1][1] if segments else started)
    if effective_end < started or any(segment_end > effective_end for _, segment_end in segments):
        raise EvaluationError("invalid_time", "WorldMock activity end precedes actual segments")
    return effective_end


def _validate_planned_end(row: JsonObject, started: datetime, line_number: int) -> None:
    """@brief 验证活动计划终点晚于起点。"""
    planned = parse_utc_seconds(row.get("planned_end_at"), f"activity line {line_number} planned_end_at")
    if planned <= started:
        raise EvaluationError("invalid_time", "WorldMock planned_end_at must follow started_at")


def _validate_world_links(
    events: list[EvidenceEvent], activities: list[ActivitySession], delivery: VerifiedDelivery
) -> None:
    """@brief 验证共同导出的引用和逐事件守恒。"""
    event_ids = {event.source_event_id for event in events}
    activity_ids = {activity.activity_id for activity in activities}
    events_by_id = {event.source_event_id: event for event in events}
    for event in events:
        if any(parent not in event_ids for parent in event.caused_by):
            raise EvaluationError("dangling_reference", "WorldMock caused_by references a missing event")
        if event.activity_id and event.activity_id not in activity_ids:
            raise EvaluationError("dangling_reference", "WorldMock event references a missing activity")
    for activity in activities:
        if any(source not in events_by_id for source in activity.caused_by):
            raise EvaluationError("dangling_reference", "WorldMock activity source event is missing")
        if any(events_by_id[source].activity_id != activity.activity_id for source in activity.caused_by):
            raise EvaluationError("identity_mismatch", "WorldMock activity source event identity differs")
    if len(events) != delivery.artifacts["events"].rows:
        raise EvaluationError("conservation_failed", "WorldMock event adapter did not preserve every row")


def _validate_world_completion(
    events: Sequence[EvidenceEvent],
    scenario: EvaluationScenario,
    delivery: VerifiedDelivery,
) -> None:
    """@brief 验证完成导出在声明右边界具有唯一运行终态。"""
    endings = [item for item in events if item.event_family == "run_ended"]
    if len(endings) != 1 or endings[0].occurred_at != scenario.window.end:
        raise EvaluationError("invalid_input", "WorldMock run_ended must occur once at window end")
    checkpoints = [item for item in events if item.event_family == "checkpoint_created"]
    expected_id = delivery.evidence.get("checkpoint_id")
    valid = [item for item in checkpoints if item.occurred_at == scenario.window.end]
    if len(valid) != 1 or valid[0].checkpoint_id != expected_id:
        raise EvaluationError("invalid_input", "WorldMock completion checkpoint does not match manifest")
    if events.index(valid[0]) <= events.index(endings[0]) or valid[0] != events[-1]:
        raise EvaluationError("invalid_order", "WorldMock completion checkpoint must follow run_ended")
    if any(item.event_family == "run_failed" for item in events):
        raise EvaluationError("invalid_input", "completed WorldMock export contains run_failed")


def build_days(
    events: Iterable[EvidenceEvent], activities: Iterable[ActivitySession], scenario: EvaluationScenario
) -> list[DayRecord]:
    """@brief 构造包含零活动日的十分钟自然日视图。"""
    event_list = tuple(events)
    activity_list = tuple(activities)
    return [
        _build_day(value, event_list, activity_list, scenario)
        for value in scenario.window.days
    ]


def _build_day(
    value: date,
    events: tuple[EvidenceEvent, ...],
    activities: tuple[ActivitySession, ...],
    scenario: EvaluationScenario,
) -> DayRecord:
    """@brief 构造单个自然日视图。"""
    zone = ZoneInfo(scenario.window.timezone)
    start = datetime.combine(value, time.min, zone)
    end = start + timedelta(days=1)
    day_activities = [item for item in activities if _activity_overlaps(item, start, end)]
    day_events = [item for item in events if start <= item.occurred_at.astimezone(zone) < end]
    slots = tuple(_slot_category(start, index, day_activities) for index in range(144))
    return DayRecord(
        value,
        scenario.calendar_class(value),
        tuple(item.activity_id for item in day_activities),
        slots,
        len(day_events),
        tuple(sorted({item.channel for item in day_events})),
    )


def _slot_category(start: datetime, index: int, activities: list[ActivitySession]) -> str:
    """@brief 按最大重叠选择一个十分钟格的确定类别。"""
    slot_start = start + timedelta(minutes=index * 10)
    slot_end = slot_start + timedelta(minutes=10)
    ranked = [(_overlap_seconds(item, slot_start, slot_end), item.category, item.activity_id) for item in activities]
    eligible = [item for item in ranked if item[0] > 0]
    if not eligible:
        return "none"
    best = sorted(eligible, key=lambda item: (-item[0], item[1], item[2]))[0]
    return best[1]


def _overlap_seconds(activity: ActivitySession, start: datetime, end: datetime) -> float:
    """@brief 计算活动与时间格的重叠秒数。"""
    segments = (
        activity.segments
        if activity.segments is not None
        else ((activity.started_at, activity.ended_at),)
    )
    return sum(_interval_overlap(left, right, start, end) for left, right in segments)


def _activity_overlaps(activity: ActivitySession, start: datetime, end: datetime) -> bool:
    """@brief 判断活动的实际区间是否与自然日重叠。"""
    if activity.segments is not None:
        return any(left < end and right > start for left, right in activity.segments)
    return activity.started_at < end and activity.ended_at >= start


def _interval_overlap(left: datetime, right: datetime, start: datetime, end: datetime) -> float:
    """@brief 计算两个左闭右开区间的重叠秒数。"""
    overlap_start = max(left, start)
    overlap_end = min(right, end)
    return max(0.0, (overlap_end - overlap_start).total_seconds())


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    """@brief 验证对象。"""
    if not isinstance(value, dict):
        raise EvaluationError("invalid_input", f"{field_name} must be an object")
    return value


def _text(value: Any, field_name: str) -> str:
    """@brief 验证非空字符串。"""
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_input", f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: Any) -> str | None:
    """@brief 验证可选字符串。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_input", "optional text field must be a non-empty string")
    return value


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    """@brief 验证不重复字符串数组。"""
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise EvaluationError("invalid_input", f"{field_name} must be a string array")
    if len(value) != len(set(value)):
        raise EvaluationError("invalid_input", f"{field_name} must not contain duplicates")
    return tuple(value)


def _person_id(value: Any) -> str:
    """@brief 把人物标识转换为共同字符串。"""
    if value is None:
        return "system"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise EvaluationError("invalid_input", "agent_id must be an integer, string, or null")
    return str(value)


def _integer_or_none(value: Any, field_name: str) -> int | None:
    """@brief 验证可选非负整数。"""
    if value is None:
        return None
    return _nonnegative_int(value, field_name)


def _nonnegative_number(value: Any, field_name: str) -> float:
    """@brief 验证非负数值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise EvaluationError("invalid_input", f"{field_name} must be a non-negative number")
    return float(value)
