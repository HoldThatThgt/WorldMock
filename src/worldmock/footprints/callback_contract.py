"""@brief 回调生产与持久读取共用的封闭效果正文。"""

from typing import Literal

from pydantic import ConfigDict, Field

from worldmock.base_models import StrictModel


class StateDeltaResult(StrictModel):
    """@brief 非零请求增量与实际钳位前后值。"""

    model_config = ConfigDict(strict=True)
    kind: Literal["callback_effect"] = "callback_effect"
    type: Literal["state_delta"] = "state_delta"
    rule_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    delta: int
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class StateResetResult(StrictModel):
    """@brief 真正收货将状态直接重置到上限。"""

    model_config = ConfigDict(strict=True)
    kind: Literal["callback_effect"] = "callback_effect"
    type: Literal["reset_to_cap"] = "reset_to_cap"
    rule_id: None = None
    state: str = Field(min_length=1)
    goods_id: str = Field(min_length=1)
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class ConsumeOnStartResult(StrictModel):
    """@brief 凭状态的地点内容开始时把本人这个状态减 1：before、after 是减 1 前后的值。"""

    model_config = ConfigDict(strict=True)
    kind: Literal["callback_effect"] = "callback_effect"
    type: Literal["consume_on_start"] = "consume_on_start"
    rule_id: None = None
    state: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class TransferResult(StrictModel):
    """@brief 固定两账户之间的实际付款或未付结果。"""

    model_config = ConfigDict(strict=True)
    kind: Literal["callback_effect"] = "callback_effect"
    type: Literal["transfer"] = "transfer"
    rule_id: str = Field(min_length=1)
    direction: Literal["income", "expense"]
    amount_fen: int = Field(gt=0)
    from_account_id: str = Field(min_length=1)
    to_account_id: str = Field(min_length=1)
    status: Literal["paid", "unpaid"]
