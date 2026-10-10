"""发布身份绑定相对路径与原始字节，原子替换失败保留旧清单。"""

import shutil

import pytest

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import compute_catalog_identity, refresh_manifest_identity
from worldmock.catalog.loader import load_catalog
from worldmock.footprints import assets


def test_identity_is_independent_of_machine_directory(catalog_dir, tmp_path):
    relocated = tmp_path / "relocated"
    shutil.copytree(catalog_dir, relocated)
    assert compute_catalog_identity(catalog_dir) == compute_catalog_identity(relocated)
    assert load_catalog(catalog_dir, 7).manifest == load_catalog(relocated, 7).manifest
    path = relocated / "merchant-catalog.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"price_fen":', b'"price_fen":1', 1))
    assert compute_catalog_identity(catalog_dir) != compute_catalog_identity(relocated)


def test_atomic_identity_publication_preserves_original_on_failure(catalog_dir, monkeypatch):
    original = (catalog_dir / "manifest.json").read_bytes()
    path = catalog_dir / "merchant-catalog.jsonl"
    path.write_bytes(path.read_bytes() + b" ")

    def reject_replace(source, target):
        raise OSError("Atomic replacement rejected for test")

    monkeypatch.setattr(assets.os, "replace", reject_replace)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        refresh_manifest_identity(catalog_dir)
    assert (catalog_dir / "manifest.json").read_bytes() == original
