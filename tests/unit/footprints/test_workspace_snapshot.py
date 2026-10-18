"""人物工作区快照的存储形式：排序的普通文件 tar 以固定头部 gzip 压缩（级别 6、修改时刻 0），素材是 application/gzip；
恢复先完整解压再按 tar 核对成员，压缩层或归档层损坏都按无效快照拒绝（docs/design/05-recording-engine.md
「冻结输入、检查点与分支」）。"""

import gzip
import io
import logging
import random
import tarfile
import time

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore
from worldmock.footprints.checkpoint import restore_directory, snapshot_directory

#: 无效快照的错误原文。
_INVALID = "^Workspace snapshot archive is invalid$"
#: 无效快照的英文错误日志片段。
_LOGGED = "is not a valid gzip-compressed tar"
#: 工作区文件：名字全小写、排序与平台无关；一份不可压缩的内容让 gzip 流长过 515 字节。
_FILES = {"notes.md": "# 笔记\n".encode("utf-8"), "agent.json": b'{"id": 1}',
          "memory/state.json": b"{}", "memory/episodes.jsonl": random.Random(0).randbytes(4096)}


def _workspace(root):
    """按 _FILES 写出一个工作区目录。"""
    for relative, raw in _FILES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(raw)
    return root


def _gzipped(raw: bytes) -> bytes:
    """按生产快照的同一调用（级别 6、修改时刻 0）压缩。"""
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=6, mtime=0) as stream:
        stream.write(raw)
    return compressed.getvalue()


def _snapshot_bytes(tmp_path) -> tuple[bytes, bytes]:
    """真实快照的（gzip 素材字节, 其中的 tar 字节）。"""
    assets = AssetStore(tmp_path / "source-assets")
    content = assets.read(snapshot_directory(_workspace(tmp_path / "workspace"), assets))
    return content, gzip.decompress(content)


def test_workspace_snapshot_is_a_gzip_of_the_sorted_tar_with_a_fixed_header(tmp_path):
    """素材的媒体类型是 application/gzip、来源是工作区；头十字节固定为 1f 8b 08 00 00 00 00 00 00 ff；解压得到的 tar
    按相对路径排序列出每个普通文件，权限 0600、修改时刻 0、属主为 0 且无名，内容逐字节相同。"""
    assets = AssetStore(tmp_path / "assets")
    reference = snapshot_directory(_workspace(tmp_path / "workspace"), assets)
    assert reference.media_type == "application/gzip"
    assert reference.source == {"kind": "workspace"}
    content = assets.read(reference)
    assert content[:10] == bytes.fromhex("1f8b08000000000000ff")
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(content)), mode="r:") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(_FILES)
        for member in members:
            assert member.type == tarfile.REGTYPE
            assert (member.mode, member.mtime, member.uid, member.gid) == (0o600, 0, 0, 0)
            assert (member.uname, member.gname) == ("", "")
            assert archive.extractfile(member).read() == _FILES[member.name]


def test_workspace_snapshot_bytes_do_not_depend_on_the_wall_clock(tmp_path, monkeypatch):
    """同一工作区在两个相隔很远的墙钟时刻快照到两个素材库，得到同一摘要：gzip 头部不写当前时刻。"""
    workspace = _workspace(tmp_path / "workspace")
    monkeypatch.setattr(time, "time", lambda: 1_000_000_000.0)
    first = snapshot_directory(workspace, AssetStore(tmp_path / "first"))
    monkeypatch.setattr(time, "time", lambda: 2_000_000_000.0)
    second = snapshot_directory(workspace, AssetStore(tmp_path / "second"))
    assert first.sha256 == second.sha256


def _flipped_crc(content: bytes) -> bytes:
    """把 gzip 尾部 CRC-32 的第一个字节取反。"""
    return content[:-8] + bytes([content[-8] ^ 0xFF]) + content[-7:]


@pytest.mark.parametrize("damage", [
    lambda content, archive: archive,
    lambda content, archive: content[:515],
    lambda content, archive: _flipped_crc(content),
    lambda content, archive: b"\x01" * 1024,
], ids=["plain-tar", "truncated-gzip", "flipped-crc", "not-gzip"])
def test_restore_refuses_a_plain_tar_and_damaged_gzip(tmp_path, caplog, damage):
    """未压缩的 tar、截断的 gzip 流、CRC 不符的 gzip 与根本不是 gzip 的字节：恢复写英文错误日志并以无效快照拒绝，
    目标目录不会被创建。"""
    content, archive = _snapshot_bytes(tmp_path)
    assets = AssetStore(tmp_path / "assets")
    reference = assets.put(damage(content, archive), {"media_type": "application/gzip",
                                                      "source": {"kind": "workspace"}})
    destination = tmp_path / "restore"
    caplog.set_level(logging.ERROR)
    with pytest.raises(IntegrityError, match=_INVALID):
        restore_directory(reference, assets, destination)
    assert _LOGGED in caplog.text
    assert not destination.exists()


@pytest.mark.parametrize("damage", [lambda archive: archive[:515], lambda archive: b"\x01" * 1024],
                         ids=["truncated-tar", "not-a-tar"])
def test_restore_refuses_a_gzip_of_a_damaged_tar(tmp_path, caplog, damage):
    """完好的 gzip 里装着截断的 tar 或不是 tar 的字节：解压通过、归档层失败，同样写英文错误日志并以无效快照拒绝，
    目标目录不会被创建。"""
    _, archive = _snapshot_bytes(tmp_path)
    assets = AssetStore(tmp_path / "assets")
    reference = assets.put(_gzipped(damage(archive)), {"media_type": "application/gzip",
                                                       "source": {"kind": "workspace"}})
    destination = tmp_path / "restore"
    caplog.set_level(logging.ERROR)
    with pytest.raises(IntegrityError, match=_INVALID):
        restore_directory(reference, assets, destination)
    assert _LOGGED in caplog.text
    assert not destination.exists()
