"""@brief 调用生产规则检查器执行因果和资源连续性门禁。"""

from __future__ import annotations

import logging
from typing import Any

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.models import CausalResult, DatasetView, JsonObject

LOGGER = logging.getLogger(__name__)
_FINDING_FIELDS = {"code", "severity", "event_ids", "source_pointer", "message"}


def evaluate_causality(view: DatasetView, support: JsonObject) -> CausalResult:
    """@brief 使用统一证据投影调用唯一生产因果检查器。"""
    try:
        from worldmock.delivery.rules import evaluate_causal_events
    except (ImportError, ModuleNotFoundError) as exc:
        LOGGER.exception("Production causal evaluator is unavailable")
        raise EvaluationError("causal_checker_unavailable", "production causal evaluator is unavailable") from exc
    events = [event.to_rule_dict() for event in view.events]
    try:
        findings = evaluate_causal_events(events, support)
    except Exception as exc:
        LOGGER.exception("Production causal evaluation failed")
        raise EvaluationError("causal_checker_failed", "production causal evaluator failed") from exc
    checked = _validate_findings(findings)
    hard_errors = tuple(item for item in checked if item["severity"] == "hard_error")
    return CausalResult(tuple(checked), hard_errors, not hard_errors)


def _validate_findings(value: Any) -> list[JsonObject]:
    """@brief 验证生产检查器返回的冻结 finding 合同。"""
    if not isinstance(value, list):
        raise EvaluationError("causal_checker_failed", "causal findings must be an array")
    return [_validate_finding(item) for item in value]


def _validate_finding(value: Any) -> JsonObject:
    """@brief 验证一个可回查的硬错误记录。"""
    if not isinstance(value, dict) or set(value) != _FINDING_FIELDS:
        raise EvaluationError("causal_checker_failed", "causal finding schema is invalid")
    for field in ("code", "source_pointer", "message"):
        if not isinstance(value[field], str) or not value[field]:
            raise EvaluationError("causal_checker_failed", f"causal finding {field} is invalid")
    event_ids = value["event_ids"]
    if not isinstance(event_ids, list) or not event_ids:
        raise EvaluationError("causal_checker_failed", "causal finding event_ids is invalid")
    if not all(isinstance(item, str) and item for item in event_ids):
        raise EvaluationError("causal_checker_failed", "causal finding event_ids is invalid")
    if value["severity"] != "hard_error":
        raise EvaluationError("causal_checker_failed", "causal finding severity is invalid")
    return dict(value)
