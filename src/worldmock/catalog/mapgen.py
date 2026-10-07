"""@brief 使用锁定 NetworkX 和独立随机流生成确定性发布图。"""

import random
from itertools import combinations
from pathlib import Path

import networkx as nx

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.models import MapEdge, MapGraph
from worldmock.exchange.constants import MAP_EDGE_LENGTH_M, MAP_EXTRA_EDGES, MAP_GENERATOR_RULE_VERSION, MAP_MIN_NODES
from worldmock.footprints.assets import atomic_write
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
    return MapGraph(schema_version=1, seed=seed, generator_rule_version=MAP_GENERATOR_RULE_VERSION,
                    nodes=tuple(sorted(location_ids)), edges=generate_edges(location_ids, seed))


def map_bytes(graph: MapGraph) -> bytes:
    """@brief 使用足迹公共编码器生成恰好一个末尾 LF 的地图字节。"""
    return canonical_json(graph.model_dump(mode="json")) + b"\n"


def publish_map(graph: MapGraph, output: Path | str, *, check: bool = False) -> None:
    """@brief 原子发布地图；检查模式只逐字节比较，绝不改写。"""
    expected, target = map_bytes(graph), Path(output)
    if check:
        if not target.is_file() or target.read_bytes() != expected:
            raise RegistrationError("MAP_NOT_REGENERATED")
    else:
        atomic_write(target, expected)


def require_regenerated_map(graph: MapGraph, content: bytes) -> None:
    """@brief 发布校验按地点和种子重生成并逐字节核对地图。"""
    if content != map_bytes(generate_map(graph.nodes, graph.seed)):
        raise RegistrationError("MAP_NOT_REGENERATED")
