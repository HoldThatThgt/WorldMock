"""@brief 人工语料树的文件边界与符号链接拒绝行为。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worldmock.corpora import load_manifest
from worldmock.errors import IntegrityError


def test_manifest_rejects_in_root_symbolic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根内文件即使指向安全目标，被标记为符号链接也必须拒绝。"""
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.write_bytes(target.read_bytes())
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "corpus_id": "symlink-negative",
        "created_at": "2026-09-15T12:00:00+08:00",
        "transformer_version": "test",
        "entries": [{
            "entry_id": "link",
            "path": "link.json",
            "sha256": digest,
            "size_bytes": 2,
            "media_type": "application/json",
            "purpose": "negative",
            "source": {"kind": "scenario", "url": None, "retrieved_at": None,
                       "license": "test", "selector": "test", "fact_cutoff": None},
        }],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_manifest(tmp_path)
