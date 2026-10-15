"""@brief 以不可变素材保存人物工作区，并在新分支恢复共同状态。"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from uuid import uuid4

from worldmock.corpora.manifest import relative_declaration
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore, atomic_write
from worldmock.footprints.models import AssetRef, EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore


def snapshot_directory(directory: Path, assets: AssetStore) -> AssetRef:
    """@brief 保存目录中完整普通文件，拒绝符号链接与目录联接并固定归档元信息；
    成员名先按恢复时的同一规则核对，恢复不了的快照不会被保存。"""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise IntegrityError("Participant workspace is missing")
    files = sorted(_workspace_files(root))
    entries = [tarfile.TarInfo(path.relative_to(root).as_posix()) for path in files]
    _check_members(entries, "Participant workspace contains an unsafe file name")
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        for path, entry in zip(files, entries):
            raw = path.read_bytes()
            entry.size = len(raw)
            entry.mode = 0o600
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(raw))
    return assets.put(content.getvalue(), {"media_type": "application/x-tar", "source": {"kind": "workspace"}})


def _workspace_files(root: Path) -> list[Path]:
    """@brief 自上而下列出工作区里的普通文件；遇到符号链接或目录联接立即拒绝，不进入它指向的目录。"""
    files: list[Path] = []
    for directory, dirnames, filenames in root.walk():
        for name in (*dirnames, *filenames):
            if (directory / name).is_symlink() or (directory / name).is_junction():
                raise IntegrityError("Participant workspace contains a symbolic link")
        files.extend(path for name in filenames if (path := directory / name).is_file())
    return files


def restore_directory(reference: AssetRef, assets: AssetStore, destination: Path) -> None:
    """@brief 全部成员核对通过后才展开普通相对文件，不覆盖已有目标目录，也不让两个成员写到同一个文件。"""
    destination = Path(destination)
    if destination.exists():
        raise IntegrityError("Participant restore destination already exists")
    content = assets.read(reference)
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            members = archive.getmembers()
            _check_members(members)
            destination.mkdir(parents=True)
            for member in members:
                source = archive.extractfile(member)
                target = destination / member.name
                # 目标目录是新建的：写入前已存在只能是先前成员的别名（Windows 8.3 短名、大小写或 Unicode 规范化）。
                if os.path.lexists(target):
                    raise IntegrityError("Workspace snapshot contains an unsafe member")
                atomic_write(target, source.read())
    except tarfile.TarError as exc:
        raise IntegrityError("Workspace snapshot archive is invalid") from exc


def _check_members(members: list[tarfile.TarInfo],
                   message: str = "Workspace snapshot contains an unsafe member") -> None:
    """@brief 快照保存前与恢复展开前核对全部成员：只接受普通文件（稀疏、连续等变体不算），名字可移植，忽略大小写与
    Unicode 规范形式后互不重名，且没有文件同时是另一个成员的上级目录。"""
    files: set[str] = set()
    for member in members:
        if member.type != tarfile.REGTYPE or member.issparse() or not relative_declaration(member.name):
            raise IntegrityError(message)
        key = unicodedata.normalize("NFD", unicodedata.normalize("NFD", member.name).casefold())
        key = PurePosixPath(key).as_posix()
        if key in files:
            raise IntegrityError(message)
        files.add(key)
    if files & {parent.as_posix() for key in files for parent in PurePosixPath(key).parents}:
        raise IntegrityError(message)


def restore_branch(store: FootprintStore, checkpoint_id: str, destination: Path,
                   branch_metadata: dict | None = None) -> FootprintStore:
    """@brief 校验父检查点后原子发布新运行，不复制父目录较新的可变状态。"""
    checkpoint = store.get_checkpoint(checkpoint_id)
    metadata = _branch_metadata(branch_metadata)
    destination = Path(destination).resolve()
    if destination.exists() or store.run_dir == destination or store.run_dir in destination.parents:
        raise IntegrityError("Restore requires a new destination outside the parent run")
    events = store.read_events(checkpoint.last_event_seq)
    for event in events:
        for reference in event.artifact_refs:
            store.assets.read(reference)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        _populate_branch(store, checkpoint, events, stage, metadata)
        stage.rename(destination)
    except Exception:
        logging.exception("Checkpoint branch restoration failed")
        shutil.rmtree(stage)
        raise
    return FootprintStore(destination)


def _populate_branch(store: FootprintStore, checkpoint, events, stage: Path, metadata: dict) -> None:
    """@brief 新库保留父事件身份，并绑定子运行和共同初态。"""
    manifest = dict(store.manifest)
    manifest.pop("intervention_sha256", None)
    manifest.update(metadata)
    manifest.update(run_id=uuid4().hex, parent_run_id=store.manifest["run_id"],
                    parent_checkpoint_id=checkpoint.checkpoint_id,
                    source_group_id=store.manifest.get("source_group_id", store.manifest["run_id"]))
    manifest["source_run_ids"] = sorted({event.run_id for event in events} | {manifest["run_id"]})
    child = FootprintStore.create(stage, manifest, checkpoint.state)
    try:
        references = _branch_asset_references(store, checkpoint, events)
        for reference in references.values():
            child.assets.put(store.assets.read(reference), {
                "media_type": reference.media_type, "source": reference.source,
            })
        with child.connection:
            for event in events:
                child._insert_event(event)
        state = dict(checkpoint.state)
        state.update(run_id=manifest["run_id"], version=state["version"] + 1)
        child.commit(checkpoint.state, state, [EventDraft(
            event_type="environment_event", occurred_at=state["current_time"], visibility="system",
            data={"kind": "branch_created", "parent_checkpoint_id": checkpoint.checkpoint_id})])
        atomic_write(stage / "restored-checkpoint.json", canonical_json(checkpoint.model_dump()))
    finally:
        child.close()


def _branch_asset_references(store: FootprintStore, checkpoint, events: list) -> dict[str, AssetRef]:
    """@brief 检查点、真实事件与完整冻结环境素材必须共同复制到恢复分支。"""
    references = {reference.sha256: reference for reference in checkpoint.snapshots.values()}
    if not isinstance(store.manifest.get("environment_assets"), dict):
        logging.error("Frozen environment assets are missing from the parent run")
        raise IntegrityError("Frozen environment assets are missing from the parent run")
    for value in store.manifest["environment_assets"].values():
        reference = AssetRef.model_validate(value)
        references[reference.sha256] = reference
    for event in events:
        references.update({reference.sha256: reference for reference in event.artifact_refs})
    return references


def _branch_metadata(value: dict | None) -> dict:
    """@brief 只允许明确的恢复或已绑定干预身份，不允许覆盖父实验参数。"""
    if value is None:
        return {"branch_kind": "resume"}
    if set(value) != {"branch_kind", "intervention_sha256"} or value["branch_kind"] != "counterfactual":
        raise IntegrityError("Counterfactual branch metadata is invalid")
    digest = value["intervention_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise IntegrityError("Counterfactual intervention hash is invalid")
    return dict(value)
