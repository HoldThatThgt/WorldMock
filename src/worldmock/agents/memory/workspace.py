"""@brief 人物工作区：运行目录下每个人物一个目录，只放记忆视图与条目文件；负责新建、共同检查点快照、
恢复时唯一一次严格校验与建记忆存储，不导入引擎。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worldmock.agents.memory.life_memory_store import (
    EPISODES_FILE,
    MEMORY_VIEW_FILE,
    WorldMockMemoryStore,
    read_episodes,
)
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import AssetRef
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.recording_engine.storage.checkpoint import restore_directory, snapshot_directory

logger = logging.getLogger(__name__)

#: 运行目录下人物工作区的根目录名。
WORKSPACES_DIR = "agents"
#: 第一次决策之后工作区的完整成员；第一次决策之前工作区为空。
WORKSPACE_MEMBERS = frozenset({MEMORY_VIEW_FILE, EPISODES_FILE})


def agent_workspace(run_dir: Path, agent_id: int) -> Path:
    """@brief 人物的工作区 run_dir/agents/agent_NNNN；生产代码里只有这一处拼这条路径。"""
    return Path(run_dir) / WORKSPACES_DIR / f"agent_{agent_id:04d}"


def create_workspaces(run_dir: Path, agent_ids: Sequence[int]) -> None:
    """@brief 新建运行时为每个人物建出空工作区；人物目录已存在时先写错误日志，再以 FileExistsError 拒绝。"""
    for agent_id in agent_ids:
        path = agent_workspace(run_dir, agent_id)
        if path.exists():
            logger.error("Agent workspace already exists: %s", path)
            raise FileExistsError(f"Agent workspace already exists: {path}")
        path.mkdir(parents=True)


def snapshot_workspaces(run_dir: Path, agent_ids: Sequence[int], assets: AssetStore) -> dict[str, AssetRef]:
    """@brief 按 agent_ids 的次序快照每个人物工作区，得到共同检查点的 agent:<编号> 参与者快照。"""
    return {f"agent:{agent_id}": snapshot_directory(agent_workspace(run_dir, agent_id), assets)
            for agent_id in agent_ids}


def restore_workspaces(snapshots: Mapping[str, AssetRef], assets: AssetStore, run_dir: Path,
                       agent_ids: Sequence[int]) -> dict[int, tuple[dict[str, Any], ...]]:
    """@brief 恢复与重放在暂存目录里按人物次序展开快照并严格校验一次：成员为空或恰为两个文件，MEMORY.md 非空，
    条目逐行读回；任一不成立都在发布子目录之前停止。

    :returns: 人物编号到已校验条目的映射，交给运行宿主建记忆存储。
    :raises IntegrityError: 缺快照、成员不对或记忆不合格式。
    """
    records: dict[int, tuple[dict[str, Any], ...]] = {}
    for agent_id in agent_ids:
        reference = snapshots.get(f"agent:{agent_id}")
        if reference is None:
            logger.error("Checkpoint is missing an agent workspace snapshot")
            raise IntegrityError("Checkpoint is missing an agent workspace snapshot")
        workspace = agent_workspace(run_dir, agent_id)
        restore_directory(reference, assets, workspace)
        members = _workspace_members(workspace)
        if members and members != WORKSPACE_MEMBERS:
            logger.error("Restored agent workspace members are invalid")
            raise IntegrityError("Restored agent workspace members are invalid")
        records[agent_id] = _restored_records(workspace, agent_id, members)
    return records


def memory_stores(run_dir: Path, records: Mapping[int, Sequence[Mapping[str, Any]]]
                  ) -> dict[int, WorldMockMemoryStore]:
    """@brief 按人物编号升序为每个人物建一个整次运行共用的记忆存储；人物集合就是 records 的键，建存储不读文件。"""
    return {agent_id: WorldMockMemoryStore(agent_workspace(run_dir, agent_id), agent_id, records[agent_id])
            for agent_id in sorted(records)}


def _workspace_members(workspace: Path) -> frozenset[str]:
    """@brief 工作区全部普通文件的相对 POSIX 路径。"""
    return frozenset(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file())


def _restored_records(workspace: Path, agent_id: int, members: frozenset[str]) -> tuple[dict[str, Any], ...]:
    """@brief 恢复校验的末步：成员为空时没有条目；否则 MEMORY.md 去掉空白后非空，再严格读回条目文件。
    读取、解码与格式错误都先写日志，再改报 IntegrityError，原异常作 __cause__。"""
    if not members:
        return ()
    try:
        if not (workspace / MEMORY_VIEW_FILE).read_text(encoding="utf-8").strip():
            raise ValueError("MEMORY.md must not be empty")
        return read_episodes(workspace, agent_id)
    except (OSError, UnicodeError, ValueError) as error:
        logger.error("Restored agent memory is invalid: %s", error)
        raise IntegrityError("Restored agent memory is invalid") from error
