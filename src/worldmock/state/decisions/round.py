"""@brief 决策轮的三个数据类型：决策上下文、宿主补齐身份的工具调用与调度状态（docs/design/10-runtime.md「决策循环」）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

__all__ = ["DecisionContext", "DirectToolCall", "SchedulingState"]


def _require_text(value: str, field_name: str) -> None:
    """@brief 校验决策身份文本。

    :param value: 待校验的文本。
    :param field_name: 报错使用的字段名。
    :raises ValueError: 文本为空。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """@brief 绑定一次人物决策与共同世界观察版本。

    :param run_id: 当前运行身份。
    :param round_id: 当前共同决策轮身份。
    :param decision_id: 当前人物决策身份。
    :param agent_id: 由宿主绑定的人物身份。
    :param observed_at: 人物观察到的带时区模拟时间。
    :param world_state_version: 人物观察到的已提交世界版本。
    """

    run_id: str
    round_id: str
    decision_id: str
    agent_id: int
    observed_at: datetime
    world_state_version: int

    def __post_init__(self) -> None:
        """@brief 拒绝不能形成确定决策身份的上下文。"""
        _require_text(self.run_id, "run_id")
        _require_text(self.round_id, "round_id")
        _require_text(self.decision_id, "decision_id")
        if not isinstance(self.agent_id, int) or isinstance(self.agent_id, bool):
            raise ValueError("agent_id must be an integer")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not isinstance(self.world_state_version, int) or self.world_state_version < 0:
            raise ValueError("world_state_version must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DirectToolCall:
    """@brief 封装由宿主补齐身份的一次直接环境工具调用。

    :param context: 当前不可变决策上下文。
    :param action_id: 宿主为本次调用生成的稳定身份。
    :param tool_name: 闭集生活工具名。
    :param request: 由对应工具 Pydantic 模型验证的模型参数。
    """

    context: DecisionContext
    action_id: str
    tool_name: str
    request: BaseModel

    def __post_init__(self) -> None:
        """@brief 拒绝缺少宿主身份或未验证请求的调用。"""
        if not isinstance(self.context, DecisionContext):
            raise ValueError("context must be a DecisionContext")
        _require_text(self.action_id, "action_id")
        _require_text(self.tool_name, "tool_name")
        if not isinstance(self.request, BaseModel):
            raise ValueError("request must be a validated Pydantic model")


@dataclass(frozen=True, slots=True)
class SchedulingState:
    """@brief 描述环境当前时钟和下一次调度边界。

    :param current_time: 已确认的带时区模拟时间。
    :param ready_agent_ids: 当前边界允许决策的人物身份。
    :param next_event_at: 当前动作结算后的下一时间边界。
    :param world_state_version: 已提交世界状态版本。
    """

    current_time: datetime
    ready_agent_ids: tuple[int, ...]
    next_event_at: datetime | None
    world_state_version: int

    def __post_init__(self) -> None:
        """@brief 拒绝含糊时间、重复人物或倒退边界。"""
        self._validate_time(self.current_time, "current_time")
        if self.next_event_at is not None:
            self._validate_time(self.next_event_at, "next_event_at")
            if self.next_event_at <= self.current_time:
                raise ValueError("next_event_at must be later than current_time")
        if len(set(self.ready_agent_ids)) != len(self.ready_agent_ids):
            raise ValueError("ready_agent_ids must be unique")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in self.ready_agent_ids):
            raise ValueError("ready_agent_ids must contain integers")
        if not isinstance(self.world_state_version, int) or self.world_state_version < 0:
            raise ValueError("world_state_version must be a non-negative integer")

    @staticmethod
    def _validate_time(value: Any, field_name: str) -> None:
        """@brief 校验带时区时间。

        :param value: 待校验值。
        :param field_name: 报错使用的字段名。
        """
        if not isinstance(value, datetime):
            raise ValueError(f"{field_name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
