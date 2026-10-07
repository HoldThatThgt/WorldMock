"""@brief 完整目录字节身份和真实原子写失败的行为证明。"""

import shutil

import pytest

from tests.catalog.conftest import read_payload, write_payload
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import compute_catalog_identity, refresh_manifest_identity
from worldmock.catalog.loader import load_catalog
from worldmock.footprints import assets


def test_catalog_identity_is_bound_to_relative_files(catalog_dir, tmp_path):
    """@brief 换目录和机器根不改相对路径与真实字节绑定的身份。"""
    relocated = tmp_path / "relocated" / "catalog"
    shutil.copytree(catalog_dir, relocated)
    assert compute_catalog_identity(catalog_dir) == compute_catalog_identity(relocated)
    assert load_catalog(catalog_dir).manifest == load_catalog(relocated).manifest
    payload = read_payload(relocated, "goods.json")
    payload["goods"][0]["price_fen"] += 1
    write_payload(relocated, "goods.json", payload)
    assert compute_catalog_identity(catalog_dir) != compute_catalog_identity(relocated)


def test_catalog_identity_publication_is_atomic(catalog_dir, monkeypatch):
    """@brief 真实atomic_write替换失败时，旧清单字节完整保留。"""
    original = (catalog_dir / "manifest.json").read_bytes()
    payload = read_payload(catalog_dir, "goods.json")
    payload["goods"][0]["price_fen"] += 1
    write_payload(catalog_dir, "goods.json", payload, refresh=False)

    def reject_replace(source, target):
        raise OSError("Atomic replacement rejected for test")

    monkeypatch.setattr(assets.os, "replace", reject_replace)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        refresh_manifest_identity(catalog_dir)
    assert (catalog_dir / "manifest.json").read_bytes() == original


def test_catalog_identity_rejects_symbolic_files(catalog_dir, tmp_path):
    """@brief 目录文件符号链接不能把目录身份指向外部机器内容。"""
    target = tmp_path / "external-goods.json"
    shutil.copyfile(catalog_dir / "goods.json", target)
    (catalog_dir / "goods.json").unlink()
    (catalog_dir / "goods.json").symlink_to(target)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_catalog(catalog_dir)
