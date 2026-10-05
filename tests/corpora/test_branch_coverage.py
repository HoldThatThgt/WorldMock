"""@brief 人工语料树的清单校验与文件身份验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldmock.corpora.manifest import load_manifest
from worldmock.errors import IntegrityError


def _entry_dict(path: str, content: bytes, entry_id: str | None = None):
    import hashlib
    return {"entry_id": entry_id or path.replace("/", ":"), "path": path,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "media_type": "application/json", "purpose": "test",
            "source": {"kind": "scenario", "url": None, "retrieved_at": None,
                       "license": "test", "selector": "test", "fact_cutoff": None}}


def _manifest_dict(entries, **overrides):
    payload = {"schema_version": 1, "corpus_id": "test-corpus",
               "created_at": "2026-09-15T12:00:00+08:00",
               "transformer_version": "test", "entries": entries}
    payload.update(overrides)
    return payload


def _write_manifest(root: Path, manifest):
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_manifest_rejects_naive_created_at_and_duplicate_identities(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    entry = _entry_dict("a.json", b"{}")
    naive = _manifest_dict([entry], created_at="2026-09-15T12:00:00")
    _write_manifest(tmp_path, naive)
    with pytest.raises(IntegrityError, match="validation failed"):
        load_manifest(tmp_path)
    duplicated = _manifest_dict([entry, dict(entry)])
    _write_manifest(tmp_path, duplicated)
    with pytest.raises(IntegrityError, match="validation failed"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_missing_manifest_and_hash_pin_mismatch(tmp_path):
    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        load_manifest(tmp_path)
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    _write_manifest(tmp_path, _manifest_dict([_entry_dict("a.json", b"{}")]))
    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_manifest(tmp_path, expected_sha256="0" * 64)


def test_load_manifest_rejects_malformed_manifest_json(tmp_path):
    (tmp_path / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(IntegrityError, match="validation failed"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_entry_content_drift(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    stale = _entry_dict("a.json", b"different bytes")
    _write_manifest(tmp_path, _manifest_dict([stale]))
    with pytest.raises(IntegrityError, match="identity mismatch"):
        load_manifest(tmp_path)


def test_load_manifest_accepts_a_minimal_valid_tree(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    _write_manifest(tmp_path, _manifest_dict([_entry_dict("a.json", b"{}")]))
    manifest = load_manifest(tmp_path)
    assert manifest.entries[0].path == "a.json"
