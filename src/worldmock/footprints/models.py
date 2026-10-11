"""@brief 数字足迹和不可变素材的公共数据合同。"""

from __future__ import annotations

import hashlib
from worldmock.base_models import StrictModel
from worldmock.base_models import FrozenModel
from worldmock.errors import reject_value
import json
import logging
import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worldmock.resources.models import DRAIN_DIRECTION, SOURCE_DIRECTION

#: 活动/结算事件 data
#: 内登记资源流水的保留字段名（docs/design/05-recording-engine.md「事件与来源」统一证据格式）。
#: 取值为分录字典列表：{resource, owner, direction, amount, cause}——direction 取
#: source/drain，amount 为有限正数，cause 引用触发动作/规则；时间戳与主体身份
#: 由所在足迹事件自身（occurred_at/agent_id）承载，不在流水字典内重复。
RESOURCE_FLOWS_FIELD = "resource_flows"

_FLOW_DICT_KEYS = frozenset({"resource", "owner", "direction", "amount", "cause"})

#: 决策节律增量字段（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）
#: ：activity_progress.data 声明该结算边界是否
#: 向人物提供了决策机会。静默边界（decision_offered=false）不调用模型、不产生
#: decision_started 等决策事件；共同身体结算明确记录是否提供决策。
DECISION_OFFERED_FIELD = "decision_offered"

#: 决策节律增量字段（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）
#: ：提供决策的边界记录实际命中的全部唤醒原因。
WAKE_REASONS_FIELD = "wake_reasons"

#: decision_started.data 的闭集（docs/design/06-agent.md「宿主模型协议」）。
DECISION_STARTED_FIELDS = frozenset({"round_id", "round_order", "world_state_version", WAKE_REASONS_FIELD})
#: 已退役的决策自述名（decision_started 字段与清单阶段键）：带它的事件与清单以英文错误拒绝。
RETIRED_DECISION_STATEMENT = "decision_statement"

