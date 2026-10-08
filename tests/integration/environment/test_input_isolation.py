"""@brief 测试输入副本的修改不能影响原环境。"""

import shutil

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT


def test_purchase_world_edits_leave_formal_input_bytes_and_inodes_unchanged(tmp_path):
    """@brief 新目录构造器实际改商品与地点并登记后，源环境字节和文件身份保持不变。"""
    from tests.catalog.conftest import read_payload, write_payload
    from worldmock.life.world_setup import load_runtime_scenario

    source_root, world = tmp_path / "source", tmp_path / "world"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, source_root)
    shutil.copytree(source_root, world)
    inputs = [path.relative_to(source_root) for path in source_root.rglob("*") if path.is_file()]
    before = {relative: ((source_root / relative).read_bytes(), (source_root / relative).stat().st_ino)
              for relative in inputs}
    catalog = world / "catalog"
    locations = read_payload(catalog, "locations.json")
    locations["locations"][0]["name"] = "隔离地点"
    write_payload(catalog, "locations.json", locations)
    goods = read_payload(catalog, "goods.json")
    goods["goods"][0]["price_fen"] += 123
    write_payload(catalog, "goods.json", goods)
    isolated, _ = load_runtime_scenario(world / "scenarios/single-person.json")
    original, _ = load_runtime_scenario(source_root / "scenarios/single-person.json")
    assert isolated.catalog.locations[locations["locations"][0]["location_id"]].name == "隔离地点"
    goods_id = goods["goods"][0]["goods_id"]
    assert isolated.catalog.goods[goods_id].price_fen == original.catalog.goods[goods_id].price_fen + 123
    for relative, (content, inode) in before.items():
        source_file, copied = source_root / relative, world / relative
        assert source_file.read_bytes() == content
        assert source_file.stat().st_ino == inode
        assert copied.stat().st_ino != inode
        copied.write_bytes(b"isolated edit\n")
        assert source_file.read_bytes() == content
