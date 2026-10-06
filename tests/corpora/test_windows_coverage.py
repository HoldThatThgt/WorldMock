"""@brief 跨平台人工语料清单的结构、符号链接、身份与哈希拒绝测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.corpora.manifest import CorpusManifest, load_manifest
from worldmock.errors import IntegrityError


def _entry(path: str, content: bytes, entry_id: str | None = None) -> dict:
    """@brief 与内容一致的最小 manifest 条目。"""
    return {"entry_id": entry_id or path.replace("/", ":"), "path": path,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "media_type": "text/plain", "purpose": "fixture",
            "source": {"kind": "scenario", "license": "WorldMock-project", "selector": "complete frozen file"}}


def _manifest(entries: list[dict], created_at: str = "2026-09-15T08:00:00+08:00") -> dict:
    """@brief 最小 manifest 载荷。"""
    return {"schema_version": 1, "corpus_id": "fixture", "created_at": created_at,
            "transformer_version": "fixture-1", "entries": entries}


def _write_tree(root: Path) -> bytes:
    """@brief 写出两份语料文件及登记它们的 manifest，返回 manifest 字节。"""
    files = {"notes/a.txt": b"alpha\n", "b.txt": b"beta\n"}
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(content)
    raw = json.dumps(_manifest([_entry(path, content) for path, content in files.items()])).encode("utf-8")
    (root / "manifest.json").write_bytes(raw)
    return raw


def test_manifest_accepts_unique_identities_with_aware_creation_time():
    manifest = CorpusManifest.model_validate(_manifest([_entry("a.txt", b"a"), _entry("b.txt", b"b")]))

    assert [entry.entry_id for entry in manifest.entries] == ["a.txt", "b.txt"]
    assert manifest.created_at.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("payload, message", [
    (_manifest([_entry("a.txt", b"a")], created_at="2026-09-15T08:00:00"), "timezone-aware"),
    (_manifest([_entry("a.txt", b"a", "same"), _entry("b.txt", b"b", "same")]), "must be unique"),
    (_manifest([_entry("a.txt", b"a", "first"), _entry("a.txt", b"a", "second")]), "must be unique"),
], ids=["naive-created-at", "duplicate-entry-id", "duplicate-path"])
def test_manifest_rejects_naive_creation_time_and_duplicate_identities(payload, message):
    with pytest.raises(ValidationError, match=message):
        CorpusManifest.model_validate(payload)


def test_load_manifest_verifies_every_entry_and_accepts_matching_pin(tmp_path):
    raw = _write_tree(tmp_path)

    manifest = load_manifest(tmp_path, expected_sha256=hashlib.sha256(raw).hexdigest())

    assert sorted(entry.path for entry in manifest.entries) == ["b.txt", "notes/a.txt"]


def test_load_manifest_rejects_missing_or_symlinked_manifest(tmp_path, monkeypatch):
    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        load_manifest(tmp_path)
    _write_tree(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "manifest.json" or original(self))

    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_pin_mismatch(tmp_path):
    _write_tree(tmp_path)

    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_manifest(tmp_path, expected_sha256="0" * 64)


@pytest.mark.parametrize("raw", [
    b"{not json",
    json.dumps(_manifest([], created_at="2026-09-15T08:00:00")).encode("utf-8"),
], ids=["malformed-json", "naive-created-at"])
def test_load_manifest_wraps_parse_and_schema_failures(tmp_path, raw):
    (tmp_path / "manifest.json").write_bytes(raw)

    with pytest.raises(IntegrityError, match="validation failed") as caught:
        load_manifest(tmp_path)

    assert isinstance(caught.value.__cause__, ValueError)


def test_load_manifest_rejects_entry_content_drift(tmp_path):
    _write_tree(tmp_path)
    (tmp_path / "b.txt").write_bytes(b"BETA\n")

    with pytest.raises(IntegrityError, match="identity mismatch"):
        load_manifest(tmp_path)
