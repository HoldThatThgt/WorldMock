"""@brief 固定解释器、依赖安装与环境 profile 的标准库实现。"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.bootstrap_sources import file_sha256

PROFILE_ENV = "WORLDMOCK_ENVIRONMENT_PROFILE"
MISSING_NODE = "Node.js with working node:sqlite is unavailable for the dev environment profile"
ROOT = Path(__file__).resolve().parents[1]

# 与查看器相同，真实执行绑定写入和两种 JSON 查询，不以模块名或版本代替能力。
_NODE_SQLITE_PROBE = (
    "const assert = require('node:assert/strict'); "
    "const {DatabaseSync} = require('node:sqlite'); "
    "const database = new DatabaseSync(':memory:'); "
    "database.exec('CREATE TABLE probe(body TEXT NOT NULL)'); "
    "database.prepare('INSERT INTO probe(body) VALUES (?)').run(JSON.stringify({values: [7]})); "
    "const rows = database.prepare(\"SELECT json_extract(probe.body, '$.values[0]') AS extracted, "
    "item.value AS expanded FROM probe, json_each(probe.body, '$.values') AS item\").all(); "
    "assert.deepEqual(rows.map(row => [row.extracted, row.expanded]), [[7, 7]]); database.close();"
)


def environment_python(directory: Path) -> Path:
    """@brief 取本操作系统虚拟环境内的唯一 Python 路径。"""
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def required_python_version(root: Path) -> str:
    """@brief 从唯一解释器版本文件读取地图与基线所需的完整版本。"""
    return (root / ".python-version").read_text(encoding="utf-8").strip()


def require_python_identity(identity: dict, version: str) -> None:
    """@brief 拒绝实现或补丁版本不同的解释器。"""
    if identity != {"implementation": "CPython", "version": version}:
        logging.error("CPython %s is required; actual interpreter: %s", version, identity)
        raise RuntimeError(f"CPython {version} is required to run WorldMock")


def python_identity(command: list[str]) -> dict:
    """@brief 用实际解释器报告实现名称和完整版本，避免从命令名推测身份。"""
    code = ("import json, platform; print(json.dumps({'implementation': platform.python_implementation(), "
            "'version': platform.python_version()}))")
    try:
        return json.loads(subprocess.check_output([*command, "-c", code], text=True))
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        logging.error("Could not read Python interpreter identity: %s", error)
        raise RuntimeError("Could not read Python interpreter identity") from error


def python_command() -> list[str]:
    """@brief 只选版本文件声明的 CPython，不接受同一小版本的其它补丁版本。"""
    version = required_python_version(ROOT)
    if tuple(sys.version_info[:3]) == tuple(int(part) for part in version.split(".")):
        require_python_identity({"implementation": platform.python_implementation(), "version": version}, version)
        return [sys.executable]
    minor = ".".join(version.split(".")[:2])
    command = ["py", f"-{minor}"] if os.name == "nt" else [f"python{minor}"]
    require_python_identity(python_identity(command), version)
    return command


def create_environment(directory: Path, use_uv: bool = False) -> Path:
    """@brief 用选定安装器创建环境，显式 uv 缺失即失败。"""
    interpreter = python_command()
    if use_uv:
        binary = shutil.which("uv")
        if binary is None:
            logging.error("uv was explicitly requested but is not installed")
            raise RuntimeError("uv was explicitly requested but is not installed")
        subprocess.run([binary, "venv", "--python", required_python_version(ROOT), str(directory)], check=True)
    else:
        subprocess.run([*interpreter, "-m", "venv", str(directory)], check=True)
    python = environment_python(directory)
    require_python_identity(python_identity([str(python)]), required_python_version(ROOT))
    return python


def install_requirements(python: Path, requirements: Path, wheelhouse: Path | None = None,
                         use_uv: bool = False) -> None:
    """@brief 哈希安装唯一依赖锁；wheelhouse 严格禁止索引和网络下载。"""
    if use_uv:
        binary = shutil.which("uv")
        if binary is None:
            raise RuntimeError("uv was explicitly requested but is not installed")
        command = [binary, "pip", "install", "--python", str(python)]
    else:
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(["--require-hashes", "-r", str(requirements)])
    if wheelhouse is not None:
        command.extend(["--no-index", "--find-links", str(wheelhouse)])
    subprocess.run(command, check=True)


def install_source(python: Path, source: Path, use_uv: bool = False) -> None:
    """@brief 已核对源码只用已锁构建后端安装，不隐式解析或下载依赖。"""
    if use_uv:
        binary = shutil.which("uv")
        if binary is None:
            raise RuntimeError("uv was explicitly requested but is not installed")
        command = [binary, "pip", "install", "--python", str(python)]
    else:
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(["--no-deps", "--no-build-isolation", "-e", str(source)])
    subprocess.run(command, check=True)


def _node_status() -> tuple[str | None, bool]:
    """@brief 记录实际 Node 版本，并以真实内存数据库读写核对 SQLite 能力。"""
    node = shutil.which("node")
    node_version = None
    if node is None:
        logging.error("Node.js with working node:sqlite is unavailable")
        return node_version, False
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
        node_version = result.stdout.strip()
        result = subprocess.run([node, "-e", _NODE_SQLITE_PROBE], capture_output=True, text=True,
                                timeout=30, check=False)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        logging.error("Node.js SQLite capability check failed: %s", error)
        return node_version, False
    if result.returncode != 0:
        logging.error("Node.js SQLite read/write probe failed: %s", result.stderr.strip())
    return node_version, result.returncode == 0




def component_status(root: Path) -> dict:
    """@brief 核对查看器 SQLite的实际能力及缺失集合。"""
    node_version, node_sqlite_available = _node_status()
    missing = []
    if not node_sqlite_available:
        missing.append("Node")
    return {"node_version": node_version, "node_sqlite_available": node_sqlite_available,
            "missing_components": missing}


def require_profile(status: dict, profile: str) -> None:
    """@brief release 缺组件立即失败；dev 的缺失只能形成未通过收据。"""
    if profile not in {"release", "dev"}:
        logging.error("Environment profile must be release or dev")
        raise RuntimeError("Environment profile must be release or dev")
    if profile == "release" and status["missing_components"]:
        logging.error("Required release components are missing: %s", ", ".join(status["missing_components"]))
        raise RuntimeError("Required release components are missing: " + ", ".join(status["missing_components"]))


def write_receipt(root: Path, profile: str, sources: dict) -> dict:
    """@brief 保存不含机器绝对路径、身份或凭据的固定环境收据。"""
    status = component_status(root)
    require_profile(status, profile)
    python = environment_python(root / ".venv")
    identity = python_identity([str(python)])
    require_python_identity(identity, required_python_version(root))
    locks = {path.relative_to(root).as_posix(): file_sha256(path)
             for path in sorted((root / "requirements").glob("*-lock.txt"))}
    locks["requirements/lock.txt"] = file_sha256(root / "requirements/lock.txt")
    receipt = {"schema_version": "environment-1", "profile": profile,
               "python_version": identity["version"], "python_implementation": identity["implementation"],
               "python_version_file_sha256": file_sha256(root / ".python-version"),
               "dependency_locks": locks, **sources, **status, "passed": not status["missing_components"]}
    destination = root / ".work/environment-receipt.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return receipt
