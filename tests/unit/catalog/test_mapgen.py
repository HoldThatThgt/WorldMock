"""唯一种子在加载时生成地图，结构与字节均确定。"""

import hashlib

import pytest

from worldmock.catalog.admission import validate_map
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.mapgen import generate_map, map_bytes
from worldmock.life.randomness import random_stream


def test_map_is_sorted_deterministic_and_seeded(catalog_dir):
    nodes = ["g", "f", "e", "d", "c", "b", "a"]
    first = generate_map(nodes, 7)
    assert map_bytes(first) == map_bytes(generate_map(list(reversed(nodes)), 7))
    assert map_bytes(first) != map_bytes(generate_map(nodes, 8))
    assert first.nodes == tuple("abcdefg")
    assert len(first.edges) == 9
    catalog = load_catalog(catalog_dir, 7)
    state = hashlib.sha256(b"random:7").hexdigest()
    assert catalog.map_data == generate_map(tuple(catalog.locations), random_stream(state, "map").getrandbits(64))
    assert not (catalog_dir / "map.json").exists()


@pytest.mark.parametrize("count", [2, 3, 7, 20])
def test_edge_counts_lengths_and_connectivity(count):
    nodes = [f"place-{index:02}" for index in range(count)]
    for seed in range(20):
        graph = generate_map(nodes, seed)
        assert len(graph.edges) == count - 1 + (count - 1) // 2
        assert len({(edge.a, edge.b) for edge in graph.edges}) == len(graph.edges)
        assert all(edge.a < edge.b and 200 <= edge.length_m <= 2000 for edge in graph.edges)
        validate_map(graph, set(nodes))


@pytest.mark.parametrize("nodes,seed,code", [(["a"], 7, "MAP_TOO_SMALL"),
                                           (["a", "a"], 7, "ENVIRONMENT_ID_DUPLICATE"),
                                           (["a", "b"], True, "ENVIRONMENT_NUMBER_INVALID")])
def test_invalid_map_inputs_are_rejected(nodes, seed, code):
    with pytest.raises(RegistrationError, match=code):
        generate_map(nodes, seed)


def test_frozen_tree_extra_edge_and_length_stream():
    graph = generate_map(["c", "a", "b"], 7)
    assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == [
        ("a", "b", 508), ("a", "c", 1008), ("b", "c", 1533)]
    assert map_bytes(graph) == (
        b'{"edges":[{"a":"a","b":"b","length_m":508},{"a":"a","b":"c","length_m":1008},'
        b'{"a":"b","b":"c","length_m":1533}],"nodes":["a","b","c"]}\n')
