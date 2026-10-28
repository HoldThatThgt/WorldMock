"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from worldmock.agents.protocol.life_feedback import HostFeedback, host_feedback, report_content
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



def truncation_feedback() -> HostFeedback:
    """@brief 真实截断回合的纠错：经 host_feedback 写一条错误日志，证据文字带英文说明。"""
    return host_feedback(_truncation_report(), TRUNCATION_DETAIL)



#: 截断纠错回给模型的正文（固定），用来判定消息历史是否以它结尾；由错误结构直接生成，导入时不写日志。
TRUNCATION_CONTENT = report_content(_truncation_report())



def thinking_enabled(request: Mapping[str, Any]) -> bool:
    """@brief 请求是否开启了思考（扩展体 thinking.type=enabled）；本次决策还没有发出请求（空请求）时为否。"""
    body = request.get("extra_body")
    return isinstance(body, Mapping) and body.get("thinking") == {"type": "enabled"}



def retry_due(history: Sequence[Mapping[str, Any]], previous: Mapping[str, Any]) -> bool:
    """@brief 下一次请求是否为截断重试：消息历史以截断纠错结尾，且产生这次截断的上一次请求开启了思考（重试自己截断时
    不再重试）。

    :param history: 本次决策当前的消息历史。
    :param previous: 本次决策上一次实际发出的请求（还没有时为空）。
    """
    return (bool(history) and history[-1].get("role") == "user"
            and history[-1].get("content") == TRUNCATION_CONTENT and thinking_enabled(previous))



def with_feedback(messages: Sequence[dict[str, Any]], content: str, start: int) -> list[dict[str, Any]]:
    """@brief 纠错作为一条 user 消息接在历史末尾，并去掉本次决策中已有的相同纠错（替换、不叠加）。

    :param start: 本次决策自有消息的起点，即续接链前缀的条数；前缀逐字节保留。
    """
    kept = [item for item in messages[start:] if item.get("role") != "user" or item.get("content") != content]
    return [*messages[:start], *kept, {"role": "user", "content": content}]
