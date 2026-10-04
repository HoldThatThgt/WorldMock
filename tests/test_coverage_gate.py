"""scripts/coverage_gate.py 门禁判定逻辑的单元测试。

@file     test_coverage_gate.py
@brief    用合成 coverage JSON 与合成 Python 源文件（tmp_path）验证门禁核心判定
          逻辑本身；不真跑全量 pytest（那是门禁实际使用时的行为）。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_gate_module():
    """@brief 按文件路径加载 scripts/coverage_gate.py（scripts/ 非包，沿用项目先例）。"""
    module_path = REPO_ROOT / "scripts" / "coverage_gate.py"
    spec = importlib.util.spec_from_file_location("coverage_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass 解析字符串注解依赖 sys.modules 中同名模块，须先注册再执行。
    sys.modules["coverage_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate_module()


def make_file_entry(covered_branches: int, missing_branches: int, missing_lines=()):
    """@brief 构造结构对齐 coverage 7.x json 输出的单文件条目。"""
    return {
        "executed_lines": [],
        "missing_lines": list(missing_lines),
        "excluded_lines": [],
        "summary": {
            "covered_lines": 0,
            "num_statements": 0,
            "percent_covered": 0.0,
            "missing_lines": len(list(missing_lines)),
            "excluded_lines": 0,
            "num_branches": covered_branches + missing_branches,
            "num_partial_branches": 0,
            "covered_branches": covered_branches,
            "missing_branches": missing_branches,
        },
    }


def make_cov_data(files: dict) -> dict:
    """@brief 构造 coverage JSON 顶层结构。"""
    return {"meta": {"format": 3, "version": "7.15.4"}, "files": files, "totals": {}}


def write_module(base: Path, rel_path: str, source: str) -> None:
    """@brief 在临时目录下写入合成生产源文件。"""
    path = base / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_branch_pass_and_fail_states():
    """@brief 分支覆盖率达标与不达标两种判定状态。"""
    passing = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/a.py": make_file_entry(90, 10, [])})
    )
    assert passing.percent == 90.0
    assert gate.judge_gate(passing.percent, 0) is True

    failing = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/a.py": make_file_entry(60, 40, [])})
    )
    assert failing.percent == 60.0
    assert gate.judge_gate(failing.percent, 0) is False

    # 覆盖率达标的分支若存在未覆盖接口，判定仍为不通过。
    assert gate.judge_gate(passing.percent, 1) is False
    # 无任何分支数据时按 0% 失败关闭。
    empty = gate.compute_branch_summary(make_cov_data({}))
    assert empty.percent == 0.0
    assert gate.judge_gate(empty.percent, 0) is False


def test_branch_threshold_boundary():
    """@brief 阈值边界：84.99 不达标，85.0 恰好达标（比较取两位小数后值）。"""
    below = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/a.py": make_file_entry(8499, 1501, [])})
    )
    assert below.percent == 84.99
    assert gate.judge_gate(below.percent, 0) is False

    exact = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/a.py": make_file_entry(8500, 1500, [])})
    )
    assert exact.percent == 85.0
    assert gate.judge_gate(exact.percent, 0) is True


def test_per_file_branch_stat_without_branches_is_none():
    """@brief 无分支文件的分文件覆盖率记为 None（报告展示 n/a），不参与百分比。"""
    stats = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/plain.py": make_file_entry(0, 0, [])})
    )
    assert stats.per_file[0].percent is None


def test_uncovered_public_interface_detection(tmp_path):
    """@brief 模块级函数与类方法的未执行行均被检出，行号为 def 起始行。"""
    source = (
        '"""Synthetic module."""\n'
        "\n"
        "\n"
        "def public_api():\n"          # line 4
        "    return 1\n"               # line 5 (missing)
        "\n"
        "\n"
        "def _private_helper():\n"     # line 8
        "    return 2\n"               # line 9 (missing)
        "\n"
        "\n"
        "class Service:\n"             # line 12
        "    def __init__(self):\n"    # line 13
        "        self.value = 0\n"     # line 14 (missing)
        "\n"
        "    def run(self):\n"         # line 16
        "        return self.value\n"  # line 17 (missing)
    )
    write_module(tmp_path, "custom/agents/synthetic_agent.py", source)
    cov = make_cov_data(
        {"custom/agents/synthetic_agent.py": make_file_entry(1, 1, [5, 9, 14, 17])}
    )
    gaps = gate.find_uncovered_interfaces(tmp_path, ("custom",), cov)
    located = {(gap.file, gap.line, gap.name, gap.kind) for gap in gaps}
    assert located == {
        ("custom/agents/synthetic_agent.py", 4, "public_api", "function"),
        ("custom/agents/synthetic_agent.py", 16, "run", "method"),
    }


def test_private_and_dunder_functions_not_counted(tmp_path):
    """@brief 下划线私有函数与 dunder 方法不计入接口覆盖，已执行接口不产生缺口。"""
    source = (
        "def public_ok():\n"           # line 1（整函数已执行）
        "    return 1\n"               # line 2
        "\n"
        "def _internal():\n"           # line 4
        "    return 2\n"               # line 5 (missing)
        "\n"
        "class Thing:\n"               # line 7
        "    def __repr__(self):\n"    # line 8
        "        return 'Thing'\n"     # line 9 (missing)
    )
    write_module(tmp_path, "custom/envs/synthetic_env.py", source)
    cov = make_cov_data(
        {"custom/envs/synthetic_env.py": make_file_entry(0, 2, [5, 9])}
    )
    gaps = gate.find_uncovered_interfaces(tmp_path, ("custom",), cov)
    assert gaps == []


def test_pure_stub_counted_as_uncovered(tmp_path):
    """@brief 纯 stub（仅 ... 体）函数体未执行时计为未覆盖接口。"""
    source = "def stub_api():\n    ...\n"
    write_module(tmp_path, "src/worldmock/synthetic_stub.py", source)
    cov = make_cov_data(
        {"src/worldmock/synthetic_stub.py": make_file_entry(0, 0, [2])}
    )
    gaps = gate.find_uncovered_interfaces(tmp_path, ("src/worldmock",), cov)
    assert [(gap.file, gap.line, gap.name) for gap in gaps] == [
        ("src/worldmock/synthetic_stub.py", 1, "stub_api")
    ]


def test_async_def_collected_as_interface(tmp_path):
    """@brief 顶层 async def 与同步 def 同等计入公共接口。"""
    source = "async def fetch_data():\n    return 42\n"
    write_module(tmp_path, "src/worldmock/synthetic_async.py", source)
    cov = make_cov_data(
        {"src/worldmock/synthetic_async.py": make_file_entry(0, 0, [2])}
    )
    gaps = gate.find_uncovered_interfaces(tmp_path, ("src/worldmock",), cov)
    assert [(gap.name, gap.kind) for gap in gaps] == [("fetch_data", "function")]


def test_empty_file_and_unmeasured_file_handling(tmp_path):
    """@brief 空文件解析不崩溃且无接口；未被 coverage 测量的文件其公共接口全部未覆盖。"""
    write_module(tmp_path, "src/worldmock/empty_module.py", "")
    write_module(
        tmp_path, "src/worldmock/unmeasured.py", "def never_measured():\n    return 1\n"
    )
    cov = make_cov_data({"src/worldmock/empty_module.py": make_file_entry(0, 0, [])})
    gaps = gate.find_uncovered_interfaces(tmp_path, ("src/worldmock",), cov)
    assert [(gap.file, gap.name) for gap in gaps] == [
        ("src/worldmock/unmeasured.py", "never_measured")
    ]


def test_build_report_structure():
    """@brief 报告含生成命令、证据 manifest、整体与分文件分支数据、缺口清单与判定位。"""
    branch = gate.compute_branch_summary(
        make_cov_data({"src/worldmock/a.py": make_file_entry(90, 10, [])})
    )
    gaps = [gate.InterfaceGap("src/worldmock/a.py", 3, "foo", "function")]
    command = "python -m coverage run -m pytest -q"
    snapshot_id = "test-snapshot-001"
    manifest_hash = "abc123def456"
    source_identity = "commit-hash-789"
    report = gate.build_report(
        branch, gaps, False, command, snapshot_id, manifest_hash, source_identity
    )
    assert report["command"] == command
    assert report["generated_at"]
    assert report["evidence_manifest"]["snapshot_id"] == snapshot_id
    assert report["evidence_manifest"]["manifest_hash"] == manifest_hash
    assert report["evidence_manifest"]["source_identity"] == source_identity
    assert report["branch_coverage"]["percent"] == 90.0
    assert report["branch_coverage"]["passed"] is True
    assert report["per_file_branch"][0]["file"] == "src/worldmock/a.py"
    assert report["uncovered_public_interfaces"] == [
        {"file": "src/worldmock/a.py", "line": 3, "name": "foo", "kind": "function"}
    ]
    assert report["passed"] is False


def test_write_report_roundtrip(tmp_path):
    """@brief 报告可写入磁盘并原样读回，父目录自动创建。"""
    branch = gate.compute_branch_summary(make_cov_data({}))
    report = gate.build_report(
        branch, [], True, "cmd", "snap-id", "hash-abc", "source-xyz"
    )
    target = tmp_path / "nested" / "coverage-report.json"
    gate.write_report(report, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == report
    assert loaded["passed"] is True
    assert loaded["branch_coverage"]["percent"] == 0.0
    assert loaded["evidence_manifest"]["snapshot_id"] == "snap-id"
    assert loaded["evidence_manifest"]["manifest_hash"] == "hash-abc"
    assert loaded["evidence_manifest"]["source_identity"] == "source-xyz"


def test_shards_cover_all_files_once_and_keep_directory_order(tmp_path):
    for name in ("tests/a/test_b.py", "tests/a/test_a.py", "tests/b/test_c.py", "tests/test_root.py"):
        write_module(tmp_path, name, "def test_ok():\n    assert True\n")
    shards = gate.test_shards(tmp_path, 3)
    files = [name for shard in shards for name in shard]
    assert files == sorted(files)
    assert len(files) == len(set(files)) == 4
    assert all(shard for shard in shards)
    with pytest.raises(ValueError, match="positive integer"):
        gate.test_shards(tmp_path, 0)


def test_real_shards_use_current_python_and_report_skipped_tests(tmp_path, monkeypatch):
    write_module(tmp_path, "tests/a/test_pass.py", "def test_pass():\n    assert True\n")
    skip = "sk" + "ip"
    write_module(tmp_path, "tests/b/test_skip.py", f"import pytest\ndef test_skip():\n    pytest.{skip}('missing')\n")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    code, results = gate.run_test_suite(data, 2)
    assert code == 0
    assert results == {"tests": 2, "failures": 0, "errors": 0, "skipped": 1}
    assert (data / ".coverage").is_file()
    assert gate.TEST_COMMAND[:3] == (sys.executable, "-m", "coverage")
    assert "uv" not in gate.TEST_COMMAND


@pytest.mark.parametrize("results", [
    {"tests": 1, "failures": 0, "errors": 0, "skipped": 1},
    {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
    {"tests": 1, "failures": 1, "errors": 0, "skipped": 0},
])
def test_main_never_passes_for_skipped_empty_or_failed_tests(tmp_path, monkeypatch, results):
    monkeypatch.setattr(gate, "run_test_suite", lambda directory, shards: (0, results))
    monkeypatch.setattr(gate, "export_coverage_json", lambda path: make_cov_data({
        "src/worldmock/a.py": make_file_entry(90, 10, [])}))
    monkeypatch.setattr(gate, "find_uncovered_interfaces", lambda *args: [])
    monkeypatch.setattr(gate, "REPORT_PATH", tmp_path / "report.json")
    assert gate.main(["--shards", "2"]) == 1
    report = json.loads(gate.REPORT_PATH.read_text())
    assert report["passed"] is False
    assert report["test_results"] == results
    assert report["shards"] == 2


def test_missing_junit_report_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        gate.junit_summary([tmp_path / "missing.xml"])
