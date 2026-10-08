"""@brief 真实环境命令、地图发布和纯命令收集边界。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.cli import build_parser

ROOT = Path(__file__).resolve().parents[3]
CLI_ENTRY = "from worldmock.cli import main; raise SystemExit(main())"


def _command(*arguments: str) -> subprocess.CompletedProcess:
    """@brief 用当前解释器调用实际 CLI 入口，不替换生产模型或目录加载器。"""
    return subprocess.run([sys.executable, "-c", CLI_ENTRY, *map(str, arguments)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)




def test_generate_map_command_writes_the_real_library_bytes(tmp_path) -> None:
    """@brief 实际命令发布字节与真实生成库逐字节相等并恰有一个末尾换行。"""
    from worldmock.catalog.loader import parse_catalog_model
    from worldmock.catalog.mapgen import generate_map, map_bytes
    from worldmock.catalog.models import LocationsFile

    source = ENVIRONMENT_ROOT / "catalog/locations.json"
    locations = parse_catalog_model(json.loads(source.read_bytes()), LocationsFile)
    output = tmp_path / "map.json"
    result = _command("environment", "generate-map", "--locations", source, "--seed", "17", "--output", output)
    assert result.returncode == 0, result.stderr
    expected = map_bytes(generate_map([row.location_id for row in locations.locations], 17))
    assert output.read_bytes() == expected and expected.endswith(b"\n") and not expected.endswith(b"\n\n")
    assert json.loads(result.stdout)["seed"] == 17


def test_generate_map_check_preserves_matching_and_mismatched_output(tmp_path) -> None:
    """@brief 检查只比较发布字节，失配打印登记码且不会修补目标。"""
    source, output = ENVIRONMENT_ROOT / "catalog/locations.json", tmp_path / "map.json"
    arguments = ("environment", "generate-map", "--locations", source, "--seed", "17", "--output", output)
    result = _command(*arguments)
    assert result.returncode == 0, result.stderr
    expected = output.read_bytes()
    matching = _command(*arguments, "--check")
    assert matching.returncode == 0 and output.read_bytes() == expected, matching.stderr
    changed = expected + b" "
    output.write_bytes(changed)
    mismatched = _command(*arguments, "--check")
    assert mismatched.returncode == 1 and "MAP_NOT_REGENERATED" in mismatched.stderr
    assert output.read_bytes() == changed


def test_pure_cli_validation_and_runtime_imports_do_not_load_the_engine_or_as2() -> None:
    """@brief 纯命令与恢复输入类型收集不导入完整引擎或 AgentSociety2。"""
    script = (
        "import sys; from worldmock.cli import build_parser; import worldmock.validation; import worldmock.runtime; "
        "build_parser(); forbidden=('worldmock.life.engine','worldmock.life.engine_context',"
        "'worldmock.life.read_query_runtime'); "
        "assert not set(forbidden)&set(sys.modules); "
        "assert not any(name.startswith('agentsociety2') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_pytest_collection_of_pure_nodes_does_not_load_engine_or_as2(tmp_path) -> None:
    """@brief 真实 pytest 收集命令同时核对模块清单，不能用静态源码检查冒充隔离。"""
    arguments = ["--collect-only", "tests/system/cli/test_cli.py", "tests/system/environment/test_validation.py",
                 "tests/unit/runtime/test_run_shell.py::test_run_shell_allows_only_status_file", "-q",
                 "--environment-profile", "dev", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "collection")]
    script = (
        "import sys,pytest; code=pytest.main(" + repr(arguments) + "); "
        "assert code==0; forbidden=('worldmock.life.engine','worldmock.life.engine_context',"
        "'worldmock.life.read_query_runtime'); assert not set(forbidden)&set(sys.modules); "
        "assert not any(name.startswith('agentsociety2') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generate_map_check_uses_the_real_cli_dispatch(tmp_path, caplog) -> None:
    """@brief 进程内真实命令拒绝失配，用于证明注入 CLI 源码的变异确实被行为断言杀死。"""
    from worldmock.cli import main

    source, output = ENVIRONMENT_ROOT / "catalog/locations.json", tmp_path / "map.json"
    arguments = ["environment", "generate-map", "--locations", str(source), "--seed", "17", "--output", str(output)]
    assert main(arguments) == 0
    changed = output.read_bytes() + b" "
    output.write_bytes(changed)
    assert main([*arguments, "--check"]) == 1
    assert "MAP_NOT_REGENERATED" in caplog.text and output.read_bytes() == changed
