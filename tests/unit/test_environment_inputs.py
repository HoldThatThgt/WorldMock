"""验证完整环境输入、原文字节身份与独立失败汇总。"""

from pathlib import Path
import shutil
import tomllib

import pytest
from pydantic import ValidationError

from tests.frozen_inputs import assert_deeply_immutable
from tests.life.persona_inputs import REQUIRED_PERSONA_FIELDS, declare, drop_field, edit_persona
from worldmock.configuration.inputs.environment import (
    EnvironmentInputError,
    load_environment,
    parse_persona_file,
    parse_personas_dir,
    parse_run_config_file,
)
from worldmock.configuration.inputs.models import (
    ModelMetadata,
    ModelProfileConfig,
    ModelRoutingConfig,
    ModelStageOverride,
    PersonaConfig,
    RunConfig,
)
from worldmock.configuration.model.model_metadata import (
    load_active_metadata,
    parse_model_metadata,
    verify_active_credentials,
)
from worldmock.configuration.persona.persona_card import persona_card
from worldmock.configuration.persona.persona_text import count_hanzi, render_card
from worldmock.configuration.inputs.validation import validate_environment


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
    assert len(loaded.personas) == 1
    assert loaded.personas[0].agent_id == 1
    assert loaded.persona_by_id[1] == loaded.personas[0]
    assert loaded.persona_paths[1] == "personas/persona_1.toml"
    assert loaded.home_location_ids[1] == "home"
    assert loaded.workplace_location_ids[1] == "office"
    assert set(loaded.model_metadata) == {"reasoner", "fast_search"}
    assert loaded.model_metadata["reasoner"].context_window == 524288
    assert loaded.model_metadata["fast_search"].context_window == 524288
    assert_deeply_immutable(loaded)
    after = {path.relative_to(environment): path.read_bytes() for path in environment.rglob("*") if path.is_file()}
    assert after == before


#: 人设表里写成 TOML 数组的字段（时段、关系、习惯、口味），导出字典时保持 JSON 数组且逐值等于原文。
_PERSONA_ARRAYS = ("wake_window", "leave_home_window", "lunch_window", "dinner_window", "bedtime_window",
                   "relationships", "habits", "tastes")


def test_persona_and_config_serialize_without_warnings_and_keep_arrays_as_written(environment):
    """警告升成错误后，序列化人设表与运行配置都不抛；数组在模型里冻结成元组，dump(mode='json') 时还原成 JSON 数组。"""
    import warnings

    loaded = load_environment(environment, metadata_loader=metadata)
    persona = loaded.personas[0]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        persona.model_dump(mode="json")
        loaded.config.model_dump(mode="json")
    assert [str(item.message) for item in caught] == []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped_persona = persona.model_dump(mode="json")
        loaded.config.model_dump(mode="json")
    assert type(persona.lunch_window) is tuple and type(persona.relationships) is tuple
    assert type(persona.relationships[0].channels) is tuple and type(persona.tastes.media) is tuple
    assert type(persona.work_schedule.weekly["0"].breaks) is tuple
    assert type(persona.work_schedule.weekly["0"].breaks[0]) is tuple
    assert type(loaded.config.calendar.spring_festival_dates) is tuple
    written = tomllib.loads((environment / "personas" / "persona_1.toml").read_text(encoding="utf-8"))
    assert {key: dumped_persona[key] for key in _PERSONA_ARRAYS} == {key: written[key] for key in _PERSONA_ARRAYS}
    assert dumped_persona["work_schedule"]["weekly"] == written["work_schedule"]["weekly"]
    assert type(dumped_persona["relationships"]) is list
    assert type(dumped_persona["work_schedule"]["weekly"]["0"]["breaks"][0]) is list


def test_valid_environment_result_has_only_public_identity(environment):
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": True, "environment_id": "artificial-test", "environment_version": "4",
    }


