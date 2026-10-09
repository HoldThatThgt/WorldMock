"""@brief C4f 分支覆盖补测：quality/validation/store/checkpoint/export/ledger/evidence/assets。

只新增正/反例行为测试，不改动生产代码与既有用例；所有校验拒绝路径按
fail-closed 语义断言 IntegrityError。
"""

from __future__ import annotations
from worldmock.footprints.checkpoint import restore_branch

import hashlib
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints import evidence, export as export_module
from worldmock.footprints import timeline_labels as timeline_module
from worldmock.footprints.assets import AssetStore, atomic_write
from worldmock.footprints.export import export_run, reconstruct_activities
from worldmock.footprints.ledger import (
    validate_account_history,
    validate_account_transition,
)
from worldmock.footprints.models import AssetRef, Event, EventDraft, canonical_json
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export


# ---------------------------------------------------------------------------
# 共用构造器
# ---------------------------------------------------------------------------


def _event(sequence, event_type, *, occurred_at=None, run_id="run", wall_ms=None, **fields):
    data = fields.pop("data", {})
    if event_type == "run_ended":
        fields.setdefault("visibility", "system")
    return Event(
        event_id=f"event-{sequence}", event_type=event_type,
        occurred_at=100 + sequence if occurred_at is None else occurred_at,
        run_id=run_id, event_seq=sequence,
        recorded_at=fields.pop("recorded_at", "2026-09-15T07:00:00+00:00"),
        wall_elapsed_ms=sequence * 100 if wall_ms is None else wall_ms,
        state_version_before=0, state_version_after=0, data=data, **fields,
    )


def _manifest(start=100, end=1100, **extra):
    payload = {"run_id": "run", "source_group_id": "group", "work_content_ids": {"1": []},
               "window": {"start": start, "end": end, "timezone": "Asia/Shanghai"}}
    payload.update(extra)
    return payload


def _activity(identity, kind, elapsed, status="completed", **fields):
    return {"activity_id": identity, "agent_id": 1, "activity_type": kind,
            "elapsed_seconds": elapsed, "status": status, **fields}


def _conclusion(*memories):
    """结束一项活动的完整现行控制请求。"""
    return {"items": [{"activity_name": "等待（1月5日 00:00 起）", "summary": "等完了。",
                       "memories": list(memories), "views": []}], "errands": []}


def _tool_response(call_id, arguments, name="conclude_activity", usage=None, error=None, model="deepseek-flash"):
    response = {
        "phase": "response", "call_id": call_id, "attempt": 1, "error": error,
        "response": {
            "model": model, "usage": usage,
            "choices": [{"message": {"tool_calls": [{
                "function": {"name": name, "arguments": arguments},
            }]}}],
        },
    }
    return response


def _usage(**extra):
    payload = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
               "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}
    payload.update(extra)
    return payload


def _run_report(events, activities, manifest):
    return build_quality_report(events, activities, manifest)


def _make_store(tmp_path, agent_ids=(1,)):
    manifest = {"run_id": "run", "source_group_id": "run", "agent_ids": list(agent_ids),
                "environment_assets": {},
                "window": {"start": 100, "end": 300, "timezone": "UTC"}}
    return FootprintStore.create(tmp_path / "run", manifest,
                                 {"version": 0, "current_time": 100})




