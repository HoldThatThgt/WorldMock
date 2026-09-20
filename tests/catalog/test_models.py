"""@brief 目录数据模型的 Schema 正反例测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.catalog.models import (
    AlertRule,
    DrainRules,
    ExchangeObject,
    FormulaDeclaration,
    Provenance,
    ResourceBounds,
    ResourceDefinition,
    Tombstone,
    is_role_placeholder,
)

from .conftest import ENERGY_RESOURCE, MILK_OBJECT, MONEY_RESOURCE, SLEEP_RESOURCE


def test_resource_definition_accepts_design_example() -> None:
    """@brief docs/design/03-resource-engine.md「可计量资源定义」的资源示例（数组溯源+drain_rules）原样通过校验。"""
    resource = ResourceDefinition.model_validate(ENERGY_RESOURCE)
    assert resource.resource_id == "energy_kcal"
    assert resource.bounds is not None and resource.bounds.max == 2400
    assert len(resource.provenance) == 5
    assert resource.provenance[0].paths == ("drain_rules.basal_kcal_per_hour",)
    assert resource.drain_rules is not None
    assert resource.drain_rules.basal_kcal_per_hour == 65
    assert resource.drain_rules.food_efficiency_curve is not None
    assert resource.alert_rules[0].code == "HUNGRY"


def test_account_resource_accepts_null_bounds() -> None:
    """@brief 账户型资源（money_fen）bounds 为 null、无 drain_rules 也合法。"""
    resource = ResourceDefinition.model_validate(MONEY_RESOURCE)
    assert resource.bounds is None
    assert resource.drain_rules is None
    assert resource.alert_rules == ()


def test_level_resource_with_null_max_bounds_accepted() -> None:
    """@brief 存量型资源的 max 可为 null（无上界，如睡眠债）。"""
    resource = ResourceDefinition.model_validate(SLEEP_RESOURCE)
    assert resource.bounds is not None
    assert resource.bounds.min == 0
    assert resource.bounds.max is None


def test_level_resource_without_bounds_rejected() -> None:
    """@brief 存量型资源的上下界是硬约束。"""
    payload = dict(ENERGY_RESOURCE)
    payload.pop("bounds")
    with pytest.raises(ValidationError, match="must declare bounds"):
        ResourceDefinition.model_validate(payload)


def test_bounds_min_above_max_rejected() -> None:
    """@brief 上下界区间不许倒挂。"""
    with pytest.raises(ValidationError, match="max must not be below min"):
        ResourceBounds.model_validate({"min": 10, "max": 0})


def test_resource_without_provenance_rejected() -> None:
    """@brief 每个静态数值必须有来源标注（模型层面强制）。"""
    payload = dict(ENERGY_RESOURCE)
    payload.pop("provenance")
    with pytest.raises(ValidationError, match="provenance"):
        ResourceDefinition.model_validate(payload)


def test_resource_provenance_parts_must_be_unique() -> None:
    """@brief 资源溯源数组的 part 不许重复。"""
    payload = dict(ENERGY_RESOURCE)
    duplicated = [dict(payload["provenance"][0]), dict(payload["provenance"][0])]
    payload["provenance"] = duplicated
    with pytest.raises(ValidationError, match="parts must be unique"):
        ResourceDefinition.model_validate(payload)


def test_drain_rules_rejects_unknown_rule_names() -> None:
    """@brief drain_rules 是闭集 Schema，未知规则名被拒。"""
    with pytest.raises(ValidationError):
        DrainRules.model_validate({"basal_kcal_per_hour": 65, "typo_rate": 1})


def test_provenance_unknown_type_rejected() -> None:
    """@brief 溯源类型是四枚举闭集。"""
    with pytest.raises(ValidationError):
        Provenance.model_validate({"type": "guess", "source": "nowhere"})


def test_alert_rule_requires_exactly_one_direction() -> None:
    """@brief 警报规则必须恰好声明一个阈值方向。"""
    with pytest.raises(ValidationError, match="exactly one"):
        AlertRule.model_validate({"below": 5, "above": 9, "code": "X"})
    with pytest.raises(ValidationError, match="exactly one"):
        AlertRule.model_validate({"code": "X"})


def test_role_placeholder_detection() -> None:
    """@brief 占位端点判定：花括号包裹为占位，其余为字面资源 id。"""
    assert is_role_placeholder("{buyer}")
    assert is_role_placeholder("{merchant}")
    assert not is_role_placeholder("energy_kcal")
    assert not is_role_placeholder("{unbalanced")


def test_transfer_edge_requires_amount() -> None:
    """@brief 转账边必须携带金额（整数或公式绑定）。"""
    neither = {
        "on": "depart",
        "type": "transfer",
        "from": "{buyer}",
        "to": "{merchant}",
        "account_roles": {"from": "buyer", "to": "merchant:grocery"},
    }
    with pytest.raises(ValidationError, match="amount_fen"):
        ExchangeObject.model_validate(
            {**MILK_OBJECT, "edges": [neither, MILK_OBJECT["edges"][0]]}
        )


def test_transfer_edge_accepts_formula_binding_amount() -> None:
    """@brief 转账边金额可为公式绑定（公式 id + 入参路径）。"""
    binding = {
        "on": "depart",
        "type": "transfer",
        "from": "{traveler}",
        "to": "{transport_operator}",
        "amount_fen": {"formula_ref": "fare", "params": {"m": "route.distance_meters"}},
        "account_roles": {"from": "traveler", "to": "transport:service"},
    }
    payload = {**MILK_OBJECT, "object_id": "travel-x", "edges": [binding]}
    obj = ExchangeObject.model_validate(payload)
    assert obj.edges[0].amount_fen.formula_ref == "fare"  # type: ignore[union-attr]


def test_transfer_edge_same_endpoints_rejected() -> None:
    """@brief 转账边两端必须不同。"""
    self_loop = {
        "on": "depart",
        "type": "transfer",
        "from": "{buyer}",
        "to": "{buyer}",
        "amount_fen": 680,
        "account_roles": {"from": "buyer", "to": "merchant:grocery"},
    }
    with pytest.raises(ValidationError, match="endpoints must differ"):
        ExchangeObject.model_validate({**MILK_OBJECT, "edges": [self_loop, MILK_OBJECT["edges"][0]]})


def test_transfer_placeholder_without_account_roles_rejected() -> None:
    """@brief 占位端点缺角色标注被拒。"""
    orphan = {
        "on": "depart",
        "type": "transfer",
        "from": "{buyer}",
        "to": "{merchant}",
        "amount_fen": 680,
    }
    with pytest.raises(ValidationError, match="account_roles"):
        ExchangeObject.model_validate({**MILK_OBJECT, "edges": [orphan, MILK_OBJECT["edges"][0]]})


def test_source_edge_accepts_zero_amount_fact() -> None:
    """@brief source 边 amount=0 是合法事实（零卡汽水）。"""
    payload = {**MILK_OBJECT, "edges": [MILK_OBJECT["edges"][0], {
        "on": "consume", "type": "source", "resource": "energy_kcal", "amount": 0,
    }]}
    obj = ExchangeObject.model_validate(payload)
    assert obj.edges[1].amount == 0  # type: ignore[union-attr]


def test_source_edge_requires_amount() -> None:
    """@brief source 边必须携带数量。"""
    neither = {
        "on": "consume",
        "type": "source",
        "resource": "energy_kcal",
        "duration_s": 900,
    }
    with pytest.raises(ValidationError, match="amount"):
        ExchangeObject.model_validate({**MILK_OBJECT, "edges": [MILK_OBJECT["edges"][0], neither]})


def test_convert_edge_requires_exactly_one_amount_or_formula() -> None:
    """@brief 折算边的静态数量与内联公式互斥且必居其一。"""
    both = {
        "on": "settle",
        "type": "convert",
        "from": "{employer}",
        "to": "{worker}",
        "amount": 5000,
        "formula": "pay_fen",
        "account_roles": {"from": "employer:zhangjiang", "to": "worker"},
    }
    with pytest.raises(ValidationError, match="exactly one"):
        ExchangeObject.model_validate({**MILK_OBJECT, "edges": [both]})
    neither = dict(both)
    neither.pop("amount")
    neither.pop("formula")
    with pytest.raises(ValidationError, match="exactly one"):
        ExchangeObject.model_validate({**MILK_OBJECT, "edges": [neither]})


def test_convert_edge_same_endpoints_rejected() -> None:
    """@brief convert 边两端必须不同。"""
    payload = {
        "object_id": "bad-convert",
        "name": "自环折算",
        "object_kind": "contract",
        "edges": [
            {"on": "perform", "type": "convert", "from": "energy_kcal", "to": "energy_kcal", "amount": 1}
        ],
        "provenance": [{"part": "edge", "type": "assumption", "source": "test"}],
        "version": 1,
    }
    with pytest.raises(ValidationError, match="endpoints must differ"):
        ExchangeObject.model_validate(payload)


def test_object_rejects_unknown_fields() -> None:
    """@brief 目录条目模型拒绝未知字段。"""
    payload = {**MILK_OBJECT, "price_fen": 680}
    with pytest.raises(ValidationError):
        ExchangeObject.model_validate(payload)


def test_object_provenance_must_cover_edges() -> None:
    """@brief 每条边至少要有一条溯源标注。"""
    payload = {**MILK_OBJECT, "edges": [*MILK_OBJECT["edges"], *MILK_OBJECT["edges"]],
               "provenance": MILK_OBJECT["provenance"][:1]}
    with pytest.raises(ValidationError, match="at least one provenance"):
        ExchangeObject.model_validate(payload)


def test_object_provenance_parts_must_be_unique() -> None:
    """@brief 溯源标注的 part 不许重复。"""
    duplicated = [dict(MILK_OBJECT["provenance"][0]), dict(MILK_OBJECT["provenance"][0])]
    payload = {**MILK_OBJECT, "provenance": duplicated}
    with pytest.raises(ValidationError, match="parts must be unique"):
        ExchangeObject.model_validate(payload)


def test_nonproduct_object_requires_at_least_one_edge() -> None:
    """@brief 服务与合同仍然必须声明交换边。"""
    payload = {**MILK_OBJECT, "object_kind": "service", "edges": []}
    with pytest.raises(ValidationError):
        ExchangeObject.model_validate(payload)


def test_formula_declaration_requires_branches() -> None:
    """@brief 公式声明至少要有一个分支。"""
    with pytest.raises(ValidationError, match="at least one branch"):
        FormulaDeclaration.model_validate(
            {"formula_id": "fare", "unit": "fen", "branches": {}}
        )


def test_tombstone_requires_known_entry_kind() -> None:
    """@brief 墓碑目标命名空间是三枚举闭集。"""
    with pytest.raises(ValidationError):
        Tombstone.model_validate(
            {"target_id": "x", "kind": "dataset", "at_version": 1, "reason": "retired"}
        )
