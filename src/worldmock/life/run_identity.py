"""@brief 运行、恢复与重放共享同一份冻结环境内容身份核验。"""

from __future__ import annotations
from worldmock.footprints.store import FootprintStore

import logging
from pathlib import Path
from typing import Any

from worldmock.errors import IntegrityError


def environment_identity(semantics: Any) -> dict[str, str | int]:
    """@brief 从已校验目录提取运行必须冻结的五个环境身份字段。"""
    catalog = semantics.catalog
    return {
        "environment_id": catalog.manifest.environment_id,
        "environment_version": catalog.manifest.environment_version,
        "rules_version": catalog.manifest.rules_version,
        "catalog_content_sha256": catalog.manifest.catalog_content_sha256,
        "map_sha256": catalog.map_sha256,
    }


def require_environment_identity(manifest: dict[str, Any], semantics: Any) -> None:
    """@brief 在读取已有运行状态前拒绝缺失身份或同版本异内容。"""
    expected = environment_identity(semantics)
    if manifest.get("schema_version") != 3 or manifest.get("environment_identity") != expected:
        logging.error("Runtime environment identity mismatch")
        raise IntegrityError("Runtime environment identity mismatch")


def open_environment_validated_store(run_dir: Path, semantics: Any) -> Any:
    """@brief 打开既有存储后先核对环境身份，失败时立即关闭连接。"""

    store = FootprintStore(run_dir)
    try:
        require_environment_identity(store.manifest, semantics)
    except Exception:
        store.close()
        raise
    return store