def _rewrite_jsonl(directory, name, rows):
    """@brief 重写一个导出 JSONL 并同步清单身份（哈希/行数）。"""
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    (directory / name).write_bytes(raw)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = {"path": name, "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    if name == "events.jsonl":
        manifest["artifacts"]["events"] = descriptor
    elif name == "activities.jsonl":
        manifest["artifacts"]["activities"] = descriptor
    elif name == "verified-experiences.jsonl":
        manifest["artifacts"]["verified_experiences"] = descriptor
    else:
        manifest["asset_index"] = descriptor
    manifest_path.write_text(json.dumps(manifest))


def _load_manifest_json(directory):
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def _save_manifest_json(directory, manifest):
    (directory / "manifest.json").write_text(json.dumps(manifest))


def _rehash_checkpoint(manifest):
    """@brief 修正被篡改 checkpoint 的 record_sha256，使行内身份校验先通过。"""
    checkpoint = dict(manifest["checkpoint"])
    checkpoint.pop("record_sha256")
    manifest["checkpoint"]["record_sha256"] = hashlib.sha256(
        canonical_json(checkpoint)).hexdigest()
    return manifest


# ---------------------------------------------------------------------------
# quality.py 缺口
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("manifest", [
    "not-a-dict",
    {"window": {"start": 1, "end": 2}},
    {"run_id": "run", "window": "not-a-dict"},
    {"run_id": "run", "window": {"start": "x", "end": 2}},
    {"run_id": "run", "window": {"start": 5, "end": 5}},
])
def test_quality_report_rejects_invalid_manifest_shapes(manifest):
    events = [_event(1, "run_ended", occurred_at=200)]
    with pytest.raises(IntegrityError):
        build_quality_report(events, [], manifest)


def test_quality_report_rejects_invalid_activity_identities():
    events = [_event(1, "run_ended", occurred_at=200)]
    with pytest.raises(IntegrityError):
        build_quality_report(events, [{"activity_id": ""}], _manifest(end=200))
    with pytest.raises(IntegrityError):
        build_quality_report(events, [{"agent_id": 1}], _manifest(end=200))
    duplicate = [_activity("same", "work", 1), _activity("same", "work", 2)]
    with pytest.raises(IntegrityError):
        build_quality_report(events, duplicate, _manifest(end=200))


def test_action_result_status_buckets_report_missing_and_unrecognized():
    events = [
        _event(1, "action_result", action_id="a", data={"status": 7}),
        _event(2, "action_result", action_id="b", data={"status": "teleported"}),
        _event(3, "run_ended", occurred_at=200),
    ]
    actions = _run_report(events, [], _manifest(end=200))["actions"]
    assert actions["result_events_missing_status"] == ["event-1"]
    assert actions["unrecognized_result_status_counts"] == {"teleported": 1}


def test_watch_repeat_report_missing_targets_and_single_session_groups():
    """@brief 活动声明不产生接触，只有真实消费事件中的已完成轮数产生重复计数。"""
    from tests.unit.footprints.test_quality import _content_event
    activities = [_activity('phone-1', 'use_app', 60, target_id='bilibili', data={'repeat': True}),
                  _activity('phone-2', 'use_app', 60, target_id=None)]
    end = _event(1, 'run_ended', occurred_at=200)
    assert _run_report([end], activities, _manifest(end=200))['content_contact']['repeat_watch'] == 0
    actual = _content_event(1, after=60, rounds=1)
    report = _run_report([actual], activities, _manifest(end=200))['content_contact']
    assert report['repeat_watch'] == 1
    assert report['by_app'] == {'bilibili': 1}
    assert 'by_action' not in report
    assert report['repeat_seconds_share'] == 1.0


def test_efficiency_reports_missing_and_partial_samples():
    events = [
        _event(1, "environment_event", agent_id=1,
               data={"kind": "goods_received", "category": "daily", "provided": 0.05, "applied": 0.02}),
        _event(2, "environment_event", agent_id=1,
               data={"kind": "goods_received", "category": "meal", "provided": 400, "applied": None}),
        _event(3, "run_ended", occurred_at=200),
    ]
    efficiency = _run_report(events, [], _manifest(end=200))["conversion_efficiency"]
    assert "work" not in efficiency
    assert efficiency["food"]["available"] is False
    assert efficiency["food"]["missing_sample_count"] == 1
    assert efficiency["food"]["unavailable_or_incomplete_reason"] == "all_efficiency_samples_missing"


def test_memory_quality_reports_each_problem_code():
    """记忆质量的问题码闭集按写入点核对（docs/design/04-simulation-engine.md 第七章「来源由系统记录」）。

    记忆不再带引用、没有 memory_read 与 finish，依赖它们的问题码（unknown_memory_reference、
    finish_without_memory_update、memory_update_without_successful_finish、invalid_memory_tool_arguments、
    invalid_memory_reference_shape）不再出现；trigger 或 decision_id 畸形归入 invalid_memory_update。
    """
    def update_data(identity, decision_id, trigger=("activity_conclusion",), merged=()):
        return {"operation": "append_episodes", "created_episode_ids": [] if merged else [identity],
                "merged_episode_ids": list(merged), "episodes": [{"id": identity, "type": "goal",
                "text": "我想出门", "epistemic_status": "subjective"}],
                "memory_before_sha256": "a" * 64, "memory_sha256": "b" * 64,
                "episodes_sha256": "c" * 64, "state_sha256": "d" * 64,
                "consolidated": False, "observation_event_id": "observation-absent",
                "verified_event_ids": [], "trigger": list(trigger), "decision_id": decision_id}
    memory_item = {"type": "goal", "text": "我想出门", "importance": 0.5, "keywords": ["出门"]}
    events = [
        _event(1, "model_response", agent_id=1, decision_id="d1",
               data={**_tool_response("call-1", "{not json", error="invalid conclude_activity arguments: memories"),
                     "protocol_feedback": {"error_code": "ARGUMENTS_INVALID", "reason": "Invalid memory.",
                                           "fix": "Correct the memory.", "unmet": [], "items": [
                                               {"path": ["tool_calls", 0, "function", "arguments", "items", 0,
                                                         "memories"], "written": None,
                                                "error_code": "ARGUMENTS_INVALID", "allowed_values": [],
                                                "expected": "Valid memory entries are required.",
                                                "why": "The memory is invalid.", "candidates": []}]}}),
        _event(2, "model_response", agent_id=1, decision_id="d1",
               data=_tool_response("call-2", {"query": "x"}, name="search", usage=_usage())),
        _event(3, "memory_updated", agent_id=1, decision_id="d1", data={"episode_ids": "bad"}),
        _event(4, "action_requested", agent_id=1, decision_id="d2", action_id="a2",
               data={"tool_name": "conclude_activity", "request": _conclusion(memory_item)}),
        _event(5, "memory_updated", agent_id=1, decision_id="d2", data=update_data("m1", "d2")),
        _event(6, "memory_updated", agent_id=1, decision_id="d2", data=update_data("m1", "d2")),
        _event(7, "memory_updated", agent_id=1, decision_id="d2", data=update_data("m2", "d2", ("finish",))),
        _event(8, "memory_updated", agent_id=1, decision_id="d2", data=update_data("m3", "another")),
        _event(9, "memory_updated", agent_id=1, decision_id="d3",
               data=update_data("absent", "d3", ("reflection",), merged=("absent",))),
        _event(10, "action_requested", agent_id=1, decision_id="d4", action_id="a4",
               data={"tool_name": "conclude_activity", "request": _conclusion(memory_item)}),
        _event(11, "run_failed", occurred_at=200, data={"error": "memories corrupted"}),
    ]
    memory = _run_report(events, [], _manifest(end=200))["memory_references"]
    codes = [(problem["code"], problem["event_id"]) for problem in memory["problems"]]
    assert codes == [
        ("memory_protocol_error", "event-1"),
        ("invalid_memory_update", "event-3"),
        ("invalid_memory_update_source_window", "event-5"),
        ("duplicate_memory_episode_id", "event-6"),
        ("invalid_memory_update_source_window", "event-6"),
        ("invalid_memory_update", "event-7"),
        ("invalid_memory_update", "event-8"),
        ("unknown_merged_memory_episode_id", "event-9"),
        ("invalid_memory_update_source_window", "event-9"),
        ("memory_update_without_source", "event-9"),
        ("memory_run_failure", "event-11"),
        ("conclusion_without_memory_update", "event-10"),
    ]
    assert memory["write_points_checked"] == 3


def test_model_usage_reports_cost_breakdown_rejections_and_pending_calls():
    naive_time = "2026-09-15T07:00:00"
    bad_tokens = _tool_response("call-1", _conclusion(),
                                usage={"prompt_tokens": "x", "completion_tokens": 2,
                                       "total_tokens": 3, "prompt_cache_hit_tokens": 0,
                                       "prompt_cache_miss_tokens": 100})
    disagree = _tool_response("call-2", _conclusion(), usage=_usage(prompt_cache_hit_tokens=50))
    events = [
        _event(1, "model_request", recorded_at=naive_time, data={"call_id": "call-0"}),
        _event(2, "model_response", recorded_at=naive_time,
               data=_tool_response("call-0", _conclusion(), usage=_usage())),
        _event(3, "model_request", data={"call_id": "call-1"}),
        _event(4, "model_response", data=bad_tokens),
        _event(5, "model_request", data={"call_id": "call-2"}),
        _event(6, "model_response", data=disagree),
        _event(7, "model_request", data={"call_id": "call-pending"}),
        _event(8, "run_ended", occurred_at=200),
    ]
    usage = _run_report(events, [], _manifest(end=200))["model_usage"]
    reasons = usage["estimated_cost"]["unavailable_reasons"]
    assert reasons == {"recorded_wall_time_is_invalid": 1,
                       "cache_or_token_breakdown_missing": 1,
                       "cache_breakdown_disagrees_with_input_tokens": 1,
                       "pending_call_cost_unknown": 1}
    assert usage["pending_call_ids"] == ["call-pending"]
    assert usage["token_usage"]["calls_without_provider_response"] == 1


def test_acceleration_rejects_events_before_window_and_reports_zero_wall_time():
    with pytest.raises(IntegrityError, match="precede the run window"):
        build_quality_report([_event(1, "run_ended", occurred_at=99)], [], _manifest())
    events = [_event(1, "run_ended", occurred_at=200, wall_ms=0)]
    acceleration = _run_report(events, [], _manifest(end=200))["acceleration"]
    assert acceleration["available"] is False
    assert acceleration["unavailable_reason"] == "observed_wall_time_is_zero"
    stalled = [_event(1, "run_ended", occurred_at=100, wall_ms=5)]
    acceleration = _run_report(stalled, [], _manifest(end=200))["acceleration"]
    assert acceleration["available"] is False
    assert acceleration["unavailable_reason"] == "simulation_time_did_not_advance"


# ---------------------------------------------------------------------------
# store.py 缺口
# ---------------------------------------------------------------------------


def test_store_open_rejects_missing_database(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text("{}")
    with pytest.raises(IntegrityError, match="does not exist"):
        FootprintStore(run)


def test_store_create_rejects_existing_artifacts(tmp_path):
    _make_store(tmp_path)
    with pytest.raises(IntegrityError, match="already exist"):
        FootprintStore.create(tmp_path / "run", {"run_id": "run"}, {"version": 0, "current_time": 0})


def test_store_create_rolls_back_when_manifest_unserializable(tmp_path):
    with pytest.raises(TypeError):
        FootprintStore.create(tmp_path / "run", {"run_id": "run", "bad": object()},
                              {"version": 0, "current_time": 0})
    leftovers = [item for item in os.listdir(tmp_path / "run") if item != "footprints.sqlite"]
    assert leftovers == []


def test_store_commit_rejects_clock_regression_and_version_jumps(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(IntegrityError, match="backwards"):
        store.commit({"version": 1, "current_time": 50},
                     [EventDraft(event_type="observation", occurred_at=50)])
    with pytest.raises(IntegrityError, match="discontinuous"):
        store.commit({"version": 2, "current_time": 100},
                     [EventDraft(event_type="observation", occurred_at=100)])
    with pytest.raises(IntegrityError, match="discontinuous"):
        store.commit({**store.state, "extra": 1},
                     [EventDraft(event_type="observation", occurred_at=100)])
    with pytest.raises(IntegrityError, match="must be integers"):
        store.commit({"version": "one", "current_time": 100},
                     [EventDraft(event_type="observation", occurred_at=100)])
    store.close()


def test_store_read_rejects_out_of_range_boundaries(tmp_path):
    store = _make_store(tmp_path)
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100,
                                          agent_id=1)])
    with pytest.raises(IntegrityError, match="boundary"):
        store.read_agent_events("observation", 1, after=5)
    with pytest.raises(IntegrityError, match="cutoff"):
        store.read_events(9)
    store.close()


