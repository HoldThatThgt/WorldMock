"""唯一种子在加载时生成地图，结构与字节均确定。"""

import hashlib

import networkx as nx
import pytest

from worldmock.catalog.admission import validate_map
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.graph import Graph
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.mapgen import generate_map, map_bytes
from worldmock.catalog.models import MapEdge
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


FIXTURE_MAP_SHA256 = "017bbcfedf78e142ac0310242e504655142c92796fb8395c8ccc4a11a3afedf5"
FIXTURE_LOCATIONS = ("cafe", "home", "noodle-1", "noodle-2", "office", "parcel-station", "park", "store")


def _declared(*rows):
    return tuple(MapEdge(a=a, b=b, length_m=meters) for a, b, meters in rows)


def _fixture_map_seed():
    return random_stream(hashlib.sha256(b"random:7").hexdigest(), "map").getrandbits(64)


def test_fixture_map_without_declarations_keeps_its_published_digest(catalog_dir):
    """没有 map.jsonl 时，夹具地点加种子 7 的地图与修订前逐字节相同。"""
    catalog = load_catalog(catalog_dir, 7)
    assert tuple(catalog.locations) == FIXTURE_LOCATIONS and not (catalog_dir / "map.jsonl").exists()
    assert catalog.map_sha256 == FIXTURE_MAP_SHA256
    generated = generate_map(FIXTURE_LOCATIONS, _fixture_map_seed())
    assert hashlib.sha256(map_bytes(generated)).hexdigest() == FIXTURE_MAP_SHA256
    assert generated == generate_map(FIXTURE_LOCATIONS, _fixture_map_seed(), ())
    assert len(generated.edges) == 10 and Graph(generated).path("home", "office") == ("home", "park", "office")
    assert Graph(generated).distance_meters("home", "office") == 1745 + 212


def test_fixture_declaration_of_home_to_office_drops_the_bypassing_edges():
    """声明家到公司 12,600 米：一条树边因两端已连通被丢掉，一条额外边因会绕过声明被丢掉且不补。"""
    graph = generate_map(FIXTURE_LOCATIONS, _fixture_map_seed(), _declared(("home", "office", 12600)))
    assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == [
        ("cafe", "parcel-station", 1869), ("home", "office", 12600), ("home", "park", 1745),
        ("noodle-1", "noodle-2", 1156), ("noodle-1", "office", 1655), ("noodle-1", "store", 779),
        ("noodle-2", "store", 1532), ("office", "parcel-station", 903), ("parcel-station", "store", 212)]
    assert hashlib.sha256(map_bytes(graph)).hexdigest() == (
        "4f50d86da786151125b7f067349b1e0b96e99a2564728c032fea81be14e74f3e")
    routes = Graph(graph)
    assert routes.distance_meters("home", "office") == 12600 and routes.path("home", "office") == ("home", "office")
    assert routes.distance_meters("office", "park") == 12600 + 1745
    assert len(graph.edges) < len(FIXTURE_LOCATIONS) - 1 + (len(FIXTURE_LOCATIONS) - 1) // 2
    validate_map(graph, set(FIXTURE_LOCATIONS))


@pytest.mark.parametrize("count", [3, 5, 8, 13])
@pytest.mark.parametrize("meters", [1, 50, 2000, 5000, 30000])
def test_declared_distances_always_hold_on_a_connected_map(count, meters):
    """任意种子下：声明边原样在图里，声明的地点对最短距离等于声明值，全图连通，随机边都在 200 到 2000 米。"""
    nodes = [f"place-{index:02}" for index in range(count)]
    for seed in range(25):
        picker = random_stream(f"{count}:{meters}", f"declared:{seed}")
        pairs = sorted(picker.sample([(a, b) for a in nodes for b in nodes if a < b], picker.randint(1, count)))
        declared = _declared(*((a, b, meters) for a, b in pairs))
        graph = generate_map(nodes, seed, declared)
        validate_map(graph, set(nodes))
        lengths = {(edge.a, edge.b): edge.length_m for edge in graph.edges}
        routes = Graph(graph)
        for edge in declared:
            assert lengths[edge.a, edge.b] == meters
            assert routes.distance_meters(edge.a, edge.b) == meters
        assert all(200 <= length <= 2000 for pair, length in lengths.items() if pair not in pairs)
        connecting = nx.number_connected_components(nx.Graph(pairs)) + count - len({n for pair in pairs for n in pair}) - 1
        assert len(pairs) + connecting <= len(graph.edges) <= len(pairs) + connecting + (count - 1) // 2
        assert graph == generate_map(list(reversed(nodes)), seed, tuple(reversed(declared)))


def test_declared_distance_shorter_than_every_random_edge_keeps_all_extra_edges():
    """声明值小于任何随机边时没有额外边能绕过它，抽到的额外边全部保留。"""
    nodes = [f"place-{index:02}" for index in range(9)]
    for seed in range(20):
        graph = generate_map(nodes, seed, _declared((nodes[0], nodes[1], 1)))
        assert len(graph.edges) == 1 + (len(nodes) - 2) + (len(nodes) - 1) // 2
        assert Graph(graph).distance_meters(nodes[0], nodes[1]) == 1


@pytest.mark.parametrize("nodes,rows", [
    (["a", "b"], [("a", "b", 70000)]),
    (["a", "b", "c"], [("a", "b", 500), ("a", "c", 600), ("b", "c", 700)]),
])
def test_candidates_run_out_when_every_pair_is_declared(nodes, rows):
    """全部地点对都声明时没有候选可抽，有几条取几条，地图就是声明的边。"""
    for seed in range(10):
        graph = generate_map(nodes, seed, _declared(*rows))
        assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == rows


def test_three_places_with_two_declared_pairs_take_the_single_remaining_candidate():
    """三个地点声明两对：随机树的边都不再需要，唯一的候选在不绕过声明时保留。"""
    for seed in range(40):
        graph = generate_map(["a", "b", "c"], seed, _declared(("a", "b", 300), ("b", "c", 300)))
        edges = {(edge.a, edge.b): edge.length_m for edge in graph.edges}
        assert edges["a", "b"] == edges["b", "c"] == 300
        assert set(edges) <= {("a", "b"), ("a", "c"), ("b", "c")}
        if ("a", "c") in edges:
            assert 200 <= edges["a", "c"] <= 2000
        assert Graph(graph).distance_meters("a", "b") == Graph(graph).distance_meters("b", "c") == 300
