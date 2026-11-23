"""@brief 回调生产与持久读取共用的封闭效果正文。"""

from typing import Literal

from pydantic import ConfigDict, Field

from worldmock.foundation.models import StrictModel


class StateDeltaResult(StrictModel):
    """@brief 非零请求增量与实际钳位前后值。"""

    model_config = ConfigDict(strict=True)
    #: 回调效果类别固定标识。
    kind: Literal["callback_effect"] = "callback_effect"
    #: 效果正文类型：规则增量。
    type: Literal["state_delta"] = "state_delta"
    #: 触发本次增量的回调规则标识。
    rule_id: str = Field(min_length=1)
    #: 发生变更的回调状态名称。
    state: str = Field(min_length=1)
    #: 请求施加的原始增量。
    delta: int
    #: 变更前的状态值。
    before: int = Field(ge=0)
    #: 钳位并变更后的状态值。
    after: int = Field(ge=0)


class StateResetResult(StrictModel):
    """@brief 真正收货将状态直接重置到上限。"""

    model_config = ConfigDict(strict=True)
    #: 回调效果类别固定标识。
    kind: Literal["callback_effect"] = "callback_effect"
    #: 效果正文类型：收货置满。
    type: Literal["reset_to_cap"] = "reset_to_cap"
    #: 规则标识：商品重置无回调规则编号，固定为 None。
    rule_id: None = None
    #: 被重置的回调状态名称。
    state: str = Field(min_length=1)
    #: 触发本次重置的商品标识。
    goods_id: str = Field(min_length=1)
    #: 收货重置前的状态值。
    before: int = Field(ge=0)
    #: 收货置满后的状态值。
    after: int = Field(ge=0)


class StateGainResult(StrictModel):
    """@brief 真正收货按 gain_on_goods 将状态加 1 并钳位至上限。"""

    model_config = ConfigDict(strict=True)
    #: 回调效果类别固定标识。
    kind: Literal["callback_effect"] = "callback_effect"
    #: 效果正文类型：收货累加增益。
    type: Literal["gain_on_receipt"] = "gain_on_receipt"
    #: 规则标识：商品增益无回调规则编号，固定为 None。
    rule_id: None = None
    #: 发生增益的回调状态名称。
    state: str = Field(min_length=1)
    #: 触发本次增益的商品标识。
    goods_id: str = Field(min_length=1)
    #: 收货前的状态值。
    before: int = Field(ge=0)
    #: 收货并钳位至上限后的状态值。
    after: int = Field(ge=0)


class ConsumeOnStartResult(StrictModel):
    """@brief 凭状态的地点内容、限制声明边或商业住宿开始时把本人这个状态减 1：before、after 是减 1 前后的值。"""

    model_config = ConfigDict(strict=True)
    #: 回调效果类别固定标识。
    kind: Literal["callback_effect"] = "callback_effect"
    #: 效果正文类型：开始时消耗。
    type: Literal["consume_on_start"] = "consume_on_start"
    #: 规则标识：开始消耗无回调规则编号，固定为 None。
    rule_id: None = None
    #: 被扣减的回调状态名称。
    state: str = Field(min_length=1)
    #: 开始地点内容时为对应内容身份；地图限制边出发或酒店入睡扣减时为 None。
    content_id: str | None = Field(default=None, min_length=1)
    #: 扣减前的状态值。
    before: int = Field(ge=0)
    #: 扣减后的状态值。
    after: int = Field(ge=0)


class TransferResult(StrictModel):
    """@brief 固定两账户之间的实际付款或未付结果。"""

    model_config = ConfigDict(strict=True)
    #: 回调效果类别固定标识。
    kind: Literal["callback_effect"] = "callback_effect"
    #: 效果正文类型：转账。
    type: Literal["transfer"] = "transfer"
    #: 触发本次转账的回调规则标识。
    rule_id: str = Field(min_length=1)
    #: 资金流动方向（收入或支出）。
    direction: Literal["income", "expense"]
    #: 转账金额（分）。
    amount_fen: int = Field(gt=0)
    #: 转出账户标识。
    from_account_id: str = Field(min_length=1)
    #: 转入账户标识。
    to_account_id: str = Field(min_length=1)
    #: 实际支付结果（已付或未付）。
    status: Literal["paid", "unpaid"]
