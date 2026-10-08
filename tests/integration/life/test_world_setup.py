"""@brief 不创建引擎的真实场景装配与环境读取边界。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.life.world_setup import load_runtime_scenario


def test_loader_assembles_without_corpora_and_ignores_unread_directories(tmp_path):
    """@brief 只发布目录与场景即可装配，其他目录中的坏内容不被读取或校验。"""
    root = tmp_path / "environment"
    for name in ("catalog", "scenarios"):
        shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / name, root / name)
    path = root / "scenarios/single-person.json"
    first, digest = load_runtime_scenario(path)
    assert not (root / "corpora").exists()
    for name in ("corpora", "phone", "author"):
        (root / name).mkdir()
        (root / name / "manifest.json").write_text("not valid JSON", encoding="utf-8")
    second, repeated = load_runtime_scenario(path)
    assert first.scenario == second.scenario
    assert first.catalog.manifest == second.catalog.manifest
    assert digest == repeated
    assert second.scenario.schema_version == 8


def test_pure_loading_and_coverage_do_not_import_the_life_engine():
    """@brief 新进程中的真实装配与覆盖目标构造不触发引擎模块导入。"""
    source = """
import json
import sys
from pathlib import Path
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.profile.coverage_targets import build_coverage_targets
semantics, digest = load_runtime_scenario(Path(sys.argv[1]))
targets = build_coverage_targets(semantics.scenario, semantics.catalog)
assert 'worldmock.life.engine' not in sys.modules
print(json.dumps({'scenario': semantics.scenario.schema_version, 'apps': len(targets.app_names)}))
"""
    result = subprocess.run([sys.executable, "-c", source,
                             str(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json")],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"scenario": 8, "apps": 8}
