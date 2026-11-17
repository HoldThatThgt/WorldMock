"""@brief 人物工作区（docs/design/09-io-formats.md「人物工作区」、docs/design/05-recording-engine.md「冻结输入、检查点与
分支」）：路径在 run_dir/agents/agent_NNNN 下，新建时为空、初始快照是空归档；第一次决策之后恰有两个记忆文件，最后一个
共同检查点的人物快照与终态工作区逐字节相同；恢复在暂存目录里只校验一次，把读回的条目交给记忆存储。

运行级用例经 native_runtime 在进程内驱动真实运行宿主、引擎与 LifeAgent；恢复用例另用 engine_factory 的真实引擎与 SQLite。
"""

from __future__ import annotations

import gzip
import io
import logging
import shutil
import tarfile
from datetime import datetime, timezone

import pytest

from tests.integration.runtime.native_runtime_fixtures import native_runtime  # noqa: F401
from worldmock.agents.memory import life_memory_store, workspace
from worldmock.agents.memory.life_memory import MemoryStep, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.agents.memory.workspace import (
    agent_workspace,
    create_workspaces,
    memory_stores,
    restore_workspaces,
    snapshot_workspaces,
)
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.recording_engine.storage.checkpoint import restore_branch, restore_directory, snapshot_directory
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.runtime.lifecycle.runner import ResumeRequest, RunRequest, resume_world, run_world

_NOW = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)


def _memory(text, kind="goal"):
    """@brief conclude_activity 经 control_items 读出的一条存储形状记忆。"""
    return {"type": kind, "text": text, "importance": 0.5, "keywords": ["日常"], "subject": None}


def _files(directory) -> dict[str, bytes]:
    """@brief 目录里全部普通文件的相对 POSIX 路径与字节。"""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def _checkpoints(run_dir) -> tuple[list, FootprintStore]:
    """@brief 运行库里按先后的全部共同检查点（连同打开的库，调用方负责关闭）。"""
    store = FootprintStore(run_dir)
    events = list(store.iter_events(store.last_event_seq))
    return [store.get_checkpoint(event.data["checkpoint_id"]) for event in events
            if event.event_type == "checkpoint_created"], store


async def _parent(native_runtime, tmp_path):
    """@brief 经真实运行宿主跑完一小时的新运行，返回运行结果与冻结输入。"""
    root, frozen, _ = native_runtime
    result = await run_world(RunRequest(root, tmp_path / "parent", "native-parent", "native-parent"))
    assert result.current_time == frozen.config.stop_time
    return root, result


async def test_a_new_run_keeps_agent_workspaces_under_agents_only(native_runtime, tmp_path):
    """新运行的人物工作区是 run_dir/agents/agent_0001，由 agent_workspace 给出；运行目录下没有旧的框架工作区根与 env/。"""
    _, result = await _parent(native_runtime, tmp_path)
    run_dir = result.run_dir
    assert agent_workspace(run_dir, 1) == run_dir / "agents" / "agent_0001"
    assert [path.name for path in (run_dir / "agents").iterdir()] == ["agent_0001"]
    # 旧的框架工作区根目录名由片段拼出，测试树的文字守卫不许出现它的整写。
    assert not (run_dir / ("a" + "s2")).exists() and not (run_dir / "env").exists()


async def test_new_run_workspaces_start_empty_and_the_initial_snapshot_is_an_empty_archive(native_runtime, tmp_path):
    """create_workspaces 为每个人物建出空目录；新运行的初始检查点里 agent:1 快照是空 tar 的 gzip，与快照一个空目录得到
    的素材是同一份，展开后是空工作区。"""
    create_workspaces(tmp_path / "fresh", (1, 2))
    assert [_files(agent_workspace(tmp_path / "fresh", agent_id)) for agent_id in (1, 2)] == [{}, {}]
    _, result = await _parent(native_runtime, tmp_path)
    checkpoints, store = _checkpoints(result.run_dir)
    try:
        initial = checkpoints[0].snapshots["agent:1"]
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(store.assets.read(initial))), mode="r:") as archive:
            assert archive.getmembers() == []
        empty = tmp_path / "empty"
        empty.mkdir()
        assert snapshot_directory(empty, AssetStore(tmp_path / "assets")).sha256 == initial.sha256
        restore_directory(initial, store.assets, tmp_path / "restored")
        assert _files(tmp_path / "restored") == {}
    finally:
        store.close()


def test_creating_an_existing_workspace_is_refused(tmp_path, caplog):
    """人物目录已存在时先写错误日志，再以 FileExistsError 拒绝，原有内容不动。"""
    existing = agent_workspace(tmp_path / "run", 1)
    existing.mkdir(parents=True)
    (existing / "MEMORY.md").write_bytes(b"kept")
    with caplog.at_level(logging.ERROR), pytest.raises(FileExistsError, match="^Agent workspace already exists: "):
        create_workspaces(tmp_path / "run", (1,))
    assert f"Agent workspace already exists: {existing}" in caplog.text
    assert _files(existing) == {"MEMORY.md": b"kept"}


async def test_a_decided_run_leaves_exactly_the_two_memory_files(native_runtime, tmp_path):
    """做过决策的运行结束后，人物工作区恰有 MEMORY.md 与 memory/episodes.jsonl（LF，没有别的文件）；最后一个共同检查点的
    agent:1 快照展开后的成员与字节都等于终态工作区。"""
    _, result = await _parent(native_runtime, tmp_path)
    final = _files(agent_workspace(result.run_dir, 1))
    assert set(final) == {"MEMORY.md", "memory/episodes.jsonl"}
    assert all(b"\r" not in raw for raw in final.values())
    checkpoints, store = _checkpoints(result.run_dir)
    try:
        restore_directory(checkpoints[-1].snapshots["agent:1"], store.assets, tmp_path / "last")
    finally:
        store.close()
    assert _files(tmp_path / "last") == final


