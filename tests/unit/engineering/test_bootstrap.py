"""@brief 可重建环境的真实来源、哈希锁和安装边界验证。"""

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import bootstrap as entry
from scripts import bootstrap_environment as environment

ROOT = Path(__file__).resolve().parents[3]


def _requirements(path):
    rows = {}
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        for match in re.finditer(r"^([a-z0-9-]+)==([^;\s\\]+)([^\n]*)\n((?:[ \t]+[^\n]*\n?)*)", block, re.M):
            name, version, marker, tail = match.groups()
            rows[(name, version, marker.rstrip(" \\"))] = set(re.findall(r"--hash=(sha256:[a-f0-9]+)", tail))
    return rows


def test_exported_dependency_versions_and_all_hashes_match_original_lock():
    packages = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    originals = {(package["name"], package["version"]): package for package in packages
                 if "registry" in package["source"]}
    rows = _requirements(ROOT / "requirements/lock.txt")
    assert rows
    assert {(name, version) for name, version, marker in rows} == set(originals)
    for (name, version, marker), hashes in rows.items():
        package = originals[name, version]
        artifacts = [*package.get("wheels", []), *([package["sdist"]] if "sdist" in package else [])]
        assert hashes == {artifact["hash"] for artifact in artifacts}
    assert "textworld" not in {name for name, version, marker in rows}
    assert ("tomlkit", "0.15.1") in originals
    assert not {"hatchling", "editables"} & {package["name"] for package in packages}


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_recursive_backend_locks_do_not_conflict_with_application_dependencies(platform):
    from packaging.markers import Marker

    paths = [ROOT / "requirements/lock.txt", ROOT / "requirements/build-lock.txt"]
    versions = {}
    context = {"python_full_version": "3.12.13", "python_version": "3.12", "sys_platform": platform}
    while paths:
        path = paths.pop()
        for relative in re.findall(r"(?m)^-r (.+)$", path.read_text()):
            paths.append(path.parent / relative)
        for name, version, marker in _requirements(path):
            if marker and not Marker(marker.lstrip(" ;")).evaluate(context):
                continue
            versions.setdefault(name, set()).add(version)
    assert all(len(values) == 1 for values in versions.values())
    assert versions["setuptools"] == {"84.0.0"}
    assert versions["packaging"] == {"26.3"}
    assert "hatchling" not in versions and "editables" not in versions


def test_pip_offline_install_requires_hashes_and_source_build_uses_locked_backend(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(environment.subprocess, "run", lambda command, **kwargs: calls.append(command))
    python = environment.environment_python(tmp_path / ".venv")
    environment.install_requirements(python, ROOT / "requirements/lock.txt", tmp_path / "wheels")
    environment.install_source(python, tmp_path)
    assert calls[0][:4] == [str(python), "-m", "pip", "install"]
    assert "--require-hashes" in calls[0]
    assert "--no-index" in calls[0]
    assert calls[0][-2:] == ["--find-links", str(tmp_path / "wheels")]
    assert "--no-deps" in calls[1] and "--no-build-isolation" in calls[1]
    assert "--upgrade" not in calls[0]


def test_locked_setuptools_backend_installs_real_editable_source_offline(tmp_path, monkeypatch):
    """@brief 用当前哈希锁后端执行真实离线安装，并证明源码修改直接生效。"""
    locked = _requirements(ROOT / "requirements/build-lock.txt")
    assert {name for name, _, _ in locked} == {"setuptools"}
    backend_paths = set()
    for name, version, _ in locked:
        distribution = importlib.metadata.distribution(name)
        assert distribution.version == version
        backend_paths.add(str(distribution.locate_file("")))
    source = tmp_path / "source"
    package = source / "locked_editable_probe"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text('VALUE = "initial"\n', encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools==84.0.0"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "locked-editable-probe"\nversion = "0.0.0"\n'
        'dependencies = ["worldmock-unavailable-editable-dependency==0"]\n'
        '[tool.setuptools]\npackages = ["locked_editable_probe"]\n', encoding="utf-8")
    # 仅借用已安装且版本与哈希锁一致的后端，实际 pip 安装不得解析依赖或访问索引。
    python = environment.create_environment(tmp_path / "environment")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sorted(backend_paths)))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    environment.install_source(python, source)
    command = [str(python), "-c", "import locked_editable_probe; print(locked_editable_probe.VALUE)"]
    assert subprocess.check_output(command, cwd=tmp_path, text=True).strip() == "initial"
    module.write_text('VALUE = "changed directly"\n', encoding="utf-8")
    assert subprocess.check_output(command, cwd=tmp_path, text=True).strip() == "changed directly"


