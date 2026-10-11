"""完整输入文件中的人设表、日期与运行窗口反例必须在纯准入阶段拒绝。"""

from datetime import datetime
from pathlib import Path

import pytest
import tomlkit

from tests.frozen_inputs import fixture_metadata
from tests.life.environment_inputs import copy_environment
from worldmock.environment import load_environment, parse_persona_file, parse_run_config_file
from worldmock.errors import EnvironmentInputError


def write_input(root, name, values):
    document = tomlkit.parse((root / name).read_text(encoding="utf-8"))
    for key, value in values.items():
        document[key] = value
    (root / name).write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")


@pytest.mark.parametrize("shift", [
    {"starts": 600, "ends": 600, "breaks": []},
    {"starts": 600, "ends": 900, "breaks": [[590, 700]]},
    {"starts": 600, "ends": 900, "breaks": [[700]]},
    {"starts": 600, "ends": 900, "breaks": [[700, 800], [750, 850]]},
])
def test_invalid_work_shift_cannot_be_loaded(tmp_path, shift):
    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", {"work_schedule": {"workplace": "公司", "weekly": {"0": shift}}})
    with pytest.raises(EnvironmentInputError, match=r"^work_schedule\.weekly\.0(\.breaks\.0)?: ") as caught:
        parse_persona_file(root / "persona.toml")
    assert (caught.value.path, caught.value.code) == ("persona.toml", "PERSONA_INVALID")


@pytest.mark.parametrize("schedule", [
    {"workplace": "公司", "weekly": {"mon": {"starts": 540, "ends": 1080, "breaks": []}}},
    {"workplace": "公司", "weekly": {}, "exceptions": {"20260105": []}},
    {"workplace": "公司", "weekly": {}, "exceptions": {"invalid": []}},
    {"workplace": "公司", "weekly": {},
     "exceptions": {"2026-01-05": [{"starts": 540, "ends": 1080, "breaks": []}]}},
])
def test_work_schedule_has_canonical_weekdays_dates_and_one_daily_shift(tmp_path, schedule):
    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", {"work_schedule": schedule})
    with pytest.raises(EnvironmentInputError, match=r"^work_schedule(\.exceptions\.2026-01-05\.\S+)?: ") as caught:
        parse_persona_file(root / "persona.toml")
    assert (caught.value.path, caught.value.code) == ("persona.toml", "PERSONA_INVALID")


@pytest.mark.parametrize("values,reason", [
    ({"lunch_window": [12.0]}, "lunch_window: must be two finite hours with start < end"),
    ({"dinner_window": [12.0, 12.0]}, "dinner_window: must be two finite hours with start < end"),
    ({"bedtime_window": [24.0, 23.0]}, "bedtime_window: must be two finite hours with start < end"),
    ({"lunch_window": [float("nan"), 13.0]}, "lunch_window: must be two finite hours with start < end"),
    ({"wake_window": [7.0, float("inf")]}, "wake_window: must be two finite hours with start < end"),
    ({"leave_home_window": "08:00-08:30"}, "leave_home_window: Input should be a valid list"),
])
def test_personal_windows_need_two_finite_hours_in_order(tmp_path, values, reason):
    """每个时段只有形状这一条检查，不成立时在纯准入阶段被拒绝，英文原因随 PERSONA_INVALID 返回并写出字段。"""
    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", values)
    with pytest.raises(EnvironmentInputError) as caught:
        parse_persona_file(root / "persona.toml")
    assert (caught.value.path, caught.value.code, caught.value.reason) == ("persona.toml", "PERSONA_INVALID", reason)


@pytest.mark.parametrize("values", [
    {"leave_work_window": [19.0, 20.0], "arrive_home_window": [18.0, 19.0]},
    {"dinner_window": [19.0, 20.0], "bedtime_window": [19.5, 25.0]},
    {"lunch_window": [12.0, 13.5], "dinner_window": [13.0, 14.0]},
    {"lunch_window": [1.0, 15.0]},
    {"bedtime_window": [23.0, 31.0]},
    {"bedtime_window": [5.0, 7.0]},
    {"leave_work_window": [25.0, 25.5]},
])
def test_windows_that_contradict_each_other_or_leave_the_day_load_as_written(tmp_path, values):
    """时段之间没有先后、重叠、范围或跨度检查：这些写法通过完整环境加载，引擎的饭点窗口也照写的值装配。"""
    from worldmock.life.world_ops import meal_windows_by_agent

    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", values)
    loaded = load_environment(root, metadata_loader=fixture_metadata).persona
    assert {name: list(getattr(loaded, name)) for name in values} == values
    windows = meal_windows_by_agent(loaded)[loaded.agent_id]
    assert (windows.lunch, windows.dinner) == (tuple(loaded.lunch_window), tuple(loaded.dinner_window))


