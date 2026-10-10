"""完整环境纯校验与真实画像覆盖输出的当前入口行为。"""

import json
import os

import pytest

from tests.integration.runtime.current_contract_fixtures import compiled_persona, inputs, metadata_service
from tests.life.environment_inputs import copy_environment
from tests.profile.conftest import make_card
from worldmock.profile.coverage import PersonaCoverageError
from worldmock.profile.models import Tastes
from worldmock.validation import validate_environment, validate_persona_coverage


def configured_model(monkeypatch, frozen):
    from agentsociety2.config.config import Config

    calls = metadata_service(monkeypatch, frozen)
    for name in ("LLM_API_BASE", "CODER_LLM_API_BASE"):
        monkeypatch.setattr(Config, name, frozen.config.model.base_url)
    for name in ("LLM_MODEL", "CODER_LLM_MODEL"):
        monkeypatch.setattr(Config, name, frozen.config.model.requested_model)
    monkeypatch.setattr(Config, "EMBEDDING_MODEL", "")
    for name in ("WORKSPACE_PATH", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE", "AGENTSOCIETY_LLM_MODEL",
                 "AGENTSOCIETY_CODER_LLM_API_KEY", "AGENTSOCIETY_CODER_LLM_API_BASE", "AGENTSOCIETY_CODER_LLM_MODEL",
                 "AGENTSOCIETY_EMBEDDING_MODEL", "LITELLM_LOCAL_MODEL_COST_MAP", "AGENTSOCIETY_LLM_REQUEST_TIMEOUT",
                 "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY", "PYTHONPATH"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    return calls


def mapped_result(frozen):
    result = compiled_persona(frozen)
    habit = result.card.habits[0].model_copy(update={"response": frozen.catalog.locations["park"].display_name})
    food = next(item.name for item in frozen.catalog.goods.values() if item.category in {"meal", "snack", "drink"})
    tastes = Tastes(content_genres=("纪录片",), food_preferences=(food,),
                    media=(frozen.catalog.apps["bilibili"].name,))
    return result.model_copy(update={"card": make_card(habits=(habit,), tastes=tastes)})


@pytest.mark.parametrize("asynchronous", [False, True])
def test_persona_coverage_compiles_once_and_writes_real_identity_and_targets(tmp_path, monkeypatch, asynchronous):
    frozen = inputs()
    calls = configured_model(monkeypatch, frozen)
    root = copy_environment(tmp_path / "input")
    compiled = mapped_result(frozen)
    compiler_calls = []

    async def compile_async(persona):
        compiler_calls.append(persona)
        return compiled

    def compile_sync(persona):
        compiler_calls.append(persona)
        return compiled

    destination = tmp_path / "reports" / "coverage.json"
    report = validate_persona_coverage(root, persona_compiler=compile_async if asynchronous else compile_sync,
                                      coverage_out=destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert len(compiler_calls) == 1 and len(calls) == 1
    assert document["persona_sha256"] == frozen.persona_sha256 and document["threshold"] == 0.8
    assert document["agents"][0]["agent_id"] == frozen.persona.agent_id
    assert document["agents"][0]["executable_ratio"] == 1 and document["agents"][0]["passed"] is True
    assert report["coverage_path"] == str(destination) and report["input_warnings"] == []


def test_failed_persona_coverage_keeps_actual_missing_mappings_in_written_report(tmp_path, monkeypatch):
    frozen = inputs()
    configured_model(monkeypatch, frozen)
    root = copy_environment(tmp_path / "input")
    compiled = compiled_persona(frozen)
    destination = tmp_path / "reports" / "rejected.json"
    with pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED"):
        validate_persona_coverage(root, persona_compiler=lambda persona: compiled, coverage_out=destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["agents"][0]["passed"] is False and document["agents"][0]["missing"]
    assert set(item.name for item in root.iterdir()) == {"catalog", "persona.toml", "config.toml"}


def test_environment_validation_aggregates_independent_errors_without_compiling(tmp_path):
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
