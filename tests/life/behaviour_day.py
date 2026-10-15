"""在本检出里录两份脚本化完整日并写出只屏蔽代码身份的行为签名，供掩码完整日比较在基与头两侧各跑一次。

@file     behaviour_day.py
@brief    以 `python -P -m tests.life.behaviour_day --case-root DIR --output FILE` 在一侧检出里运行；
          录制与签名都用这一侧自己的代码和测试助手。
"""

from __future__ import annotations

import tests.conftest  # noqa: F401  # 先装上测试进程的缺省变量（离线凭据与本地成本表），pytest 之外运行时也一样。

import argparse
import json
from pathlib import Path

import pytest

from tests.dayrun_baseline import behaviour_signature
from tests.life.day_baseline_runtime import record_complete_day

PROJECT = Path(__file__).resolve().parents[2]
#: 两份完整日：活动日与回调日。
KINDS = ("activity", "callback")


def record(case_root: Path) -> dict:
    """@brief 在 case_root 下各录一份活动日与回调日，返回以种类为键的行为签名记录。"""
    records = {}
    for kind in KINDS:
        with pytest.MonkeyPatch.context() as monkeypatch:
            records[kind] = record_complete_day(monkeypatch, case_root / kind, PROJECT, kind, behaviour_signature)
    return records


def main(argv: list[str] | None = None) -> int:
    """@brief 命令入口：录制两份完整日，把行为签名写成规范 JSON 加一个换行。"""
    parser = argparse.ArgumentParser(description="Record the two scripted complete days with behaviour signatures")
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    records = record(arguments.case_root)
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(payload.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
