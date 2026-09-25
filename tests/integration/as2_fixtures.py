"""运行身份测试用的临时 AS2 包：代码身份与锁定核对都不依赖本机实际安装的 AS2。

@file     as2_fixtures.py
@brief    在临时目录写一个可被 importlib.util.find_spec 解析的包，让 runtime 的 AS2 身份解析到它；
          摘要按 docs/dev/SPEC-time-and-energy.md「运行身份」独立计算（除 __pycache__ 外的全部文件，
          标签 agentsociety2/<相对路径>），不复用生产实现。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import worldmock.runtime as runtime

#: 临时 AS2 包的缺省内容：含非 Python 文件，以及必须排除在摘要之外的 __pycache__ 文件。
FAKE_AS2_FILES = {
    "__init__.py": b"VERSION = 'fake'\n",
    "society/society.py": b"def step():\n    return 1\n",
    "agent/skills/guide/SKILL.md": b"# guide\n",
    "__pycache__/__init__.cpython-312.pyc": b"\x00compiled",
}


def write_fake_as2(site: Path, name: str, files: dict[str, bytes] | None = None) -> Path:
    """在 site 下写一个名为 name 的包目录（files 为空时只建空目录）并返回该目录。"""
    package = site / name
    package.mkdir(parents=True, exist_ok=True)
    for relative, content in (FAKE_AS2_FILES if files is None else files).items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return package


def expected_as2_sha256(package: Path) -> str:
    """按规格口径独立计算包目录的源码摘要，与安装位置无关。"""
    rows = sorted((f"agentsociety2/{path.relative_to(package).as_posix()}", path.read_bytes())
                  for path in package.rglob("*")
                  if path.is_file() and "__pycache__" not in path.relative_to(package).parts)
    digest = hashlib.sha256()
    for label, content in rows:
        encoded = label.encode()
        for part in (len(encoded).to_bytes(8, "big"), encoded, len(content).to_bytes(8, "big"), content):
            digest.update(part)
    return digest.hexdigest()


def use_fake_as2(monkeypatch, site: Path, files: dict[str, bytes] | None = None, *, locked: bool = True) -> Path:
    """让运行身份解析到 site 下新写的临时包；locked 时把锁定摘要设为该包的摘要。"""
    name = f"worldmock_fake_as2_{uuid4().hex}"
    package = write_fake_as2(site, name, files)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(runtime, "_AS2_PACKAGE", name)
    if locked:
        monkeypatch.setattr(runtime, "AS2_LOCKED_SOURCE_SHA256", expected_as2_sha256(package))
    return package


def write_project(root: Path) -> Path:
    """写一个最小工程根：runtime_code_sha256 需要的依赖文件与一份生产源码。"""
    for relative, text in (("pyproject.toml", "[project]\nname = 'fixture'\n"), ("uv.lock", "version = 1\n"),
                           ("src/worldmock/probe.py", "VALUE = 1\n"),
                           ("custom/agents/life_agent.py", "TARGET = 'fixture'\n")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
