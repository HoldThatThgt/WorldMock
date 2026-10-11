"""验证完整环境输入、原文字节身份与独立失败汇总。"""

from pathlib import Path
import shutil
import tomllib

import pytest
from pydantic import ValidationError

from tests.life.persona_inputs import REQUIRED_PERSONA_FIELDS, declare, drop_field, edit_persona
from worldmock.environment import EnvironmentInputError, load_environment, parse_persona_file, parse_run_config_file
from worldmock.input_models import ModelConfig, ModelMetadata, PersonaConfig, RunConfig
from worldmock.model_endpoint import parse_model_metadata
from worldmock.profile.models import persona_card
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
    (environment / "persona.toml").write_bytes(b"schema_version = 2\n")
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
    ("agent_id", True), ("initial_balance_fen", -1), ("background_story", ""),
    ("entertainment_need", 0.2), ("mood_valence", 0.2), ("contacts", []),
    ("social_need_growth_per_hour", 0.01), ("social_need_growth_per_hour", 0.11),
    ("chronotype_midsleep", 24.0), ("chronotype_midsleep", -0.1), ("sleep_need_hours", 6.9),
    ("sleep_need_hours", 9.1), ("sleep_need_hours", float("nan")), ("coping", ""), ("coping", "压" * 61),
    ("big_five", {"openness": 4}), ("world_dynamics", {"social_need_growth_per_hour": 0.06}),
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
    ("reflection_validation_max_retries", 0, 5), ("day_plan_validation_max_retries", 0, 5),
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


def rejected(environment, mutate=None, **values):
    """人设表被真实人设文件解析拒绝时的（文件, 登记码, 英文原因）。"""
    path = edit_persona(environment, mutate) if mutate is not None else declare(environment, **values)
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(path)
    return caught.value.path, caught.value.code, caught.value.reason


def test_schema_version_one_is_rejected_without_an_old_format_reader(environment):
    """persona.toml 的结构版本是 2：写 1 的文件直接被严格字面量拒绝，纯校验给出同一条 {path, code, reason}。"""
    reason = "schema_version: Input should be 2"
    assert rejected(environment, schema_version=1) == ("persona.toml", "PERSONA_INVALID", reason)
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": False, "errors": [{"path": "persona.toml", "code": "PERSONA_INVALID", "reason": reason}]}


def test_run_configuration_no_longer_has_a_persona_validation_budget():
    """画像输出校验重试随模型抽取一起删除：config.toml 的 [model] 里再写这个键就是未知字段。"""
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))["model"]
    assert "persona_validation_max_retries" not in ModelConfig.model_fields
    with pytest.raises(ValidationError):
        ModelConfig.model_validate({**payload, "persona_validation_max_retries": 2})


@pytest.mark.parametrize("field", REQUIRED_PERSONA_FIELDS)
def test_each_required_persona_field_is_rejected_when_missing_and_named(environment, field):
    """人设表的必填字段没有缺省值：缺任何一个都以 PERSONA_INVALID 拒绝，原因写出这个字段的点分路径。"""
    assert rejected(environment, lambda document: drop_field(document, field)) == (
        "persona.toml", "PERSONA_INVALID", f"{field} is required")


@pytest.mark.parametrize("field", ["health_habits", "social_style", "leave_work_window", "arrive_home_window",
                                   "work_schedule"])
def test_only_the_five_optional_fields_may_be_left_out(environment, field):
    """可以不写的只有健康习惯、社交风格、下班与到家时段和工作安排：夹具人物写上或删掉它们都能加载。"""
    values = {"health_habits": "每周慢跑", "social_style": "话少", "leave_work_window": (18.0, 18.5),
              "arrive_home_window": (18.5, 19.0)}
    if field in values:
        assert getattr(parse_persona_file(declare(environment, **{field: values[field]}))[0], field) is not None
    assert getattr(parse_persona_file(declare(environment, **{field: None}))[0], field) is None


@pytest.mark.parametrize("dotted", ["nickname", "identity.gender", "situation.mood", "relationships.0.birthday",
                                    "habits.0.frequency", "tastes.music", "body.height_cm"])
def test_unknown_persona_keys_are_rejected_at_every_level(environment, dotted):
    """人设表是严格的：顶层、每张子表和数组的每一项里出现未知键都被拒绝，原因写出这个键。"""
    def mutate(document):
        *parents, last = dotted.split(".")
        node = document
        for part in parents:
            node = node[int(part)] if part.isdigit() else node[part]
        node[last] = "extra"

    assert rejected(environment, mutate) == (
        "persona.toml", "PERSONA_INVALID", f"{dotted} is not a persona.toml field")


