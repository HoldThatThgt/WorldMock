"""当前目录不可变索引与纯加载边界。"""

import subprocess
import sys

import pytest

from tests.data_paths import CATALOG_ROOT
from tests.life.environment_inputs import travel_method_row, walking_only, write_catalog_rows
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity
from worldmock.resource_engine.catalog.loader import load_catalog, validate_catalog_files
from worldmock.exchange_engine.commerce.operators import travel_seconds
from worldmock.foundation.serialization import canonical_json


def test_catalog_is_deeply_immutable():
    catalog = load_catalog(CATALOG_ROOT, 7)
    with pytest.raises(TypeError):
        catalog.apps["invented"] = next(iter(catalog.apps.values()))
    with pytest.raises(TypeError):
        catalog.goods_by_store["home"] = ("invented",)
    assert isinstance(catalog.contents_by_app["bilibili"], tuple)


def test_pure_catalog_loading_does_not_import_runtime_engine():
    source = ("from pathlib import Path; import sys; from worldmock.resource_engine.catalog.loader import load_catalog; "
              "catalog=load_catalog(Path(sys.argv[1]),7); assert 'worldmock.simulation_engine.engine' not in sys.modules; "
              "print(len(catalog.locations))")
    result = subprocess.run([sys.executable, "-c", source, str(CATALOG_ROOT)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8"


CALLBACK_ROW = b'{"cap":3,"initial":1,"reset_on_goods":[],"state":"stock"}\n'
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'
FIXTURE_MAP_SHA256 = "017bbcfedf78e142ac0310242e504655142c92796fb8395c8ccc4a11a3afedf5"


METHOD_ROW = ('{"exertion":"moderate","name":"骑车","price_per_km_fen":0,"speed_meters_per_second":4.5,'
              '"start_price_fen":150,"travel_method_id":"bike"}\n').encode("utf-8")


@pytest.mark.parametrize("callbacks", [None, b"", CALLBACK_ROW], ids=["no-callbacks", "empty-callbacks", "callbacks"])
@pytest.mark.parametrize("distances", [None, b"", MAP_ROW], ids=["no-map", "empty-map", "map"])
@pytest.mark.parametrize("methods", [None, b"", METHOD_ROW], ids=["no-methods", "empty-methods", "methods"])
def test_each_optional_file_loads_absent_empty_or_with_rows(catalog_dir, callbacks, distances, methods):
    """三个可选文件各自不存在、零字节、有内容都能加载和纯校验；不存在与零字节得到同一个世界。"""
    walking_only(catalog_dir.parent)
    files = (("callbacks.jsonl", callbacks), ("map.jsonl", distances), ("travel-method.jsonl", methods))
    for name, content in files:
        if content is not None:
            (catalog_dir / name).write_bytes(content)
    refresh_manifest_identity(catalog_dir)
    validate_catalog_files(catalog_dir)
    catalog = load_catalog(catalog_dir, 7)
    assert [entry.path for entry in catalog.manifest.files] == sorted(
        {"app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl"}
        | {name for name, content in files if content is not None})
    assert set(catalog.callback_states) == ({"stock"} if callbacks else set())
    assert [(row.travel_method_id, row.speed_meters_per_second) for row in catalog.travel_methods.values()] == (
        [("bike", 4.5)] if methods else [])
    assert dict(catalog.apps_by_travel_method) == {"walking": ("amap",), **({"bike": ()} if methods else {})}
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
        "files": [], "format_version": 5, "rules_version": 6}) + b"\n")
    refresh_manifest_identity(directory)
    return directory


def test_farthest_pair_may_take_exactly_one_day_of_walking_in_snow(tmp_path):
    catalog = load_catalog(small_catalog(tmp_path / "catalog", ["home", "office"], [("home", "office", 62208)]), 7)
    assert [(edge.a, edge.b, edge.length_m) for edge in catalog.map_data.edges] == [("home", "office", 62208)]
    assert travel_seconds(1.2, 62208, 0.6) == 86400


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


def slow_catalog(directory, meters, *methods):
    """两个地点、一条声明边，再加数据集定义的出行方式行。"""
    directory.parent.mkdir()
    small_catalog(directory, ["home", "office"], [("home", "office", meters)])
    write_catalog_rows(directory.parent, "travel-method.jsonl", list(methods))
    return directory


def test_day_limit_follows_the_slowest_dataset_defined_travel_method(tmp_path):
    """数据集定义了每秒 0.5 米的方式时，任意两地的上限从 62,208 米变成 25,920 米；纯校验不做这项检查。"""
    crawl, fast = travel_method_row("crawl", "挪步", 0.5), travel_method_row("fast", "快车", 30)
    catalog = load_catalog(slow_catalog(tmp_path / "at-limit" / "catalog", 25920, crawl, fast), 7)
    assert catalog.shortest_m("home", "office") == 25920 and travel_seconds(0.5, 25920, 0.6) == 86400
    directory = slow_catalog(tmp_path / "beyond" / "catalog", 25921, crawl, fast)
    validate_catalog_files(directory)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert str(failure.value) == (
        "MAP_ROUTE_TOO_LONG: Two places are farther apart than the slowest travel method can cover in one day "
        "in the worst weather. home -> office is 25921 m, which takes 86404 s at 0.5 m/s "
        "in the worst weather; the limit is 86400 s.")


def test_day_limit_stays_at_walking_when_every_dataset_method_is_faster(tmp_path):
    """数据集定义的方式都不比步行慢时，上限仍按步行的每秒 1.2 米算。"""
    fast = travel_method_row("fast", "快车", 30)
    assert load_catalog(slow_catalog(tmp_path / "at-limit" / "catalog", 62208, fast), 7).shortest_m(
        "home", "office") == 62208
    with pytest.raises(RegistrationError) as failure:
        load_catalog(slow_catalog(tmp_path / "beyond" / "catalog", 62209, fast), 7)
    assert (failure.value.code, failure.value.detail) == (
        "MAP_ROUTE_TOO_LONG",
        "home -> office is 62209 m, which takes 86402 s at 1.2 m/s in the worst weather; the limit is 86400 s.")


def test_equally_far_pairs_report_the_smallest_pair(tmp_path):
    declared = [("a", "b", 70000), ("a", "c", 70000), ("b", "c", 70000)]
    with pytest.raises(RegistrationError) as failure:
        load_catalog(small_catalog(tmp_path / "catalog", ["c", "a", "b"], declared), 7)
    assert failure.value.code == "MAP_ROUTE_TOO_LONG" and failure.value.detail.startswith("a -> b is 70000 m,")


def test_small_catalog_without_declarations_is_far_inside_the_day_limit(tmp_path):
    for seed in range(8):
        catalog = load_catalog(small_catalog(tmp_path / f"catalog-{seed}", ["a", "b", "c", "d", "e"]), seed)
        assert all(200 <= edge.length_m <= 2000 for edge in catalog.map_data.edges)
