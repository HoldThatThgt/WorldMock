"""@brief 本地严格校验和宿主协议错误保持完整 JSON 位置与合法闭集。"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from pydantic import BaseModel, ValidationError

from worldmock.state.interaction.errors import ErrorItem, ErrorReport, host_defaults, host_report, items_fix
from worldmock.state.interaction.request_errors import argument_report

FEEDBACK_INSTRUCTION = "No world action was registered. Correct the reported parameters and submit again."

class HostFeedback(str):
    """@brief 英文证据字符串同时持有完整的结构化错误。"""

    report: ErrorReport

    def __new__(cls, text: str, report: ErrorReport | None = None) -> "HostFeedback":
        """@brief 复制时保留实例字典中的结构化合同。"""
        feedback = super().__new__(cls, text)
        if report is not None:
            feedback.report = report
        return feedback


class HostIssue(NamedTuple):
    """@brief 参数明细和英文原因。"""

    item: ErrorItem
    sentence: str
    detail: str


def host_feedback(report: ErrorReport, detail: str) -> HostFeedback:
    """@brief 错误日志和模型反馈从同一结构产生。"""
    logging.error("Life tool protocol rejected: %s: %s", report.error_code, detail)
    return HostFeedback(f"{report.error_code}: {detail}", report)


def merged_feedback(issues: Sequence[HostIssue], total: int, fixes: Mapping[str, str] | None = None) -> HostFeedback:
    """@brief 所有独立错误按参数顺序保留，不因错误类别截断。"""
    items = tuple(issue.item for issue in issues)
    table = {item.error_code: host_defaults(item.error_code)[1] for item in items} | dict(fixes or {})
    report = host_report(items[0].error_code, "; ".join(issue.sentence for issue in issues),
                         items_fix(items, total, table), items)
    return host_feedback(report, "; ".join(issue.detail for issue in issues))


def argument_feedback(tool: str, error: ValidationError, model: type[BaseModel], arguments: Any) -> HostFeedback:
    """@brief 保留全部 Pydantic loc、原值、有限枚举及明确格式条件。"""
    return host_feedback(argument_report(tool, error, model, arguments), f"Invalid {tool} arguments.")


def feedback_report(error: str) -> ErrorReport:
    """@brief 只消费生成时保留的结构，拒绝从旧日志字符串重建错误。"""
    if not isinstance(error, HostFeedback) or not isinstance(getattr(error, "report", None), ErrorReport):
        logging.error("Tool protocol feedback is missing its structured error report")
        raise TypeError("Tool protocol feedback requires a structured error report")
    return error.report


def report_content(report: ErrorReport) -> str:
    """@brief 由错误结构直接编出模型收到的纠错正文：完整五字段错误加固定英文修正指示；不写日志。"""
    return json.dumps({**report.payload(), "instruction": FEEDBACK_INSTRUCTION}, ensure_ascii=False)


def feedback_content(error: str) -> str:
    """@brief 模型收到完整五字段错误和固定英文修正指示。"""
    return report_content(feedback_report(error))
