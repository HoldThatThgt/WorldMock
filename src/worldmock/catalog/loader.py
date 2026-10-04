"""@brief 冻结目录加载、内容身份校验与原始对象编译。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from pydantic import ValidationError

from worldmock.categories import (
    CategoryError,
    compute_catalog_identity,
    load_category_contract,
    load_exchange_rules,
    validate_rule_resource_domains,
)
from worldmock.catalog.compiler import compile_exchange_object
from worldmock.catalog.models import (
    Catalog,
    CatalogEntryKind,
    CategoryError,
    CatalogManifest,
    CatalogModel,
    ObjectDeclaration,
    ResourceDefinition,
    Tombstone,
)

RESOURCE_FILE = "resources.jsonl"
OBJECT_FILE = "objects.jsonl"
TOMBSTONE_FILE = "overrides/tombstones.jsonl"
MANIFEST_FILE = "manifest.json"
RULES_DIR = "rules"
CATEGORY_CONTRACT_FILE = "category-contract.json"
EXCHANGE_RULES_FILE = "exchange-rules.json"
TRAVEL_TIME_FILE = "rules/travel-time.toml"

_Entry = TypeVar("_Entry", bound=CatalogModel)


@dataclass(frozen=True)
class _CatalogContent:
    """@brief 目录构造前已校验的内容集合。"""

    resources: dict
    raw_objects: dict
    objects: dict
    tombstones: dict
    contract: object
    rules: object
    travel_bytes: bytes


def load_catalog(catalog_dir: Path | str) -> Catalog:
    """@brief 加载摘要绑定的契约、规则、资源和 raw 对象并编译有效视图。

    :param catalog_dir: 完整冻结目录路径。
    :returns: 运行期只读目录视图。
    :raises CategoryError: 任一摘要、Schema、来源、规则或对象无效。
    """
    directory = Path(catalog_dir).resolve()
    try:
        return _load_catalog(directory)
    except CategoryError as error:
        raise CategoryError(error.code, error.message) from error


def load_object_declarations(path: Path | str) -> tuple[ObjectDeclaration, ...]:
    """@brief 读取 objects.jsonl 的完整追加历史并校验逐身份版本递增。

    :param path: raw 对象 JSONL 文件。
    :returns: 按文件顺序排列的严格对象声明。
    :raises CategoryError: JSON、Schema 或版本链无效。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise CategoryError("CATALOG_OBJECTS_MISSING", f"{file_path} does not exist")
    entries = _parse_jsonl(file_path, ObjectDeclaration)
    _validate_versions(entries, "object_id", "version", file_path)
    return tuple(entries)


def _load_catalog(directory: Path) -> Catalog:
    """@brief 执行目录加载的依赖顺序，并在末尾一次构造冻结视图。"""
    manifest, manifest_payload = _load_manifest(directory)
    identity = compute_catalog_identity(directory, manifest_payload)
    _validate_identity(manifest, identity)
    contract = load_category_contract(directory / CATEGORY_CONTRACT_FILE)
    rules = load_exchange_rules(directory / EXCHANGE_RULES_FILE, contract)
    _validate_contract_identity(manifest, contract.contract_version)
    resources = _load_latest_entries(directory / RESOURCE_FILE, ResourceDefinition, "resource_id")
    validate_rule_resource_domains(rules, resources)
    raw_objects = _latest_objects(directory / OBJECT_FILE)
    objects = {
        object_id: compile_exchange_object(declaration, contract, rules)
        for object_id, declaration in raw_objects.items()
    }
    tombstones = _load_tombstones(directory / TOMBSTONE_FILE)
    _reject_tombstone_overwrites(resources, raw_objects, tombstones)
    travel_bytes = _optional_bytes(directory / TRAVEL_TIME_FILE)
    content = _CatalogContent(
        resources, raw_objects, objects, tombstones, contract, rules, travel_bytes,
    )
    return _catalog_view(directory, manifest, content)


def _catalog_view(
    directory: Path,
    manifest: CatalogManifest,
    content: _CatalogContent,
) -> Catalog:
    """@brief 用只读代理包装目录映射并复制清单身份。"""
    return Catalog(
        version_major=manifest.version_major,
        version_minor=manifest.version_minor,
        category_contract_version=manifest.category_contract_version,
        category_contract_sha256=manifest.category_contract_sha256,
        exchange_rules_sha256=manifest.exchange_rules_sha256,
        catalog_content_sha256=manifest.catalog_content_sha256,
        resources=MappingProxyType(content.resources),
        raw_objects=MappingProxyType(content.raw_objects),
        objects=MappingProxyType(content.objects),
        tombstones=MappingProxyType(content.tombstones),
        category_contract=content.contract,
        exchange_rules=content.rules,
        source_dir=directory,
        travel_time_rules_bytes=content.travel_bytes,
        category_requirements=manifest.category_requirements,
    )


