"""@brief 截断判定与协议纠错正文构造。"""


from __future__ import annotations
from typing import Any
from worldmock.agents.protocol.life_feedback import (
    FEEDBACK_INSTRUCTION,
    ProtocolFeedback,
    host_feedback,
    report_content,
)
from worldmock.state.interaction.errors import ErrorItem, ErrorReport, host_report


RETRY_TOOL_CHOICE = "required"

#: 截断回合写进模型响应证据的英文说明（协议错误串为「OUTPUT_TRUNCATED: <说明>」）。
TRUNCATION_DETAIL = "model output reached max_tokens while reasoning, before any tool call (finish_reason=length)"



def truncated(response: Any) -> bool:
    """@brief 回复是否为截断回合：finish_reason=length、正文为空（或只有空白）且没有工具调用。"""
    choices = getattr(response, "choices", None)
    if not choices:
        return False
    message = getattr(choices[0], "message", None)
    return (getattr(choices[0], "finish_reason", None) == "length" and message is not None
            and not str(getattr(message, "content", None) or "").strip()
            and not getattr(message, "tool_calls", None))



def _truncation_report() -> ErrorReport:
    """@brief 截断回合的错误结构：OUTPUT_TRUNCATED 取登记的原因与改法；只构造结构，不写日志。"""
    item = ErrorItem(("tool_calls",), None, "OUTPUT_TRUNCATED",
                     expected="One complete world request is required after the truncated output.",
                     why=TRUNCATION_DETAIL)
    return host_report("OUTPUT_TRUNCATED", items=(item,))



def truncation_feedback() -> ProtocolFeedback:
    """@brief 真实截断回合的纠错：经 host_feedback 写一条错误日志，证据文字带英文说明。"""
    return host_feedback(_truncation_report(), TRUNCATION_DETAIL)



#: 截断纠错回给模型的正文（固定），由错误结构直接生成，导入时不写日志。
TRUNCATION_CONTENT = report_content(_truncation_report())
