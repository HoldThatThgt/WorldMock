"""@brief 可重建环境的真实来源、哈希锁和安装边界验证。"""

import gzip
import io
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap as entry
from scripts import bootstrap_environment as environment
from scripts import bootstrap_sources as sources

ROOT = Path(__file__).resolve().parents[3]
ORIGINAL_AS2_DIGEST = "65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334"


def _source_repository(tmp_path):
    shutil.copytree(ROOT / "patches", tmp_path / "patches")
    return tmp_path


@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_series_rebuilds_the_real_package_and_refuses_source_corruption(tmp_path, monkeypatch, autocrlf):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", autocrlf)
    root = _source_repository(tmp_path)
    series = sources.read_series(root)
    assert sources.restore_as2(root) == series["source_sha256"]
    assert sources.restore_as2(root) == series["source_sha256"]
    package = root / "vendor/agentsociety2/packages/agentsociety2/agentsociety2"
    assert sources.source_sha256(package, (".",), "agentsociety2/") == series["source_sha256"]
    source = package / "__init__.py"
    source.write_bytes(source.read_bytes() + b"\n# corrupt\n")
    with pytest.raises(RuntimeError, match="AS2 runtime identity differs"):
        sources.restore_as2(root)
    assert source.read_bytes().endswith(b"# corrupt\n")


def test_original_full_patch_rebuilds_the_original_locked_identity(tmp_path):
    evidence = ROOT / "patches/agentsociety2/evidence"
    sources.extract_archive(ROOT / "patches/agentsociety2/baseline.tar.gz", tmp_path)
    patch = gzip.decompress((evidence / "original.patch.gz").read_bytes())
    command = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply"]
    subprocess.run([*command, "--check", "-"], cwd=tmp_path, input=patch, check=True)
    subprocess.run([*command, "-"], cwd=tmp_path, input=patch, check=True)
    package = tmp_path / "agentsociety2"
    assert sources.source_sha256(package, (".",), "agentsociety2/") == ORIGINAL_AS2_DIGEST
    assert len([path for path in package.rglob("*") if path.is_file()]) == 228


def test_shrunk_source_keeps_framework_integrity_and_generic_scanner(tmp_path):
    import ast

    root = _source_repository(tmp_path)
    sources.restore_as2(root)
    package = root / "vendor/agentsociety2/packages/agentsociety2/agentsociety2"
    memory = ast.parse((package / "agent/memory.py").read_text())
    runtime = ast.parse((package / "agent/memory_runtime.py").read_text())
    methods = {node.name for node in ast.walk(memory) if isinstance(node, ast.FunctionDef)}
    assert {"validate_existing", "_read_state_file", "_read_episodes_file", "_validate_episode_record"} <= methods
    assert not {"text_bigrams", "bigram_jaccard", "_find_merge_target", "_within_merge_window"} & methods
    assert not {"_run_reflection", "_parse_reflection", "_persona_card"} & {
        node.name for node in ast.walk(runtime) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    scanner = (package / "backend/services/custom/compatibility.py").read_text(encoding="utf-8")
    assert "WorldMock" not in scanner and "LifeEnv" not in scanner
    scanner_methods = {node.name for node in ast.walk(ast.parse(scanner)) if isinstance(node, ast.FunctionDef)}
    assert {"build_env_scan_diagnostic", "has_direct_environment_contract"} <= scanner_methods


@pytest.mark.parametrize("artifact", ["archive", "patch"])
def test_rebuild_refuses_corrupt_artifacts_before_publishing(tmp_path, artifact):
    root = _source_repository(tmp_path)
    series = sources.read_series(root)
    filename = series["upstream"]["archive"] if artifact == "archive" else series["patches"][0]["file"]
    path = root / "patches/agentsociety2" / filename
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(RuntimeError, match="Locked artifact hash differs"):
        sources.restore_as2(root)
    assert not (root / "vendor/agentsociety2/packages/agentsociety2").exists()


@pytest.mark.parametrize("name,kind", [("../outside", "file"), ("/outside", "file"),
                                      ("C:/outside", "file"), ("link", "symlink")])
def test_source_archive_rejects_unsafe_entries(tmp_path, name, kind):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "../outside"
            stream.addfile(member)
        else:
            member.size = 1
            stream.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="unsafe path|non-regular member"):
        sources.extract_archive(archive, tmp_path / "output")
    assert not (tmp_path / "outside").exists()


def test_source_archive_rechecks_the_path_left_after_stripping_the_prefix(tmp_path):
    """成员名 pkg//<路径> 整体是相对路径，去掉前缀 pkg/ 后却是绝对路径；这里指向临时目录内，越界也只落在测试目录里。"""
    escaped = tmp_path / "escaped.txt"
    archive = tmp_path / "prefixed.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("pkg//" + "/".join(escaped.parts[1:]))
        member.size = 1
        stream.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="unsafe path"):
        sources.extract_archive(archive, tmp_path / "output", "pkg")
    assert not escaped.exists()


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
    assert ("hatchling", "1.32.4") in originals


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_recursive_backend_locks_do_not_conflict_with_application_dependencies(platform):
    from packaging.markers import Marker

    paths = [ROOT / "requirements/lock.txt"]
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
    assert versions["hatchling"] == {"1.32.4"}
    assert versions["setuptools"] == {"84.0.0"}
    assert versions["editables"] == {"0.6"}
    assert versions["packaging"] == {"26.3"}


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


