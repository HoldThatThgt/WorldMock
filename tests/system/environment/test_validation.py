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


@pytest.mark.parametrize("missing", ["persona.toml", "config.toml", "catalog/location.jsonl", "catalog/manifest.json"])
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
    """人物自己的家没有标 sleepable（缺这个键或写成 false）：目录本身能加载，validate 以非零退出并只报这一条，
    原因后接详情 persona.home_location。"""
    def unmark(rows):
        home = next(row for row in rows if row["location_id"] == "home")
        assert home["name"] == "家" and home.pop("sleepable") is True
        if marked == "false":
            home["sleepable"] = False
        return rows

    republish_rows(published_environment, "location.jsonl", unmark)
    assert report(published_environment) == [{
        "path": "persona.toml", "code": "HOME_NOT_SLEEPABLE",
        "reason": "The persona home must be marked sleepable. persona.home_location"}]


@pytest.mark.parametrize("title", ["测试休息", "测试 休息", " 测试休息\t"])
def test_two_location_contents_of_one_kind_sharing_a_unit_name_are_a_registration_failure(
        published_environment, title):
    """同一地点类型（home）下两条独立内容的单元名相同（只差空白也算相同）：validate 以非零退出并只报这一条，
    原因后接详情：两行的行号、地点类型和先登记的单元名。"""
    def duplicate(rows):
        rest = next(row for row in rows if row["content_id"] == "c-rest")
        assert rest["title"] == "测试休息" and rest["location_kinds"] == ["home"] and "series" not in rest
        return sorted([*rows, {**rest, "content_id": "c-rest-2", "title": title}], key=lambda row: row["content_id"])

    republish_rows(published_environment, "content-catalog.jsonl", duplicate)
    assert report(published_environment) == [{
        "path": "catalog", "code": "ENVIRONMENT_ID_DUPLICATE",
        "reason": "An identifier or display name is declared twice in one catalog file. content-catalog.jsonl:10 "
                  "and content-catalog.jsonl:11 give location kind home the same activity name 测试休息."}]


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


CALLBACK_ROW = b'{"cap":3,"initial":1,"reset_on_goods":[],"state":"stock"}\n'
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'


def accepted(root):
    result = _command("validate", "--environment", root)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def catalog_error(root, code):
    """@brief 登记失败的 reason 是固定英文原因后接详情，返回这条错误供逐字核对。"""
    rows = [row for row in report(root) if row["code"] == code]
    assert rows and all(row["path"] == "catalog" for row in rows), rows
    return rows[0]


@pytest.mark.parametrize("files", [{}, {"callbacks.jsonl": b"", "map.jsonl": b""},
                                   {"callbacks.jsonl": CALLBACK_ROW, "map.jsonl": MAP_ROW}],
                         ids=["four-required-files", "empty-optional-files", "all-catalog-files"])
def test_package_with_or_without_optional_catalog_files_is_valid(published_environment, files):
    """@brief 没有 callbacks.jsonl 的夹具通过；两个可选文件放空或放内容并刷新清单后同样通过。"""
    catalog = published_environment / "catalog"
    assert sorted(path.name for path in catalog.iterdir()) == [
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "manifest.json", "merchant-catalog.jsonl"]
    for name, content in files.items():
        (catalog / name).write_bytes(content)
    refresh_manifest_identity(catalog)
    assert accepted(published_environment) == {"valid": True, "environment_id": "artificial-test",
                                              "environment_version": "3"}


@pytest.mark.parametrize("change,code,reason", [
    ({"rules_version": 3}, "RULES_VERSION_MISMATCH", "The environment was published for a different rules version."),
    ({"rules_version": 3, "format_version": 3}, "RULES_VERSION_MISMATCH",
     "The environment was published for a different rules version."),
])
def test_package_published_for_the_previous_rules_version_is_refused(published_environment, change, code, reason):
    """@brief 规则版本是 3 的夹具副本：输出 {path, code, reason}，非零退出，原因没有详情时只有固定原因。"""
    path = published_environment / "catalog/manifest.json"
    path.write_bytes(json.dumps({**json.loads(path.read_bytes()), **change}, separators=(",", ":")).encode() + b"\n")
    assert report(published_environment) == [{"path": "catalog", "code": code, "reason": reason}]


