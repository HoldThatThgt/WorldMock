"""@brief 评估使用的不可变共同数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from worldmock.evaluation.errors import EvaluationError


JsonObject = dict[str, Any]


def parse_datetime(value: Any, field_name: str) -> datetime:
    """@brief 解析必须带时区的 ISO-8601 时间。"""
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_time", f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvaluationError("invalid_time", f"{field_name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvaluationError("invalid_time", f"{field_name} must include a timezone offset")
    return parsed


def parse_utc_seconds(value: Any, field_name: str) -> datetime:
    """@brief 把整数 UTC 秒解析为带时区时间。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError("invalid_time", f"{field_name} must be integer UTC seconds")
    try:
        return datetime.fromtimestamp(value, ZoneInfo("UTC"))
    except (OverflowError, OSError, ValueError) as exc:
        raise EvaluationError("invalid_time", f"{field_name} is outside supported range") from exc


@dataclass(frozen=True)
class TimeWindow:
    """@brief 表示左闭右开的完整自然日窗口。"""

    start: datetime
    end: datetime
    timezone: str

    def __post_init__(self) -> None:
        """@brief 验证窗口时区、边界和自然日对齐。"""
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise EvaluationError("invalid_window", "window timezone is unknown") from exc
        if self.start >= self.end:
            raise EvaluationError("invalid_window", "window start must precede end")
        if self.start.astimezone(zone).timetz().replace(tzinfo=None) != time.min:
            raise EvaluationError("invalid_window", "window start must be local midnight")
        if self.end.astimezone(zone).timetz().replace(tzinfo=None) != time.min:
            raise EvaluationError("invalid_window", "window end must be local midnight")

    @property
    def days(self) -> tuple[date, ...]:
        """@brief 返回窗口内按本地时区排列的自然日。"""
        zone = ZoneInfo(self.timezone)
        first = self.start.astimezone(zone).date()
        stop = self.end.astimezone(zone).date()
        count = (stop - first).days
        return tuple(first + timedelta(days=index) for index in range(count))

    def contains(self, value: datetime) -> bool:
        """@brief 判断时间是否位于左闭右开窗口。"""
        return self.start <= value < self.end

    def localize(self, value: datetime) -> datetime:
        """@brief 把时间转换到场景时区。"""
        return value.astimezone(ZoneInfo(self.timezone))


