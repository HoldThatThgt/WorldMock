"""@brief 呈现与受理共用的活动计划时长规则。"""

from __future__ import annotations

from typing import Any

from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind, AgentState, DERIVED_DURATION_CAP_SECONDS


def capped_duration_seconds(duration: int) -> int:
    """@brief 不足一分钟拒绝，一般活动超过一天按一天受理。"""
    if duration < 60:
        raise BusinessRuleError("ACTIVITY_DURATION_INVALID")
    return min(duration, DERIVED_DURATION_CAP_SECONDS)


def derived_activity_duration(kind: ActivityKind, agent: AgentState, item: Any, feedback_seconds: int) -> int:
    """@brief 从内容进度、生活活动目录、睡眠需要或反馈间隔推导非工作活动计划秒数。"""
    if kind == ActivityKind.LIFESTYLE:
        return capped_duration_seconds(item.typical_duration_minutes * 60)
    if kind == ActivityKind.SLEEP:
        return capped_duration_seconds(round(agent.body.sleep_need_hours * 3600))
    return capped_duration_seconds(feedback_seconds)
