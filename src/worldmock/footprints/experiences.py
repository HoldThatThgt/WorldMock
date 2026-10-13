"""@brief 从已提交生活事件投影可追溯的本人经历，不接收模型文字。"""

from __future__ import annotations

from typing import get_args

import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from worldmock.errors import IntegrityError
from worldmock.exchange.types import GoodsCategory
from worldmock.footprints.models import Event
from worldmock.footprints.app_usage import validate_activity_prefix

if TYPE_CHECKING:
    from worldmock.footprints.store import FootprintStore

_SOURCE_TYPES = frozenset({"activity_progress", "environment_event"})
_PAGE_SIZE = 32
_COMMON_FIELDS = frozenset({"kind", "agent_id", "activity_id", "source_event_id", "event_seq", "occurred_at"})
_KIND_FIELDS = {
    "received": frozenset({"order_id", "goods_id", "category", "quantity"}),
    "arrived": frozenset({"location_id", "access_status"}),
    "played": frozenset({"content_id", "app_id", "position_before_seconds", "position_after_seconds",
                         "interval_start", "interval_end"}),
}


def validate_verified_experiences(rows: Any, agent_id: int, current_time: int) -> list[dict[str, Any]]:
    """@brief 严格验证人物观察的五条规范经历和种类载荷。"""
    if (not isinstance(rows, list) or len(rows) > 5 or type(agent_id) is not int or agent_id <= 0
            or type(current_time) is not int or current_time < 0):
        _invalid("Observed experience window is invalid")
    facts = []
    seen = set()
    for row in rows:
        _validate_fact(row, agent_id, current_time)
        identity = (row["source_event_id"], row["kind"])
        if identity in seen:
            _invalid("Observed experience is duplicated")
        seen.add(identity)
        facts.append(dict(row))
    ordering = lambda item: (item["event_seq"], item["kind"])
    if facts != sorted(facts, key=ordering):
        _invalid("Observed experiences are out of event order")
    return facts


def _validate_fact(row: Any, agent_id: int, current_time: int) -> None:
    """@brief 核对一条经历的共同身份、截止时间和封闭载荷。"""
    if not isinstance(row, dict) or row.get("kind") not in _KIND_FIELDS:
        _invalid("Observed experience kind is invalid")
    if set(row) != _COMMON_FIELDS | _KIND_FIELDS[row["kind"]]:
        _invalid("Observed experience fields are invalid")
    if (type(row["agent_id"]) is not int or row["agent_id"] != agent_id
            or type(row["event_seq"]) is not int or row["event_seq"] <= 0
            or type(row["occurred_at"]) is not int or not 0 <= row["occurred_at"] <= current_time
            or not isinstance(row["source_event_id"], str) or not row["source_event_id"]):
        _invalid("Observed experience identity or time is invalid")
    kind = row["kind"]
    if ((kind == "received" and row["activity_id"] is not None)
            or (kind != "received" and (not isinstance(row["activity_id"], str) or not row["activity_id"]))):
        _invalid("Observed experience activity identity is invalid")
    if kind == "played":
        _validate_fact_interval(row, current_time)
    if kind == "received":
        _validate_received_fact(row)
    if kind == "arrived" and (not isinstance(row["location_id"], str) or not row["location_id"]
                               or row["access_status"] not in {"allowed", "denied"}):
        _invalid("Observed arrival is invalid")
    if kind == "played":
        _validate_played_fact(row)