WINDOW_REASON = "must be two finite hours with start < end"


@pytest.mark.parametrize("field", ["wake_window", "leave_home_window", "lunch_window", "dinner_window",
                                   "bedtime_window", "leave_work_window", "arrive_home_window"])
@pytest.mark.parametrize("window", [(12.0,), (12.0, 13.0, 14.0), (13.0, 13.0), (13.0, 12.0),
                                    (float("nan"), 13.0), (12.0, float("inf"))])
def test_every_window_needs_two_finite_hours_with_start_before_end(environment, field, window):
    """每个时段只有一条检查：恰好两个有限数，起点小于终点；不成立时原因写出字段和这一条。"""
    assert rejected(environment, **{field: window}) == (
        "persona.toml", "PERSONA_INVALID", f"{field}: {WINDOW_REASON}")


def test_habit_windows_follow_the_same_single_rule(environment):
    """习惯签名的时段用同一条检查，原因带着数组下标。"""
    def mutate(document):
        document["habits"][1]["time_window"] = [23.0, 22.5]

    assert rejected(environment, mutate) == (
        "persona.toml", "PERSONA_INVALID", f"habits.1.time_window: {WINDOW_REASON}")


#: 修订前被成对关系、0 到 24 与 30 小时上界、12 小时跨度拒绝的写法，现在都能加载。
FORMERLY_REJECTED = [
    {"bedtime_window": (5.0, 7.0)},
    {"bedtime_window": (19.5, 20.5), "dinner_window": (19.0, 20.0)},
    {"bedtime_window": (17.0, 18.0), "dinner_window": (19.0, 20.0)},
    {"bedtime_window": (9.0, 10.0), "lunch_window": (12.0, 13.0)},
    {"leave_work_window": (25.0, 25.5), "arrive_home_window": (24.25, 24.75)},
    {"leave_work_window": (18.5, 19.0), "arrive_home_window": (18.0, 18.5)},
    {"lunch_window": (12.0, 13.5), "dinner_window": (13.0, 14.0)},
    {"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0)},
    {"lunch_window": (1.0, 15.0)},
    {"bedtime_window": (23.0, 31.0)},
    {"dinner_window": (30.5, 31.25)},
    {"wake_window": (-1.0, 0.5)},
]


@pytest.mark.parametrize("declared", FORMERLY_REJECTED, ids=[", ".join(f"{name}={value}" for name, value in row.items())
                                                             for row in FORMERLY_REJECTED])
def test_formerly_rejected_windows_load_and_reach_the_card_as_written(environment, declared):
    """时段之间没有任何先后、重叠、范围或跨度检查：这些写法都通过完整环境加载，人设表和卡片里是原值。"""
    loaded = load_environment(declare(environment, **declared).parent, metadata_loader=metadata).persona
    assert {name: tuple(getattr(loaded, name)) for name in declared} == declared
    anchors = persona_card(loaded).time_anchors
    for name, (start, end) in declared.items():
        stored = getattr(anchors, name.removesuffix("_window"))
        assert (stored.start_hour, stored.end_hour) == (start, end)
    assert validate_environment(environment, metadata_loader=metadata)["valid"] is True


def test_off_grid_rhythm_values_load_unchanged(environment):
    """取值不要求落在任何格点上，也不要求是整分钟：九项都不在格点上的人设通过加载，取值原样保留。"""
    declared = {"chronotype_midsleep": 4.1, "sleep_need_hours": 7.6, "wake_window": (6.95, 7.4),
                "leave_home_window": (8.05, 8.3), "lunch_window": (12.1, 12.2),
                "leave_work_window": (17.9, 18.05), "arrive_home_window": (18.25, 18.75),
                "dinner_window": (18.3333, 19.6), "bedtime_window": (23.55, 24.45)}
    loaded = load_environment(declare(environment, **declared).parent, metadata_loader=metadata).persona
    assert (loaded.chronotype_midsleep, loaded.sleep_need_hours) == (4.1, 7.6)
    assert {name: tuple(getattr(loaded, name)) for name in declared if name.endswith("_window")} == {
        name: value for name, value in declared.items() if name.endswith("_window")}


@pytest.mark.parametrize("mutate,reason", [
    (lambda doc: doc["identity"].update(age=15), "identity.age: Input should be greater than or equal to 16"),
    (lambda doc: doc["identity"].update(age=101), "identity.age: Input should be less than or equal to 100"),
    (lambda doc: doc["identity"].update(age="32"), "identity.age: Input should be a valid integer"),
    (lambda doc: doc["identity"].update(occupation="长" * 21),
     "identity.occupation: String should have at most 20 characters"),
    (lambda doc: doc["relationships"][0].update(intimacy=31),
     "relationships.0.intimacy: Input should be less than or equal to 30"),
    (lambda doc: doc["relationships"][1].update(channels=["微信", "短信"]),
     "relationships.1.channels.1: Input should be '电话', '微信' or '见面'"),
    (lambda doc: doc["relationships"][0].update(channels=[]),
     "relationships.0.channels: List should have at least 1 item after validation, not 0"),
    (lambda doc: doc["relationships"][0].update(channels=["电话", "微信", "见面", "电话"]),
     "relationships.0.channels: List should have at most 3 items after validation, not 4"),
    (lambda doc: doc["tastes"].update(media=["长视频", " "]),
     "tastes.media.1: String should match pattern '\\S'"),
    (lambda doc: doc["tastes"].update(media=[f"媒介{index}" for index in range(5)]),
     "tastes.media: List should have at most 4 items after validation, not 5"),
    (lambda doc: doc["tastes"].update(content_genres=[f"题材{index}" for index in range(7)]),
     "tastes.content_genres: List should have at most 6 items after validation, not 7"),
    (lambda doc: doc["habits"][0].update(response="做" * 41),
     "habits.0.response: String should have at most 40 characters"),
    (lambda doc: doc.update(sleep_need_hours=6.5), "sleep_need_hours: Input should be greater than or equal to 7"),
    (lambda doc: doc.update(social_need_growth_per_hour=0.2),
     "social_need_growth_per_hour: Input should be less than or equal to 0.1"),
])
def test_table_format_and_formula_ranges_are_rejected_with_the_field_named(environment, mutate, reason):
    """保留的检查只有表的格式与计算要用的范围：类型、渠道闭集、数值范围、条数与字数上限；原因先写出错的字段。"""
    assert rejected(environment, mutate) == ("persona.toml", "PERSONA_INVALID", reason)


@pytest.mark.parametrize("field,key,limit", [("relationships", "name_or_appellation", 4), ("habits", "trigger", 5)])
def test_relationship_and_habit_counts_are_capped_for_the_prompt_budget(environment, field, key, limit):
    """关系至多 4 条、习惯至多 5 条，正好到上限可以加载；空数组也可以加载。"""
    def rows(count):
        def mutate(document):
            first = dict(document[field][0])
            document[field] = [{**first, key: f"条目{index}"} for index in range(count)]
        return mutate

    assert len(getattr(parse_persona_file(edit_persona(environment, rows(limit)))[0], field)) == limit
    assert rejected(environment, rows(limit + 1))[2] == (
        f"{field}: List should have at most {limit} items after validation, not {limit + 1}")
    assert getattr(parse_persona_file(declare(environment, **{field: []}))[0], field) == ()


def test_taste_duplicates_are_kept_as_written(environment):
    """口味里重复的词不替作者去重，卡片里照写的样子保留。"""
    def mutate(document):
        document["tastes"]["content_genres"] = ["悬疑", "悬疑", "纪录片"]

    loaded = parse_persona_file(edit_persona(environment, mutate))[0]
    assert persona_card(loaded).tastes.content_genres == ("悬疑", "悬疑", "纪录片")


def test_background_story_has_no_minimum_length(environment):
    """background_story 仍是必填文字，但不再有一百字符下限：一句话就能加载，零点计划原样读它。"""
    loaded = parse_persona_file(declare(environment, background_story="住在公司附近。"))[0]
    assert loaded.background_story == "住在公司附近。"


@pytest.mark.parametrize("original,replacement,reason", [
    ("initial_energy_kcal = 1800.0", "initial_energy_kcal = 2400.5", "initial energy exceeds maximum energy"),
    ("initial_relaxation_need = 0.2", "initial_relaxation_need = 1.5",
     "initial_relaxation_need: Input should be less than or equal to 1"),
    ("breaks = [[720, 780]]", "breaks = [[100, 200]]",
     "work_schedule.weekly.0: work shift breaks must lie within the shift"),
    ("mass_kg = 65.0", "mass_kg = 300.5", "body.mass_kg: Input should be less than or equal to 300"),
])
def test_other_persona_failures_name_the_field(environment, original, replacement, reason):
    """初始能量超上限沿用规定原因；其余字段取值与班次内部错误的原因先写出错的字段。"""
    path = environment / "persona.toml"
    text = path.read_text(encoding="utf-8")
    assert original in text
    path.write_bytes(text.replace(original, replacement, 1).encode("utf-8"))
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(path)
    assert (caught.value.code, caught.value.reason) == ("PERSONA_INVALID", reason)
