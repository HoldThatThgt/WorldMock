"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import StrictModel
from typing import Any, Literal
from pydantic import Field




class StoredAction(StrictModel):
    """@brief 一条已结算动作的回执与即时结果：本秒的拒绝计数读它，活动终结时核对开始动作仍在
    （docs/design/04-simulation-engine.md「原子性与恢复」）。"""

    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    receipt: dict[str, Any]
    result: dict[str, Any] | None = None



class PendingAction(StrictModel):
    """@brief 一个决策轮中尚未结算的动作。"""

    run_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    observed_at: int = Field(ge=0)
    world_state_version: int = Field(ge=0)
    order: int = Field(ge=0)
    tool_name: str = Field(min_length=1)
    request: dict[str, Any]
    request_sha256: str = Field(min_length=64, max_length=64)
    requested_event_id: str = Field(min_length=1)



class DecisionRoundState(StrictModel):
    """@brief 决策轮的预期人物和已登记请求。"""

    round_id: str = Field(min_length=1)
    expected_agent_ids: list[int]
    observed_version: int = Field(ge=0)
    status: Literal["open", "committed", "aborted"] = "open"
    pending_actions: list[PendingAction] = Field(default_factory=list)



class SameMomentPresentation(StrictModel):
    """@brief 一个人物本秒的呈现暂存（docs/design/04-simulation-engine.md「原子性与恢复」）。

    stamps 为本秒各轮实际呈现的对象到时刻映射，时钟推进时才并入 presentation_state。
    """

    stamps: dict[str, int]
