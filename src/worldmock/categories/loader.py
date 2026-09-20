"""@brief 冻结品类契约与闭集交换规则 JSON 加载入口。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from worldmock.categories.common import CategoryError
from worldmock.categories.contract import build_category_contract
from worldmock.categories.models import CategoryContract, CategoryContractDocument
from worldmock.categories.rules import (
    ExchangeRuleSet,
    ExchangeRulesDocument,
    build_exchange_rules,
)

CATEGORY_CONTRACT_FILE = "category-contract.json"
EXCHANGE_RULES_FILE = "exchange-rules.json"


def load_category_contract(path: Path | str) -> CategoryContract:
    """@brief 从单个 JSON 文件加载并解析冻结品类契约。

    :param path: `category-contract.json` 路径。
    :returns: 已完成结构、来源、树和值校验的冻结契约。
    :raises CategoryError: 文件缺失、JSON、Schema 或语义无效。
    """
    file_path = Path(path)
    payload = _load_json_document(file_path, "CATEGORY_CONTRACT")
    try:
        document = CategoryContractDocument.model_validate(payload)
    except ValidationError as error:
        raise CategoryError(
            "CATEGORY_CONTRACT_SCHEMA_INVALID", f"{file_path}: {error}",
        ) from error
    return build_category_contract(document, payload)


def load_exchange_rules(
    path: Path | str,
    contract: CategoryContract,
) -> ExchangeRuleSet:
    """@brief 从单个 JSON 文件加载与指定契约绑定的闭集规则。

    :param path: `exchange-rules.json` 路径。
    :param contract: 规则声明绑定的冻结品类契约。
    :returns: 已完成来源、算子和配对引用校验的规则集合。
    :raises CategoryError: 文件、Schema、版本或引用无效。
    """
    file_path = Path(path)
    payload = _load_json_document(file_path, "EXCHANGE_RULES")
    try:
        document = ExchangeRulesDocument.model_validate(payload)
    except ValidationError as error:
        raise CategoryError("EXCHANGE_RULES_SCHEMA_INVALID", f"{file_path}: {error}") from error
    return build_exchange_rules(document, payload, contract)


def _load_json_document(path: Path, prefix: str) -> dict[str, Any]:
    """@brief 读取一个 JSON 对象并拒绝同对象内重复键。"""
    if not path.is_file():
        raise CategoryError(f"{prefix}_MISSING", f"{path} does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CategoryError(f"{prefix}_MALFORMED", f"{path}: {error}") from error
    if not isinstance(payload, dict):
        raise CategoryError(f"{prefix}_MALFORMED", f"{path} root must be a JSON object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """@brief JSON object_pairs_hook：重复键立即失败。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CategoryError("CATEGORY_JSON_DUPLICATE_KEY", f"duplicate JSON key {key}")
        result[key] = value
    return result
