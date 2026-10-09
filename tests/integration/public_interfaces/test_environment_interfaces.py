"""真实目录、交换与素材恢复公开入口的当前行为。"""


import pytest
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import read_catalog_file
from worldmock.errors import IntegrityError
from worldmock.footprints import checkpoint
from worldmock.footprints.assets import AssetStore
from pathlib import Path


@pytest.mark.parametrize("endpoint", ["origin", "destination"])
def test_catalog_graph_rejects_unknown_query_endpoint(loaded_catalog, endpoint):
    """@brief 完整图查询不能用未登记端点产生假路线。"""
    with pytest.raises(RegistrationError) as caught:
        if endpoint == "origin":
            loaded_catalog.graph.shortest_paths("unknown-place")
        else:
            loaded_catalog.graph.path("home", "unknown-place")
    assert caught.value.code == "ENVIRONMENT_REF_UNKNOWN"
    assert loaded_catalog.graph.path("home", "office") == ("home", "park", "office")


def test_catalog_file_read_reports_real_byte_read_failure(catalog_dir, monkeypatch):
    """@brief 安全路径已核对但文件打开失败时，保留故障及稳定登记码。"""
    path = catalog_dir / "merchant-catalog.jsonl"
    original = Path.read_bytes
    failure = PermissionError("Catalog file read denied")

    def read(selected):
        if selected == path:
            raise failure
        return original(selected)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RegistrationError) as caught:
        read_catalog_file(catalog_dir, "merchant-catalog.jsonl")
    assert caught.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert caught.value.__cause__ is failure
    assert original(path)
