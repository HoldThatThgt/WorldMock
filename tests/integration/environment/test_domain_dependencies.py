"""@brief 纯目录及环境校验在独立解释器内不加载完整执行链。"""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("operation", [
    "from worldmock.resource_engine.catalog.loader import load_catalog; load_catalog(root / 'catalog', seed=1)",
    "from worldmock.configuration.inputs.environment import load_environment; load_environment(root, metadata_loader=fixture_metadata)",
    "from worldmock.configuration.inputs.validation import validate_environment; "
    "report = validate_environment(root, metadata_loader=fixture_metadata); assert report['valid']",
], ids=["catalog", "environment", "validation"])
def test_domain_checks_do_not_load_the_complete_engine(operation):
    """@brief 真实纯加载与校验只读取输入，不导入引擎、环境 actor 或原生 Society。"""
    script = (
        "import sys; from tests.data_paths import ENVIRONMENT_ROOT as root; "
        "from tests.frozen_inputs import fixture_metadata; "
        "before={str(path):path.read_bytes() for path in root.rglob('*') if path.is_file()}; "
        + operation + "; "
        "after={str(path):path.read_bytes() for path in root.rglob('*') if path.is_file()}; assert before==after; "
        "forbidden={'worldmock.simulation_engine.engine','worldmock.simulation_engine.game','custom.envs.life_env',"
        "'worldmock.runtime.framework.read_query'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