def validate_resource_flows(payload: Any) -> list[dict[str, Any]]:
    """@brief 校验 data[resource_flows] 的分录字典约定，合法则原样返回。

    :param payload: 事件 data 中 resource_flows 字段的取值。
    :returns: 通过校验的分录字典列表。
    :raises ValueError: 结构、词表或数值违反约定时 fail-closed。
    """
    if not isinstance(payload, list):
        reject_value("resource_flows must be a list of flow dicts")
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != _FLOW_DICT_KEYS:
            reject_value(f"resource_flows[{index}] must contain exactly {sorted(_FLOW_DICT_KEYS)}")
        for field in ("resource", "owner", "cause"):
            if not isinstance(item[field], str) or not item[field]:
                reject_value(f"resource_flows[{index}].{field} must be a non-empty string")
        if item["direction"] not in (SOURCE_DIRECTION, DRAIN_DIRECTION):
            reject_value(f"resource_flows[{index}].direction must be source or drain")
        amount = item["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or not math.isfinite(amount) or amount <= 0:
            reject_value(f"resource_flows[{index}].amount must be a finite positive number")
    return payload


def validate_decision_rhythm(data: dict[str, Any]) -> None:
    """@brief 校验事件携带的决策节律字段：决策开始、共同身体结算与活动进度。

    写入路径在落库前调用，事件携带的唤醒原因必须属于当前闭集。

    :param data: decision_started、body_settlement 或 activity_progress 事件的 data 字典。
    :raises ValueError: decision_offered 非布尔或 wake_reasons 违反冻结词表时 fail-closed。
    """
    if DECISION_OFFERED_FIELD in data and not isinstance(data[DECISION_OFFERED_FIELD], bool):
        raise ValueError(f"{DECISION_OFFERED_FIELD} must be a boolean")
    if WAKE_REASONS_FIELD in data:
        from worldmock.life.schedule import WAKE_TRIGGERS
        allowed = {trigger.name for trigger in WAKE_TRIGGERS}
        reasons = data[WAKE_REASONS_FIELD]
        if not isinstance(reasons, list):
            raise ValueError(f"{WAKE_REASONS_FIELD} must be a list of wake reason names")
        for index, reason in enumerate(reasons):
            if not isinstance(reason, str) or reason not in allowed:
                raise ValueError(f"{WAKE_REASONS_FIELD}[{index}] must be one of the frozen wake reasons")


def validate_decision_started(data: dict[str, Any]) -> None:
    """@brief 校验 decision_started.data 恰为闭集 {round_id, round_order, world_state_version, wake_reasons}。

    :param data: decision_started 事件的 data 字典。
    :raises ValueError: 带退役的决策自述字段，或键集与闭集不符时 fail-closed。
    """
    if RETIRED_DECISION_STATEMENT in data:
        logging.error("decision_started data carries the retired field %s", RETIRED_DECISION_STATEMENT)
        raise ValueError(f"decision_started.data carries the retired field {RETIRED_DECISION_STATEMENT}")
    if set(data) != DECISION_STARTED_FIELDS:
        logging.error("decision_started data keys are outside the closed set: %s", sorted(data))
        raise ValueError("decision_started.data must be exactly round_id, round_order, world_state_version, "
                         "wake_reasons")


EventType = Literal[
    "observation", "decision_started", "model_request", "model_response", "action_requested",
    "action_accepted", "action_rejected", "activity_progress", "action_result", "environment_event",
    "memory_updated", "checkpoint_created", "run_ended", "run_failed",
]


class AssetRef(FrozenModel):
    """@brief 已完成落盘的素材身份，大小单位为字节。"""

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    source: dict[str, Any]


class EventDraft(BaseModel):
    """@brief 环境提供事实，存储分配持久序号和状态版本。

    活动/结算事件的 data 可携带 RESOURCE_FLOWS_FIELD 字段登记本次结算的资源
    分录（{resource, owner, direction, amount, cause} 字典列表），写入前可用
    validate_resource_flows 校验；activity_progress 可另携 DECISION_OFFERED_FIELD
    与 WAKE_REASONS_FIELD（validate_decision_rhythm 校验）；decision_started 的 data 恰为
    DECISION_STARTED_FIELDS 闭集，由本模型自动执行 validate_decision_started 校验；
    其余事件的 data 结构由各自领域自行约定。
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
    #: 事件实际造成的账户或资源差额；领域正文不复制资金流水。
    resource_changes: dict[str, int | float] = Field(default_factory=dict)
    artifact_refs: list[AssetRef] = Field(default_factory=list)
    visibility: Literal["agent", "evaluation", "system"] = "agent"
    source_kind: Literal["environment", "model", "scenario", "external"] = "environment"
    observed_at: int | None = None

    @model_validator(mode="after")
    def validate_decision_started_evidence(self) -> EventDraft:
        """@brief 在创建、持久化和读取决策事件时校验 decision_started 的闭集字段。"""
        if "resource_changes" in self.data or "resource_changes" in self.data.get("data", {}):
            logging.error("Event data carries retired resource_changes")
            raise ValueError("Event data carries retired resource_changes")
        if self.event_type == "decision_started":
            validate_decision_started(self.data)
        if self.event_type == "environment_event":
            from worldmock.footprints.environment_events import validate_environment_event

            validate_environment_event(self)
        return self


class Event(EventDraft):
    """@brief 带严格全序、程序时钟和世界版本的持久事件。"""

    schema_version: Literal[1] = 1
    run_id: str
    event_seq: int = Field(gt=0)
    recorded_at: str
    wall_elapsed_ms: int = Field(ge=0)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=0)


class Checkpoint(StrictModel):
    """@brief SQLite 完整事务提交的共同恢复点。"""

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
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Canonical JSON encoding failed")
        raise


def sha256_json(value: Any) -> str:
    """@brief 对唯一规范 JSON 字节计算 SHA-256；非有限值直接拒绝。"""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def is_sha256(value: Any) -> bool:
    """@brief 仅接受恰好六十四个小写十六进制字符的字符串。"""
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
