#!/usr/bin/env python3
"""@brief 不需要 uv 或 SSH 的 WorldMock 固定环境引导入口。"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

# 直接执行脚本时明确使用仓库根下的同一标准库辅助模块。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bootstrap_environment import create_environment, install_requirements, install_source, write_receipt
from scripts.bootstrap_sources import restore_as2


def bootstrap(root: Path, profile: str, wheelhouse: Path | None = None, use_uv: bool = False) -> dict:
    """@brief 先核对 AS2 源码，再按唯一哈希锁安装 WorldMock 环境。"""
    as2_digest = restore_as2(root)
    python = create_environment(root / ".venv", use_uv)
    install_requirements(python, root / "requirements/lock.txt", wheelhouse, use_uv)
    install_source(python, root / "vendor/agentsociety2/packages/agentsociety2", use_uv)
    install_source(python, root, use_uv)
    return write_receipt(root, profile, {"as2_source_sha256": as2_digest})


def main() -> int:
    """@brief 解析固定参数，失败记录英文错误并以非零码终止。"""
    parser = argparse.ArgumentParser(description="Rebuild the locked WorldMock environment without SSH")
    parser.add_argument("--profile", choices=("release", "dev"), default="release")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--use-uv", action="store_true")
    arguments = parser.parse_args()
    wheelhouse = arguments.wheelhouse.resolve() if arguments.wheelhouse is not None else None
    try:
        receipt = bootstrap(ROOT, arguments.profile, wheelhouse, arguments.use_uv)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        logging.error("Environment bootstrap failed: %s", error)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
