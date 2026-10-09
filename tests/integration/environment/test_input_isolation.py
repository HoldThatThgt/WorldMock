"""@brief 完整环境副本修改不影响源文件字节或真实文件身份。"""

import json
import shutil

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.environment import load_environment


def test_catalog_edits_leave_source_bytes_and_inodes_unchanged(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    shutil.copytree(ENVIRONMENT_ROOT, source)
    shutil.copytree(source, copy)
    before = {p.relative_to(source): (p.read_bytes(), p.stat().st_ino)
              for p in source.rglob("*") if p.is_file()}
    path = copy / "catalog/merchant-catalog.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["price_fen"] += 123
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")
    refresh_manifest_identity(copy / "catalog")
    original = load_environment(source, metadata_loader=fixture_metadata)
    isolated = load_environment(copy, metadata_loader=fixture_metadata)
    identity = rows[0]["goods_id"]
    assert isolated.catalog.goods[identity].price_fen == original.catalog.goods[identity].price_fen + 123
    for relative, (content, inode) in before.items():
        assert (source / relative).read_bytes() == content
        assert (source / relative).stat().st_ino == inode
        assert (copy / relative).stat().st_ino != inode