def test_independent_invalid_inputs_are_all_reported(environment):
    (environment / "personas" / "persona_1.toml").write_bytes(b"schema_version = 3\n")
    (environment / "config.toml").write_bytes(b"schema_version = 3\n")
    (environment / "catalog/app-catalog.jsonl").write_bytes(b"[]\n")
    result = validate_environment(environment, metadata_loader=metadata)
    assert result["valid"] is False
    assert {row["path"] for row in result["errors"]} == {"personas/persona_1.toml", "config.toml", "catalog"}
    assert {row["code"] for row in result["errors"]} >= {"PERSONA_INVALID", "RUN_CONFIG_INVALID"}
    assert all(set(row) == {"path", "code", "reason"} for row in result["errors"])


def test_persona_hash_uses_raw_file_not_story_only(environment):
    persona_path = environment / "personas" / "persona_1.toml"
    original, original_hash = parse_persona_file(persona_path)
    with persona_path.open("ab") as output:
        output.write(b"\n# Same persona facts, different source bytes.\n")
    changed, changed_hash = parse_persona_file(persona_path)
    assert original == changed
    assert original_hash != changed_hash


@pytest.mark.parametrize("filename,parse,code", [
    ("personas/persona_1.toml", parse_persona_file, "PERSONA_INVALID"),
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
    payload = tomllib.loads((SOURCE / "personas" / "persona_1.toml").read_text(encoding="utf-8"))
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
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))["models"]["reasoner"]
    for value in (low, high):
        assert getattr(ModelProfileConfig.model_validate({**payload, field: value}), field) == value
    for value in (low - 1, high + 1, True, "1", 1.5):
        with pytest.raises(ValidationError):
            ModelProfileConfig.model_validate({**payload, field: value})


@pytest.mark.parametrize("payload", [
    {}, {"data": []}, {"data": [{"id": "another", "context_window": 524288, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": True, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": 262143, "max_output_tokens": 32768}]},
    {"data": [{"id": "test-model", "context_window": 262144, "max_output_tokens": 262145}]},
])
def test_model_metadata_requires_actual_matching_capacity(payload):
    config, _ = parse_run_config_file(SOURCE / "config.toml")
    with pytest.raises(ValueError):
        parse_model_metadata(config.models["reasoner"], payload)


def test_duplicate_model_metadata_is_rejected():
    config, _ = parse_run_config_file(SOURCE / "config.toml")
    row = {"id": "test-model", "context_window": 524288, "max_output_tokens": 32768}
    with pytest.raises(ValueError):
        parse_model_metadata(config.models["reasoner"], {"data": [row, row]})


def test_initial_location_can_differ_from_home(environment):
    path = environment / "personas" / "persona_1.toml"
    path.write_bytes(path.read_bytes().replace('initial_location = "家"'.encode(),
                                              'initial_location = "公司"'.encode()))
    loaded = load_environment(environment, metadata_loader=metadata)
    assert loaded.home_location_ids[1] == "home"
    assert loaded.initial_location_ids[1] == "office"


def rejected(environment, mutate=None, **values):
    """人设表被真实人设文件解析拒绝时的（文件, 登记码, 英文原因）。"""
    path = edit_persona(environment, mutate) if mutate is not None else declare(environment, **values)
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(path)
    return caught.value.path, caught.value.code, caught.value.reason


@pytest.mark.parametrize("version", [1, 2])
def test_old_schema_versions_are_rejected_without_an_old_format_reader(environment, version):
    """personas/*.toml 的结构版本是 3：写旧版的文件直接被严格字面量拒绝，纯校验给出同一条 {path, code, reason}。"""
    reason = "schema_version: Input should be 3"
    assert rejected(environment, schema_version=version) == ("personas/persona_1.toml", "PERSONA_INVALID", reason)
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": False, "errors": [{"path": "personas/persona_1.toml", "code": "PERSONA_INVALID", "reason": reason}]}


