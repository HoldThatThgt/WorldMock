"""分支清单的 lineage：父运行各段按先后排列，结束原因只看父运行这一段自己的事件，导出与质量报告原样带出。"""

import copy
import json
from datetime import datetime

import pytest

from tests.life.conftest import advance_next
from tests.life.environment_runtime import run_to_end, settle_decision
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.recording_engine.export.export import export_run
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.reports.quality import _lineage_quality
from worldmock.recording_engine.validation.validation import _validate_identity
from worldmock.simulation_engine.engine import LifeEngine

#: lineage 每一项的键与精确类型（branched_at、last_recorded_at 是 UTC 墙钟文本）。
ENTRY_TYPES = {"run_id": str, "simulated_from": int, "checkpoint_id": str, "checkpoint_event_seq": int,
               "checkpoint_time": int, "last_event_seq": int, "last_event_time": int, "last_recorded_at": str,
               "discarded_events": int, "end_reason": str, "end_error": (str, type(None)), "branched_at": str}


def _checkpoint(engine):
    """在引擎当前时刻提交一个带测试人物快照的共同检查点。"""
    snapshot = engine.context.store.assets.put_json({"agent": 1}, {"kind": "test_agent_snapshot"})
    return engine.checkpoint({"agent:1": snapshot})


def _extra_event(engine, label):
    """追加一条与终态无关的证据事件，只为让父运行在检查点之后多走一步。"""
    draft = EventDraft(event_type="model_request", occurred_at=engine.context.state.current_time, agent_id=1,
                       visibility="system", source_kind="model", data={"phase": "request", "call_id": label})
    return engine.context.store.append_evidence([draft])[-1]


def _branch(engine, checkpoint, destination):
    """从引擎的存储在检查点分出新运行，关闭分支存储后以同一冻结输入打开为引擎。"""
    restore_branch(engine.context.store, checkpoint.checkpoint_id, destination).close()
    return LifeEngine.open(engine.context.inputs, destination)


def _move_on(engine):
    """处理当前决策机会后推进到下一个边界，使检查点时刻晚于运行开始（导出要求正的时间窗口）。"""
    if engine.scheduling_state().ready_agent_ids:
        settle_decision(engine, f"move-{engine.context.state.current_time}")
    advance_next(engine)
    assert engine.context.state.current_time > engine.context.store.manifest["window"]["start"]


def _chain(engine_factory, tmp_path):
    """父运行 P 失败、子运行 A 被结束（没有终态事件）、孙运行 B 从 A 分出；返回三个引擎与两个检查点。"""
    parent = engine_factory()
    _move_on(parent)
    first = _checkpoint(parent)
    _extra_event(parent, "p-1")
    _extra_event(parent, "p-2")
    parent.fail_run("RuntimeError('boom')")
    child = _branch(parent, first, tmp_path / "child")
    second = _checkpoint(child)
    _extra_event(child, "a-1")
    grandchild = _branch(child, second, tmp_path / "grandchild")
    return (parent, child, grandchild), (first, second)


def test_fresh_run_manifest_has_empty_lineage_and_structure_seven(engine_factory):
    """新建运行的清单结构号是 7，lineage 是空数组。"""
    manifest = engine_factory().context.store.manifest
    assert manifest["lineage"] == []
    assert manifest["schema_version"] == 7


def test_lineage_chains_parents_in_order_with_their_end_reasons(engine_factory, tmp_path):
    """孙运行的 lineage 依次是 P、A 两段：P 以 run_failed 结束并带它的错误，A 没有终态事件；丢弃事件数是各段检查点
    之后的尾长，A 段的模拟起点是 P 段的检查点时刻；每个键与类型都如规定，source_run_ids 仍是排序的集合。"""
    (parent, child, grandchild), (first, second) = _chain(engine_factory, tmp_path)
    lineage = grandchild.context.store.manifest["lineage"]
    p_store, a_store = parent.context.store, child.context.store
    assert [entry["run_id"] for entry in lineage] == [p_store.manifest["run_id"], a_store.manifest["run_id"]]
    for entry in lineage:
        assert set(entry) == set(ENTRY_TYPES)
        assert all(isinstance(entry[key], kind) and type(entry[key]) is not bool for key, kind in ENTRY_TYPES.items())
        assert datetime.fromisoformat(entry["branched_at"]).utcoffset().total_seconds() == 0
    p_entry, a_entry = lineage
    assert (p_entry["end_reason"], p_entry["end_error"]) == ("run_failed", "RuntimeError('boom')")
    assert (a_entry["end_reason"], a_entry["end_error"]) == ("no terminal event", None)
    assert p_entry["checkpoint_id"] == first.checkpoint_id and a_entry["checkpoint_id"] == second.checkpoint_id
    assert p_entry["checkpoint_event_seq"] == first.last_event_seq
    assert p_entry["last_event_seq"] == p_store.last_event_seq
    assert p_entry["discarded_events"] == p_store.last_event_seq - first.last_event_seq == 3
    assert a_entry["discarded_events"] == a_store.last_event_seq - second.last_event_seq == 1
    assert p_entry["simulated_from"] == p_store.manifest["window"]["start"]
    assert a_entry["simulated_from"] == p_entry["checkpoint_time"]
    assert child.context.store.manifest["lineage"] == [p_entry]
    run_ids = {p_store.manifest["run_id"], a_store.manifest["run_id"], grandchild.context.store.manifest["run_id"]}
    assert grandchild.context.store.manifest["source_run_ids"] == sorted(run_ids)


