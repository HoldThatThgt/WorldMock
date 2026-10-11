"""@brief 真实 CLI 验证纯输入、原文字节身份、模型服务与地点引用。"""

import json

import pytest

from tests.life.environment_inputs import catalog_rows, travel_method_row, walking_only, write_catalog_rows
from tests.life.prerequisite_inputs import CLOCK, CLOCK_PREREQUISITE
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


@pytest.mark.parametrize("original,replacement,reason", [
    (b"schema_version = 2\n", b"schema_version = 1\n", "schema_version: Input should be 2"),
    (b"coping = \"\xe6\x8b\xbc\xe9\xab\x98\xe8\xbe\xbe\xe6\xa8\xa1\xe5\x9e\x8b\"\n", b"", "coping is required"),
    (b"social_need_growth_per_hour = 0.06\n", b"", "social_need_growth_per_hour is required"),
    (b"wake_window = [7.0, 7.5]\n", b"", "wake_window is required"),
    (b"age = 32\n", b"", "identity.age is required"),
    (b"lunch_window = [12.0, 13.0]\n", b"lunch_window = [13.0, 12.0]\n",
     "lunch_window: must be two finite hours with start < end"),
    (b"sleep_need_hours = 7.5\n", b"sleep_need_hours = 7.5\nbig_five = 4\n", "big_five is not a persona.toml field"),
])
def test_persona_table_failures_are_reported_with_the_field_named(published_environment, original, replacement,
                                                                   reason):
    """@brief 结构版本 1、缺少必填的卡片字段、不合格的时段和未知键都让 validate 非零退出，输出里是 persona.toml、
    PERSONA_INVALID 和写出字段的英文原因。"""
    path = published_environment / "persona.toml"
    assert path.read_bytes().count(original) == 1
    path.write_bytes(path.read_bytes().replace(original, replacement))
    assert report(published_environment) == [{"path": "persona.toml", "code": "PERSONA_INVALID", "reason": reason}]


def test_windows_without_any_plausible_order_pass_validation(published_environment):
    """@brief 时段之间没有任何检查：早于晚饭的就寝、清晨才开始的就寝写法、午夜之后的下班和重叠的午饭晚饭都通过 validate。"""
    path = published_environment / "persona.toml"
    content = path.read_bytes()
    for original, replacement in (
            (b"lunch_window = [12.0, 13.0]\n", b"lunch_window = [12.0, 19.0]\n"),
            (b"dinner_window = [18.5, 19.5]\n", b"dinner_window = [18.5, 19.5]\nleave_work_window = [25.0, 25.5]\n"),
            (b"bedtime_window = [23.0, 24.0]\n", b"bedtime_window = [5.0, 7.0]\narrive_home_window = [18.25, 18.75]\n")):
        assert content.count(original) == 1
        content = content.replace(original, replacement)
    path.write_bytes(content)
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


@pytest.mark.parametrize("arguments", [("profile",), ("profile", "compile", "--persona", "persona.toml"),
                                       ("profile", "check", "--persona", "persona.toml"),
                                       ("profile", "validate", "--environment", "environment")])
def test_profile_is_not_a_command(arguments):
    """@brief 人物卡片没有自己的命令：真实入口把 profile 当作无效选择，以参数错误退出，不产生任何输出文件。"""
    result = _command(*arguments)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "invalid choice: 'profile'" in result.stderr and result.stdout == ""


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


METHOD_ROW = ('{"exertion":"light","name":"骑车","price_per_km_fen":0,"speed_meters_per_second":4,'
              '"start_price_fen":0,"travel_method_id":"bike"}\n').encode("utf-8")


@pytest.mark.parametrize("files", [
    {}, {"callbacks.jsonl": b"", "map.jsonl": b"", "travel-method.jsonl": b""},
    {"callbacks.jsonl": CALLBACK_ROW, "map.jsonl": MAP_ROW, "travel-method.jsonl": METHOD_ROW}],
    ids=["four-required-files", "empty-optional-files", "all-seven-catalog-files"])
def test_package_with_or_without_optional_catalog_files_is_valid(published_environment, files):
    """@brief 夹具是清单加五个文件；只有四个必需文件的环境包通过，三个可选文件放空或放内容并刷新清单后同样通过。"""
    catalog = published_environment / "catalog"
    assert sorted(path.name for path in catalog.iterdir()) == [
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "manifest.json", "merchant-catalog.jsonl",
        "travel-method.jsonl"]
    assert accepted(published_environment)["valid"] is True
    walking_only(published_environment)
    for name, content in files.items():
        (catalog / name).write_bytes(content)
    refresh_manifest_identity(catalog)
    assert sorted(path.name for path in catalog.iterdir()) == sorted([
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "manifest.json", "merchant-catalog.jsonl",
        *files])
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


