"""@brief 运行、恢复与重放共享同一份冻结环境内容身份核验。"""

from __future__ import annotations

import logging
from typing import Any

from worldmock.foundation.errors import IntegrityError
from worldmock.configuration.inputs.environment import EnvironmentInputs, environment_identity
from worldmock.state.identity import RUN_MANIFEST_SCHEMA_VERSION


def require_environment_identity(manifest: dict[str, Any], inputs: EnvironmentInputs) -> None:
    """@brief 在读取已有运行状态前核对运行清单结构版本、目录、人物与配置；模型元数据由清单本身提供
    （model_endpoint.frozen_metadata_loader），不再比较。"""
    expected = environment_identity(inputs)
    valid = (manifest.get("schema_version") == RUN_MANIFEST_SCHEMA_VERSION
             and manifest.get("environment_identity") == expected
             and manifest.get("persona_sha256") == inputs.persona_sha256
             and manifest.get("config_sha256") == inputs.config_sha256)
    if not valid:
        logging.error("Runtime environment identity mismatch")
        raise IntegrityError("Runtime environment identity mismatch")