def config_rejected(environment, original: bytes, replacement: bytes):
    """把夹具 config.toml 里恰好出现一次的一行换掉后，真实运行配置解析给出的（文件, 登记码, 英文原因）。"""
    path = environment / "config.toml"
    content = path.read_bytes()
    assert content.count(original) == 1
    path.write_bytes(content.replace(original, replacement))
    with pytest.raises(EnvironmentInputError) as caught:
        parse_run_config_file(path)
    return caught.value.path, caught.value.code, caught.value.reason


@pytest.mark.parametrize("written", [b"1", b"2", b"4", b"0", b'"3"', b"3.5", b"true"])
def test_config_schema_version_other_than_three_is_rejected_without_an_old_format_reader(environment, written):
    """config.toml 的结构版本是 3：写旧版本或非整数字面量的文件都被严格字面量拒绝，
    原因写出这个字段；纯校验给出同一条 {path, code, reason}。夹具写的 3 可以加载。"""
    assert parse_run_config_file(environment / "config.toml")[0].schema_version == 3
    reason = "schema_version: Input should be 3"
    assert config_rejected(environment, b"schema_version = 3\n", b"schema_version = " + written + b"\n") == (
        "config.toml", "RUN_CONFIG_INVALID", reason)
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": False, "errors": [{"path": "config.toml", "code": "RUN_CONFIG_INVALID", "reason": reason}]}


def test_config_without_a_schema_version_is_rejected_and_names_the_field(environment):
    """config.toml 的结构版本没有缺省值：整行不写时原因是这个字段必填。"""
    assert config_rejected(environment, b"schema_version = 3\n", b"") == (
        "config.toml", "RUN_CONFIG_INVALID", "schema_version is required")


def test_run_configuration_no_longer_has_a_persona_validation_budget(environment):
    """画像输出校验重试随模型抽取一起删除：config.toml 的模型档里再写这个键就是未知字段，文件被拒绝。"""
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))["models"]["reasoner"]
    assert "persona_validation_max_retries" not in ModelProfileConfig.model_fields
    with pytest.raises(ValidationError):
        ModelProfileConfig.model_validate({**payload, "persona_validation_max_retries": 2})
    anchor = b'requested_model = "test-model"\n'
    assert config_rejected(environment, anchor, anchor + b"persona_validation_max_retries = 3\n") == (
        "config.toml", "RUN_CONFIG_INVALID", "Run config validation failed")


@pytest.mark.parametrize("field", REQUIRED_PERSONA_FIELDS)
def test_each_required_persona_field_is_rejected_when_missing_and_named(environment, field):
    """人设表的必填字段没有缺省值：缺任何一个都以 PERSONA_INVALID 拒绝，原因写出这个字段的点分路径。"""
    assert rejected(environment, lambda document: drop_field(document, field)) == (
        "personas/persona_1.toml", "PERSONA_INVALID", f"{field} is required")


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
        "personas/persona_1.toml", "PERSONA_INVALID", f"{dotted} is not a persona field")


WINDOW_REASON = "must be two finite hours with start < end"


@pytest.mark.parametrize("field", ["wake_window", "leave_home_window", "lunch_window", "dinner_window",
                                   "bedtime_window", "leave_work_window", "arrive_home_window"])
@pytest.mark.parametrize("window", [(12.0,), (12.0, 13.0, 14.0), (13.0, 13.0), (13.0, 12.0),
                                    (float("nan"), 13.0), (12.0, float("inf"))])
def test_every_window_needs_two_finite_hours_with_start_before_end(environment, field, window):
    """每个时段只有一条检查：恰好两个有限数，起点小于终点；不成立时原因写出字段和这一条。"""
    assert rejected(environment, **{field: window}) == (
        "personas/persona_1.toml", "PERSONA_INVALID", f"{field}: {WINDOW_REASON}")


