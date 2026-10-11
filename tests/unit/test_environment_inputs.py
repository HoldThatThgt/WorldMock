"""验证完整环境输入、原文字节身份与独立失败汇总。"""

from pathlib import Path
import shutil
import tomllib

import pytest
from pydantic import ValidationError

from worldmock.environment import EnvironmentInputError, load_environment, parse_persona_file, parse_run_config_file
from worldmock.input_models import ModelConfig, ModelMetadata, PersonaConfig, RunConfig
from worldmock.model_endpoint import parse_model_metadata
from worldmock.validation import validate_environment


SOURCE = Path(__file__).resolve().parents[1] / "fixtures/dataset-environment"


def metadata(config):
    return ModelMetadata(requested_model=config.requested_model, metadata_url=config.metadata_url,
                         context_window=524288, max_output_tokens=32768)


@pytest.fixture
def environment(tmp_path):
    target = tmp_path / "environment"
    shutil.copytree(SOURCE, target)
    return target


def test_full_inputs_are_loaded_without_writes(environment):
    before = {path.relative_to(environment): path.read_bytes() for path in environment.rglob("*") if path.is_file()}
    loaded = load_environment(environment, metadata_loader=metadata)
    assert loaded.persona.agent_id == 1
    assert loaded.home_location_id == "home"
    assert loaded.workplace_location_id == "office"
    assert loaded.model_metadata.context_window == 524288
    after = {path.relative_to(environment): path.read_bytes() for path in environment.rglob("*") if path.is_file()}
    assert after == before


def test_valid_environment_result_has_only_public_identity(environment):
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": True, "environment_id": "artificial-test", "environment_version": "3",
    }


def test_independent_invalid_inputs_are_all_reported(environment):
    (environment / "persona.toml").write_bytes(b"schema_version = 1\n")
    (environment / "config.toml").write_bytes(b"schema_version = 1\n")
    (environment / "catalog/app-catalog.jsonl").write_bytes(b"[]\n")
    result = validate_environment(environment, metadata_loader=metadata)
    assert result["valid"] is False
    assert {row["path"] for row in result["errors"]} == {"persona.toml", "config.toml", "catalog"}
    assert {row["code"] for row in result["errors"]} >= {"PERSONA_INVALID", "RUN_CONFIG_INVALID"}
    assert all(set(row) == {"path", "code", "reason"} for row in result["errors"])


def test_persona_hash_uses_raw_file_not_story_only(environment):
    original, original_hash = parse_persona_file(environment / "persona.toml")
    with (environment / "persona.toml").open("ab") as output:
        output.write(b"\n# Same persona facts, different source bytes.\n")
    changed, changed_hash = parse_persona_file(environment / "persona.toml")
    assert original == changed
    assert original_hash != changed_hash


@pytest.mark.parametrize("filename,parse,code", [
    ("persona.toml", parse_persona_file, "PERSONA_INVALID"),
    ("config.toml", parse_run_config_file, "RUN_CONFIG_INVALID"),
])
@pytest.mark.parametrize("prefix,ending", [(b"\xef\xbb\xbf", b"\n"), (b"", b"\r\n")])
def test_raw_input_encoding_is_strict(environment, filename, parse, code, prefix, ending):
    path = environment / filename
    path.write_bytes(prefix + path.read_bytes().replace(b"\n", ending))
    with pytest.raises(EnvironmentInputError) as caught:
        parse(path)
    assert caught.value.code == code


@pytest.mark.parametrize("field,value", [
    ("initial_relaxation_need", -0.1), ("initial_relaxation_need", 1.1),
    ("initial_social_need", float("inf")), ("initial_social_need", float("nan")),
    ("agent_id", True), ("initial_balance_fen", -1), ("background_story", "short"),
    ("entertainment_need", 0.2), ("mood_valence", 0.2), ("contacts", []),
])
def test_persona_invalid_or_retired_values_fail(field, value):
    payload = tomllib.loads((SOURCE / "persona.toml").read_text(encoding="utf-8"))
    payload[field] = value
    with pytest.raises(ValidationError):
        PersonaConfig.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("seed", True), ("seed", 7.0), ("candidate_pool_limit", 100),
    ("max_same_moment_decisions", 6), ("idle_reconsideration_interval_seconds", 900),
    ("scenario_id", "old"),
])
def test_run_configuration_rejects_retired_or_coerced_values(field, value):
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))
    payload[field] = value
    with pytest.raises(ValidationError):
        RunConfig.model_validate(payload)


@pytest.mark.parametrize("field,low,high", [
    ("max_retries", 0, 10), ("transport_max_retries", 0, 10), ("max_react_turns", 1, 64),
    ("persona_validation_max_retries", 0, 5), ("reflection_validation_max_retries", 0, 5),
    ("day_plan_validation_max_retries", 0, 5),
])
def test_model_budgets_are_strict_bounded_integers(field, low, high):
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))["model"]
    for value in (low, high):
        assert getattr(ModelConfig.model_validate({**payload, field: value}), field) == value
    for value in (low - 1, high + 1, True, "1", 1.5):
        with pytest.raises(ValidationError):
            ModelConfig.model_validate({**payload, field: value})


@pytest.mark.parametrize("payload", [
    {}, {"data": []}, {"data": [{"id": "another", "context_window": 524288, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": True, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": 262143, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": 262144, "max_output_tokens": 262145}]},
])
def test_model_metadata_requires_actual_matching_capacity(payload):
    config, _ = parse_run_config_file(SOURCE / "config.toml")
    with pytest.raises(ValueError):
        parse_model_metadata(config.model, payload)


def test_duplicate_model_metadata_is_rejected():
    config, _ = parse_run_config_file(SOURCE / "config.toml")
    row = {"id": "test-model", "context_window": 524288, "max_output_tokens": 32768}
    with pytest.raises(ValueError):
        parse_model_metadata(config.model, {"data": [row, row]})


def test_initial_location_can_differ_from_home(environment):
    path = environment / "persona.toml"
    path.write_bytes(path.read_bytes().replace('initial_location = "家"'.encode(),
                                              'initial_location = "公司"'.encode()))
    loaded = load_environment(environment, metadata_loader=metadata)
    assert loaded.home_location_id == "home"
    assert loaded.initial_location_id == "office"