@pytest.mark.parametrize("name", ["travel-methods.jsonl", "travel_method.jsonl", "callback.jsonl", "map.json"])
def test_catalog_entry_outside_the_closed_set_reports_its_actual_path(published_environment, name):
    """@brief 闭集外的文件名报 ENVIRONMENT_STALE_FILE，reason 带着实际路径。"""
    extra = published_environment / "catalog" / name
    extra.write_bytes(b"")
    error = catalog_error(published_environment, "ENVIRONMENT_STALE_FILE")
    assert error["reason"] == f"The environment contains an entry outside its declared package. {extra}"


@pytest.mark.parametrize("name", ["callbacks.jsonl", "map.jsonl", "travel-method.jsonl"])
def test_optional_file_placed_without_refreshing_the_manifest_reports_its_name(published_environment, name):
    walking_only(published_environment)
    (published_environment / "catalog" / name).write_bytes(b"")
    error = catalog_error(published_environment, "ENVIRONMENT_FILE_UNLISTED")
    assert error["reason"] == f"The manifest does not list a catalog file that is required or present. {name}"
    refresh_manifest_identity(published_environment / "catalog")
    assert accepted(published_environment)["valid"] is True


@pytest.mark.parametrize("rows,code,reason", [
    ([travel_method_row("bike", "骑车", 4), travel_method_row("car", " 骑 车 ", 9)], "ENVIRONMENT_ID_DUPLICATE",
     "An identifier or display name is declared twice in one catalog file. "
     "travel-method.jsonl:1 and travel-method.jsonl:2 share the name 骑车."),
    ([travel_method_row("bike", "步行", 4)], "ENVIRONMENT_ID_DUPLICATE",
     "An identifier or display name is declared twice in one catalog file. "
     "travel-method.jsonl:1 uses the built-in name 步行."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("walking", "散步", 1)], "ENVIRONMENT_ID_DUPLICATE",
     "An identifier or display name is declared twice in one catalog file. "
     "travel-method.jsonl:2 uses the reserved identity walking."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("bike", "单车", 5)], "ENVIRONMENT_ID_DUPLICATE",
     "An identifier or display name is declared twice in one catalog file. "
     "travel-method.jsonl:1 and travel-method.jsonl:2 share the identity bike."),
    ([travel_method_row("car", "开车", 9), travel_method_row("bike", "骑车", 4)], "ENVIRONMENT_KIND_INVALID",
     "A declared kind is outside the fixed set. "
     "travel-method.jsonl:2 Travel method rows must be sorted by travel_method_id."),
])
def test_travel_method_registration_failures_report_their_lines(published_environment, rows, code, reason):
    """@brief 出行方式重名、使用自带名称或保留身份、身份重复、未排序：validate 非零退出，只报这一条，reason 带行号。"""
    walking_only(published_environment)
    write_catalog_rows(published_environment, "travel-method.jsonl", rows)
    assert report(published_environment) == [{"path": "catalog", "code": code, "reason": reason}]


def test_travel_method_row_number_error_reports_its_line(published_environment):
    """@brief 速度不大于 0 的出行方式行：数值登记码，reason 以固定原因后接文件名和行号开头。"""
    walking_only(published_environment)
    write_catalog_rows(published_environment, "travel-method.jsonl", [
        travel_method_row("bike", "骑车", 4), travel_method_row("car", "开车", 0)])
    error = catalog_error(published_environment, "ENVIRONMENT_NUMBER_INVALID")
    assert error["reason"].startswith("A declared number is outside its allowed domain. travel-method.jsonl:2 ")
    assert "speed_meters_per_second must be greater than 0." in error["reason"]


def test_function_app_referring_to_an_unregistered_travel_method_reports_its_line(published_environment):
    """@brief 删掉出行方式文件后，高德地图仍绑定夹具的三种方式：报未登记引用，reason 指出 app 行号与第一个身份。"""
    (published_environment / "catalog/travel-method.jsonl").unlink()
    refresh_manifest_identity(published_environment / "catalog")
    assert report(published_environment) == [{
        "path": "catalog", "code": "ENVIRONMENT_REF_UNKNOWN",
        "reason": "A binding refers to an object that is not registered. "
                  "app-catalog.jsonl:2 Unknown travel_method_id: bus."}]


