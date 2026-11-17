"""@brief 本地严格校验和宿主协议错误保持完整 JSON 位置与合法闭集。"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from pydantic import BaseModel, ValidationError

from worldmock.state.interaction.errors import ErrorItem, ErrorReport, host_defaults, host_report, items_fix
from worldmock.state.interaction.request_errors import argument_report

FEEDBACK_INSTRUCTION = "No world action was registered. Correct the reported parameters and submit again."


@dataclass(frozen=True, slots=True)
class ProtocolFeedback:
    """@brief 宿主协议纠错：五字段错误结构与写进证据 error 的英文文字。"""

    report: ErrorReport
    text: str


class HostIssue(NamedTuple):
    """@brief 参数明细和英文原因。"""

    item: ErrorItem
    sentence: str
    detail: str


def host_feedback(report: ErrorReport, detail: str) -> ProtocolFeedback:
    """@brief 错误日志和模型反馈从同一结构产生。"""
    logging.error("Life tool protocol rejected: %s: %s", report.error_code, detail)
    return ProtocolFeedback(report, f"{report.error_code}: {detail}")


def merged_feedback(issues: Sequence[HostIssue], total: int,
                    fixes: Mapping[str, str] | None = None) -> ProtocolFeedback:
    """@brief 所有独立错误按参数顺序保留，不因错误类别截断。"""
    items = tuple(issue.item for issue in issues)
    table = {item.error_code: host_defaults(item.error_code)[1] for item in items} | dict(fixes or {})
    report = host_report(items[0].error_code, "; ".join(issue.sentence for issue in issues),
                         items_fix(items, total, table), items)
    return host_feedback(report, "; ".join(issue.detail for issue in issues))


def argument_feedback(tool: str, error: ValidationError, model: type[BaseModel], arguments: Any) -> ProtocolFeedback:
    """@brief 保留全部 Pydantic loc、原值、有限枚举及明确格式条件。"""
    return host_feedback(argument_report(tool, error, model, arguments), f"Invalid {tool} arguments.")


def stage_feedback(code: str, path: tuple) -> ProtocolFeedback:
    """@brief 服务和证据异常明确指出失败阶段与调用位置，不虚构参数或执行结果。"""
    report = host_report(code)
    item = ErrorItem(path, None, code, expected=report.fix, why=report.reason)
    return host_feedback(replace(report, items=(item,)), report.reason)


def stage_error(code: str, path: tuple) -> RuntimeError:
    """@brief 阶段失败的异常：文字是阶段纠错的证据文字，同一份纠错结构挂在属性 feedback 上。"""
    feedback = stage_feedback(code, path)
    error = RuntimeError(feedback.text)
    error.feedback = feedback
    return error


def report_content(report: ErrorReport) -> str:
    """@brief 由错误结构直接编出模型收到的纠错正文：完整五字段错误加固定英文修正指示；不写日志。"""
    return json.dumps({**report.payload(), "instruction": FEEDBACK_INSTRUCTION}, ensure_ascii=False)
