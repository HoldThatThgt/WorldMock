"""@brief 在 pytest 门禁中执行查看器真实 JavaScript 与 SQLite 回归。"""

from pathlib import Path
import shutil
import subprocess


def test_run_viewer_recorded_names():
    """@brief Node 缺失明确失败，防止核心页面回归被静默跳过。"""
    node = shutil.which("node")
    assert node is not None, "Node.js with node:sqlite is required to test the run viewer"
    test_file = Path(__file__).with_name("run_viewer.test.cjs")
    result = subprocess.run(
        [node, "--test", str(test_file)],
        cwd=test_file.parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
