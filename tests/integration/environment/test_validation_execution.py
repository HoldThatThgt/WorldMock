"""完整环境纯校验的当前入口行为：只读输入，不调用生成模型。"""

import json

from tests.life.environment_inputs import copy_environment
from worldmock.validation import validate_environment


def test_environment_validation_aggregates_independent_errors_without_reading_metadata(tmp_path):
    root = copy_environment(tmp_path / "input")
    (root / "persona.toml").write_text("not TOML [", encoding="utf-8")
    (root / "config.toml").write_text("not TOML [", encoding="utf-8")
    calls = []
    result = validate_environment(root, metadata_loader=lambda model: calls.append(model))
    assert result["valid"] is False
    assert {item["path"] for item in result["errors"]} == {"persona.toml", "config.toml"}
    assert calls == []


def test_environment_validation_reports_metadata_failure_as_config_error(tmp_path):
    root = copy_environment(tmp_path / "input")

    def offline(model):
        raise OSError("Model metadata service unavailable")

    result = validate_environment(root, metadata_loader=offline)
    assert result["valid"] is False
    assert result["errors"] == [{"path": "config.toml", "code": "RUN_CONFIG_INVALID",
                                 "reason": "Model metadata service unavailable"}]


def test_valid_environment_returns_only_published_identity_without_creating_assets(tmp_path):
    from tests.frozen_inputs import fixture_metadata

    root = copy_environment(tmp_path / "input")
    result = validate_environment(root, metadata_loader=fixture_metadata)
    manifest = json.loads((root / "catalog" / "manifest.json").read_text(encoding="utf-8"))
    assert result == {"valid": True, "environment_id": manifest["environment_id"],
                      "environment_version": manifest["environment_version"]}
    assert set(item.name for item in root.iterdir()) == {"catalog", "persona.toml", "config.toml"}


def test_environment_validation_checks_the_whole_persona_table_without_any_model_client(tmp_path, monkeypatch):
    """纯校验就是人设表的全部检查：独立模型客户端一旦被构造就失败，合格的人设表照样通过，缺一个卡片字段时指出它。"""
    from tests.frozen_inputs import fixture_metadata
    from tests.life.persona_inputs import drop_field, edit_persona
    from worldmock import model_endpoint

    def refuse(*arguments, **keywords):
        raise AssertionError("Environment validation must not build a model client")

    monkeypatch.setattr(model_endpoint.IndependentModelClient, "__init__", refuse)
    root = copy_environment(tmp_path / "input")
    assert validate_environment(root, metadata_loader=fixture_metadata)["valid"] is True
    edit_persona(root, lambda document: drop_field(document, "tastes.media"))
    assert validate_environment(root, metadata_loader=fixture_metadata) == {
        "valid": False, "errors": [{"path": "persona.toml", "code": "PERSONA_INVALID",
                                    "reason": "tastes.media is required"}]}
    assert set(item.name for item in root.iterdir()) == {"catalog", "persona.toml", "config.toml"}
