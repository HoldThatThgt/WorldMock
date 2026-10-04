"""固定源码的实际扫描必须接受完整生活环境，并拒绝残缺直接环境合同。"""

from pathlib import Path

from agentsociety2.backend.services.custom.compatibility import build_env_scan_diagnostic
from agentsociety2.backend.services.custom.scanner import CustomModuleScanner

from custom.envs.life_env import LifeEnv


ROOT = Path(__file__).resolve().parents[2]


def test_actual_scanner_accepts_life_env_with_the_complete_direct_environment_contract(monkeypatch):
    monkeypatch.setenv('WORKSPACE_PATH', str(ROOT))
    result = CustomModuleScanner(str(ROOT)).scan_all()
    assert not result['errors']
    assert {item['type'] for item in result['envs']} == {'LifeEnv'}
    diagnostic = next(item for item in result['env_diagnostics'] if item['class_name'] == 'LifeEnv')
    assert diagnostic['accepted'] is True
    assert diagnostic['metadata']['tool_count'] == 0
    assert diagnostic['metadata']['has_direct_contract'] is True


def test_scan_diagnostic_rejects_an_incomplete_direct_environment_contract():
    class IncompleteLifeEnv(LifeEnv):
        begin_decision_round = None

    diagnostic = build_env_scan_diagnostic(
        workspace_path=ROOT, module_path='custom/envs/life_env.py',
        file_path=ROOT / 'custom/envs/life_env.py', cls=IncompleteLifeEnv,
    )
    assert diagnostic.accepted is False
    assert diagnostic.metadata['has_direct_contract'] is False
    assert any(item.code == 'missing_tools' for item in diagnostic.issues)
