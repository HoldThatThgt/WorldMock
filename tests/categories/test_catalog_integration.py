"""@brief raw 对象编译、摘要绑定和目录真实加载测试。"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.categories.conftest import (
    contract_document,
    raw_object,
    rules_document,
    write_contract,
    write_rules,
)
from worldmock.categories import load_category_contract, load_exchange_rules
from worldmock.catalog import CatalogError, ExchangeObject, RuleEdge, SourceEdge, TransferEdge, load_catalog
from worldmock.catalog.compiler import compile_exchange_object
from worldmock.catalog.models import ObjectDeclaration


def _compile_documents(
    tmp_path: Path, contract: dict, rules: dict, declaration: dict,
) -> ExchangeObject:
    """@brief 编译一组内存测试文档并返回运行期对象。"""
    loaded_contract = load_category_contract(write_contract(tmp_path, contract))
    loaded_rules = load_exchange_rules(write_rules(tmp_path, rules), loaded_contract)
    return compile_exchange_object(
        ObjectDeclaration.model_validate(declaration), loaded_contract, loaded_rules,
    )


def test_real_loader_compiles_raw_object_to_effective_view(valid_catalog_dir: Path) -> None:
    """一条真实 raw 对象经契约和规则编译为现有 ExchangeObject 视图。"""
    catalog = load_catalog(valid_catalog_dir)
    obj = catalog.objects["milk"]
    assert obj.category_id == "food"
    assert obj.attribute("weight") == 10
    assert obj.attribute("price_fen") == 680
    assert isinstance(obj.edges[0], RuleEdge)
    assert obj.edges[0].rule_id == "buy-product"
    assert obj.edges[0].coefficient == 1
    assert not any(isinstance(edge, TransferEdge) for edge in obj.edges)
    assert isinstance(obj.edges[1], SourceEdge)
    assert obj.edges[1].amount == 185
    assert catalog.raw_objects["milk"].model_dump(exclude={"provenance"}).keys() == {
        "object_id", "name", "category_id", "values", "version",
    }


def test_effective_attributes_are_deep_read_only(valid_catalog_dir: Path) -> None:
    """目录对象属性映射不能被外部调用者修改。"""
    obj = load_catalog(valid_catalog_dir).objects["milk"]
    with pytest.raises(TypeError):
        obj.attributes["price_fen"] = 1


def test_catalog_models_cannot_mutate_frozen_runtime_semantics(valid_catalog_dir: Path) -> None:
    """原始声明、编译对象和资源模型都不能绕过只读目录映射重绑定字段。"""
    catalog = load_catalog(valid_catalog_dir)
    raw = catalog.raw_objects["milk"]
    obj = catalog.objects["milk"]
    resource = catalog.resources["money_fen"]
    for model, field, value in (
        (raw, "category_id", "product"),
        (obj, "effect", "changed"),
        (obj.edges[0], "amount_fen", 1),
        (resource, "unit", "changed"),
    ):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, value)
    assert catalog.objects["milk"].edges[0].rule_id == "buy-product"
    assert catalog.resources["money_fen"].unit == "fen"


def test_frozen_catalog_serializes_without_warnings(valid_catalog_dir: Path) -> None:
    """只读容器可无损导出 JSON；导出后的修改不污染冻结模型。"""
    catalog = load_catalog(valid_catalog_dir)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        raw = catalog.raw_objects["milk"].model_dump(mode="json")
        effective = catalog.objects["milk"].model_dump(mode="json")
        resources = [resource.model_dump(mode="json") for resource in catalog.resources.values()]
    assert json.loads(json.dumps(raw))["category_id"] == "food"
    assert resources
    effective["attributes"]["price_fen"] = 1
    assert catalog.objects["milk"].attribute("price_fen") == 680


def test_same_version_content_tamper_is_rejected(valid_catalog_dir: Path) -> None:
    """同版本规则字节变化但未刷新清单时加载明确拒绝。"""
    path = valid_catalog_dir / "exchange-rules.json"
    path.write_text(path.read_text().replace("buy-product", "buy-products", 1))
    with pytest.raises(CatalogError) as excinfo:
        load_catalog(valid_catalog_dir)
    assert excinfo.value.code == "CATALOG_CONTENT_IDENTITY_MISMATCH"


def test_manifest_binds_contract_and_catalog_content(valid_catalog_dir: Path) -> None:
    """加载视图保留清单中的契约版本与三份摘要。"""
    catalog = load_catalog(valid_catalog_dir)
    manifest = json.loads((valid_catalog_dir / "manifest.json").read_text())
    assert catalog.category_contract_version == 1
    assert catalog.category_contract_sha256 == manifest["category_contract_sha256"]
    assert catalog.exchange_rules_sha256 == manifest["exchange_rules_sha256"]
    assert catalog.catalog_content_sha256 == manifest["catalog_content_sha256"]


def test_pairing_and_rule_for_are_catalog_public_interfaces(valid_catalog_dir: Path) -> None:
    """目录公开查询直接复用唯一配对与 typed rule。"""
    catalog = load_catalog(valid_catalog_dir)
    pairing = catalog.pairing("person", "food", "purchase")
    assert pairing.rule_id == "buy-product"
    rule, coefficient = catalog.rule_for("person", "food", "purchase")
    assert rule.rule_id == "buy-product"
    assert coefficient == 1


def test_food_duration_uses_rule_named_slot(tmp_path: Path) -> None:
    """食品运行期时长来自能量规则命名槽，而非固定属性名。"""
    contract = contract_document()
    food = next(row for row in contract["categories"] if row["category_id"] == "food")
    food["slots"]["meal_duration_seconds"] = {
        "schema": {"type": "integer", "minimum": 1}, "required": True, "unit": "second",
    }
    rules = rules_document()
    rules["rules"][1]["duration_slot"] = "meal_duration_seconds"
    declaration = raw_object()
    declaration["values"]["absolute"]["meal_duration_seconds"] = 321

    compiled = _compile_documents(tmp_path, contract, rules, declaration)

    consume = next(edge for edge in compiled.edges if isinstance(edge, SourceEdge))
    assert consume.duration_s == 321
    assert compiled.use_duration_seconds == 321
    assert compiled.attribute("use_duration_seconds") == 900


def test_durable_effect_uses_rule_named_slot_and_default(tmp_path: Path) -> None:
    """耐用品效果服从命名槽；规则未命名时保留对象 effect。"""
    contract = contract_document()
    food = next(row for row in contract["categories"] if row["category_id"] == "food")
    food["slots"].update({
        "effect": {"schema": {"type": "string", "minLength": 1}, "required": True},
        "shelter_effect": {"schema": {"type": "string", "minLength": 1}, "required": True},
    })
    contract["pairings"].append({
        "actor_category_id": "person", "target_category_id": "food",
        "action": "consume:use", "state": "defined", "rule_id": "use-food", "coefficient": 1,
    })
    rules = rules_document()
    rules["rules"].append({
        "rule_id": "use-food", "category_id": "food", "action": "consume:use",
        "operator": "durable_use", "resource_id": "inventory", "unit": "item",
        "conservation": "open", "effect_slot": "shelter_effect",
    })
    declaration = raw_object()
    declaration["values"]["absolute"].update({
        "effect": "nutrition", "shelter_effect": "weather_protection",
    })

    compiled = _compile_documents(tmp_path, contract, rules, declaration)
    assert compiled.effect == "weather_protection"

    rules["rules"][-1].pop("effect_slot")
    fallback = _compile_documents(tmp_path, contract, rules, declaration)
    assert fallback.effect == "nutrition"
