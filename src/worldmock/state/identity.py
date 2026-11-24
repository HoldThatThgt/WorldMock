"""@brief 共享运行身份、多档案模型身份与多人物摘要校验的独立定义。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from worldmock.configuration.inputs.models import (
    ModelMetadata,
    ModelRoutingConfig,
    referenced_profile_ids,
)
from worldmock.foundation.models import FrozenModel
from worldmock.foundation.serialization import is_sha256
from worldmock.state.logging import _LOGGER

#: 运行清单结构版本：顶层字段集合或 ModelIdentity 结构变化时升级，不迁移旧版本。
RUN_MANIFEST_SCHEMA_VERSION = 8


def validate_persona_sha256s_input(value: Any) -> Any:
    """@brief 在反序列化前拒绝布尔键、非正整数键及非规范数字字符串键。"""
    if not isinstance(value, dict):
        return value
    for key in value:
        if isinstance(key, bool):
            _LOGGER.error("persona_sha256s key must be a positive integer, got bool")
            raise ValueError("persona_sha256s keys must be positive integers")
        if isinstance(key, str):
            if not (key.isascii() and key.isdigit() and int(key) > 0 and str(int(key)) == key):
                _LOGGER.error("persona_sha256s string key %r is not a canonical positive integer", key)
                raise ValueError("persona_sha256s keys must be positive integers")
        elif type(key) is not int or key <= 0:
            _LOGGER.error("persona_sha256s key %r must be a positive integer", key)
            raise ValueError("persona_sha256s keys must be positive integers")
    return value


def check_persona_sha256s(mapping: dict[int, str], expected_ids: set[int]) -> None:
    """@brief 核对人物摘要映射的键集与人物编号完全一致，且值均为规范 SHA-256。"""
    if set(mapping) != expected_ids:
        _LOGGER.error("persona_sha256s keys %s disagree with agents %s", sorted(mapping), sorted(expected_ids))
        raise ValueError("persona_sha256s keys must match agent_ids")
    if any(not is_sha256(digest) for digest in mapping.values()):
        _LOGGER.error("persona_sha256s contains an invalid SHA-256 digest")
        raise ValueError("persona_sha256s values must be valid SHA-256 digests")


class StageProfileIdentity(FrozenModel):
    """@brief 单个激活模型档的冻结配置、权威元数据与有效输出上限，不含凭据信息。"""

    provider: Literal["openai_compatible"]
    base_url: str = Field(min_length=1)
    requested_model: str = Field(min_length=1, pattern=r"\S")
    metadata_url: str = Field(min_length=1)
    context_window: int = Field(strict=True, ge=262144)
    thinking: StrictBool
    max_output_tokens: int = Field(strict=True, gt=0)
    max_retries: int = Field(strict=True, ge=0, le=10)
    transport_max_retries: int = Field(strict=True, ge=0, le=10)
    max_react_turns: int = Field(strict=True, ge=1, le=64)
    reflection_validation_max_retries: int = Field(strict=True, ge=0, le=5)
    day_plan_validation_max_retries: int = Field(strict=True, ge=0, le=5)
    model_metadata: ModelMetadata
    effective_max_tokens: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def validate_profile_metadata(self) -> StageProfileIdentity:
        """@brief 核对冻结元数据与模型档标识、容量及有效输出上限一致。"""
        meta = self.model_metadata
        if (meta.requested_model != self.requested_model or meta.metadata_url != self.metadata_url
                or meta.context_window < self.context_window):
            _LOGGER.error("Stage profile %s disagrees with its frozen model_metadata", self.requested_model)
            raise ValueError("stage profile model metadata disagrees with profile")
        expected_tokens = min(self.max_output_tokens, meta.max_output_tokens)
        if self.effective_max_tokens != expected_tokens:
            _LOGGER.error("effective_max_tokens %d disagrees with expected %d",
                          self.effective_max_tokens, expected_tokens)
            raise ValueError("effective_max_tokens disagrees with output token limits")
        return self


class ModelIdentity(FrozenModel):
    """@brief 不含凭据的多档案路由、提示指纹与工具模式指纹冻结身份。"""

    profiles: dict[str, StageProfileIdentity]
    routing: dict[str, Any]
    prompt_sha256s: dict[str, str]
    tool_schema_sha256s: dict[str, str]

    @model_validator(mode="after")
    def validate_model_identity(self) -> ModelIdentity:
        """@brief 核对路由表仅引用 profiles 中的全部激活档位，且主/搜索提示与工具指纹齐全。"""
        routing = ModelRoutingConfig.model_validate(self.routing)
        referenced = set(referenced_profile_ids(routing))
        if not self.profiles or set(self.profiles) != referenced:
            _LOGGER.error("ModelIdentity profiles %s must match referenced routing profiles %s",
                          sorted(self.profiles), sorted(referenced))
            raise ValueError("ModelIdentity profiles must equal the profiles referenced by routing")
        _check_sha256_stages(self.prompt_sha256s, "prompt_sha256s")
        _check_sha256_stages(self.tool_schema_sha256s, "tool_schema_sha256s")
        return self


def _check_sha256_stages(mapping: dict[str, str], label: str) -> None:
    """@brief 校验提示与工具指纹均恰含 action 与 search 两个规范 SHA-256 键。"""
    if set(mapping) != {"action", "search"} or any(not is_sha256(value) for value in mapping.values()):
        _LOGGER.error("%s must map action and search to valid SHA-256 digests", label)
        raise ValueError(f"{label} must map action and search to valid SHA-256 digests")


class RunIdentity(FrozenModel):
    """@brief 由 runtime 显式分配并与场景交叉验证的多人物运行身份。"""

    run_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    agent_ids: tuple[int, ...]
    persona_sha256s: dict[int, str]
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_identity: ModelIdentity
    git_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    git_dirty: StrictBool | None = None

    @field_validator("agent_ids", mode="before")
    @classmethod
    def reject_non_strict_agent_ids(cls, value: Any) -> Any:
        """@brief 拒绝布尔元素进入人物编号元组。"""
        if isinstance(value, (list, tuple)) and any(isinstance(item, bool) for item in value):
            _LOGGER.error("agent_ids must not contain boolean values")
            raise ValueError("agent_ids must contain positive integers")
        return value

    _validate_persona_keys = field_validator("persona_sha256s", mode="before")(validate_persona_sha256s_input)

    @model_validator(mode="after")
    def validate_run_identity(self) -> RunIdentity:
        """@brief 核对人物集合非空不重复、人物摘要匹配，以及 Git 提交与脏标志成对记录。"""
        if not self.agent_ids or len(set(self.agent_ids)) != len(self.agent_ids) or any(
            item <= 0 for item in self.agent_ids
        ):
            _LOGGER.error("RunIdentity agent_ids must be non-empty unique positive integers")
            raise ValueError("agent_ids must be non-empty unique positive integers")
        check_persona_sha256s(self.persona_sha256s, set(self.agent_ids))
        if (self.git_commit is None) != (self.git_dirty is None):
            _LOGGER.error("git_commit and git_dirty must both be recorded or both be null")
            raise ValueError("git_commit and git_dirty must both be recorded or both be null")
        return self