def test_habit_windows_follow_the_same_single_rule(environment):
    """习惯签名的时段用同一条检查，原因带着数组下标。"""
    def mutate(document):
        document["habits"][1]["time_window"] = [23.0, 22.5]

    assert rejected(environment, mutate) == (
        "personas/persona_1.toml", "PERSONA_INVALID", f"habits.1.time_window: {WINDOW_REASON}")


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
    declare(environment, **declared)
    loaded = load_environment(environment, metadata_loader=metadata).personas[0]
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
    declare(environment, **declared)
    loaded = load_environment(environment, metadata_loader=metadata).personas[0]
    assert (loaded.chronotype_midsleep, loaded.sleep_need_hours) == (4.1, 7.6)
    assert {name: tuple(getattr(loaded, name)) for name in declared if name.endswith("_window")} == {
        name: value for name, value in declared.items() if name.endswith("_window")}


@pytest.mark.parametrize("mutate,reason", [
    (lambda doc: doc["identity"].update(age="32"), "identity.age: Input should be a valid integer"),
    (lambda doc: doc["identity"].update(age=32.0), "identity.age: Input should be a valid integer"),
    (lambda doc: doc["identity"].update(occupation="长" * 21),
     "identity.occupation: String should have at most 20 characters"),
    (lambda doc: doc["identity"].update(living_arrangement="住" * 31),
     "identity.living_arrangement: String should have at most 30 characters"),
    (lambda doc: doc["situation"].update(busy_this_week="忙" * 61),
     "situation.busy_this_week: String should have at most 60 characters"),
    (lambda doc: doc["situation"].update(cares_about="想" * 61),
     "situation.cares_about: String should have at most 60 characters"),
    (lambda doc: doc["relationships"][1].update(name_or_appellation="名" * 13),
     "relationships.1.name_or_appellation: String should have at most 12 characters"),
    (lambda doc: doc["relationships"][0].update(role="亲" * 13),
     "relationships.0.role: String should have at most 12 characters"),
    (lambda doc: doc["relationships"][0].update(intimacy=-1),
     "relationships.0.intimacy: Input should be greater than or equal to 0"),
    (lambda doc: doc["relationships"][0].update(intimacy=31),
     "relationships.0.intimacy: Input should be less than or equal to 30"),
    (lambda doc: doc["relationships"][1].update(channels=["微信", " "]),
     "relationships.1.channels.1: String should match pattern '\\S'"),
    (lambda doc: doc["relationships"][1].update(channels=[""]),
     "relationships.1.channels.0: String should match pattern '\\S'"),
    (lambda doc: doc["relationships"][1].update(channels=["微信", 3]),
     "relationships.1.channels.1: Input should be a valid string"),
    (lambda doc: doc["relationships"][0].update(channels=[]),
     "relationships.0.channels: Tuple should have at least 1 item after validation, not 0"),
    (lambda doc: doc["relationships"][0].update(channels=["电话", "微信", "见面", "电话"]),
     "relationships.0.channels: Tuple should have at most 3 items after validation, not 4"),
    (lambda doc: doc["tastes"].update(media=["长视频", " "]),
     "tastes.media.1: String should match pattern '\\S'"),
    (lambda doc: doc["tastes"].update(media=[f"媒介{index}" for index in range(5)]),
     "tastes.media: Tuple should have at most 4 items after validation, not 5"),
    (lambda doc: doc["tastes"].update(content_genres=[f"题材{index}" for index in range(7)]),
     "tastes.content_genres: Tuple should have at most 6 items after validation, not 7"),
    (lambda doc: doc["tastes"].update(food_preferences=[f"菜{index}" for index in range(7)]),
     "tastes.food_preferences: Tuple should have at most 6 items after validation, not 7"),
    (lambda doc: doc["habits"][1].update(trigger="时" * 31),
     "habits.1.trigger: String should have at most 30 characters"),
    (lambda doc: doc["habits"][0].update(response="做" * 41),
     "habits.0.response: String should have at most 40 characters"),
    (lambda doc: doc.update(sleep_need_hours=6.5), "sleep_need_hours: Input should be greater than or equal to 7"),
    (lambda doc: doc.update(social_need_growth_per_hour=0.2),
     "social_need_growth_per_hour: Input should be less than or equal to 0.1"),
])
def test_table_format_and_formula_ranges_are_rejected_with_the_field_named(environment, mutate, reason):
    """保留的检查只有表的格式与计算要用的范围：类型、数值范围、条数与字数上限；联系渠道只要求是一到三项非空白文字。
    原因先写出错的字段。"""
    assert rejected(environment, mutate) == ("personas/persona_1.toml", "PERSONA_INVALID", reason)


