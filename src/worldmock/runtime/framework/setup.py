"""@brief 冻结运行身份与准备人物输入的唯一职责。"""
from __future__ import annotations
import hashlib
import logging
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import worldmock
from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.foundation.errors import IntegrityError
from worldmock.configuration.inputs.models import ModelConfig, ModelMetadata
from worldmock.configuration.inputs.warnings import content_empty_warnings, location_name_warnings
from worldmock.state.identity import ModelIdentity

logger = logging.getLogger(__name__)


def _model_identity(config: ModelConfig) -> ModelIdentity:
    """@brief 冻结显式模型端点、独立预算与当前提示和工具身份，不包含凭据。"""
    from worldmock.agents.life_agent import ACTION_THINKING, ACTION_TOOL_CHOICE, life_tool_schema_sha256
    from worldmock.agents.prompt.life_prompt import life_prompt_sha256

    return ModelIdentity(provider=config.provider, base_url=config.base_url, requested_model=config.requested_model,
                         thinking=ACTION_THINKING["thinking"]["type"], tool_choice=ACTION_TOOL_CHOICE,
                         max_retries=config.max_retries, transport_max_retries=config.transport_max_retries,
                         prompt_sha256=life_prompt_sha256(), tool_schema_sha256=life_tool_schema_sha256())


def stage_model_identity(config: ModelConfig, metadata: ModelMetadata,
                         same_second_continuation: bool = True) -> dict[str, Any]:
    """@brief 冻结行动阶段、实际服务容量及每个独立输出校验预算。"""
    from worldmock.agents.life_agent import ACTION_MAX_TOKENS, ACTION_THINKING, ACTION_TOOL_CHOICE

    identity = config.model_dump(mode="json", exclude={"api_key_env"})
    identity.update({"max_react_turns": config.max_react_turns, "strict": True, "reasoning_effort": None,
                     "model_metadata": metadata.model_dump(mode="json"),
                     "stages": {"action": {"thinking": ACTION_THINKING["thinking"]["type"],
                                           "tool_choice": ACTION_TOOL_CHOICE,
                                           "max_tokens": min(ACTION_MAX_TOKENS, metadata.max_output_tokens),
                                           "same_second_continuation": same_second_continuation}}})
    return validate_stage_model_identity(identity)


def project_root() -> Path:
    """@brief 由已导入的 worldmock 包位置推出项目根：包目录 src/worldmock 往上两级；不读命令行、当前目录或环境变量。"""
    return Path(worldmock.__file__).resolve().parents[2]


def runtime_code_sha256() -> str:
    """@brief 哈希项目根 src 下全部 Python 源码与根目录的 pyproject.toml、uv.lock（docs/design/09-io-formats.md
    「输入与模型身份」）；src 下其他类型的文件不进代码身份。
    """
    root = project_root()
    files = [(path.relative_to(root).as_posix(), path) for path in (root / "src").rglob("*.py")]
    files.extend((name, root / name) for name in ("pyproject.toml", "uv.lock"))
    return _labeled_sha256(files)


def _labeled_sha256(files: list[tuple[str, Path]]) -> str:
    """@brief 按标签排序，逐文件写入标签长度、标签、内容长度与内容；缺失或不安全的来源即拒绝。"""
    digest = hashlib.sha256()
    for label, path in sorted(files, key=lambda item: item[0]):
        if not path.is_file() or path.is_symlink():
            raise IntegrityError("Runtime identity source is missing or unsafe")
        encoded = label.encode()
        content = path.read_bytes()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_state(root: Path) -> tuple[str | None, bool | None]:
    """@brief 返回项目根所在 Git 检出的（HEAD 提交, 是否有未提交改动）；非 Git 检出或 git 失败时二者为 None。

    未提交标志取 git status --porcelain 是否非空（显式含未跟踪文件，不含忽略文件）；二者只记录，不参与恢复比对。
    """
    head = _git_output(root, "rev-parse", "HEAD")
    if head is None:
        return None, None
    status = _git_output(root, "status", "--porcelain", "--untracked-files=normal")
    if status is None:
        return None, None
    return head.strip().decode("ascii"), bool(status.strip())