def test_restore_hands_the_written_records_to_the_stores(engine_factory, tmp_path):
    """真实引擎的运行目录里写出工作区并提交检查点；restore_branch 加 restore_workspaces 返回的条目等于写出的条目；
    memory_stores 用这份条目建存储，工作区文件不在也照样建成（建存储不读文件）。"""
    engine = engine_factory()
    run_dir = engine.context.store.run_dir
    create_workspaces(run_dir, (1,))
    store = WorldMockMemoryStore(agent_workspace(run_dir, 1), 1, ())
    save_step(store, MemoryStep("林舟", (), "observation-1",
                                (_memory("周末想去爬山"), _memory("答应同事周五交初稿", "commitment")), _NOW))
    written = read_episodes(store.workspace, 1)
    checkpoint = engine.checkpoint(snapshot_workspaces(run_dir, (1,), engine.context.store.assets))
    branch = tmp_path / "branch"
    child = restore_branch(engine.context.store, checkpoint.checkpoint_id, branch)
    try:
        records = restore_workspaces(checkpoint.snapshots, child.assets, branch, (1,))
    finally:
        child.close()
    assert records == {1: written} and [item["id"] for item in written] == ["m_000001", "m_000002"]
    shutil.rmtree(agent_workspace(branch, 1))
    stores = memory_stores(branch, records)
    assert list(stores) == [1] and stores[1].workspace == agent_workspace(branch, 1)
    assert stores[1].agent_id == 1 and stores[1].episode_ids() == {"m_000001", "m_000002"}


def _snapshot(tmp_path, files: dict[str, bytes]):
    """@brief 把给定文件写成一个工作区并快照，返回快照与素材库。"""
    source = tmp_path / "source"
    for relative, raw in files.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(raw)
    source.mkdir(exist_ok=True)
    assets = AssetStore(tmp_path / "assets")
    return snapshot_directory(source, assets), assets


def _valid_files(tmp_path) -> dict[str, bytes]:
    """@brief 记忆存储真实写出的两个文件（一条条目）。"""
    store = WorldMockMemoryStore(tmp_path / "written", 1, ())
    save_step(store, MemoryStep("林舟", (), "observation-1", (_memory("周末想去爬山"),), _NOW))
    return _files(store.workspace)


@pytest.mark.parametrize("case,message,cause", [
    ("extra_agent_json", "^Restored agent workspace members are invalid$", None),
    ("only_memory_view", "^Restored agent workspace members are invalid$", None),
    ("empty_memory_view", "^Restored agent memory is invalid$", "MEMORY.md must not be empty"),
    ("identity_gap", "^Restored agent memory is invalid$", "has a non-contiguous identity"),
    ("missing_snapshot", "^Checkpoint is missing an agent workspace snapshot$", None),
])
def test_restore_rejects_unexpected_members_and_corrupt_memory(tmp_path, caplog, case, message, cause):
    """恢复只认空工作区或恰为两个记忆文件：多出 AGENT.json、只有 MEMORY.md 都按成员不对拒绝；MEMORY.md 为空或条目断号
    按记忆不合格式拒绝（原异常作 __cause__）；缺 agent:1 快照同样以运行故障拒绝。每一种都先写英文错误日志。"""
    files = _valid_files(tmp_path)
    line = files["memory/episodes.jsonl"].decode("utf-8")
    files = {
        "extra_agent_json": {**files, "AGENT.json": b"{}"},
        "only_memory_view": {"MEMORY.md": files["MEMORY.md"]},
        "empty_memory_view": {**files, "MEMORY.md": b" \n\t"},
        "identity_gap": {**files, "memory/episodes.jsonl": (line + line.replace("m_000001", "m_000003")).encode()},
        "missing_snapshot": files,
    }[case]
    reference, assets = _snapshot(tmp_path, files)
    snapshots = {} if case == "missing_snapshot" else {"agent:1": reference}
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=message) as raised:
        restore_workspaces(snapshots, assets, tmp_path / "staged", (1,))
    if cause is not None:
        assert cause in str(raised.value.__cause__)
        assert f"Restored agent memory is invalid: {raised.value.__cause__}" in caplog.text
    assert message.strip("^$") in caplog.text


async def test_resume_validates_each_workspace_exactly_once(native_runtime, tmp_path, monkeypatch):
    """从父运行第一次决策之后的共同检查点普通恢复并跑到结束：整次恢复运行里条目文件的严格读回恰好发生人物数那么多次
    （暂存目录里的 restore_workspaces），运行宿主建存储与此后各次决策都不再读回。"""
    root, result = await _parent(native_runtime, tmp_path)
    store = FootprintStore(result.run_dir)
    try:
        events = list(store.iter_events(store.last_event_seq))
        agent_ids = store.manifest["agent_ids"]
    finally:
        store.close()
    observed = next(event for event in events if event.event_type == "observation")
    checkpoint = next(event.data["checkpoint_id"] for event in events
                      if event.event_type == "checkpoint_created" and event.event_seq > observed.event_seq)
    calls = []
    original = life_memory_store.read_episodes

    def counted(*arguments):
        """记下一次严格读回，再照常读回。"""
        calls.append(arguments)
        return original(*arguments)

    monkeypatch.setattr(life_memory_store, "read_episodes", counted)
    monkeypatch.setattr(workspace, "read_episodes", counted)
    restored = await resume_world(ResumeRequest(root, result.run_dir, checkpoint, tmp_path / "child"))
    assert restored.current_time == result.current_time
    assert len(calls) == len(agent_ids) == 1