@dataclass(frozen=True)
class ArtifactIdentity:
    """@brief 保存已经实际核验的文件身份。"""

    path: Path
    rows: int | None
    sha256: str
    bytes: int

    def to_dict(self) -> JsonObject:
        """@brief 返回可稳定序列化的文件身份。"""
        return {
            "path": str(self.path),
            "rows": self.rows,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class EvidenceEvent:
    """@brief 保存逐事件守恒适配后的共同证据。"""

    dataset_id: str
    source_event_id: str
    person_id: str
    occurred_at: datetime
    ended_at: datetime | None
    event_family: str
    channel: str
    category: str
    activity_id: str | None
    action_id: str | None
    decision_id: str | None
    caused_by: tuple[str, ...]
    state_version_before: int | None
    state_version_after: int | None
    resource_changes: Mapping[str, float]
    outcome: str | None
    source_pointer: str
    source_channel: str = ""
    visibility: str = "agent"
    source_kind: str = "environment"
    checkpoint_id: str | None = None

    def to_rule_dict(self) -> JsonObject:
        """@brief 返回生产因果检查器的冻结证据投影。"""
        return {
            "dataset_id": self.dataset_id,
            "source_event_id": self.source_event_id,
            "person_id": self.person_id,
            "occurred_at": self.occurred_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "event_family": self.event_family,
            "channel": self.channel,
            "source_channel": self.source_channel,
            "activity_id": self.activity_id,
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "caused_by": list(self.caused_by),
            "state_version_before": self.state_version_before,
            "state_version_after": self.state_version_after,
            "category": self.category,
            "resource_changes": dict(self.resource_changes),
            "outcome": self.outcome,
            "source_pointer": self.source_pointer,
            "visibility": self.visibility,
            "source_kind": self.source_kind,
            "checkpoint_id": self.checkpoint_id,
        }


@dataclass(frozen=True)
class ActivitySession:
    """@brief 保存不会把多个证据帧重复计数的活动会话。"""

    activity_id: str
    person_id: str
    category: str
    started_at: datetime
    ended_at: datetime
    status: str
    channels: tuple[str, ...]
    application_category: str | None
    duration_kind: str
    official_domains: tuple[str, ...]
    caused_by: tuple[str, ...]
    source_pointer: str
    segments: tuple[tuple[datetime, datetime], ...] | None = None
    source_channels: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        """@brief 返回非负活动秒数。"""
        if self.segments is not None:
            return sum((end - start).total_seconds() for start, end in self.segments)
        return max(0.0, (self.ended_at - self.started_at).total_seconds())


@dataclass(frozen=True)
class PublicEvidence:
    """@brief 保存可进入盲评的闭集公开结构事实。"""

    occurred_at: datetime
    category: str
    interaction: str
    object_type: str
    state: str | None
    object_key: str | None
    attributes: Mapping[str, str]
    source_event_ids: tuple[str, ...]
    session_key: str | None = None

    def field_values(self) -> JsonObject:
        """@brief 返回参与类别字段交集的内部值。"""
        values: JsonObject = {
            "interaction": self.interaction,
            "object_type": self.object_type,
            "state": self.state,
            "object_ref": self.object_key,
        }
        values.update(self.attributes)
        return values


@dataclass(frozen=True)
class DayRecord:
    """@brief 保存一个完整声明自然日的统计视图。"""

    calendar_date: date
    calendar_class: str
    activity_ids: tuple[str, ...]
    slot_categories: tuple[str, ...]
    event_count: int
    channels: tuple[str, ...]


@dataclass(frozen=True)
class DatasetView:
    """@brief 保存一个候选的认证身份和共同三层视图。"""

    source_kind: str
    dataset_id: str
    run_id: str
    window: TimeWindow
    events: tuple[EvidenceEvent, ...]
    activities: tuple[ActivitySession, ...]
    days: tuple[DayRecord, ...]
    artifacts: Mapping[str, ArtifactIdentity]
    validation: Mapping[str, Any] = field(default_factory=dict)
    public_evidence: tuple[PublicEvidence, ...] = ()

    def mapping_report(self) -> JsonObject:
        """@brief 返回事件守恒和类别覆盖报告。"""
        unknown = sum(activity.category == "unknown" for activity in self.activities)
        channels = sorted({event.channel for event in self.events})
        categories = sorted({activity.category for activity in self.activities})
        return {
            "source_kind": self.source_kind,
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "input_events": self.validation.get("input_events"),
            "mapped_events": len(self.events),
            "dropped_events": 0,
            "activities": len(self.activities),
            "unknown_activities": unknown,
            "public_evidence": len(self.public_evidence),
            "channels": channels,
            "categories": categories,
            "declared_days": len(self.window.days),
            "mapped_days": len(self.days),
        }


@dataclass(frozen=True)
class SharedProfile:
    """@brief 保存双方确实共享的粗粒度人物画像。"""

    person_count: int
    occupation_class: str
    timezone: str

    def to_dict(self) -> JsonObject:
        """@brief 返回盲评可见的固定画像。"""
        return {
            "person_count": self.person_count,
            "occupation_class": self.occupation_class,
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class EvaluationScenario:
    """@brief 保存评估窗口、映射、抽样和外部参照。"""

    scenario_id: str
    schema_version: str
    window: TimeWindow
    bootstrap_seed: int
    bootstrap_repetitions: int
    block_days: int
    review_seed: str
    reviewer_count: int
    review_dimensions: tuple[str, ...]
    shared_profile: SharedProfile
    special_dates: Mapping[str, str]
    labelkit_class_map: Mapping[str, str]
    labelkit_pattern_map: Mapping[str, str]
    worldmock_activity_map: Mapping[str, str]
    official_domain_map: Mapping[str, tuple[str, ...]]
    official_reference: Mapping[str, Any]
    expected_labelkit: Mapping[str, Any]

    def calendar_class(self, value: date) -> str:
        """@brief 返回冻结的日历类别。"""
        explicit = self.special_dates.get(value.isoformat())
        if explicit:
            return explicit
        return "ordinary_weekend" if value.weekday() >= 5 else "regular_workday"


@dataclass(frozen=True)
class CausalResult:
    """@brief 保存生产检查器的因果门槛结果。"""

    findings: tuple[JsonObject, ...]
    hard_errors: tuple[JsonObject, ...]
    passed: bool

    def to_dict(self) -> JsonObject:
        """@brief 返回可序列化的因果结果。"""
        return {
            "passed": self.passed,
            "finding_count": len(self.findings),
            "hard_error_count": len(self.hard_errors),
            "findings": list(self.findings),
        }
