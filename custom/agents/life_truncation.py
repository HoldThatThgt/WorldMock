"""@brief LifeAgent 的输出截断处理（docs/dev/SPEC-dayrun-fidelity.md 7.2「输出截断与思考螺旋」，裁决 D10）。

截断回合：提供商回复 finish_reason=length、正文为空且没有工具调用，即思考用尽了输出上限（max_tokens 同时覆盖思考与
输出）。它不进入后续请求（思考随之丢弃，只留在模型证据中），改发第四章结构的纠错 OUTPUT_TRUNCATED；写进模型响应证据的
协议错误串为「OUTPUT_TRUNCATED: <英文说明>」。

截断重试：截断之后的下一次请求关闭思考并 tool_choice=required（与工作验收、零点计划的组合相同；思考模式拒收 required），
max_tokens 不变、不发 reasoning_effort，只开放世界请求工具（只读问答只开放 finish），不开放记忆查询。是否重试按状态判定：
本次决策的消息历史以截断纠错结尾，且产生这次截断的请求开启了思考；所以每次截断只重试一次，其余请求（含只读问答）的
参数不变。重试的回复没有 reasoning_content：它给出的世界请求结束本次决策，同一秒的下一次决策按缺思考断链；它被纠错时
该回合与截断回合一样不留在历史中，本次决策此后带思考的请求里没有缺思考的 assistant。

纠错进历史：没有工具调用的回复（含截断回合）与截断重试的回合，纠错作为一条 user 消息接在历史末尾；本次决策中已有的
相同纠错去掉（替换、不叠加），续接链前缀逐字节不动。带工具调用的其余回复仍按 AS2 配对 assistant 与 tool 消息。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from custom.agents.life_feedback import HostFeedback, feedback_content, host_feedback
from worldmock.life.error_contract import host_report

#: 截断重试的思考开关与工具选择（接口合同「生成参数」；思考模式拒收 required，关闭思考后可用，真实探针见规格 7.2）。
RETRY_THINKING = {"thinking": {"type": "disabled"}}
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


def truncation_feedback() -> HostFeedback:
    """@brief 截断回合的纠错：OUTPUT_TRUNCATED 取登记的原因与改法，证据文字带英文说明。"""
    return host_feedback(host_report("OUTPUT_TRUNCATED"), TRUNCATION_DETAIL)


#: 截断纠错回给模型的正文（固定），用来判定消息历史是否以它结尾。
TRUNCATION_CONTENT = feedback_content(truncation_feedback())


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
