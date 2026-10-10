"""@brief 真实清单入口先拒绝旧规则版本，不让形状错误掩盖版本错误。"""

import pytest

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.exchange.constants import RULES_VERSION
from worldmock.footprints.models import canonical_json


@pytest.mark.parametrize("payload,code", [
    ({"rules_version": RULES_VERSION - 1, "retired": True}, "RULES_VERSION_MISMATCH"),
    ({"rules_version": RULES_VERSION + 1, "format_version": "old"}, "RULES_VERSION_MISMATCH"),
    ({"rules_version": RULES_VERSION, "format_version": "old"}, "ENVIRONMENT_KIND_INVALID"),
    ({"rules_version": True}, "ENVIRONMENT_KIND_INVALID"),
    ({"rules_version": False}, "ENVIRONMENT_KIND_INVALID"),
    ({"rules_version": str(RULES_VERSION - 1)}, "ENVIRONMENT_KIND_INVALID"),
    ({"rules_version": float(RULES_VERSION - 1)}, "ENVIRONMENT_KIND_INVALID"),
    ({}, "ENVIRONMENT_KIND_INVALID"),
])
def test_manifest_rule_version_priority_precedes_strict_shape(tmp_path, payload, code):
    """@brief 用真实文件验证严格整数比较及错误优先级，不构造兼容环境。"""
    (tmp_path / "manifest.json").write_bytes(canonical_json(payload) + b"\n")
    with pytest.raises(RegistrationError) as captured:
        load_catalog(tmp_path, 7)
    assert captured.value.code == code
