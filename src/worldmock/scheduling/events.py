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
    #: 本人设置的一次性闹钟，到期可唤醒睡眠。
    ALARM_RING = "alarm_ring"


class ScheduledEvent(StrictModel):
    """@brief 模拟时间到期时由环境处理的事件。"""

    scheduled_id: str = Field(min_length=1)
    kind: ScheduledKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    agent_id: int | None = Field(default=None, gt=0)
    data: dict[str, Any] = Field(default_factory=dict)
    #: 动作创建的排期引用同次提交的真实结果事件；静态场景排期可为空。
    caused_by: list[str] = Field(default_factory=list)
