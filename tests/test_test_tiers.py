"""@brief 三层选择与调度的行为反例：完整节点身份比数量更严格。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import run_tests
from tests.tier_support import classify, module_for_path, selected


def _record(name="test_plain", markers=(), imports=(), file="tests/test_sample.py"):
    return {"nodeid": f"{file}::{name}", "file": file, "markers": list(markers), "imports": list(imports)}


@pytest.mark.parametrize(("source", "fixtures", "expected"), [
    ("def test_any(): pass", ["renamed_engine_factory"], set()),
    ("from worldmock.life.engine import LifeEngine as Driver\ndef test_any(): pass", [], set()),
    ("from custom.envs.life_env import LifeEnv\ndef test_any(): pass", [], set()),
    ("from tests.mutation_runner import SourceMutation", [], set()),
    ("from tests.mutation_runner import SourceMutation", ["errands_mutation_baseline"], set()),
    ("from textworld import start\ndef test_any(): pass", [], set()),
    ("from worldmock.life.game import validate_game\ndef test_any(): pass", [], set()),
    ("from tests.integration.test_dayrun_complete_days import DayModel\ndef test_any(): pass", [], set()),
    ("from worldmock.life.engine import LifeEngine as Driver\n"
     "def test_any(): Driver.create(None, None, None)", [], {"engine"}),
    ("from worldmock.life.engine import LifeEngine as Driver\n"
     "def unused(): Driver.create(None, None, None)\n"
     "def test_any(): pass", [], set()),
    ("from textworld import start as load\ndef test_any(): load('game')", [], {"external"}),
    ("import subprocess as process\ndef test_any(): process.run(['node', 'test.cjs'])", [], {"external"}),
    ("import subprocess\nfrom pathlib import Path\n"
     "def test_any(): subprocess.run([str(Path('vendor/labelkit/.venv/bin/labelkit'))])", [], {"external"}),
])
def test_new_driver_and_fixture_dependencies_are_classified(tmp_path, source, fixtures, expected):
    path = tmp_path / "test_new_feature.py"
    path.write_text(source, encoding="utf-8")
    assert classify(path, fixtures) == expected


def test_fast_selection_adds_only_direct_changed_engine_dependencies():
    plain = _record()
    engine = _record("engine", ["engine"], ["worldmock.life.body"])
    other = _record("other", ["engine"], ["worldmock.life.engine"])
    changed = ["src/worldmock/life/body.py"]
    assert selected(plain, "fast", changed)
    assert selected(engine, "fast", changed)
    assert not selected(other, "fast", changed)
    assert not selected(engine, "fast")
    for marker in ("mutation", "day_run", "external"):
        assert not selected(_record(marker, ["engine", marker], engine["imports"]), "fast", changed)
    assert not selected(_record("changed_test", ["engine"]), "fast", ["tests/test_sample.py"])


def test_new_source_mutation_execution_is_classified_without_hand_written_marker(tmp_path):
    path = tmp_path / "test_review.py"
    path.write_text("from tests.mutation_runner import run_behavior as execute\n"
                    "def test_format():\n    assert mutation.symbol\n"
                    "def test_review():\n    execute((), directory, mutation)\n", encoding="utf-8")
    assert classify(path, [], "test_review") == {"mutation"}
    assert classify(path, [], "test_format") == set()


@pytest.mark.parametrize("name, expected", [
    ("test_import_only", set()), ("test_indirect", {"engine"}), ("test_factory", {"engine"}),
    ("test_unused_factory", set()), ("test_actual_day", {"engine", "day_run"}),
    ("test_partial_day", {"engine"}), ("test_mutation", {"mutation"}), ("test_baseline", set()),
])
def test_actual_fixture_objects_helpers_and_returned_factories_are_followed_per_test(tmp_path, name, expected):
    path = tmp_path / "test_actual_calls.py"
    path.write_text(
        "def build():\n    from worldmock.life.engine import LifeEngine as Driver\n"
        "    return Driver.create(None, None, None)\n"
        "def unrelated_engine_helper():\n    return build()\n"
        "def ordinary():\n    return build()\n"
        "def factory():\n    def create():\n        return build()\n    return create\n"
        "def execute(nodes, directory, mutation=None):\n"
        "    from tests.mutation_runner import run_behavior as review\n"
        "    return review(nodes, directory, mutation, None)\n"
        "def baseline():\n    return execute((), None)\n"
        "def test_import_only():\n    assert True\n"
        "def test_indirect(unusual):\n    assert unusual\n"
        "def test_factory(unusual):\n    alias = unusual\n    alias()\n"
        "def test_unused_factory(unusual):\n    assert callable(unusual)\n"
        "def test_actual_day(unusual):\n    driver = unusual()\n"
        "    while driver.context.state.current_time < driver.context.state.end_time:\n"
        "        driver.advance(1, None)\n"
        "def test_partial_day(unusual):\n    driver = unusual()\n    driver.advance(1, None)\n"
        "def test_mutation(mutation):\n    execute((), None, mutation)\n"
        "def test_baseline(mutation_baseline):\n    assert mutation_baseline\n", encoding="utf-8")
    namespace = {"__name__": "actual_tier_calls"}
    exec(compile(path.read_text("utf-8"), str(path), "exec"), namespace)
    functions = {"unusual": namespace["ordinary" if name == "test_indirect" else "factory"],
                 "mutation_baseline": namespace["baseline"]}
    fixtures = (["unusual"] if name in {"test_indirect", "test_factory", "test_unused_factory",
                                         "test_actual_day", "test_partial_day"} else
                ["mutation_baseline"] if name == "test_baseline" else [])
    assert classify(path, fixtures, name, fixture_functions=functions, test_function=namespace[name]) == expected


def test_real_pytest_collection_classifies_each_test_and_actual_fixture_closure(tmp_path):
    source_root = run_tests.ROOT
    (tmp_path / "tests").mkdir()
    for name in ("__init__.py", "tier_support.py", "frozen_inputs.py", "engine_fixture.py"):
        shutil.copy2(source_root / "tests" / name, tmp_path / "tests" / name)
    shutil.copy2(source_root / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n', "utf-8")
    package = tmp_path / "src/worldmock/life"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("", "utf-8")
    (package / "__init__.py").write_text("", "utf-8")
    (package / "engine.py").write_text(
        "class LifeEngine:\n    @classmethod\n    def create(cls, *args):\n        raise AssertionError('collect only')\n",
        "utf-8")
    (tmp_path / "tests/test_calls.py").write_text(
        "import pytest\nfrom worldmock.life.engine import LifeEngine as Creator\n"
        "def unused():\n    return Creator.create()\n"
        "def helper():\n    return Creator.create()\n"
        "@pytest.fixture(name='arbitrary')\ndef ordinary():\n    return helper()\n"
        "@pytest.fixture\ndef indirect(arbitrary):\n    return arbitrary\n"
        "def test_plain():\n    assert True\n"
        "def test_direct():\n    alias = Creator\n    alias.create()\n"
        "def test_fixture(indirect):\n    assert indirect\n"
        "def test_day():\n    driver = helper()\n"
        "    while driver.context.state.current_time < driver.context.state.end_time:\n"
        "        driver.advance(1, None)\n"
        "def stage_last_minute(driver):\n"
        "    driver.context.state.current_time = driver.context.state.end_time - 60\n"
        "def test_short_export_window():\n    driver = helper()\n    stage_last_minute(driver)\n"
        "    while driver.context.state.current_time < driver.context.state.end_time:\n"
        "        driver.advance(1, None)\n"
        "@pytest.mark.parametrize('builder', [Creator.create])\n"
        "def test_parameter_called(builder):\n    builder()\n"
        "@pytest.mark.parametrize('builder', [Creator.create])\n"
        "def test_parameter_unused(builder):\n    assert callable(builder)\n"
        "@pytest.fixture(params=[Creator.create])\n"
        "def parameter_fixture(request):\n    return request.param()\n"
        "@pytest.fixture\ndef indirect_parameter(request):\n    return request.param()\n"
        "def test_fixture_parameter(parameter_fixture):\n    assert parameter_fixture\n"
        "@pytest.mark.parametrize('indirect_parameter', [Creator.create], indirect=True)\n"
        "def test_indirect_parameter(indirect_parameter):\n    assert indirect_parameter\n"
        "class TestMethods:\n    def helper(self):\n        return Creator.create()\n"
        "    def test_bound_helper(self):\n        self.helper()\n", "utf-8")
    manifest = tmp_path / "collection.json"
    process = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                              f"--worldmock-manifest={manifest}"], cwd=tmp_path,
                             capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    records = {row["nodeid"].rsplit("::", 1)[-1]: set(row["markers"])
               for row in json.loads(manifest.read_text("utf-8"))}
    assert records == {"test_plain": set(), "test_direct": {"engine"}, "test_fixture": {"engine"},
                       "test_day": {"engine", "day_run"}, "test_short_export_window": {"engine"},
                       "test_parameter_called[create]": {"engine"},
                       "test_parameter_unused[create]": set(), "test_fixture_parameter[create]": {"engine"},
                       "test_indirect_parameter[create]": {"engine"}, "test_bound_helper": {"engine"}}


def test_standard_and_full_sets_preserve_external_and_long_reviews():
    records = [_record(marker, [marker]) for marker in ("engine", "mutation", "day_run", "external")]
    assert [row["nodeid"] for row in records if selected(row, "standard")] == [
        records[0]["nodeid"], records[3]["nodeid"]]
    assert all(selected(row, "full") for row in records)
    with pytest.raises(ValueError, match="Unknown test tier"):
        selected(records[0], "other")
    assert module_for_path("src/worldmock/life/__init__.py") == "worldmock.life"
    assert module_for_path("custom/envs/life_env.py") == "custom.envs.life_env"


def test_duration_balancing_keeps_new_files_and_file_nodes_together():
    records = [_record("first", file="tests/test_slow.py"), _record("second", file="tests/test_slow.py"),
               _record(file="tests/test_medium.py"), _record(file="tests/test_new.py")]
    buckets = run_tests.shards(records, 2, {"tests/test_slow.py": 100, "tests/test_medium.py": 50})
    assert {item for bucket in buckets for item in bucket["nodes"]} == {row["nodeid"] for row in records}
    assert len([bucket for bucket in buckets if "tests/test_slow.py" in bucket["files"]]) == 1
    assert sorted(bucket["weight"] for bucket in buckets) == [51, 100]
    with pytest.raises(ValueError, match="positive"):
        run_tests.shards(records, 0, {})


def _receipt(nodes):
    nodes = tuple(nodes)
    phases = {name: {"outcome": "passed", "duration": 0.1} for name in ("setup", "call", "teardown")}
    return {"exit_code": 0, "collected": list(nodes),
            "tests": {name: {"duration": 0.3, "phases": dict(phases)} for name in nodes}}


def _result(nodes):
    return {"index": 0, "returncode": 0, "receipt": _receipt(nodes), "expected": list(nodes), "log": "worker.log"}


@pytest.mark.parametrize("damage", ["worker", "receipt", "missing", "extra", "duplicate", "collection", "teardown",
                                    "setup", "call"])
def test_incomplete_worker_evidence_is_rejected(damage):
    result = _result(["a", "b"])
    if damage == "worker":
        result["returncode"] = 1
    elif damage == "receipt":
        result["receipt"] = None
    elif damage == "missing":
        result["receipt"]["tests"].pop("b")
    elif damage == "extra":
        result["receipt"]["tests"]["c"] = result["receipt"]["tests"]["b"]
    elif damage == "duplicate":
        with pytest.raises(RuntimeError, match="complete selected collection"):
            run_tests.validate_receipts(["a", "b"], [result, result])
        return
    elif damage == "collection":
        result["receipt"]["collected"] = ["a", "c"]
    elif damage in {"teardown", "setup", "call"}:
        result["receipt"]["tests"]["a"]["phases"].pop(damage)
    with pytest.raises(RuntimeError):
        run_tests.validate_receipts(["a", "b"], [result])


def test_equal_node_count_cannot_hide_a_wrong_test_or_failed_phase():
    result = _result(["a", "b"])
    result["receipt"]["tests"]["c"] = result["receipt"]["tests"].pop("b")
    with pytest.raises(RuntimeError, match="complete selected collection"):
        run_tests.validate_receipts(["a", "b"], [result])
    result = _result(["a", "b"])
    result["receipt"]["tests"]["a"]["phases"]["call"]["outcome"] = "failed"
    with pytest.raises(RuntimeError, match="contains a failure"):
        run_tests.validate_receipts(["a", "b"], [result])


def test_duration_report_covers_phases_slow_fast_tests_and_file_totals():
    records = [_record("plain"), _record("world", ["engine"]), _record("long", ["day_run"])]
    receipt = _receipt(row["nodeid"] for row in records)
    receipt["tests"][records[0]["nodeid"]]["duration"] = 2.0
    report = run_tests.duration_report(records, receipt["tests"], 3.0, {"workers": 6}, {"wall_seconds": 1.5})
    assert len(report["nodes"]) == 3
    assert report["files"] == {"tests/test_sample.py": pytest.approx(2.6)}
    assert report["standard_total_seconds"] == pytest.approx(2.3)
    assert report["fast_over_one_second"] == [{"nodeid": records[0]["nodeid"], "seconds": 2.0}]
    assert set(report["nodes"][0]["phases"]) == {"setup", "call", "teardown"}
    assert report["standard_wall_seconds"] == 1.5


@pytest.mark.parametrize("damage", ["missing", "source", "collection", "workers", "none"])
def test_standard_wall_time_reuses_only_the_same_verified_run(tmp_path, monkeypatch, damage):
    records = [_record(), _record("mutation", ["mutation"])]
    path = tmp_path / ".work/test-runs/standard-summary.json"
    path.parent.mkdir(parents=True)
    matching = {"source_sha256": "source", "collection_sha256": run_tests.collection_digest(records[:1]),
                "environment": run_tests.environment(6), "wall_seconds": 10}
    candidate = json.loads(json.dumps(matching))
    if damage == "source":
        candidate["source_sha256"] = "different"
    elif damage == "collection":
        candidate["collection_sha256"] = "different"
    elif damage == "workers":
        candidate["environment"]["workers"] = 1
    if damage != "missing":
        path.write_text(json.dumps(candidate), encoding="utf-8")
    calls = []

    def measure(args):
        calls.append(args)
        path.write_text(json.dumps(matching), encoding="utf-8")

    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "run", measure)
    result = run_tests.standard_reference(Namespace(workers=6, base="HEAD"), records, "source")
    assert result["wall_seconds"] == 10
    assert len(calls) == (damage != "none")
    if calls:
        assert calls[0].tier == "standard"


def test_changed_engine_time_is_separate_from_default_fast_reference():
    records = [_record(), _record("world", ["engine"], ["worldmock.life.body"])]
    tests = _receipt(row["nodeid"] for row in records)["tests"]
    report = run_tests._summary(Namespace(tier="fast", workers=6), records, tests, [], [], 200)
    assert report["changed_engine_nodes"] == [records[1]["nodeid"]]
    assert report["changed_engine_total_seconds"] == pytest.approx(0.3)
    assert report["default_test_total_seconds"] == pytest.approx(0.3)
    assert report["reference_target_exceeded"] is None


def test_input_identity_changes_when_source_changes_and_skips_are_reported(tmp_path, monkeypatch):
    for file in ("conftest.py", "pyproject.toml", "uv.lock"):
        (tmp_path / file).write_text("initial", encoding="utf-8")
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    digest = run_tests.source_digest()
    run_tests.require_unchanged_input(digest)
    (tmp_path / "conftest.py").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="input changed"):
        run_tests.require_unchanged_input(digest)
    record = _record()
    receipt = _receipt([record["nodeid"]])
    receipt["tests"][record["nodeid"]]["phases"]["call"]["outcome"] = "skipped"
    summary = run_tests._summary(Namespace(tier="fast", workers=6), [record], receipt["tests"], [], [], 1)
    assert summary["skipped_nodes"] == [record["nodeid"]]
    assert summary["reference_target_exceeded"] is None


def test_changed_selection_includes_staged_unstaged_and_untracked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    commands = [["config", "user.email", "test@example.invalid"], ["config", "user.name", "test"]]
    for arguments in commands:
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("first", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("staged", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("unstaged", encoding="utf-8")
    (tmp_path / "new.py").write_text("new", encoding="utf-8")
    assert run_tests.changed_paths("HEAD") == ["new.py", "tracked.py"]
    with pytest.raises(RuntimeError, match="Git change selection failed"):
        run_tests.changed_paths("missing-ref")


def test_real_serial_and_parallel_workers_return_identical_complete_nodes(tmp_path, monkeypatch):
    source_root = run_tests.ROOT
    (tmp_path / "tests").mkdir()
    for name in ("__init__.py", "tier_support.py", "frozen_inputs.py", "engine_fixture.py"):
        shutil.copy2(source_root / "tests" / name, tmp_path / "tests" / name)
    shutil.copy2(source_root / "conftest.py", tmp_path / "conftest.py")
    with (tmp_path / "conftest.py").open("a", encoding="utf-8") as handle:
        handle.write("\n@pytest.fixture\ndef shared_value():\n    return 42\n")
    records = []
    for name in ("alpha", "beta"):
        file = f"tests/test_{name}.py"
        (tmp_path / file).write_text(
            "from pathlib import Path\n"
            "def test_shared(shared_value, tmp_path):\n"
            "    assert shared_value == 42\n"
            "    assert not tmp_path.is_relative_to(Path.cwd())\n", encoding="utf-8")
        records.append(_record("test_shared", file=file))
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    expected = [row["nodeid"] for row in records]
    for count in (1, 2):
        directory = tmp_path / f"workers-{count}"
        directory.mkdir()
        buckets = run_tests.shards(records, count, {})
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(run_tests.run_shard, index, bucket, directory, False)
                       for index, bucket in enumerate(buckets)]
            results = [future.result() for future in futures]
        tests = run_tests.validate_receipts(expected, results)
        assert set(tests) == set(expected)
        assert all(value["phases"]["call"]["outcome"] == "passed" for value in tests.values())


def test_missing_coverage_report_cannot_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(run_tests, "_run", lambda *args, **kwargs: Namespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="Coverage combination failed"):
        run_tests.coverage_gate(tmp_path)


def test_real_parallel_coverage_is_combined_for_the_current_normal_source(tmp_path, monkeypatch):
    source_root = run_tests.ROOT
    for name in ("tests", "scripts", "src/worldmock", "docs/dev"):
        (tmp_path / name).mkdir(parents=True)
    for name in ("__init__.py", "tier_support.py", "frozen_inputs.py", "engine_fixture.py"):
        shutil.copy2(source_root / "tests" / name, tmp_path / "tests" / name)
    shutil.copy2(source_root / "conftest.py", tmp_path / "conftest.py")
    shutil.copy2(source_root / "scripts/coverage_gate.py", tmp_path / "scripts/coverage_gate.py")
    (tmp_path / "src/worldmock/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src/worldmock/sample.py").write_text(
        "def choose(value):\n    if value:\n        return 1\n    return 0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
        '[tool.coverage.run]\nbranch = true\nsource = ["src/worldmock"]\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    records = []
    for name, value in (("true", True), ("false", False)):
        file = f"tests/test_{name}.py"
        (tmp_path / file).write_text("from worldmock.sample import choose\n"
                                    f"def test_branch():\n    assert choose({value}) == {int(value)}\n",
                                    encoding="utf-8")
        records.append(_record("test_branch", file=file))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for arguments in (["config", "user.email", "test@example.invalid"], ["config", "user.name", "test"],
                      ["add", "."], ["commit", "-qm", "coverage fixture"]):
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True)
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "DURATIONS", tmp_path / "docs/dev/test-durations.json")
    monkeypatch.setattr(run_tests, "duration_weights", lambda: {})
    assert run_tests.run(Namespace(tier="full", workers=2, base="HEAD")) == 0
    path = next((tmp_path / ".work/test-runs").glob("*/coverage.json"))
    directory = path.parent
    coverage = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
    sample = coverage["files"]["src/worldmock/sample.py"]["summary"]
    assert sample["covered_branches"] == 2
    assert sample["missing_branches"] == 0
    assert json.loads((tmp_path / "docs/dev/coverage-report.json").read_text("utf-8"))["passed"]
    duration = json.loads(run_tests.DURATIONS.read_text("utf-8"))
    assert {row["nodeid"] for row in duration["nodes"]} == {row["nodeid"] for row in records}
    assert duration["standard_wall_seconds"] > 0
    assert duration["standard_reference"]["source_sha256"] == run_tests.source_digest()
