"""@brief 数字足迹和不可变素材的公共数据合同。"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "observation", "decision_started", "model_request", "model_response", "action_requested",
    "action_accepted", "action_rejected", "activity_progress", "action_result", "environment_event",
    "memory_updated", "checkpoint_created", "run_ended", "run_failed",
]


class AssetRef(BaseModel):
    """@brief 已完成落盘的素材身份，大小单位为字节。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    source: dict[str, Any]


class EventDraft(BaseModel):
    """@brief 环境提供事实，存储分配持久序号和状态版本。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: EventType
    occurred_at: int
    agent_id: int | None = None
    decision_id: str | None = None
    action_id: str | None = None
    activity_id: str | None = None
    caused_by: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[AssetRef] = Field(default_factory=list)
    visibility: Literal["agent", "evaluation", "system"] = "agent"
    source_kind: Literal["environment", "model", "scenario", "external"] = "environment"
    observed_at: int | None = None


class Event(EventDraft):
    """@brief 带严格全序、程序时钟和世界版本的持久事件。"""

    schema_version: Literal[1] = 1
    run_id: str
    event_seq: int = Field(gt=0)
    recorded_at: str
    wall_elapsed_ms: int = Field(ge=0)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=0)


class Checkpoint(BaseModel):
    """@brief SQLite 完整事务提交的共同恢复点。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    checkpoint_id: str
    run_id: str
    current_time: int
    state_version: int
    last_event_seq: int
    state: dict[str, Any]
    snapshots: dict[str, AssetRef]
    manifest_sha256: str
    record_sha256: str


def canonical_json(value: Any) -> bytes:
    """@brief 使用唯一 JSON 编码，使哈希与重放身份稳定。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
