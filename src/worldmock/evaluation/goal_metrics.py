"""@brief 从冻结事件重算外部约束与拒绝证据。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo



UNREPORTED_CODE_KEY = "code_sha256_unreported"
#: 真实世界的营业、资金、履约与内容可用性约束；协议和活动控制纠错另计。
EXTERNAL_CONSTRAINT_ERROR_CODES = frozenset({
    "LOCATION_CLOSED", "INSUFFICIENT_FUNDS", "INSUFFICIENT_TRAVEL_FUNDS",
    "DELIVERY_NOT_DUE", "PARCEL_NOT_READY", "CONTENT_EXHAUSTED",
})
_TERMINAL_STATUSES = {"completed", "rejected", "cancelled", "failed"}


def day_plan_metrics(events: list[dict[str, Any]], timezone: str) -> dict[str, Any]:
    """@brief 按代码摘要分组计算外部约束；未知样本不冒充零或成功。

    @param events 已冻结事件字典，可覆盖多个运行、人物和本地日。
    @param timezone 场景冻结的本地时区。
    @return 每个代码摘要的外部约束报告与受阻次数门禁。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        groups[str(event.get("code_sha256") or UNREPORTED_CODE_KEY)].append(event)
    zone = ZoneInfo(timezone)
    return {code: _group_metrics(group, zone) for code, group in sorted(groups.items())}


def _group_metrics(events: list[dict], zone: ZoneInfo) -> dict:
    """@brief 在一个代码组内重算真实动作接受、拒绝和受阻门禁。"""
    ordered = sorted(events, key=_event_order)
    constraints = _external_constraints(ordered)
    value = constraints["block_count"] if constraints["evidence_complete"] else None
    return {
        "external_constraints": constraints,
        "gates": {"external_constraint_block_count": {
            "value": value, "comparison": "==", "threshold": 0, "pass": value == 0,
            "note": "External constraint blocks describe the run, not an engine validation defect"
            if value is not None else "Rejection evidence lacks action_id or error_code",
        }},
    }


def _event_order(event: dict) -> tuple:
    """@brief 使用运行身份和持久顺序稳定回放，事件标识仅用于最终稳定排序。"""
    return (str(event.get("run_id")), event.get("event_seq", 0),
            event.get("occurred_at", 0), str(event.get("event_id")))













def _action_evidence(event: dict) -> tuple[bool, str | None] | None:
    """@brief 只从明确的拒绝或动作终态读取受阻证据。"""
    data = event.get("data", {})
    if data.get("reused"):
        return None
    rejected = event.get("event_type") == "action_rejected" or data.get("status") == "rejected"
    terminal = event.get("event_type") == "action_result" and data.get("status") in _TERMINAL_STATUSES
    if event.get("event_type") not in {"action_rejected", "action_result"} or not (rejected or terminal):
        return None
    code = data.get("error_code")
    code = code if isinstance(code, str) and code else None
    return rejected or code in EXTERNAL_CONSTRAINT_ERROR_CODES, code


def _external_constraints(events: list[dict]) -> dict:
    """@brief 按运行和动作去重外部约束拒绝，并独立暴露协议拒绝与证据缺口。"""
    actions: dict[tuple, dict] = {}
    missing = []
    for event in events:
        evidence = _action_evidence(event)
        if evidence is None:
            continue
        rejected, code = evidence
        if rejected and (not event.get("action_id") or code is None):
            missing.append(event.get("event_id"))
        if not event.get("action_id"):
            continue
        key = (event.get("run_id"), event["action_id"])
        action = actions.setdefault(key, {"rejected": False, "code": None, "source": None})
        if rejected and not action["rejected"]:
            action.update(rejected=True, code=code, source=event.get("event_id"))
    blocked = [item for item in actions.values() if item["code"] in EXTERNAL_CONSTRAINT_ERROR_CODES]
    other = [item for item in actions.values() if item["rejected"] and item not in blocked]
    return {
        "block_count": len(blocked), "by_error_code": dict(sorted(Counter(item["code"] for item in blocked).items())),
        "source_event_ids": sorted(item["source"] for item in blocked), "evidence_complete": not missing,
        "observed_action_acceptance_rate":
            sum(not item["rejected"] for item in actions.values()) / len(actions) if actions else None,
        "terminal_action_count": len(actions), "missing_evidence_event_ids": sorted(set(missing)),
        "other_rejections": {"count": len(other),
                             "by_error_code": dict(sorted(Counter(item["code"] or "missing_error_code"
                                                                      for item in other).items())),
                             "source_event_ids": sorted(item["source"] for item in other)},
    }
