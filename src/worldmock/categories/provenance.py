"""@brief 静态数值来源的 JSON Pointer 精确覆盖校验。"""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Iterable

from jsonpointer import JsonPointer, JsonPointerException

from worldmock.categories.common import CategoryError, SourceRecord


def validate_numeric_sources(
    document: dict[str, Any],
    sources: Iterable[SourceRecord],
    excluded_roots: frozenset[str],
) -> None:
    """@brief 验证每个模拟静态数值叶恰好被一份来源记录覆盖。

    来源路径可指向一个父对象或数组；校验会展开其下全部数值叶。版本、索引和
    来源记录元数据由调用方通过 ``excluded_roots`` 排除。

    :param document: 已完成 Pydantic 校验的原始 JSON 文档。
    :param sources: 文档声明的来源记录。
    :param excluded_roots: 不参与静态数值追溯的根字段名。
    :raises CategoryError: 路径无效、漏覆盖或重复覆盖。
    """
    expected = _numeric_leaves(document, "", excluded_roots)
    coverage: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        for pointer in source.paths:
            target = _resolve_pointer(document, pointer, source.source_id)
            leaves = _numeric_leaves(target, pointer, frozenset())
            for leaf in leaves:
                coverage[leaf].append(source.source_id)
    _reject_coverage_mismatch(expected, coverage)


def _numeric_leaves(value: Any, path: str, excluded_roots: frozenset[str]) -> set[str]:
    """@brief 递归收集非布尔有限性另行校验的 JSON 数值叶路径。"""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return set()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise CategoryError("CATEGORY_NUMBER_INVALID", f"{path or '/'} must be finite")
        return {path or "/"}
    if isinstance(value, dict):
        return _mapping_numeric_leaves(value, path, excluded_roots)
    if isinstance(value, (list, tuple)):
        return _sequence_numeric_leaves(value, path)
    return set()


def _mapping_numeric_leaves(
    value: dict[str, Any], path: str, excluded_roots: frozenset[str],
) -> set[str]:
    """@brief 收集映射中的数值叶，根级排除元数据字段。"""
    leaves: set[str] = set()
    for key, item in value.items():
        if not path and key in excluded_roots:
            continue
        child = f"{path}/{_escape(key)}"
        leaves.update(_numeric_leaves(item, child, frozenset()))
    return leaves


def _sequence_numeric_leaves(value: list[Any] | tuple[Any, ...], path: str) -> set[str]:
    """@brief 收集数组中的数值叶；数组索引仅定位，不单独追溯。"""
    leaves: set[str] = set()
    for index, item in enumerate(value):
        leaves.update(_numeric_leaves(item, f"{path}/{index}", frozenset()))
    return leaves


def _resolve_pointer(document: dict[str, Any], pointer: str, source_id: str) -> Any:
    """@brief 解析来源 JSON Pointer，并把库异常转成稳定错误码。"""
    try:
        return JsonPointer(pointer).resolve(document)
    except JsonPointerException as error:
        raise CategoryError(
            "CATEGORY_SOURCE_PATH_UNKNOWN",
            f"source {source_id} path {pointer} does not resolve: {error}",
        ) from error


def _reject_coverage_mismatch(expected: set[str], coverage: dict[str, list[str]]) -> None:
    """@brief 拒绝漏覆盖、重复覆盖和覆盖范围外的数值路径。"""
    unknown = sorted(set(coverage) - expected)
    if unknown:
        raise CategoryError(
            "CATEGORY_SOURCE_PATH_OUTSIDE_NUMERIC_SCOPE",
            f"source paths cover excluded numeric values: {unknown}",
        )
    missing = sorted(expected - set(coverage))
    if missing:
        raise CategoryError("CATEGORY_NUMERIC_SOURCE_MISSING", f"numeric values lack source: {missing}")
    duplicates = {path: ids for path, ids in coverage.items() if len(ids) != 1}
    if duplicates:
        raise CategoryError(
            "CATEGORY_NUMERIC_SOURCE_DUPLICATE",
            f"numeric values have multiple sources: {duplicates}",
        )


def _escape(token: str) -> str:
    """@brief 按 RFC 6901 转义一个 JSON Pointer 路径段。"""
    return token.replace("~", "~0").replace("/", "~1")
