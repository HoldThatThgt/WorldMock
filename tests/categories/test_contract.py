"""@brief 品类树、属性继承、来源和只读语义反例。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from worldmock.categories import (
    CategoryError,
    load_category_contract,
    resolve_object_values,
    validate_category_attributes,
)
from worldmock.catalog.models import ObjectDeclaration

from .conftest import contract_document, raw_object, write_contract


def test_required_object_slot_may_be_unset_on_category(tmp_path: Path) -> None:
    """品类可声明对象必填槽位而不复制一个虚假默认值。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    assert "price_fen" not in contract.categories["product"].attributes
    resolved = resolve_object_values(ObjectDeclaration.model_validate(raw_object()), contract)
    assert resolved.attributes["price_fen"] == 680
    assert resolved.attributes["weight"] == 10


def test_relative_child_tracks_parent_value(tmp_path: Path) -> None:
    """子类相对增量保持关系，修改父值后结果同步移动。"""
    document = contract_document()
    document["categories"][3]["values"]["relative"]["weight"] = 2
    first = load_category_contract(write_contract(tmp_path, document))
    assert first.categories["food"].attributes["weight"] == 12
    document["categories"][2]["slots"]["weight"]["schema"]["default"] = 20
    write_contract(tmp_path, document)
    second = load_category_contract(tmp_path / "category-contract.json")
    assert second.categories["food"].attributes["weight"] == 22


def test_resolved_nested_values_are_read_only(tmp_path: Path) -> None:
    """解析值的嵌套映射和数组不能被调用方改写。"""
    document = contract_document()
    document["categories"][2]["slots"]["metadata"] = {
        "schema": {"type": "object"}, "required": False,
    }
    document["categories"][2]["values"]["absolute"]["metadata"] = {"tags": ["one"]}
    contract = load_category_contract(write_contract(tmp_path, document))
    metadata = contract.categories["product"].attributes["metadata"]
    with pytest.raises(TypeError):
        metadata["new"] = "value"
    with pytest.raises(TypeError):
        metadata["tags"][0] = "changed"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda doc: doc["categories"].append(copy.deepcopy(doc["categories"][2])), "CATEGORY_DUPLICATE"),
        (lambda doc: doc["categories"][3].update(parent_id="missing"), "CATEGORY_PARENT_UNKNOWN"),
        (lambda doc: doc["categories"][2].update(parent_id="food"), "CATEGORY_CYCLE"),
        (lambda doc: doc["categories"][3].update(kind="service"), "CATEGORY_CROSS_KIND_INHERITANCE"),
    ],
)
def test_invalid_category_tree_rejected(tmp_path: Path, mutation, code: str) -> None:
    """重复、孤儿、环与跨形态继承均以稳定错误拒绝。"""
    document = contract_document()
    mutation(document)
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == code


def test_redundant_object_absolute_value_rejected(tmp_path: Path) -> None:
    """对象不能重新复制已从品类继承的数值。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    payload = raw_object()
    payload["values"]["absolute"]["weight"] = 10
    payload["provenance"][0]["paths"] = ["/values"]
    declaration = ObjectDeclaration.model_validate(payload)
    with pytest.raises(CategoryError) as excinfo:
        resolve_object_values(declaration, contract)
    assert excinfo.value.code == "CATEGORY_VALUE_REDUNDANT"


@pytest.mark.parametrize(("operation", "change"), [("relative", 0), ("proportional", 1)])
def test_redundant_object_numeric_operation_rejected(
    tmp_path: Path,
    operation: str,
    change: int,
) -> None:
    """零加法和单位乘法不能绕过重复值拒绝。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    payload = raw_object()
    payload["values"][operation]["weight"] = change
    declaration = ObjectDeclaration.model_validate(payload)
    with pytest.raises(CategoryError) as excinfo:
        resolve_object_values(declaration, contract)
    assert excinfo.value.code == "CATEGORY_VALUE_REDUNDANT"


def test_candidate_category_attributes_use_all_effective_constraints(tmp_path: Path) -> None:
    """细化检查可直接用候选品类全部祖先约束验证已审核属性。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    resolved = validate_category_attributes(
        contract,
        "food",
        {
            "price_fen": 1,
            "merchant_role": "merchant:grocery",
            "weight": 2,
            "energy_kcal_per_serving": 3,
            "use_duration_seconds": 4,
        },
    )
    assert resolved["energy_kcal_per_serving"] == 3
    with pytest.raises(CategoryError):
        validate_category_attributes(contract, "food", {"price_fen": 1})


def test_candidate_category_values_fill_new_slots_without_overwriting_current_values(tmp_path: Path) -> None:
    """候选子类 values 为新槽位供值，旧对象已验证属性保持实值。"""
    document = contract_document()
    document["categories"].append({
        "category_id": "flavored-food",
        "name": "Flavored Food",
        "parent_id": "food",
        "kind": "product",
        "slots": {"flavor": {"schema": {"type": "string"}, "required": True}},
        "values": {"absolute": {"flavor": "mild", "weight": 12}, "relative": {}, "proportional": {}},
    })
    contract = load_category_contract(write_contract(tmp_path, document))
    current = resolve_object_values(ObjectDeclaration.model_validate(raw_object()), contract).attributes
    resolved = validate_category_attributes(contract, "flavored-food", current)
    assert resolved["flavor"] == "mild"
    assert resolved["weight"] == 10


def test_frozen_raw_declarations_are_deep_read_only(tmp_path: Path) -> None:
    """冻结契约的原始声明不能通过内层映射改写共享语义。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    product = contract.declarations["product"]
    with pytest.raises(TypeError):
        product.values.absolute["weight"] = 99
    with pytest.raises(TypeError):
        product.slots["new"] = product.slots["weight"]
    with pytest.raises(TypeError):
        product.slots["weight"].schema_["minimum"] = -1


