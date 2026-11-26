"""@brief 单日连续上下文会话状态与五层基准键集定义。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from worldmock.state.decisions.suspended import StateModel
from worldmock.state.logging import _LOGGER

#: 可纳入跨唤醒差量与日会话观察基准的五层内部键集。
FIVE_LAYER_KEYS = frozenset({
    "tool_layer",
    "todo_layer",
    "environment_layer",
    "memory_layer",
    "progress_layer",
})


class DaySessionState(StateModel):
    """@brief 单个人物在单个本地日内跨唤醒共享的不可变连续会话状态。"""

    #: 绑定的本地 ISO 日期（YYYY-MM-DD）；跨午夜零点重置。
    local_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    #: 日会话建立时固化的系统层紧凑 JSON；日内恒定不变。
    system_prompt: str = Field(min_length=1)
    #: 当前主循环工具 Schema 的 SHA-256 摘要。
    tools_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: 日内连续消息序列：[system, init_user, assistant, tool, ...]。
    messages: tuple[dict[str, Any], ...]
    #: 上次唤醒退出时挂起的边界工具调用 ID 序列。
    pending_boundary_calls: tuple[str, ...] = ()
    #: 上次已交付给模型且生效的五层基准快照。
    last_observed_layers: dict[str, Any] = Field(default_factory=dict)
    #: 本周期通过 lookup_targets 载入的工具层目标键集合。
    loaded_target_keys: tuple[tuple[Literal["location", "app", "product"], str], ...] = ()
    #: 本本地日内已触发的日内 Compaction 次数。
    compaction_count: int = Field(default=0, ge=0)
    #: 最近一次 Compaction 折叠覆盖的最大已提交 event_seq。
    compacted_through_seq: int = Field(default=0, ge=0)
    #: 上一次模型调用观测到的输入令牌数。
    last_prompt_tokens: int | None = Field(default=None, ge=0)

    @field_validator("local_date")
    @classmethod
    def validate_local_date(cls, value: str) -> str:
        """@brief 核对本地日期为合法的规范 ISO 日历日期。"""
        try:
            date.fromisoformat(value)
        except ValueError as error:
            _LOGGER.error("DaySessionState local_date %r is not a valid calendar date", value)
            raise ValueError("local_date must be a valid canonical ISO date") from error
        return value

    @field_validator("pending_boundary_calls")
    @classmethod
    def validate_pending_boundary_calls(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """@brief 挂起边界调用标识必须全部非空白且互不重复。"""
        if any(not item.strip() for item in value):
            _LOGGER.error("DaySessionState pending_boundary_calls contains blank call ID")
            raise ValueError("pending_boundary_calls must contain non-blank strings")
        if len(set(value)) != len(value):
            _LOGGER.error("DaySessionState pending_boundary_calls contains duplicate call IDs")
            raise ValueError("pending_boundary_calls must not contain duplicates")
        return value

    @field_validator("last_observed_layers")
    @classmethod
    def validate_last_observed_layers(cls, value: dict[str, Any]) -> dict[str, Any]:
        """@brief 五层基准字典仅允许包含 FIVE_LAYER_KEYS 中的键。"""
        unknown = sorted(set(value) - FIVE_LAYER_KEYS)
        if unknown:
            _LOGGER.error("DaySessionState last_observed_layers contains unknown keys: %s", unknown)
            raise ValueError(f"last_observed_layers contains unsupported keys: {unknown}")
        return value

    @field_validator("loaded_target_keys")
    @classmethod
    def validate_loaded_target_keys(
        cls,
        value: tuple[tuple[Literal["location", "app", "product"], str], ...],
    ) -> tuple[tuple[Literal["location", "app", "product"], str], ...]:
        """@brief 核对已载入目标键中的对象标识均非空白。"""
        for kind, object_id in value:
            if not object_id.strip():
                _LOGGER.error("DaySessionState loaded_target_keys has blank object_id for %s", kind)
                raise ValueError("loaded_target_keys object_id must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_message_sequence(self) -> DaySessionState:
        """@brief 核对消息序列角色结构与挂起边界调用 ID 的一致性。"""
        if len(self.messages) < 2:
            _LOGGER.error("DaySessionState messages must have at least system and init_user")
            raise ValueError("messages must contain at least system and initial user messages")
        if self.messages[0].get("role") != "system" or self.messages[1].get("role") != "user":
            _LOGGER.error("DaySessionState messages prefix must start with system then user")
            raise ValueError("messages[0] must be system and messages[1] must be user")
        if any(message.get("role") == "user" for message in self.messages[2:]):
            _LOGGER.error("DaySessionState messages contains a second user message after index 1")
            raise ValueError("messages must not contain user role after index 1")
        if self.pending_boundary_calls:
            _validate_pending_calls_against_tail(self.messages[-1], self.pending_boundary_calls)
        return self


def _validate_pending_calls_against_tail(tail: dict[str, Any], pending: tuple[str, ...]) -> None:
    """@brief 核对非空挂起边界调用与末尾 assistant 消息的 tool_calls 匹配。"""
    if tail.get("role") != "assistant":
        _LOGGER.error("DaySessionState pending_boundary_calls requires assistant tail message")
        raise ValueError("pending_boundary_calls requires messages[-1] role to be assistant")
    tool_calls = tail.get("tool_calls")
    if not isinstance(tool_calls, (list, tuple)):
        _LOGGER.error("DaySessionState assistant tail message lacks tool_calls list")
        raise ValueError("assistant tail message must include tool_calls for pending_boundary_calls")
    declared = {item.get("id") for item in tool_calls if isinstance(item, dict) and isinstance(item.get("id"), str)}
    missing = [call_id for call_id in pending if call_id not in declared]
    if missing:
        _LOGGER.error("DaySessionState pending_boundary_calls missing from tail tool_calls: %s", missing)
        raise ValueError("pending_boundary_calls must appear in messages[-1] tool_calls")