def _load_manifest(directory: Path) -> tuple[CatalogManifest, dict]:
    """@brief 读取目录清单并同时保留原始载荷供内容摘要使用。"""
    path = directory / MANIFEST_FILE
    if not path.is_file():
        raise CategoryError("CATALOG_MANIFEST_MISSING", f"{path} does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CatalogManifest.model_validate(payload), payload
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise CategoryError("CATALOG_MALFORMED_MANIFEST", f"{path}: {error}") from error


def _validate_identity(manifest: CatalogManifest, identity) -> None:
    """@brief 要求清单四个身份字段与当前字节计算结果完全一致。"""
    expected = identity.manifest_fields()
    actual = {name: getattr(manifest, name) for name in expected}
    if actual != expected:
        changed = sorted(name for name in expected if actual[name] != expected[name])
        raise CategoryError(
            "CATALOG_CONTENT_IDENTITY_MISMATCH", f"catalog identity fields mismatch: {changed}",
        )


def _validate_contract_identity(manifest: CatalogManifest, contract_version: int) -> None:
    """@brief 要求解析契约版本与清单绑定版本一致。"""
    if manifest.category_contract_version != contract_version:
        raise CategoryError(
            "CATALOG_CONTRACT_VERSION_MISMATCH", "manifest binds another category contract version",
        )


def _latest_objects(path: Path) -> dict[str, ObjectDeclaration]:
    """@brief 返回 raw 对象追加历史中每个身份的末版本。"""
    return {entry.object_id: entry for entry in load_object_declarations(path)}


def _load_latest_entries(path: Path, model: type[_Entry], id_field: str) -> dict[str, _Entry]:
    """@brief 解析资源 JSONL 并返回逐身份末版本。"""
    if not path.is_file():
        raise CategoryError("CATALOG_RESOURCE_FILE_MISSING", f"{path} does not exist")
    entries = _parse_jsonl(path, model)
    _validate_versions(entries, id_field, "version", path)
    return {str(getattr(entry, id_field)): entry for entry in entries}


def _parse_jsonl(path: Path, model: type[_Entry]) -> list[_Entry]:
    """@brief 逐行解析 JSONL，并把 Pydantic 错误转换为稳定目录错误。"""
    entries: list[_Entry] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
            entries.append(model.model_validate(payload))
        except (json.JSONDecodeError, ValidationError) as error:
            raise CategoryError(
                "CATALOG_SCHEMA_INVALID", f"{path.name} line {line_number}: {error}",
            ) from error
    return entries


def _validate_versions(entries: list, id_field: str, version_field: str, path: Path) -> None:
    """@brief 要求同一身份的追加版本严格递增。"""
    versions: dict[str, int] = {}
    for entry in entries:
        identity = str(getattr(entry, id_field))
        version = int(getattr(entry, version_field))
        previous = versions.get(identity)
        if previous is not None and version <= previous:
            raise CategoryError(
                "CATALOG_VERSION_NOT_MONOTONIC",
                f"{path.name}: {identity} version {version} does not exceed previous {previous}",
            )
        versions[identity] = version


def _load_tombstones(path: Path) -> dict[str, Tombstone]:
    """@brief 解析可选墓碑追加历史并返回逐身份末版本。"""
    if not path.exists():
        return {}
    entries = _parse_jsonl(path, Tombstone)
    _validate_versions(entries, "target_id", "at_version", path)
    return {entry.target_id: entry for entry in entries}


def _reject_tombstone_overwrites(
    resources: dict[str, ResourceDefinition],
    objects: dict[str, ObjectDeclaration],
    tombstones: dict[str, Tombstone],
) -> None:
    """@brief 拒绝墓碑之后追加更高版本活条目。"""
    namespaces = {CatalogEntryKind.RESOURCE: resources, CatalogEntryKind.OBJECT: objects}
    for target_id, tombstone in tombstones.items():
        live = namespaces[tombstone.kind].get(target_id)
        if live is not None and live.version > tombstone.at_version:
            raise CategoryError(
                "CATALOG_TOMBSTONE_CONFLICT",
                f"{tombstone.kind.value} {target_id} has version beyond its tombstone",
            )


def _optional_bytes(path: Path) -> bytes:
    """@brief 在目录加载时捕获可选 typed travel-time 规则的不可变字节。"""
    return path.read_bytes() if path.is_file() else b""
