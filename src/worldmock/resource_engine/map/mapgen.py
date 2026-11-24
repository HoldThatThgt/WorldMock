"""@brief 使用锁定 NetworkX 和分城独立随机流，在声明的距离之上生成确定性发布图。"""

from collections import defaultdict
from collections.abc import Sequence
from itertools import combinations, groupby
import logging
import random

import networkx as nx

from worldmock.exchange_engine.definitions.constants import MAP_EDGE_LENGTH_M, MAP_EXTRA_EDGES, MAP_MIN_NODES
from worldmock.foundation.randomness import random_stream
from worldmock.foundation.serialization import canonical_json
from worldmock.resource_engine.catalog.models import Location, MapEdge, MapGraph
from worldmock.resource_engine.registration.errors import RegistrationError

_LOGGER = logging.getLogger(__name__)


def generate_edges(locations: Sequence[Location], random_state: str,
                   declared_edges: Sequence[MapEdge] = ()) -> tuple[MapEdge, ...]:
    """@brief 声明边原样入图，再按 city_id 分城独立生成不绕过同城声明的随机边。"""
    nodes = tuple(sorted(location.location_id for location in locations))
    if len(nodes) != len(set(nodes)):
        _LOGGER.error("Duplicate location_id in map generation")
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if len(nodes) < MAP_MIN_NODES:
        _LOGGER.error("Insufficient locations for map generation: %d", len(nodes))
        raise RegistrationError("MAP_TOO_SMALL")
    if type(random_state) is not str or not random_state:
        _LOGGER.error("Invalid random_state for map generation")
        raise RegistrationError("ENVIRONMENT_KIND_INVALID", "Map random_state must be a non-empty string.")
    city_by_node = {location.location_id: location.city_id for location in locations}
    if any(edge.a >= edge.b or edge.a not in city_by_node or edge.b not in city_by_node for edge in declared_edges):
        _LOGGER.error("Invalid declared edge in map generation")
        raise RegistrationError("MAP_EDGE_INVALID")
    random_edges = []
    for city_id, city_nodes in _grouped_locations(locations):
        if len(city_nodes) < MAP_MIN_NODES:
            continue
        city_declared = tuple(sorted(
            (edge for edge in declared_edges
             if city_by_node[edge.a] == city_id and city_by_node[edge.b] == city_id),
            key=lambda edge: (edge.a, edge.b)))
        random_edges.extend(_city_random_edges(city_id, city_nodes, random_state, city_declared))
    return tuple(sorted((*declared_edges, *random_edges), key=lambda edge: (edge.a, edge.b)))


def _grouped_locations(locations: Sequence[Location]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """@brief 按 city_id 升序分组各地点身份，组内按 location_id 升序。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for location in locations:
        grouped[location.city_id].append(location.location_id)
    return tuple((city_id, tuple(sorted(grouped[city_id]))) for city_id in sorted(grouped))


def _city_random_edges(city_id: str, nodes: tuple[str, ...], random_state: str,
                       declared: tuple[MapEdge, ...]) -> list[MapEdge]:
    """@brief 为单个多节点城市生成随机生成树边与不绕过城内声明边的额外边。"""
    seed = random_stream(random_state, f"map:{city_id}").getrandbits(64)
    free_declared = tuple(edge for edge in declared if edge.travel_method_ids is None)
    declared_pairs = {(edge.a, edge.b) for edge in declared}
    tree = nx.random_labeled_tree(len(nodes), seed=seed)
    tree_pairs = sorted(tuple(sorted((nodes[a], nodes[b]))) for a, b in tree.edges)
    connecting = _connecting_pairs(nodes, free_declared, declared_pairs, tree_pairs)
    excluded = set(tree_pairs) | declared_pairs
    candidates = sorted(set(combinations(nodes, 2)) - excluded)
    rng = random.Random(seed)
    extra = set(rng.sample(candidates, min(MAP_EXTRA_EDGES(len(nodes)), len(candidates))))
    lengths = {pair: rng.randint(*MAP_EDGE_LENGTH_M) for pair in sorted(connecting | extra)}
    kept = _extra_pairs_kept(declared, {pair: lengths[pair] for pair in connecting},
                             {pair: lengths[pair] for pair in sorted(extra)})
    return [MapEdge(a=a, b=b, length_m=lengths[a, b]) for a, b in connecting | kept]


def _connecting_pairs(nodes: tuple[str, ...], free_declared: tuple[MapEdge, ...],
                      declared_pairs: set[tuple[str, str]],
                      tree_pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """@brief 随机树的边按端点升序逐条看，跳过已有声明对并保留加入时两端还不连通的那些。"""
    parts = nx.utils.UnionFind(nodes)
    for edge in free_declared:
        parts.union(edge.a, edge.b)
    kept = set()
    for a, b in tree_pairs:
        if (a, b) not in declared_pairs and parts[a] != parts[b]:
            parts.union(a, b)
            kept.add((a, b))
    return kept


def _extra_pairs_kept(declared: tuple[MapEdge, ...], connecting: dict[tuple[str, str], int],
                      extra: dict[tuple[str, str], int]) -> set[tuple[str, str]]:
    """@brief 额外边按端点升序逐条加入，会让任何声明的距离出现更短路线的丢掉且不补。"""
    network = nx.Graph()
    network.add_weighted_edges_from(((edge.a, edge.b, edge.length_m) for edge in declared), weight="length_m")
    network.add_weighted_edges_from(((a, b, meters) for (a, b), meters in connecting.items()), weight="length_m")
    kept = set()
    for (a, b), meters in extra.items():
        network.add_edge(a, b, length_m=meters)
        if _bypasses_declared(network, declared):
            network.remove_edge(a, b)
        else:
            kept.add((a, b))
    return kept


def _bypasses_declared(network: nx.Graph, declared: tuple[MapEdge, ...]) -> bool:
    """@brief 任何一条声明边两端的最短距离严格小于声明值，声明的距离就被绕过了。"""
    for origin, edges in groupby(declared, key=lambda edge: edge.a):
        meters = nx.single_source_dijkstra_path_length(network, origin, weight="length_m")
        if any(meters[edge.b] < edge.length_m for edge in edges):
            return True
    return False


def generate_map(locations: Sequence[Location], random_state: str,
                 declared_edges: Sequence[MapEdge] = ()) -> MapGraph:
    """@brief 生成可由目录加载器直接校验的分城地图模型。"""
    edges = generate_edges(locations, random_state, declared_edges)
    nodes = tuple(sorted(location.location_id for location in locations))
    return MapGraph(nodes=nodes, edges=edges)


def map_bytes(graph: MapGraph) -> bytes:
    """@brief 使用足迹公共编码器生成恰好一个末尾 LF 的地图字节，省略为空的可选边字段。"""
    return canonical_json(graph.model_dump(mode="json", exclude_none=True)) + b"\n"
