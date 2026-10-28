#!/usr/bin/env python3
"""录音应答运行的一段子进程：沿用来源这一段的运行身份，调用被测检出自己的命令入口。

@file     recorded_response_child.py
@brief    以 `python -P scripts/recorded_response_child.py WORK/segments/<kk>.json` 在被测检出里运行（-P：工具自己的
          scripts/ 不进 sys.path，只经 PYTHONPATH 导入项目代码）。只在本进程里把给运行命名的那一次 uuid4 换成只能用一次
          的替身：新建运行在 worldmock.cli，恢复在 worldmock.recording_engine.storage.checkpoint；两个模块都不导入 AS2，进程配置仍在
          AS2 导入之前完成。事件与检查点身份照常是新的 uuid4，不进任何随机键。
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

#: 各模式下运行身份抽取所在模块。
RUN_IDENTITY_MODULES = {"run": "worldmock.cli", "resume": 'worldmock.recording_engine.storage.checkpoint'}


def one_shot_uuid(run_id: str, module: str) -> Callable[[], uuid.UUID]:
    """@brief 只能用一次的 uuid4 替身：第一次返回来源的运行身份，第二次记错误并拒绝。"""
    drawn = False

    def draw() -> uuid.UUID:
        """@brief 给运行命名的那一次抽取。"""
        nonlocal drawn
        if drawn:
            message = f"Recorded-response run expected one run identity draw in {module}"
            logging.getLogger(__name__).error("%s", message)
            raise RuntimeError(message)
        drawn = True
        return uuid.UUID(hex=run_id)

    return draw


def main(argv: list[str]) -> int:
    """@brief 读段计划、替换抽取、调用被测检出的 CLI 并返回其退出码。"""
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    name = RUN_IDENTITY_MODULES[spec["mode"]]
    module = importlib.import_module(name)
    module.uuid4 = one_shot_uuid(spec["run_id"], name)
    cli = importlib.import_module("worldmock.cli")
    return cli.main(spec["argv"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
