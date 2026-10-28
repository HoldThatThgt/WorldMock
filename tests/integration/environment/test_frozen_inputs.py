"""@brief 完整环境字节缓存与不可变输入在实际运行中的隔离。"""

from __future__ import annotations

import shutil
from types import MappingProxyType

import networkx as nx
import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import FrozenInputs, assert_deeply_immutable, input_digest, object_digest
from tests.life.environment_inputs import walking_only
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity
from worldmock.resource_engine.map.mapgen import map_bytes
from worldmock.foundation.errors import IntegrityError

MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'


@pytest.fixture
def copied_environment(tmp_path):
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    return root


@pytest.mark.parametrize("relative", ["persona.toml", "config.toml", "catalog/manifest.json",
    "catalog/app-catalog.jsonl", "catalog/location.jsonl", "catalog/merchant-catalog.jsonl", "catalog/content-catalog.jsonl",
    "catalog/callbacks.jsonl", "catalog/map.jsonl", "catalog/travel-method.jsonl"])
def test_cache_key_covers_every_actual_input_byte(copied_environment, relative):
    (copied_environment / "catalog/callbacks.jsonl").write_bytes(b"")
    (copied_environment / "catalog/map.jsonl").write_bytes(b"")
    before = input_digest(copied_environment)
    path = copied_environment / relative
    path.write_bytes(path.read_bytes() + b" ")
    assert input_digest(copied_environment) != before


def test_cache_key_covers_names_and_file_set(copied_environment):
    before = input_digest(copied_environment)
    (copied_environment / "catalog/callbacks.jsonl").write_bytes(b"")
    added = input_digest(copied_environment)
    (copied_environment / "catalog/callbacks.jsonl").rename(copied_environment / "catalog/map.jsonl")
    assert len({before, added, input_digest(copied_environment)}) == 3


METHOD_ROW = ('{"exertion":"light","name":"骑车","price_per_km_fen":0,"speed_meters_per_second":4,'
              '"start_price_fen":0,"travel_method_id":"bike"}\n').encode("utf-8")


@pytest.mark.parametrize("files", [{}, {"callbacks.jsonl": b""}, {"map.jsonl": b""}, {"travel-method.jsonl": b""},
                                   {"callbacks.jsonl": b"", "map.jsonl": MAP_ROW, "travel-method.jsonl": METHOD_ROW}],
                         ids=["required-only", "empty-callbacks", "empty-map", "empty-methods", "all-three"])
def test_new_run_freezes_exactly_the_listed_catalog_files_and_the_generated_map(
        copied_environment, engine_factory, files):
    """新建运行冻结的素材恰为两份 TOML、清单、清单登记的每个目录文件，加生成的 map.json。"""
    walking_only(copied_environment)
    for name, content in files.items():
        (copied_environment / "catalog" / name).write_bytes(content)
    refresh_manifest_identity(copied_environment / "catalog")
    engine = engine_factory(copied_environment)
    store, catalog = engine.context.store, engine.context.catalog
    listed = {entry.path for entry in catalog.manifest.files}
    assert listed == {"app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl",
                      "merchant-catalog.jsonl"} | set(files)
    assets = store.manifest["environment_assets"]
    assert set(assets) == {"persona.toml", "config.toml", "catalog/manifest.json", "map.json"} | {
        f"catalog/{name}" for name in listed}
    for name in set(assets) - {"map.json"}:
        assert (store.run_dir / "assets" / assets[name]["sha256"]).read_bytes() == (
            copied_environment / name).read_bytes()
    assert (store.run_dir / "assets" / assets["map.json"]["sha256"]).read_bytes() == map_bytes(catalog.map_data)
    assert not (copied_environment / "catalog/map.json").exists()


def test_real_cache_shares_only_strictly_frozen_inputs(copied_environment):
    cache = FrozenInputs()
    first = cache.record(copied_environment)
    assert cache.record(copied_environment) is first
    with cache.use(copied_environment) as inputs:
        assert inputs is first["inputs"]
        assert_deeply_immutable(inputs)
    cache.verify()


def test_changed_catalog_bytes_are_revalidated(copied_environment):
    cache = FrozenInputs()
    cache.record(copied_environment)
    path = copied_environment / "catalog/content-catalog.jsonl"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(IntegrityError):
        cache.record(copied_environment)
    assert len(cache.records) == 1


def test_cache_guard_rejects_source_mutation(copied_environment):
    cache = FrozenInputs()
    cache.record(copied_environment)
    path = copied_environment / "persona.toml"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(AssertionError, match="source input changed"):
        cache.verify()


def test_cache_guard_rejects_object_mutation(copied_environment):
    cache = FrozenInputs()
    inputs = cache.record(copied_environment)["inputs"]
    object.__setattr__(inputs, "home_location_id", "changed")
    with pytest.raises(AssertionError, match="object changed"):
        cache.verify()


def test_structural_graph_freeze_does_not_hide_mutable_attributes():
    graph = nx.freeze(nx.Graph([("a", "b", {"length_m": 301})]))
    with pytest.raises(AssertionError, match="mutable dict"):
        assert_deeply_immutable(graph)
    before = object_digest(graph)
    graph["a"]["b"]["length_m"] = 1
    assert object_digest(graph) != before


def test_object_digest_preserves_container_and_key_types():
    assert object_digest([1]) != object_digest((1,))
    assert object_digest({1: "value", "1": "value"}) != object_digest({"1": "value"})
    assert object_digest(b"raw") != object_digest("raw")
    with pytest.raises(AssertionError, match="immutability contract"):
        assert_deeply_immutable(MappingProxyType({"bad": object()}))


def test_cache_rejects_symlinks(copied_environment, monkeypatch):
    path = copied_environment / "catalog/app-catalog.jsonl"
    original = type(path).is_symlink
    monkeypatch.setattr(type(path), "is_symlink", lambda candidate: candidate == path or original(candidate))
    with pytest.raises(AssertionError, match="symbolic links"):
        input_digest(copied_environment)
