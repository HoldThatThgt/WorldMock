"""@brief 品类影响检查、真实触达统计与新目录原子发布。"""

from __future__ import annotations
from worldmock.categories.common import CategoryError
from worldmock.categories.contract import validate_category_attributes
from worldmock.categories.identity import refresh_manifest_identity
from worldmock.life.world_setup import load_runtime_scenario

import ctypes
import errno
import json
import logging
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import CategoryError
from worldmock.footprints.store import FootprintStore

_LOG = logging.getLogger(__name__)
_PAIR_FIELDS = ("actor_category_id", "target_category_id", "action")


def check_catalog(directory: Path, queries=(), run_dir: Path | None = None) -> dict:
    """@brief 严格加载目录并报告查询配对，未定义结果只报告不拒绝。"""
    catalog = load_catalog(directory)
    contract = _read_json(Path(directory) / "category-contract.json")
    requested = list(queries) if isinstance(queries, (list, tuple)) else None
    if requested is None:
        _reject("CATEGORY_QUERY_INVALID", "Pairing queries must be a JSON array")
    pairings = [_pair_report(catalog, item) for item in requested]
    return {
        "catalog": str(Path(directory).resolve()),
        "identity": _read_json(Path(directory) / "manifest.json"),
        "category_count": len(contract["categories"]),
        "object_count": len(catalog.objects),
        "explicit_pairing_counts": dict(Counter(item["state"] for item in contract["pairings"])),
        "queries": pairings,
        "touches": category_touches(run_dir) if run_dir is not None else [],
    }


def refinement_report(source: Path, candidate: Path, parent_category: str,
                      run_dir: Path | None = None) -> dict:
    """@brief 对照两份真实目录列出细化影响和唯一或歧义建议，不修改对象归属。"""
    old, new = load_catalog(source), load_catalog(candidate)
    old.category_contract.require_category(parent_category)
    new.category_contract.require_category(parent_category)
    children = sorted(category.category_id for category in new.category_contract.categories.values()
                      if category.parent_id == parent_category)
    if not children:
        _reject("CATEGORY_REFINEMENT_EMPTY", "Refinement must declare child categories")
    affected = [_object_suggestion(obj, new.category_contract, children)
                for obj in sorted(old.objects.values(), key=lambda item: item.object_id)
                if old.category_contract.is_descendant(obj.category_id, parent_category)]
    old_contract = _read_json(Path(source) / "category-contract.json")
    new_contract = _read_json(Path(candidate) / "category-contract.json")
    old_rules = _read_json(Path(source) / "exchange-rules.json")["rules"]
    new_rules = _read_json(Path(candidate) / "exchange-rules.json")["rules"]
    touches = category_touches(run_dir) if run_dir is not None else []
    return {
        "parent_category": parent_category,
        "source_version": old.category_contract_version,
        "candidate_version": new.category_contract_version,
        "objects": affected,
        "rule_changes": _declaration_changes(old_rules, new_rules, ("rule_id",)),
        "pairing_changes": _declaration_changes(old_contract["pairings"], new_contract["pairings"], _PAIR_FIELDS),
        "touches": touches,
        "refinement_signals": _refinement_signals(touches, old),
    }


def _object_suggestion(obj, contract, children: list[str]) -> dict:
    """@brief 用旧对象的有效属性检验子品类约束，保留全部匹配而不任选一个。"""

    matches = []
    for category in children:
        try:
            validate_category_attributes(contract, category, obj.attributes)
        except CategoryError:
            continue
        matches.append(category)
    status = "unique" if len(matches) == 1 else "ambiguous" if matches else "unmatched"
    return {"object_id": obj.object_id, "current_category": obj.category_id,
            "candidates": matches, "status": status,
            "suggested_category": matches[0] if len(matches) == 1 else None}