def test_previous_manifest_format_is_refused_after_the_rules_version_matches(published_environment):
    path = published_environment / "catalog/manifest.json"
    manifest = {**json.loads(path.read_bytes()), "format_version": 3}
    path.write_bytes(json.dumps(manifest, separators=(",", ":")).encode() + b"\n")
    error = catalog_error(published_environment, "ENVIRONMENT_KIND_INVALID")
    assert error["reason"].startswith("A declared kind is outside the fixed set. ") and "format_version" in error["reason"]


@pytest.mark.parametrize("name", ["travel-method.jsonl", "travel_method.jsonl", "callback.jsonl", "map.json"])
def test_catalog_entry_outside_the_closed_set_reports_its_actual_path(published_environment, name):
    """@brief 闭集外的文件名报 ENVIRONMENT_STALE_FILE，reason 带着实际路径。"""
    extra = published_environment / "catalog" / name
    extra.write_bytes(b"")
    error = catalog_error(published_environment, "ENVIRONMENT_STALE_FILE")
    assert error["reason"] == f"The environment contains an entry outside its declared package. {extra}"


@pytest.mark.parametrize("name", ["callbacks.jsonl", "map.jsonl"])
def test_optional_file_placed_without_refreshing_the_manifest_reports_its_name(published_environment, name):
    (published_environment / "catalog" / name).write_bytes(b"")
    error = catalog_error(published_environment, "ENVIRONMENT_FILE_UNLISTED")
    assert error["reason"] == f"The manifest does not list a catalog file that is required or present. {name}"
    refresh_manifest_identity(published_environment / "catalog")
    assert accepted(published_environment)["valid"] is True


def test_contradicting_declared_distances_report_the_shorter_route(published_environment):
    rows = (MAP_ROW + b'{"a":"home","b":"park","length_m":3000}\n' + b'{"a":"office","b":"park","length_m":4000}\n')
    (published_environment / "catalog/map.jsonl").write_bytes(rows)
    refresh_manifest_identity(published_environment / "catalog")
    assert report(published_environment) == [{
        "path": "catalog", "code": "MAP_DISTANCE_CONTRADICTED",
        "reason": "A declared distance is longer than a route through other declared distances. "
                  "map.jsonl:1 home-office declares 12600 m but home -> park -> office is 7000 m."}]


def test_declared_distance_row_error_reports_its_line(published_environment):
    (published_environment / "catalog/map.jsonl").write_bytes(MAP_ROW + b'{"a":"home","b":"school","length_m":5}\n')
    refresh_manifest_identity(published_environment / "catalog")
    assert report(published_environment) == [{
        "path": "catalog", "code": "ENVIRONMENT_REF_UNKNOWN",
        "reason": "A binding refers to an object that is not registered. map.jsonl:2 Unknown location_id: school."}]


def test_declared_distance_beyond_one_day_reports_the_farthest_pair(published_environment):
    """@brief 超限只在带种子生成地图时发现：家到公司 70,000 米，最远的一对是各自一侧挂得最远的两个地点。"""
    (published_environment / "catalog/map.jsonl").write_bytes(MAP_ROW.replace(b"12600", b"70000"))
    refresh_manifest_identity(published_environment / "catalog")
    assert report(published_environment) == [{
        "path": "catalog", "code": "MAP_ROUTE_TOO_LONG",
        "reason": "Two places are farther apart than the slowest travel method can cover in one day in the worst "
                  "weather. cafe -> park is 74517 m, which takes 103496 s at 1.2 m/s in the worst weather; "
                  "the limit is 86400 s."}]
