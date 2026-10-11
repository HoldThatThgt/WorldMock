"""当前目录不可变索引与纯加载边界。"""

import subprocess
import sys

import pytest

from tests.data_paths import CATALOG_ROOT
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.exchange.operators import travel_seconds
from worldmock.footprints.models import canonical_json


def test_catalog_is_deeply_immutable():
    catalog = load_catalog(CATALOG_ROOT, 7)
    with pytest.raises(TypeError):
        catalog.apps["invented"] = next(iter(catalog.apps.values()))
    with pytest.raises(TypeError):
        catalog.goods_by_store["home"] = ("invented",)
    assert isinstance(catalog.contents_by_app["bilibili"], tuple)


def test_pure_catalog_loading_does_not_import_runtime_engine():
    source = ("from pathlib import Path; import sys; from worldmock.catalog.loader import load_catalog; "
              "catalog=load_catalog(Path(sys.argv[1]),7); assert 'worldmock.life.engine' not in sys.modules; "
              "print(len(catalog.locations))")
    result = subprocess.run([sys.executable, "-c", source, str(CATALOG_ROOT)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8"


CALLBACK_ROW = b'{"cap":3,"initial":1,"reset_on_goods":[],"state":"stock"}\n'
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'
FIXTURE_MAP_SHA256 = "017bbcfedf78e142ac0310242e504655142c92796fb8395c8ccc4a11a3afedf5"


@pytest.mark.parametrize("callbacks", [None, b"", CALLBACK_ROW], ids=["no-callbacks", "empty-callbacks", "callbacks"])
@pytest.mark.parametrize("distances", [None, b"", MAP_ROW], ids=["no-map", "empty-map", "map"])
def test_each_optional_file_loads_absent_empty_or_with_rows(catalog_dir, callbacks, distances):
    """可选文件不存在、零字节、有内容三种情形都能加载和纯校验；不存在与零字节得到同一个世界。"""
    for name, content in (("callbacks.jsonl", callbacks), ("map.jsonl", distances)):
        if content is not None:
            (catalog_dir / name).write_bytes(content)
    refresh_manifest_identity(catalog_dir)
    validate_catalog_files(catalog_dir)
    catalog = load_catalog(catalog_dir, 7)
    assert [entry.path for entry in catalog.manifest.files] == sorted(
        {"app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl"}
        | ({"callbacks.jsonl"} if callbacks is not None else set())
        | ({"map.jsonl"} if distances is not None else set()))
    assert set(catalog.callback_states) == ({"stock"} if callbacks else set())
    if distances:
        assert catalog.shortest_m("home", "office") == 12600 and catalog.map_sha256 != FIXTURE_MAP_SHA256
    else:
        assert catalog.shortest_m("home", "office") == 1957 and catalog.map_sha256 == FIXTURE_MAP_SHA256


def small_catalog(directory, location_ids, declared=None):
    """只有地点的最小合法目录：其余必需文件零行，距离由测试逐条声明。"""
    directory.mkdir()
    for name in ("app-catalog.jsonl", "content-catalog.jsonl", "merchant-catalog.jsonl"):
        (directory / name).write_bytes(b"")
    rows = [{"app_ids": [], "kind": "place", "location_id": key, "name": key} for key in sorted(location_ids)]
    rows[0]["is_home"] = True
    (directory / "location.jsonl").write_bytes(b"".join(canonical_json(row) + b"\n" for row in rows))
    if declared is not None:
        (directory / "map.jsonl").write_bytes(b"".join(
            canonical_json({"a": a, "b": b, "length_m": meters}) + b"\n" for a, b, meters in declared))
    (directory / "manifest.json").write_bytes(canonical_json({
        "catalog_content_sha256": "0" * 64, "environment_id": "map-limit", "environment_version": "1",
        "files": [], "format_version": 4, "rules_version": 4}) + b"\n")
    refresh_manifest_identity(directory)
    return directory


def test_farthest_pair_may_take_exactly_one_day_of_walking_in_snow(tmp_path):
    catalog = load_catalog(small_catalog(tmp_path / "catalog", ["home", "office"], [("home", "office", 62208)]), 7)
    assert [(edge.a, edge.b, edge.length_m) for edge in catalog.map_data.edges] == [("home", "office", 62208)]
    assert travel_seconds("walking", 62208, 0.6) == 86400


@pytest.mark.parametrize("meters,seconds", [(62209, 86402), (70000, 97223)])
def test_farthest_pair_one_metre_beyond_a_day_is_refused_only_when_the_map_is_generated(tmp_path, meters, seconds):
    directory = small_catalog(tmp_path / "catalog", ["home", "office"], [("home", "office", meters)])
    validate_catalog_files(directory)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert str(failure.value) == (
        "MAP_ROUTE_TOO_LONG: Two places are farther apart than the slowest travel method can cover in one day "
        f"in the worst weather. home -> office is {meters} m, which takes {seconds} s at 1.2 m/s "
        "in the worst weather; the limit is 86400 s.")


@pytest.mark.parametrize("seed", range(12))
def test_two_legal_declarations_in_a_row_are_refused_for_the_farthest_pair(tmp_path, seed):
    """每条声明都在一天之内，串起来的两端超出一天：报的是没有声明过的最远那一对。"""
    declared = [("a", "b", 36000), ("a", "c", 46000), ("b", "c", 10000), ("b", "d", 44000), ("c", "d", 34000)]
    directory = small_catalog(tmp_path / "catalog", ["a", "b", "c", "d"], declared)
    validate_catalog_files(directory)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, seed)
    assert (failure.value.code, failure.value.detail) == (
        "MAP_ROUTE_TOO_LONG",
        "a -> d is 80000 m, which takes 111112 s at 1.2 m/s in the worst weather; the limit is 86400 s.")


def test_equally_far_pairs_report_the_smallest_pair(tmp_path):
    declared = [("a", "b", 70000), ("a", "c", 70000), ("b", "c", 70000)]
    with pytest.raises(RegistrationError) as failure:
        load_catalog(small_catalog(tmp_path / "catalog", ["c", "a", "b"], declared), 7)
    assert failure.value.code == "MAP_ROUTE_TOO_LONG" and failure.value.detail.startswith("a -> b is 70000 m,")


def test_small_catalog_without_declarations_is_far_inside_the_day_limit(tmp_path):
    for seed in range(8):
        catalog = load_catalog(small_catalog(tmp_path / f"catalog-{seed}", ["a", "b", "c", "d", "e"]), seed)
        assert all(200 <= edge.length_m <= 2000 for edge in catalog.map_data.edges)
