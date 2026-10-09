"""@brief 完整环境字节缓存与不可变输入在实际运行中的隔离。"""

from __future__ import annotations

import shutil
from types import MappingProxyType

import networkx as nx
import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import FrozenInputs, assert_deeply_immutable, input_digest, object_digest
from worldmock.errors import IntegrityError


@pytest.fixture
def copied_environment(tmp_path):
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    return root


@pytest.mark.parametrize("relative", ["persona.toml", "config.toml", "catalog/manifest.json",
    "catalog/app-catalog.jsonl", "catalog/location.jsonl", "catalog/merchant-catalog.jsonl", "catalog/content-catalog.jsonl",
    "catalog/callbacks.jsonl"])
def test_cache_key_covers_every_actual_input_byte(copied_environment, relative):
    before = input_digest(copied_environment)
    path = copied_environment / relative
    path.write_bytes(path.read_bytes() + b" ")
    assert input_digest(copied_environment) != before


def test_cache_key_covers_names_and_file_set(copied_environment):
    before = input_digest(copied_environment)
    (copied_environment / "catalog/callbacks.jsonl").rename(copied_environment / "catalog/renamed.jsonl")
    assert input_digest(copied_environment) != before


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
