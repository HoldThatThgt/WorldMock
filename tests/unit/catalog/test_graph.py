"""@brief 手算完整图上的距离、全路径决胜与场景未选中间地点。"""

from dataclasses import replace

import pytest

from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.catalog.models import MapEdge, MapGraph


def hand_graph(edges, nodes=None):
    """@brief 使用真实发布模型构造可手算的无向图。"""
    ids = nodes or sorted({node for a, b, _ in edges for node in (a, b)})
    return Graph(MapGraph(nodes=tuple(ids),
                          edges=tuple(MapEdge(a=a, b=b, length_m=length) for a, b, length in edges)))


@pytest.mark.parametrize("reverse", [False, True])
def test_full_path_lexicographic_tie_ignores_discovery_and_edge_order(reverse):
    """@brief 较小后缀晚被发现时仍替换整条等长路径，边顺序不能决定结果。"""
    edges = [("s", "a", 200), ("a", "b", 200), ("b", "z", 100),
             ("a", "x", 100), ("x", "z", 200), ("s", "c", 100), ("c", "z", 400)]
    graph = hand_graph(list(reversed(edges)) if reverse else edges)
    assert graph.distance_meters("s", "z") == 500
    assert graph.path("s", "z") == ("s", "a", "b", "z")
    assert graph.shortest_paths("s")["z"] == (500, ("s", "a", "b", "z"))


def test_hand_calculated_paths_and_same_place_zero():
    graph = hand_graph([("a", "b", 301), ("b", "c", 402), ("a", "c", 1000)])
    assert graph.path("a", "c") == ("a", "b", "c")
    assert graph.distance_meters("a", "c") == 703
    assert graph.path("c", "a") == ("c", "b", "a")
    assert graph.distance_meters("a", "a") == 0
    assert graph.path("a", "a") == ("a",)


@pytest.mark.parametrize("reverse", [False, True])
def test_full_path_comparison_prefers_smaller_first_hop_despite_larger_suffix(reverse):
    """@brief 较小首跳后的前驱较大，完整路径仍应优先于仅比较终点前驱的结果。"""
    edges = [("s", "a", 200), ("a", "z", 200), ("z", "t", 200),
             ("s", "b", 200), ("b", "c", 200), ("c", "t", 200)]
    graph = hand_graph(list(reversed(edges)) if reverse else edges)
    assert graph.distance_meters("s", "t") == 600
    assert graph.path("s", "t") == ("s", "a", "z", "t")


def test_catalog_routes_through_an_unselected_intermediate_location(loaded_catalog):
    """@brief 家和公司之间必须沿完整环境图经过公园。"""
    assert loaded_catalog.graph.path("home", "office") == ("home", "park", "office")
    assert loaded_catalog.shortest_m("home", "office") == 1957
    paths = loaded_catalog.graph.shortest_paths("home")
    with pytest.raises(TypeError):
        paths["office"] = (1, ("home", "office"))


def test_catalog_graph_does_not_recreate_published_edges(loaded_catalog):
    shifted = loaded_catalog.map_data.model_copy()
    catalog = replace(loaded_catalog, map_data=shifted)
    assert catalog.graph.path("home", "office") == loaded_catalog.graph.path("home", "office")


def test_published_graph_attributes_cannot_change_shared_route_distances():
    """@brief 共享图的边长度和节点属性不能跨世界被改写，结构冻结也必须覆盖属性。"""
    graph = hand_graph([("a", "b", 301)])
    with pytest.raises(TypeError):
        graph._network["a"]["b"]["length_m"] = 1
    with pytest.raises(TypeError):
        graph._network.nodes["a"]["name"] = "changed"
    with pytest.raises(TypeError):
        graph._network.graph["name"] = "changed"
    assert graph.distance_meters("a", "b") == 301
