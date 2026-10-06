"""@brief 预排事件的封闭类型与载荷模型（docs/design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎「预排事件」）。

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

    MAIL_ARRIVAL = "mail_arrival"
    DELIVERY_ARRIVAL = "delivery_arrival"
    CALENDAR_FLAG = "calendar_flag"
    BILL_DUE = "bill_due"
    PAYROLL = "payroll"
    #: 联系人回复的预排送达（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）：到期写入收件箱并以 message_arrival 唤醒。
    MESSAGE_ARRIVAL = "message_arrival"
    #: 应用整点推送机会，到期时以人物当前会话判定资格。
    APP_PUSH = "app_push"


class ScheduledEvent(StrictModel):
    """@brief 模拟时间到期时由环境处理的事件。"""

    scheduled_id: str = Field(min_length=1)
    kind: ScheduledKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    agent_id: int | None = Field(default=None, gt=0)
    data: dict[str, Any] = Field(default_factory=dict)
