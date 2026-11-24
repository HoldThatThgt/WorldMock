"""@brief 按职责保存现有定义与纯计算：请求用途、行动请求判定、人物模型调用证据与 assistant 消息形状。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from worldmock.state.interaction.actions import START_TOOLS

#: 零点计划请求与响应的用途标记；交付窗口、决策统计、行为指标与质量报告按它区分。
PLAN_DAY_PURPOSE = "plan_day"
#: 日、月、年回顾请求与响应的用途标记。
MEMORY_DIGEST_PURPOSE = "memory_digest"
#: 搜索子会话请求与响应的用途标记。
SEARCH_SESSION_PURPOSE = "search_session"

#: 行动请求才开放的世界请求工具（start_main_activity）。
_ACTION_TOOL = START_TOOLS[0]


def is_action_request(request: Any) -> bool:
    """@brief 行动请求的唯一判定：没有 purpose、tool_choice 为 auto 且 tools 含 start_main_activity。

    与存储侧 SQL 谓词同义（docs/design/06-agent.md「宿主模型协议」）；检索子会话、零点计划与回顾带 purpose，
    截断重试的 tool_choice 为 required。
    """
    if not isinstance(request, dict) or request.get("purpose") is not None:
        return False
    if request.get("tool_choice") != "auto":
        return False
    tools = request.get("tools")
    if not isinstance(tools, list):
        return False
    return any(isinstance(item, dict) and isinstance(item.get("function"), dict)
               and item["function"].get("name") == _ACTION_TOOL for item in tools)


def response_self_note(response: Any) -> str | None:
    """@brief 提交世界请求那一次回复的正文：去掉首尾空白后的 choices[0].message.content，空或缺失时为 None。"""
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    text = content.strip() if isinstance(content, str) else ""
    return text or None


def request_tool_names(request: Any) -> list[str]:
    """@brief 一次模型请求实际开放的工具名；没有工具或形状不符时为空列表（行为指标与质量报告的调用类共用）。"""
    tools = request.get("tools") if isinstance(request, Mapping) else None
    return [item["function"].get("name") for item in tools if isinstance(item, Mapping)
            and isinstance(item.get("function"), Mapping)] if isinstance(tools, list) else []


@dataclass(frozen=True, slots=True)
class ModelCallEvidence:
    """@brief 一次人物或搜索子会话模型调用的请求、响应或失败证据；引擎按属性读取。"""

    wakeup_id: str
    agent_id: int
    call_id: str
    phase: Literal["request", "response", "failure"]
    attempt: int
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    error: str | None = None
    transport_attempts: tuple[dict[str, Any], ...] = ()
    purpose: str | None = None
    activity_id: str | None = None


def wire_tool_call(identity: Any, name: Any, arguments: Any) -> dict[str, Any]:
    """@brief 一个原生工具调用的发送形状：键依次为 id、type、function（name、arguments），参数缺省为 "{}"。"""
    return {"id": str(identity or ""), "type": "function",
            "function": {"name": str(name or ""), "arguments": str(arguments or "{}")}}


def assistant_message(content: Any, tool_calls: list[dict[str, Any]], reasoning: Any) -> dict[str, Any]:
    """@brief 回传给后续请求的 assistant 消息：键依次为 role、content、tool_calls，思考为真值时追加 reasoning_content。"""
    message: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    if reasoning:
        message["reasoning_content"] = reasoning
    return message
