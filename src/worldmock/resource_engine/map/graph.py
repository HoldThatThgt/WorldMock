"""@brief 在完整发布图与同城自由图上按方式隔离和字典序查询确定性最短路及跨城枢纽。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import heapq
import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping

import networkx as nx

from worldmock.resource_engine.registration.errors import RegistrationError

if TYPE_CHECKING:
    from worldmock.resource_engine.catalog.models import MapEdge, MapGraph

_LOGGER = logging.getLogger(__name__)


def _freeze_nx(nodes: tuple[str, ...], edges: Iterable[MapEdge]) -> nx.Graph:
    """@brief 构建并深度冻结无向加权图，防止运行时改写节点、边或属性。"""
    network = nx.Graph()
    network.add_nodes_from(nodes)
    network.add_weighted_edges_from(((edge.a, edge.b, edge.length_m) for edge in edges), weight="length_m")
    network.graph = MappingProxyType(network.graph)
    network._node = MappingProxyType({node: MappingProxyType(data) for node, data in network._node.items()})
    network._adj = MappingProxyType({
        node: MappingProxyType({neighbor: MappingProxyType(data) for neighbor, data in adjacent.items()})
        for node, adjacent in network._adj.items()
    })
    return nx.freeze(network)


@dataclass(frozen=True, init=False)
class Graph:
    """@brief 环境图的只读查询对象，隔离同城自由街区与限定声明边路由。"""

    #: 完整环境图（含自由边与限定声明边），用于全图连通性与跨城拓扑。
    _network: nx.Graph
    #: 仅含同城自由边（未声明 travel_method_ids）的子图。
    _free_network: nx.Graph
    #: 地点身份到城市身份的只读映射。
    _city_by_node: Mapping[str, str]
    #: 规范无向键 (a, b) 到原始 MapEdge 的只读映射。
    _edges: Mapping[tuple[str, str], MapEdge]

    def __init__(self, map_data: MapGraph, city_by_node: Mapping[str, str] | None = None):
        """@brief 从发布模型与城市归属建立只读图存储，不重新生成边。"""
        if city_by_node is None:
            cities = {node: "" for node in map_data.nodes}
        else:
            missing = [node for node in map_data.nodes if node not in city_by_node]
            if missing:
                _LOGGER.error("Missing city_id for map node: %s", missing[0])
                raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", f"Unknown location_id: {missing[0]}.")
            cities = {node: city_by_node[node] for node in map_data.nodes}
        free_edges = [edge for edge in map_data.edges
                      if edge.travel_method_ids is None and cities[edge.a] == cities[edge.b]]
        edges_map = {(edge.a, edge.b) if edge.a < edge.b else (edge.b, edge.a): edge for edge in map_data.edges}
        object.__setattr__(self, "_network", _freeze_nx(map_data.nodes, map_data.edges))
        object.__setattr__(self, "_free_network", _freeze_nx(map_data.nodes, free_edges))
        object.__setattr__(self, "_city_by_node", MappingProxyType(cities))
        object.__setattr__(self, "_edges", MappingProxyType(edges_map))

    def city_of(self, location_id: str) -> str:
        """@brief 返回已登记地点所属的城市身份。"""
        if location_id not in self._city_by_node:
            _LOGGER.error("Unknown location_id requested for city lookup: %s", location_id)
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", f"Unknown location_id: {location_id}.")
        return self._city_by_node[location_id]

    def declared_edge(self, a: str, b: str) -> MapEdge | None:
        """@brief 按无向规范端点返回图上的边模型，不存在时返回 None。"""
        return self._edges.get((a, b) if a < b else (b, a))

    def gated_edges(self) -> tuple[MapEdge, ...]:
        """@brief 返回所有声明了出行方式限制的边，按 (a, b) 升序。"""
        restricted = (edge for edge in self._edges.values() if edge.travel_method_ids is not None)
        return tuple(sorted(restricted, key=lambda edge: (edge.a, edge.b)))

    def intercity_edges_from(self, location_id: str) -> tuple[tuple[str, MapEdge], ...]:
        """@brief 返回从该地点直达其他城市的跨城声明边，按目的地点身份升序。"""
        origin_city = self.city_of(location_id)
        rows = []
        for other in sorted(self._network[location_id]):
            if self._city_by_node[other] != origin_city:
                edge = self.declared_edge(location_id, other)
                if edge is not None:
                    rows.append((other, edge))
        return tuple(rows)

    def next_hub_toward(self, origin: str, destination: str) -> str | None:
        """@brief 按城市最少跳数 BFS（平局按本城步行距离与地点身份）计算跨城首步枢纽。"""
        origin_city, dest_city = self.city_of(origin), self.city_of(destination)
        if origin_city == dest_city:
            return None
        city_hops = self._city_hops_to(dest_city)
        free_paths = self._dijkstra(self._free_network, origin)
        best: tuple[int, int, str, str] | None = None
        for dep_hub, (walk_m, _) in free_paths.items():
            for remote_hub, _ in self.intercity_edges_from(dep_hub):
                next_city = self._city_by_node[remote_hub]
                if next_city not in city_hops:
                    continue
                candidate = (city_hops[next_city], walk_m, dep_hub, remote_hub)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            return None
        return best[2] if origin != best[2] else best[3]

    def shortest_route(self, origin: str, destination: str, travel_method_id: str | None = None,
                       city_wide: bool = True) -> tuple[int, tuple[str, ...]] | None:
        """@brief 按出行方式隔离条件查询确定性最短路；不可达时返回 None。"""
        if origin not in self._network or destination not in self._network:
            _LOGGER.error("Unknown endpoint in shortest_route: origin=%s destination=%s", origin, destination)
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        if travel_method_id is None and not city_wide:
            return self._dijkstra(self._network, origin, destination).get(destination)
        edge = self.declared_edge(origin, destination)
        direct: tuple[int, tuple[str, ...]] | None = None
        if (edge is not None and edge.travel_method_ids is not None
                and travel_method_id is not None and travel_method_id in edge.travel_method_ids):
            direct = (edge.length_m, (origin, destination))
        free_route: tuple[int, tuple[str, ...]] | None = None
        if city_wide and self._city_by_node[origin] == self._city_by_node[destination]:
            free_route = self._dijkstra(self._free_network, origin, destination).get(destination)
        if direct is None:
            return free_route
        if free_route is None:
            return direct
        return direct if direct[0] < free_route[0] else free_route

    def shortest_paths(self, origin: str) -> Mapping[str, tuple[int, tuple[str, ...]]]:
        """@brief 在完整图上运行 Dijkstra，等长时取完整路径字典序最小的一条。"""
        return self._dijkstra(self._network, origin)

    def free_shortest_paths(self, origin: str) -> Mapping[str, tuple[int, tuple[str, ...]]]:
        """@brief 仅沿同城自由街区边运行 Dijkstra，用于同城一日时限检查。"""
        return self._dijkstra(self._free_network, origin)

    def distance_meters(self, origin: str, destination: str, travel_method_id: str | None = None,
                        city_wide: bool = True) -> int:
        """@brief 返回允许图上的最短距离，同地点自然为零。"""
        return self._destination(origin, destination, travel_method_id, city_wide)[0]

    def path(self, origin: str, destination: str, travel_method_id: str | None = None,
             city_wide: bool = True) -> tuple[str, ...]:
        """@brief 返回等长路径中完整地点标识元组字典序最小的一条。"""
        return self._destination(origin, destination, travel_method_id, city_wide)[1]

    def _destination(self, origin: str, destination: str, travel_method_id: str | None,
                     city_wide: bool) -> tuple[int, tuple[str, ...]]:
        """@brief 路由不可达或引用未知地点时记录日志并拒绝。"""
        route = self.shortest_route(origin, destination, travel_method_id, city_wide)
        if route is None:
            _LOGGER.error("No route from %s to %s (method=%s, city_wide=%s)",
                          origin, destination, travel_method_id, city_wide)
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        return route

    def _city_hops_to(self, target_city: str) -> dict[str, int]:
        """@brief 在城市连通图上从目标城市反向 BFS 计算各城市最少跨城跳数。"""
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._edges.values():
            ca, cb = self._city_by_node[edge.a], self._city_by_node[edge.b]
            if ca != cb:
                adjacency[ca].add(cb)
                adjacency[cb].add(ca)
        dist = {target_city: 0}
        queue = deque([target_city])
        while queue:
            city = queue.popleft()
            for neighbor in sorted(adjacency.get(city, ())):
                if neighbor not in dist:
                    dist[neighbor] = dist[city] + 1
                    queue.append(neighbor)
        return dist

    def _dijkstra(self, network: nx.Graph, origin: str,
                  destination: str | None = None) -> Mapping[str, tuple[int, tuple[str, ...]]]:
        """@brief 确定性 Dijkstra：堆项与最优键均为 (距离, 完整路径元组)。"""
        if origin not in network:
            _LOGGER.error("Unknown origin location_id in Dijkstra: %s", origin)
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN")
        best = {origin: (0, (origin,))}
        frontier = [best[origin]]
        while frontier:
            meters, path = heapq.heappop(frontier)
            here = path[-1]
            if (meters, path) != best[here]:
                continue
            if here == destination:
                return MappingProxyType({destination: best[destination]})
            for target, edge in network[here].items():
                candidate = (meters + edge["length_m"], (*path, target))
                if target not in best or candidate < best[target]:
                    best[target] = candidate
                    heapq.heappush(frontier, candidate)
        return MappingProxyType({node: best[node] for node in sorted(best)})
