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

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_AS2_DIGEST = "65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334"


def _source_repository(tmp_path):
    shutil.copytree(ROOT / "patches", tmp_path / "patches")
    (tmp_path / "requirements").mkdir()
    shutil.copy2(ROOT / "requirements/labelkit-source.json", tmp_path / "requirements/labelkit-source.json")
    return tmp_path


def test_series_rebuilds_the_real_package_and_refuses_source_corruption(tmp_path):
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
    subprocess.run(["git", "apply", "--check", "-"], cwd=tmp_path, input=patch, check=True)
    subprocess.run(["git", "apply", "-"], cwd=tmp_path, input=patch, check=True)
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


def test_wheelhouse_missing_labelkit_artifact_never_accesses_network(tmp_path, monkeypatch):
    root = _source_repository(tmp_path)
    monkeypatch.setattr(sources.urllib.request, "urlopen",
                        lambda *args, **kwargs: pytest.fail("offline bootstrap accessed the network"))
    with pytest.raises(RuntimeError, match="Locked artifact hash differs"):
        sources.labelkit_artifact(root, tmp_path / "wheelhouse")


def test_existing_labelkit_source_is_identity_checked_without_network(monkeypatch):
    monkeypatch.setattr(sources.urllib.request, "urlopen",
                        lambda *args, **kwargs: pytest.fail("valid source must not download"))
    result = sources.restore_labelkit(ROOT)
    assert result["commit"] == "d0afd606decbc3f4aa64c64ef5339586120c5a7a"


def _requirements(path):
    rows = {}
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        for match in re.finditer(r"^([a-z0-9-]+)==([^;\s\\]+)([^\n]*)\n((?:[ \t]+[^\n]*\n?)*)", block, re.M):
            name, version, marker, tail = match.groups()
            rows[(name, version, marker.rstrip(" \\"))] = set(re.findall(r"--hash=(sha256:[a-f0-9]+)", tail))
    return rows


@pytest.mark.parametrize("lock,export", [("uv.lock", "lock.txt"),
                                         ("vendor/labelkit/uv.lock", "labelkit-lock.txt")])
def test_exported_dependency_versions_and_all_hashes_match_original_lock(lock, export):
    packages = tomllib.loads((ROOT / lock).read_text(encoding="utf-8"))["package"]
    originals = {(package["name"], package["version"]): package for package in packages
                 if "registry" in package["source"]}
    rows = _requirements(ROOT / "requirements" / export)
    assert rows
    assert {(name, version) for name, version, marker in rows} == set(originals)
    for (name, version, marker), hashes in rows.items():
        package = originals[name, version]
        artifacts = [*package.get("wheels", []), *([package["sdist"]] if "sdist" in package else [])]
        assert hashes == {artifact["hash"] for artifact in artifacts}
    if export == "lock.txt":
        textworld = [marker for name, version, marker in rows if name == "textworld"]
        assert textworld == [" ; sys_platform != 'win32'"]
        assert ("hatchling", "1.32.4") in originals


@pytest.mark.parametrize("export", ["lock.txt", "labelkit-lock.txt"])
@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_recursive_backend_locks_do_not_conflict_with_application_dependencies(export, platform):
    from packaging.markers import Marker

    paths = [ROOT / "requirements" / export]
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
    assert versions["packaging"] == {"26.3" if export == "lock.txt" else "26.2"}


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
    monkeypatch.setattr(environment.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="(3, 13)"))
    with pytest.raises(RuntimeError, match="Python 3.12 is required"):
        environment.python312()


def test_bootstrap_checks_source_before_installs_and_uses_two_environments(tmp_path, monkeypatch):
    calls = []
    (tmp_path / "requirements").mkdir()
    monkeypatch.setattr(entry, "restore_as2", lambda root: calls.append("as2") or "a" * 64)
    source = {"commit": "c", "sha256": "d", "source_sha256": "e"}
    (tmp_path / "requirements/labelkit-source.json").write_text(json.dumps(source))
    monkeypatch.setattr(entry, "restore_labelkit", lambda root, wheels: calls.append("labelkit") or source)
    monkeypatch.setattr(entry, "create_environment", lambda directory, uv: calls.append(directory) or directory)
    monkeypatch.setattr(entry, "install_requirements", lambda *args: calls.append(("requirements", args)))
    monkeypatch.setattr(entry, "install_source", lambda *args: calls.append(("source", args)))
    monkeypatch.setattr(entry, "write_receipt", lambda root, profile, sources: sources)
    result = entry.bootstrap(tmp_path, "release")
    assert calls[:2] == ["as2", "labelkit"]
    assert [item for item in calls if isinstance(item, Path)] == [tmp_path / ".venv", tmp_path / "vendor/labelkit/.venv"]
    assert result["as2_source_sha256"] == "a" * 64


def test_dev_offline_missing_labelkit_remains_a_nonpassing_environment(tmp_path, monkeypatch):
    (tmp_path / "requirements").mkdir()
    metadata = {"archive": "locked-labelkit.tar.gz", "source_sha256": "l" * 64, "commit": "c", "sha256": "s"}
    (tmp_path / "requirements/labelkit-source.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(entry, "restore_as2", lambda root: "a" * 64)
    monkeypatch.setattr(entry, "restore_labelkit", lambda *args: pytest.fail("missing optional artifact was downloaded"))
    monkeypatch.setattr(entry, "create_environment", lambda directory, uv: directory)
    monkeypatch.setattr(entry, "install_requirements", lambda *args: None)
    monkeypatch.setattr(entry, "install_source", lambda *args: None)
    (tmp_path / "requirements/lock.txt").write_text("")
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *args, **kwargs: "Python 3.12.13")
    monkeypatch.setattr(environment, "component_status",
                        lambda root: {"node_version": "v24", "missing_components": ["LabelKit"]})
    receipt = entry.bootstrap(tmp_path, "dev", wheelhouse=tmp_path / "wheelhouse")
    assert receipt["passed"] is False
    assert receipt["missing_components"] == ["LabelKit"]


def test_same_committed_source_identity_survives_autocrlf_checkouts(tmp_path):
    from worldmock.runtime import runtime_code_sha256

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
