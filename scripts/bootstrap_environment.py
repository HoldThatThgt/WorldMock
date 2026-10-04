"""@brief 固定解释器、依赖安装与环境 profile 的标准库实现。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

from scripts.bootstrap_sources import file_sha256

PROFILE_ENV = "WORLDMOCK_ENVIRONMENT_PROFILE"
MISSING_NODE = "Node.js is not installed for the dev environment profile"
MISSING_LABELKIT = "The isolated LabelKit CLI is not installed for the dev environment profile"


def environment_python(directory: Path) -> Path:
    """@brief 取本操作系统虚拟环境内的唯一 Python 路径。"""
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def labelkit_cli(root: Path) -> Path:
    """@brief 保持独立 LabelKit CLI 的跨平台公开安装位置。"""
    return root / "vendor/labelkit/.venv" / ("Scripts/labelkit.exe" if os.name == "nt" else "bin/labelkit")


def python312() -> list[str]:
    """@brief 明确使用 Python 3.12，缺失时失败，不接受其它 Python 版本。"""
    if sys.version_info[:2] == (3, 12):
        return [sys.executable]
    command = ["py", "-3.12"] if os.name == "nt" else ["python3.12"]
    try:
        result = subprocess.run([*command, "-c", "import sys; print(sys.version_info[:2])"],
                                check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Python 3.12 is required to bootstrap WorldMock") from error
    if result.stdout.strip() != "(3, 12)":
        raise RuntimeError("Python 3.12 is required to bootstrap WorldMock")
    return command


def create_environment(directory: Path, use_uv: bool = False) -> Path:
    """@brief 用选定安装器创建环境，显式 uv 缺失即失败。"""
    interpreter = python312()
    if use_uv:
        binary = shutil.which("uv")
        if binary is None:
            raise RuntimeError("uv was explicitly requested but is not installed")
        subprocess.run([binary, "venv", "--python", "3.12", str(directory)], check=True)
    else:
        subprocess.run([*interpreter, "-m", "venv", str(directory)], check=True)
    return environment_python(directory)


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


def component_status(root: Path) -> dict:
    """@brief 核对可选部件的实际 CLI，返回固定名称的缺失集合。"""
    node = shutil.which("node")
    node_version = None
    if node is not None:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
        node_version = result.stdout.strip()
    missing = []
    if node_version is None:
        missing.append("Node")
    if not labelkit_cli(root).is_file():
        missing.append("LabelKit")
    return {"node_version": node_version, "missing_components": missing}


def require_profile(status: dict, profile: str) -> None:
    """@brief release 缺组件立即失败；dev 的缺失只能形成未通过收据。"""
    if profile not in {"release", "dev"}:
        raise RuntimeError("Environment profile must be release or dev")
    if profile == "release" and status["missing_components"]:
        raise RuntimeError("Required release components are missing: " + ", ".join(status["missing_components"]))


def write_receipt(root: Path, profile: str, sources: dict) -> dict:
    """@brief 保存不含机器绝对路径、身份或凭据的固定环境收据。"""
    status = component_status(root)
    require_profile(status, profile)
    python = environment_python(root / ".venv")
    version = subprocess.check_output([str(python), "--version"], text=True).strip()
    locks = {path.relative_to(root).as_posix(): file_sha256(path)
             for path in sorted((root / "requirements").glob("*-lock.txt"))}
    locks["requirements/lock.txt"] = file_sha256(root / "requirements/lock.txt")
    receipt = {"schema_version": "environment-1", "profile": profile, "python_version": version,
               "dependency_locks": locks, **sources, **status, "passed": not status["missing_components"]}
    destination = root / ".work/environment-receipt.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return receipt
