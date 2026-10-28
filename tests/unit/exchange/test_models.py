"""@brief 幸存基础定义搬移后的深度冻结与有限数值合同。"""

import pytest
from pydantic import ValidationError

from worldmock.foundation.models import DeepFrozenModel, require_finite_number
from worldmock.exchange_engine.ledger.ledger_models import Conservation


class NestedModel(DeepFrozenModel):
    """@brief 验证嵌套容器真正冻结且可序列化的测试模型。"""

    values: dict[str, object]


def test_deep_model_freezes_nested_containers_and_serializes():
    """@brief 递归冻结不只冻结顶层字段，JSON仍保留全部原值。"""
    original = {"a": [1, 2]}
    model = NestedModel(values=original)
    original["a"].append(3)
    assert model.values["a"] == (1, 2)
    with pytest.raises(TypeError):
        model.values["a"] = (3,)
    with pytest.raises(ValidationError):
        model.values = {}
    assert model.model_dump(mode="json") == {"values": {"a": [1, 2]}}


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "1"])
def test_finite_number_primitive_rejects_invalid_json_number(value, caplog):
    """@brief 通用数值原语拒绝无效JSON数值并记录英文错误日志。"""
    with pytest.raises(ValueError):
        require_finite_number(value, "amount")
    assert caplog.records


def test_surviving_definitions_have_one_canonical_owner():
    """@brief 旧品类位置不保留兼容重导出，消费者直接引用搬移后的定义。"""
    from worldmock.resource_engine.catalog import models
    from worldmock.exchange_engine.commerce import operators
    assert DeepFrozenModel.__module__ == 'worldmock.foundation.models'
    assert Conservation.__module__ == 'worldmock.exchange_engine.ledger.ledger_models'
    assert models.DeepFrozenModel is DeepFrozenModel
    assert operators.require_finite_number is require_finite_number
    assert tuple(Conservation) == ("open", "closed")
