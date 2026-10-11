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


def republish_rows(root, name, change):
    """@brief 改写夹具副本的一份目录文件（逐行 JSON、身份升序），再按真实字节刷新清单身份。"""
    path = root / "catalog" / name
    rows = change([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()])
    path.write_bytes("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                             for row in rows).encode("utf-8"))
    refresh_manifest_identity(path.parent)


@pytest.mark.parametrize("marked", ["absent", "false"])
def test_persona_home_that_is_not_sleepable_is_a_registration_failure(published_environment, marked):
    """人物自己的家没有标 sleepable（缺这个键或写成 false）：目录本身能加载，validate 以非零退出并只报这一条。"""
    def unmark(rows):
        home = next(row for row in rows if row["location_id"] == "home")
        assert home["name"] == "家" and home.pop("sleepable") is True
        if marked == "false":
            home["sleepable"] = False
        return rows

    republish_rows(published_environment, "location.jsonl", unmark)
    assert report(published_environment) == [{"path": "persona.toml", "code": "HOME_NOT_SLEEPABLE",
                                             "reason": "The persona home must be marked sleepable."}]


@pytest.mark.parametrize("title", ["测试休息", "测试 休息", " 测试休息\t"])
def test_two_location_contents_of_one_kind_sharing_a_unit_name_are_a_registration_failure(
        published_environment, title):
    """同一地点类型（home）下两条独立内容的单元名相同（只差空白也算相同）：validate 以非零退出并只报这一条。"""
    def duplicate(rows):
        rest = next(row for row in rows if row["content_id"] == "c-rest")
        assert rest["title"] == "测试休息" and rest["location_kinds"] == ["home"] and "series" not in rest
        return sorted([*rows, {**rest, "content_id": "c-rest-2", "title": title}], key=lambda row: row["content_id"])

    republish_rows(published_environment, "content-catalog.jsonl", duplicate)
    assert report(published_environment) == [{
        "path": "catalog", "code": "ENVIRONMENT_ID_DUPLICATE",
        "reason": "An identifier or display name is declared twice in one catalog file."}]


def test_republished_fixture_copy_with_a_distinct_unit_name_stays_valid(published_environment):
    """对照：同一条新增内容换一个不重名的单元名后，照同样的改写与刷新仍然通过 validate。"""
    def distinct(rows):
        rest = next(row for row in rows if row["content_id"] == "c-rest")
        return sorted([*rows, {**rest, "content_id": "c-rest-2", "title": "测试午休"}],
                      key=lambda row: row["content_id"])

    republish_rows(published_environment, "content-catalog.jsonl", distinct)
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_authoritative_metadata_failure_is_a_configuration_error(published_environment):
    path = published_environment / "config.toml"
    path.write_bytes(path.read_bytes().replace(b'requested_model = "test-model"', b'requested_model = "missing"'))
    errors = report(published_environment)
    assert any(row["path"] == "config.toml" and row["code"] == "RUN_CONFIG_INVALID"
               and "model context metadata is unavailable" in row["reason"] for row in errors)