def test_store_checkpoint_rejects_stale_state_and_empty_snapshots(tmp_path):
    store = _make_store(tmp_path)
    reference = store.assets.put_json({"snapshot": 1}, {"kind": "fixture"})
    with pytest.raises(IntegrityError, match="current world state"):
        store.checkpoint({"version": 9, "current_time": 100}, {"agent:1": reference, "life": reference})
    with pytest.raises(IntegrityError, match="participant snapshots"):
        store.checkpoint(store.state, {})
    store.close()


def _insert_checkpoint_row(store, body: dict, record_sha256: str):
    store.connection.execute("INSERT INTO checkpoints VALUES(?,?,?)", (
        body["checkpoint_id"], canonical_json(body).decode(), record_sha256))


def test_get_checkpoint_rejects_missing_damaged_and_inconsistent_records(tmp_path):
    store = _make_store(tmp_path)
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100)])
    with pytest.raises(IntegrityError, match="missing or its record is damaged"):
        store.get_checkpoint("no-such-checkpoint")

    reference = store.assets.put_json({"snapshot": 1}, {"kind": "fixture"})
    good = {"schema_version": 1, "checkpoint_id": "cp-damaged", "run_id": "run",
            "current_time": 100, "state_version": 0, "last_event_seq": 1,
            "state": {"version": 0, "current_time": 100},
            "snapshots": {"agent:1": reference.model_dump(), "life": reference.model_dump()},
            "manifest_sha256": hashlib.sha256(canonical_json(store.manifest)).hexdigest()}
    _insert_checkpoint_row(store, good, "0" * 64)
    with pytest.raises(IntegrityError, match="missing or its record is damaged"):
        store.get_checkpoint("cp-damaged")

    wrong_run = dict(good, checkpoint_id="cp-identity", run_id="other")
    _insert_checkpoint_row(store, wrong_run,
                           hashlib.sha256(canonical_json(wrong_run)).hexdigest())
    with pytest.raises(IntegrityError, match="identity, time, or version disagrees"):
        store.get_checkpoint("cp-identity")

    wrong_manifest = dict(good, checkpoint_id="cp-manifest",
                          manifest_sha256="0" * 64)
    _insert_checkpoint_row(store, wrong_manifest,
                           hashlib.sha256(canonical_json(wrong_manifest)).hexdigest())
    with pytest.raises(IntegrityError, match="frozen manifest disagree"):
        store.get_checkpoint("cp-manifest")

    orphan = dict(good, checkpoint_id="cp-orphan", last_event_seq=99)
    _insert_checkpoint_row(store, orphan, hashlib.sha256(canonical_json(orphan)).hexdigest())
    with pytest.raises(IntegrityError, match="event is missing"):
        store.get_checkpoint("cp-orphan")
    store.close()


