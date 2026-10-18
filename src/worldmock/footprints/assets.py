"""@brief 先持久化再发布按哈希寻址的素材，拒绝损坏引用。"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

from worldmock.errors import IntegrityError
from worldmock.footprints.models import AssetRef, canonical_json


#: Windows 上同步或杀毒软件短暂占用目标时 os.replace 被拒绝访问，按这些间隔（秒）重试（issue #7），共 12.75 秒。
_REPLACE_RETRY_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4)


def _replace(temporary: Path, path: Path) -> None:
    """@brief 原子替换；仅 Windows 的 PermissionError 按固定间隔重试，用尽后抛出最后一次的错误，其他平台与其他错误立即抛出。"""
    for delay in _REPLACE_RETRY_SECONDS:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            logging.warning("Atomic replace denied, retrying in %.2fs: %s", delay, path.name)
            time.sleep(delay)
    os.replace(temporary, path)


def atomic_write(path: Path, content: bytes) -> None:
    """@brief 刷盘临时文件并原子替换；POSIX 再刷盘父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        logging.exception("Atomic artifact publication failed: %s", path.name)
        raise
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class AssetStore:
    """@brief 素材路径只由 SHA-256 决定，调用方不能指定任意读取路径。"""

    def __init__(self, root: Path):
        """@brief 绑定当前运行的素材目录。"""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, metadata: dict) -> AssetRef:
        """@brief 完整发布素材，保留模态和来源。"""
        reference = AssetRef(
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content),
            media_type=metadata["media_type"], source=metadata["source"],
        )
        path = self.root / reference.sha256
        if not path.exists():
            atomic_write(path, content)
        self.read(reference)
        return reference

    def put_json(self, value: object, source: dict) -> AssetRef:
        """@brief 按唯一编码保存已脱敏的结构化证据。"""
        return self.put(canonical_json(value), {"media_type": "application/json", "source": source})

    def read(self, reference: AssetRef) -> bytes:
        """@brief 每次实际读取都复核路径、大小与内容身份。"""
        path = self.root / reference.sha256
        if path.is_symlink() or not path.is_file():
            raise IntegrityError("Artifact is missing or is a symbolic link")
        content = path.read_bytes()
        if len(content) != reference.size_bytes or hashlib.sha256(content).hexdigest() != reference.sha256:
            raise IntegrityError("Artifact hash or byte count mismatch")
        return content
