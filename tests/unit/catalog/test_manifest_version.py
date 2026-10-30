"""@brief 真实清单入口先拒绝旧规则版本，不让形状错误掩盖版本错误。"""

import json

import pytest

from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.loader import load_catalog, validate_catalog_files
from worldmock.exchange_engine.definitions.constants import RULES_VERSION
from worldmock.foundation.serialization import canonical_json


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


@pytest.mark.parametrize("change,code,reason", [
    ({"rules_version": 5}, "RULES_VERSION_MISMATCH", "The environment was published for a different rules version."),
    ({"rules_version": 5, "format_version": 4}, "RULES_VERSION_MISMATCH",
     "The environment was published for a different rules version."),
    ({"rules_version": 7}, "RULES_VERSION_MISMATCH", "The environment was published for a different rules version."),
    ({"format_version": 4}, "ENVIRONMENT_KIND_INVALID", "A declared kind is outside the fixed set."),
    ({"format_version": 5}, "ENVIRONMENT_KIND_INVALID", "A declared kind is outside the fixed set."),
])
def test_otherwise_complete_package_with_an_old_version_number_is_refused(catalog_dir, change, code, reason):
    """@brief 其余内容全部合法的环境包，只因版本号是旧值就在加载和纯校验的第一步被拒绝。"""
    assert RULES_VERSION == 6
    manifest = json.loads((catalog_dir / "manifest.json").read_bytes())
    assert (manifest["rules_version"], manifest["format_version"]) == (6, 6)
    (catalog_dir / "manifest.json").write_bytes(canonical_json({**manifest, **change}) + b"\n")
    for entry in (lambda: load_catalog(catalog_dir, 7), lambda: validate_catalog_files(catalog_dir)):
        with pytest.raises(RegistrationError) as captured:
            entry()
        assert (captured.value.code, captured.value.message) == (code, reason)
