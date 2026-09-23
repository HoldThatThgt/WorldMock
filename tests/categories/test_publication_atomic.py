"""@brief 原子目录公开必须拒绝最终重命名时出现的任何目标。"""

from __future__ import annotations

import ctypes
import errno
import os

import pytest

from tests.categories.test_maintenance import _candidate, _snapshot
from worldmock.categories import maintenance


@pytest.mark.parametrize("entry_kind", ["empty_directory", "file", "dangling_symlink"])
def test_publication_refuses_every_existing_entry(valid_catalog_dir, tmp_path, entry_kind, monkeypatch):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    destination = tmp_path / "published"
    if entry_kind == "empty_directory":
        destination.mkdir()
    elif entry_kind == "file":
        destination.write_text("another publisher")
    else:
        original_is_symlink = type(destination).is_symlink
        monkeypatch.setattr(type(destination), "is_symlink",
                            lambda path: path == destination or original_is_symlink(path))
    before = _snapshot(valid_catalog_dir)
    previous = destination.lstat() if entry_kind != "dangling_symlink" else None
    with pytest.raises(ValueError, match="CATEGORY_DESTINATION_EXISTS"):
        maintenance.publish_catalog(valid_catalog_dir, candidate, destination, {}, (tmp_path / "scene",))
    if previous is None:
        assert not destination.exists()
    else:
        assert destination.lstat() == previous
    assert _snapshot(valid_catalog_dir) == before
    assert not (tmp_path / "absent").exists()
    assert not list(tmp_path.glob(".category-publish-*"))


def test_atomic_publication_preserves_competing_empty_directory(valid_catalog_dir, tmp_path, monkeypatch):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    destination = tmp_path / "published"
    before = _snapshot(valid_catalog_dir)
    previous = {}
    original = maintenance._rename_exclusive

    def race_after_validation(source, target):
        target.mkdir()
        previous["inode"] = target.stat().st_ino
        original(source, target)

    monkeypatch.setattr(maintenance, "_admit_scenarios", lambda *args: None)
    monkeypatch.setattr(maintenance, "_rename_exclusive", race_after_validation)
    with pytest.raises(ValueError, match="CATEGORY_DESTINATION_EXISTS"):
        maintenance.publish_catalog(valid_catalog_dir, candidate, destination, {}, (tmp_path / "scene",))
    assert destination.stat().st_ino == previous["inode"]
    assert list(destination.iterdir()) == []
    assert _snapshot(valid_catalog_dir) == before
    assert not list(tmp_path.glob(".category-publish-*"))


def test_exclusive_rename_publishes_complete_directory(tmp_path):
    staged, destination = tmp_path / "staged", tmp_path / "published"
    staged.mkdir()
    (staged / "marker").write_text("complete catalog")
    maintenance._rename_exclusive(staged, destination)
    assert not staged.exists()
    assert (destination / "marker").read_text() == "complete catalog"


@pytest.mark.parametrize("platform, expected", [
    ("darwin", (b"staged", b"published", 4)),
    ("linux", (-100, b"staged", -100, b"published", 1)),
])
def test_native_exclusive_rename_flags(platform, expected, monkeypatch):
    calls = []

    class Operation:
        def __call__(self, *args):
            calls.append(args)
            return 0

    class Library:
        renamex_np = Operation()
        renameat2 = Operation()

    monkeypatch.setattr(maintenance.sys, "platform", platform)
    monkeypatch.setattr(maintenance.ctypes, "CDLL", lambda *args, **kwargs: Library())
    maintenance._rename_exclusive("staged", "published")
    assert calls == [expected]


def test_unsupported_platform_fails_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance.sys, "platform", "unsupported")
    with pytest.raises(ValueError, match="CATEGORY_ATOMIC_PUBLICATION_UNSUPPORTED"):
        maintenance._rename_exclusive(tmp_path / "staged", tmp_path / "published")
    assert not (tmp_path / "published").exists()


def test_native_publication_error_is_preserved(tmp_path, monkeypatch):
    class Operation:
        def __call__(self, *args):
            ctypes.set_errno(errno.EACCES)
            return -1

    class Library:
        renamex_np = Operation()

    monkeypatch.setattr(maintenance.sys, "platform", "darwin")
    monkeypatch.setattr(maintenance.ctypes, "CDLL", lambda *args, **kwargs: Library())
    with pytest.raises(OSError) as error:
        maintenance._rename_exclusive(tmp_path / "staged", tmp_path / "published")
    assert error.value.errno == errno.EACCES
    assert error.value.filename == os.fspath(tmp_path / "published")
