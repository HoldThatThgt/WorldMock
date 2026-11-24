"""唯一种子在加载时生成地图，结构与字节均确定。"""

import hashlib

import networkx as nx
import pytest

from worldmock.resource_engine.registration.admission import validate_map
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.resource_engine.map.mapgen import generate_map, map_bytes
from worldmock.resource_engine.catalog.models import Location, MapEdge
from worldmock.foundation.randomness import random_stream


def _places(ids, city_id="test-city"):
    return tuple(Location(location_id=item, name=item, kind="place", city_id=city_id, app_ids=()) for item in ids)


def _rstate(seed):
    return hashlib.sha256(f"random:{seed}".encode()).hexdigest()


def test_map_is_sorted_deterministic_and_seeded(catalog_dir):
    nodes = ["g", "f", "e", "d", "c", "b", "a"]
    places = _places(nodes)
    first = generate_map(places, _rstate(7))
    assert map_bytes(first) == map_bytes(generate_map(list(reversed(places)), _rstate(7)))
    assert map_bytes(first) != map_bytes(generate_map(places, _rstate(8)))
    assert first.nodes == tuple("abcdefg")
    assert len(first.edges) == 9
    catalog = load_catalog(catalog_dir, 7)
    assert catalog.map_data == generate_map(tuple(catalog.locations.values()), _rstate(7))
    assert not (catalog_dir / "map.json").exists()


@pytest.mark.parametrize("count", [2, 3, 7, 20])
def test_edge_counts_lengths_and_connectivity(count):
    nodes = [f"place-{index:02}" for index in range(count)]
    places = _places(nodes)
    for seed in range(20):
        graph = generate_map(places, _rstate(seed))
        assert len(graph.edges) == count - 1 + (count - 1) // 2
        assert len({(edge.a, edge.b) for edge in graph.edges}) == len(graph.edges)
        assert all(edge.a < edge.b and 200 <= edge.length_m <= 2000 for edge in graph.edges)
        validate_map(graph, set(nodes))


@pytest.mark.parametrize("nodes,random_state,code", [
    (["a"], _rstate(7), "MAP_TOO_SMALL"),
    (["a", "a"], _rstate(7), "ENVIRONMENT_ID_DUPLICATE"),
    (["a", "b"], True, "ENVIRONMENT_KIND_INVALID"),
    (["a", "b"], "", "ENVIRONMENT_KIND_INVALID"),
])
def test_invalid_map_inputs_are_rejected(nodes, random_state, code):
    with pytest.raises(RegistrationError, match=code):
        generate_map(_places(nodes), random_state)


def test_frozen_tree_extra_edge_and_length_stream():
    graph = generate_map(_places(["c", "a", "b"]), _rstate(7))
    assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == [
        ("a", "b", 1913), ("a", "c", 1379), ("b", "c", 1125)]
    assert map_bytes(graph) == (
        b'{"edges":[{"a":"a","b":"b","length_m":1913},{"a":"a","b":"c","length_m":1379},'
        b'{"a":"b","b":"c","length_m":1125}],"nodes":["a","b","c"]}\n')


FIXTURE_MAP_SHA256 = "c4d0e3f83e4b7684c8bc2a113b4becf8260b3621729a8e9b1ff2d69362a4289a"
FIXTURE_LOCATIONS = ("cafe", "home", "noodle-1", "noodle-2", "office", "parcel-station", "park", "store")


def _declared(*rows):
    return tuple(MapEdge(a=a, b=b, length_m=meters) for a, b, meters in rows)


def test_fixture_map_without_declarations_keeps_its_published_digest(catalog_dir):
    """没有 map.jsonl 时，夹具地点加种子 7、city_id test-city 的地图流与规则一致。"""
    catalog = load_catalog(catalog_dir, 7)
    assert tuple(catalog.locations) == FIXTURE_LOCATIONS and not (catalog_dir / "map.jsonl").exists()
    assert catalog.map_sha256 == FIXTURE_MAP_SHA256
    generated = generate_map(_places(FIXTURE_LOCATIONS), _rstate(7))
    assert hashlib.sha256(map_bytes(generated)).hexdigest() == FIXTURE_MAP_SHA256
    assert generated == generate_map(_places(FIXTURE_LOCATIONS), _rstate(7), ())
    assert len(generated.edges) == 10 and Graph(generated).path("home", "office") == ("home", "store", "office")
    assert Graph(generated).distance_meters("home", "office") == 1353


