"""@brief 静态测试目录经真实 pytest 收集可正确解析共享夹具。"""

import subprocess
import sys
from pathlib import Path

from scripts import fresh_check as fresh


def test_fixed_directory_collection_resolves_environment_fixtures():
    command = [sys.executable, "-m", "pytest", *fresh.QUICK_TESTS, "--collect-only", "--fixtures-per-test",
               "-q", "--environment-profile", "dev", "-p", "no:cacheprovider"]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[3], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    header = "fixtures used by test_dev_environment_receipt_records_missing_without_absolute_paths"
    assert header in result.stdout
    block = result.stdout.partition(header)[2].split("fixtures used by ", 1)[0]
    names = {line.partition(" -- ")[0].strip() for line in block.splitlines() if " -- " in line}
    assert {"tmp_path", "monkeypatch"} <= names
