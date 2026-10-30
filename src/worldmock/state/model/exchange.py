"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from worldmock.state.interaction.actions import START_TOOLS



#: 零点计划请求与响应的用途标记；交付窗口、决策统计、行为指标与质量报告按它区分。
PLAN_DAY_PURPOSE = "plan_day"
#: 日、月、年回顾请求与响应的用途标记。
MEMORY_DIGEST_PURPOSE = "memory_digest"


#: 行动请求才开放的世界请求工具（start_main_activity）；只读问答、记忆巩固与反思的请求都不带它。
_ACTION_TOOL = START_TOOLS[0]



def is_action_request(request: Any) -> bool:
    """@brief 行动请求的唯一判定：没有 purpose、tools 含 start_main_activity 且 thinking 开启。

    与存储侧 SQL 谓词同义（docs/design/06-agent.md「宿主模型协议」）；工作验收、零点计划与日、月、年回顾带 purpose，
    只读问答、记忆巩固与反思不带 start_main_activity。
    """
    if not isinstance(request, dict) or request.get("purpose") is not None:
        return False
    extra_body = request.get("extra_body")
    if not isinstance(extra_body, dict) or extra_body.get("thinking") != {"type": "enabled"}:
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