@pytest.mark.parametrize("trigger,code,reason", [
    ({"class": "action", "key": "travel", "travel_method_id": "boat"}, "ENVIRONMENT_REF_UNKNOWN",
     "A binding refers to an object that is not registered. callbacks.jsonl:2 Unknown travel_method_id: boat."),
    ({"class": "action", "key": "drive"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "travel"}, "ENVIRONMENT_KIND_INVALID", None),
])
def test_callback_travel_trigger_failures_report_their_lines(published_environment, trigger, code, reason):
    """@brief 回调的出行触发指向未登记的出行方式、写旧出行动作名或不带出行方式：各报规定的登记码，reason 带行号。"""
    write_catalog_rows(published_environment, "callbacks.jsonl", [
        {"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": []},
        {"rule_id": "rule", "trigger": trigger, "effect": {"type": "state_delta", "state": "stock", "delta": -1}}])
    error = catalog_error(published_environment, code)
    if reason is None:
        assert error["reason"].startswith("A declared kind is outside the fixed set. callbacks.jsonl:2 ")
    else:
        assert error["reason"] == reason


def test_function_app_binding_a_retired_travel_action_is_refused(published_environment):
    """@brief 功能 app 的 actions 里写旧出行动作名：版本号正确不构成放行，报种类错误并指出 app 行号。"""
    rows = catalog_rows(published_environment, "app-catalog.jsonl")
    amap = next(row for row in rows if row["app_id"] == "amap")
    amap.pop("travel_method_ids")
    amap["actions"] = ["drive", "take_bus", "take_taxi", "walk"]
    write_catalog_rows(published_environment, "app-catalog.jsonl", rows)
    error = catalog_error(published_environment, "ENVIRONMENT_KIND_INVALID")
    assert error["reason"].startswith("A declared kind is outside the fixed set. app-catalog.jsonl:2 ")


def test_dataset_method_slower_than_walking_tightens_the_day_limit(published_environment):
    """@brief 数据集定义了每秒 0.5 米的方式：家到公司声明 30,000 米时步行走得到而它走不到，validate 报最远的一对。"""
    write_catalog_rows(published_environment, "travel-method.jsonl", [travel_method_row("crawl", "挪步", 0.5)])
    rows = catalog_rows(published_environment, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "amap")["travel_method_ids"] = ["crawl", "walking"]
    write_catalog_rows(published_environment, "app-catalog.jsonl", rows)
    (published_environment / "catalog/map.jsonl").write_bytes(MAP_ROW.replace(b"12600", b"30000"))
    refresh_manifest_identity(published_environment / "catalog")
    error = catalog_error(published_environment, "MAP_ROUTE_TOO_LONG")
    assert error["reason"].startswith(
        "Two places are farther apart than the slowest travel method can cover in one day in the worst weather. ")
    assert " s at 0.5 m/s in the worst weather; the limit is 86400 s." in error["reason"]


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


def with_dingtalk(root, content):
    """@brief 夹具副本加入内容 app 钉钉和它的一条内容，两份目录文件都按身份升序写回并刷新清单。"""
    republish_rows(root, "app-catalog.jsonl", lambda rows: sorted(
        [*rows, {"app_id": "dingtalk", "name": "钉钉", "role": "content"}], key=lambda row: row["app_id"]))
    republish_rows(root, "content-catalog.jsonl", lambda rows: sorted(
        [*rows, content], key=lambda row: row["content_id"]))


def test_package_with_a_legal_content_prerequisite_is_valid(published_environment):
    """@brief 钉钉里一条只在办公室、只在工作日上下班时段能开始的打卡：validate 通过。"""
    with_dingtalk(published_environment, CLOCK)
    assert CLOCK["prerequisite"] == CLOCK_PREREQUISITE and CLOCK_PREREQUISITE["location_kinds"] == ["office"]
    assert accepted(published_environment)["valid"] is True


def test_prerequisite_written_on_a_location_content_is_a_registration_failure(published_environment):
    """@brief 前提只能写在 app 内容上：写在绑定到地点类型的内容上，validate 非零退出并报出登记码与详情。"""
    def gate(rows):
        rest = next(row for row in rows if row["content_id"] == "c-rest")
        assert rest["location_kinds"] == ["home"] and "app_ids" not in rest
        rest["prerequisite"] = {"location_kinds": ["home"]}
        return rows

    republish_rows(published_environment, "content-catalog.jsonl", gate)
    assert report(published_environment) == [{
        "path": "catalog", "code": "CONTENT_PREREQUISITE_INVALID",
        "reason": "A content prerequisite must belong to an app content and declare location kinds, opening hours, "
                  "or both. c-rest is a location content."}]


def test_prerequisite_naming_an_unregistered_location_kind_is_a_registration_failure(published_environment):
    """@brief 前提的地点类型要与某个登记地点的类型逐字相等：没有地点声明的类型以悬空绑定的登记码拒绝。"""
    with_dingtalk(published_environment, {**CLOCK, "prerequisite": {"location_kinds": ["factory"]}})
    assert report(published_environment) == [{
        "path": "catalog", "code": "CONTENT_BINDING_DANGLING",
        "reason": "A content names a location kind that no registered location declares. "
                  "dingtalk-clock prerequisite names factory."}]
