"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations

from typing import Any

from pydantic import Field

from worldmock.foundation.models import StrictModel


class StoredAction(StrictModel):
    """@brief 一条已结算动作的即时结果：活动终结时核对开始动作仍在
    （docs/design/04-simulation-engine.md「原子性与恢复」）。"""

    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    result: dict[str, Any] | None = None
