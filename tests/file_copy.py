"""@brief 所有测试隔离副本使用同一跨卷复制规则。"""

import errno
import os
import shutil


def link_or_copy(source: str, target: str) -> str:
    """@brief 同卷创建硬链接，只有跨卷错误才按实际字节复制，其他错误原样传播。"""
    try:
        os.link(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
        shutil.copy2(source, target)
    return target
