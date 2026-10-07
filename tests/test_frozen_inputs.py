"""@brief 缓存的字节身份、可变分支复制、正式世界隔离和污染拒绝。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest
import networkx as nx
from pydantic import BaseModel, ConfigDict

from tests.frozen_inputs import FrozenInputs, assert_deeply_immutable, clone_semantics, input_digest, object_digest


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
    (tmp_path / "corpora").mkdir()
    for name in ("manifest.json", "map.json", "foods.json", "contents.json"):
        (tmp_path / "catalog" / name).write_text(name, encoding="utf-8")
    (tmp_path / "corpora/lifestyle").mkdir()
    (tmp_path / "corpora/lifestyle/items.json").write_text("items", encoding="utf-8")
    for name in ("manifest.json",):
        (tmp_path / "corpora" / name).write_text(name, encoding="utf-8")
    path = tmp_path / "scenarios/formal.json"
    path.write_text(json.dumps({"corpus_root": "../corpora"}), encoding="utf-8")
    return path


@pytest.mark.parametrize("file", ["catalog/manifest.json", "catalog/map.json", "catalog/foods.json",
                                 "catalog/contents.json", "corpora/manifest.json", "corpora/lifestyle/items.json"])
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
    (tmp_path / "corpora/extra.json").write_text("extra", encoding="utf-8")
    assert input_digest(path) != renamed


def test_cache_hit_reuses_same_bytes_and_changed_input_reloads(tmp_path, monkeypatch):
    path = _input_tree(tmp_path)
    cache, calls = FrozenInputs(), []
    monkeypatch.setattr(cache, "_load", lambda candidate: calls.append(candidate) or {"data": len(calls)})
    first = cache.record(path)
    assert cache.record(path) is first
    (tmp_path / "catalog/foods.json").write_text("changed", encoding="utf-8")
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
