"""@brief 数字足迹和不可变素材的公共数据合同。"""

from __future__ import annotations

import json
import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from worldmock.resources.models import DRAIN_DIRECTION, SOURCE_DIRECTION

#: 活动/结算事件 data 内登记资源流水的保留字段名（设计文档 §9 统一证据格式）。
#: 取值为分录字典列表：{resource, owner, direction, amount, cause}——direction 取
#: source/drain，amount 为有限正数，cause 引用触发动作/规则；时间戳与主体身份
#: 由所在足迹事件自身（occurred_at/agent_id）承载，不在流水字典内重复。
RESOURCE_FLOWS_FIELD = "resource_flows"

_FLOW_DICT_KEYS = frozenset({"resource", "owner", "direction", "amount", "cause"})


def validate_resource_flows(payload: Any) -> list[dict[str, Any]]:
    """@brief 校验 data[resource_flows] 的分录字典约定，合法则原样返回。

    :param payload: 事件 data 中 resource_flows 字段的取值。
    :returns: 通过校验的分录字典列表。
    :raises ValueError: 结构、词表或数值违反约定时 fail-closed。
    """
    if not isinstance(payload, list):
        raise ValueError("resource_flows must be a list of flow dicts")
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != _FLOW_DICT_KEYS:
            raise ValueError(f"resource_flows[{index}] must contain exactly {sorted(_FLOW_DICT_KEYS)}")
        for field in ("resource", "owner", "cause"):
            if not isinstance(item[field], str) or not item[field]:
                raise ValueError(f"resource_flows[{index}].{field} must be a non-empty string")
        if item["direction"] not in (SOURCE_DIRECTION, DRAIN_DIRECTION):
            raise ValueError(f"resource_flows[{index}].direction must be source or drain")
        amount = item["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"resource_flows[{index}].amount must be a finite positive number")
    return payload


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
    """@brief 环境提供事实，存储分配持久序号和状态版本。

    活动/结算事件的 data 可携带 RESOURCE_FLOWS_FIELD 字段登记本次结算的资源
    分录（{resource, owner, direction, amount, cause} 字典列表），写入前可用
    validate_resource_flows 校验；其余事件的 data 结构由各自领域自行约定。
    """

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
