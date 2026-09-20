"""@brief 品类契约与闭集规则测试夹具。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from worldmock.categories import refresh_manifest_identity


def review() -> dict:
    """@brief 返回真实委托审查形态。"""
    return {
        "reviewer": "test agent",
        "reviewer_type": "delegated_agent",
        "evidence": "frozen test specification",
        "reason": "verify category behavior",
        "authorization": "user requested delegated review",
    }


def source(source_id: str, paths: list[str], source_type: str = "scenario") -> dict:
    """@brief 构造一个人工测试来源。"""
    return {
        "source_id": source_id,
        "type": source_type,
        "producer": "human",
        "source": "test fixture",
        "paths": paths,
    }


def contract_document() -> dict:
    """@brief 返回含产品、食品与稀疏配对的最小合法契约。"""
    document = {
        "format_version": 1,
        "contract_version": 1,
        "categories": [
            _category("entity", None, "entity"),
            _category("person", "entity", "person"),
            _product_category(),
            _food_category(),
        ],
        "pairings": [
            _pairing("person", "product", "purchase", "buy-product"),
            _pairing("person", "food", "consume:eat", "eat-food"),
        ],
        "sources": [source("contract-numbers", ["/categories", "/pairings"])],
        "review": review(),
    }
    return copy.deepcopy(document)


def rules_document() -> dict:
    """@brief 返回购买和食品能量两个 typed rule。"""
    document = {
        "format_version": 1,
        "contract_version": 1,
        "rules": [
            {
                "rule_id": "buy-product",
                "category_id": "product",
                "action": "purchase",
                "operator": "purchase_transfer",
                "resource_id": "money_fen",
                "unit": "fen",
                "conservation": "closed",
                "from_role": "buyer",
                "to_role": "merchant",
            },
            {
                "rule_id": "eat-food",
                "category_id": "food",
                "action": "consume:eat",
                "operator": "food_energy_source",
                "resource_id": "energy_kcal",
                "unit": "kcal",
                "conservation": "open",
                "energy_slot": "energy_kcal_per_serving",
                "duration_slot": "use_duration_seconds",
            },
        ],
        "sources": [source("rule-structure", ["/rules/0/rule_id"])],
        "review": review(),
    }
    return copy.deepcopy(document)


def raw_object(object_id: str = "milk", category_id: str = "food") -> dict:
    """@brief 返回一条只含身份、品类和对象差异的 raw 对象。"""
    values = {
        "absolute": {
            "price_fen": 680,
            "merchant_role": "merchant:grocery",
            "energy_kcal_per_serving": 185,
            "use_duration_seconds": 900,
        },
        "relative": {},
        "proportional": {},
    }
    return {
        "object_id": object_id,
        "name": "Milk",
        "category_id": category_id,
        "values": values,
        "provenance": [source("object-values", ["/values"])],
        "version": 1,
    }


def write_contract(root: Path, document: dict | None = None) -> Path:
    """@brief 写出契约文件并返回路径。"""
    path = root / "category-contract.json"
    path.write_text(json.dumps(document or contract_document()), encoding="utf-8")
    return path


def write_rules(root: Path, document: dict | None = None) -> Path:
    """@brief 写出规则文件并返回路径。"""
    path = root / "exchange-rules.json"
    path.write_text(json.dumps(document or rules_document()), encoding="utf-8")
    return path


def write_catalog(root: Path) -> Path:
    """@brief 写出一份可真实 load_catalog 的最小目录。"""
    root.mkdir()
    write_contract(root)
    write_rules(root)
    _write_jsonl(root / "objects.jsonl", [raw_object()])
    _write_jsonl(root / "resources.jsonl", [_money_resource(), _energy_resource()])
    (root / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    refresh_manifest_identity(root)
    return root


@pytest.fixture
def valid_catalog_dir(tmp_path: Path) -> Path:
    """@brief 返回一份摘要已刷新的最小目录。"""
    return write_catalog(tmp_path / "catalog")


def _category(identity: str, parent: str | None, kind: str) -> dict:
    """@brief 构造无槽位品类节点。"""
    return {
        "category_id": identity,
        "name": identity.title(),
        "parent_id": parent,
        "kind": kind,
        "slots": {},
        "values": {"absolute": {}, "relative": {}, "proportional": {}},
    }


def _product_category() -> dict:
    """@brief 构造含对象必填价格和继承默认重量的产品节点。"""
    node = _category("product", "entity", "product")
    node["slots"] = {
        "price_fen": {"schema": {"type": "integer", "minimum": 0}, "required": True, "unit": "fen"},
        "merchant_role": {"schema": {"type": "string", "minLength": 1}, "required": True},
        "weight": {
            "schema": {"type": "number", "minimum": 0, "default": 10},
            "required": True,
            "unit": "kg",
        },
    }
    return node


def _food_category() -> dict:
    """@brief 构造食品节点及其对象必填能量、时长槽位。"""
    node = _category("food", "product", "product")
    node["slots"] = {
        "energy_kcal_per_serving": {
            "schema": {"type": "number", "minimum": 0},
            "required": True,
            "unit": "kcal",
        },
        "use_duration_seconds": {
            "schema": {"type": "integer", "minimum": 1},
            "required": True,
            "unit": "second",
        },
    }
    return node


def _pairing(actor: str, target: str, action: str, rule_id: str) -> dict:
    """@brief 构造显式单位系数的 defined 配对。"""
    return {
        "actor_category_id": actor,
        "target_category_id": target,
        "action": action,
        "state": "defined",
        "rule_id": rule_id,
        "coefficient": 1,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """@brief 写出紧凑 JSONL。"""
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _provenance() -> list[dict]:
    """@brief 返回旧资源模型使用的来源记录。"""
    return [{"part": "definition", "type": "scenario", "source": "test fixture", "paths": []}]


def _money_resource() -> dict:
    """@brief 返回封闭货币账户资源。"""
    return {
        "resource_id": "money_fen",
        "kind": "account",
        "unit": "fen",
        "owner": "global",
        "conservation": "closed",
        "provenance": _provenance(),
        "version": 1,
    }


def _energy_resource() -> dict:
    """@brief 返回开放身体能量资源。"""
    return {
        "resource_id": "energy_kcal",
        "kind": "level",
        "unit": "kcal",
        "owner": "agent",
        "bounds": {"min": 0, "max": 2400},
        "conservation": "open",
        "provenance": _provenance(),
        "version": 1,
    }


def _manifest() -> dict:
    """@brief 返回供 identity helper 刷新的占位清单。"""
    digest = "0" * 64
    return {
        "version_major": 3,
        "version_minor": 0,
        "category_contract_version": 1,
        "category_contract_sha256": digest,
        "exchange_rules_sha256": digest,
        "catalog_content_sha256": digest,
    }
