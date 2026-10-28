"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import FrozenModel
from typing import Literal
from pydantic import Field, StrictBool, model_validator


#: 运行清单结构版本：顶层字段集合或 stage_model_identity 的键集变了才升；版本不符的运行不能打开、恢复或重放，
#: 导出认证同样拒绝，不迁移。
RUN_MANIFEST_SCHEMA_VERSION = 7



class ModelIdentity(FrozenModel):
    """@brief 不包含凭据的冻结模型请求身份。"""

    provider: Literal["openai_compatible"]
    base_url: str
    requested_model: str
    thinking: Literal["disabled", "enabled"]
    tool_choice: Literal["required", "auto"]
    max_retries: int = Field(strict=True, ge=0, le=10)
    transport_max_retries: int = Field(strict=True, ge=0, le=10)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")



class RunIdentity(FrozenModel):
    """@brief 由 runtime 显式分配并与场景交叉验证的运行身份。

    git_commit 与 git_dirty 记录启动时的检出状态，非 Git 检出时二者为 null；只记录，不参与恢复比对
    （docs/design/06-agent.md「宿主模型协议」）。
    """

    run_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    agent_ids: tuple[int, ...]
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_identity: ModelIdentity
    git_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    git_dirty: StrictBool | None = None

    @model_validator(mode="after")
    def validate_git_state(self) -> "RunIdentity":
        """@brief Git 提交与未提交标志同时记录，或在非 Git 检出时同时为 null。"""
        if (self.git_commit is None) != (self.git_dirty is None):
            raise ValueError("git_commit and git_dirty must both be recorded or both be null")
        return self
