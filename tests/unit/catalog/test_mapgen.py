"""@brief 真实地图生成、逐字节发布校验和运行期加载边界。"""

from pathlib import Path

import pytest

from tests.catalog.conftest import read_payload, write_payload
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.mapgen import generate_map, map_bytes, publish_map, require_regenerated_map


def test_map_generation_is_deterministic_and_seeded():
    """@brief 排序输入不改字节，同种子一致、不同种子不同。"""
    nodes = ["g", "f", "e", "d", "c", "b", "a"]
    first = generate_map(nodes, 7)
    assert map_bytes(first) == map_bytes(generate_map(list(reversed(nodes)), 7))
    assert map_bytes(first) != map_bytes(generate_map(nodes, 8))
    assert first.nodes == tuple("abcdefg")
    assert len(first.edges) == 9
    assert [(edge.a, edge.b) for edge in first.edges] == sorted((edge.a, edge.b) for edge in first.edges)
    assert map_bytes(first).endswith(b"\n") and not map_bytes(first).endswith(b"\n\n")
    assert first.schema_version == first.generator_rule_version == 1


@pytest.mark.parametrize("count", [2, 3, 7, 20])
def test_map_edge_counts_length_bounds_and_connectivity(count):
    """@brief 树加额外边的计数、正长度闭区间和连通性均可观察。"""
    from worldmock.catalog.admission import validate_map
    nodes = [f"place-{index:02}" for index in range(count)]
    for seed in range(20):
        graph = generate_map(nodes, seed)
        assert len(graph.edges) == count - 1 + (count - 1) // 2
        assert len({(edge.a, edge.b) for edge in graph.edges}) == len(graph.edges)
        assert all(edge.a < edge.b and 200 <= edge.length_m <= 2000 for edge in graph.edges)
        validate_map(graph, set(nodes))
    if count == 3:
        assert {(edge.a, edge.b) for edge in graph.edges} == {
            (nodes[0], nodes[1]), (nodes[0], nodes[2]), (nodes[1], nodes[2])}


def test_map_matches_frozen_artificial_seed(catalog_dir):
    """@brief 发布人工图是冻结算法在其节点和种子上的逐字节产物。"""
    raw = (catalog_dir / "map.json").read_bytes()
    catalog = load_catalog(catalog_dir)
    assert raw == map_bytes(generate_map(tuple(catalog.locations), 7))
    require_regenerated_map(catalog.map_data, raw)


def test_map_check_rejects_changed_bytes_without_rewriting(catalog_dir, tmp_path):
    """@brief 检查模式拒绝改变的边长并保存目标字节。"""
    graph = generate_map(["a", "b", "c"], 7)
    target = tmp_path / "map.json"
    publish_map(graph, target)
    publish_map(graph, target, check=True)
    changed = map_bytes(graph).replace(b'"length_m":', b'"length_m":1', 1)
    target.write_bytes(changed)
    with pytest.raises(RegistrationError, match="MAP_NOT_REGENERATED"):
        publish_map(graph, target, check=True)
    assert target.read_bytes() == changed
    with pytest.raises(RegistrationError, match="MAP_NOT_REGENERATED"):
        require_regenerated_map(graph, changed)


def test_runtime_catalog_does_not_regenerate_map(catalog_dir, monkeypatch):
    """@brief 已重新发布身份的结构合法图可运行加载，发布校验仍拒绝偏离生成器。"""
    graph = read_payload(catalog_dir, "map.json")
    graph["edges"][0]["length_m"] += 1
    write_payload(catalog_dir, "map.json", graph)
    monkeypatch.setattr("worldmock.catalog.mapgen.generate_map", lambda *args: pytest.fail("Runtime regenerated map"))
    catalog = load_catalog(catalog_dir)
    assert catalog.map_data.edges[0].length_m == graph["edges"][0]["length_m"]


@pytest.mark.parametrize("nodes,seed,code", [
    (["a"], 7, "MAP_TOO_SMALL"), (["a", "a"], 7, "ENVIRONMENT_ID_DUPLICATE"),
    (["a", "b"], True, "ENVIRONMENT_NUMBER_INVALID"),
])
def test_map_generator_rejects_invalid_inputs(nodes, seed, code):
    """@brief 生成入口拒绝过小地点集、重复身份和布尔种子。"""
    with pytest.raises(RegistrationError, match=code):
        generate_map(nodes, seed)


def test_sorted_nodes_independent_tree_stream_and_length_assignment():
    """@brief 三节点种子7的排序编号和独立随机流得到固定边及长度。"""
    graph = generate_map(["c", "a", "b"], 7)
    assert [(edge.a, edge.b, edge.length_m) for edge in graph.edges] == [
        ("a", "b", 508), ("a", "c", 1008), ("b", "c", 1533)]
    assert map_bytes(graph) == (
        b'{"edges":[{"a":"a","b":"b","length_m":508},{"a":"a","b":"c","length_m":1008},'
        b'{"a":"b","b":"c","length_m":1533}],"generator_rule_version":1,"nodes":["a","b","c"],'
        b'"schema_version":1,"seed":7}\n')