def _validate_fact_interval(row: dict[str, Any], current_time: int) -> None:
    """@brief 校验经历实际区间不会越过本轮观察时刻。"""
    start, end = row["interval_start"], row["interval_end"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= current_time:
        _invalid("Observed experience interval is invalid")


def _validate_received_fact(row: dict[str, Any]) -> None:
    """@brief 校验本人收货的商品、订单与实际取得份数。"""
    if (any(not isinstance(row[key], str) or not row[key] for key in ("order_id", "goods_id"))
            or row["category"] not in set(get_args(GoodsCategory))
            or not _finite_number(row["quantity"]) or row["quantity"] <= 0):
        _invalid("Observed goods receipt evidence is invalid")


def _validate_played_fact(row: dict[str, Any]) -> None:
    """@brief 校验真实播放内容和正向媒体区间。"""
    start, end = row["position_before_seconds"], row["position_after_seconds"]
    if (not isinstance(row["content_id"], str) or not row["content_id"]
            or (row["app_id"] is not None and (not isinstance(row["app_id"], str) or not row["app_id"]))
            or not _finite_number(start) or not _finite_number(end) or not 0 <= start < end):
        _invalid("Observed playback evidence is invalid")


def _finite_number(value: Any) -> bool:
    """@brief 排除布尔值和非有限数值。"""
    return type(value) in (int, float) and math.isfinite(value)


def project_verified_experiences(
    events: Sequence[Event], *, agent_id: int, cutoff_seq: int,
) -> list[dict[str, Any]]:
    """@brief 从认证事件的完整截止前缀投影本人全部规范经历。"""
    if type(agent_id) is not int or agent_id <= 0 or type(cutoff_seq) is not int or cutoff_seq < 0:
        _invalid("Experience projection boundary is invalid")
    validate_activity_prefix([event.model_dump(mode="json") for event in events])
    facts: list[dict[str, Any]] = []
    seen: dict[str, Event] = {}
    previous_seq = 0
    for event in events:
        if event.event_seq < previous_seq or event.event_seq > cutoff_seq:
            _invalid("Experience events are outside the ordered cutoff")
        previous_seq = event.event_seq
        prior = seen.get(event.event_id)
        if prior is not None:
            if prior != event:
                _invalid("Experience source identity conflicts with a prior event")
            continue
        seen[event.event_id] = event
        if event.agent_id != agent_id or event.event_type not in _SOURCE_TYPES:
            continue
        if event.event_type == "environment_event" and event.data.get("kind") != "goods_received":
            continue
        if event.source_kind != "environment" or event.visibility != "agent":
            _invalid("Experience source has invalid provenance or visibility")
        facts.extend(_project_event(event))
    return facts


def recent_verified_experiences(store: FootprintStore, agent_id: int, cutoff_seq: int) -> list[dict[str, Any]]:
    """@brief 通过本人倒序索引分页取最近五条事实，避免观察扫描全历史。"""
    if type(cutoff_seq) is not int or cutoff_seq < 0 or cutoff_seq > store.last_event_seq:
        _invalid("Recent experience cutoff is outside the committed sequence")
    before = cutoff_seq + 1
    facts: list[dict[str, Any]] = []
    while before > 1 and len(facts) < 5:
        page = store.read_recent_experience_sources(agent_id, before, _PAGE_SIZE)
        if not page:
            break
        facts.extend(project_verified_experiences(
            sorted(page, key=lambda item: item.event_seq), agent_id=agent_id, cutoff_seq=cutoff_seq,
        ))
        before = page[-1].event_seq
    facts.sort(key=lambda item: (item["event_seq"], item["kind"]))
    return facts[-5:]


def _project_event(event: Event) -> list[dict[str, Any]]:
    """@brief 按结算事件种类分派唯一领域投影。"""
    data = event.data
    if event.event_type == "environment_event":
        return [_received(event)]
    activity = data.get("activity")
    if not isinstance(activity, dict):
        _invalid("Activity progress has no activity snapshot")
    kind = activity.get("kind")
    if kind == "interact":
        row = _played(event, activity)
        return [row] if row is not None else []
    if kind == "travel":
        row = _arrival(event, activity)
        return [row] if row is not None else []
    return []


def _base(event: Event, kind: str) -> dict[str, Any]:
    """@brief 保留规范经历共同的来源身份与全序。"""
    if kind != "received" and (not isinstance(event.activity_id, str) or not event.activity_id):
        _invalid("Experience source has no activity identity")
    return {"kind": kind, "agent_id": event.agent_id, "activity_id": event.activity_id,
            "source_event_id": event.event_id, "event_seq": event.event_seq,
            "occurred_at": event.occurred_at}


def _interval(event: Event) -> tuple[int, int]:
    """@brief 严格取已发生的正时长区间。"""
    start, end = event.data.get("interval_start"), event.data.get("interval_end")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        _invalid("Experience interval is invalid")
    return start, end


def _received(event: Event) -> dict[str, Any]:
    """@brief 收货只取本人 goods_received，订单状态和付款不能代替收货事实。"""
    row = {**_base(event, "received"), **{key: event.data.get(key)
            for key in ("order_id", "goods_id", "category", "quantity")}}
    if event.activity_id is not None:
        _invalid("Goods receipt source has an activity identity")
    _validate_received_fact(row)
    return row


def _played(event: Event, activity: dict[str, Any]) -> dict[str, Any] | None:
    """@brief 仅以真实前进的媒体位置证明内容接触。"""
    del activity
    content = event.data.get("content")
    if (not isinstance(content, dict) or "app_id" not in content
            or not isinstance(content.get("content_id"), str) or not content["content_id"]
            or (content.get("app_id") is not None and
                (not isinstance(content["app_id"], str) or not content["app_id"]))):
        _invalid("Playback progress has no app content evidence")
    start, end = content.get("position_before_seconds"), content.get("position_after_seconds")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in (start, end)):
        _invalid("Playback position is invalid")
    if end < start:
        _invalid("Playback position moved backwards")
    if end == start:
        return None
    interval_start, interval_end = _interval(event)
    return {**_base(event, "played"), "content_id": content["content_id"], "app_id": content["app_id"],
            "position_before_seconds": start, "position_after_seconds": end,
            "interval_start": interval_start, "interval_end": interval_end}


def _arrival(event: Event, activity: dict[str, Any]) -> dict[str, Any] | None:
    """@brief 路线进度终态携实际到达位置才形成到达事实。

    快照 ``location_id`` 是出发地，到达地点只取 ``arrival.location_id``。
    """
    status = activity.get("status")
    if status not in {"completed", "failed"}:
        return None
    detail = activity.get("data")
    arrival = detail.get("arrival") if isinstance(detail, dict) else None
    if not isinstance(arrival, dict):
        if status == "completed":
            _invalid("Completed travel has no arrival evidence")
        return None
    location_id = arrival.get("location_id")
    if (not isinstance(location_id, str) or not location_id
            or location_id != activity.get("destination_id")
            or arrival.get("access_status") not in {"allowed", "denied"}):
        _invalid("Travel arrival location or access state is invalid")
    return {**_base(event, "arrived"), "location_id": location_id,
            "access_status": arrival["access_status"]}


def _invalid(message: str) -> None:
    """@brief 记录投影数据错误并阻止生成猜测事实。"""
    logging.error("Verified experience projection failed: %s", message)
    raise IntegrityError(message)
