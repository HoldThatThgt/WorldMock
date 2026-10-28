"""@brief 使用锁定 NetworkX 和独立随机流，在声明的距离之上生成确定性发布图。"""

import random
from itertools import combinations, groupby

import networkx as nx

from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.models import MapEdge, MapGraph
from worldmock.exchange_engine.definitions.constants import MAP_EDGE_LENGTH_M, MAP_EXTRA_EDGES, MAP_MIN_NODES
from worldmock.foundation.serialization import canonical_json


def generate_edges(location_ids: tuple[str, ...] | list[str], seed: int,
                   declared: tuple[MapEdge, ...] = ()) -> tuple[MapEdge, ...]:
    """@brief 声明边原样入图，再按冻结的树流、额外边流和排序赋长顺序生成不绕过声明的随机边。"""
    nodes = tuple(sorted(location_ids))
    if len(nodes) < MAP_MIN_NODES:
        raise RegistrationError("MAP_TOO_SMALL")
    if len(nodes) != len(set(nodes)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if type(seed) is not int:
        raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
    declared = tuple(sorted(declared, key=lambda edge: (edge.a, edge.b)))
    tree = nx.random_labeled_tree(len(nodes), seed=seed)
    tree_pairs = sorted(tuple(sorted((nodes[a], nodes[b]))) for a, b in tree.edges)
    connecting = _connecting_pairs(nodes, declared, tree_pairs)
    excluded = set(tree_pairs) | {(edge.a, edge.b) for edge in declared}
    candidates = sorted(set(combinations(nodes, 2)) - excluded)
    rng = random.Random(seed)
    extra = set(rng.sample(candidates, min(MAP_EXTRA_EDGES(len(nodes)), len(candidates))))
    lengths = {pair: rng.randint(*MAP_EDGE_LENGTH_M) for pair in sorted(connecting | extra)}
    kept = _extra_pairs_kept(declared, {pair: lengths[pair] for pair in connecting},
                             {pair: lengths[pair] for pair in sorted(extra)})
    random_edges = [MapEdge(a=a, b=b, length_m=lengths[a, b]) for a, b in connecting | kept]
    return tuple(sorted((*declared, *random_edges), key=lambda edge: (edge.a, edge.b)))


def _connecting_pairs(nodes: tuple[str, ...], declared: tuple[MapEdge, ...],
                      tree_pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """@brief 随机树的边按端点升序逐条看，只保留加入时两端还不连通的那些。"""
    parts = nx.utils.UnionFind(nodes)
    for edge in declared:
        parts.union(edge.a, edge.b)
    kept = set()
    for a, b in tree_pairs:
        if parts[a] != parts[b]:
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


def generate_map(location_ids: tuple[str, ...] | list[str], seed: int,
                 declared: tuple[MapEdge, ...] = ()) -> MapGraph:
    """@brief 生成可由目录加载器直接校验的地图模型。"""
    return MapGraph(nodes=tuple(sorted(location_ids)), edges=generate_edges(location_ids, seed, declared))


def map_bytes(graph: MapGraph) -> bytes:
    """@brief 使用足迹公共编码器生成恰好一个末尾 LF 的地图字节。"""
    return canonical_json(graph.model_dump(mode="json")) + b"\n"