def _git_output(root: Path, *arguments: str) -> bytes | None:
    """@brief 在项目根执行一条只读 git 命令并返回标准输出；git 不可用或命令失败时返回 None。"""
    command = ["git", "-C", str(root), *arguments]
    try:
        result = subprocess.run(command, capture_output=True, check=False,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def creation_inputs(inputs: EnvironmentInputs,
                    same_second_continuation: bool = True) -> tuple[tuple[dict[str, Any], ...], list[dict]]:
    """@brief 由人设表直接构造唯一人物卡片，生成真实目录输入警告；不调用模型。"""
    from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
    from worldmock.configuration.persona.persona_text import render_card

    persona = inputs.persona
    card = persona_card(persona)
    profile = persona.model_dump(mode="json")
    profile.update({"id": persona.agent_id, "same_second_continuation": same_second_continuation,
                    "persona_card": render_card(card), "persona_card_struct": persona_card_struct(card),
                    "model_identity": stage_model_identity(inputs.config.model, inputs.model_metadata,
                                                            same_second_continuation)})
    specs = ({"id": persona.agent_id, "profile": profile},)
    return specs, input_warnings(inputs)


def input_warnings(inputs: EnvironmentInputs) -> list[dict[str, Any]]:
    """@brief 按真实目录计算两种运行输入警告：没有绑定内容而人物写了媒体口味，地点显示名可疑。"""
    return [*content_empty_warnings(inputs.persona, inputs.catalog), *location_name_warnings(inputs.catalog)]


RETIRED_STAGE_IDENTITY_KEY = "two_stage_model_identity"


def validate_manifest_stage_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 运行入口拒绝退役阶段及缺失身份，只校验实际冻结的显式身份。"""
    if RETIRED_STAGE_IDENTITY_KEY in manifest:
        logger.error("Run manifest key %s is retired", RETIRED_STAGE_IDENTITY_KEY)
        raise IntegrityError(f"Run manifest key {RETIRED_STAGE_IDENTITY_KEY} is retired")
    if "stage_model_identity" not in manifest:
        logger.error("Run manifest decision stage identity is missing")
        raise IntegrityError("Run manifest decision stage identity is missing")
    return validate_stage_model_identity(manifest["stage_model_identity"])


def validate_stage_model_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """@brief 校验唯一行动阶段与实际容量、预算；端点及模型来自运行声明。"""
    from worldmock.agents.life_agent import ACTION_MAX_TOKENS, ACTION_THINKING, ACTION_TOOL_CHOICE
    from pydantic import ValidationError

    if not isinstance(identity, dict):
        _identity_error("Stage model identity must be an object")
    stages = identity.get("stages")
    if not isinstance(stages, dict) or set(stages) != {"action"}:
        _identity_error("Model identity stages must be exactly action")
    try:
        fields = set(ModelConfig.model_fields) - {"api_key_env"}
        config = ModelConfig.model_validate({**{key: identity[key] for key in fields}, "api_key_env": "MODEL_KEY"})
        metadata = ModelMetadata.model_validate(identity["model_metadata"])
    except (KeyError, TypeError, ValidationError) as error:
        _identity_error("Stage model identity has invalid configuration", error)
    if (metadata.requested_model != config.requested_model or metadata.metadata_url != config.metadata_url
            or metadata.context_window < config.context_window):
        _identity_error("Stage model identity has inconsistent model metadata")
    action = stages["action"]
    switch = action.get("same_second_continuation") if isinstance(action, dict) else None
    expected = {"thinking": ACTION_THINKING["thinking"]["type"], "tool_choice": ACTION_TOOL_CHOICE,
                "max_tokens": min(ACTION_MAX_TOKENS, metadata.max_output_tokens), "same_second_continuation": switch}
    if (not isinstance(switch, bool) or action != expected or identity.get("strict") is not True
            or identity.get("reasoning_effort") is not None):
        _identity_error("Stage model identity is inconsistent")
    allowed = fields | {"model_metadata", "stages", "strict", "reasoning_effort"}
    if set(identity) != allowed:
        _identity_error("Stage model identity has unexpected fields")
    return identity


def _identity_error(message: str, cause: Exception | None = None) -> None:
    """@brief 冻结模型身份不合法时写入英文日志并停止。"""
    logger.error("%s", message)
    raise IntegrityError(message) from cause
