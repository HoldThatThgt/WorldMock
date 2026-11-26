"""@brief 冻结运行身份与准备多人物输入的唯一职责。"""

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
from worldmock.configuration.inputs.models import (
    ModelMetadata,
    ModelProfileConfig,
    RunConfig,
    referenced_profile_ids,
)
from worldmock.configuration.inputs.warnings import content_empty_warnings, location_name_warnings
from worldmock.foundation.errors import IntegrityError
from worldmock.state.identity import ModelIdentity, StageProfileIdentity

logger = logging.getLogger(__name__)


def stage_profile_identity(profile: ModelProfileConfig, metadata: ModelMetadata) -> StageProfileIdentity:
    """@brief 由配置档与实测元数据构造冻结的单模型档身份，不包含凭据值或环境变量名。"""
    effective = min(profile.max_output_tokens, metadata.max_output_tokens)
    return StageProfileIdentity(
        provider=profile.provider, base_url=profile.base_url, requested_model=profile.requested_model,
        metadata_url=profile.metadata_url, context_window=profile.context_window, thinking=profile.thinking,
        max_output_tokens=profile.max_output_tokens, max_retries=profile.max_retries,
        transport_max_retries=profile.transport_max_retries, max_react_turns=profile.max_react_turns,
        reflection_validation_max_retries=profile.reflection_validation_max_retries,
        day_plan_validation_max_retries=profile.day_plan_validation_max_retries,
        compact_trigger_tokens=profile.compact_trigger_tokens, prompt_cache_key=profile.prompt_cache_key,
        model_metadata=metadata, effective_max_tokens=effective,
    )


def model_identity(config: RunConfig, metadata: Mapping[str, ModelMetadata]) -> ModelIdentity:
    """@brief 冻结生效模型档、四维路由与各阶段提示及工具指纹，不包含凭据。"""
    from worldmock.agents.life_agent import life_tool_schema_sha256
    from worldmock.agents.prompt.life_prompt import life_prompt_sha256
    from worldmock.agents.search.search_prompt import search_prompt_sha256, search_tool_schema_sha256

    profiles = {
        profile_id: stage_profile_identity(config.models[profile_id], metadata[profile_id])
        for profile_id in referenced_profile_ids(config.model_routing)
    }
    return ModelIdentity(
        profiles=profiles,
        routing=config.model_routing.model_dump(mode="json"),
        prompt_sha256s={"action": life_prompt_sha256(), "search": search_prompt_sha256()},
        tool_schema_sha256s={"action": life_tool_schema_sha256(), "search": search_tool_schema_sha256()},
    )


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
            logger.error("Runtime identity source missing or unsafe: %s", label)
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


def creation_inputs(inputs: EnvironmentInputs) -> tuple[tuple[dict[str, Any], ...], list[dict[str, Any]]]:
    """@brief 由多人物人设表直接构造全部人物卡片，生成真实目录输入警告；不调用模型。"""
    from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
    from worldmock.configuration.persona.persona_text import render_card

    specs = []
    for persona in inputs.personas:
        card = persona_card(persona)
        profile = persona.model_dump(mode="json")
        profile.update({"id": persona.agent_id, "persona_card": render_card(card),
                        "persona_card_struct": persona_card_struct(card)})
        specs.append({"id": persona.agent_id, "profile": profile})
    return tuple(specs), input_warnings(inputs)


def input_warnings(inputs: EnvironmentInputs) -> list[dict[str, Any]]:
    """@brief 按真实目录计算全部人物的媒体口味警告与地点显示名警告。"""
    warnings = [warning for persona in inputs.personas
                for warning in content_empty_warnings(persona, inputs.catalog)]
    warnings.extend(location_name_warnings(inputs.catalog))
    return warnings
