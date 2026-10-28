"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import FrozenModel
import re
from typing import Any, Literal
from pydantic import Field, model_validator

from worldmock.state.world.kinds import ActionStatus


class CounterfactualIntervention(FrozenModel):
    """@brief 对新反事实分支应用一次的封闭世界条件干预。"""

    kind: Literal["set_balance_fen"]
    agent_id: int = Field(gt=0)
    balance_fen: int = Field(ge=0)
    hypothesis: str = Field(min_length=1)
    source_event_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> "CounterfactualIntervention":
        """@brief 要求干预证据身份唯一且假设具有实际文本。"""
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("counterfactual source event ids must be unique")
        if not self.hypothesis.strip():
            raise ValueError("counterfactual hypothesis must not be blank")
        return self



class CounterfactualReceipt(FrozenModel):
    """@brief 已提交反事实干预的事件和状态身份。"""

    intervention_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    before_balance_fen: int = Field(ge=0)
    after_balance_fen: int = Field(ge=0)
    world_state_version: int = Field(gt=0)



class ActionReceipt(FrozenModel):
    """@brief 决策阶段成功登记动作的回执。"""

    action_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    status: Literal["submitted"] = "submitted"
    request_sha256: str = Field(min_length=64, max_length=64)



class ActionResult(FrozenModel):
    """@brief 结算后返回给人物的权威动作结果。"""

    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    status: ActionStatus
    occurred_at: int = Field(ge=0)
    activity_id: str | None = None
    error_code: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)



def rejection_reason_text(error: Exception) -> str:
    """@brief 拒绝的英文日志文本：业务错误码加完整链式底层原因，只清除十六进制身份。

    只写进运行日志，不进入结果：拒绝结果的 message 与 details 取 life.guidance 生成的公开原因
    （docs/design/04-simulation-engine.md「动作、名称与并行」），链式底层异常（骨架校验等）可能是请求模型的校验转储。
    """
    reason = str(error)
    cause = error.__cause__
    if cause is not None:
        detail = re.sub(r"[0-9a-fA-F]{8,}", "-", " ".join(str(cause).split()))
        reason = f"{reason}: {detail}"
    return reason
