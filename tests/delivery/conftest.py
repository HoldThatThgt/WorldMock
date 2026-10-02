"""真实数据集加载测试使用短目录，避免第三方锁文件把路径重复展开后超过 Windows 限制。"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def dataset_tmp_path():
    """@brief 以机器当前用户目录为根创建短暂存目录，真实执行后由标准库清理。"""
    with TemporaryDirectory(prefix="wm", dir=Path.home()) as directory:
        yield Path(directory)
