"""@brief 缓存的字节身份、可变分支复制、正式世界隔离和污染拒绝。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest
import networkx as nx
from pydantic import BaseModel, ConfigDict

from tests.frozen_inputs import FrozenInputs, assert_deeply_immutable, clone_semantics, input_digest, object_digest
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.errors import IntegrityError
from worldmock.life import world_setup


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)
    nested: dict


@dataclass(frozen=True)
class _Semantics:
    document: _Frozen
    scenario: _Frozen
    body: _Frozen
    accounts: dict
    catalog: object


def _semantics():
    return _Semantics(_Frozen(nested={"value": [1]}), _Frozen(nested={"value": [2]}),
                      _Frozen(nested={"value": [3]}), {"money": 4}, MappingProxyType({"rule": (1, 2)}))


def test_only_verified_immutable_branches_are_shared():
    original = _semantics()
    copied = clone_semantics(original)
    copied.document.nested["value"].append(9)
    copied.scenario.nested["value"].append(9)
    copied.body.nested["value"].append(9)
    copied.accounts["money"] = 99
    assert original.document.nested == {"value": [1]}
    assert original.scenario.nested == {"value": [2]}
    assert original.body.nested == {"value": [3]}
    assert original.accounts == {"money": 4}
    assert copied.catalog is original.catalog
    with pytest.raises(AssertionError, match="mutable"):
        clone_semantics(_Semantics(original.document, original.scenario, original.body, {}, {"bad": []}))
    with pytest.raises(AssertionError, match="mutable"):
        assert_deeply_immutable(MappingProxyType({"bad": _Frozen(nested={})}))
    with pytest.raises(AssertionError, match="no immutability contract"):
        assert_deeply_immutable(MappingProxyType({"bad": object()}))


def _input_tree(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "catalog").mkdir()
    for name in ("manifest.json", "apps.json", "locations.json", "goods.json", "actions.json", "contents.json", "map.json"):
        (tmp_path / "catalog" / name).write_text(name, encoding="utf-8")
    path = tmp_path / "scenarios/formal.json"
    path.write_text(json.dumps({"schema_version": 8}), encoding="utf-8")
    return path


@pytest.mark.parametrize("file", ["catalog/manifest.json", "catalog/apps.json", "catalog/locations.json",
                                 "catalog/goods.json", "catalog/actions.json", "catalog/contents.json", "catalog/map.json"])
def test_cache_key_changes_for_real_bytes_outside_the_scenario(tmp_path, file):
    path = _input_tree(tmp_path)
    original = input_digest(path)
    target = tmp_path / file
    value = target.read_bytes()
    target.write_bytes(value[:-1] + b"X")
    assert input_digest(path) != original


def test_cache_key_includes_relative_file_names_and_file_set(tmp_path):
    path = _input_tree(tmp_path)
    original = input_digest(path)
    (tmp_path / "catalog/map.json").rename(tmp_path / "catalog/renamed.json")
    renamed = input_digest(path)
    assert renamed != original
    (tmp_path / "catalog/extra.json").write_text("extra", encoding="utf-8")
    assert input_digest(path) != renamed


def test_cache_hit_reuses_same_bytes_and_changed_input_reloads(tmp_path, monkeypatch):
    path = _input_tree(tmp_path)
    cache, calls = FrozenInputs(), []
    monkeypatch.setattr(cache, "_load", lambda candidate: calls.append(candidate) or {"data": len(calls)})
    first = cache.record(path)
    assert cache.record(path) is first
    (tmp_path / "catalog/goods.json").write_text("changed", encoding="utf-8")
    assert cache.record(path) is not first
    assert len(calls) == 2


def test_session_guard_rejects_mutated_cached_object_and_source(tmp_path):
    path = _input_tree(tmp_path)
    digest = input_digest(path)
    payload = {"nested": {"value": [1]}}
    value = {**payload, "object_sha256": object_digest(payload)}
    cache = FrozenInputs()
    cache.records[(path.resolve(), digest)] = value
    cache.verify()
    value["nested"]["value"].append(2)
    with pytest.raises(AssertionError, match="Cached object changed"):
        cache.verify()
    value["nested"]["value"].pop()
    (tmp_path / "catalog/map.json").write_text("changed", encoding="utf-8")
    with pytest.raises(AssertionError, match="Cached source input changed"):
        cache.verify()


def test_object_guard_distinguishes_container_types_and_typed_mapping_keys():
    assert object_digest([1]) != object_digest((1,))
    assert object_digest({1: "value", "1": "value"}) != object_digest({"1": "value"})
    assert object_digest(b"raw") != object_digest("raw")


def test_graph_freeze_alone_does_not_satisfy_shared_input_contract():
    """@brief 成熟图容器的结构冻结不能掩盖仍可改写的边属性。"""
    graph = nx.freeze(nx.Graph([("a", "b", {"length_m": 301})]))
    with pytest.raises(AssertionError, match="mutable dict"):
        assert_deeply_immutable(graph)
    original = object_digest(graph)
    graph["a"]["b"]["length_m"] = 1
    assert object_digest(graph) != original


def test_cache_identity_ignores_directories_the_runtime_does_not_read(tmp_path):
    """@brief 场景与目录以外的文件不进入缓存身份，也不制造无关输入漂移。"""
    path = _input_tree(tmp_path)
    original = input_digest(path)
    for name in ("corpora", "author", "phone"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "unread.json").write_text("invalid JSON", encoding="utf-8")
    assert input_digest(path) == original


def test_cache_key_rejects_a_symbolic_link_inside_the_catalog(tmp_path):
    """@brief 共享缓存仍拒绝外部文件链接，不因语料退役放宽目录隔离。"""
    path = _input_tree(tmp_path)
    (tmp_path / "catalog/link.json").symlink_to(tmp_path / "catalog/map.json")
    with pytest.raises(AssertionError, match="symbolic links"):
        input_digest(path)


def test_real_cache_shares_only_frozen_catalog_and_restores_the_loader(tmp_path):
    """@brief 真实六文件目录只加载一次，每次使用独立场景副本，退出后恢复生产加载函数。"""
    root = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    cache = FrozenInputs()
    record = cache.record(path)
    assert set(record) == {"semantics", "digest", "object_sha256"}
    original_loader = world_setup.load_runtime_scenario
    with cache.use(path):
        first, first_digest = world_setup.load_runtime_scenario(path)
    assert world_setup.load_runtime_scenario is original_loader
    with cache.use(path):
        second, second_digest = world_setup.load_runtime_scenario(path)
    assert world_setup.load_runtime_scenario is original_loader
    assert first_digest == second_digest == record["digest"]
    assert first.catalog is second.catalog is record["semantics"].catalog
    assert first.scenario is not second.scenario
    assert first.document is not second.document
    assert first.body is not second.body
    first.accounts["agent:1"] = 1
    assert second.accounts["agent:1"] == record["semantics"].accounts["agent:1"] == 10000
    cache.verify()


def test_changed_registered_file_revalidates_instead_of_reusing_cached_semantics(tmp_path):
    """@brief 文件变化越过缓存键后仍走真实登记核验，不能复用旧语义掩盖坏环境。"""
    root = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    cache = FrozenInputs()
    cache.record(path)
    target = root / "catalog/actions.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(IntegrityError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        cache.record(path)
    assert len(cache.records) == 1