def test_explicit_uv_missing_and_wrong_python_are_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="explicitly requested"):
        environment.create_environment(tmp_path / ".venv", True)
    monkeypatch.setattr(environment.sys, "version_info", (3, 13))
    monkeypatch.setattr(environment.subprocess, "check_output",
                        lambda *args, **kwargs: json.dumps({"implementation": "CPython", "version": "3.13.0"}))
    with pytest.raises(RuntimeError, match="CPython 3.12.13 is required"):
        environment.python_command()


@pytest.mark.parametrize("identity", [{"implementation": "CPython", "version": "3.12.12"},
                                      {"implementation": "PyPy", "version": "3.12.13"}])
def test_bootstrap_rejects_wrong_patch_or_implementation(monkeypatch, identity):
    """@brief 地图生成与双跑基线要求精确补丁版本以及 CPython 实现。"""
    monkeypatch.setattr(environment.sys, "version_info", (3, 13, 0))
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *args, **kwargs: json.dumps(identity))
    with pytest.raises(RuntimeError, match="CPython 3.12.13 is required"):
        environment.python_command()


def test_bootstrap_installs_one_environment_from_the_lock_and_the_project(tmp_path, monkeypatch):
    """@brief 引导恰好四步：建环境、按哈希锁装依赖、可编辑安装 WorldMock、写收据，返回收据。"""
    calls = []
    receipt = object()
    monkeypatch.setattr(entry, "create_environment", lambda directory, uv: calls.append(directory) or directory)
    monkeypatch.setattr(entry, "install_requirements", lambda *args: calls.append(("requirements", args)))
    monkeypatch.setattr(entry, "install_source", lambda *args: calls.append(("source", args)))
    monkeypatch.setattr(entry, "write_receipt",
                        lambda root, profile: calls.append(("receipt", (root, profile))) or receipt)
    result = entry.bootstrap(tmp_path, "release")
    assert calls == [
        tmp_path / ".venv",
        ("requirements", (tmp_path / ".venv", tmp_path / "requirements/lock.txt", None, False)),
        ("source", (tmp_path / ".venv", tmp_path, False)),
        ("receipt", (tmp_path, "release")),
    ]
    assert result is receipt


def _capability_receipt_root(tmp_path, monkeypatch, status):
    """@brief 只隔离收据落盘与解释器身份，能力状态来自真实子进程探针。"""
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements/lock.txt").write_bytes(b"locked\n")
    version = environment.required_python_version(ROOT)
    (tmp_path / ".python-version").write_text(version + "\n", encoding="utf-8")
    monkeypatch.setattr(environment, "component_status", lambda root: status)
    monkeypatch.setattr(environment, "python_identity",
                        lambda command: {"implementation": "CPython", "version": version})
    return tmp_path


