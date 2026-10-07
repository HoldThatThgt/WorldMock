"""@brief 系统活动受理共用规则派生的计划时长。"""

from __future__ import annotations

from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind, AgentState, DERIVED_DURATION_CAP_SECONDS


def capped_duration_seconds(duration: int) -> int:
    """@brief 等待与睡眠不足一分钟拒绝，超过一天按一天受理。"""
    if duration < 60:
        raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
    return min(duration, DERIVED_DURATION_CAP_SECONDS)


def derived_activity_duration(kind: ActivityKind, agent: AgentState, item: Any, feedback_seconds: int) -> int:
    """@brief 睡眠时长取睡眠需要，等待取现行反馈间隔。"""
    if kind == ActivityKind.SLEEP:
        return capped_duration_seconds(round(agent.body.sleep_need_hours * 3600))
    return capped_duration_seconds(feedback_seconds)