# ---------------------------------------------------------------------------
# checkpoint.py 缺口
# ---------------------------------------------------------------------------


def test_snapshot_directory_skips_directories_and_packs_files(tmp_path):
    from worldmock.footprints.checkpoint import snapshot_directory
    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "nested" / "note.txt").write_text("inner")
    (workspace / "top.txt").write_text("outer")
    reference = snapshot_directory(workspace, assets)
    assert reference.media_type == "application/x-tar"
    assert reference.size_bytes > 0


def test_snapshot_directory_rejects_missing_workspace(tmp_path):
    assets = AssetStore(tmp_path / "assets")
    with pytest.raises(IntegrityError, match="workspace is missing"):
        from worldmock.footprints.checkpoint import snapshot_directory
        snapshot_directory(tmp_path / "absent", assets)


def test_snapshot_directory_rejects_symlinks(tmp_path, monkeypatch):
    from worldmock.footprints.checkpoint import snapshot_directory

    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "real.txt").write_text("x")
    try:
        os.symlink("real.txt", workspace / "link.txt")
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            # 权限不允许创建时模拟文件系统的链接标志，仍执行真实拒绝入口，不声称创建成功。
            link = workspace / "link.txt"
            link.write_text("x")
            original = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original(path))
        else:
            raise
    with pytest.raises(IntegrityError, match="symbolic link"):
        snapshot_directory(workspace, assets)


def test_restore_directory_rejects_existing_destination_and_unsafe_members(tmp_path):
    import tarfile
    import io
    from worldmock.footprints.checkpoint import restore_directory
    assets = AssetStore(tmp_path / "assets")
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        member = tarfile.TarInfo("../escape.txt")
        member.size = 0
        archive.addfile(member, io.BytesIO(b""))
    reference = assets.put(content.getvalue(), {"media_type": "application/x-tar",
                                               "source": {"kind": "workspace"}})
    destination = tmp_path / "restore"
    destination.mkdir()
    with pytest.raises(IntegrityError, match="destination already exists"):
        restore_directory(reference, assets, destination)
    destination.rmdir()
    with pytest.raises(IntegrityError, match="unsafe member"):
        restore_directory(reference, assets, destination)


def _workspace_snapshot(assets, members):
    """以（成员名, 成员类型）构造工作区快照素材；普通文件的内容是成员名本身。"""
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        for name, kind in members:
            entry = tarfile.TarInfo(name)
            entry.type = kind
            raw = name.encode("utf-8", "surrogateescape") if kind == tarfile.REGTYPE else b""
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
    return assets.put(content.getvalue(), {"media_type": "application/x-tar", "source": {"kind": "workspace"}})


def _forbid_restore_writes(monkeypatch):
    """核对必须先于展开：任何写入都让用例失败，回归时也不会真的写到临时目录之外。"""
    from worldmock.footprints import checkpoint

    def written(path, raw):
        raise AssertionError(f"restore wrote before rejecting the snapshot: {path}")

    monkeypatch.setattr(checkpoint, "atomic_write", written)