@pytest.mark.requires_node
@pytest.mark.parametrize("failure", ["module_missing", "query_wrong"])
def test_node_printable_version_cannot_pass_without_working_sqlite(tmp_path, monkeypatch, failure, caplog):
    """@brief 实际 Node 能打印版本但模块或 JSON 查询失效时，release 拒绝且 dev 收据未通过。"""
    node = shutil.which("node")
    actual_run = subprocess.run
    faults = {
        "module_missing": (
            "const Module = require('node:module'); const load = Module._load; "
            "Module._load = function(name, ...args) { "
            "if (name === 'node:sqlite') throw Error('SQLite unavailable'); "
            "return load.call(this, name, ...args); }; "
        ),
        "query_wrong": (
            "const sqlite = require('node:sqlite'); const prepare = sqlite.DatabaseSync.prototype.prepare; "
            "sqlite.DatabaseSync.prototype.prepare = function(sql) { "
            "const statement = prepare.call(this, sql); "
            "if (sql.startsWith('SELECT')) return {all: () => [{extracted: 8, expanded: 8}]}; "
            "return statement; }; "
        ),
    }

    def execute(command, **options):
        """@brief 只在真实 Node 查询进程注入缺陷，版本进程与字体进程照常执行。"""
        if command[:2] == [node, "-e"]:
            command = [node, "-e", faults[failure] + command[2]]
        return actual_run(command, **options)

    monkeypatch.setattr(environment.subprocess, "run", execute)
    status = environment.component_status(ROOT)
    assert status["node_version"].startswith("v")
    assert status["node_sqlite_available"] is False
    assert "Node" in status["missing_components"]
    assert "Node.js SQLite read/write probe failed" in caplog.text
    root = _capability_receipt_root(tmp_path, monkeypatch, status)
    with pytest.raises(RuntimeError, match="Required release components are missing: Node"):
        environment.write_receipt(root, "release")
    receipt = environment.write_receipt(root, "dev")
    assert receipt["passed"] is False
    assert receipt["node_sqlite_available"] is False




def test_component_probe_keeps_production_font_import_outside_parent_process():
    """@brief 在干净宿主子进程调用真实组件探针，生产字体与完整引擎均不进入该父进程。"""
    code = (
        "import json, sys; from pathlib import Path; "
        "from scripts.bootstrap_environment import component_status; "
        "status = component_status(Path('.').resolve()); "
        "forbidden = {'worldmock.evaluation.reporting', 'worldmock.simulation_engine.engine', "
        "'worldmock.simulation_engine.game', 'worldmock.simulation_engine.games', "
        "'worldmock.runtime.lifecycle.run_host', 'worldmock.configuration.model.model_client'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "print(json.dumps(status))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(result.stdout)
    assert isinstance(status["node_sqlite_available"], bool)


def test_same_committed_source_identity_survives_autocrlf_checkouts(tmp_path):
    """@brief 两种换行设置的克隆各在自己的子进程里由导入的包推出项目根，算出的代码身份相同。"""
    seed = tmp_path / "seed"
    seed.mkdir()
    shutil.copytree(ROOT / "src", seed / "src", ignore=shutil.ignore_patterns("__pycache__"))
    for name in (".gitattributes", "pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, seed / name)
    subprocess.run(["git", "init", "-q"], cwd=seed, check=True)
    subprocess.run(["git", "add", "."], cwd=seed, check=True)
    subprocess.run(["git", "-c", "user.name=WorldMock test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Identity fixture"], cwd=seed, check=True)
    code = ("import json; from worldmock.runtime.framework.setup import project_root, runtime_code_sha256; "
            "print(json.dumps([str(project_root()), runtime_code_sha256()]))")
    work = tmp_path / "work"
    work.mkdir()
    identities = []
    for setting in ("true", "false"):
        clone = tmp_path / setting
        subprocess.run(["git", "-c", f"core.autocrlf={setting}", "clone", "-q", "--no-local",
                        str(seed), str(clone)], check=True)
        output = subprocess.check_output([sys.executable, "-c", code], cwd=work, text=True,
                                         env={**os.environ, "PYTHONPATH": str(clone / "src")})
        root, identity = json.loads(output)
        assert Path(root) == clone.resolve()
        identities.append(identity)
    assert identities[0] == identities[1]
