"""@brief 真实 CLI 验证纯输入、原文字节身份、模型服务与地点引用。"""

import json

import pytest

from tests.system.cli.test_cli import _command, published_environment
from worldmock.catalog.identity import refresh_manifest_identity


def report(root):
    result = _command("validate", "--environment", root)
    document = json.loads(result.stdout)
    assert result.returncode == 1, result.stdout + result.stderr
    assert document["valid"] is False
    assert all(set(error) == {"path", "code", "reason"} for error in document["errors"])
    return document["errors"]


def test_independent_invalid_sources_are_all_reported(published_environment):
    root = published_environment
    (root / "persona.toml").write_bytes(b"schema_version = 1\n")
    (root / "config.toml").write_bytes(b"schema_version = 1\n")
    (root / "catalog/app-catalog.jsonl").write_bytes(b"[]\n")
    errors = report(root)
    assert {row["path"] for row in errors} == {"catalog", "persona.toml", "config.toml"}
    assert {row["code"] for row in errors} >= {"PERSONA_INVALID", "RUN_CONFIG_INVALID"}


@pytest.mark.parametrize("relative", ["map.json", "apps.json", "affordances.toml", "rules/motivation.toml"])
def test_obsolete_catalog_files_are_rejected(published_environment, relative):
    path = published_environment / "catalog" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{}\n")
    assert any(row["path"] == "catalog" for row in report(published_environment))


@pytest.mark.parametrize("relative,payload,code", [
    ("persona.toml", b"\xef\xbb\xbfschema_version = 1\n", "PERSONA_INVALID"),
    ("persona.toml", b"schema_version = 1\r\n", "PERSONA_INVALID"),
    ("config.toml", b"\xff\n", "RUN_CONFIG_INVALID"),
    ("config.toml", b"schema_version = 1", "RUN_CONFIG_INVALID"),
])
def test_toml_raw_format_errors_are_public(published_environment, relative, payload, code):
    (published_environment / relative).write_bytes(payload)
    assert any(row["path"] == relative and row["code"] == code for row in report(published_environment))


def test_catalog_bytes_cannot_change_without_republication(published_environment):
    path = published_environment / "catalog/merchant-catalog.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    assert any(row["path"] == "catalog" for row in report(published_environment))


def test_even_republished_jsonl_rejects_blank_lines(published_environment):
    path = published_environment / "catalog/content-catalog.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    refresh_manifest_identity(path.parent)
    assert any(row["path"] == "catalog" for row in report(published_environment))


@pytest.mark.parametrize("missing", ["persona.toml", "config.toml", "catalog/callbacks.jsonl"])
def test_each_required_source_is_mandatory(published_environment, missing):
    (published_environment / missing).unlink()
    assert report(published_environment)


@pytest.mark.parametrize("field,original", [("home_location", b'home_location = "\xe5\xae\xb6"'),
                                           ("initial_location", b'initial_location = "\xe5\xae\xb6"')])
def test_persona_location_reference_uses_exact_registered_display_names(published_environment, field, original):
    path = published_environment / "persona.toml"
    assert original in path.read_bytes()
    path.write_bytes(path.read_bytes().replace(original, f'{field} = "unknown"'.encode()))
    expected = "HOME_LOCATION_REQUIRED" if field == "home_location" else "ENVIRONMENT_REF_UNKNOWN"
    assert any(row["path"] == "persona.toml" and row["code"] == expected
               for row in report(published_environment))


@pytest.mark.parametrize("declared,reason", [
    (b"lunch_window = [18.0, 19.0]\ndinner_window = [12.0, 13.0]\n",
     "lunch_window must end before dinner_window starts"),
    (b"bedtime_window = [22.0, 23.0]\nleave_work_window = [23.0, 23.5]\n",
     "leave_work_window must not start after bedtime_window"),
    (b"bedtime_window = [9.0, 10.0]\nlunch_window = [12.0, 13.0]\n",
     "bedtime_window must not start before lunch_window"),
])
def test_contradictory_persona_rhythm_is_reported_with_its_verbatim_reason(published_environment, declared, reason):
    """@brief 自相矛盾的作息声明让 validate 非零退出，输出里是 persona.toml、PERSONA_INVALID 和逐字的英文原因。"""
    path = published_environment / "persona.toml"
    assert path.read_bytes().count(b"\n[body]\n") == 1
    path.write_bytes(path.read_bytes().replace(b"\n[body]\n", b"\n" + declared + b"\n[body]\n"))
    assert report(published_environment) == [{"path": "persona.toml", "code": "PERSONA_INVALID", "reason": reason}]


def test_after_midnight_bedtime_with_dinner_passes_validation(published_environment):
    """@brief 就寝起点在午夜之后的人物可以同时声明晚饭和就寝，不在格点上的声明值照常通过。"""
    path = published_environment / "persona.toml"
    declared = b"dinner_window = [19.0, 20.0]\nbedtime_window = [1.0, 1.5]\narrive_home_window = [18.25, 18.75]\n"
    path.write_bytes(path.read_bytes().replace(b"\n[body]\n", b"\n" + declared + b"\n[body]\n"))
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_authoritative_metadata_failure_is_a_configuration_error(published_environment):
    path = published_environment / "config.toml"
    path.write_bytes(path.read_bytes().replace(b'requested_model = "test-model"', b'requested_model = "missing"'))
    errors = report(published_environment)
    assert any(row["path"] == "config.toml" and row["code"] == "RUN_CONFIG_INVALID"
               and "model context metadata is unavailable" in row["reason"] for row in errors)