def _declaration_changes(before: list[dict], after: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """@brief 按稳定身份列出声明新增、移除和正文变化，保留两侧完整证据。"""
    old = {tuple(item[key] for key in keys): item for item in before}
    new = {tuple(item[key] for key in keys): item for item in after}
    result = []
    for identity in sorted(old.keys() | new.keys()):
        if old.get(identity) == new.get(identity):
            continue
        kind = "added" if identity not in old else "removed" if identity not in new else "changed"
        result.append({"identity": dict(zip(keys, identity)), "change": kind,
                       "before": old.get(identity), "after": new.get(identity)})
    return result


def _refinement_signals(touches: list[dict], catalog) -> list[dict]:
    """@brief 报告反复仅命中同一祖先规则的品类及原始次数，不设置自动拆分阈值。"""
    grouped: dict[str, list[dict]] = {}
    for row in touches:
        grouped.setdefault(row["category_id"], []).append(row)
    signals = []
    for category, rows in sorted(grouped.items()):
        rules = {row["rule_id"] for row in rows}
        if len(rules) != 1:
            continue
        rule_id = next(iter(rules))
        rule = catalog.exchange_rules.require(rule_id)
        if rule.category_id != category and catalog.category_contract.is_descendant(category, rule.category_id):
            signals.append({"category_id": category, "rule_id": rule_id,
                            "count": sum(row["count"] for row in rows)})
    return signals


def category_touches(run_dir: Path) -> list[dict]:
    """@brief 只按每个 action_result 的实际规则命中汇总次数，不重复统计活动完成事件。"""
    store = FootprintStore(run_dir)
    counts: Counter = Counter()
    try:
        for event in store.read_events(store.last_event_seq):
            if event.event_type != "action_result":
                continue
            matches = event.data.get("category_matches", [])
            for match in matches:
                counts[(match["category_id"], match["rule_id"], match["action"])] += 1
    finally:
        store.close()
    return [{"category_id": category, "rule_id": rule, "action": action, "count": count}
            for (category, rule, action), count in sorted(counts.items())]


def publish_catalog(source: Path, candidate: Path, destination: Path,
                    assignments: dict[str, str], scenarios: tuple[Path, ...]) -> dict:
    """@brief 审核新版本并在独立临时目录完成全部场景准入后公开，不改源目录。"""
    source, candidate = Path(source).resolve(), Path(candidate).resolve()
    destination = Path(destination).absolute()
    destination = destination.parent.resolve() / destination.name
    _publication_paths(source, candidate, destination)
    source_catalog = load_catalog(source)
    _validate_publication_request(source, candidate, assignments, scenarios)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".category-publish-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "catalog"
        try:
            shutil.copytree(candidate, staged)
            _apply_assignments(source, staged, assignments)
            refresh_manifest_identity(staged)
            staged_catalog = load_catalog(staged)
            if (staged_catalog.version_major, staged_catalog.version_minor) <= (
                    source_catalog.version_major, source_catalog.version_minor):
                _reject("CATEGORY_VERSION_NOT_INCREMENTED", "Published catalog version must increase")
            _admit_scenarios(staged, scenarios)
            identity = _read_json(staged / "manifest.json")
            _rename_exclusive(staged, destination)
        except Exception:
            _LOG.exception("Category publication failed")
            raise
    return {"published": str(destination), "identity": identity,
            "assignments": dict(sorted(assignments.items())), "scenarios": [str(path) for path in scenarios]}


def _publication_paths(source: Path, candidate: Path, destination: Path) -> None:
    """@brief 拒绝覆盖发布和输入目录嵌套写入，确保临时文件不进入源目录。"""
    if destination.exists() or destination.is_symlink():
        _reject("CATEGORY_DESTINATION_EXISTS", "Publication destination already exists")
    if not source.is_dir() or not candidate.is_dir():
        _reject("CATEGORY_DIRECTORY_MISSING", "Source and candidate catalogs must exist")
    if destination.is_relative_to(source) or destination.is_relative_to(candidate):
        _reject("CATEGORY_DESTINATION_NESTED", "Publication destination must be outside input catalogs")
    if any(path.is_symlink() for path in candidate.rglob("*")):
        _reject("CATEGORY_SYMLINK_FORBIDDEN", "Candidate catalog must contain regular files")


def _rename_exclusive(source: Path, destination: Path) -> None:
    """@brief 使用系统排他重命名原子公开目录，任何已有目标均不得被覆盖。"""
    if sys.platform == "win32":
        try:
            os.rename(source, destination)
        except FileExistsError:
            _reject("CATEGORY_DESTINATION_EXISTS", "Publication destination already exists")
        except OSError:
            _LOG.exception("Exclusive directory publication failed: destination=%s", destination)
            raise
        return
    if sys.platform not in {"darwin", "linux"}:
        _reject("CATEGORY_ATOMIC_PUBLICATION_UNSUPPORTED", "Exclusive directory rename is unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    paths = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin":
        operation = library.renamex_np
        operation.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        parameters = (*paths, 0x00000004)  # RENAME_EXCL：目标已存在则失败。
    elif sys.platform.startswith("linux"):
        operation = library.renameat2
        operation.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        parameters = (-100, paths[0], -100, paths[1], 1)  # AT_FDCWD 与 RENAME_NOREPLACE。
    operation.restype = ctypes.c_int
    if operation(*parameters) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        _reject("CATEGORY_DESTINATION_EXISTS", "Publication destination already exists")
    _LOG.error("Exclusive directory publication failed: destination=%s errno=%s", destination, error)
    raise OSError(error, os.strerror(error), str(destination))


def _validate_publication_request(source: Path, candidate: Path, assignments, scenarios) -> None:
    """@brief 校验契约递增、显式归属确认及非空场景范围。"""
    if (not isinstance(assignments, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in assignments.items())):
        _reject("CATEGORY_ASSIGNMENTS_INVALID", "Assignments must map object IDs to category IDs")
    if not scenarios:
        _reject("CATEGORY_SCENARIOS_REQUIRED", "Publication requires at least one scenario")
    previous_manifest = _read_json(source / "manifest.json")
    proposed_manifest = _read_json(candidate / "manifest.json")
    if previous_manifest.get("category_requirements") != proposed_manifest.get("category_requirements"):
        _reject("CATEGORY_REQUIREMENTS_CHANGED", "Category publication must preserve world category requirements")
    current = _read_json(source / "category-contract.json")
    proposed = _read_json(candidate / "category-contract.json")
    if proposed["contract_version"] <= current["contract_version"]:
        _reject("CATEGORY_VERSION_NOT_INCREMENTED", "Category contract version must increase")


def _apply_assignments(source: Path, staged: Path, assignments: dict[str, str]) -> None:
    """@brief 仅为显式确认的归属追加新对象版本，拒绝候选里的未经确认改挂。"""
    old = {row["object_id"]: row for row in _object_rows(source)}
    rows = _object_rows(staged)
    current = {row["object_id"]: row for row in rows}
    for identity, row in current.items():
        if identity in old and row["category_id"] != old[identity]["category_id"]:
            if assignments.get(identity) != row["category_id"]:
                _reject("CATEGORY_ASSIGNMENT_UNCONFIRMED", f"Object {identity} changed category without confirmation")
            if row["version"] != old[identity]["version"] + 1:
                _reject("CATEGORY_OBJECT_VERSION_INVALID", f"Object {identity} requires its next version")
    for identity, category in assignments.items():
        if identity not in current or identity not in old:
            _reject("CATEGORY_ASSIGNMENT_UNKNOWN", f"Assignment references unknown object {identity}")
        row = current[identity]
        if row["category_id"] != category:
            rows.append({**row, "category_id": category, "version": row["version"] + 1})
    (staged / "objects.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8")


def _admit_scenarios(directory: Path, scenarios: tuple[Path, ...]) -> None:
    """@brief 使用真正的场景加载入口逐份完成目录与可触达规则准入。"""

    for scenario in scenarios:
        load_runtime_scenario(Path(scenario), catalog_dir=directory)


def _pair_report(catalog, query: dict) -> dict:
    """@brief 查询明确的有向配对；多余字段或缺失字段均拒绝。"""
    if not isinstance(query, dict) or set(query) != set(_PAIR_FIELDS):
        _reject("CATEGORY_QUERY_INVALID", "Pairing query must contain actor, target, and action")
    pairing = catalog.pairing(*(query[name] for name in _PAIR_FIELDS))
    return {**query, **pairing.model_dump(mode="json")}


def _object_rows(directory: Path) -> list[dict]:
    """@brief 读取声明的完整追加历史，不静默跳过坏行。"""
    return [json.loads(line) for line in (directory / "objects.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _read_json(path: Path) -> dict:
    """@brief 从明确路径读取 JSON 文档，解析异常交给调用边界记录。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _reject(code: str, message: str) -> None:
    """@brief 为维护拒绝记录稳定英文错误并抛出目录异常。"""
    _LOG.error("%s: %s", code, message)
    raise CategoryError(code, message)