def test_bool_cannot_satisfy_integer_slot(tmp_path: Path) -> None:
    """布尔值不能冒充对象的整数价格。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    payload = raw_object()
    payload["values"]["absolute"]["price_fen"] = True
    declaration = ObjectDeclaration.model_validate(payload)
    with pytest.raises(CategoryError) as excinfo:
        resolve_object_values(declaration, contract)
    assert excinfo.value.code == "CATEGORY_SLOT_VALUE_INVALID"


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf")])
def test_numeric_delta_rejects_non_json_numbers(value) -> None:
    """增量输入在类型转换前拒绝布尔、字符串与非有限数。"""
    payload = raw_object()
    payload["values"]["absolute"].pop("price_fen")
    payload["values"]["relative"]["price_fen"] = value
    with pytest.raises(Exception):
        ObjectDeclaration.model_validate(payload)


def test_non_finite_absolute_value_is_rejected(tmp_path: Path) -> None:
    """JSON Schema 数值槽也不能接受 NaN。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    payload = raw_object()
    payload["values"]["absolute"]["energy_kcal_per_serving"] = float("nan")
    declaration = ObjectDeclaration.model_validate(payload)
    with pytest.raises(CategoryError) as excinfo:
        resolve_object_values(declaration, contract)
    assert excinfo.value.code == "CATEGORY_NUMBER_INVALID"


def test_missing_and_duplicate_numeric_sources_rejected(tmp_path: Path) -> None:
    """静态数值漏来源或被两条来源覆盖均拒绝。"""
    document = contract_document()
    document["sources"][0]["paths"] = ["/categories"]
    with pytest.raises(CategoryError) as missing:
        load_category_contract(write_contract(tmp_path, document))
    assert missing.value.code == "CATEGORY_NUMERIC_SOURCE_MISSING"
    document = contract_document()
    duplicate = copy.deepcopy(document["sources"][0])
    duplicate["source_id"] = "duplicate"
    duplicate["paths"] = ["/pairings"]
    document["sources"].append(duplicate)
    with pytest.raises(CategoryError) as doubled:
        load_category_contract(write_contract(tmp_path, document))
    assert doubled.value.code == "CATEGORY_NUMERIC_SOURCE_DUPLICATE"


def test_model_fact_and_unreviewed_agent_rejected(tmp_path: Path) -> None:
    """模型不能直报 fact，委托 agent 审核必须保留授权来源。"""
    document = contract_document()
    document["sources"][0].update(type="fact", producer="model")
    with pytest.raises(CategoryError) as fact:
        load_category_contract(write_contract(tmp_path, document))
    assert fact.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"
    document = contract_document()
    document["review"].pop("authorization")
    with pytest.raises(CategoryError) as review:
        load_category_contract(write_contract(tmp_path, document))
    assert review.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


@pytest.mark.parametrize("case", ["anchored", "duplicate", "relative"])
def test_source_evidence_shape_is_strict(tmp_path: Path, case: str) -> None:
    """模型锚定记录、路径去重与绝对 JSON Pointer 均在加载时校验。"""
    document = contract_document()
    record = document["sources"][0]
    if case == "anchored":
        record.update(type="anchored", producer="model")
    elif case == "duplicate":
        record["paths"] = ["/categories", "/categories"]
    else:
        record["paths"] = ["categories"]
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


def test_non_object_category_and_unknown_category_are_rejected(tmp_path: Path) -> None:
    """对象不能归属 person，且未知品类查询必须明确失败。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    payload = raw_object(category_id="person")
    with pytest.raises(CategoryError) as kind:
        resolve_object_values(ObjectDeclaration.model_validate(payload), contract)
    assert kind.value.code == "CATEGORY_OBJECT_KIND_INVALID"
    with pytest.raises(CategoryError) as unknown:
        contract.require_category("unknown")
    assert unknown.value.code == "CATEGORY_UNKNOWN"


def test_blank_attribute_operation_name_is_rejected() -> None:
    """属性操作不能使用空白槽位名。"""
    payload = raw_object()
    payload["values"]["relative"][" "] = 1
    with pytest.raises(Exception):
        ObjectDeclaration.model_validate(payload)


def test_raw_object_rejects_edges_and_formula() -> None:
    """旧 raw 对象的 edges 与 formula 字段不能再进入模型。"""
    payload = raw_object()
    payload["edges"] = []
    payload["formula"] = "price * 2"
    with pytest.raises(Exception):
        ObjectDeclaration.model_validate(payload)