@pytest.mark.parametrize("names", [
    ("/x",), ("\\x",), ("C:x",), ("D:x",), ("C:\\x",), ("\\\\server\\share\\x",), ("a/../../x",),
    ("a\\..\\..\\x",), (".",), ("a\\b",), ("NUL",), ("a/nul.txt",), ("NUL .txt",), ("CON",), ("COM1.log",),
    ("lpt¹",), ("x.",), ("x ",), ("a./b",), ("notes:1.md",), ("a?b",), ("a/b\x01c",), ("a\udcff",), ("x" * 256,),
    ("x", "X"), ("memory/state.json", "memory/STATE.json"), ("dup", "dup"), ("a/b", "a//b"),
    ("caf\u00e9", "cafe\u0301"), ("a", "a/b"), ("a/b", "a"), ("A", "a/b"),
])
def test_restore_directory_rejects_members_that_escape_or_cannot_be_restored_faithfully(tmp_path, monkeypatch, names):
    """每个平台上都拒绝带盘符或根、含父引用、不只用 "/" 分隔、是 Windows 设备名、含禁用字符或孤立代理码点、
    一级超过 255 字节、以点或空格结尾、忽略大小写与 Unicode 规范形式后重名，或文件同时是另一成员上级目录的成员
    （Windows 上 "/x"、"\\x"、"D:x" 会写到目标目录之外，"x" 与 "x." 互相覆盖）；核对先于展开，目标目录不会被创建。"""
    from worldmock.footprints.checkpoint import restore_directory

    _forbid_restore_writes(monkeypatch)
    assets = AssetStore(tmp_path / "assets")
    reference = _workspace_snapshot(assets, [(name, tarfile.REGTYPE) for name in names])
    with pytest.raises(IntegrityError, match="unsafe member"):
        restore_directory(reference, assets, tmp_path / "restore" / "agent")
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize("kind", [tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.AREGTYPE, tarfile.CONTTYPE,
                                  tarfile.GNUTYPE_SPARSE])
def test_restore_directory_rejects_members_that_are_not_plain_regular_files(tmp_path, monkeypatch, kind):
    """快照只写普通文件；目录、链接，以及同样算作文件的旧式、连续与稀疏成员（稀疏成员可声明远大于归档的长度）都拒绝。"""
    from worldmock.footprints.checkpoint import restore_directory

    _forbid_restore_writes(monkeypatch)
    assets = AssetStore(tmp_path / "assets")
    reference = _workspace_snapshot(assets, [("AGENT.json", tarfile.REGTYPE), ("memory", kind)])
    with pytest.raises(IntegrityError, match="unsafe member"):
        restore_directory(reference, assets, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_restore_directory_rejects_a_member_that_aliases_an_earlier_file(tmp_path):
    """文件系统给长名生成 8.3 短名时，"LONGFI~1.JSO" 与先写的 "longfilename.json" 是同一个文件，后者不能被静默覆盖；
    文件系统不生成短名时两者是不同的文件，照常恢复。"""
    from worldmock.footprints.checkpoint import restore_directory

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "longfilename.json").write_bytes(b"probe")
    aliases = (probe / "LONGFI~1.JSO").exists()
    assets = AssetStore(tmp_path / "assets")
    reference = _workspace_snapshot(assets, [("longfilename.json", tarfile.REGTYPE),
                                             ("LONGFI~1.JSO", tarfile.REGTYPE)])
    destination = tmp_path / "restore"
    if aliases:
        with pytest.raises(IntegrityError, match="unsafe member"):
            restore_directory(reference, assets, destination)
        assert (destination / "longfilename.json").read_bytes() == b"longfilename.json"
    else:
        restore_directory(reference, assets, destination)
        assert (destination / "LONGFI~1.JSO").read_bytes() == b"LONGFI~1.JSO"


@pytest.mark.parametrize("corrupt", [lambda archive: archive[:515], lambda archive: b"\x01" * 1024],
                         ids=["truncated-member-data", "not-a-tar"])
def test_restore_directory_reports_a_corrupt_archive_as_an_integrity_error(tmp_path, corrupt):
    from worldmock.footprints.checkpoint import restore_directory

    assets = AssetStore(tmp_path / "assets")
    archive = assets.read(_workspace_snapshot(assets, [("AGENT.json", tarfile.REGTYPE)]))
    reference = assets.put(corrupt(archive), {"media_type": "application/x-tar", "source": {"kind": "workspace"}})
    with pytest.raises(IntegrityError, match="archive is invalid"):
        restore_directory(reference, assets, tmp_path / "restore")


def test_snapshot_and_restore_keep_every_workspace_file_byte_for_byte(tmp_path):
    """真实工作区的文件名（人物身份、配置、记忆、提示快照）照常快照并逐字节恢复；空目录不进归档。"""
    from worldmock.footprints.checkpoint import restore_directory, snapshot_directory

    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    files = {"AGENT.json": b'{"id": 1}', "config.json": b'{"max_react_turns": 16}',
             "MEMORY.md": "# 记忆\r\n".encode("utf-8"), "memory/episodes.jsonl": b"",
             "memory/state.json": b"{}", "life_prompt_snapshot.json": b'{"persona": {}}'}
    for relative, raw in files.items():
        (workspace / relative).parent.mkdir(parents=True, exist_ok=True)
        (workspace / relative).write_bytes(raw)
    (workspace / "skills").mkdir()
    destination = tmp_path / "restored"
    restore_directory(snapshot_directory(workspace, assets), assets, destination)
    restored = {path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*") if path.is_file()}
    assert restored == files


def test_snapshot_directory_refuses_a_workspace_that_restore_would_reject(tmp_path):
    """快照与恢复用同一成员检查：同一个名字的 NFC 与 NFD 写法在 NTFS、ext4 上是两个文件，恢复到 APFS 会互相覆盖，
    所以快照时就拒绝，不保存恢复不了的快照；文件系统本身把它们当作同一个文件时只剩一个，照常快照。"""
    from worldmock.footprints.checkpoint import snapshot_directory

    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "café.md").write_bytes(b"nfc")
    (workspace / "café.md").write_bytes(b"nfd")
    if len(list(workspace.iterdir())) == 2:
        with pytest.raises(IntegrityError, match="unsafe file name"):
            snapshot_directory(workspace, assets)
        assert not [path for path in (tmp_path / "assets").rglob("*") if path.is_file()]
    else:
        snapshot_directory(workspace, assets)


