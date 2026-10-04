"""@brief 运行、恢复与重放共享同一份冻结品类内容身份核验。"""

from __future__ import annotations
from worldmock.footprints.store import FootprintStore

from pathlib import Path
from typing import Any

from worldmock.errors import IntegrityError


def category_identity(semantics: Any) -> dict[str, str | int]:
    """@brief 从已校验目录提取运行必须冻结的四个品类身份字段。"""
    catalog = semantics.catalog
    return {
        "category_contract_version": catalog.category_contract_version,
        "category_contract_sha256": catalog.category_contract_sha256,
        "exchange_rules_sha256": catalog.exchange_rules_sha256,
        "catalog_content_sha256": catalog.catalog_content_sha256,
    }


def require_category_identity(manifest: dict[str, Any], semantics: Any) -> None:
    """@brief 在读取已有运行状态前拒绝缺失身份或同版本异内容。"""
    expected = category_identity(semantics)
    if {key: manifest.get(key) for key in expected} != expected:
        raise IntegrityError("Runtime category contract identity mismatch")


def open_category_validated_store(run_dir: Path, semantics: Any) -> Any:
    """@brief 打开既有存储后先核对品类身份，失败时立即关闭连接。"""

    store = FootprintStore(run_dir)
    try:
        require_category_identity(store.manifest, semantics)
    except Exception:
        store.close()
        raise
    return store
