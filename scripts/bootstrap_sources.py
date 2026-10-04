"""@brief 按固定制品和补丁恢复源码；本模块只使用标准库。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


def file_sha256(path: Path) -> str:
    """@brief 返回文件原始字节的 SHA-256。"""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_sha256(base: Path, paths: tuple[str, ...], prefix: str = "") -> str:
    """@brief 按相对标签与原始字节计算固定源码身份，排除解释器缓存。"""
    files = []
    for relative in paths:
        path = base / relative
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if "__pycache__" not in candidate.parts and not candidate.is_dir():
                files.append((prefix + candidate.relative_to(base).as_posix(), candidate))
    if not files:
        raise RuntimeError("Locked source files are missing")
    digest = hashlib.sha256()
    for label, path in sorted(files):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Locked source contains an unsafe file")
        encoded, content = label.encode("utf-8"), path.read_bytes()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    """@brief 固定制品缺失或摘要不符时明确失败。"""
    if path.is_symlink() or not path.is_file() or file_sha256(path) != expected:
        raise RuntimeError(f"Locked artifact hash differs: {path.name}")


def read_series(root: Path) -> dict:
    """@brief 读取仓库内唯一 AS2 来源声明。"""
    return json.loads((root / "patches/agentsociety2/SERIES").read_text(encoding="utf-8"))


def extract_archive(archive: Path, destination: Path, prefix: str = "") -> None:
    """@brief 仅解包普通文件与目录，拒绝绝对路径、越界、链接和特殊条目。"""
    with tarfile.open(archive, "r:*") as stream:
        for member in stream.getmembers():
            name = member.name.replace("\\", "/")
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or ":" in name:
                raise RuntimeError("Source archive contains an unsafe path")
            if not member.isfile() and not member.isdir():
                raise RuntimeError("Source archive contains a non-regular member")
            if prefix:
                if not name.startswith(prefix + "/"):
                    continue
                relative = PurePosixPath(name[len(prefix) + 1:])
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def _require_as2(package: Path, series: dict) -> str:
    """@brief 同时核对实际包字节与安装元数据，拒绝错误既有源码。"""
    digest = source_sha256(package / "agentsociety2", (".",), "agentsociety2/")
    if digest != series["source_sha256"]:
        raise RuntimeError("AS2 runtime identity differs from the locked source")
    for relative, expected in series["install_files"].items():
        require_hash(package / relative, expected)
    return digest


def restore_as2(root: Path) -> str:
    """@brief 在独立暂存目录应用标准 Git 补丁，再发布经身份核对的唯一源码。"""
    series = read_series(root)
    package = root / "vendor/agentsociety2/packages/agentsociety2"
    if package.exists() and any(package.iterdir()):
        return _require_as2(package, series)
    patch_root = root / "patches/agentsociety2"
    archive = patch_root / series["upstream"]["archive"]
    require_hash(archive, series["upstream"]["sha256"])
    work = root / ".work/bootstrap"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="as2-", dir=work) as temporary:
        stage = Path(temporary)
        extract_archive(archive, stage)
        for entry in series["patches"]:
            patch = patch_root / entry["file"]
            require_hash(patch, entry["sha256"])
            command = ["git", "apply", f"--directory={stage.relative_to(root).as_posix()}", str(patch)]
            subprocess.run(command, cwd=root, check=True)
        digest = _require_as2(stage, series)
        package.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, package, dirs_exist_ok=True)
    return digest


def labelkit_artifact(root: Path, wheelhouse: Path | None = None) -> Path:
    """@brief 按固定 HTTPS 制品取得 LabelKit，离线入口绝不访问网络。"""
    spec = json.loads((root / "requirements/labelkit-source.json").read_text(encoding="utf-8"))
    directory = wheelhouse if wheelhouse is not None else root / ".work/bootstrap/artifacts"
    archive = directory / spec["archive"]
    if not archive.exists() and wheelhouse is None:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(".download")
        try:
            with urllib.request.urlopen(spec["url"], timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            require_hash(temporary, spec["sha256"])
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    require_hash(archive, spec["sha256"])
    return archive


def restore_labelkit(root: Path, wheelhouse: Path | None = None) -> dict:
    """@brief 独立 LabelKit 进程只消费固定公开源码，既有错误源码不得覆盖。"""
    spec = json.loads((root / "requirements/labelkit-source.json").read_text(encoding="utf-8"))
    destination = root / "vendor/labelkit"
    paths = tuple(spec["source_paths"])
    if destination.exists() and any(destination.iterdir()):
        if source_sha256(destination, paths) != spec["source_sha256"]:
            raise RuntimeError("LabelKit source identity differs from the locked source")
        return spec
    archive = labelkit_artifact(root, wheelhouse)
    work = root / ".work/bootstrap"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="labelkit-", dir=work) as temporary:
        stage = Path(temporary)
        extract_archive(archive, stage, spec["archive_prefix"])
        if source_sha256(stage, paths) != spec["source_sha256"]:
            raise RuntimeError("LabelKit source identity differs from the locked source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=True)
        for relative in paths:
            source, target = stage / relative, destination / relative
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
    return spec
