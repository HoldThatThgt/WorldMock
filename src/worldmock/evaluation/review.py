"""@brief 生成隔离盲评包，校验评审收据并在最后解盲。"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import krippendorff
import numpy as np

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import atomic_write_json, read_json, sha256_file, stable_json_bytes
from worldmock.evaluation.models import (
    ActivitySession,
    DatasetView,
    EvaluationScenario,
    JsonObject,
    PublicEvidence,
)

_CHOICES = {"left", "right", "tie", "unjudgeable"}
_CANDIDATE_CODES = {"left_candidate": 0.0, "right_candidate": 1.0, "tie": 2.0}
_PUBLIC_FIELDS = {
    "interaction", "object_type", "state", "object_ref",
    "fulfillment_mode", "travel_mode", "place_type",
}
_PUBLIC_VALUES = {
    "interaction": {
        "order", "travel", "check_in", "check_out", "open", "close", "view", "copy",
        "notify", "arrive", "leave", "visit", "read", "work", "submit", "pay", "deliver",
        "play", "browse", "consume", "report", "pause", "conclude",
        "deadline", "chat", "call",
    },
    "object_type": {"order", "route", "workplace", "application", "content", "message", "task", "game",
                    "product", "activity", "person"},
    "state": {
        "declared", "requested", "started", "completed", "received", "read", "pending", "ready",
        "paid", "delivered", "available", "expired", "rejected", "cancelled", "failed", "unknown",
        "observed", "viewed", "accepted", "paused", "in_progress", "reached",
    },
    "fulfillment_mode": {"delivery", "pickup"},
    "travel_mode": {"walking", "driving", "transit"},
    "place_type": {"workplace"},
}
_LONG_TERM_OBJECT_TYPES = {"order", "task", "message", "game"}
_CAUSAL_STATES = {
    "order": {"requested", "pending", "ready", "paid", "delivered", "cancelled", "failed"},
    "task": {
        "available", "requested", "started", "completed", "accepted", "rejected", "paid",
        "reached", "cancelled", "failed",
    },
    "message": {"received", "requested", "started", "read", "completed", "failed"},
    "game": {"requested", "started", "in_progress", "completed", "cancelled", "failed"},
    "activity": {"requested", "started", "paused", "completed", "cancelled", "failed"},
    "route": {"requested", "started", "completed", "cancelled", "failed"},
    "product": {"requested", "started", "completed", "cancelled", "failed"},
    "content": {"requested", "started", "viewed", "completed", "cancelled", "failed"},
}
_JUDGEABILITY_REASONS = {
    "both_sides_have_observed_activity_timing",
    "both_sides_have_observed_object_state_changes",
    "coarse_shared_profile_and_both_activity_timelines",
    "both_sides_have_cross_day_object_state_changes",
    "multi_day_activity_coverage_on_both_sides",
    "complete_declared_days_and_calendar_classes",
    "comparable_public_evidence_not_observed_on_both_sides",
}


@dataclass(frozen=True)
class ReviewMaterial:
    """@brief 保存公开索引、隔离包和独立解盲键。"""

    index: JsonObject
    packets: tuple[JsonObject, ...]
    key: JsonObject


@dataclass(frozen=True)
class _ReviewContext:
    """@brief 聚合一个盲评包所需的候选与场景。"""

    left: DatasetView
    right: DatasetView
    support: Mapping[str, Any]
    scenario: EvaluationScenario


@dataclass(frozen=True)
class _EvidenceMask:
    """@brief 保存一个评审槽内真实候选的匿名对象命名空间。"""

    reviewer_slot: str
    candidate_namespace: str
    seed: str
    fields_by_category: Mapping[str, Sequence[str]]


def create_review_material(
    left: DatasetView,
    right: DatasetView,
    support: Mapping[str, Any],
    scenario: EvaluationScenario,
) -> ReviewMaterial:
    """@brief 为每个评审槽生成独立且确定性左右随机的盲评包。"""
    items = _review_items(scenario)
    pack_id = _pack_id(scenario, items, support)
    packets: list[JsonObject] = []
    key_items: dict[str, JsonObject] = {}
    context = _ReviewContext(left, right, support, scenario)
    for index in range(scenario.reviewer_count):
        slot = f"reviewer-{index + 1:02d}"
        packet, mapping = _reviewer_packet(slot, pack_id, items, context)
        packets.append(packet)
        key_items[slot] = mapping
    descriptors = [_packet_descriptor(packet) for packet in packets]
    public_index = {
        "schema_version": "blind-review-index-1",
        "pack_id": pack_id,
        "reviewer_count": scenario.reviewer_count,
        "dimensions": list(scenario.review_dimensions),
        "shared_profile": scenario.shared_profile.to_dict(),
        "public_fields_by_category": support.get("public_fields_by_category", {}),
        "selection_coverage": _selection_coverage(scenario, items),
        "packets": descriptors,
    }
    key = {
        "schema_version": "blind-review-key-1",
        "pack_id": pack_id,
        "candidate_labels": ["left_candidate", "right_candidate"],
        "reviewer_mappings": key_items,
    }
    return ReviewMaterial(public_index, tuple(packets), key)


def write_review_material(root: Path, material: ReviewMaterial) -> JsonObject:
    """@brief 将索引、隔离评审包和解盲键写入不同路径。"""
    atomic_write_json(root / "blind-review-pack.json", material.index)
    atomic_write_json(root / "unblinding-key.json", material.key)
    packet_files: list[JsonObject] = []
    for descriptor, packet in zip(material.index["packets"], material.packets):
        path = root / "review-packets" / f"{descriptor['reviewer_slot']}.json"
        atomic_write_json(path, packet)
        packet_files.append({**descriptor, "actual_sha256": sha256_file(path)})
    if any(item["sha256"] != item["actual_sha256"] for item in packet_files):
        raise EvaluationError("write_failed", "written blind review packet hash changed")
    return {"index": material.index, "packets": packet_files}


def ingest_reviews(
    review_paths: Sequence[Path], prepared_root: Path, scenario: EvaluationScenario
) -> JsonObject:
    """@brief 校验全部隔离评审收据，解盲后计算偏好和一致性。"""
    index = read_json(prepared_root / "blind-review-pack.json")
    key = read_json(prepared_root / "unblinding-key.json")
    _validate_review_identity(index, key, scenario)
    descriptors = _verify_packets(prepared_root, index, key, scenario)
    records = _read_review_records(review_paths, descriptors, index, scenario)
    decoded = [_decode_record(record, key, scenario) for record in records]
    return {
        "schema_version": "review-results-1",
        "pack_id": index["pack_id"],
        "selection_coverage": index["selection_coverage"],
        "records": records,
        "decoded_ratings": decoded,
        "preference": _preference_summary(decoded, scenario),
        "reliability": _reliability(decoded, scenario),
        "reviewer_dependence": _reviewer_dependence(records),
    }


def _review_items(scenario: EvaluationScenario) -> list[JsonObject]:
    """@brief 覆盖每个周块、特殊日期及每周哈希选择的普通日。"""
    blocks = _date_blocks(scenario)
    items: list[JsonObject] = []
    for block in blocks:
        block_start = block[0]
        items.append(_item("week", block, block_start))
        specials = [value for value in block if value.isoformat() in scenario.special_dates]
        items.extend(_item("special_day", (value,), block_start) for value in specials)
        for calendar_class in ("regular_workday", "ordinary_weekend"):
            candidates = [value for value in block if scenario.calendar_class(value) == calendar_class]
            if candidates:
                chosen = _hashed_choice(candidates, scenario.review_seed, block_start, calendar_class)
                items.append(_item(calendar_class, (chosen,), block_start))
    return items


def _date_blocks(scenario: EvaluationScenario) -> list[tuple[date, ...]]:
    """@brief 将完整声明窗口切成连续自然日块。"""
    days = scenario.window.days
    size = scenario.block_days
    return [tuple(days[index:index + size]) for index in range(0, len(days), size)]


def _selection_coverage(
    scenario: EvaluationScenario, items: Sequence[JsonObject]
) -> JsonObject:
    """@brief 明确记录没有普通工作日或周末可选的周块。"""
    scopes = ("regular_workday", "ordinary_weekend")
    selected = {(item["block_start"], item["scope"]) for item in items}
    missing = {
        scope: [
            block[0].isoformat()
            for block in _date_blocks(scenario)
            if (block[0].isoformat(), scope) not in selected
        ]
        for scope in scopes
    }
    return {
        "week_blocks": len(_date_blocks(scenario)),
        "special_dates": sum(item["scope"] == "special_day" for item in items),
        "missing_ordinary_samples": missing,
    }


def _item(scope: str, dates: Sequence[date], block_start: date) -> JsonObject:
    """@brief 构造不含候选身份的评审单元描述。"""
    date_text = ",".join(value.isoformat() for value in dates)
    digest = hashlib.sha256(f"{scope}:{date_text}".encode()).hexdigest()[:16]
    return {
        "item_id": f"item-{digest}",
        "scope": scope,
        "dates": [value.isoformat() for value in dates],
        "block_start": block_start.isoformat(),
    }


def _hashed_choice(values: Sequence[date], seed: str, block_start: date, kind: str) -> date:
    """@brief 用冻结种子和日期哈希选择普通自然日。"""
    digest = hashlib.sha256(f"{seed}:{block_start}:{kind}".encode()).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


def _pack_id(
    scenario: EvaluationScenario, items: Sequence[JsonObject], support: Mapping[str, Any]
) -> str:
    """@brief 从公开评审设计派生稳定包身份。"""
    public = {
        "window_start": scenario.window.start.isoformat(),
        "window_end": scenario.window.end.isoformat(),
        "dimensions": scenario.review_dimensions,
        "shared_profile": scenario.shared_profile.to_dict(),
        "public_fields_by_category": support.get("public_fields_by_category", {}),
        "items": list(items),
    }
    return hashlib.sha256(stable_json_bytes(public)).hexdigest()


def _reviewer_packet(
    slot: str,
    pack_id: str,
    items: Sequence[JsonObject],
    context: _ReviewContext,
) -> tuple[JsonObject, JsonObject]:
    """@brief 生成一个隔离评审槽的材料及其私有左右映射。"""
    packet_items: list[JsonObject] = []
    mappings: dict[str, JsonObject] = {}
    for item in items:
        swap = _swap_sides(context.scenario.review_seed, slot, item["item_id"])
        first, second = (context.right, context.left) if swap else (context.left, context.right)
        namespaces = (
            "right_candidate" if swap else "left_candidate",
            "left_candidate" if swap else "right_candidate",
        )
        packet_items.append(_packet_item(item, (first, second), namespaces, slot, context))
        mappings[item["item_id"]] = {
            "left": "right_candidate" if swap else "left_candidate",
            "right": "left_candidate" if swap else "right_candidate",
            "block_start": item["block_start"],
        }
    return _packet_document(slot, pack_id, packet_items, context), mappings


def _swap_sides(seed: str, slot: str, item_id: str) -> bool:
    """@brief 为每个评审槽和单元确定性随机左右顺序。"""
    digest = hashlib.sha256(f"{seed}:{slot}:{item_id}".encode()).digest()
    return bool(digest[0] & 1)


def _packet_item(
    item: JsonObject,
    views: tuple[DatasetView, DatasetView],
    namespaces: tuple[str, str],
    slot: str,
    context: _ReviewContext,
) -> JsonObject:
    """@brief 构造活动区间、公开事实和六维可判定性。"""
    dates = tuple(date.fromisoformat(value) for value in item["dates"])
    public = {key: value for key, value in item.items() if key != "block_start"}
    public["calendar_classes"] = [context.scenario.calendar_class(value) for value in dates]
    sides = []
    for view, namespace in zip(views, namespaces, strict=True):
        sides.append(_candidate_timeline(view, dates, slot, namespace, context))
    public["left"], public["right"] = sides
    public["judgeability"] = _judgeability(public, context.scenario.review_dimensions)
    return public


def _candidate_timeline(
    view: DatasetView,
    dates: Sequence[date],
    slot: str,
    namespace: str,
    context: _ReviewContext,
) -> JsonObject:
    """@brief 生成一侧的活动区间和匿名公开结构事实。"""
    mask = _EvidenceMask(
        slot, namespace, context.scenario.review_seed,
        context.support.get("public_fields_by_category", {}),
    )
    return {
        "activities": _timeline(view, dates, context.support, mask),
        "public_evidence": _public_evidence_rows(view, dates, mask),
    }


def _timeline(
    view: DatasetView, dates: Sequence[date], support: Mapping[str, Any], mask: _EvidenceMask
) -> list[JsonObject]:
    """@brief 输出类别、起止时间、地点、同伴与规范化状态，不输出来源或内部身份。"""
    categories = set(support["shared_categories"])
    channels = set(support["shared_channels"])
    selected = set(dates)
    rows: list[JsonObject] = []
    for activity in view.activities:
        if activity.category not in categories or not set(activity.channels) & channels:
            continue
        rows.extend(_timeline_segments(view, activity, selected, mask))
    return sorted(rows, key=lambda item: (item["start"], item["end"], item["category"]))


def _timeline_segments(
    view: DatasetView, activity: ActivitySession, selected: set[date], mask: _EvidenceMask
) -> list[JsonObject]:
    """@brief 将一个会话的真实运行区间裁到评审日期。"""
    zone = ZoneInfo(view.window.timezone)
    segments = (
        activity.segments
        if activity.segments is not None
        else ((activity.started_at, activity.ended_at),)
    )
    rows: list[JsonObject] = []
    for value in sorted(selected):
        day_start = datetime.combine(value, time.min, zone)
        day_end = day_start + timedelta(days=1)
        for start, end in segments:
            clipped_start, clipped_end = max(start, day_start), min(end, day_end)
            if clipped_end > clipped_start:
                rows.append(_timeline_row(activity, clipped_start, clipped_end, zone, mask))
    return rows


def _timeline_row(
    activity: ActivitySession, start: datetime, end: datetime, zone: ZoneInfo,
    mask: _EvidenceMask,
) -> JsonObject:
    """@brief 规范化一个可见活动区间，同伴以 keyed HMAC 匿名对象引用输出。

    地点与同伴两列为docs/dev/SPEC-life-runtime.md「社交最小可行版」、docs/dev/SPEC-life-realism.md「呈现与导出」新增：location_id 适配层尚未
    填充时为 null；partner_id 只以评审槽+候选命名空间隔离的匿名引用出现，
    原始人物身份不进盲评包。
    """
    return {
        "category": activity.category,
        "start": start.astimezone(zone).isoformat(),
        "end": end.astimezone(zone).isoformat(),
        "status": _public_status(activity.status),
        "location": getattr(activity, "location_id", None),
        "companion": _companion_ref(activity, mask),
    }


def _companion_ref(activity: ActivitySession, mask: _EvidenceMask) -> str | None:
    """@brief 同伴匿名引用：无同伴为 null，有则与对象引用同一 HMAC 机制。"""
    partner = getattr(activity, "partner_id", None)
    return _masked_ref(mask, "person", partner) if partner else None


def _public_evidence_rows(
    view: DatasetView, dates: Sequence[date], mask: _EvidenceMask
) -> list[JsonObject]:
    """@brief 输出会话内状态变化粒度的共同字段，并剥离原始身份。"""
    selected = set(dates)
    rows = []
    previous: dict[tuple[str, str, str, str], tuple[Any, ...]] = {}
    for evidence in view.public_evidence:
        local = view.window.localize(evidence.occurred_at)
        fields = mask.fields_by_category.get(evidence.category)
        if local.date() not in selected or not fields:
            continue
        group, signature = _evidence_change_identity(evidence)
        if previous.get(group) == signature:
            continue
        previous[group] = signature
        rows.append(_public_evidence_row(evidence, local, fields, mask))
    return sorted(rows, key=lambda item: (item["at"], item["category"], item["interaction"]))


def _evidence_change_identity(
    evidence: PublicEvidence,
) -> tuple[tuple[str, str, str, str], tuple[Any, ...]]:
    """@brief 仅在同一真实会话和对象语义内折叠重复状态。"""
    session = evidence.session_key or "\0".join(evidence.source_event_ids)
    group = (session, evidence.category, evidence.object_type, evidence.object_key or "")
    signature = (
        evidence.interaction,
        evidence.state,
        tuple(sorted(evidence.attributes.items())),
    )
    return group, signature


def _public_evidence_row(
    evidence: PublicEvidence,
    local: datetime,
    fields: Sequence[str],
    mask: _EvidenceMask,
) -> JsonObject:
    """@brief 生成一条带缺失值和 keyed HMAC 引用的公开事实。"""
    values = evidence.field_values()
    row: JsonObject = {"at": local.isoformat(), "category": evidence.category}
    for field in fields:
        if field == "object_ref":
            row[field] = _object_ref(evidence, mask) if evidence.object_key else None
        else:
            row[field] = values.get(field)
    return row


def _masked_ref(mask: _EvidenceMask, object_type: str, object_key: str) -> str:
    """@brief keyed HMAC 匿名引用：候选与评审槽隔离、同侧跨日稳定。"""
    key = hmac.new(mask.seed.encode(), f"reviewer:{mask.reviewer_slot}".encode(), hashlib.sha256).digest()
    message = "\0".join((mask.candidate_namespace, object_type, object_key))
    return "object-" + hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def _object_ref(evidence: PublicEvidence, mask: _EvidenceMask) -> str:
    """@brief 生成候选和评审会话隔离、同侧跨日稳定的对象引用。"""
    return _masked_ref(mask, evidence.object_type, evidence.object_key or "")


def _judgeability(item: JsonObject, dimensions: Sequence[str]) -> JsonObject:
    """@brief 依据两侧同等可观察证据声明每一评审维度的可判定性。"""
    left, right = item["left"], item["right"]
    multiple_days = len(item["dates"]) > 1
    activities = bool(left["activities"]) and bool(right["activities"])
    causal = _has_state_chain(left["public_evidence"], False)
    causal = causal and _has_state_chain(right["public_evidence"], False)
    long_term = multiple_days and _has_state_chain(left["public_evidence"], True)
    long_term = long_term and _has_state_chain(right["public_evidence"], True)
    repeated = multiple_days and activities
    rules = {
        "temporal_plausibility": (activities, "both_sides_have_observed_activity_timing"),
        "causal_continuity": (causal, "both_sides_have_observed_object_state_changes"),
        "persona_consistency": (activities, "coarse_shared_profile_and_both_activity_timelines"),
        "long_term_consequences": (long_term, "both_sides_have_cross_day_object_state_changes"),
        "routine_and_variation": (repeated, "multi_day_activity_coverage_on_both_sides"),
        "missing_expected_activity": (True, "complete_declared_days_and_calendar_classes"),
    }
    return {name: _judgeability_value(*rules[name]) for name in dimensions}


def _judgeability_value(judgeable: bool, success_reason: str) -> JsonObject:
    """@brief 返回不偏向任一候选的可观察性说明。"""
    reason = success_reason if judgeable else "comparable_public_evidence_not_observed_on_both_sides"
    return {"judgeable": judgeable, "reason": reason}


def _has_state_chain(rows: Sequence[JsonObject], cross_day: bool) -> bool:
    """@brief 要求同一匿名对象出现真实不同状态，跨日时还需覆盖两日。"""
    states: dict[str, set[str]] = defaultdict(set)
    dates: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        reference, state = row.get("object_ref"), row.get("state")
        object_type = row.get("object_type")
        if state not in _CAUSAL_STATES.get(str(object_type), set()):
            continue
        if cross_day and object_type not in _LONG_TERM_OBJECT_TYPES:
            continue
        if isinstance(reference, str) and isinstance(state, str):
            states[reference].add(state)
            dates[reference].add(str(row.get("at", ""))[:10])
    return any(len(values) > 1 and (not cross_day or len(dates[key]) > 1)
               for key, values in states.items())


def _public_status(value: str) -> str:
    """@brief 将来源特有状态收敛为共同公开状态。"""
    if value in {"cancelled", "failed", "truncated"}:
        return "interrupted"
    if value in {"active", "paused"}:
        return "ongoing"
    return "recorded"


def _packet_document(
    slot: str, pack_id: str, items: list[JsonObject], context: _ReviewContext
) -> JsonObject:
    """@brief 组装单个评审只能独立读取的完整包。"""
    return {
        "schema_version": "blind-review-packet-1",
        "pack_id": pack_id,
        "reviewer_slot": slot,
        "dimensions": list(context.scenario.review_dimensions),
        "shared_profile": context.scenario.shared_profile.to_dict(),
        "public_fields_by_category": context.support.get("public_fields_by_category", {}),
        "instructions_zh": [
            "只依据本包中的活动区间和公开结构证据，逐项判断哪一侧更贴近连贯的人类活动。",
            "每个维度必须选择 left、right、tie 或 unjudgeable，并给出简短依据。",
            "judgeable=false 的维度必须选择 unjudgeable；单侧证据更丰富不代表更真实。",
            "共享人物只证明单人、白领和时区。不能据此推测偏好、性格、收入或身体状态。",
            "不要推测生成系统、模型、文件或运行身份。",
        ],
        "items": items,
    }


def _packet_descriptor(packet: JsonObject) -> JsonObject:
    """@brief 返回公开索引中的隔离包路径和内容哈希。"""
    slot = packet["reviewer_slot"]
    return {
        "reviewer_slot": slot,
        "sha256": hashlib.sha256(stable_json_bytes(packet)).hexdigest(),
        "item_count": len(packet["items"]),
    }


def _validate_review_identity(
    index: JsonObject, key: JsonObject, scenario: EvaluationScenario
) -> None:
    """@brief 验证索引、解盲键和场景的共同身份。"""
    if index.get("schema_version") != "blind-review-index-1":
        raise EvaluationError("invalid_review", "blind review index schema is invalid")
    if key.get("schema_version") != "blind-review-key-1" or key.get("pack_id") != index.get("pack_id"):
        raise EvaluationError("invalid_review", "unblinding key does not match review pack")
    if index.get("dimensions") != list(scenario.review_dimensions):
        raise EvaluationError("invalid_review", "review dimensions do not match scenario")
    if index.get("shared_profile") != scenario.shared_profile.to_dict():
        raise EvaluationError("invalid_review", "review shared profile does not match scenario")
    _validate_public_fields(index.get("public_fields_by_category"))
    if index.get("selection_coverage") != _selection_coverage(scenario, _review_items(scenario)):
        raise EvaluationError("invalid_review", "review selection coverage does not match scenario")
    packets = index.get("packets")
    if not isinstance(packets, list) or len(packets) != scenario.reviewer_count:
        raise EvaluationError("invalid_review", "review packet count does not match scenario")


def _verify_packets(
    root: Path,
    index: JsonObject,
    key: JsonObject,
    scenario: EvaluationScenario,
) -> list[JsonObject]:
    """@brief 复核每个隔离包的哈希、遮蔽字段和解盲映射覆盖。"""
    verified: list[JsonObject] = []
    for descriptor in index["packets"]:
        path = _packet_path(root, descriptor)
        if sha256_file(path) != descriptor.get("sha256"):
            raise EvaluationError("invalid_review", "blind review packet hash is invalid")
        packet = read_json(path)
        judgeability = _validate_packet(packet, descriptor, index, scenario)
        item_ids = list(judgeability)
        _validate_key_items(key, descriptor["reviewer_slot"], item_ids)
        verified.append({**descriptor, "item_ids": item_ids, "judgeability": judgeability})
    return verified


def _packet_path(root: Path, descriptor: Mapping[str, Any]) -> Path:
    """@brief 将公开索引中的隔离包路径约束在准备目录。"""
    slot = descriptor.get("reviewer_slot")
    if not isinstance(slot, str) or not slot.startswith("reviewer-"):
        raise EvaluationError("invalid_review", "blind review packet slot is invalid")
    return (root / "review-packets" / f"{slot}.json").resolve()


def _validate_packet(
    packet: JsonObject,
    descriptor: Mapping[str, Any],
    index: JsonObject,
    scenario: EvaluationScenario,
) -> dict[str, dict[str, bool]]:
    """@brief 验证公开包身份、闭集字段和时间线遮蔽。"""
    required = {
        "schema_version", "pack_id", "reviewer_slot", "dimensions", "shared_profile",
        "public_fields_by_category", "instructions_zh", "items",
    }
    if set(packet) != required or packet.get("schema_version") != "blind-review-packet-1":
        raise EvaluationError("invalid_review", "blind review packet schema is invalid")
    if packet.get("pack_id") != index["pack_id"] or packet.get("reviewer_slot") != descriptor["reviewer_slot"]:
        raise EvaluationError("invalid_review", "blind review packet identity is invalid")
    if packet.get("dimensions") != list(scenario.review_dimensions):
        raise EvaluationError("invalid_review", "blind review packet dimensions are invalid")
    if packet.get("shared_profile") != index.get("shared_profile"):
        raise EvaluationError("invalid_review", "blind review packet shared profile is invalid")
    if packet.get("public_fields_by_category") != index.get("public_fields_by_category"):
        raise EvaluationError("invalid_review", "blind review packet public field support is invalid")
    items = packet.get("items")
    if not isinstance(items, list) or len(items) != descriptor.get("item_count"):
        raise EvaluationError("invalid_review", "blind review packet item count is invalid")
    _reject_identity_keys(packet)
    validated = [
        _validate_packet_item(item, scenario.review_dimensions, packet["public_fields_by_category"])
        for item in items
    ]
    item_ids = [identity for identity, _ in validated]
    if len(item_ids) != len(set(item_ids)):
        raise EvaluationError("invalid_review", "blind review packet item IDs repeat")
    return {identity: values for identity, values in validated}


def _reject_identity_keys(value: Any) -> None:
    """@brief 递归拒绝会暴露候选来源或解盲关系的字段。"""
    forbidden = {
        "model", "provider", "path", "run_id", "dataset_id", "source", "source_pointer", "key",
        "object_key", "event_id", "activity_id", "message_id", "thread_id", "task_id", "product_id",
        "order_id", "route_id", "person_id", "agent_id", "decision_id", "action_id", "memory", "body",
        "caption", "content", "subject", "raw_text",
    }
    if isinstance(value, dict):
        if set(value) & forbidden:
            raise EvaluationError("invalid_review", "blind review packet exposes source identity")
        for item in value.values():
            _reject_identity_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_identity_keys(item)


def _validate_packet_item(
    value: Any, dimensions: Sequence[str], public_fields: Mapping[str, Any]
) -> tuple[str, dict[str, bool]]:
    """@brief 验证一个盲评单元及其规范时间线。"""
    required = {"item_id", "scope", "dates", "calendar_classes", "left", "right", "judgeability"}
    if not isinstance(value, dict) or set(value) != required:
        raise EvaluationError("invalid_review", "blind review item schema is invalid")
    item_id = value.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        raise EvaluationError("invalid_review", "blind review item ID is invalid")
    for side in ("left", "right"):
        _validate_candidate_timeline(value[side], public_fields)
    judgeability = _validate_judgeability(value.get("judgeability"), dimensions)
    if value["judgeability"] != _judgeability(value, dimensions):
        raise EvaluationError("invalid_review", "blind review judgeability disagrees with evidence")
    return item_id, judgeability


def _validate_candidate_timeline(value: Any, public_fields: Mapping[str, Any]) -> None:
    """@brief 验证一侧活动和公开事实的闭集结构。"""
    if not isinstance(value, dict) or set(value) != {"activities", "public_evidence"}:
        raise EvaluationError("invalid_review", "blind review candidate timeline schema is invalid")
    if not isinstance(value["activities"], list) or not isinstance(value["public_evidence"], list):
        raise EvaluationError("invalid_review", "blind review candidate collections must be arrays")
    for row in value["activities"]:
        if (not isinstance(row, dict)
                or set(row) != {"category", "start", "end", "status", "location", "companion"}
                or not _valid_location(row["location"])
                or not _valid_anonymous_ref(row["companion"])):
            raise EvaluationError("invalid_review", "blind review activity timeline schema is invalid")
    for row in value["public_evidence"]:
        _validate_public_row(row, public_fields)


def _validate_public_fields(value: Any) -> None:
    """@brief 验证类别字段掩码只包含允许公开的闭集字段。"""
    if not isinstance(value, dict):
        raise EvaluationError("invalid_review", "public field support must be an object")
    for category, fields in value.items():
        valid = isinstance(category, str) and category and isinstance(fields, list) and fields
        if not valid or len(fields) != len(set(fields)) or not set(fields) <= _PUBLIC_FIELDS:
            raise EvaluationError("invalid_review", "public field support contains invalid fields")


def _validate_public_row(value: Any, public_fields: Mapping[str, Any]) -> None:
    """@brief 验证一条公开事实完全符合该类别共同字段。"""
    if not isinstance(value, dict) or not isinstance(value.get("category"), str):
        raise EvaluationError("invalid_review", "blind review public evidence schema is invalid")
    fields = public_fields.get(value["category"])
    if not isinstance(fields, list) or set(value) != {"at", "category", *fields}:
        raise EvaluationError("invalid_review", "blind review public evidence fields are invalid")
    if not isinstance(value.get("at"), str):
        raise EvaluationError("invalid_review", "blind review public evidence time is invalid")
    for field in fields:
        _validate_public_value(field, value.get(field))


def _valid_location(value: Any) -> bool:
    """@brief 活动地点列为显式缺失或非空字符串（场景地名不是闭集）。"""
    return value is None or (isinstance(value, str) and bool(value))


def _valid_anonymous_ref(value: Any) -> bool:
    """@brief 同伴列只允许缺失或匿名对象引用形态，拒绝原始人物身份。"""
    return value is None or (isinstance(value, str)
                             and re.fullmatch(r"object-[0-9a-f]{64}", value) is not None)


def _validate_public_value(field: str, value: Any) -> None:
    """@brief 验证匿名引用、显式缺失或闭集公开值。"""
    if value is None:
        return
    if field == "object_ref":
        if not isinstance(value, str) or re.fullmatch(r"object-[0-9a-f]{64}", value) is None:
            raise EvaluationError("invalid_review", "blind review object reference is invalid")
        return
    if value not in _PUBLIC_VALUES[field]:
        raise EvaluationError("invalid_review", "blind review public evidence value is outside the closed set")


def _validate_judgeability(value: Any, dimensions: Sequence[str]) -> dict[str, bool]:
    """@brief 验证六维可判定性和不偏向候选的原因。"""
    if not isinstance(value, dict) or set(value) != set(dimensions):
        raise EvaluationError("invalid_review", "blind review judgeability dimensions are invalid")
    result = {}
    for dimension, descriptor in value.items():
        if not isinstance(descriptor, dict) or set(descriptor) != {"judgeable", "reason"}:
            raise EvaluationError("invalid_review", "blind review judgeability schema is invalid")
        if not isinstance(descriptor["judgeable"], bool) or descriptor["reason"] not in _JUDGEABILITY_REASONS:
            raise EvaluationError("invalid_review", "blind review judgeability value is invalid")
        result[dimension] = descriptor["judgeable"]
    return result


def _validate_key_items(key: JsonObject, slot: str, item_ids: Sequence[str]) -> None:
    """@brief 验证独立解盲键恰好覆盖一个隔离包。"""
    mappings = key.get("reviewer_mappings")
    values = mappings.get(slot) if isinstance(mappings, dict) else None
    if not isinstance(values, dict) or set(values) != set(item_ids):
        raise EvaluationError("invalid_review", "unblinding key item coverage is invalid")
    for mapping in values.values():
        required = {"left", "right", "block_start"}
        if not isinstance(mapping, dict) or set(mapping) != required:
            raise EvaluationError("invalid_review", "unblinding mapping schema is invalid")
        if {mapping["left"], mapping["right"]} != {"left_candidate", "right_candidate"}:
            raise EvaluationError("invalid_review", "unblinding candidate mapping is invalid")


def _read_review_records(
    review_paths: Sequence[Path],
    descriptors: Sequence[JsonObject],
    index: JsonObject,
    scenario: EvaluationScenario,
) -> list[JsonObject]:
    """@brief 从固定评审槽文件读取并校验收据。"""
    if len(review_paths) != len(descriptors):
        raise EvaluationError("invalid_review", "review record count does not match reviewer slots")
    loaded = [read_json(path.resolve()) for path in review_paths]
    by_slot = _records_by_slot(loaded)
    records: list[JsonObject] = []
    sessions: set[str] = set()
    for descriptor in descriptors:
        slot = descriptor["reviewer_slot"]
        record = by_slot.get(slot)
        if record is None:
            raise EvaluationError("invalid_review", "review record is missing for a reviewer slot")
        _validate_record(record, descriptor, index, scenario)
        session = record["reviewer_session_id"]
        if session in sessions:
            raise EvaluationError("invalid_review", "reviewer session IDs must be unique")
        sessions.add(session)
        records.append(record)
    return records


def _records_by_slot(records: Sequence[JsonObject]) -> dict[str, JsonObject]:
    """@brief 按评审槽索引输入文件并拒绝重复槽。"""
    indexed: dict[str, JsonObject] = {}
    for record in records:
        slot = record.get("reviewer_slot")
        if not isinstance(slot, str) or not slot or slot in indexed:
            raise EvaluationError("invalid_review", "review record slots are invalid or duplicated")
        indexed[slot] = record
    return indexed


def _validate_record(
    record: JsonObject,
    descriptor: Mapping[str, Any],
    index: JsonObject,
    scenario: EvaluationScenario,
) -> None:
    """@brief 验证评审身份、原始响应和逐单元逐维度完整性。"""
    required = {
        "schema_version", "pack_id", "reviewer_slot", "reviewer_kind", "reviewer_session_id",
        "model", "prompt_sha256", "packet_sha256", "raw_response", "ratings",
    }
    if set(record) != required or record.get("schema_version") != "review-record-1":
        raise EvaluationError("invalid_review", "review record schema is invalid")
    _validate_record_identity(record, descriptor, index)
    _validate_reviewer(record)
    _validate_ratings(record.get("ratings"), descriptor, scenario)


def _validate_record_identity(
    record: JsonObject, descriptor: Mapping[str, Any], index: JsonObject
) -> None:
    """@brief 验证记录绑定正确隔离包和提示。"""
    if record.get("pack_id") != index.get("pack_id"):
        raise EvaluationError("invalid_review", "review record pack_id is invalid")
    if record.get("reviewer_slot") != descriptor.get("reviewer_slot"):
        raise EvaluationError("invalid_review", "review record slot is invalid")
    if record.get("packet_sha256") != descriptor.get("sha256"):
        raise EvaluationError("invalid_review", "review record packet hash is invalid")
    prompt_hash = record.get("prompt_sha256")
    if not _is_sha256(prompt_hash):
        raise EvaluationError("invalid_review", "review prompt SHA-256 is invalid")
    if not isinstance(record.get("raw_response"), str) or not record["raw_response"].strip():
        raise EvaluationError("invalid_review", "raw review response must be preserved as text")


def _is_sha256(value: Any) -> bool:
    """@brief 判断值是否为规范小写 SHA-256。"""
    alphabet = set("0123456789abcdef")
    return isinstance(value, str) and len(value) == 64 and set(value) <= alphabet


def _validate_reviewer(record: JsonObject) -> None:
    """@brief 验证模型与真人评审元数据不混淆。"""
    kind = record.get("reviewer_kind")
    session = record.get("reviewer_session_id")
    if kind not in {"model", "human"} or not isinstance(session, str) or not session:
        raise EvaluationError("invalid_review", "reviewer identity is invalid")
    model = record.get("model")
    if kind == "model":
        if not isinstance(model, dict) or set(model) != {"provider", "name"}:
            raise EvaluationError("invalid_review", "model reviewer metadata is invalid")
        if not all(isinstance(value, str) and value for value in model.values()):
            raise EvaluationError("invalid_review", "model reviewer metadata is invalid")
    elif model is not None:
        raise EvaluationError("invalid_review", "human reviewer must not declare model metadata")


def _validate_ratings(
    value: Any, descriptor: Mapping[str, Any], scenario: EvaluationScenario
) -> None:
    """@brief 验证每个评审单元的全部维度都有选择和依据。"""
    if not isinstance(value, list) or len(value) != descriptor["item_count"]:
        raise EvaluationError("invalid_review", "review ratings do not cover every item")
    item_ids: set[str] = set()
    for rating in value:
        if not isinstance(rating, dict) or set(rating) != {"item_id", "dimensions"}:
            raise EvaluationError("invalid_review", "review rating schema is invalid")
        item_id = rating["item_id"]
        if not isinstance(item_id, str) or not item_id or item_id in item_ids:
            raise EvaluationError("invalid_review", "review item IDs are invalid")
        if item_id not in descriptor["judgeability"]:
            raise EvaluationError("invalid_review", "review rating references an unknown item")
        item_ids.add(item_id)
        _validate_dimensions(rating["dimensions"], scenario)
        _enforce_judgeability(rating["dimensions"], descriptor["judgeability"][item_id])
    if item_ids != set(descriptor["item_ids"]):
        raise EvaluationError("invalid_review", "review ratings reference the wrong items")


def _validate_dimensions(value: Any, scenario: EvaluationScenario) -> None:
    """@brief 验证评审维度闭集和选择闭集。"""
    if not isinstance(value, dict) or set(value) != set(scenario.review_dimensions):
        raise EvaluationError("invalid_review", "review dimensions are incomplete")
    for decision in value.values():
        if not isinstance(decision, dict) or set(decision) != {"choice", "rationale"}:
            raise EvaluationError("invalid_review", "review dimension decision schema is invalid")
        if decision["choice"] not in _CHOICES:
            raise EvaluationError("invalid_review", "review choice is outside the closed set")
        if not isinstance(decision["rationale"], str) or not decision["rationale"].strip():
            raise EvaluationError("invalid_review", "review rationale must be non-empty")


def _enforce_judgeability(
    decisions: Mapping[str, Any], judgeability: Mapping[str, bool]
) -> None:
    """@brief 不可判定维度只能提交 unjudgeable，避免奖励单侧可见性。"""
    for dimension, allowed in judgeability.items():
        if not allowed and decisions[dimension]["choice"] != "unjudgeable":
            raise EvaluationError(
                "invalid_review", "unjudgeable review dimension must use the unjudgeable choice"
            )


def _decode_record(
    record: JsonObject, key: JsonObject, scenario: EvaluationScenario
) -> JsonObject:
    """@brief 在所有收据齐备后把左右选择解码为候选身份。"""
    mappings = key["reviewer_mappings"][record["reviewer_slot"]]
    decoded: list[JsonObject] = []
    for rating in record["ratings"]:
        mapping = mappings.get(rating["item_id"])
        if not isinstance(mapping, dict):
            raise EvaluationError("invalid_review", "review rating references an unknown item")
        dimensions = {
            name: _decode_choice(decision["choice"], mapping)
            for name, decision in rating["dimensions"].items()
        }
        decoded.append({
            "item_id": rating["item_id"],
            "block_start": mapping["block_start"],
            "dimensions": dimensions,
        })
    return {
        "reviewer_slot": record["reviewer_slot"],
        "reviewer_kind": record["reviewer_kind"],
        "ratings": decoded,
    }


def _decode_choice(choice: str, mapping: Mapping[str, Any]) -> str:
    """@brief 将盲评方位转换为稳定候选或保留中立选择。"""
    return mapping[choice] if choice in {"left", "right"} else choice


def _preference_summary(
    decoded: Sequence[JsonObject], scenario: EvaluationScenario
) -> JsonObject:
    """@brief 分维度汇总偏好、无法判断比例和周块区间。"""
    results: dict[str, JsonObject] = {}
    for dimension in scenario.review_dimensions:
        choices = _dimension_choices(decoded, dimension)
        counts = Counter(choice for _, choice in choices)
        judged = counts["left_candidate"] + counts["right_candidate"] + counts["tie"]
        results[dimension] = {
            "counts": dict(sorted(counts.items())),
            "left_preference_proportion": counts["left_candidate"] / judged if judged else None,
            "right_preference_proportion": counts["right_candidate"] / judged if judged else None,
            "tie_proportion": counts["tie"] / judged if judged else None,
            "unjudgeable_proportion": counts["unjudgeable"] / len(choices) if choices else None,
            "week_block_confidence_interval": _preference_interval(choices, dimension, scenario),
        }
    return {"by_dimension": results}


def _dimension_choices(
    decoded: Sequence[JsonObject], dimension: str
) -> list[tuple[str, str]]:
    """@brief 返回一个维度的周块与解盲选择。"""
    return [
        (rating["block_start"], rating["dimensions"][dimension])
        for record in decoded
        for rating in record["ratings"]
    ]


def _preference_interval(
    choices: Sequence[tuple[str, str]], dimension: str, scenario: EvaluationScenario
) -> JsonObject:
    """@brief 以自然周块重抽样候选偏好差。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for block, choice in choices:
        grouped[block].append(choice)
    block_values = [_preference_difference(values) for _, values in sorted(grouped.items())]
    eligible = [value for value in block_values if value is not None]
    if len(eligible) < 2:
        return {"status": "insufficient_blocks", "eligible_blocks": len(eligible)}
    seed = int.from_bytes(hashlib.sha256(f"{scenario.bootstrap_seed}:{dimension}".encode()).digest()[:8], "big")
    generator = np.random.default_rng(seed)
    data = np.asarray(eligible, dtype=float)
    indices = generator.integers(0, len(data), size=(scenario.bootstrap_repetitions, len(data)))
    means = data[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return {
        "status": "ok", "eligible_blocks": len(eligible), "lower": float(lower),
        "upper": float(upper), "repetitions": scenario.bootstrap_repetitions,
    }


def _preference_difference(values: Sequence[str]) -> float | None:
    """@brief 返回一个周块内左候选减右候选偏好比例。"""
    judged = [value for value in values if value != "unjudgeable"]
    if not judged:
        return None
    left = sum(value == "left_candidate" for value in judged)
    right = sum(value == "right_candidate" for value in judged)
    return (left - right) / len(judged)


def _reliability(decoded: Sequence[JsonObject], scenario: EvaluationScenario) -> JsonObject:
    """@brief 使用成熟 krippendorff 包计算每个维度的 nominal alpha。"""
    return {
        "method": "Krippendorff's alpha, nominal",
        "by_dimension": {
            dimension: _dimension_alpha(decoded, dimension)
            for dimension in scenario.review_dimensions
        },
    }


def _dimension_alpha(decoded: Sequence[JsonObject], dimension: str) -> JsonObject:
    """@brief 将无法判断作为缺失值后计算单维度一致性。"""
    item_ids = sorted({rating["item_id"] for record in decoded for rating in record["ratings"]})
    rows: list[list[float]] = []
    for record in decoded:
        ratings = {item["item_id"]: item["dimensions"][dimension] for item in record["ratings"]}
        rows.append([_choice_code(ratings.get(item_id)) for item_id in item_ids])
    array = np.asarray(rows, dtype=float)
    valid_units = int(
        sum(bool(np.count_nonzero(~np.isnan(array[:, index])) >= 2) for index in range(array.shape[1]))
    )
    values = set(array[~np.isnan(array)].tolist())
    if valid_units < 2 or len(values) < 2:
        return {"status": "not_applicable", "alpha": None, "valid_units": valid_units}
    alpha = float(krippendorff.alpha(reliability_data=array, level_of_measurement="nominal"))
    if not math.isfinite(alpha):
        return {"status": "not_applicable", "alpha": None, "valid_units": valid_units}
    return {"status": "ok", "alpha": alpha, "valid_units": valid_units}


def _choice_code(value: str | None) -> float:
    """@brief 将解盲选择编码为 nominal alpha 输入。"""
    if value is None or value == "unjudgeable":
        return float("nan")
    return _CANDIDATE_CODES[value]


def _reviewer_dependence(records: Sequence[JsonObject]) -> JsonObject:
    """@brief 披露模型会话同源关系，避免把它们称为独立真人。"""
    groups: dict[str, list[str]] = defaultdict(list)
    model_count = 0
    human_count = 0
    for record in records:
        if record["reviewer_kind"] == "model":
            model_count += 1
            identity = f"{record['model']['provider']}:{record['model']['name']}"
            groups[identity].append(record["reviewer_session_id"])
        else:
            human_count += 1
    correlated = {key: value for key, value in sorted(groups.items()) if len(value) > 1}
    return {
        "model_review_count": model_count,
        "human_review_count": human_count,
        "same_model_session_groups": correlated,
        "disclosure": "Model sessions sharing a provider and model may be statistically correlated.",
    }
