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


def test_gate_uses_the_current_interpreter_and_the_shared_full_runner(monkeypatch):
    """@brief 没有 uv 的运行环境也调用同一当前解释器全量入口。"""
    calls = []
    monkeypatch.setattr(gate.subprocess, "call", lambda command, **kwargs: calls.append((command, kwargs)) or 7)
    assert gate.run_test_suite() == 7
    assert calls == [(gate.TEST_COMMAND, {"cwd": gate.REPO_ROOT})]
    assert gate.TEST_COMMAND == (sys.executable, "scripts/run_tests.py", "--tier", "full", "--workers", "6")
    assert gate.main(["--shards", "3"]) == 7
    assert calls[-1][0] == (*gate.TEST_COMMAND[:-1], "3")


def test_coverage_entrypoint_forwards_the_explicit_environment_to_the_full_runner(monkeypatch):
    """@brief 覆盖入口必须把调用方指定的环境交给实际全量测试入口。"""
    calls = []
    monkeypatch.setattr(gate.subprocess, "call", lambda command, **kwargs: calls.append(command) or 7)
    assert gate.main(["--environment-root", "../dataset/environment", "--shards", "3"]) == 7
    assert calls == [(*gate.TEST_COMMAND[:-1], "3", "--environment-root", "../dataset/environment")]


def test_missing_coverage_json_fails_closed(tmp_path, monkeypatch):
    """@brief 覆盖数据缺失不能读取旧结果或返回成功。"""
    assert gate.main(["--from-json", str(tmp_path / "missing.json")]) == 1


def test_missing_source_identity_is_an_error(monkeypatch):
    """@brief 证据绑定不能用 unknown 掩盖 Git 身份读取失败。"""
    from types import SimpleNamespace

    monkeypatch.setattr(gate.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="missing repository"))
    with pytest.raises(RuntimeError, match="Source identity lookup failed"):
        gate.get_git_commit_hash()


