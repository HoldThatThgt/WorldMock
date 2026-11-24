"""@brief 运行、恢复与重放共享同一份冻结环境内容身份核验。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from worldmock.configuration.inputs.environment import EnvironmentInputs, environment_identity
from worldmock.foundation.errors import IntegrityError
from worldmock.state.identity import RUN_MANIFEST_SCHEMA_VERSION

_LOGGER = logging.getLogger(__name__)


def _normalized_sha256s(raw: Any) -> dict[int, str] | None:
    """@brief 把运行清单中的字符串或整型人物键规范化为整型编号到摘要的映射。"""
    if not isinstance(raw, Mapping):
        return None
    try:
        return {int(key): str(value) for key, value in raw.items()}
    except (TypeError, ValueError):
        return None


def require_environment_identity(manifest: dict[str, Any], inputs: EnvironmentInputs) -> None:
    """@brief 在读取已有运行状态前核对运行清单结构版本、目录、全部人物摘要与配置摘要。"""
    expected = environment_identity(inputs)
    valid = (manifest.get("schema_version") == RUN_MANIFEST_SCHEMA_VERSION
             and manifest.get("environment_identity") == expected
             and _normalized_sha256s(manifest.get("persona_sha256s")) == dict(inputs.persona_sha256s)
             and manifest.get("config_sha256") == inputs.config_sha256)
    if not valid:
        _LOGGER.error("Runtime environment identity mismatch")
        raise IntegrityError("Runtime environment identity mismatch")
