"""@brief 全局时钟调度边界状态数据类型（docs/design/10-runtime.md「决策循环」）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from worldmock.state.logging import _LOGGER

__all__ = ["SchedulingState"]


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
                _LOGGER.error("SchedulingState next_event_at is not later than current_time")
                raise ValueError("next_event_at must be later than current_time")
        if len(set(self.ready_agent_ids)) != len(self.ready_agent_ids):
            _LOGGER.error("SchedulingState ready_agent_ids contains duplicates")
            raise ValueError("ready_agent_ids must be unique")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in self.ready_agent_ids):
            _LOGGER.error("SchedulingState ready_agent_ids contains non-integer value")
            raise ValueError("ready_agent_ids must contain integers")
        if not isinstance(self.world_state_version, int) or self.world_state_version < 0:
            _LOGGER.error("SchedulingState world_state_version is negative")
            raise ValueError("world_state_version must be a non-negative integer")

    @staticmethod
    def _validate_time(value: Any, field_name: str) -> None:
        """@brief 校验带时区时间。

        :param value: 待校验值。
        :param field_name: 报错使用的字段名。
        """
        if not isinstance(value, datetime):
            _LOGGER.error("SchedulingState %s is not a datetime", field_name)
            raise ValueError(f"{field_name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            _LOGGER.error("SchedulingState %s is not timezone-aware", field_name)
            raise ValueError(f"{field_name} must be timezone-aware")
