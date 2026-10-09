"""@brief 运行、恢复与重放共享同一份冻结环境内容身份核验。"""

from __future__ import annotations
from worldmock.footprints.store import FootprintStore

import logging
from pathlib import Path
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.environment import EnvironmentInputs, environment_identity


def require_environment_identity(manifest: dict[str, Any], inputs: EnvironmentInputs) -> None:
    """@brief 在读取已有运行状态前核对目录、人物、配置与权威模型容量。"""
    expected = environment_identity(inputs)
    valid = (manifest.get("schema_version") == 5 and manifest.get("environment_identity") == expected
             and manifest.get("persona_sha256") == inputs.persona_sha256
             and manifest.get("config_sha256") == inputs.config_sha256
             and manifest.get("model_metadata") == inputs.model_metadata.model_dump(mode="json"))
    if not valid:
        logging.error("Runtime environment identity mismatch")
        raise IntegrityError("Runtime environment identity mismatch")


def open_environment_validated_store(run_dir: Path, inputs: EnvironmentInputs) -> Any:
    """@brief 打开既有存储后先核对环境身份，失败时立即关闭连接。"""

    store = FootprintStore(run_dir)
    try:
        require_environment_identity(store.manifest, inputs)
    except Exception:
        store.close()
        raise
    return store
