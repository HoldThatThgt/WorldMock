"""@brief 领域测试在独立解释器执行时不加载完整引擎和 AS2 执行链。"""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("collect, paths", [
    (True, (
        "tests/unit/catalog/test_admission.py", "tests/unit/catalog/test_engine_package_resources.py",
        "tests/unit/catalog/test_identity.py", "tests/unit/catalog/test_loader.py", "tests/unit/catalog/test_mapgen.py",
        "tests/unit/exchange/test_constants.py", "tests/unit/exchange/test_operators.py", "tests/unit/exchange/test_types.py",
        "tests/integration/runtime/test_environment_identity.py", "tests/integration/life/test_acs_loading.py",
        "tests/integration/life/test_load_checks.py", "tests/integration/life/test_scenario.py", "tests/integration/life/test_semantics.py",
        "tests/integration/life/test_session_state.py", "tests/integration/life/test_state_serialization.py",
        "tests/system/cli/test_cli.py", "tests/unit/engineering/test_single_source.py", "tests/system/environment/test_validation.py",
    )),
    (False, (
        "tests/unit/catalog/test_graph.py", "tests/integration/evaluation/test_adapters.py", "tests/integration/life/test_travel_cost.py",
        "tests/integration/life/test_travel_graph.py", "tests/integration/life/test_wayfinding.py",
    )),
    (False, (
        "tests/unit/catalog/test_affordances.py", "tests/integration/life/test_action_restore_and_text.py",
        "tests/integration/life/test_action_run.py", "tests/integration/life/test_app_progress.py", "tests/integration/life/test_app_push.py",
        "tests/unit/life/test_app_vocabulary.py", "tests/integration/life/test_content_select.py", "tests/integration/life/test_routing.py",
        "tests/unit/life/test_start_activity_schema.py::test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb",
        "tests/system/viewer/test_run_viewer.py",
    )),
], ids=["scenario-collection", "map-behavior", "app-behavior"])
def test_domain_checks_do_not_load_the_complete_engine(tmp_path, collect, paths):
    """@brief 保留实际收集和行为执行的依赖隔离检查，不读取历史阶段或所有权登记。"""
    arguments = [*paths, "-q", "--environment-profile", "dev", "-p", "no:cacheprovider",
                 "--basetemp", str(tmp_path / "domain")]
    if collect:
        arguments.insert(0, "--collect-only")
    forbidden = {"worldmock.life.engine", "worldmock.life.game", "worldmock.life.games",
                 "worldmock.life.read_query_runtime", "custom.envs.life_env"}
    if collect:
        forbidden.add("worldmock.life.rules")
    script = (
        "import sys,pytest; result=pytest.main(" + repr(arguments) + "); assert result==0; "
        "forbidden=" + repr(forbidden) + "; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
