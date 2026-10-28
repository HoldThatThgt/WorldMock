"""@brief 记录与评估共享的整数秒区间及共同身体结算判定。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def valid_interval(start: Any, end: Any) -> bool:
    """@brief 只接受非布尔整数秒构成的正时长半开区间。"""
    return type(start) is int and type(end) is int and start < end


def is_body_settlement(event: Any) -> bool:
    """@brief 判定实际主体的共同身体结算，不依赖单项活动。"""
    if isinstance(event, Mapping):
        return event.get("event_type") == "environment_event" and event.get("data", {}).get("kind") == "body_settlement"
    return event.event_type == "environment_event" and event.data.get("kind") == "body_settlement"


def body_settlements(events: Iterable[Any], agent_id: int | None = None) -> list[Any]:
    """@brief 按输入顺序筛选属于实际人物的共同身体结算，可限定同一人物。"""
    selected = []
    for event in events:
        owner = event.get("agent_id") if isinstance(event, Mapping) else event.agent_id
        if type(owner) is int and (agent_id is None or owner == agent_id) and is_body_settlement(event):
            selected.append(event)
    return selected