@pytest.mark.parametrize("field", ["occupation", "routine_preference", "consumption_preference", "risk_preference"])
def test_deleted_persona_templates_are_rejected_as_input(tmp_path, field):
    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", {field: "obsolete"})
    with pytest.raises(EnvironmentInputError, match="legacy template fields are deleted, merge them into the card"):
        parse_persona_file(root / "persona.toml")


@pytest.mark.parametrize("values,reason", [
    ({"start_time": datetime(2026, 1, 5, 8)}, "start_time must be timezone-aware"),
    ({"stop_time": datetime(2026, 1, 6, 8)}, "stop_time must be timezone-aware"),
    ({"start_time": datetime.fromisoformat("2026-01-05T08:01:00+08:00")},
     "start_time and stop_time must fall on whole hours"),
    ({"stop_time": datetime.fromisoformat("2026-01-05T08:00:00+08:00")},
     "run time interval or timezone is invalid"),
    ({"timezone": "Invalid/WorldMock"}, "run time interval or timezone is invalid"),
    ({"start_time": datetime.fromisoformat("2026-01-05T08:00:00+00:00")},
     "run time interval or timezone is invalid"),
])
def test_invalid_run_windows_fail_before_metadata_or_assets(tmp_path, values, reason):
    root = copy_environment(tmp_path / "input")
    write_input(root, "config.toml", values)
    calls = []
    with pytest.raises(EnvironmentInputError, match=reason):
        load_environment(root, metadata_loader=lambda model: calls.append(model))
    assert calls == []
    assert set(item.name for item in root.iterdir()) == {"catalog", "config.toml", "persona.toml"}


@pytest.mark.parametrize("field,value", [
    ("spring_festival_dates", ["2026-01-05", "2026-01-05"]),
    ("makeup_work_dates", ["20260105"]), ("overtime_opportunity_dates", ["invalid"]),
    ("sick_leave_date", "invalid"), ("personal_leave_date", "2026-02-30"),
    ("social_dinner_date", "20260105"),
])
def test_run_calendar_rejects_duplicate_or_noncanonical_dates(tmp_path, field, value):
    root = copy_environment(tmp_path / "input")
    document = tomlkit.parse((root / "config.toml").read_text(encoding="utf-8"))
    document["calendar"][field] = value
    (root / "config.toml").write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(EnvironmentInputError, match="Run config validation failed"):
        parse_run_config_file(root / "config.toml")


@pytest.mark.parametrize("payload", [b"\xff", b"not TOML [", b"\xef\xbb\xbfkey=1\n", b"key=1\r\n"])
@pytest.mark.parametrize("name,parser", [("persona.toml", parse_persona_file), ("config.toml", parse_run_config_file)])
def test_input_files_reject_noncanonical_bytes(tmp_path, payload, name, parser):
    target = tmp_path / name
    target.write_bytes(payload)
    with pytest.raises(EnvironmentInputError):
        parser(target)


@pytest.mark.parametrize("field,name", [("home_location", "公司"), ("initial_location", "不存在的地方")])
def test_persona_references_resolve_actual_registered_locations(tmp_path, field, name):
    from worldmock.catalog.errors import RegistrationError

    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", {field: name})
    with pytest.raises(RegistrationError):
        load_environment(root, metadata_loader=fixture_metadata)


def test_persona_story_is_required_text_without_a_minimum_length(tmp_path):
    """background_story 必填且非空，但一句话就够：零点计划原样读它，引擎不从中抽取任何字段。"""
    root = copy_environment(tmp_path / "input")
    write_input(root, "persona.toml", {"background_story": "Too short"})
    assert parse_persona_file(root / "persona.toml")[0].background_story == "Too short"
    write_input(root, "persona.toml", {"background_story": ""})
    with pytest.raises(EnvironmentInputError, match="^background_story: String should have at least 1 character$"):
        parse_persona_file(root / "persona.toml")


def test_model_metadata_rejects_output_budget_larger_than_actual_context_window():
    from pydantic import ValidationError
    from worldmock.input_models import ModelMetadata

    with pytest.raises(ValidationError, match="model output limit exceeds the context window"):
        ModelMetadata(requested_model="test-model", metadata_url="https://example.invalid/models",
                      context_window=8192, max_output_tokens=8193)
