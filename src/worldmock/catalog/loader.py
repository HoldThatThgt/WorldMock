"""@brief 冻结目录的加载与结构校验（JSONL 条目 + TOML 公式规则）。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from worldmock.catalog.models import (
    Catalog,
    CatalogEntryKind,
    CatalogError,
    CatalogManifest,
    CatalogModel,
    ExchangeObject,
    FormulaBranch,
    FormulaDeclaration,
    Provenance,
    ResourceDefinition,
    Tombstone,
)

RESOURCE_FILE = "resources.jsonl"
OBJECT_FILE = "objects.jsonl"
RULES_DIR = "rules"
TOMBSTONE_FILE = "overrides/tombstones.jsonl"
MANIFEST_FILE = "manifest.json"
FORMULA_AMOUNT_KEY = "amount_fen"

_Entry = TypeVar("_Entry", bound=CatalogModel)


def load_catalog(catalog_dir: Path | str) -> Catalog:
    """@brief 从目录路径加载冻结视图并执行结构校验。

    校验：JSONL 可解析且过模型 Schema、同一 id 的 version 沿文件序严格递增、
    墓碑之后不允许出现更高 version 的活条目；公式声明解析自 rules/ 下的
    TOML 公式文件（travel-fare 结构），映射规则文件跳过不解析。

    :param catalog_dir: 含 manifest.json、两类 JSONL 与 rules/ 的目录。
    :returns: 各映射只读的冻结目录视图。
    :raises CatalogError: 任一结构校验失败时以稳定错误码失败。
    """
    directory = Path(catalog_dir)
    manifest = _load_manifest(directory)
    resources = _load_latest_entries(directory / RESOURCE_FILE, ResourceDefinition, "resource_id")
    objects = _load_latest_entries(directory / OBJECT_FILE, ExchangeObject, "object_id")
    formulas = _load_formulas(directory / RULES_DIR)
    tombstones = _load_tombstones(directory / TOMBSTONE_FILE)
    _reject_tombstone_overwrites(resources, objects, formulas, tombstones)
    return Catalog.build(manifest, resources, objects, formulas, tombstones)


def _load_manifest(directory: Path) -> CatalogManifest:
    """@brief 读取并校验目录版本清单。"""
    path = directory / MANIFEST_FILE
    if not path.is_file():
        raise CatalogError("CATALOG_MANIFEST_MISSING", f"{path} does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CatalogManifest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as error:
        raise CatalogError("CATALOG_MALFORMED_MANIFEST", f"{path}: {error}") from error


def _load_latest_entries(path: Path, model: type[_Entry], id_field: str) -> dict[str, _Entry]:
    """@brief 解析追加式 JSONL，返回每个 id 的末行条目。

    :param path: 条目文件，缺失视为空目录。
    :param model: 条目的 Pydantic 模型。
    :param id_field: 条目身份字段名。
    :returns: 以 id 为键的当前（末行胜出）条目表。
    """
    if not path.exists():
        return {}
    latest: dict[str, _Entry] = {}
    versions: dict[str, int] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        entry = _parse_entry(path, line_number, raw_line, model)
        entry_id = str(getattr(entry, id_field))
        previous = versions.get(entry_id)
        if previous is not None and entry.version <= previous:
            raise CatalogError(
                "CATALOG_VERSION_NOT_MONOTONIC",
                f"{path.name} line {line_number}: {entry_id} version {entry.version}"
                f" does not exceed previous {previous}",
            )
        versions[entry_id] = entry.version
        latest[entry_id] = entry
    return latest


def _parse_entry(path: Path, line_number: int, raw_line: str, model: type[_Entry]) -> _Entry:
    """@brief 解析并 Schema 校验单行 JSONL 条目。"""
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError as error:
        raise CatalogError(
            "CATALOG_MALFORMED_JSON",
            f"{path.name} line {line_number}: {error}",
        ) from error
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise CatalogError(
            "CATALOG_SCHEMA_INVALID",
            f"{path.name} line {line_number}: {error}",
        ) from error


def _load_formulas(rules_dir: Path) -> dict[str, FormulaDeclaration]:
    """@brief 解析 rules/ 下的 TOML 文件，公式声明文件入表、映射规则跳过。

    :param rules_dir: 规则目录，缺失视为无公式声明。
    :returns: 以 formula_id（文件名去后缀）为键的公式声明表。
    :raises CatalogError: TOML 不可解析或结构不合约时以稳定错误码失败。
    """
    if not rules_dir.is_dir():
        return {}
    formulas: dict[str, FormulaDeclaration] = {}
    for path in sorted(rules_dir.glob("*.toml")):
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise CatalogError(
                "CATALOG_MALFORMED_TOML",
                f"{path}: {error}",
            ) from error
        if "formula" not in payload:
            continue
        formulas[path.stem] = _formula_from_toml(path, payload)
    return formulas


def _formula_from_toml(path: Path, payload: dict) -> FormulaDeclaration:
    """@brief 把 travel-fare 结构的 TOML 载荷解析成公式声明。

    结构约定：[formula.<分支>] 表内唯一键 amount_fen（整数为字面值、字符串为
    表达式）；[params] 参数名→输入路径；[provenance] part 名→溯源字段；
    顶层 version 可缺省为 1。输出单位取 amount_fen 键的后缀（fen）。
    """
    raw_branches = payload["formula"]
    if not isinstance(raw_branches, dict) or not raw_branches:
        raise CatalogError(
            "CATALOG_MALFORMED_TOML",
            f"{path}: [formula] must contain at least one branch table",
        )
    branches: dict[str, FormulaBranch] = {}
    for branch_name, table in raw_branches.items():
        if not isinstance(table, dict) or set(table) != {FORMULA_AMOUNT_KEY}:
            raise CatalogError(
                "CATALOG_MALFORMED_TOML",
                f"{path}: [formula.{branch_name}] must declare exactly '{FORMULA_AMOUNT_KEY}'",
            )
        branches[branch_name] = FormulaBranch(amount=table[FORMULA_AMOUNT_KEY])
    raw_provenance = payload.get("provenance", {})
    if not isinstance(raw_provenance, dict):
        raise CatalogError(
            "CATALOG_MALFORMED_TOML",
            f"{path}: [provenance] must be a table",
        )
    try:
        provenance = tuple(
            Provenance.model_validate({"part": part, **fields})
            for part, fields in raw_provenance.items()
        )
        declaration = FormulaDeclaration(
            formula_id=path.stem,
            unit=FORMULA_AMOUNT_KEY.removeprefix("amount_"),
            branches=branches,
            params=payload.get("params", {}),
            provenance=provenance,
            version=payload.get("version", 1),
        )
    except ValidationError as error:
        raise CatalogError(
            "CATALOG_SCHEMA_INVALID",
            f"{path}: {error}",
        ) from error
    return declaration


def _load_tombstones(path: Path) -> dict[str, Tombstone]:
    """@brief 解析追加式墓碑文件，返回每个 target_id 的末行墓碑。"""
    if not path.exists():
        return {}
    latest: dict[str, Tombstone] = {}
    versions: dict[str, int] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        tombstone = _parse_entry(path, line_number, raw_line, Tombstone)
        previous = versions.get(tombstone.target_id)
        if previous is not None and tombstone.at_version <= previous:
            raise CatalogError(
                "CATALOG_VERSION_NOT_MONOTONIC",
                f"{path.name} line {line_number}: {tombstone.target_id} at_version"
                f" {tombstone.at_version} does not exceed previous {previous}",
            )
        versions[tombstone.target_id] = tombstone.at_version
        latest[tombstone.target_id] = tombstone
    return latest


def _reject_tombstone_overwrites(
    resources: dict[str, ResourceDefinition],
    objects: dict[str, ExchangeObject],
    formulas: dict[str, FormulaDeclaration],
    tombstones: dict[str, Tombstone],
) -> None:
    """@brief 拒绝墓碑之后又追加的更高 version 活条目。"""
    namespaces = {
        CatalogEntryKind.RESOURCE: resources,
        CatalogEntryKind.OBJECT: objects,
        CatalogEntryKind.FORMULA: formulas,
    }
    for target_id, tombstone in tombstones.items():
        live = namespaces[tombstone.kind].get(target_id)
        if live is not None and live.version > tombstone.at_version:
            raise CatalogError(
                "CATALOG_TOMBSTONE_CONFLICT",
                f"{tombstone.kind.value} {target_id} has live version {live.version}"
                f" beyond tombstone at_version {tombstone.at_version}",
            )
