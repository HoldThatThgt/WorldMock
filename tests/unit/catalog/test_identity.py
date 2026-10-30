"""发布身份绑定相对路径与在场文件的原始字节，原子替换失败保留旧清单。"""

import hashlib
import json
import shutil

import pytest

from tests.life.environment_inputs import walking_only
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.identity import (
    OPTIONAL_CATALOG_FILES,
    REQUIRED_CATALOG_FILES,
    compute_catalog_identity,
    refresh_manifest_identity,
)
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.foundation import atomic_write as assets
from worldmock.foundation.serialization import canonical_json

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'


def _manifest(directory):
    return json.loads((directory / "manifest.json").read_bytes())


def _framed_digest(directory, names):
    """按合同逐帧重算：路径、NUL、八字节大端长度、内容，最后是去自引用的清单帧。"""
    digest, files = hashlib.sha256(), []
    for name in sorted(names):
        content = (directory / name).read_bytes()
        files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
        digest.update(name.encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    payload = {**_manifest(directory), "files": files}
    payload.pop("catalog_content_sha256")
    encoded = canonical_json(payload)
    digest.update(b"manifest.json\0" + len(encoded).to_bytes(8, "big") + encoded)
    return files, digest.hexdigest()


def test_identity_is_independent_of_machine_directory(catalog_dir, tmp_path):
    relocated = tmp_path / "relocated"
    shutil.copytree(catalog_dir, relocated)
    assert compute_catalog_identity(catalog_dir) == compute_catalog_identity(relocated)
    assert load_catalog(catalog_dir, 7).manifest == load_catalog(relocated, 7).manifest
    path = relocated / "merchant-catalog.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"price_fen":', b'"price_fen":1', 1))
    assert compute_catalog_identity(catalog_dir) != compute_catalog_identity(relocated)


def test_identity_frames_exactly_the_present_catalog_files(catalog_dir):
    """夹具是四个必需文件加出行方式共五帧；没有可选文件时只有四帧加清单帧；放入一个可选文件后多一帧和一项文件表记录。"""
    present = {name for name in OPTIONAL_CATALOG_FILES if (catalog_dir / name).exists()}
    assert (catalog_dir / "manifest.json").exists() and present == {"travel-method.jsonl"}
    files, digest = _framed_digest(catalog_dir, REQUIRED_CATALOG_FILES | present)
    identity = compute_catalog_identity(catalog_dir)
    assert len(files) == 5 and [entry.model_dump(mode="json") for entry in identity.files] == files
    assert identity.catalog_content_sha256 == digest == _manifest(catalog_dir)["catalog_content_sha256"]
    walking_only(catalog_dir.parent)
    files, digest = _framed_digest(catalog_dir, REQUIRED_CATALOG_FILES)
    identity = compute_catalog_identity(catalog_dir)
    assert len(files) == 4 and [entry.model_dump(mode="json") for entry in identity.files] == files
    assert identity.catalog_content_sha256 == digest == _manifest(catalog_dir)["catalog_content_sha256"]
    (catalog_dir / "map.jsonl").write_bytes(MAP_ROW)
    files, digest = _framed_digest(catalog_dir, REQUIRED_CATALOG_FILES | {"map.jsonl"})
    identity = compute_catalog_identity(catalog_dir)
    assert [entry.path for entry in identity.files] == [
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "map.jsonl", "merchant-catalog.jsonl"]
    assert [entry.model_dump(mode="json") for entry in identity.files] == files
    assert identity.catalog_content_sha256 == digest


@pytest.mark.parametrize("name", sorted(OPTIONAL_CATALOG_FILES))
def test_absent_and_empty_optional_file_load_the_same_world_under_different_identity(catalog_dir, tmp_path, name):
    """不放与放一个零字节文件：文件表和摘要不同，登记出的对象、回调、出行方式和地图相同。"""
    walking_only(catalog_dir.parent)
    with_empty = tmp_path / "with-empty"
    shutil.copytree(catalog_dir, with_empty)
    (with_empty / name).write_bytes(b"")
    refreshed = refresh_manifest_identity(with_empty)
    absent, empty = load_catalog(catalog_dir, 7), load_catalog(with_empty, 7)
    record = next(entry for entry in refreshed.files if entry.path == name)
    assert (record.size_bytes, record.sha256) == (0, EMPTY_SHA256)
    assert name not in [entry.path for entry in absent.manifest.files]
    assert len(empty.manifest.files) == len(absent.manifest.files) + 1
    assert empty.manifest.catalog_content_sha256 != absent.manifest.catalog_content_sha256
    for field in ("apps", "locations", "goods", "contents", "callback_states", "callback_rules", "travel_methods",
                  "apps_by_travel_method"):
        assert dict(getattr(empty, field)) == dict(getattr(absent, field))
    assert empty.callback_states == {} and empty.callback_rules == {} and empty.travel_methods == {}
    assert dict(empty.apps_by_travel_method) == {"walking": ("amap",)}
    assert (empty.map_data, empty.map_sha256) == (absent.map_data, absent.map_sha256)


def test_refresh_registers_new_optional_files_and_removes_deleted_ones(catalog_dir):
    """刷新只登记在场文件：新放入的进文件表，删掉的移出，不写占位记录，四个身份字段原样保留。"""
    before = _manifest(catalog_dir)
    (catalog_dir / "map.jsonl").write_bytes(MAP_ROW)
    (catalog_dir / "callbacks.jsonl").write_bytes(b"")
    refresh_manifest_identity(catalog_dir)
    registered = _manifest(catalog_dir)
    assert [entry["path"] for entry in registered["files"]] == [
        "app-catalog.jsonl", "callbacks.jsonl", "content-catalog.jsonl", "location.jsonl", "map.jsonl",
        "merchant-catalog.jsonl", "travel-method.jsonl"]
    assert (catalog_dir / "manifest.json").read_bytes() == canonical_json(registered) + b"\n"
    assert load_catalog(catalog_dir, 7).shortest_m("home", "office") == 12600
    (catalog_dir / "map.jsonl").unlink()
    (catalog_dir / "callbacks.jsonl").unlink()
    refresh_manifest_identity(catalog_dir)
    assert _manifest(catalog_dir) == before
    assert {key: before[key] for key in ("format_version", "rules_version", "environment_id",
                                         "environment_version")} == {
        "format_version": 6, "rules_version": 6, "environment_id": "artificial-test", "environment_version": "4"}


def test_refresh_without_a_required_file_keeps_the_old_manifest(catalog_dir):
    original = (catalog_dir / "manifest.json").read_bytes()
    (catalog_dir / "location.jsonl").unlink()
    with pytest.raises(RegistrationError) as failure:
        refresh_manifest_identity(catalog_dir)
    assert failure.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert (catalog_dir / "manifest.json").read_bytes() == original


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