@pytest.mark.parametrize("age", [15, 16, 100, 101, 0, 130])
def test_age_takes_any_integer_the_dataset_writes(environment, age):
    """年龄不进任何计算，只要求是整数：16 以下、100 以上的写法都通过完整环境加载和纯校验，
    人设表、卡片和卡片文本里都是写的那个数。"""
    edit_persona(environment, lambda document: document["identity"].update(age=age))
    assert validate_environment(environment, metadata_loader=metadata)["valid"] is True
    loaded = load_environment(environment, metadata_loader=metadata).personas[0]
    assert loaded.identity.age == age
    card = persona_card(loaded)
    assert card.identity.age == age and f"产品经理，{age}岁，" in render_card(card)


@pytest.mark.parametrize("first,second", [
    (["飞书", "邮件", "飞书"], ["写信"]),
    (["短信"], ["Slack", "Zoom 视频", "当面聊"]),
    (["电话", "微信", "见面"], ["电话"]),
])
def test_relationship_channels_take_any_words_the_dataset_writes(environment, first, second):
    """联系渠道不设闭集：数据集写的任何非空白文字都通过完整环境加载和纯校验，一到三项，重复的词也不去重；
    人设表、卡片和卡片文本里都是写的那几个词。"""
    def mutate(document):
        document["relationships"][0]["channels"] = first
        document["relationships"][1]["channels"] = second

    edit_persona(environment, mutate)
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": True, "environment_id": "artificial-test", "environment_version": "4"}
    loaded = load_environment(environment, metadata_loader=metadata).personas[0]
    assert [list(item.channels) for item in loaded.relationships] == [first, second]
    card = persona_card(loaded)
    assert [list(item.channels) for item in card.relationships] == [first, second]
    text = render_card(card)
    assert f"母亲（家人，很亲近，{'、'.join(first)}联系）" in text
    assert f"老周（老友，较亲近，{'、'.join(second)}联系）" in text


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
        f"{field}: Tuple should have at most {limit} items after validation, not {limit + 1}")
    assert getattr(parse_persona_file(declare(environment, **{field: []}))[0], field) == ()


@pytest.mark.parametrize("mutate", [
    lambda doc: doc["identity"].update(occupation="长" * 20, living_arrangement="住" * 30),
    lambda doc: doc["situation"].update(busy_this_week="忙" * 60, cares_about="想" * 60),
    lambda doc: doc["relationships"][0].update(name_or_appellation="名" * 12, role="亲" * 12, intimacy=0),
    lambda doc: doc["habits"][0].update(trigger="时" * 30, response="做" * 40),
    lambda doc: doc["tastes"].update(food_preferences=[f"菜{index}" for index in range(6)]),
    lambda doc: doc.update(coping="压" * 60),
])
def test_values_exactly_at_a_length_or_count_limit_load(environment, mutate):
    """字数、条数和亲密度正好写到上限或下限的人设表可以加载：上限本身不被拒绝。"""
    assert parse_persona_file(edit_persona(environment, mutate))[0].schema_version == 3


def _crowded(document, last_response: int) -> None:
    """每一栏都在各自上限之内的人设表：五条习惯、四个关系都写满，最后一条习惯的反应写 last_response 个字。"""
    document["habits"] = [{"trigger": "情" * 30, "response": "做" * 40, "time_window": [19.0, 20.0]}
                          for _ in range(5)]
    document["habits"][-1]["response"] = "做" * last_response
    document["relationships"] = [{"name_or_appellation": name * 12, "role": "友" * 12, "intimacy": 25,
                                  "channels": ["电话", "微信", "见面"]} for name in "甲乙丙丁"]


