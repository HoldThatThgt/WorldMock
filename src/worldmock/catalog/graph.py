"""@brief 在完整发布图上按距离与完整路径字典序查询确定性最短路。"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

import networkx as nx

from worldmock.catalog.errors import RegistrationError

if TYPE_CHECKING:
    from worldmock.catalog.models import MapGraph


@dataclass(frozen=True, init=False)
class Graph:
    """@brief 完整环境图的只读查询对象，场景选择不会裁剪中间节点。"""

    #: 成熟图容器保存已登记的无向连接与正整数长度。
    _network: nx.Graph

    def __init__(self, published: MapGraph):
        """@brief 从发布模型建立一次图存储，不重新生成边。"""
        network = nx.Graph()
        network.add_nodes_from(published.nodes)
        network.add_weighted_edges_from(((edge.a, edge.b, edge.length_m) for edge in published.edges),
                                       weight="length_m")
        network.graph = MappingProxyType(network.graph)
        network._node = MappingProxyType({node: MappingProxyType(data) for node, data in network._node.items()})
        network._adj = MappingProxyType({
            node: MappingProxyType({neighbor: MappingProxyType(data) for neighbor, data in adjacent.items()})
            for node, adjacent in network._adj.items()
        })
        object.__setattr__(self, "_network", nx.freeze(network))

    def shortest_paths(self, origin: str) -> Mapping[str, tuple[int, tuple[str, ...]]]:
        """@brief Dijkstra 的堆项与最优键均为距离及完整路径，等长时仅更小的完整元组可替换。"""
        if origin not in self._network:
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        best = {origin: (0, (origin,))}
        frontier = [best[origin]]
        while frontier:
            meters, path = heapq.heappop(frontier)
            here = path[-1]
            if (meters, path) != best[here]:
                continue
            for target, edge in self._network[here].items():
                candidate = (meters + edge["length_m"], (*path, target))
                if target not in best or candidate < best[target]:
                    best[target] = candidate
                    heapq.heappush(frontier, candidate)
        return MappingProxyType({node: best[node] for node in sorted(best)})

    def distance_meters(self, origin: str, destination: str) -> int:
        """@brief 返回完整图上的最短距离，同地点自然为零。"""
        return self._destination(origin, destination)[0]

    def path(self, origin: str, destination: str) -> tuple[str, ...]:
        """@brief 返回等长路径中完整地点标识元组字典序最小的一条。"""
        return self._destination(origin, destination)[1]

    def _destination(self, origin: str, destination: str) -> tuple[int, tuple[str, ...]]:
        """@brief 地图已经登记为连通图，未知地点属于引用错误。"""
        paths = self.shortest_paths(origin)
        if destination not in paths:
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        return paths[destination]
