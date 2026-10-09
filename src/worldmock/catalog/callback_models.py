"""@brief 声明式回调状态、触发器、效果与可见提示的严格模型。"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, field_validator

from worldmock.base_models import DeepFrozenModel
from worldmock.catalog.models import ConcreteAction, NonEmptyString, NonNegativeInteger, PositiveInteger
from worldmock.errors import reject_value


class CallbackState(DeepFrozenModel):
    """@brief 单个通用状态的初值、上限与收货重置商品。"""

    state: NonEmptyString
    initial: StrictInt
    cap: NonNegativeInteger
    reset_on_goods: tuple[NonEmptyString, ...]


class CallbackTrigger(DeepFrozenModel):
    """@brief 保留 JSON 的 class 字段，以完整属性名供求值器使用。"""

    model_config = ConfigDict(serialize_by_alias=True)


class ActionTrigger(CallbackTrigger):
    """@brief 成功受理一个具体动作。"""

    trigger_class: Literal["action"] = Field(alias="class")
    key: ConcreteAction


class TimeTrigger(CallbackTrigger):
    """@brief 不带月度参数的固定时间触发。"""

    trigger_class: Literal["time"] = Field(alias="class")
    key: Literal["hourly", "daily", "run_start", "run_stop"]


class MonthlyTrigger(CallbackTrigger):
    """@brief 本地月度日期与二十四小时制时刻。"""

    trigger_class: Literal["time"] = Field(alias="class")
    key: Literal["monthly"]
    day: Annotated[StrictInt, Field(ge=1, le=31)]
    time: NonEmptyString


class StateTrigger(CallbackTrigger):
    """@brief 状态从阈值以上越到阈值以下。"""

    trigger_class: Literal["state"] = Field(alias="class")
    key: NonEmptyString
    below: PositiveInteger


class EventTrigger(CallbackTrigger):
    """@brief 合格预排事件或静态唤醒类别订阅。"""

    trigger_class: Literal["event"] = Field(alias="class")
    key: Literal[
        "delivery_arrival", "app_push", "calendar_flag", "weather", "time_boundary", "duty_boundary",
        "reminder_due", "event_arrival", "state_change", "outcome_feedback",
    ]


class StateDelta(DeepFrozenModel):
    """@brief 非零状态增量，可按实际结算本地日期限一次。"""

    type: Literal["state_delta"]
    state: NonEmptyString
    delta: StrictInt
    once_per: Literal["local_day"] | None = None

    @field_validator("once_per", mode="before")
    @classmethod
    def require_declared_local_day(cls, value):
        """@brief 按日限次只有缺省或 local_day，不接受显式 null。"""
        if value is None:
            reject_value("A declared callback daily limit must be local_day.")
        return value


class FootprintStep(DeepFrozenModel):
    """@brief 足迹链一项随机选择，支付步骤本身不转账。"""

    kind: Literal["browse", "payment"]
    app_pool: tuple[NonEmptyString, ...]
    pick: Literal["random"]


class FootprintChain(DeepFrozenModel):
    """@brief 按声明顺序执行的附加 app 足迹链。"""

    type: Literal["footprint_chain"]
    steps: tuple[FootprintStep, ...]


class MoneyTransfer(DeepFrozenModel):
    """@brief 人物与外部锚点之间的收入或支出。"""

    type: Literal["transfer"]
    direction: Literal["income", "expense"]
    amount_fen: PositiveInteger


class VisibleHint(DeepFrozenModel):
    """@brief 足迹链按触发净资金差额显示提示。"""

    visible: StrictBool


class ReminderCondition(DeepFrozenModel):
    """@brief 静默提醒的状态与严格低于阈值条件。"""

    state: NonEmptyString
    below: PositiveInteger


class StateReminder(DeepFrozenModel):
    """@brief 内容 app 的静默状态提醒。"""

    when: ReminderCondition
    channel: Literal["app_push"]
    app_id: NonEmptyString
    text: NonEmptyString


Trigger = ActionTrigger | TimeTrigger | MonthlyTrigger | StateTrigger | EventTrigger
Effect = Annotated[StateDelta | FootprintChain | MoneyTransfer, Field(discriminator="type")]


class CallbackRule(DeepFrozenModel):
    """@brief 一条严格触发规则与恰好一个效果。"""

    rule_id: NonEmptyString
    trigger: Trigger
    effect: Effect
    notify: VisibleHint | StateReminder | None = None
