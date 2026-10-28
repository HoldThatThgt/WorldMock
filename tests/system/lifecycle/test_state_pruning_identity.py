"""删去已处理动作不改变记录的事实：两份脚本化完整日各录两遍，一遍照常删去规则不再读取的已处理动作，一遍一条不删；
事件、导出工件和检查点里除 processed_actions 之外的一切逐字节相同（docs/design/04-simulation-engine.md「原子性与恢复」）。

对照在同一台机器、同一份代码上录两遍，所以不依赖冻结基线。
"""

import json
import sqlite3
from pathlib import Path

import pytest

import worldmock.simulation_engine.scheduling.clock.time_advance
from tests.life.day_baseline_runtime import record_complete_day
from worldmock.recording_engine.storage.store import FootprintStore

PROJECT = Path(__file__).resolve().parents[3]


def _events(case: Path) -> list[tuple]:
    """运行库里按序号排列的全部事件行。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        return connection.execute("SELECT event_seq, event_id, body FROM events ORDER BY event_seq").fetchall()


def _export_files(case: Path) -> dict[str, bytes]:
    """导出目录里除 manifest.json 之外的每个文件的字节，按相对路径。"""
    root = case / "export"
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"}


def _without(mapping: dict, key: str) -> dict:
    """去掉一个键的浅副本。"""
    return {name: value for name, value in mapping.items() if name != key}


def _checkpoint_states(case: Path) -> dict[str, dict]:
    """经生产读取方取出的每个检查点的世界状态，按检查点行的先后。"""
    store = FootprintStore(case / "run")
    try:
        identities = [row[0] for row in store.connection.execute(
            "SELECT checkpoint_id FROM checkpoints ORDER BY rowid")]
        return {identity: store.get_checkpoint(identity).state for identity in identities}
    finally:
        store.close()


def _ledger_is_readable(state: dict) -> None:
    """每个条目要么是本秒结算的结果，要么是世界里某项活动的开始动作；进行中或暂停活动的开始动作都在。"""
    activities = state["activities"]
    origins = {activity["origin_action_id"] for activity in activities}
    live = {activity["origin_action_id"] for activity in activities if activity["status"] in {"active", "paused"}}
    for key, stored in state["processed_actions"].items():
        assert key in origins or stored["result"]["occurred_at"] == state["current_time"], key
    assert live <= set(state["processed_actions"])


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_pruning_leaves_every_event_and_export_artifact_unchanged(monkeypatch, tmp_path, kind):
    """照常删去已处理动作与一条不删各录一遍同一份完整日：事件逐行相同；导出里除清单外的文件逐字节相同、路径集合相同；
    清单去掉最终检查点后相同，检查点的世界去掉 processed_actions 后相同；两边的检查点身份相同，每个检查点的世界去掉
    processed_actions 后相同，删过的一边的条目是另一边的真子集且逐项相同，只含规则还会读取的条目。"""
    pruned, kept = tmp_path / "pruned" / kind, tmp_path / "kept" / kind
    record_complete_day(monkeypatch, pruned, PROJECT, kind)
    monkeypatch.setattr(worldmock.simulation_engine.scheduling.clock.time_advance, "prune_processed_actions", lambda state: state)
    record_complete_day(monkeypatch, kept, PROJECT, kind)

    assert _events(pruned) == _events(kept)
    files = _export_files(pruned)
    assert files == _export_files(kept)
    manifests = [json.loads((case / "export" / "manifest.json").read_text(encoding="utf-8")) for case in (pruned, kept)]
    assert _without(manifests[0], "checkpoint") == _without(manifests[1], "checkpoint")
    assert (_without(manifests[0]["checkpoint"]["state"], "processed_actions")
            == _without(manifests[1]["checkpoint"]["state"], "processed_actions"))
    points = [_checkpoint_states(case) for case in (pruned, kept)]
    assert list(points[0]) == list(points[1]) and points[0]
    for identity, state in points[0].items():
        other = points[1][identity]
        assert _without(state, "processed_actions") == _without(other, "processed_actions")
        assert all(other["processed_actions"].get(key) == entry for key, entry in state["processed_actions"].items())
        assert len(other["processed_actions"]) > len(state["processed_actions"])
        _ledger_is_readable(state)