def test_card_text_over_600_hanzi_is_rejected_at_load_although_every_field_is_within_its_limit(environment):
    """条数与字数上限各管一个字段，合起来仍可能超过卡片文本的 600 汉字：正好 600 汉字的人设表可以加载，601 汉字的在加载
    时以 PERSONA_INVALID 拒绝并写出汉字数，纯校验给出同一条，不必等到新建运行。"""
    loaded = parse_persona_file(edit_persona(environment, lambda document: _crowded(document, 25)))[0]
    assert count_hanzi(render_card(persona_card(loaded))) == 600
    reason = "persona card render exceeds budget: 601 hanzi > 600 (input-side problem, not truncated)"
    assert rejected(environment, lambda document: _crowded(document, 26)) == (
        "personas/persona_1.toml", "PERSONA_INVALID", reason)
    assert validate_environment(environment, metadata_loader=metadata) == {
        "valid": False, "errors": [{"path": "personas/persona_1.toml", "code": "PERSONA_INVALID", "reason": reason}]}


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
    path = environment / "personas" / "persona_1.toml"
    text = path.read_text(encoding="utf-8")
    assert original in text
    path.write_bytes(text.replace(original, replacement, 1).encode("utf-8"))
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(path)
    assert (caught.value.code, caught.value.reason) == ("PERSONA_INVALID", reason)


def _add_second_persona(environment, agent_id: int = 2, name: str = "陈默") -> Path:
    src = (environment / "personas" / "persona_1.toml").read_text(encoding="utf-8")
    dst_text = (
        src.replace("agent_id = 1\n", f"agent_id = {agent_id}\n", 1)
        .replace('name = "林舟"\n', f'name = "{name}"\n', 1)
    )
    target = environment / "personas" / "persona_2.toml"
    target.write_bytes(dst_text.encode("utf-8"))
    return target


def test_multi_persona_dir_loads_sorted_and_checks_uniqueness(environment):
    """多个人设文件按 agent_id 升序返回；agent_id 或去空白姓名重复均在对应文件上拒绝。"""
    _add_second_persona(environment, agent_id=2, name="陈默")
    loaded = load_environment(environment, metadata_loader=metadata)
    assert [p.agent_id for p in loaded.personas] == [1, 2]
    assert loaded.persona_paths[2] == "personas/persona_2.toml"
    assert set(loaded.home_location_ids) == {1, 2}
    assert_deeply_immutable(loaded)

    _add_second_persona(environment, agent_id=1, name="陈默")
    with pytest.raises(EnvironmentInputError) as dup_id:
        parse_personas_dir(environment / "personas")
    assert (dup_id.value.path, dup_id.value.code, dup_id.value.reason) == (
        "personas/persona_2.toml", "PERSONA_INVALID", "duplicate agent_id: 1")

    _add_second_persona(environment, agent_id=2, name=" 林舟 ")
    with pytest.raises(EnvironmentInputError) as dup_name:
        parse_personas_dir(environment / "personas")
    assert (dup_name.value.path, dup_name.value.code, dup_name.value.reason) == (
        "personas/persona_2.toml", "PERSONA_INVALID", "duplicate persona name: 林舟")


def test_empty_or_missing_personas_dir_is_rejected(environment):
    """personas 目录缺失或没有任何合法 .toml 文件时以 PERSONA_INVALID 拒绝。"""
    (environment / "personas" / "persona_1.toml").unlink()
    with pytest.raises(EnvironmentInputError) as empty_err:
        parse_personas_dir(environment / "personas")
    assert empty_err.value.code == "PERSONA_INVALID"
    (environment / "personas").rmdir()
    with pytest.raises(EnvironmentInputError) as missing_err:
        parse_personas_dir(environment / "personas")
    assert missing_err.value.code == "PERSONA_INVALID"


