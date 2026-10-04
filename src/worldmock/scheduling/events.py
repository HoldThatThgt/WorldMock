"""@brief 预排事件的封闭类型与载荷模型（docs/design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎「预排事件」）。

本模块是 ScheduledKind/ScheduledEvent 的权威定义处；life.models 再导出以维持
既有导入路径。为保持导入方向（life.models → 本模块），此处不得依赖任何
worldmock.life 模块，模型基类按 life.models.StrictModel 的语义本地声明。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictScheduledModel(BaseModel):
    """@brief 与 life.models.StrictModel 同语义的封闭模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=False)


class ScheduledKind(StrEnum):
    """@brief 环境自主到期事件的封闭类型。"""

    MAIL_ARRIVAL = "mail_arrival"
    DELIVERY_ARRIVAL = "delivery_arrival"
    WORK_AVAILABLE = "work_available"
    TASK_EXPIRED = "task_expired"
    CALENDAR_FLAG = "calendar_flag"
    BILL_DUE = "bill_due"
    PAYROLL = "payroll"
    PRICE_CHANGE = "price_change"
    SUPPLY_QUOTE_CHANGE = "supply_quote_change"
    PRODUCT_RELEASE = "product_release"
    #: 联系人回复的预排送达（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）：到期写入收件箱并以 message_arrival 唤醒。
    MESSAGE_ARRIVAL = "message_arrival"


class ScheduledEvent(_StrictScheduledModel):
    """@brief 模拟时间到期时由环境处理的事件。"""

    scheduled_id: str = Field(min_length=1)
    kind: ScheduledKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    agent_id: int | None = Field(default=None, gt=0)
    data: dict[str, Any] = Field(default_factory=dict)
