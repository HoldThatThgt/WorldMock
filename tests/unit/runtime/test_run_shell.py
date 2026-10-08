"""@brief 运行目录准入只接受允许的状态文件。"""

import pytest

from worldmock.errors import IntegrityError
from worldmock.runtime import _require_run_shell


def test_run_shell_allows_only_status_file(tmp_path) -> None:
    """CLI 状态壳可用，任何已有运行工件都被拒绝。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text("{}")
    _require_run_shell(run_dir)

    (run_dir / "manifest.json").write_text("{}")
    with pytest.raises(FileExistsError, match="artifacts"):
        _require_run_shell(run_dir)
