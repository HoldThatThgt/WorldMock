"""@brief 在 pytest 门禁中执行查看器真实 JavaScript 与 SQLite 回归。"""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.requires_node
def test_run_viewer_recorded_names():
    """@brief Node 缺失明确失败，防止核心页面回归被静默跳过。"""
    node = shutil.which("node")
    assert node is not None, "Node.js with node:sqlite is required to test the run viewer"
    test_file = Path(__file__).with_name("run_viewer.test.cjs")
    context_file = Path(__file__).with_name("run_viewer_context.test.cjs")
    wording_file = Path(__file__).with_name("run_viewer_wording.test.cjs")
    identity_file = Path(__file__).with_name("run_viewer_identity.test.cjs")
    files_file = Path(__file__).with_name("run_viewer_files.test.cjs")
    activities_file = Path(__file__).with_name("run_viewer_activities.test.cjs")
    result = subprocess.run(
        [node, "--test", str(test_file), str(context_file), str(wording_file), str(identity_file), str(files_file),
         str(activities_file)],
        cwd=test_file.parents[3],
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
