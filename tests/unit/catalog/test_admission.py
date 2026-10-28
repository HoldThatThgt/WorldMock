"""当前目录清单的原始字节漂移必须在真实加载入口被拒绝。"""

import json

import pytest

from worldmock.resource_engine.registration.admission import validate_map
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity
from worldmock.resource_engine.catalog.loader import load_catalog, validate_catalog_files
from worldmock.foundation.serialization import canonical_json


@pytest.mark.parametrize("change", ["bytes", "digest", "size", "missing", "manifest_hash"])
def test_file_identity_drift_is_rejected(catalog_dir, change):
    manifest = json.loads((catalog_dir / "manifest.json").read_bytes())
    path = catalog_dir / manifest["files"][0]["path"]
    if change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "missing":
        path.unlink()
    else:
        if change == "digest":
            manifest["files"][0]["sha256"] = "0" * 64
        elif change == "size":
            manifest["files"][0]["size_bytes"] += 1
        else:
            manifest["catalog_content_sha256"] = "0" * 64
        (catalog_dir / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    with pytest.raises(RegistrationError):
        load_catalog(catalog_dir, 7)


HOME_OFFICE = '{"a":"home","b":"office","length_m":12600}'
FIXTURE_MAP_SHA256 = "017bbcfedf78e142ac0310242e504655142c92796fb8395c8ccc4a11a3afedf5"


def declare(directory, *lines):
    """把声明的距离写进夹具副本并按生产发布入口登记这个可选文件。"""
    (directory / "map.jsonl").write_bytes(b"".join(line.encode() + b"\n" for line in lines))
    refresh_manifest_identity(directory)


def refusal(directory):
    """带种子的加载与不带种子的纯校验必须给出同一个登记失败。"""
    with pytest.raises(RegistrationError) as loaded:
        load_catalog(directory, 7)
    with pytest.raises(RegistrationError) as checked:
        validate_catalog_files(directory)
    assert str(loaded.value) == str(checked.value)
    return loaded.value


@pytest.mark.parametrize("lines,code,detail", [
    (['{"a":"home","b":"home","length_m":5}'], "ENVIRONMENT_KIND_INVALID",
     "map.jsonl:1 Map endpoints must satisfy a < b."),
    ([HOME_OFFICE, '{"a":"store","b":"park","length_m":5}'], "ENVIRONMENT_KIND_INVALID",
     "map.jsonl:2 Map endpoints must satisfy a < b."),
    ([HOME_OFFICE, '{"a":"cafe","b":"home","length_m":5}'], "ENVIRONMENT_KIND_INVALID",
     "map.jsonl:2 Map rows must be sorted by (a, b)."),
    ([HOME_OFFICE, '{"a":"home","b":"noodle-1","length_m":5}'], "ENVIRONMENT_KIND_INVALID",
     "map.jsonl:2 Map rows must be sorted by (a, b)."),
    ([HOME_OFFICE, '{"a":"home","b":"park","length_m":5}', '{"a":"home","b":"office","length_m":6}'],
     "ENVIRONMENT_ID_DUPLICATE", "map.jsonl:1 and map.jsonl:3 declare the same pair home-office."),
    ([HOME_OFFICE, HOME_OFFICE], "ENVIRONMENT_ID_DUPLICATE",
     "map.jsonl:1 and map.jsonl:2 declare the same pair home-office."),
    ([HOME_OFFICE, '{"a":"home","b":"school","length_m":5}'], "ENVIRONMENT_REF_UNKNOWN",
     "map.jsonl:2 Unknown location_id: school."),
    (['{"a":"gym","b":"home","length_m":5}'], "ENVIRONMENT_REF_UNKNOWN", "map.jsonl:1 Unknown location_id: gym."),
    (['{"a":"home","b":"公司","length_m":5}'], "ENVIRONMENT_REF_UNKNOWN", "map.jsonl:1 Unknown location_id: 公司."),
])
def test_declared_distance_rows_are_checked_against_registered_places_with_line_numbers(
        catalog_dir, lines, code, detail):
    declare(catalog_dir, *lines)
    failure = refusal(catalog_dir)
    assert (failure.code, failure.detail) == (code, detail)


@pytest.mark.parametrize("lines,code,detail", [
    (['{"a":"home","b":"school","length_m":5}', '{"a":"park","b":"home","length_m":5}'],
     "ENVIRONMENT_KIND_INVALID", "map.jsonl:2 Map endpoints must satisfy a < b."),
    ([HOME_OFFICE, HOME_OFFICE, '{"a":"home","b":"school","length_m":5}'],
     "ENVIRONMENT_REF_UNKNOWN", "map.jsonl:3 Unknown location_id: school."),
    (['{"a":"home","b":"park","length_m":5}', HOME_OFFICE, '{"a":"home","b":"park","length_m":5}'],
     "ENVIRONMENT_ID_DUPLICATE", "map.jsonl:1 and map.jsonl:3 declare the same pair home-park."),
    (['{"a":"office","b":"park","length_m":4000}', HOME_OFFICE, '{"a":"home","b":"park","length_m":3000}'],
     "ENVIRONMENT_KIND_INVALID", "map.jsonl:2 Map rows must be sorted by (a, b)."),
])
def test_declared_distance_checks_run_in_the_documented_order(catalog_dir, lines, code, detail):
    """端点次序先于端点登记，登记先于重复，重复先于行次序，行次序先于互相矛盾。"""
    declare(catalog_dir, *lines)
    failure = refusal(catalog_dir)
    assert (failure.code, failure.detail) == (code, detail)


def test_contradicting_declarations_are_refused_with_the_shorter_route(catalog_dir):
    declare(catalog_dir, HOME_OFFICE, '{"a":"home","b":"park","length_m":3000}',
            '{"a":"office","b":"park","length_m":4000}')
    failure = refusal(catalog_dir)
    assert str(failure) == (
        "MAP_DISTANCE_CONTRADICTED: A declared distance is longer than a route through other declared distances. "
        "map.jsonl:1 home-office declares 12600 m but home -> park -> office is 7000 m.")


@pytest.mark.parametrize("lines,detail", [
    (['{"a":"home","b":"noodle-1","length_m":100}', '{"a":"home","b":"office","length_m":5000}',
      '{"a":"noodle-1","b":"noodle-2","length_m":100}', '{"a":"noodle-2","b":"office","length_m":100}'],
     "map.jsonl:2 home-office declares 5000 m but home -> noodle-1 -> noodle-2 -> office is 300 m."),
    (['{"a":"home","b":"noodle-1","length_m":100}', '{"a":"home","b":"noodle-2","length_m":100}',
      '{"a":"home","b":"office","length_m":5000}', '{"a":"noodle-1","b":"office","length_m":100}',
      '{"a":"noodle-2","b":"office","length_m":100}'],
     "map.jsonl:3 home-office declares 5000 m but home -> noodle-1 -> office is 200 m."),
    (['{"a":"cafe","b":"home","length_m":9000}', '{"a":"cafe","b":"office","length_m":1000}',
      '{"a":"home","b":"office","length_m":1000}', '{"a":"home","b":"park","length_m":50}',
      '{"a":"home","b":"store","length_m":900}', '{"a":"park","b":"store","length_m":50}'],
     "map.jsonl:1 cafe-home declares 9000 m but cafe -> office -> home is 2000 m."),
])
def test_contradiction_reports_the_first_failing_row_and_the_smallest_shortest_route(catalog_dir, lines, detail):
    declare(catalog_dir, *lines)
    failure = refusal(catalog_dir)
    assert (failure.code, failure.detail) == ("MAP_DISTANCE_CONTRADICTED", detail)


@pytest.mark.parametrize("direct", [7000, 6999, 1000])
def test_direct_distance_not_longer_than_the_detour_is_accepted(catalog_dir, direct):
    """绕行总长等于声明值不算矛盾；把直达改到不超过绕行就能加载，三条声明值照样算数。"""
    declare(catalog_dir, f'{{"a":"home","b":"office","length_m":{direct}}}',
            '{"a":"home","b":"park","length_m":3000}', '{"a":"office","b":"park","length_m":4000}')
    validate_catalog_files(catalog_dir)
    catalog = load_catalog(catalog_dir, 7)
    assert (catalog.shortest_m("home", "office"), catalog.shortest_m("home", "park"),
            catalog.shortest_m("office", "park")) == (direct, 3000, 4000)
    validate_map(catalog.map_data, set(catalog.locations))


def test_shortening_one_declaration_too_far_contradicts_another_row(catalog_dir):
    """直达改得太短时，轮到另一条声明比经它绕行更长，报的是那一行。"""
    declare(catalog_dir, '{"a":"home","b":"office","length_m":999}',
            '{"a":"home","b":"park","length_m":3000}', '{"a":"office","b":"park","length_m":4000}')
    failure = refusal(catalog_dir)
    assert (failure.code, failure.detail) == (
        "MAP_DISTANCE_CONTRADICTED", "map.jsonl:3 office-park declares 4000 m but office -> home -> park is 3999 m.")


def test_declaration_file_without_rows_leaves_the_map_unchanged(catalog_dir):
    declare(catalog_dir)
    validate_catalog_files(catalog_dir)
    catalog = load_catalog(catalog_dir, 7)
    assert "map.jsonl" in [entry.path for entry in catalog.manifest.files]
    assert catalog.map_sha256 == FIXTURE_MAP_SHA256
