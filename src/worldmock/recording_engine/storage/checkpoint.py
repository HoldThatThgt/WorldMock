"""@brief 以不可变素材保存人物工作区，并在新分支恢复共同状态。"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import unicodedata
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from worldmock.foundation.filesystem import relative_declaration
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.foundation.atomic_write import atomic_write
from worldmock.recording_engine.events.models import AssetRef, Checkpoint, EventDraft
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore

#: 工作区快照的 gzip 压缩级别（docs/design/05-recording-engine.md「冻结输入、检查点与分支」）。
_WORKSPACE_GZIP_LEVEL = 6


def snapshot_directory(directory: Path, assets: AssetStore) -> AssetRef:
    """@brief 保存目录中完整普通文件，拒绝符号链接与目录联接并固定归档元信息；
    成员名先按恢复时的同一规则核对，恢复不了的快照不会被保存。
    归档以固定头部 gzip 压缩（修改时刻 0），同一工作区在任何时刻得到同一素材。"""
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
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=_WORKSPACE_GZIP_LEVEL, mtime=0) as stream:
        stream.write(content.getvalue())
    return assets.put(compressed.getvalue(), {"media_type": "application/gzip", "source": {"kind": "workspace"}})


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
    """@brief 完整解压 gzip 并核对全部成员后才展开普通相对文件，不覆盖已有目标目录，也不让两个成员写到同一个文件。"""
    destination = Path(destination)
    if destination.exists():
        logging.error("Participant restore destination already exists: %s", destination)
        raise IntegrityError("Participant restore destination already exists")
    content = assets.read(reference)
    try:
        archive_bytes = gzip.decompress(content)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            members = archive.getmembers()
            _check_members(members)
            destination.mkdir(parents=True)
            for member in members:
                source = archive.extractfile(member)
                target = destination / member.name
                # 目标目录是新建的：写入前已存在只能是先前成员的别名（Windows 8.3 短名、大小写或 Unicode 规范化）。
                if os.path.lexists(target):
                    logging.error("Workspace snapshot member %s aliases an earlier member", member.name)
                    raise IntegrityError("Workspace snapshot contains an unsafe member")
                atomic_write(target, source.read())
    except (gzip.BadGzipFile, EOFError, zlib.error, tarfile.TarError) as exc:
        logging.error("Workspace snapshot %s is not a valid gzip-compressed tar: %s", reference.sha256, exc)
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


@dataclass(frozen=True)
class _ParentPrefix:
    """@brief 父库检查点前缀逐条复核的结果。"""

    #: 前缀事件出现过的全部运行身份。
    run_ids: frozenset[str]
    #: 按事件序号、事件内次序排列的全部素材引用（可重复），每个都已读取并核对。
    references: tuple[AssetRef, ...]


def _validated_prefix(store: FootprintStore, cutoff: int) -> _ParentPrefix:
    """@brief 逐条解码校验父库前缀（分页、不留事件列表），读取核对每个素材引用，记下运行身份与引用。"""
    run_ids, references = set(), []
    for event in store.iter_events(cutoff):
        run_ids.add(event.run_id)
        for reference in event.artifact_refs:
            store.assets.read(reference)
            references.append(reference)
    return _ParentPrefix(frozenset(run_ids), tuple(references))


def restore_branch(store: FootprintStore, checkpoint_id: str, destination: Path,
                   branch_metadata: dict | None = None) -> FootprintStore:
    """@brief 校验父检查点并逐条复核父前缀后，在暂存目录按字节复制前缀并原子发布新运行，不复制父目录较新的可变状态。"""
    checkpoint = store.get_checkpoint(checkpoint_id)
    metadata = _branch_metadata(branch_metadata)
    destination = Path(destination).resolve()
    if destination.exists() or store.run_dir == destination or store.run_dir in destination.parents:
        raise IntegrityError("Restore requires a new destination outside the parent run")
    prefix = _validated_prefix(store, checkpoint.last_event_seq)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        _populate_branch(store, checkpoint, prefix, stage, metadata)
        stage.rename(destination)
    except Exception:
        logging.exception("Checkpoint branch restoration failed")
        shutil.rmtree(stage)
        raise
    return FootprintStore(destination)


def _populate_branch(store: FootprintStore, checkpoint, prefix: _ParentPrefix, stage: Path, metadata: dict) -> None:
    """@brief 新库保留父事件的存储字节与身份，并绑定子运行和共同初态。"""
    manifest = dict(store.manifest)
    manifest.pop("intervention_sha256", None)
    manifest.update(metadata)
    manifest.update(run_id=uuid4().hex, parent_run_id=store.manifest["run_id"],
                    parent_checkpoint_id=checkpoint.checkpoint_id,
                    source_group_id=store.manifest.get("source_group_id", store.manifest["run_id"]),
                    lineage=branch_lineage(store, checkpoint))
    manifest["source_run_ids"] = sorted(prefix.run_ids | {manifest["run_id"]})
    child = FootprintStore.create(stage, manifest, checkpoint.state)
    try:
        references = _branch_asset_references(store, checkpoint, prefix.references)
        for reference in references.values():
            child.assets.put(store.assets.read(reference), {
                "media_type": reference.media_type, "source": reference.source,
            })
        child.copy_event_prefix(store, checkpoint.last_event_seq, prefix.references)
        state = dict(checkpoint.state)
        state.update(run_id=manifest["run_id"], version=state["version"] + 1)
        child.commit(checkpoint.state, state, [EventDraft(
            event_type="environment_event", occurred_at=state["current_time"], visibility="system",
            data={"kind": "branch_created", "parent_checkpoint_id": checkpoint.checkpoint_id})])
        atomic_write(stage / "restored-checkpoint.json", canonical_json(checkpoint.model_dump()))
    finally:
        child.close()


def branch_lineage(store: FootprintStore, checkpoint: Checkpoint) -> list[dict]:
    """@brief 子分支的 lineage：父运行的 lineage 加一项描述父运行这一段的条目，按段先后。"""
    return [*store.manifest["lineage"], lineage_entry(store, checkpoint)]


def lineage_entry(store: FootprintStore, checkpoint: Checkpoint) -> dict:
    """@brief 父运行这一段的 lineage 条目：从哪一模拟时刻开始、在哪个检查点分出、走到哪里、为什么结束、丢弃多少事件、
    子分支何时分出；只做主键与人物类型索引上的有界查询。"""
    earlier = store.manifest["lineage"]
    last = store.read_last_event()
    terminal = store.read_terminal_event(earlier[-1]["checkpoint_event_seq"] if earlier else 0)
    if terminal is None:
        reason, error = "no terminal event", None
    else:
        reason = terminal.event_type
        error = terminal.data["error"] if reason == "run_failed" else None
    return {"run_id": store.manifest["run_id"],
            "simulated_from": earlier[-1]["checkpoint_time"] if earlier else store.manifest["window"]["start"],
            "checkpoint_id": checkpoint.checkpoint_id, "checkpoint_event_seq": checkpoint.last_event_seq,
            "checkpoint_time": checkpoint.current_time, "last_event_seq": last.event_seq,
            "last_event_time": last.occurred_at, "last_recorded_at": last.recorded_at,
            "discarded_events": last.event_seq - checkpoint.last_event_seq,
            "end_reason": reason, "end_error": error,
            "branched_at": datetime.now(timezone.utc).isoformat()}


def _branch_asset_references(store: FootprintStore, checkpoint, references: Sequence[AssetRef]) -> dict[str, AssetRef]:
    """@brief 检查点、真实事件与完整冻结环境素材必须共同复制到恢复分支。"""
    references_by_hash = {reference.sha256: reference for reference in checkpoint.snapshots.values()}
    if not isinstance(store.manifest.get("environment_assets"), dict):
        logging.error("Frozen environment assets are missing from the parent run")
        raise IntegrityError("Frozen environment assets are missing from the parent run")
    for value in store.manifest["environment_assets"].values():
        reference = AssetRef.model_validate(value)
        references_by_hash[reference.sha256] = reference
    references_by_hash.update({reference.sha256: reference for reference in references})
    return references_by_hash


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