def test_snapshot_directory_rejects_directory_junctions(tmp_path, monkeypatch):
    """Windows 目录联接不是符号链接（is_symlink 为假），快照同样拒绝，而且在进入联接之前拒绝：不列出它指向的目录
    （指回工作区的联接会让逐层遍历无穷展开）。"""
    from worldmock.footprints.checkpoint import snapshot_directory

    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENT.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")
    link = workspace.resolve() / "linked"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
        assert link.is_junction() and not link.is_symlink()
    else:
        # POSIX 没有目录联接：以真实目录模拟唯一的联接标志，仍执行真实拒绝入口。
        link.mkdir()
        (link / "secret.txt").write_text("outside")
        original = Path.is_junction
        monkeypatch.setattr(Path, "is_junction", lambda path: path == link or original(path))
    scanned = []
    scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path=".": scanned.append(Path(path)) or scandir(path))
    with pytest.raises(IntegrityError, match="symbolic link"):
        snapshot_directory(workspace, assets)
    assert workspace.resolve() in scanned
    assert link not in scanned and outside.resolve() not in scanned


def _store_with_checkpoint(tmp_path):
    store = _make_store(tmp_path)
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100,
                                          agent_id=1)])
    reference = store.assets.put_json({"workspace": True}, {"kind": "fixture"})
    point = store.checkpoint(store.state, {"agent:1": reference, "life": reference})
    return store, point


def test_restore_branch_rejects_destinations_inside_parent_run(tmp_path):
    from worldmock.footprints.checkpoint import restore_branch
    store, point = _store_with_checkpoint(tmp_path)
    with pytest.raises(IntegrityError, match="outside the parent run"):
        restore_branch(store, point.checkpoint_id, store.run_dir / 'child')
    with pytest.raises(IntegrityError, match="outside the parent run"):
        restore_branch(store, point.checkpoint_id, store.run_dir)
    store.close()


def test_restore_branch_carries_event_artifacts_into_child_run(tmp_path):
    store = _make_store(tmp_path)
    reference = store.assets.put_json({"evidence": True}, {"kind": "fixture"})
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100,
                                          agent_id=1, artifact_refs=[reference])])
    point = store.checkpoint(store.state, {"agent:1": reference, "life": reference})
    child = restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    try:
        events = child.read_events(child.last_event_seq)
        assert any(event.artifact_refs for event in events)
        assert child.manifest["parent_run_id"] == "run"
        assert child.manifest["parent_checkpoint_id"] == point.checkpoint_id
        assert "restored-checkpoint.json" in os.listdir(child.run_dir)
    finally:
        child.close()
    store.close()


def test_restore_branch_cleanup_on_population_failure(tmp_path, monkeypatch):
    from worldmock.footprints import checkpoint as checkpoint_module
    store, point = _store_with_checkpoint(tmp_path)

    def _fail(*args, **kwargs):
        raise IntegrityError("population boom")

    monkeypatch.setattr(checkpoint_module, "_populate_branch", _fail)
    with pytest.raises(IntegrityError, match="population boom"):
        restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    assert not (tmp_path / "child").exists()
    leftovers = [item for item in os.listdir(tmp_path) if item.startswith(".restore-")]
    assert leftovers == []
    store.close()


@pytest.mark.parametrize("metadata", [
    {"branch_kind": "resume"},
    {"branch_kind": "counterfactual", "intervention_sha256": "short"},
    {"branch_kind": "counterfactual", "intervention_sha256": "z" * 64},
    {"branch_kind": "counterfactual", "intervention_sha256": "0" * 64, "extra": 1},
])
def test_branch_metadata_rejects_invalid_shapes(metadata):
    from worldmock.footprints.checkpoint import _branch_metadata
    with pytest.raises(IntegrityError):
        _branch_metadata(metadata)


def test_branch_metadata_accepts_counterfactual_with_valid_hash():
    from worldmock.footprints.checkpoint import _branch_metadata
    metadata = _branch_metadata({"branch_kind": "counterfactual",
                                 "intervention_sha256": "a" * 64})
    assert metadata["branch_kind"] == "counterfactual"
    assert _branch_metadata(None) == {"branch_kind": "resume"}


# ---------------------------------------------------------------------------
# export.py 缺口
# ---------------------------------------------------------------------------


def test_export_rejects_empty_range_existing_destination_and_nonpositive_window(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(IntegrityError, match="empty event range"):
        export_run(store, 0, tmp_path / "export")
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100)])
    (tmp_path / "taken").mkdir()
    with pytest.raises(IntegrityError, match="already exists"):
        export_run(store, 1, tmp_path / "taken")
    single = _make_store(tmp_path / "flat")
    single.commit(single.state, [EventDraft(event_type="observation", occurred_at=100)])
    with pytest.raises(IntegrityError, match="positive confirmed time window"):
        export_run(single, 1, tmp_path / "flat-export")
    store.close()
    single.close()


