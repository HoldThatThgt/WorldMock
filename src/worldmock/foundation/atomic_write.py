"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import logging
import os
import tempfile
import time
from pathlib import Path




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
