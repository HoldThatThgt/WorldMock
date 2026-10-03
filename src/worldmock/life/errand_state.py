"""@brief 个人事务的状态模型、上限、枚举词与事件种类（docs/dev/SPEC-errands-workday.md 2.1、2.2、2.7）。

个人事务是人物记下的、能由世界结算确认的打算：取得或吃掉一件具体商品若干份。`AgentState.errands` 只保存未完成的事务
（键为事务身份），完成或取消时从字典移出，终态、原因与证据只写进同一提交的事件。本模块只放状态模型与常量，只依赖
pydantic 与标准库；全部逻辑在 errands.py（状态与逻辑分两个模块，models.py 引用本模块而不形成循环导入）。
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_LOGGER = logging.getLogger(__name__)

#: 上限（实验假设，docs/dev/SPEC-errands-workday.md 2.1「上限」）：每人未完成事务至多 8 条，一次请求至多 4 条变更，
#: 名称至多 30 字符，备注与取消原因各至多 120 字符，数量 1 至 9。上限由校验执行，不静默截取、不自动合并。
MAX_OPEN_ERRANDS = 8
MAX_ERRAND_CHANGES = 4
ERRAND_NAME_MAX_CHARS = 30
ERRAND_TEXT_MAX_CHARS = 120
ERRAND_QUANTITY_MIN = 1
ERRAND_QUANTITY_MAX = 9
#: 累计量达到数量减去该容差即完成（2.4「累计与容差」）。
ERRAND_TOLERANCE = 1e-9
#: 累计量的小数位数（2.1 字段表「九位小数」）。
ERRAND_PROGRESS_DIGITS = 9

#: 变更种类闭集（2.2）：没有「完成」，枚举值不用「登记」；同一请求内按「取消、更新、新增」的次序求值。
CHANGE_ADD, CHANGE_UPDATE, CHANGE_CANCEL = "新增", "更新", "取消"
ERRAND_CHANGES = (CHANGE_ADD, CHANGE_UPDATE, CHANGE_CANCEL)
#: 请求里的结果种类写法与状态里的结果种类代码互为映射。
OUTCOME_CODES = {"取得": "obtain", "吃掉": "eat"}
OUTCOME_WORDS = {"obtain": "取得", "eat": "吃掉"}

#: 六种事务事件（environment_event 的 kind，2.7）；提醒与办成两种被进度层消费，必须人物可见。
REGISTERED_KIND = "errand_registered"
UPDATED_KIND = "errand_updated"
CANCELLED_KIND = "errand_cancelled"
PROGRESSED_KIND = "errand_progressed"
COMPLETED_KIND = "errand_completed"
REMINDED_KIND = "errand_reminded"
ERRAND_EVENT_KINDS = (REGISTERED_KIND, UPDATED_KIND, CANCELLED_KIND, PROGRESSED_KIND, COMPLETED_KIND, REMINDED_KIND)
AGENT_VISIBLE_KINDS = frozenset({COMPLETED_KIND, REMINDED_KIND})


class ErrandState(BaseModel):
    """@brief 一条未完成的个人事务（docs/dev/SPEC-errands-workday.md 2.1 字段表）。

    errand_id 由来源动作身份与新增条目在请求中的序号确定性生成，只存状态与事件；name、memo 照人物所写保存，只用于呈现，
    不参与结算与完成判定；outcome 与 product_id 是完成依据，记下后不可改；progress 是已累计量（九位小数），恒小于
    quantity；remind_at 是提醒的绝对时刻，reminded_at 是实际交付提醒的时刻。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    errand_id: str = Field(min_length=1)
    name: str = Field(pattern=r"\S")
    outcome: Literal["obtain", "eat"]
    product_id: str = Field(min_length=1)
    quantity: int = Field(ge=ERRAND_QUANTITY_MIN, le=ERRAND_QUANTITY_MAX)
    progress: float = Field(ge=0, allow_inf_nan=False)
    memo: str | None
    registered_at: int = Field(ge=0)
    remind_at: int | None = Field(ge=0)
    reminded_at: int | None = Field(ge=0)
    origin_action_id: str = Field(min_length=1)
    origin_event_id: str = Field(min_length=1)

    @field_validator("progress")
    @classmethod
    def _nine_decimals(cls, value: float) -> float:
        """@brief 已累计量按九位小数保存（多次结算相加的浮点误差不进入状态与其规范编码）。"""
        return round(value, ERRAND_PROGRESS_DIGITS)

    @model_validator(mode="after")
    def check_state(self) -> "ErrandState":
        """@brief 未完成事务的两条不变量：已累计量恒小于数量（达到即完成并移出状态）；只有设了提醒时刻的事务才有交付
        时刻，且交付不早于提醒时刻。"""
        if self.progress >= self.quantity:
            _LOGGER.error("An open errand has reached its quantity")
            raise ValueError("errand progress must stay below its quantity")
        if self.reminded_at is not None and (self.remind_at is None or self.reminded_at < self.remind_at):
            _LOGGER.error("Errand reminder delivery precedes its reminder")
            raise ValueError("errand reminded_at requires remind_at and must not precede it")
        return self
