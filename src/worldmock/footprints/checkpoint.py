"""@brief 以不可变素材保存人物工作区，并在新分支恢复共同状态。"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore, atomic_write
from worldmock.footprints.models import AssetRef, EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore


def snapshot_directory(directory: Path, assets: AssetStore) -> AssetRef:
    """@brief 保存目录中完整普通文件，拒绝符号链接并固定归档元信息。"""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise IntegrityError("Participant workspace is missing")
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise IntegrityError("Participant workspace contains a symbolic link")
            if path.is_file():
                raw = path.read_bytes()
                entry = tarfile.TarInfo(path.relative_to(root).as_posix())
                entry.size = len(raw)
                entry.mode = 0o600
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(raw))
    return assets.put(content.getvalue(), {"media_type": "application/x-tar", "source": {"kind": "workspace"}})


def restore_directory(reference: AssetRef, assets: AssetStore, destination: Path) -> None:
    """@brief 仅展开已校验的普通相对文件，不覆盖已有目标目录。"""
    destination = Path(destination)
    if destination.exists():
        raise IntegrityError("Participant restore destination already exists")
    content = assets.read(reference)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise IntegrityError("Workspace snapshot contains an unsafe member")
        destination.mkdir(parents=True)
        for member in members:
            source = archive.extractfile(member)
            if source is None:
                raise IntegrityError("Workspace snapshot content is missing")
            atomic_write(destination / member.name, source.read())


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
        references = {reference.sha256: reference for reference in checkpoint.snapshots.values()}
        for event in events:
            references.update({reference.sha256: reference for reference in event.artifact_refs})
        for reference in references.values():
            child.assets.put(store.assets.read(reference), {
                "media_type": reference.media_type, "source": reference.source,
            })
        with child.connection:
            for event in events:
                child._insert_event(event)
        state = dict(checkpoint.state)
        state.update(run_id=manifest["run_id"], version=state["version"] + 1)
        child.commit(state, [EventDraft(event_type="environment_event", occurred_at=state["current_time"],
                                       visibility="system", data={"kind": "branch_created",
                                       "parent_checkpoint_id": checkpoint.checkpoint_id})])
        atomic_write(stage / "restored-checkpoint.json", canonical_json(checkpoint.model_dump()))
    finally:
        child.close()


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
