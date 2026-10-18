"""掩码完整日比较在同一个检出的两次录制之间相等：录制与行为签名可重复，比对的屏蔽与映射对完整日数据足够，
录下的值里没有机器路径。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.compare_complete_days import KINDS
from scripts.compare_runs import Chain, Options, Side, compare

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.day_run
def test_complete_day_behaviour_is_equal_for_one_checkout(tmp_path):
    """@brief 基与头是同一检出：两个进程各录两份完整日，结论 equal；两侧的运行与导出不带规则逐项比对也相等。"""
    work = tmp_path / "work"
    result = subprocess.run([sys.executable, "scripts/compare_complete_days.py", "--base-root", str(ROOT),
                             "--head-root", str(ROOT), "--work", str(work)], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=7200)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((work / "complete-day-behaviour.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "equal" and result.stdout.strip().endswith("Complete-day behaviour: equal")
    for kind in KINDS:
        sides = [Side(Chain.load([work / side / kind / "run"]), work / side / kind / "export")
                 for side in ("base", "head")]
        compared = compare(sides[0], sides[1], Options(None, (), False))
        assert compared["verdict"] == "equal", json.dumps(compared["layers"], ensure_ascii=False)[:3000]
        assert compared["problems"] == []
