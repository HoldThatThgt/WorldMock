"""@brief 目录资源、raw 对象和编译视图模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.categories.conftest import raw_object
from worldmock.catalog.models import (
    AlertRule,
    CategoryError,
    ObjectDeclaration,
    ResourceBounds,
    ResourceDefinition,
    TransferEdge,
)


def test_raw_object_accepts_only_identity_category_values_source_version() -> None:
    """raw 对象精确接受六个规范字段。"""
    obj = ObjectDeclaration.model_validate(raw_object())
    assert obj.object_id == "milk"
    assert obj.category_id == "food"
    with pytest.raises(ValidationError):
        ObjectDeclaration.model_validate({**raw_object(), "edges": []})


def test_resource_bounds_reject_inverted_span() -> None:
    """level 资源上界不能低于下界。"""
    with pytest.raises(ValidationError):
        ResourceBounds(min=2, max=1)


def test_alert_requires_exactly_one_direction() -> None:
    """资源警报必须恰好声明 below 或 above。"""
    with pytest.raises(ValidationError):
        AlertRule(code="BAD")
    with pytest.raises(ValidationError):
        AlertRule(code="BAD", below=1, above=2)


def test_level_resource_requires_bounds() -> None:
    """开放或封闭 level 资源都必须声明硬边界。"""
    payload = {
        "resource_id": "inventory_closed",
        "kind": "level",
        "unit": "item",
        "owner": "agent_location",
        "conservation": "closed",
        "provenance": [{"part": "definition", "type": "scenario", "source": "fixture"}],
        "version": 1,
    }
    with pytest.raises(ValidationError):
        ResourceDefinition.model_validate(payload)


def test_resource_provenance_parts_must_be_unique() -> None:
    """同一资源的来源 part 不能重复。"""
    payload = {
        "resource_id": "money_fen",
        "kind": "account",
        "unit": "fen",
        "owner": "global",
        "conservation": "closed",
        "provenance": [
            {"part": "definition", "type": "scenario", "source": "first"},
            {"part": "definition", "type": "scenario", "source": "second"},
        ],
        "version": 1,
    }
    with pytest.raises(ValidationError):
        ResourceDefinition.model_validate(payload)


def test_transfer_rejects_same_endpoint_and_missing_roles() -> None:
    """闭集转账要求不同端点且占位端点必须有角色。"""
    base = {
        "on": "purchase",
        "type": "transfer",
        "from": "account",
        "to": "account",
        "resource": "money_fen",
        "amount_fen": 1,
    }
    with pytest.raises(ValidationError):
        TransferEdge.model_validate(base)
    base.update({"from": "{buyer}", "to": "merchant"})
    with pytest.raises(ValidationError):
        TransferEdge.model_validate(base)


def test_catalog_error_exposes_stable_code() -> None:
    """目录错误保留稳定英文错误码。"""
    error = CategoryError("TEST_FAILURE", "detail")
    assert error.code == "TEST_FAILURE"
    assert str(error) == "TEST_FAILURE: detail"