def test_locked_hatchling_backend_installs_real_editable_source_offline(tmp_path, monkeypatch):
    """@brief 用当前哈希锁后端执行真实离线安装，并证明源码修改直接生效。"""
    locked = _requirements(ROOT / "requirements/build-lock.txt")
    assert any(name == "editables" for name, _, _ in locked)
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
        '[build-system]\nrequires = ["hatchling==1.32.4"]\nbuild-backend = "hatchling.build"\n'
        '[project]\nname = "locked-editable-probe"\nversion = "0.0.0"\n'
        'dependencies = ["worldmock-unavailable-editable-dependency==0"]\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["locked_editable_probe"]\n', encoding="utf-8")
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


def test_bootstrap_checks_source_before_installs_and_uses_one_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "restore_as2", lambda root: calls.append("as2") or "a" * 64)
    monkeypatch.setattr(entry, "create_environment", lambda directory, uv: calls.append(directory) or directory)
    monkeypatch.setattr(entry, "install_requirements", lambda *args: calls.append(("requirements", args)))
    monkeypatch.setattr(entry, "install_source", lambda *args: calls.append(("source", args)))
    monkeypatch.setattr(entry, "write_receipt", lambda root, profile, sources: sources)
    result = entry.bootstrap(tmp_path, "release")
    assert calls[0] == "as2"
    assert [item for item in calls if isinstance(item, Path)] == [tmp_path / ".venv"]
    assert [item for item in calls if isinstance(item, tuple)] == [
        ("requirements", (tmp_path / ".venv", tmp_path / "requirements/lock.txt", None, False)),
        ("source", (tmp_path / ".venv", tmp_path / "vendor/agentsociety2/packages/agentsociety2", False)),
        ("source", (tmp_path / ".venv", tmp_path, False)),
    ]
    assert result == {"as2_source_sha256": "a" * 64}


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
        environment.write_receipt(root, "release", {})
    receipt = environment.write_receipt(root, "dev", {})
    assert receipt["passed"] is False
    assert receipt["node_sqlite_available"] is False


def test_real_production_font_failure_cannot_pass_environment_receipt(tmp_path, monkeypatch, caplog):
    """@brief 在字体子进程关闭生产候选集，核对真实拒绝、明确日志和非通过收据。"""
    actual_run = subprocess.run
    font_commands = []

    def execute(command, **options):
        """@brief 仅改变字体子进程内的候选，不修改源码或父进程生产模块。"""
        if command[1:2] == ["-c"]:
            font_commands.append(command)
            fault = "import worldmock.evaluation.reporting as reporting; reporting._FONT_PATHS = (); "
            command = [*command[:2], fault + command[2]]
        return actual_run(command, **options)

    monkeypatch.setattr(environment.subprocess, "run", execute)
    status = environment.component_status(ROOT)
    assert status["chinese_chart_font_available"] is False
    assert "Chinese font" in status["missing_components"]
    assert font_commands[0][0] == str(environment.environment_python(ROOT / ".venv"))
    assert "Chinese chart font capability check failed" in caplog.text
    root = _capability_receipt_root(tmp_path, monkeypatch, status)
    with pytest.raises(RuntimeError, match="Required release components are missing: .*Chinese font"):
        environment.write_receipt(root, "release", {})
    receipt = environment.write_receipt(root, "dev", {})
    assert receipt["passed"] is False
    assert receipt["chinese_chart_font_available"] is False
    assert str(ROOT) not in json.dumps(receipt)


def test_component_probe_keeps_production_font_import_outside_parent_process():
    """@brief 在干净宿主子进程调用真实组件探针，生产字体与完整引擎均不进入该父进程。"""
    code = (
        "import json, sys; from pathlib import Path; "
        "from scripts.bootstrap_environment import component_status; "
        "status = component_status(Path('.').resolve()); "
        "forbidden = {'worldmock.evaluation.reporting', 'worldmock.life.engine', "
        "'worldmock.life.game', 'worldmock.life.games', 'worldmock.life.read_query_runtime', "
        "'custom.envs.life_env'}; "
        "assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules)); "
        "assert not any(name.startswith('agentsociety2.society') for name in sys.modules); "
        "print(json.dumps(status))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(result.stdout)
    assert isinstance(status["node_sqlite_available"], bool)
    assert isinstance(status["chinese_chart_font_available"], bool)


def test_same_committed_source_identity_survives_autocrlf_checkouts(tmp_path):
    from worldmock.run_setup import runtime_code_sha256

    seed = tmp_path / "seed"
    seed.mkdir()
    for name in ("src", "custom"):
        shutil.copytree(ROOT / name, seed / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in (".gitattributes", "pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, seed / name)
    subprocess.run(["git", "init", "-q"], cwd=seed, check=True)
    subprocess.run(["git", "add", "."], cwd=seed, check=True)
    subprocess.run(["git", "-c", "user.name=WorldMock test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Identity fixture"], cwd=seed, check=True)
    identities = []
    for setting in ("true", "false"):
        clone = tmp_path / setting
        subprocess.run(["git", "-c", f"core.autocrlf={setting}", "clone", "-q", "--no-local",
                        str(seed), str(clone)], check=True)
        identities.append(runtime_code_sha256(clone))
    assert identities[0] == identities[1]
