"""@brief 平台分支补测：atomic_write 的 POSIX 目录刷盘与 snapshot_directory 的符号链接拒绝。

Windows 上 os.name 恒为 "nt"、且账户无法创建符号链接：此处只替换被测模块内的 os 引用
（tempfile/pathlib 仍用真实 os），或按路径伪报 Path.is_symlink，在真实临时目录上断言可观察行为。
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

import worldmock.foundation.atomic_write as assets_module
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.foundation.atomic_write import atomic_write
from worldmock.recording_engine.storage.checkpoint import snapshot_directory

#: 伪目录描述符；负数保证不与任何真实文件描述符相撞。
_DIRECTORY_FD = -42


class _PosixOs:
    """@brief assets 模块专用 os 代理：报告 POSIX；目录描述符只记录，不触达真实系统。"""

    name = "posix"

    def __init__(self, target: Path, fail_directory_sync: bool = False):
        self.target = target
        self.fail_directory_sync = fail_directory_sync
        self.events: list[tuple] = []

    def __getattr__(self, attribute: str):
        """@brief 其余属性（replace、O_RDONLY 等）转发给真实 os。"""
        return getattr(os, attribute)

    def open(self, path, flags: int, mode: int = 0o777) -> int:
        """@brief 记录目录打开参数并返回伪描述符。"""
        self.events.append(("open", Path(path), flags))
        return _DIRECTORY_FD

    def fsync(self, descriptor: int) -> None:
        """@brief 文件描述符真实刷盘；目录刷盘记录此刻目标名下的内容。"""
        if descriptor != _DIRECTORY_FD:
            self.events.append(("fsync-file",))
            os.fsync(descriptor)
            return
        self.events.append(("fsync-directory", self.target.read_bytes()))
        if self.fail_directory_sync:
            raise OSError("directory fsync failed")

    def close(self, descriptor: int) -> None:
        """@brief 记录伪目录描述符的释放。"""
        if descriptor != _DIRECTORY_FD:
            os.close(descriptor)
            return
        self.events.append(("close-directory",))


def test_atomic_write_fsyncs_parent_directory_after_replace_on_posix(tmp_path, monkeypatch):
    target = tmp_path / "artifacts" / "asset.bin"
    target.parent.mkdir()
    target.write_bytes(b"old content")
    posix = _PosixOs(target)
    monkeypatch.setattr(assets_module, "os", posix)

    atomic_write(target, b"new content")

    assert target.read_bytes() == b"new content"
    # 临时文件先刷盘；目录以只读打开，在新内容已位于最终名下之后刷盘，并随即关闭。
    assert posix.events == [("fsync-file",), ("open", target.parent, os.O_RDONLY),
                            ("fsync-directory", b"new content"), ("close-directory",)]
    assert [path.name for path in target.parent.iterdir()] == ["asset.bin"]


def test_atomic_write_closes_directory_descriptor_when_directory_fsync_fails(tmp_path, monkeypatch, caplog):
    target = tmp_path / "asset.bin"
    posix = _PosixOs(target, fail_directory_sync=True)
    monkeypatch.setattr(assets_module, "os", posix)

    with pytest.raises(OSError, match="directory fsync failed"):
        atomic_write(target, b"payload")

    assert posix.events[-2:] == [("fsync-directory", b"payload"), ("close-directory",)]
    assert [path.name for path in tmp_path.iterdir()] == ["asset.bin"]
    assert "Atomic artifact publication failed: asset.bin" in caplog.text


def _workspace(root: Path) -> Path:
    """@brief 含顶层文件与子目录文件的真实临时工作区。"""
    (root / "notes").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"alpha")
    (root / "notes" / "b.txt").write_bytes(b"beta")
    return root


@pytest.mark.parametrize("linked", ["a.txt", "notes", "notes/b.txt"])
def test_snapshot_directory_rejects_symbolic_links_without_publishing(tmp_path, monkeypatch, linked):
    workspace = _workspace(tmp_path / "workspace")
    store = AssetStore(tmp_path / "assets")
    link = workspace.resolve() / linked
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or original(self))

    with pytest.raises(IntegrityError, match="symbolic link"):
        snapshot_directory(workspace, store)

    assert list(store.root.iterdir()) == []


def test_snapshot_directory_archives_the_same_tree_when_no_link_is_present(tmp_path):
    workspace = _workspace(tmp_path / "workspace")
    store = AssetStore(tmp_path / "assets")

    reference = snapshot_directory(workspace, store)

    with tarfile.open(fileobj=io.BytesIO(store.read(reference))) as archive:
        members = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    assert members == {"a.txt": b"alpha", "notes/b.txt": b"beta"}