def test_fixture_declaration_of_home_to_office_drops_the_bypassing_edges():
    """声明家到公司 12,600 米：两端已连通的树边或绕过声明的额外边被丢掉且不补。"""
    graph = generate_map(_places(FIXTURE_LOCATIONS), _rstate(7), _declared(("home", "office", 12600)))
    assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == [
        ("cafe", "parcel-station", 636), ("home", "office", 12600), ("home", "store", 1765),
        ("noodle-1", "noodle-2", 1617), ("noodle-1", "parcel-station", 1046), ("noodle-2", "office", 875),
        ("noodle-2", "park", 908), ("office", "parcel-station", 717), ("parcel-station", "park", 1426)]
    assert hashlib.sha256(map_bytes(graph)).hexdigest() == (
        "f13bf2a0933a3b8c64676ab8e20457663affdbd535036e6c262aceaf4ec606af")
    routes = Graph(graph)
    assert routes.distance_meters("home", "office") == 12600 and routes.path("home", "office") == ("home", "office")
    assert routes.distance_meters("office", "store") == 12600 + 1765
    assert len(graph.edges) < len(FIXTURE_LOCATIONS) - 1 + (len(FIXTURE_LOCATIONS) - 1) // 2
    validate_map(graph, set(FIXTURE_LOCATIONS))


@pytest.mark.parametrize("count", [3, 5, 8, 13])
@pytest.mark.parametrize("meters", [1, 50, 2000, 5000, 30000])
def test_declared_distances_always_hold_on_a_connected_map(count, meters):
    """任意种子下：声明边原样在图里，声明的地点对最短距离等于声明值，全图连通，随机边都在 200 到 2000 米。"""
    nodes = [f"place-{index:02}" for index in range(count)]
    places = _places(nodes)
    for seed in range(25):
        picker = random_stream(f"{count}:{meters}", f"declared:{seed}")
        pairs = sorted(picker.sample([(a, b) for a in nodes for b in nodes if a < b], picker.randint(1, count)))
        declared = _declared(*((a, b, meters) for a, b in pairs))
        graph = generate_map(places, _rstate(seed), declared)
        validate_map(graph, set(nodes))
        lengths = {(edge.a, edge.b): edge.length_m for edge in graph.edges}
        routes = Graph(graph)
        for edge in declared:
            assert lengths[edge.a, edge.b] == meters
            assert routes.distance_meters(edge.a, edge.b) == meters
        assert all(200 <= length <= 2000 for pair, length in lengths.items() if pair not in pairs)
        connecting = nx.number_connected_components(nx.Graph(pairs)) + count - len({n for pair in pairs for n in pair}) - 1
        assert len(pairs) + connecting <= len(graph.edges) <= len(pairs) + connecting + (count - 1) // 2
        assert graph == generate_map(list(reversed(places)), _rstate(seed), tuple(reversed(declared)))


def test_declared_distance_shorter_than_every_random_edge_keeps_all_extra_edges():
    """声明值小于任何随机边时没有额外边能绕过它，抽到的额外边全部保留。"""
    nodes = [f"place-{index:02}" for index in range(9)]
    places = _places(nodes)
    for seed in range(20):
        graph = generate_map(places, _rstate(seed), _declared((nodes[0], nodes[1], 1)))
        assert len(graph.edges) == 1 + (len(nodes) - 2) + (len(nodes) - 1) // 2
        assert Graph(graph).distance_meters(nodes[0], nodes[1]) == 1


@pytest.mark.parametrize("nodes,rows", [
    (["a", "b"], [("a", "b", 70000)]),
    (["a", "b", "c"], [("a", "b", 500), ("a", "c", 600), ("b", "c", 700)]),
])
def test_candidates_run_out_when_every_pair_is_declared(nodes, rows):
    """全部地点对都声明时没有候选可抽，有几条取几条，地图就是声明的边。"""
    places = _places(nodes)
    for seed in range(10):
        graph = generate_map(places, _rstate(seed), _declared(*rows))
        assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == rows


def test_three_places_with_two_declared_pairs_take_the_single_remaining_candidate():
    """三个地点声明两对：随机树的边都不再需要，唯一的候选在不绕过声明时保留。"""
    places = _places(["a", "b", "c"])
    for seed in range(40):
        graph = generate_map(places, _rstate(seed), _declared(("a", "b", 300), ("b", "c", 300)))
        edges = {(edge.a, edge.b): edge.length_m for edge in graph.edges}
        assert edges["a", "b"] == edges["b", "c"] == 300
        assert set(edges) <= {("a", "b"), ("a", "c"), ("b", "c")}
        if ("a", "c") in edges:
            assert 200 <= edges["a", "c"] <= 2000
        assert Graph(graph).distance_meters("a", "b") == Graph(graph).distance_meters("b", "c") == 300
