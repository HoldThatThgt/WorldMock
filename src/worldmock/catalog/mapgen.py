"""@brief 使用锁定 NetworkX 和独立随机流生成确定性发布图。"""

import random
from itertools import combinations

import networkx as nx

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.models import MapEdge, MapGraph
from worldmock.exchange.constants import MAP_EDGE_LENGTH_M, MAP_EXTRA_EDGES, MAP_MIN_NODES
from worldmock.footprints.models import canonical_json


def generate_edges(location_ids: tuple[str, ...] | list[str], seed: int) -> tuple[MapEdge, ...]:
    """@brief 按冻结的树流、额外边流和排序赋长顺序生成边。"""
    nodes = tuple(sorted(location_ids))
    if len(nodes) < MAP_MIN_NODES:
        raise RegistrationError("MAP_TOO_SMALL")
    if len(nodes) != len(set(nodes)):
        raise RegistrationError("ENVIRONMENT_ID_DUPLICATE")
    if type(seed) is not int:
        raise RegistrationError("ENVIRONMENT_NUMBER_INVALID")
    tree = nx.random_labeled_tree(len(nodes), seed=seed)
    pairs = {tuple(sorted((nodes[a], nodes[b]))) for a, b in tree.edges}
    candidates = sorted(set(combinations(nodes, 2)) - pairs)
    rng = random.Random(seed)
    pairs.update(rng.sample(candidates, MAP_EXTRA_EDGES(len(nodes))))
    return tuple(MapEdge(a=a, b=b, length_m=rng.randint(*MAP_EDGE_LENGTH_M)) for a, b in sorted(pairs))


def generate_map(location_ids: tuple[str, ...] | list[str], seed: int) -> MapGraph:
    """@brief 生成可由目录加载器直接校验的地图模型。"""
    return MapGraph(nodes=tuple(sorted(location_ids)), edges=generate_edges(location_ids, seed))


def map_bytes(graph: MapGraph) -> bytes:
    """@brief 使用足迹公共编码器生成恰好一个末尾 LF 的地图字节。"""
    return canonical_json(graph.model_dump(mode="json")) + b"\n"