def test_export_includes_event_artifact_refs_in_asset_index(tmp_path):
    store = _make_store(tmp_path)
    reference = store.assets.put_json({"artifact": True}, {"kind": "fixture"})
    store.commit({"version": 1, "current_time": 110}, [EventDraft(
        event_type="observation", occurred_at=110, artifact_refs=[reference])])
    manifest = export_run(store, 1, tmp_path / "export")
    assert reference.sha256 in [row["sha256"] for row in
                                [json.loads(line) for line in
                                 (tmp_path / "export" / "asset-index.jsonl").read_text(encoding="utf-8").splitlines()]]
    assert (tmp_path / "export" / "assets" / reference.sha256).is_file()
    assert manifest["asset_index"]["rows"] >= 1
    store.close()


def test_load_render_day_rejects_missing_timeline_script(monkeypatch):
    class _FakePath:
        def __init__(self, exists=True):
            self._exists = exists

        def resolve(self):
            return self

        def __truediv__(self, other):
            return self

        @property
        def parents(self):
            return [self] * 6

        def is_file(self):
            return self._exists

        def is_symlink(self):
            return False

    monkeypatch.setattr(timeline_module, "Path", lambda *args, **kwargs: _FakePath(exists=False))
    with pytest.raises(IntegrityError, match="timeline projection script"):
        timeline_module._load_render_day()


def _progress_event(sequence, activity, interval_start, interval_end, **fields):
    return _event(sequence, "activity_progress", data={
        "activity": dict(activity), "interval_start": interval_start,
        "interval_end": interval_end, **fields.pop("extra", {})}, **fields)


def test_reconstruct_activities_merges_adjacent_segments_and_accepts_non_progress_snapshots():
    activity = {"activity_id": "work", "agent_id": 1, "kind": "interact", "started_at": 100,
                "elapsed_seconds": 20, "ended_at": None, "status": "active"}
    first = _progress_event(1, activity, 100, 110, activity_id="work", occurred_at=110)
    second = _progress_event(2, {**activity, "elapsed_seconds": 20}, 110, 120,
                             activity_id="work", occurred_at=120)
    snapshot_only = _event(3, "action_result", occurred_at=120, activity_id="work",
                           data={"activity": {**activity, "status": "completed",
                                              "ended_at": 120}})
    records = reconstruct_activities([first, second, snapshot_only])
    assert records[0]["segments"] == [{"started_at": 100, "ended_at": 120}]
    assert records[0]["activity_type"] == "interact"
    assert records[0]["source_event_ids"] == ["event-1", "event-2", "event-3"]


@pytest.mark.parametrize("craft", [
    "identity_mismatch",
    "progress_backwards",
    "invalid_interval",
    "future_interval",
    "overlap_interval",
    "duration_mismatch",
])
def test_reconstruct_activities_rejects_invalid_progress_chains(craft):
    activity = {"activity_id": "work", "agent_id": 1, "kind": "interact", "started_at": 100,
                "elapsed_seconds": 10, "ended_at": None, "status": "active"}
    if craft == "identity_mismatch":
        events = [_event(1, "activity_progress", occurred_at=110, activity_id="other",
                         data={"activity": activity, "interval_start": 100, "interval_end": 110})]
        match = "activity reference disagrees"
    elif craft == "progress_backwards":
        later = {**activity, "elapsed_seconds": 5}
        events = [
            _progress_event(1, activity, 100, 110, activity_id="work", occurred_at=110),
            _progress_event(2, later, 110, 120, activity_id="work", occurred_at=120),
        ]
        match = "cannot move backwards"
    elif craft == "invalid_interval":
        events = [_progress_event(1, activity, 110, 110, activity_id="work", occurred_at=110)]
        match = "interval is invalid"
    elif craft == "future_interval":
        events = [_progress_event(1, activity, 100, 130, activity_id="work", occurred_at=110)]
        match = "interval is invalid"
    elif craft == "overlap_interval":
        events = [
            _progress_event(1, activity, 100, 110, activity_id="work", occurred_at=110),
            _progress_event(2, {**activity, "elapsed_seconds": 20}, 105, 115,
                            activity_id="work", occurred_at=120),
        ]
        match = "interval is invalid"
    else:
        events = [_progress_event(1, {**activity, "elapsed_seconds": 999},
                                  100, 110, activity_id="work", occurred_at=110)]
        match = "do not account for"
    with pytest.raises(IntegrityError, match=match):
        reconstruct_activities(events)


