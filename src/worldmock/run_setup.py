"""@brief 配置进程、冻结运行身份与准备人物输入的唯一职责。"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from worldmock.environment import EnvironmentInputs
from worldmock.errors import IntegrityError
from worldmock.input_models import ModelConfig, ModelMetadata, PersonaConfig
from worldmock.life.models import ModelIdentity
from worldmock.model_endpoint import credential_value

logger = logging.getLogger(__name__)


_AS2_PACKAGE = "agentsociety2"
#: 固定基线与补丁共同生成的实际 AS2 源码身份。
AS2_LOCKED_SOURCE_SHA256 = "bf716c7b982ea1aa057207b4ad06e1758b598bccef90834e18da2ca4253db3c6"
_AS2_IDENTITY_ERROR = "AS2 runtime identity differs from the locked source"


def _configure_process(project_root: Path, config: ModelConfig) -> None:
    """@brief 核对锁定源码后，按唯一运行配置与环境变量凭据配置原生模型客户端。"""
    root = Path(project_root).resolve()
    if not (root / "custom" / "agents" / "life_agent.py").is_file():
        logger.error("WorldMock project root is incomplete")
        raise FileNotFoundError("WorldMock project root is incomplete")
    require_locked_as2()
    secret = credential_value(config)
    variables = {
        "WORKSPACE_PATH": str(root),
        "AGENTSOCIETY_LLM_API_KEY": secret,
        "AGENTSOCIETY_LLM_API_BASE": config.base_url,
        "AGENTSOCIETY_LLM_MODEL": config.requested_model,
        "AGENTSOCIETY_CODER_LLM_API_KEY": secret,
        "AGENTSOCIETY_CODER_LLM_API_BASE": config.base_url,
        "AGENTSOCIETY_CODER_LLM_MODEL": config.requested_model,
        "AGENTSOCIETY_EMBEDDING_MODEL": "",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "AGENTSOCIETY_LLM_REQUEST_TIMEOUT": "300",
        "MEM0_TELEMETRY": "False",
        "ANONYMIZED_TELEMETRY": "False",
    }
    os.environ.update(variables)
    _prepend_pythonpath(root)
    _require_loaded_config(config)


def _prepend_pythonpath(root: Path) -> None:
    """@brief 让 Ray worker 从当前精确 checkout 导入 custom 与 worldmock。"""
    current = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    required = [str(root), str(root / "src")]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([*required, *current]))


def _require_loaded_config(config: ModelConfig) -> None:
    """@brief 已加载客户端配置必须与显式端点及模型一致。"""
    from agentsociety2.config.config import Config

    expected = (config.base_url, config.requested_model)
    actual = ((Config.LLM_API_BASE, Config.LLM_MODEL), (Config.CODER_LLM_API_BASE, Config.CODER_LLM_MODEL))
    if any(value != expected for value in actual) or Config.EMBEDDING_MODEL:
        logger.error("AgentSociety2 was imported before the required model configuration")
        raise RuntimeError("AgentSociety2 was imported before the required model configuration")


def _model_identity(config: ModelConfig) -> ModelIdentity:
    """@brief 冻结显式模型端点、独立预算与当前提示和工具身份，不包含凭据。"""
    from custom.agents.life_agent import ACTION_THINKING, ACTION_TOOL_CHOICE, life_tool_schema_sha256
    from worldmock.agents.life_prompt import life_prompt_sha256

    return ModelIdentity(provider=config.provider, base_url=config.base_url, requested_model=config.requested_model,
                         thinking=ACTION_THINKING["thinking"]["type"], tool_choice=ACTION_TOOL_CHOICE,
                         max_retries=config.max_retries, transport_max_retries=config.transport_max_retries,
                         prompt_sha256=life_prompt_sha256(), tool_schema_sha256=life_tool_schema_sha256())


def stage_model_identity(config: ModelConfig, metadata: ModelMetadata,
                         same_second_continuation: bool = True) -> dict[str, Any]:
    """@brief 冻结行动阶段、实际服务容量及每个独立输出校验预算。"""
    from custom.agents.life_agent import ACTION_MAX_TOKENS, ACTION_THINKING, ACTION_TOOL_CHOICE

    identity = config.model_dump(mode="json", exclude={"api_key_env"})
    identity.update({"max_react_turns": config.max_react_turns, "strict": True, "reasoning_effort": None,
                     "model_metadata": metadata.model_dump(mode="json"),
                     "stages": {"action": {"thinking": ACTION_THINKING["thinking"]["type"],
                                           "tool_choice": ACTION_TOOL_CHOICE,
                                           "max_tokens": min(ACTION_MAX_TOKENS, metadata.max_output_tokens),
                                           "same_second_continuation": same_second_continuation}}})
    return validate_stage_model_identity(identity)


def runtime_code_sha256(project_root: Path) -> str:
    """@brief 哈希全部生产 Python 源码、根依赖锁定文件与实际导入的 AS2 包（docs/design/06-agent.md「宿主模型协议」）。

    AS2 文件以 agentsociety2/<相对路径> 为标签，与安装位置无关；AS2 未通过锁定核对时拒绝给出代码身份。
    """
    root = Path(project_root).resolve()
    files = [(path.relative_to(root).as_posix(), path)
             for directory in (root / "src", root / "custom") for path in directory.rglob("*.py")]
    files.extend((name, root / name) for name in ("pyproject.toml", "uv.lock"))
    files.extend((path.relative_to(root).as_posix(), path)
                 for path in (root / "src/worldmock/data").rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".py")
    return _labeled_sha256([*files, *require_locked_as2()])


def require_locked_as2() -> list[tuple[str, Path]]:
    """@brief 核对实际导入的 AS2 包与固定基线加补丁的源码摘要，返回其（标签, 路径）文件表。

    包目录为空、导入失败或摘要不符时以 IntegrityError 拒绝；读取或哈希 AS2 文件时的任何失败（条目缺失、
    不安全或不可读）同样按摘要不符拒绝，只用规格的唯一拒绝文案。run、resume、replay 与 validate 共用。
    """
    files = _as2_source_files()
    try:
        digest = _labeled_sha256(files)
    except (IntegrityError, OSError) as error:
        raise IntegrityError(_AS2_IDENTITY_ERROR) from error
    if digest != AS2_LOCKED_SOURCE_SHA256:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    return files


def _as2_source_files() -> list[tuple[str, Path]]:
    """@brief 列出实际导入的 AS2 包目录下除 __pycache__ 外的全部文件，标签为 agentsociety2/<相对路径>。

    包目录取 importlib.util.find_spec 解析出的唯一目录；解析失败、不是唯一包目录或目录为空即拒绝。
    """
    try:
        spec = importlib.util.find_spec(_AS2_PACKAGE)
    except (ImportError, ValueError) as error:
        raise IntegrityError(_AS2_IDENTITY_ERROR) from error
    locations = list(spec.submodule_search_locations or ()) if spec is not None else []
    if len(locations) != 1:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    package = Path(locations[0]).resolve()
    files = [(f"agentsociety2/{path.relative_to(package).as_posix()}", path)
             for path in package.rglob("*")
             if "__pycache__" not in path.relative_to(package).parts
             and (path.is_symlink() or not path.is_dir())]
    if not files:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    return files


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


def _git_state(project_root: Path) -> tuple[str | None, bool | None]:
    """@brief 返回项目根所在 Git 检出的（HEAD 提交, 是否有未提交改动）；非 Git 检出或 git 失败时二者为 None。

    未提交标志取 git status --porcelain 是否非空（显式含未跟踪文件，不含忽略文件）；二者只记录，不参与恢复比对。
    """
    head = _git_output(project_root, "rev-parse", "HEAD")
    if head is None:
        return None, None
    status = _git_output(project_root, "status", "--porcelain", "--untracked-files=normal")
    if status is None:
        return None, None
    return head.strip().decode("ascii"), bool(status.strip())


def _git_output(project_root: Path, *arguments: str) -> bytes | None:
    """@brief 在项目根执行一条只读 git 命令并返回标准输出；git 不可用或命令失败时返回 None。"""
    command = ["git", "-C", str(project_root), *arguments]
    try:
        result = subprocess.run(command, capture_output=True, check=False,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def creation_inputs(inputs: EnvironmentInputs, compiled: Mapping[int, Any],
                    same_second_continuation: bool = True) -> tuple[tuple[dict[str, Any], ...], list[dict]]:
    """@brief 使用已编译的唯一人物卡生成原生工作区及真实目录输入警告。"""
    persona = inputs.persona
    card, notes = _compiled_parts(compiled.get(persona.agent_id))
    if card is None:
        logger.error("Compiled persona card is missing for agent %s", persona.agent_id)
        raise IntegrityError("Compiled persona card is missing")
    from worldmock.profile.injection import apply_persona_rhythm
    from worldmock.profile.models import persona_card_struct
    from worldmock.profile.textgen import render_card

    card = apply_persona_rhythm(card, persona).card
    profile = persona.model_dump(mode="json")
    profile.update({"id": persona.agent_id, "same_second_continuation": same_second_continuation,
                    "persona_card": render_card(card), "persona_card_struct": persona_card_struct(card),
                    "model_identity": stage_model_identity(inputs.config.model, inputs.model_metadata,
                                                            same_second_continuation)})
    config = {"enable_memory": True, "enable_todo_list": False,
              "max_react_turns": inputs.config.model.max_react_turns, "disabled_skill_ids": [],
              "default_activated_skill_ids": []}
    specs = ({"id": persona.agent_id, "profile": profile, "config": config},)
    return specs, input_warnings(inputs, compiled)


def input_warnings(inputs: EnvironmentInputs, compiled: Mapping[int, Any]) -> list[dict[str, Any]]:
    """@brief 按真实目录计算人物覆盖、内容、作息、一致性和地点名称警告。"""
    from worldmock.profile import injection
    from worldmock.profile.coverage import validate_coverage
    from worldmock.profile.coverage_targets import build_coverage_targets

    persona = inputs.persona
    card, notes = _compiled_parts(compiled.get(persona.agent_id))
    if card is None:
        logger.error("Compiled persona card is missing")
        raise IntegrityError("Compiled persona card is missing")
    targets = build_coverage_targets(inputs)
    return [*injection.content_empty_warnings(persona.agent_id, card, inputs.catalog),
            *injection.coverage_warnings(persona.agent_id, validate_coverage(card, targets)),
            *injection.apply_persona_rhythm(card, persona).warnings,
            *injection.agreement_warnings(persona.agent_id, notes),
            *injection.location_name_warnings(inputs.catalog)]


async def _compile_persona(persona: PersonaConfig, config: ModelConfig,
                           metadata: ModelMetadata, persona_sha256: str) -> Any:
    """@brief 在同一原生 dispatcher 执行三次独立画像抽取，再映射实际世界动力学。"""
    from worldmock.profile.compile import compile_persona
    from worldmock.profile.extractor import extract_persona

    extraction = await extract_persona(persona.background_story, config, metadata)
    return compile_persona(persona, extraction=extraction, persona_sha256=persona_sha256)


def _compiled_parts(result: Any) -> tuple[Any | None, tuple[str, ...]]:
    """@brief 取编译卡片与警告；测试可直接注入经过模型校验的卡片。"""
    from worldmock.profile.models import PersonaCard

    if result is None:
        return None, ()
    if isinstance(result, PersonaCard):
        return result, ()
    return result.card, tuple(result.warnings)


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
    from custom.agents.life_agent import ACTION_MAX_TOKENS, ACTION_THINKING, ACTION_TOOL_CHOICE
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
