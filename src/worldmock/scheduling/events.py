"""@brief 预排事件的封闭类型与载荷模型（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）。

本模块是 ScheduledKind/ScheduledEvent 的权威定义处，直接使用共享严格模型基类，
不依赖生活领域模块。
"""

from __future__ import annotations

from worldmock.base_models import StrictModel
from enum import StrEnum
from typing import Any

from pydantic import Field


class ScheduledKind(StrEnum):
    """@brief 环境自主到期事件的封闭类型。"""

    DELIVERY_ARRIVAL = "delivery_arrival"
    ORDER_SHIPPED = "order_shipped"
    CALENDAR_FLAG = "calendar_flag"
    APP_PUSH = "app_push"
    TIME_TRIGGER = "time_trigger"
    WEATHER = "weather"



class ScheduledEvent(StrictModel):
    """@brief 模拟时间到期时由环境处理的事件。"""

    scheduled_id: str = Field(min_length=1)
    kind: ScheduledKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    agent_id: int | None = Field(default=None, gt=0)
    data: dict[str, Any] = Field(default_factory=dict)
    #: 到达排期与发货通知排期引用同次提交里付清这一趟配送的付款事件；静态场景排期可为空。
    caused_by: list[str] = Field(default_factory=list)