# ---------------------------------------------------------------------------
# validation.py 缺口
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tamper", [
    "identity", "artifact_set", "activities_disagree", "verified_experiences_disagree", "quality_path",
    "checkpoint_manifest", "checkpoint_world", "checkpoint_participants",
    "checkpoint_event", "event_seq", "causal", "ancestor_return",
    "ancestor_before_branch", "run_failed", "no_end",
    "asset_index_repeat", "asset_index_missing", "asset_corrupt",
])
def test_validate_export_rejects_each_tamper_shape(tmp_path, tamper, engine_factory):
    directory = _complete_export(tmp_path, engine_factory)
    manifest = _load_manifest_json(directory)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    if tamper == "identity":
        manifest["run_id"] = "not-the-run"
    elif tamper == "artifact_set":
        manifest["artifacts"]["extra"] = manifest["artifacts"]["events"]
    elif tamper == "activities_disagree":
        _rewrite_jsonl(directory, "activities.jsonl", [{"activity_id": "ghost"}])
        manifest = _load_manifest_json(directory)
    elif tamper == "verified_experiences_disagree":
        _rewrite_jsonl(directory, "verified-experiences.jsonl", [{"kind": "waited", "agent_id": 1}])
        manifest = _load_manifest_json(directory)
    elif tamper == "quality_path":
        manifest["quality_report"]["path"] = "elsewhere.json"
    elif tamper == "checkpoint_manifest":
        manifest["checkpoint"]["manifest_sha256"] = "0" * 64
        manifest = _rehash_checkpoint(manifest)
    elif tamper == "checkpoint_world":
        manifest["checkpoint"]["current_time"] = 199
        manifest = _rehash_checkpoint(manifest)
    elif tamper == "checkpoint_participants":
        manifest["checkpoint"]["snapshots"].pop("life")
        manifest = _rehash_checkpoint(manifest)
    elif tamper == "checkpoint_event":
        events[-1]["data"] = {"checkpoint_id": "wrong"}
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "event_seq":
        events[0]["event_seq"] = 99
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "ancestor_before_branch":
        events[0]["run_id"] = "ancestor"
        manifest["source_run_ids"] = ["ancestor", "run"]
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "causal":
        events[0]["caused_by"] = ["missing-cause"]
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "ancestor_return":
        events[1]["run_id"] = "ancestor"
        manifest["source_run_ids"] = ["ancestor", "run"]
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "run_failed":
        events[0]["event_type"] = "run_failed"
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "no_end":
        next(row for row in events if row["event_type"] == "run_ended")["event_type"] = "observation"
        _rewrite_jsonl(directory, "events.jsonl", events)
        manifest = _load_manifest_json(directory)
    elif tamper == "asset_index_repeat":
        indexed = [json.loads(line) for line in
                   (directory / "asset-index.jsonl").read_text(encoding="utf-8").splitlines()]
        _rewrite_jsonl(directory, "asset-index.jsonl", indexed + [indexed[0]])
        manifest = _load_manifest_json(directory)
    elif tamper == "asset_index_missing":
        indexed = [json.loads(line) for line in
                   (directory / "asset-index.jsonl").read_text(encoding="utf-8").splitlines()]
        _rewrite_jsonl(directory, "asset-index.jsonl", [])
        manifest = _load_manifest_json(directory)
    elif tamper == "asset_corrupt":
        asset = next((directory / "assets").iterdir())
        asset.write_bytes(b"tampered")

    if tamper not in {"activities_disagree", "event_seq", "causal", "ancestor_return",
                      "ancestor_before_branch", "run_failed", "no_end", "checkpoint_event",
                      "asset_index_repeat", "asset_index_missing"}:
        _save_manifest_json(directory, manifest)
    with pytest.raises(IntegrityError):
        validate_export(directory)


# ---------------------------------------------------------------------------
# ledger.py 缺口
# ---------------------------------------------------------------------------


def _money_event(data):
    return SimpleNamespace(data=data)










def test_account_checks_pass_without_accounts():
    validate_account_transition({}, {}, [], False)
    validate_account_history([], {})


# ---------------------------------------------------------------------------
# evidence.py / assets.py 缺口
# ---------------------------------------------------------------------------


def test_redact_evidence_passes_scalars_and_replaces_env_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "sk-secret-value")
    assert evidence.redact_evidence(7) == 7
    assert evidence.redact_evidence(None) is None
    assert evidence.redact_evidence([3, "plain"]) == [3, "plain"]
    assert evidence.redact_evidence("token sk-secret-value here") == "token [REDACTED] here"
    assert evidence.redact_evidence({"api_key": "x", "nested": {"password": "y"}}) == {
        "api_key": "[REDACTED]", "nested": {"password": "[REDACTED]"}}


def test_atomic_write_failure_paths_clean_up_and_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("file where a directory is needed")
    with pytest.raises(OSError):
        atomic_write(blocker / "sub" / "file.txt", b"data")

    import worldmock.footprints.assets as assets_module
    target = tmp_path / "late-failure.txt"
    real_replace = os.replace

    def _failing_replace(source, destination):
        raise OSError("replace refused")

    monkeypatch.setattr(assets_module.os, "replace", _failing_replace)
    try:
        with pytest.raises(OSError):
            atomic_write(target, b"data")
    finally:
        monkeypatch.setattr(assets_module.os, "replace", real_replace)
    leftovers = [item for item in os.listdir(tmp_path) if item.startswith(".pending-")]
    assert leftovers == []


def test_asset_store_put_reuses_existing_content(tmp_path):
    assets = AssetStore(tmp_path / "assets")
    first = assets.put(b"same-bytes", {"media_type": "text/plain", "source": {"kind": "t"}})
    second = assets.put(b"same-bytes", {"media_type": "text/plain", "source": {"kind": "t"}})
    assert first == second
    assert assets.read(first) == b"same-bytes"
    assert len(os.listdir(assets.root)) == 1


def _complete_export(tmp_path, engine_factory):
    """真实环境运行结束后导出共同检查点。"""
    from tests.life.environment_runtime import run_to_end
    engine = engine_factory()
    run_to_end(engine)
    store = engine.context.store
    reference = store.assets.put_json({"saved": "complete"}, {"kind": "fixture"})
    point = engine.checkpoint({"agent:1": reference})
    directory = tmp_path / "export"
    export_run(store, point.last_event_seq, directory)
    return directory