def test_terminal_lookup_ignores_ancestor_events_and_takes_the_last(engine_factory, tmp_path):
    """同一段里先 run_ended 后 run_failed 时取后者；子运行复制来的祖先 run_ended 不算子运行这一段的终态。"""
    parent = engine_factory()
    run_to_end(parent)
    assert parent.context.store.has_event("run_ended")
    end_point = _checkpoint(parent)
    child = _branch(parent, end_point, tmp_path / "child")
    parent.fail_run("RuntimeError('late')")
    later = _branch(parent, end_point, tmp_path / "later")
    assert later.context.store.manifest["lineage"][-1]["end_reason"] == "run_failed"
    second = _checkpoint(child)
    _extra_event(child, "a-1")
    grandchild = _branch(child, second, tmp_path / "grandchild")
    assert child.context.store.has_event("run_ended")
    assert [entry["end_reason"] for entry in grandchild.context.store.manifest["lineage"]] == [
        "run_ended", "no terminal event"]


def test_terminal_event_lookup_uses_the_agent_type_index(engine_factory):
    """终态查询经人物与类型表达式索引，不扫描事件表。"""
    connection = engine_factory().context.store.connection
    plan = " ".join(row[-1] for row in connection.execute(
        "EXPLAIN QUERY PLAN SELECT body FROM events WHERE json_extract(body,'$.agent_id') IS NULL "
        "AND json_extract(body,'$.event_type') IN ('run_failed','run_ended') AND event_seq>? "
        "ORDER BY event_seq DESC LIMIT 1", (0,)))
    assert "USING INDEX events_agent_type" in plan


def test_export_and_quality_report_carry_lineage(engine_factory, tmp_path):
    """孙运行导出：导出清单、运行清单与冻结运行清单的 lineage 相同；质量报告 lineage 节有三段、各一个结束原因，
    每行停机秒数等于分出时刻减父运行最后一条事件的落盘时刻。"""
    (_, _, grandchild), _ = _chain(engine_factory, tmp_path)
    store = grandchild.context.store
    output = tmp_path / "export"
    manifest = export_run(store, store.last_event_seq, output)
    assert manifest["lineage"] == store.manifest["lineage"] == manifest["frozen_run_manifest"]["lineage"]
    quality = json.loads((output / "quality.json").read_text(encoding="utf-8"))
    section = quality["lineage"]
    assert section["segment_count"] == 3
    assert section["end_reason_counts"] == {"no terminal event": 1, "run_failed": 1}
    for row, entry in zip(section["parents"], store.manifest["lineage"], strict=True):
        expected = (datetime.fromisoformat(entry["branched_at"])
                    - datetime.fromisoformat(entry["last_recorded_at"])).total_seconds()
        assert row["downtime_seconds"] == round(expected, 3)


def test_export_lineage_must_equal_the_frozen_run_manifest(engine_factory, tmp_path):
    """完整分支导出的清单认证通过；把导出清单 lineage 里的一个值改掉，认证报 lineage 与冻结运行清单不符。"""
    parent = engine_factory()
    run_to_end(parent)
    child = _branch(parent, _checkpoint(parent), tmp_path / "child")
    _checkpoint(child)
    store = child.context.store
    export_run(store, store.last_event_seq, tmp_path / "export")
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["experiment_completed"] is True and len(manifest["lineage"]) == 1
    _validate_identity(manifest)
    changed = copy.deepcopy(manifest)
    changed["lineage"][0]["discarded_events"] += 1
    with pytest.raises(IntegrityError, match="^Export lineage disagrees with the frozen run manifest$"):
        _validate_identity(changed)


def _entry(run_id, reason, branched_at, last_recorded_at, discarded):
    """30 天运行一段父运行的 lineage 项（只填质量报告读的键与停机时刻）。"""
    return {"run_id": run_id, "checkpoint_id": f"{run_id}-checkpoint", "end_reason": reason,
            "end_error": "RuntimeError('Agent decision failed')" if reason == "run_failed" else None,
            "discarded_events": discarded, "branched_at": branched_at, "last_recorded_at": last_recorded_at}


def test_quality_lineage_section_is_computed_from_the_manifest_only():
    """30 天运行的四段父运行（结束原因取自 tail_probe）：计数、三位小数的停机秒数与段数 5 都只由清单算出。"""
    lineage = [
        _entry("6c406d98", "no terminal event", "2026-10-14T09:28:50.300000+00:00",
               "2026-10-14T09:21:00.000000+00:00", 3),
        _entry("segment-2", "run_failed", "2026-10-14T12:35:24.2004+00:00", "2026-10-14T12:00:00+00:00", 6),
        _entry("segment-3", "run_failed", "2026-10-14T18:25:11.5+00:00", "2026-10-14T18:21:00+00:00", 6),
        _entry("segment-4", "run_failed", "2026-10-15T03:05:32+00:00", "2026-10-15T03:05:00.0011+00:00", 6),
    ]
    section = _lineage_quality({"lineage": lineage})
    assert section["segment_count"] == 5
    assert section["end_reason_counts"] == {"no terminal event": 1, "run_failed": 3}
    assert list(section["end_reason_counts"]) == ["no terminal event", "run_failed"]
    assert [row["downtime_seconds"] for row in section["parents"]] == [470.3, 2124.2, 251.5, 31.999]
    assert section["downtime_seconds"] == round(470.3 + 2124.2 + 251.5 + 31.999, 3)
    assert [row["discarded_events"] for row in section["parents"]] == [3, 6, 6, 6]
    assert set(section["parents"][0]) == {"run_id", "checkpoint_id", "end_reason", "end_error", "discarded_events",
                                          "downtime_seconds"}
    assert _lineage_quality({"lineage": []}) == {
        "segment_count": 1, "parents": [], "end_reason_counts": {}, "downtime_seconds": 0.0,
        "method": section["method"]}
