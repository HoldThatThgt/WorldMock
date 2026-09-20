"""@brief 稀疏三态配对与双端偏序反例。"""

from __future__ import annotations

from pathlib import Path

import pytest

from worldmock.categories import CategoryError, PairingState, load_category_contract, resolve_pairing

from .conftest import contract_document, source, write_contract


def test_missing_pairing_is_undefined_without_materializing_matrix(tmp_path: Path) -> None:
    """缺项查询返回未定义，契约只保存显式行。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    result = resolve_pairing(contract, "person", "food", "consume:use")
    assert result.state == PairingState.UNDEFINED
    assert result.declaration is None
    assert len(contract.pairings) == 2


def test_direction_is_not_implicitly_reversed(tmp_path: Path) -> None:
    """正向购买规则不能自动解释反向查询。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    result = resolve_pairing(contract, "product", "person", "purchase")
    assert result.state == PairingState.UNDEFINED


def test_explicit_undefined_shadows_ancestor_definition(tmp_path: Path) -> None:
    """子类显式 undefined 可以遮蔽祖先 defined。"""
    document = contract_document()
    document["pairings"].append({
        "actor_category_id": "person",
        "target_category_id": "food",
        "action": "purchase",
        "state": "undefined",
    })
    contract = load_category_contract(write_contract(tmp_path, document))
    result = resolve_pairing(contract, "person", "food", "purchase")
    assert result.state == PairingState.UNDEFINED
    assert result.declaration is not None


def test_incomparable_double_specialization_is_ambiguous(tmp_path: Path) -> None:
    """两端分别更具体的命中不可按累计深度或顺序裁决。"""
    document = contract_document()
    actor = {
        "category_id": "adult",
        "name": "Adult",
        "parent_id": "person",
        "kind": "person",
        "slots": {},
        "values": {"absolute": {}, "relative": {}, "proportional": {}},
    }
    document["categories"].append(actor)
    document["pairings"].extend([
        {
            "actor_category_id": "adult",
            "target_category_id": "product",
            "action": "consume:use",
            "state": "undefined",
        },
        {
            "actor_category_id": "person",
            "target_category_id": "food",
            "action": "consume:use",
            "state": "undefined",
        },
    ])
    contract = load_category_contract(write_contract(tmp_path, document))
    with pytest.raises(CategoryError) as excinfo:
        resolve_pairing(contract, "adult", "food", "consume:use")
    assert excinfo.value.code == "PAIRING_AMBIGUOUS"


@pytest.mark.parametrize("reverse", [False, True])
def test_incomparable_pairings_remain_ambiguous_with_unequal_total_depth(tmp_path: Path, reverse: bool) -> None:
    """@brief 双端不可比时较深的一端和文件顺序都不能替代偏序判定。"""
    document = contract_document()
    document["categories"].extend([
        {"category_id": "adult", "name": "Adult", "parent_id": "person", "kind": "person"},
        {"category_id": "specialist", "name": "Specialist", "parent_id": "adult", "kind": "person"},
    ])
    declarations = [
        {
            "actor_category_id": "specialist", "target_category_id": "product",
            "action": "consume:use", "state": "undefined",
        },
        {
            "actor_category_id": "person", "target_category_id": "food",
            "action": "consume:use", "state": "undefined",
        },
    ]
    document["pairings"].extend(reversed(declarations) if reverse else declarations)
    contract = load_category_contract(write_contract(tmp_path, document))
    with pytest.raises(CategoryError) as excinfo:
        resolve_pairing(contract, "specialist", "food", "consume:use")
    assert excinfo.value.code == "PAIRING_AMBIGUOUS"


def test_no_effect_requires_review(tmp_path: Path) -> None:
    """已验证不作用若无审核证据必须拒绝。"""
    document = contract_document()
    document["pairings"].append({
        "actor_category_id": "person",
        "target_category_id": "food",
        "action": "consume:use",
        "state": "no_effect",
    })
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf")])
def test_pairing_coefficient_rejects_non_json_numbers(tmp_path: Path, value) -> None:
    """配对系数在类型转换前拒绝布尔、字符串与非有限数。"""
    document = contract_document()
    document["pairings"][0]["coefficient"] = value
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


@pytest.mark.parametrize("case", ["missing", "negative", "unexpected"])
def test_pairing_state_field_combinations_are_strict(tmp_path: Path, case: str) -> None:
    """defined 和非 defined 状态只接受各自的字段组合。"""
    document = contract_document()
    pairing = document["pairings"][0]
    if case == "missing":
        pairing.pop("rule_id")
    elif case == "negative":
        pairing["coefficient"] = -1
    else:
        pairing["state"] = "undefined"
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


def test_defined_pairing_requires_an_explicit_unit_coefficient(tmp_path: Path) -> None:
    """@brief 合法规则引用不能在缺少冻结系数时隐式取得单位系数。"""
    document = contract_document()
    document["pairings"][0].pop("coefficient")
    with pytest.raises(CategoryError) as excinfo:
        load_category_contract(write_contract(tmp_path, document))
    assert excinfo.value.code == "CATEGORY_CONTRACT_SCHEMA_INVALID"


def test_unknown_pairing_action_is_rejected(tmp_path: Path) -> None:
    """查询不在封闭集内的动作时明确失败。"""
    contract = load_category_contract(write_contract(tmp_path, contract_document()))
    with pytest.raises(CategoryError) as excinfo:
        resolve_pairing(contract, "person", "food", "invented")
    assert excinfo.value.code == "PAIRING_ACTION_UNKNOWN"