def _json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def full_run_files(tmp_path, monkeypatch):
    """@brief 真实落盘同一次运行的 collection、summary、选择和完成回执。"""
    from scripts import run_tests

    for name in ("conftest.py", "pyproject.toml", "uv.lock", ".gitattributes"):
        (tmp_path / name).write_text("frozen", encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(gate, "get_git_commit_hash", lambda: "actual-commit")
    directory = tmp_path / ".work/test-runs/current"
    records = [{"nodeid": "tests/test_actual.py::test_pass", "file": "tests/test_actual.py",
                "markers": [], "imports": []}]
    node = records[0]["nodeid"]
    phases = {name: {"outcome": "passed", "duration": 0.1} for name in ("setup", "call", "teardown")}
    receipt = {"run_id": directory.name, "exit_code": 0, "collected": [node], "tests": {node: {"phases": phases, "duration": 0.3}}}
    summary = {"run_id": directory.name, "tier": "full", "selected_nodes": 1, "workers": 1,
               "collection_sha256": run_tests.collection_digest(records),
               "source_sha256": run_tests.source_digest(), "environment": run_tests.environment(1),
               "shards": [{"index": 0, "run_id": directory.name, "returncode": 0, "wall_seconds": 1, "log": "shard-0/output.log"}]}
    _json_file(directory / "collection.json", {"run_id": directory.name, "records": records})
    _json_file(directory / "summary.json", summary)
    _json_file(directory / "shard-0/selected.json", {"run_id": directory.name, "nodes": [node]})
    _json_file(directory / "shard-0/receipt.json", receipt)
    coverage = {**make_cov_data({"src/worldmock/a.py": make_file_entry(90, 10)}), "run_id": directory.name}
    _json_file(directory / "coverage.json", coverage)
    _bind_artifacts(directory, summary)
    return directory, records, summary, receipt


def _bind_artifacts(directory, summary):
    """固定真实文件字节，在语义反例中仍给出一致的原生产快照。"""
    from scripts import run_tests

    summary["coverage_sha256"] = run_tests.file_sha256(directory / "coverage.json")
    for shard in summary["shards"]:
        target = directory / f"shard-{shard['index']}"
        shard["receipt_sha256"] = run_tests.file_sha256(target / "receipt.json")
        shard["selected_sha256"] = run_tests.file_sha256(target / "selected.json")
    _json_file(directory / "summary.json", summary)


def test_from_json_binds_complete_receipts_and_frozen_inputs(full_run_files):
    directory, _, summary, _ = full_run_files
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == 0
    report = json.loads(gate.REPORT_PATH.read_text("utf-8"))
    assert report["passed"]
    assert report["test_results"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    assert report["shards"] == 1
    assert report["evidence_manifest"]["source_sha256"] == summary["source_sha256"]
    assert report["evidence_manifest"]["collection_sha256"] == summary["collection_sha256"]


def test_coverage_receipts_require_the_same_external_environment_bytes(full_run_files):
    """@brief 外部环境的全量回执不能套到默认环境或修改后的外部环境上。"""
    from scripts import run_tests

    directory, _, summary, _ = full_run_files
    environment = gate.REPO_ROOT / "environment"
    for name in ("catalog", "corpora", "scenarios"):
        (environment / name).mkdir(parents=True)
        (environment / name / "input.json").write_text(name, encoding="utf-8")
    summary["source_sha256"] = run_tests.source_digest(environment)
    _bind_artifacts(directory, summary)
    command = ["--from-json", str(directory / "coverage.json"), "--environment-root", str(environment)]
    assert gate.main(command) == 0
    assert gate.main(command[:-2]) == 1
    (environment / "corpora/input.json").write_text("changed", encoding="utf-8")
    assert gate.main(command) == 1


@pytest.mark.parametrize("damage", ["skipped", "failed", "setup_error", "empty", "missing_receipt",
                                      "missing_selection", "missing_summary", "missing_collection",
                                      "duplicate", "omitted", "extra", "wrong_tier", "wrong_count",
                                      "wrong_collection", "stale_source", "stale_environment", "empty_shards",
                                      "duplicate_shards", "empty_worker", "worker_failure", "receipt_failure",
                                      "incomplete_phases", "not_run_outcome", "empty_outcome", "null_outcome",
                                      "duplicate_worker_nodes"])
def test_from_json_rejects_skipped_empty_failed_stale_or_incomplete_runs(full_run_files, damage):
    directory, records, summary, receipt = full_run_files
    node = records[0]["nodeid"]
    if damage in {"skipped", "failed", "setup_error"}:
        phase = "setup" if damage == "setup_error" else "call"
        receipt["tests"][node]["phases"][phase]["outcome"] = "skipped" if damage == "skipped" else "failed"
    elif damage in {"not_run_outcome", "empty_outcome", "null_outcome"}:
        receipt["tests"][node]["phases"]["call"]["outcome"] = {
            "not_run_outcome": "not-run", "empty_outcome": "", "null_outcome": None}[damage]
    elif damage == "duplicate_worker_nodes":
        receipt["collected"] = [node, node]
        _json_file(directory / "shard-0/selected.json", {"run_id": directory.name, "nodes": [node, node]})
    elif damage == "empty":
        records.clear()
    elif damage == "duplicate":
        records.append(records[0])
    elif damage == "omitted":
        receipt["tests"].clear()
    elif damage == "extra":
        receipt["tests"]["tests/test_other.py::test_other"] = receipt["tests"][node]
    elif damage == "wrong_tier":
        summary["tier"] = "standard"
    elif damage == "wrong_count":
        summary["selected_nodes"] = 0
    elif damage == "wrong_collection":
        summary["collection_sha256"] = "different"
    elif damage == "stale_source":
        (gate.REPO_ROOT / "uv.lock").write_text("changed", encoding="utf-8")
    elif damage == "stale_environment":
        summary["environment"]["python"] = "different"
    elif damage == "empty_shards":
        summary["shards"].clear()
    elif damage == "duplicate_shards":
        summary["shards"].append(summary["shards"][0])
    elif damage == "worker_failure":
        summary["shards"][0]["returncode"] = 1
    elif damage == "receipt_failure":
        receipt["exit_code"] = 1
    elif damage == "incomplete_phases":
        del receipt["tests"][node]["phases"]["teardown"]
    _json_file(directory / "collection.json", {"run_id": directory.name, "records": records})
    _json_file(directory / "summary.json", summary)
    _json_file(directory / "shard-0/receipt.json", receipt)
    if damage == "empty_worker":
        _json_file(directory / "shard-0/selected.json", {"run_id": directory.name, "nodes": []})
    _bind_artifacts(directory, summary)
    if damage.startswith("missing_"):
        name = damage.removeprefix("missing_")
        relative = {"receipt": "shard-0/receipt", "selection": "shard-0/selected"}.get(name, name)
        (directory / f"{relative}.json").unlink()
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == 1
    if gate.REPORT_PATH.exists():
        report = json.loads(gate.REPORT_PATH.read_text("utf-8"))
        assert report["passed"] is False
        assert report["test_results"]["skipped"] == 1


@pytest.mark.parametrize("artifact", ["coverage.json", "shard-0/receipt.json", "shard-0/selected.json",
                                        "collection.json", "summary.json"])
def test_other_run_artifacts_cannot_replace_current_run_artifacts(full_run_files, artifact):
    """@brief 每个制品只属于本次运行；旧覆盖和回执也不能把实际失败替换成绿色。"""
    import copy
    import shutil

    directory, records, summary, receipt = full_run_files
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == 0
    previous = directory.with_name("previous")
    shutil.copytree(directory, previous)
    old_summary = copy.deepcopy(summary)
    old_summary["run_id"] = previous.name
    old_summary["shards"][0]["run_id"] = previous.name
    old_receipt = copy.deepcopy(receipt)
    old_receipt["run_id"] = previous.name
    old_coverage = json.loads((previous / "coverage.json").read_text("utf-8"))
    old_coverage["run_id"] = previous.name
    _json_file(previous / "coverage.json", old_coverage)
    _json_file(previous / "collection.json", {"run_id": previous.name, "records": records})
    _json_file(previous / "shard-0/receipt.json", old_receipt)
    _json_file(previous / "shard-0/selected.json", {"run_id": previous.name, "nodes": receipt["collected"]})
    _bind_artifacts(previous, old_summary)
    assert gate.main(["--from-json", str(previous / "coverage.json")]) == 0
    if artifact == "coverage.json":
        _json_file(directory / artifact, {**make_cov_data({"a.py": make_file_entry(60, 40)}),
                                         "run_id": directory.name})
    elif artifact == "shard-0/receipt.json":
        receipt["tests"][records[0]["nodeid"]]["phases"]["call"]["outcome"] = "failed"
        _json_file(directory / "shard-0/receipt.json", receipt)
    _bind_artifacts(directory, summary)
    expected = 1 if artifact in {"coverage.json", "shard-0/receipt.json"} else 0
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == expected
    shutil.copyfile(previous / artifact, directory / artifact)
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == 1


@pytest.mark.parametrize("artifact", ["coverage.json", "shard-0/receipt.json", "shard-0/selected.json"])
def test_same_run_artifact_bytes_cannot_change_after_the_summary_was_frozen(full_run_files, artifact):
    directory, _, _, _ = full_run_files
    path = directory / artifact
    value = json.loads(path.read_text("utf-8"))
    if artifact == "coverage.json":
        value["files"] = {"a.py": make_file_entry(100, 0)}
    elif artifact == "shard-0/receipt.json":
        node = next(iter(value["tests"]))
        value["tests"][node]["duration"] += 1
    else:
        value["replacement"] = True
    _json_file(path, value)
    assert gate.main(["--from-json", str(directory / "coverage.json")]) == 1