def test_agent_overrides_and_routing_references_are_validated(environment):
    """路由引用未知档位被严格拦截，未匹配模型标识的元数据返回也会在加载阶段拒绝。"""
    assert config_rejected(environment, b'search = "fast_search"\n', b'search = "missing_profile"\n') == (
        "config.toml", "RUN_CONFIG_INVALID", "Run config validation failed")
    with pytest.raises(EnvironmentInputError) as bad_meta:
        load_environment(
            environment,
            metadata_loader=lambda cfg: ModelMetadata(
                requested_model="wrong-model", metadata_url=cfg.metadata_url,
                context_window=524288, max_output_tokens=32768))
    assert (bad_meta.value.path, bad_meta.value.code) == ("config.toml", "RUN_CONFIG_INVALID")


def test_config_context_window_and_agent_overrides_errors(environment, monkeypatch):
    """profile context_window 过小与未知 agent_overrides 分别在解析与加载阶段拒绝；未引用档位不请求元数据。"""
    cfg_path = environment / "config.toml"
    base = cfg_path.read_text(encoding="utf-8")
    low_ctx = base.replace("context_window = 262144\n", "context_window = 131072\n", 1)
    cfg_path.write_bytes(low_ctx.encode("utf-8"))
    with pytest.raises(EnvironmentInputError) as caught_ctx:
        parse_run_config_file(cfg_path)
    assert (caught_ctx.value.code, caught_ctx.value.reason) == (
        "RUN_CONFIG_INVALID", "context_window must be at least 262144 tokens")

    extra = base + (
        '\n[models.unused_backup]\nprovider = "openai_compatible"\n'
        'base_url = "https://unused.example.invalid/v1"\nrequested_model = "unused"\n'
        'api_key_env = "WORLDMOCK_UNUSED_KEY"\nmetadata_url = "https://unused.example.invalid/v1/models"\n'
        'context_window = 262144\nthinking = false\nmax_output_tokens = 2048\n'
        '\n[model_routing.agent_overrides."99"]\naction = "reasoner"\n'
    )
    cfg_path.write_bytes(extra.encode("utf-8"))
    assert validate_environment(environment, metadata_loader=metadata)["valid"] is False
    with pytest.raises(EnvironmentInputError) as caught_ovr:
        load_environment(environment, metadata_loader=metadata)
    assert caught_ovr.value.reason == "agent_overrides references unknown agent_id: 99"

    valid_extra = extra.replace('[model_routing.agent_overrides."99"]', '[model_routing.agent_overrides."1"]')
    cfg_path.write_bytes(valid_extra.encode("utf-8"))
    cfg, _ = parse_run_config_file(cfg_path)
    requested: list[str] = []

    def tracking_loader(profile):
        requested.append(profile.requested_model)
        return metadata(profile)

    monkeypatch.setenv("WORLDMOCK_TEST_API_KEY", "secret-token")
    monkeypatch.delenv("WORLDMOCK_UNUSED_KEY", raising=False)
    assert set(verify_active_credentials(cfg)) == {"reasoner", "fast_search"}
    loaded_meta = load_active_metadata(cfg, metadata_loader=tracking_loader)
    assert set(loaded_meta) == {"reasoner", "fast_search"}
    assert set(requested) == {"test-model", "test-search-model"}


def test_model_routing_and_profile_map_validation(environment):
    """人物覆盖不得全空、覆盖键须为规范正整数字符串，且 models 字典不得为空或含空白档名。"""
    with pytest.raises(ValidationError):
        ModelStageOverride()
    with pytest.raises(ValidationError):
        ModelRoutingConfig(action="r", search="r", plan_day="r", digest="r",
                           agent_overrides={"01": ModelStageOverride(action="r")})
    raw = tomllib.loads((environment / "config.toml").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        RunConfig.model_validate({**raw, "models": {}})
    first_profile = raw["models"][next(iter(raw["models"]))]
    with pytest.raises(ValidationError):
        RunConfig.model_validate({**raw, "models": {" ": first_profile}})
