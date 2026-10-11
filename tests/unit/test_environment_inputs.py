"""验证完整环境输入、原文字节身份与独立失败汇总。"""

from pathlib import Path
import shutil
import tomllib

import pytest
from pydantic import ValidationError

from tests.life.persona_inputs import declare_rhythm as declare
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
        "valid": True, "environment_id": "artificial-test", "environment_version": "2",
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


def rejected(environment, **values):
    """声明被真实人设文件解析拒绝时的（文件, 登记码, 英文原因）。"""
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(declare(environment, **values))
    return caught.value.path, caught.value.code, caught.value.reason


PAIR_RULES = [
    ({"leave_work_window": (18.5, 19.0), "arrive_home_window": (18.0, 18.5)},
     {"leave_work_window": (18.0, 18.5), "arrive_home_window": (18.0, 18.5)},
     "leave_work_window must not start after arrive_home_window"),
    ({"dinner_window": (19.0, 20.0), "bedtime_window": (19.5, 20.5)},
     {"dinner_window": (19.0, 20.0), "bedtime_window": (20.0, 21.0)},
     "bedtime_window must not overlap dinner window"),
    ({"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0)},
     {"lunch_window": (11.75, 12.75), "dinner_window": (12.75, 13.5)},
     "lunch_window must end before dinner_window starts"),
    ({"lunch_window": (12.0, 13.0), "bedtime_window": (9.0, 10.0)},
     {"lunch_window": (12.0, 13.0), "bedtime_window": (12.0, 13.0)},
     "bedtime_window must not start before lunch_window"),
    ({"leave_work_window": (23.0, 23.5), "bedtime_window": (22.0, 23.0)},
     {"leave_work_window": (22.0, 22.5), "bedtime_window": (22.0, 23.0)},
     "leave_work_window must not start after bedtime_window"),
    ({"arrive_home_window": (22.5, 23.0), "bedtime_window": (22.0, 23.0)},
     {"arrive_home_window": (22.0, 22.5), "bedtime_window": (22.0, 23.0)},
     "arrive_home_window must not start after bedtime_window"),
]


@pytest.mark.parametrize("contradiction,boundary,reason", PAIR_RULES, ids=[row[2] for row in PAIR_RULES])
def test_contradictory_rhythm_declarations_are_rejected_with_the_verbatim_reason(
        environment, tmp_path, contradiction, boundary, reason):
    """六条成对关系：不成立时以 PERSONA_INVALID 原样返回英文原因；两端恰好相等时通过，声明值原样保留。"""
    assert rejected(environment, **contradiction) == ("persona.toml", "PERSONA_INVALID", reason)
    accepted = tmp_path / "accepted"
    shutil.copytree(SOURCE, accepted)
    persona, _ = parse_persona_file(declare(accepted, **boundary))
    assert {name: tuple(getattr(persona, name)) for name in boundary} == boundary


def test_overlapping_lunch_and_dinner_never_reach_the_engine(environment):
    """午饭与晚饭重叠的声明在加载时被拒绝，完整环境加载与纯校验都不会走到引擎装配饭点的那一步。"""
    reason = "lunch_window must end before dinner_window starts"
    declare(environment, lunch_window=(12.0, 13.5), dinner_window=(13.0, 14.0))
    with pytest.raises(EnvironmentInputError) as caught:
        load_environment(environment, metadata_loader=metadata)
    assert (caught.value.path, caught.value.code, caught.value.reason) == ("persona.toml", "PERSONA_INVALID", reason)
    result = validate_environment(environment, metadata_loader=metadata)
    assert result == {"valid": False, "errors": [{"path": "persona.toml", "code": "PERSONA_INVALID",
                                                  "reason": reason}]}


@pytest.mark.parametrize("declared", [
    {"bedtime_window": (0.5, 1.5), "leave_work_window": (18.0, 18.5)},
    {"bedtime_window": (1.0, 1.5), "dinner_window": (19.0, 20.0)},
    {"bedtime_window": (5.9, 7.0), "dinner_window": (19.0, 20.0), "lunch_window": (12.0, 13.0),
     "leave_work_window": (18.0, 18.5), "arrive_home_window": (18.5, 19.0)},
])
def test_bedtime_starting_after_midnight_is_read_as_the_next_day(environment, declared):
    """就寝起点小于 6 时读作次日凌晨：午夜之后才睡的人物可以同时声明晚饭、下班、到家和就寝。"""
    persona, _ = parse_persona_file(declare(environment, **declared))
    assert tuple(persona.bedtime_window) == declared["bedtime_window"]


def test_bedtime_next_day_reading_stops_at_six(environment, tmp_path):
    """起点不小于 6 的就寝按当日读：09:00 的就寝排在午饭之前被拒绝，06:00 的就寝排在晚饭之前被拒绝。"""
    assert rejected(environment, bedtime_window=(9.0, 10.0), lunch_window=(12.0, 13.0))[2] == (
        "bedtime_window must not start before lunch_window")
    other = tmp_path / "other"
    shutil.copytree(SOURCE, other)
    assert rejected(other, bedtime_window=(6.0, 7.0), dinner_window=(19.0, 20.0))[2] == (
        "bedtime_window must not overlap dinner window")


@pytest.mark.parametrize("declared,reason", [
    ({"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0), "leave_work_window": (23.0, 23.5),
      "arrive_home_window": (22.5, 23.0), "bedtime_window": (9.0, 10.0)},
     "leave_work_window must not start after arrive_home_window"),
    ({"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0), "leave_work_window": (22.0, 22.5),
      "arrive_home_window": (22.5, 23.0), "bedtime_window": (9.0, 10.0)},
     "bedtime_window must not overlap dinner window"),
    ({"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0), "leave_work_window": (16.0, 16.5),
      "arrive_home_window": (16.5, 17.0), "bedtime_window": (14.0, 15.0)},
     "lunch_window must end before dinner_window starts"),
    ({"lunch_window": (18.0, 19.0), "leave_work_window": (16.0, 16.5), "arrive_home_window": (16.5, 17.0),
      "bedtime_window": (14.0, 15.0)}, "bedtime_window must not start before lunch_window"),
    ({"leave_work_window": (16.0, 16.5), "arrive_home_window": (16.5, 17.0), "bedtime_window": (14.0, 15.0)},
     "leave_work_window must not start after bedtime_window"),
    ({"arrive_home_window": (16.5, 17.0), "bedtime_window": (14.0, 15.0)},
     "arrive_home_window must not start after bedtime_window"),
    ({"lunch_window": (13.0, 12.0), "dinner_window": (12.0, 12.5), "bedtime_window": (23.0, 31.0)},
     "lunch_window must satisfy 0 <= start < end <= 24"),
    ({"dinner_window": (8.0, 21.0), "bedtime_window": (23.0, 31.0)}, "dinner_window span must not exceed 12 hours"),
    ({"bedtime_window": (23.0, 31.0)}, "bedtime_window must satisfy 0 <= start < end <= 30"),
    ({"arrive_home_window": (18.0,)}, "arrive_home_window must contain two finite hours"),
])
def test_only_the_first_failed_rhythm_check_is_reported(environment, declared, reason):
    """检查次序固定：先按午饭、晚饭、下班、到家、就寝检查每个时段自身的形状，再按六条成对关系的次序，只报第一条。"""
    assert rejected(environment, **declared) == ("persona.toml", "PERSONA_INVALID", reason)


def test_off_grid_rhythm_declarations_load_unchanged(environment):
    """声明值不要求落在任何格点上，也不要求是整分钟：七项都不在格点上的人设通过加载，取值原样保留。"""
    declared = {"chronotype_midsleep": 4.1, "sleep_need_hours": 7.6, "lunch_window": (12.1, 12.2),
                "leave_work_window": (17.9, 18.05), "arrive_home_window": (18.25, 18.75),
                "dinner_window": (18.3333, 19.6), "bedtime_window": (23.55, 24.45)}
    loaded = load_environment(declare(environment, **declared).parent, metadata_loader=metadata).persona
    assert (loaded.chronotype_midsleep, loaded.sleep_need_hours) == (4.1, 7.6)
    assert {name: tuple(getattr(loaded, name)) for name in declared if name.endswith("_window")} == {
        name: value for name, value in declared.items() if name.endswith("_window")}


@pytest.mark.parametrize("original,replacement,reason", [
    ("initial_energy_kcal = 1800.0", "initial_energy_kcal = 2400.5", "initial energy exceeds maximum energy"),
    ("initial_relaxation_need = 0.2", "initial_relaxation_need = 1.5", "Persona file validation failed"),
    ("breaks = [[720, 780]]", "breaks = [[100, 200]]", "Persona file validation failed"),
    ("mass_kg = 65.0", "mass_kg = 300.5", "Persona file validation failed"),
])
def test_other_persona_failures_keep_their_reasons(environment, original, replacement, reason):
    """只有人物模型级的作息矛盾原样返回原因；初始能量超上限、字段取值与班次内部错误的原因不变。"""
    path = environment / "persona.toml"
    text = path.read_text(encoding="utf-8")
    assert original in text
    path.write_bytes(text.replace(original, replacement, 1).encode("utf-8"))
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(path)
    assert (caught.value.code, caught.value.reason) == ("PERSONA_INVALID", reason)
