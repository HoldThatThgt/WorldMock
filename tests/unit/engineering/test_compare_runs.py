"""运行比对：精确屏蔽、双射身份映射与声明规则只放过记录形式，人物经历的任何事实变化都报出来。"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
import zlib
from pathlib import Path

import pytest

import scripts.compare_runs as compare_runs
from scripts.compare_runs import (ComparisonError, Chain, Options, Rule, Side, compare, leaf_diff, parse_rules,
                                  require_allowed)

RUN, RUN2 = "a" * 32, "c" * 32
TIMEZONE = "Asia/Shanghai"
#: 2026-01-05 08:00 +08:00。
T0 = 1767571200
URL, METADATA_URL = "https://model.example.invalid/v1", "https://model.example.invalid/v1/models"
CODE = "1" * 64


def _bytes(value) -> bytes:
    """@brief 规范 JSON 字节。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(content: bytes) -> str:
    """@brief 字节摘要。"""
    return hashlib.sha256(content).hexdigest()


def _ref(content: bytes, kind: str, media_type: str = "text/plain", path: str | None = None) -> dict:
    """@brief 一份素材引用。"""
    source = {"kind": kind, **({"path": path} if path else {})}
    return {"media_type": media_type, "sha256": _digest(content), "size_bytes": len(content), "source": source}


def _tar(members: dict[str, bytes]) -> bytes:
    """@brief 与工作区快照同样固定元信息的 tar。"""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, raw in members.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(raw), 0o600, 0
            archive.addfile(entry, io.BytesIO(raw))
    return out.getvalue()


def _config(url: str = URL, metadata_url: str = METADATA_URL, seed: int = 7) -> bytes:
    """@brief 冻结的 config.toml 原文。"""
    return f'seed = {seed}\n\n[model]\nbase_url = "{url}"\nmetadata_url = "{metadata_url}"\n'.encode()


def _workspace(initialized_at: str = "2026-10-05T00:00:00", base_url: str = URL, memory: bytes = b"# memory\n",
               workspace: dict | None = None):
    """@brief 人物工作区快照：AGENT.json 带壁钟、地址与工作区所在目录，MEMORY.md 是事实。"""
    agent = {"initialized_at": initialized_at, "profile": {"name": "测试人物", "model_identity": {
        "base_url": base_url, "metadata_url": METADATA_URL, "model_metadata": {"metadata_url": METADATA_URL}}},
        "workspace": workspace or {"root": "/runs/base/as2/agent-1", "memory_dir": "memory"}}
    return _tar({"AGENT.json": _bytes(agent), "MEMORY.md": memory})


class Recording:
    """@brief 一份可改动的最小运行：事件、检查点、世界、素材与导出，写盘时补齐摘要与身份。"""

    def __init__(self, prefix: str = "a", run_id: str = RUN):
        """@brief 用身份前缀生成事件与检查点身份；同一前缀两次得到逐字相同的运行。"""
        self.prefix, self.run_id = prefix, run_id
        self.config = _config()
        self.workspace = _workspace()
        self.action = f"{run_id}:round:{T0}:agent:1:order:0:action:0001"
        self.activity = f"{self.action}:activity"
        self.events = self._events()
        self.states = {2: self.state(T0, 1, {}), 7: self.state(T0 + 600, 2, self.processed("completed"))}
        self.manifest = self._manifest()
        self.quality = {"schema_version": 8, "run_id": run_id, "source_group_id": run_id,
                        "acceleration": {"speedup": 12.5, "observed_wall_seconds": 48.0,
                                         "wall_seconds_by_run": {run_id: 48.0}},
                        "model_usage": {"estimated_cost": {"known_estimate_usd": 0.01,
                                                           "complete_estimate_usd": 0.01},
                                        "by_purpose": [{"purpose": "decision", "responses": 1}]},
                        "actions": {"request_events": 1},
                        "day_plan_metrics": {CODE: {"external_constraints": {"source_event_ids": [self.eid(6)]}}}}
        self.extra_rows: dict[str, list[dict]] = {}

    def eid(self, seq: int) -> str:
        """@brief 第 seq 个事件的身份。"""
        return f"{self.prefix}{seq:031x}"

    def cid(self, seq: int) -> str:
        """@brief 第 seq 个事件所建检查点的身份。"""
        return f"{self.prefix}{seq:030x}cc"

    def state(self, moment: int, version: int, processed: dict) -> dict:
        """@brief 一份世界状态（基一侧的对象形式）。"""
        activity = {"activity_id": self.activity, "origin_action_id": self.action, "status": "completed",
                    "origin_request_event_id": self.eid(4), "data": {"accepted_event_id": self.eid(5)}}
        return {"schema_version": 14, "run_id": self.run_id, "current_time": moment, "version": version,
                "config_sha256": _digest(self.config), "persona_sha256": "9" * 64, "balance_fen": 1000,
                "activities": {self.activity: activity} if processed else {}, "processed_actions": processed}

    def processed(self, status: str, moment: int = T0 + 600) -> dict:
        """@brief 一条已处理动作：结果带活动快照与时刻。"""
        result = {"action_id": self.action, "activity_id": self.activity, "status": status, "occurred_at": moment,
                  "data": {"activity": {"activity_id": self.activity, "origin_request_event_id": self.eid(4),
                                        "data": {"accepted_event_id": self.eid(5)}}}}
        return {self.action: {"action_id": self.action, "request_sha256": "d" * 64, "receipt": {"status": "submitted"},
                              "result": result}}

    def _events(self) -> list[dict]:
        """@brief 八条事件：观察、检查点、决策、请求、响应、进度、检查点、结束。"""
        decision = f"{self.run_id}:round:{T0}:agent:1"
        request = {"model": "test-model", "messages": [{"role": "system", "content": "你是测试人物。"}],
                   "max_tokens": 100}
        rows = [
            ("observation", T0, 1, [], {"summary": "开始", "memory_layer": {"earlier_today": [
                {"recorded_at": T0 - 600, "text": "早饭"}]}}),
            ("checkpoint_created", T0, None, [], {"checkpoint_id": self.cid(2)}),
            ("decision_started", T0, 1, [], {"round_id": f"{self.run_id}:round:{T0}"}),
            ("model_request", T0, 1, [], {"request": request}),
            ("model_response", T0, 1, [self.eid(4)], {"phase": "response", "request": request,
                                                       "response": {"id": "r1", "choices": []}}),
            ("activity_progress", T0 + 600, 1, [self.eid(5)], {"activity": {
                "activity_id": self.activity, "origin_request_event_id": self.eid(4),
                "data": {"accepted_event_id": self.eid(5)}}}),
            ("checkpoint_created", T0 + 600, None, [], {"checkpoint_id": self.cid(7)}),
            ("run_ended", T0 + 600, None, [], {}),
        ]
        events = []
        for seq, (event_type, moment, agent, causes, data) in enumerate(rows, start=1):
            events.append({"event_seq": seq, "event_id": self.eid(seq), "event_type": event_type, "run_id": self.run_id,
                           "occurred_at": moment, "agent_id": agent, "decision_id": decision if agent else None,
                           "caused_by": causes, "data": data, "artifact_refs": [],
                           "resource_changes": {"balance_fen": -500} if event_type == "activity_progress" else {},
                           "recorded_at": f"2026-10-15T00:00:{seq:02d}+00:00", "wall_elapsed_ms": seq * 10})
        return events

    def _manifest(self) -> dict:
        """@brief 运行清单：代码身份、模型地址与冻结的 config.toml 素材。"""
        config = _ref(self.config, "environment_input", path="config.toml")
        return {"schema_version": 6, "run_id": self.run_id, "source_group_id": RUN, "source_run_ids": [self.run_id],
                "agent_ids": [1], "window": {"start": T0, "end": T0 + 600, "timezone": TIMEZONE},
                "code_sha256": CODE, "git_commit": "e" * 40, "git_dirty": False, "persona_sha256": "9" * 64,
                "config_sha256": config["sha256"], "environment_assets": {"config.toml": config},
                "model_identity": {"base_url": URL, "prompt_sha256": "5" * 64},
                "model_metadata": {"metadata_url": METADATA_URL, "context_window": 262144},
                "stage_model_identity": {"base_url": URL, "metadata_url": METADATA_URL,
                                         "model_metadata": {"metadata_url": METADATA_URL}},
                "persona_habit_responses": {"1": ["早起"]}}

    def snapshots(self, seq: int) -> tuple[dict, dict[str, bytes]]:
        """@brief 第 seq 个事件所建检查点的参与者快照与素材内容。"""
        state = self.states[seq]
        life = _bytes({"schema_version": 1, "run_id": self.run_id, "current_time": state["current_time"],
                       "config_sha256": _digest(self.config), "persona_sha256": "9" * 64})
        refs = {"agent:1": _ref(self.workspace, "workspace", "application/x-tar"),
                "life": _ref(life, "life_checkpoint", "application/json")}
        return refs, {refs["agent:1"]["sha256"]: self.workspace, refs["life"]["sha256"]: life}

    def record(self, seq: int) -> dict:
        """@brief 第 seq 个事件所建检查点的记录（含 record_sha256）。"""
        state = self.states[seq]
        snapshots, _ = self.snapshots(seq)
        body = {"schema_version": 1, "checkpoint_id": self.cid(seq), "run_id": self.run_id,
                "current_time": state["current_time"], "state_version": state["version"], "last_event_seq": seq,
                "state": state, "snapshots": snapshots, "manifest_sha256": _digest(_bytes(self.manifest))}
        return {**body, "record_sha256": _digest(_bytes(body))}


def _run(root: Path, events: list[dict], checkpoints: dict[int, dict], manifest: dict, assets: dict[str, bytes],
         **extra) -> tuple[Path, Path]:
    """@brief 写一个最小运行目录与导出（events.jsonl、带工件摘要的 manifest.json、其余工件、quality.json）。

    extra：quality（质量报告）、world（终态，缺省为最后一个检查点的状态）、rows（其余 JSONL 工件的行）、
    restored（恢复点记录）、export（False 时不写导出）、encode（检查点行正文的编码函数）。
    """
    run, export = root / "run", root / "export"
    (run / "assets").mkdir(parents=True)
    for sha, content in assets.items():
        (run / "assets" / sha).write_bytes(content)
    (run / "manifest.json").write_bytes(_bytes(manifest))
    if extra.get("restored") is not None:
        (run / "restored-checkpoint.json").write_bytes(_bytes(extra["restored"]))
    encode = extra.get("encode") or (lambda body: body.decode())
    with sqlite3.connect(run / "footprints.sqlite") as connection:
        connection.execute("CREATE TABLE events(event_seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
                           "body TEXT NOT NULL)")
        connection.execute("CREATE TABLE world_state(singleton INTEGER PRIMARY KEY, version INTEGER, body TEXT)")
        connection.execute("CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY, body, record_sha256 TEXT)")
        for event in events:
            connection.execute("INSERT INTO events VALUES(?,?,?)",
                               (event["event_seq"], event["event_id"], _bytes(event).decode()))
        for record in checkpoints.values():
            body = _bytes({key: value for key, value in record.items() if key != "record_sha256"})
            connection.execute("INSERT INTO checkpoints VALUES(?,?,?)",
                               (record["checkpoint_id"], encode(body), record["record_sha256"]))
        world = extra.get("world") or (list(checkpoints.values())[-1]["state"] if checkpoints else {})
        connection.execute("INSERT INTO world_state VALUES(1,?,?)", (world.get("version", 0), _bytes(world).decode()))
    connection.close()
    if extra.get("export", True):
        _export(export, (events, checkpoints), manifest, assets, extra)
    return run, export


def _export(export: Path, records: tuple[list[dict], dict[int, dict]], manifest: dict, assets: dict,
            extra: dict) -> None:
    """@brief 写导出目录：工件、素材、asset-index 与带摘要的清单；records 是（事件, 检查点）。"""
    events, checkpoints = records
    (export / "assets").mkdir(parents=True)
    files = {"events.jsonl": b"".join(_bytes(event) + b"\n" for event in events)}
    rows = {"activities.jsonl": [], "verified-experiences.jsonl": [], "purchases.jsonl": [], "app-usage.jsonl": [],
            "location-stays.jsonl": [], **extra.get("rows", {})}
    for name, values in rows.items():
        files[name] = b"".join(_bytes(row) + b"\n" for row in values)
    references = list(manifest["environment_assets"].values())
    last = list(checkpoints.values())[-1] if checkpoints else None
    if last is not None:
        references += list(last["snapshots"].values())
    files["asset-index.jsonl"] = b"".join(_bytes(reference) + b"\n" for reference in references)
    files["quality.json"] = _bytes(extra.get("quality") or {})
    for reference in references:
        (export / "assets" / reference["sha256"]).write_bytes(assets[reference["sha256"]])
    for name, content in files.items():
        (export / name).write_bytes(content)
    keys = {"events.jsonl": "events", "activities.jsonl": "activities", "verified-experiences.jsonl":
            "verified_experiences", "purchases.jsonl": "purchases", "app-usage.jsonl": "app_usage",
            "location-stays.jsonl": "location_stays"}
    document = {"schema_version": 7, "committed": True, "run_id": manifest["run_id"],
                "source_group_id": manifest["source_group_id"], "source_run_ids": manifest["source_run_ids"],
                "artifacts": {key: {"path": name, "rows": files[name].count(b"\n"), "sha256": _digest(files[name])}
                              for name, key in keys.items()},
                "asset_index": {"path": "asset-index.jsonl", "rows": len(references),
                                "sha256": _digest(files["asset-index.jsonl"])},
                "quality_report": {"path": "quality.json", "size_bytes": len(files["quality.json"]),
                                   "sha256": _digest(files["quality.json"])},
                "timeline": [], "frozen_run_manifest": manifest}
    if last is not None:
        document["checkpoint"] = last
    (export / "manifest.json").write_bytes(_bytes(document))


def _write(root: Path, recording: Recording, **extra) -> tuple[Path, Path]:
    """@brief 把一份 Recording 写成运行目录与导出。"""
    assets = {_digest(recording.config): recording.config}
    checkpoints = {}
    for seq in recording.states:
        checkpoints[seq] = recording.record(seq)
        assets.update(recording.snapshots(seq)[1])
    extra.setdefault("quality", recording.quality)
    if recording.extra_rows:
        extra.setdefault("rows", recording.extra_rows)
    return _run(root, recording.events, checkpoints, recording.manifest, assets, **extra)


def _compare(tmp_path: Path, base: Recording, head: Recording, *rules: str, every: bool = False,
             head_extra: dict | None = None) -> dict:
    """@brief 写两侧并比较，返回报告。"""
    base_run, base_export = _write(tmp_path / "base", base)
    head_run, head_export = _write(tmp_path / "head", head, **(head_extra or {}))
    options = Options(None, parse_rules(list(rules)), every)
    return compare(Side(Chain.load([base_run]), base_export), Side(Chain.load([head_run]), head_export), options)


def _paths(report: dict) -> list[str]:
    """@brief 报告里列出的全部差异路径。"""
    found = []
    for name, layer in compare_runs._layer_items(report["layers"]):
        found += [path["path"] for item in layer["first"] for path in item["paths"]]
    return found


def _event(recording: Recording, event_type: str) -> dict:
    """@brief 取一条事件（同类型的第一条）。"""
    return next(event for event in recording.events if event["event_type"] == event_type)


def test_identical_runs_compare_equal(tmp_path, capsys):
    base_run, base_export = _write(tmp_path / "base", Recording())
    head_run, head_export = _write(tmp_path / "head", Recording())
    code = compare_runs.main(["runs", "--base-run", str(base_run), "--base-export", str(base_export),
                              "--head-run", str(head_run), "--head-export", str(head_export),
                              "--report", str(tmp_path / "report.json")])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert code == 0 and report["verdict"] == "equal" and report["problems"] == []
    assert capsys.readouterr().out.startswith("Runs are equal: 8 events")


def test_event_and_checkpoint_ids_and_wall_clock_are_mapped_or_masked(tmp_path):
    head = Recording(prefix="b")
    for event in head.events:
        event["recorded_at"], event["wall_elapsed_ms"] = "2026-10-16T09:00:00+00:00", event["wall_elapsed_ms"] + 777
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "equal", _paths(report)
    assert report["masks"]["event:recorded_at"] == 16


def test_nested_recorded_at_is_compared(tmp_path):
    head = Recording()
    _event(head, "observation")["data"]["memory_layer"]["earlier_today"][0]["recorded_at"] = T0 - 300
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "different"
    assert ".data.memory_layer.earlier_today[0].recorded_at" in _paths(report)


def test_a_reference_to_another_event_is_a_difference(tmp_path):
    head = Recording()
    _event(head, "activity_progress")["caused_by"] = [head.eid(4)]
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "different" and ".caused_by[0]" in _paths(report)
    assert report["problems"] == []


def test_a_reference_to_an_unknown_event_is_a_problem(tmp_path):
    head = Recording()
    _event(head, "activity_progress")["caused_by"] = ["f" * 32]
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "different"
    assert {"kind": "REFERENCE_NOT_AN_ID", "id_kind": "event", "side": "head", "path": ".caused_by[0]",
            "value": "f" * 32} in report["problems"]


def test_ids_inside_free_text_are_compared_literally(tmp_path):
    base, head = Recording(), Recording(prefix="b")
    _event(base, "observation")["data"]["summary"] = f"见事件 {base.eid(3)}"
    _event(head, "observation")["data"]["summary"] = f"见事件 {head.eid(3)}"
    report = _compare(tmp_path, base, head)
    assert report["verdict"] == "different" and ".data.summary" in _paths(report)


def test_compound_ids_require_equal_run_ids(tmp_path):
    head = Recording()
    _event(head, "observation")["decision_id"] = f"{'b' * 32}:round:{T0}:agent:1"
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "different" and ".decision_id" in _paths(report)


def test_an_amount_change_is_a_difference(tmp_path):
    head = Recording()
    _event(head, "activity_progress")["resource_changes"]["balance_fen"] = -501
    report = _compare(tmp_path, Recording(), head)
    assert report["verdict"] == "different" and ".resource_changes.balance_fen" in _paths(report)


def _relocated(recording: Recording, url: str, metadata_url: str) -> Recording:
    """@brief 同一份运行换到另一个模型服务地址：清单、冻结配置与工作区里的地址都换掉，代码身份也不同。"""
    recording.config = _config(url, metadata_url)
    recording.workspace = _workspace(base_url=url)
    recording.states = {seq: {**state, "config_sha256": _digest(recording.config)}
                        for seq, state in recording.states.items()}
    recording.manifest = recording._manifest()
    recording.manifest.update(code_sha256="2" * 64, git_commit="f" * 40, git_dirty=True)
    recording.manifest["model_identity"]["base_url"] = url
    recording.manifest["model_metadata"]["metadata_url"] = metadata_url
    recording.manifest["stage_model_identity"] = {"base_url": url, "metadata_url": metadata_url,
                                                  "model_metadata": {"metadata_url": metadata_url}}
    recording.quality["day_plan_metrics"] = {"2" * 64: recording.quality["day_plan_metrics"][CODE]}
    return recording


def test_code_identity_and_model_urls_are_masked_only_at_their_paths(tmp_path):
    local = ("http://127.0.0.1:5000/beta", "http://127.0.0.1:5000/models")
    report = _compare(tmp_path / "masked", Recording(), _relocated(Recording(), *local))
    assert report["verdict"] == "equal", _paths(report)
    base, head = Recording(), _relocated(Recording(), *local)
    _event(base, "observation")["data"]["endpoint"] = URL
    _event(head, "observation")["data"]["endpoint"] = local[0]
    report = _compare(tmp_path / "event", base, head)
    assert report["verdict"] == "different" and ".data.endpoint" in _paths(report)


def test_config_digest_is_mapped_through_url_normalized_content(tmp_path):
    local = ("http://127.0.0.1:5000/beta", "http://127.0.0.1:5000/models")
    head = _relocated(Recording(), *local)
    assert head.manifest["config_sha256"] != Recording().manifest["config_sha256"]
    assert _compare(tmp_path / "urls", Recording(), head)["verdict"] == "equal"
    seeded = _relocated(Recording(), *local)
    seeded.config = _config(*local, seed=8)
    seeded.manifest["environment_assets"]["config.toml"] = _ref(seeded.config, "environment_input",
                                                                path="config.toml")
    seeded.manifest["config_sha256"] = _digest(seeded.config)
    report = _compare(tmp_path / "seed", Recording(), seeded)
    assert report["verdict"] == "different" and ".config_sha256" in _paths(report)


def test_agent_json_initialized_at_is_masked_inside_workspace_snapshots(tmp_path):
    head = Recording()
    head.workspace = _workspace(initialized_at="2026-10-16T09:30:00", base_url="http://127.0.0.1:9/beta")
    assert _compare(tmp_path / "masked", Recording(), head)["verdict"] == "equal"
    head.workspace = _workspace(memory=b"# memory\nchanged\n")
    report = _compare(tmp_path / "memory", Recording(), head)
    assert report["verdict"] == "different"
    assert any(path.startswith(".snapshots.agent:1.sha256") for path in _paths(report))


def test_agent_json_workspace_root_is_masked_only_at_its_path(tmp_path):
    head = Recording()
    head.workspace = _workspace(workspace={"root": "/runs/head/as2/agent-1", "memory_dir": "memory"})
    assert _compare(tmp_path / "root", Recording(), head)["verdict"] == "equal"
    head.workspace = _workspace(workspace={"root": "/runs/head/as2/agent-1", "memory_dir": "notes"})
    assert _compare(tmp_path / "sibling", Recording(), head)["verdict"] == "different"


def _memory_line(recording: Recording, seq: int) -> bytes:
    """@brief MEMORY.md 里一条已核验经历行，来源事件是本侧第 seq 个事件。"""
    return f"# Memory\n\n## 已核验经历\n- 1767571800：等待 600 秒。来源事件 {recording.eid(seq)}。\n".encode()


def test_memory_source_events_are_mapped_inside_workspace_snapshots(tmp_path):
    base, head = Recording(), Recording(prefix="b")
    base.workspace, head.workspace = _workspace(memory=_memory_line(base, 6)), _workspace(memory=_memory_line(head, 6))
    report = _compare(tmp_path / "mapped", base, head)
    assert report["verdict"] == "equal" and report["problems"] == [], _paths(report)
    other = Recording(prefix="b")
    other.workspace = _workspace(memory=_memory_line(other, 5))
    assert _compare(tmp_path / "other", base, other)["verdict"] == "different"
    unknown = Recording(prefix="b")
    unknown.workspace = _workspace(memory=_memory_line(Recording(prefix="f"), 6))
    report = _compare(tmp_path / "unknown", base, unknown)
    assert report["verdict"] == "different"
    assert [item["path"] for item in report["problems"]] == ["MEMORY.md"]


def _memory_digests(recording: Recording, values: tuple[str, str, str]) -> Recording:
    """@brief 两条事件里 MEMORY.md 的前后摘要：第一条从 values[0] 到 values[1]，第二条从 values[1] 到 values[2]。"""
    _event(recording, "activity_progress")["data"].update(memory_before_sha256=values[0], memory_sha256=values[1])
    _event(recording, "run_ended")["data"].update(memory_before_sha256=values[1], memory_sha256=values[2])
    return recording


def test_memory_digests_are_compared_by_first_appearance(tmp_path):
    base = _memory_digests(Recording(), ("1" * 64, "2" * 64, "3" * 64))
    head = _memory_digests(Recording(), ("4" * 64, "5" * 64, "6" * 64))
    assert _compare(tmp_path / "pattern", base, head)["verdict"] == "equal"
    repeated = _memory_digests(Recording(), ("4" * 64, "5" * 64, "5" * 64))
    report = _compare(tmp_path / "repeated", base, repeated)
    assert report["verdict"] == "different" and ".data.memory_sha256" in _paths(report)


def _without_request_copy(recording: Recording) -> Recording:
    """@brief 头一侧的记录形式：模型响应不再存请求副本。"""
    _event(recording, "model_response")["data"]["request"] = None
    return recording


def test_request_copy_rule_rebuilds_the_response_request(tmp_path):
    head = _without_request_copy(Recording(prefix="b"))
    report = _compare(tmp_path / "rule", Recording(), head, "request-copy")
    assert report["verdict"] == "equal", _paths(report)
    assert report["rules"]["request-copy"]["applied"] == 1
    report = _compare(tmp_path / "plain", Recording(), _without_request_copy(Recording(prefix="b")))
    assert report["verdict"] == "different" and ".data.request.max_tokens" in _paths(report)


def test_a_wrong_request_copy_is_a_difference(tmp_path):
    base = Recording()
    _event(base, "model_response")["data"]["request"] = {"model": "test-model", "messages": [], "max_tokens": 100}
    report = _compare(tmp_path, base, _without_request_copy(Recording()), "request-copy")
    assert report["verdict"] == "different"


def test_a_rule_on_decision_inputs_is_refused():
    rule = Rule("bindings", "projection", "event:observation", "data.activity_bindings", "none", "08")
    with pytest.raises(ComparisonError, match="targets event:observation:data.activity_bindings"):
        require_allowed(rule)


def _zlib(body: bytes) -> bytes:
    """@brief 头一侧检查点行的压缩正文。"""
    return zlib.compress(body)


def test_checkpoint_zlib_rule_requires_the_record_hash_of_the_decoded_payload(tmp_path):
    report = _compare(tmp_path / "good", Recording(), Recording(), "checkpoint-zlib", head_extra={"encode": _zlib})
    assert report["verdict"] == "equal" and report["rules"]["checkpoint-zlib"]["applied"] >= 1
    with pytest.raises(ComparisonError, match="record digest does not match its body"):
        _compare(tmp_path / "bad", Recording(), Recording(), "checkpoint-zlib",
                 head_extra={"encode": lambda body: _zlib(body + b" ")})


def test_checkpoint_rows_compressed_on_both_sides_compare_without_a_rule(tmp_path):
    """@brief 两侧都按第二章起的形式存 zlib 流：不带规则各按自己的形式解开，结论 equal；一侧文本、一侧 zlib 流而没有
    声明规则时拒绝比较，不把形式的变化当作相同。"""
    base_run, base_export = _write(tmp_path / "both" / "base", Recording(), encode=_zlib)
    head_run, head_export = _write(tmp_path / "both" / "head", Recording(), encode=_zlib)
    report = compare(Side(Chain.load([base_run]), base_export), Side(Chain.load([head_run]), head_export),
                     Options(None, (), False))
    assert report["verdict"] == "equal", _paths(report)
    with pytest.raises(ComparisonError, match="only rule checkpoint-zlib aligns them"):
        _compare(tmp_path / "mixed", Recording(), Recording(), head_extra={"encode": _zlib})


def _activity_array(recording: Recording, activities_of=None) -> Recording:
    """@brief 头一侧的记录形式：活动是带 activity_id 的有序数组。"""
    for state in recording.states.values():
        state["activities"] = (activities_of or (lambda items: list(items.values())))(state["activities"])
    return recording


def test_activities_array_rule_requires_unique_ids_and_equal_entries(tmp_path):
    report = _compare(tmp_path / "array", Recording(), _activity_array(Recording()), "activities-array")
    assert report["verdict"] == "equal", _paths(report)
    with pytest.raises(ComparisonError, match="repeat an activity identity"):
        _compare(tmp_path / "repeat", Recording(), _activity_array(Recording(), lambda items: [*items.values()] * 2),
                 "activities-array")
    changed = _activity_array(Recording(), lambda items: [{**value, "status": "failed"} for value in items.values()])
    assert _compare(tmp_path / "changed", Recording(), changed, "activities-array")["verdict"] == "different"


def _with_actions(recording: Recording, actions: dict, activity_status: str = "completed",
                  moment: int = T0 + 600) -> Recording:
    """@brief 两个检查点与终态都换成给定的已处理动作与活动状态。"""
    for seq, state in recording.states.items():
        if seq == 7:
            state["processed_actions"] = copy.deepcopy(actions)
            state["activities"] = {key: {**value, "status": activity_status}
                                   for key, value in recording.state(moment, 2, {"x": 1})["activities"].items()}
    return recording


def test_processed_actions_rule_rejects_a_changed_entry(tmp_path):
    base = Recording()
    other = f"{base.action}:other"
    entry = base.processed("completed")[base.action]
    earlier = {**entry, "action_id": other, "result": {**entry["result"], "action_id": other, "occurred_at": T0}}
    actions = {**base.processed("completed"), other: earlier}
    _with_actions(base, actions)
    head = _with_actions(Recording(), Recording().processed("completed"))
    assert _compare(tmp_path / "subset", base, head, "processed-actions-pruned")["verdict"] == "equal"
    changed = Recording().processed("completed")
    changed[base.action]["request_sha256"] = "0" * 64
    report = _compare(tmp_path / "changed", base, _with_actions(Recording(), changed), "processed-actions-pruned")
    assert report["verdict"] == "different"


def test_processed_actions_rule_accepts_only_terminal_overwrites(tmp_path):
    started = Recording().processed("completed")
    started[Recording().action]["result"].update(status="accepted", occurred_at=T0)
    base = _with_actions(Recording(), Recording().processed("completed"))
    report = _compare(tmp_path / "ended", base, _with_actions(Recording(), started), "processed-actions-pruned")
    assert report["verdict"] == "equal", _paths(report)
    assert report["rules"]["processed-actions-pruned"]["terminal_overwrites"] >= 1
    active = _with_actions(Recording(), Recording().processed("completed"), activity_status="active")
    head = _with_actions(Recording(), started, activity_status="active")
    assert _compare(tmp_path / "active", active, head, "processed-actions-pruned")["verdict"] == "different"
    changed = copy.deepcopy(started)
    changed[Recording().action]["request_sha256"] = "0" * 64
    report = _compare(tmp_path / "request", base, _with_actions(Recording(), changed), "processed-actions-pruned")
    assert report["verdict"] == "different"


def test_processed_actions_rule_requires_every_readable_entry(tmp_path):
    action = Recording().action
    paused = _with_actions(Recording(), Recording().processed("completed", moment=T0), activity_status="paused")
    report = _compare(tmp_path / "paused", paused, _with_actions(Recording(), {}, activity_status="paused"),
                      "processed-actions-pruned")
    assert report["verdict"] == "different"
    assert any(path.startswith(f".processed_actions.{action}") for path in _paths(report))
    settled = _with_actions(Recording(), Recording().processed("completed"))
    report = _compare(tmp_path / "settled", settled, _with_actions(Recording(), {}), "processed-actions-pruned")
    assert report["verdict"] == "different"
    earlier = Recording().processed("completed", moment=T0)
    ended = _with_actions(Recording(), earlier)
    report = _compare(tmp_path / "ended", ended, _with_actions(Recording(), {}), "processed-actions-pruned")
    assert report["verdict"] == "equal", _paths(report)
    report = _compare(tmp_path / "kept", ended, _with_actions(Recording(), earlier), "processed-actions-pruned")
    assert report["verdict"] == "different"


def test_head_activity_array_is_projected_before_ids_are_mapped(tmp_path):
    head = _activity_array(Recording(prefix="b"))
    report = _compare(tmp_path, Recording(), head, "activities-array")
    assert report["verdict"] == "equal", _paths(report)
    assert not report["problems"]


def _start_results(recording: Recording) -> Recording:
    """@brief 已处理动作保存开始动作自己的受理结果（start_group 的 data.activities 快照数组），不是终态覆盖。"""
    for state in recording.states.values():
        for entry in state["processed_actions"].values():
            entry["result"]["data"] = {"activities": [entry["result"]["data"].pop("activity")]}
    return recording


def test_start_results_kept_in_processed_actions_map_their_event_references(tmp_path):
    """@brief 第二章起活动终结不再覆盖开始动作的结果：已处理动作里开始结果的活动快照数组同样映射引用的事件，
    事件身份不同的两侧结论 equal。"""
    report = _compare(tmp_path, _start_results(Recording()), _start_results(Recording(prefix="b")))
    assert report["verdict"] == "equal", _paths(report)
    assert not report["problems"]


def test_activity_arrays_on_both_sides_map_their_event_references_without_a_rule(tmp_path):
    """@brief 两侧都按结构 15 把活动存成数组、事件身份不同：不带规则也在数组项上映射活动引用的事件，结论 equal；
    引用指向别的事件仍报为不同。"""
    report = _compare(tmp_path / "same", _activity_array(Recording()), _activity_array(Recording(prefix="b")))
    assert report["verdict"] == "equal", _paths(report)
    assert not report["problems"]
    head = _activity_array(Recording(prefix="b"))
    for state in head.states.values():
        for activity in state["activities"]:
            activity["data"]["accepted_event_id"] = head.eid(4)
    report = _compare(tmp_path / "other", _activity_array(Recording()), head)
    assert report["verdict"] == "different"
    assert any(".activities[0].data.accepted_event_id" in path for path in _paths(report))


def _version_seven(recording: Recording) -> Recording:
    """@brief 头一侧的运行清单：结构号 7 并带 lineage。"""
    recording.manifest.update(schema_version=7, lineage=[{"run_id": recording.run_id, "segment_count": 1}])
    return recording


def test_manifest_rules_apply_before_manifest_digests(tmp_path):
    report = _compare(tmp_path, Recording(), _version_seven(Recording()), "run-manifest-version=6:7", "lineage")
    assert report["verdict"] == "equal", _paths(report)
    for layer in ("day_end_states", "manifests"):
        assert report["layers"][layer]["different"] == 0


def _gzip_workspace(recording: Recording, media_type: str = "application/gzip") -> Recording:
    """@brief 头一侧的记录形式：工作区快照压缩存放。"""
    original = recording.snapshots

    def snapshots(seq):
        refs, contents = original(seq)
        packed = gzip.compress(recording.workspace, mtime=0)
        refs = {**refs, "agent:1": _ref(packed, "workspace", media_type)}
        contents = {**contents, refs["agent:1"]["sha256"]: packed}
        return refs, contents

    recording.snapshots = snapshots
    return recording


def test_workspace_gzip_rule_compares_the_tar_inside_and_its_media_type(tmp_path):
    report = _compare(tmp_path / "rule", Recording(), _gzip_workspace(Recording()), "workspace-gzip")
    assert report["verdict"] == "equal", _paths(report)
    assert _compare(tmp_path / "plain", Recording(), _gzip_workspace(Recording()))["verdict"] == "different"
    zipped = _gzip_workspace(Recording(), "application/zip")
    report = _compare(tmp_path / "zip", Recording(), zipped, "workspace-gzip")
    assert report["verdict"] == "different"


def test_gzip_workspace_snapshots_on_both_sides_are_compared_by_their_members_without_a_rule(tmp_path):
    """@brief 两侧都存 application/gzip 的工作区快照：不带规则也先解压再按成员比较，AGENT.json 的壁钟照常屏蔽；
    成员内容不同仍报为不同。"""
    head = Recording()
    head.workspace = _workspace(initialized_at="2026-10-16T09:30:00")
    report = _compare(tmp_path / "masked", _gzip_workspace(Recording()), _gzip_workspace(head))
    assert report["verdict"] == "equal", _paths(report)
    head = Recording()
    head.workspace = _workspace(memory=b"# memory\nchanged\n")
    report = _compare(tmp_path / "memory", _gzip_workspace(Recording()), _gzip_workspace(head))
    assert report["verdict"] == "different"
    assert any(path.startswith(".snapshots.agent:1.sha256") for path in _paths(report))


def test_transport_attempts_are_dropped_or_masked(tmp_path):
    head = Recording()
    _event(head, "model_response")["data"]["transport_attempts"] = []
    assert _compare(tmp_path / "rule", Recording(), head, "transport-attempts")["verdict"] == "equal"
    assert _compare(tmp_path / "plain", Recording(), copy.deepcopy(head))["verdict"] == "different"
    base, head = Recording(), Recording()
    _event(base, "model_response")["data"]["transport_attempts"] = [{"failed_after_ms": 35000, "wait_ms": 1000}]
    _event(head, "model_response")["data"]["transport_attempts"] = [{"failed_after_ms": 36100, "wait_ms": 2000}]
    assert _compare(tmp_path / "both", base, head)["verdict"] == "equal"


def _lineage(recording: Recording, **fields) -> Recording:
    """@brief 两侧都带 lineage 的运行清单。"""
    entry = {"run_id": recording.run_id, "checkpoint_id": None, "checkpoint_event_seq": 7, "branched_at": "w1",
             "last_recorded_at": "w2", "end_error": None, "discarded_events": 0, **fields}
    recording.manifest["lineage"] = [entry]
    return recording


def test_lineage_compares_restart_fields_and_masks_the_tail(tmp_path):
    head = _lineage(Recording(), branched_at="w9", end_error="killed", discarded_events=3)
    assert _compare(tmp_path / "tail", _lineage(Recording()), head)["verdict"] == "equal"
    moved = _lineage(Recording(), checkpoint_event_seq=2)
    report = _compare(tmp_path / "moved", _lineage(Recording()), moved)
    assert report["verdict"] == "different" and ".lineage[0].checkpoint_event_seq" in _paths(report)


def _by_call_class(recording: Recording, keep_purpose: dict | None = None) -> Recording:
    """@brief 头一侧的质量报告：按调用类分列模型用量。"""
    usage = recording.quality["model_usage"]
    usage["by_call_class"] = [{"call_class": "decision", "responses": 1, "peak_share": 0.5}]
    if keep_purpose is None:
        del usage["by_purpose"]
    else:
        usage["by_purpose"] = [keep_purpose]
    return recording


def test_cost_by_class_rule_drops_both_usage_sections_only_together(tmp_path):
    assert _compare(tmp_path / "rule", Recording(), _by_call_class(Recording()), "cost-by-class")["verdict"] == "equal"
    assert _compare(tmp_path / "plain", Recording(), _by_call_class(Recording()))["verdict"] == "different"
    both = _by_call_class(Recording(), {"purpose": "decision", "responses": 2})
    assert _compare(tmp_path / "both", Recording(), both, "cost-by-class")["verdict"] == "different"


def test_unknown_and_incomplete_rules_are_refused(caplog):
    with pytest.raises(ComparisonError, match="Rule nonsense is unknown"):
        parse_rules(["nonsense"])
    with pytest.raises(ComparisonError, match="Rule world-schema-version needs BASE:HEAD values"):
        parse_rules(["world-schema-version"])
    assert "Rule nonsense is unknown" in caplog.text


def test_an_asset_that_does_not_match_its_reference_is_refused(tmp_path):
    base_run, base_export = _write(tmp_path / "base", Recording())
    head_run, head_export = _write(tmp_path / "head", Recording())
    config = next((head_run / "assets").glob(_digest(Recording().config)))
    config.write_bytes(config.read_bytes() + b"#")
    with pytest.raises(ComparisonError, match="does not match its reference"):
        compare(Side(Chain.load([base_run]), base_export), Side(Chain.load([head_run]), head_export),
                Options(None, (), False))


def _two_segments(root: Path, recording: Recording) -> tuple[list[Path], Path]:
    """@brief 两段链：第一段到 7 号检查点后失败，第二段从它恢复（8 号是 branch_created）后结束。"""
    first = copy.deepcopy(recording)
    first.events = first.events[:7] + [{**first.events[3], "event_seq": 8, "event_id": first.eid(8),
                                        "occurred_at": T0 + 600}]
    first_run, _ = _write(root / "one", first, export=False)
    second = copy.deepcopy(recording)
    second.run_id = RUN2
    second.manifest.update(run_id=RUN2, parent_run_id=recording.run_id, parent_checkpoint_id=recording.cid(7),
                           source_run_ids=[recording.run_id, RUN2])
    branch = {**recording.events[7], "event_seq": 8, "event_id": recording.eid(8), "run_id": RUN2,
              "event_type": "environment_event",
              "data": {"kind": "branch_created", "parent_checkpoint_id": recording.cid(7)}}
    ended = {**recording.events[7], "event_seq": 9, "event_id": recording.eid(9), "run_id": RUN2}
    second.events = recording.events[:7] + [branch, ended]
    restored = recording.record(7)
    second.states = {}
    run, export = _write(root / "two", second, restored=restored, world=recording.states[7])
    return [first_run, run], export


def test_chains_of_different_length_are_different(tmp_path):
    runs, export = _two_segments(tmp_path / "base", Recording())
    head_run, head_export = _write(tmp_path / "head", Recording())
    report = compare(Side(Chain.load(runs), export), Side(Chain.load([head_run]), head_export),
                     Options(7, (), False))
    assert report["verdict"] == "different"
    assert {"kind": "SEGMENT_COUNT", "base": 2, "head": 1} in report["problems"]


def test_version_rule_accepts_only_the_declared_values(tmp_path):
    head = Recording()
    for state in head.states.values():
        state["schema_version"] = 15
    assert _compare(tmp_path / "15", Recording(), head, "world-schema-version=14:15")["verdict"] == "equal"
    for state in head.states.values():
        state["schema_version"] = 16
    assert _compare(tmp_path / "16", Recording(), head, "world-schema-version=14:15")["verdict"] == "different"


def test_added_field_rule_drops_only_its_path_on_head(tmp_path):
    head = Recording()
    head.manifest["lineage"] = [{"run_id": head.run_id, "segment_count": 1}]
    assert _compare(tmp_path / "rule", Recording(), head, "lineage")["verdict"] == "equal"
    head.manifest["persona_habit_responses"] = {"1": ["晚起"]}
    assert _compare(tmp_path / "habit", Recording(), head, "lineage")["verdict"] == "different"


def test_leaf_diff_uses_obsdiff_paths():
    first = {"name": "家", "phase": "现在", "state": "开放", "tools": [{"tool": "a", "available": True, "reason": None}]}
    second = {"name": "公司", "phase": "稍后", "state": "关门", "tools": [
        {"tool": "b", "available": False, "reason": "关门"}, {"tool": "c", "available": False, "reason": "远"}]}
    base = {"tool_layer": {"targets": [{"name": "x"}, first, second]}}
    head = {"tool_layer": {"targets": [{"name": "x"}, second, first]}}
    paths = {item["path"] for item in leaf_diff(base, head, limit=None)}
    expected = {f".tool_layer.targets[{index}]{suffix}" for index in (1, 2)
                for suffix in (".name", ".phase", ".state", ".tools#len", ".tools[0].tool", ".tools[0].available",
                               ".tools[0].reason", ".tools[1].tool", ".tools[1].available", ".tools[1].reason")}
    assert paths == expected


def test_quality_wall_fields_and_code_key_are_masked(tmp_path):
    head = Recording()
    head.manifest.update(code_sha256="2" * 64)
    head.quality["acceleration"].update(speedup=99.0, observed_wall_seconds=3.0)
    head.quality["model_usage"]["estimated_cost"]["known_estimate_usd"] = 0.5
    head.quality["day_plan_metrics"] = {"2" * 64: head.quality["day_plan_metrics"][CODE]}
    assert _compare(tmp_path / "masked", Recording(), head)["verdict"] == "equal"
    head.quality["actions"]["request_events"] = 2
    report = _compare(tmp_path / "fact", Recording(), head)
    assert report["verdict"] == "different" and ".actions.request_events" in _paths(report)


QUALITY_EVIDENCE_FIELDS = ("source_event_ids", "other_rejections.source_event_ids", "missing_evidence_event_ids")


def _quality_evidence(recording, field, values):
    """@brief 设置一组质量证据，返回持有该字段的对象以构造缺项反例。"""
    owner = recording.quality["day_plan_metrics"][CODE]["external_constraints"]
    keys = field.split(".")
    for key in keys[:-1]:
        owner = owner.setdefault(key, {})
    owner[keys[-1]] = values
    return owner


@pytest.mark.parametrize("field", QUALITY_EVIDENCE_FIELDS)
def test_quality_reference_order_follows_event_identity(tmp_path, field):
    """报告按各自随机 ID 排序，映射后必须得到相同引用；原始序号顺序故意相反。"""
    class ReversedIds(Recording):
        def eid(self, seq):
            return f"{self.prefix}{9 - seq:031x}"

    base, head = Recording(), ReversedIds(prefix="b")
    _quality_evidence(base, field, sorted([base.eid(4), base.eid(6)]))
    _quality_evidence(head, field, sorted([head.eid(4), head.eid(6)]))
    report = _compare(tmp_path, base, head)
    assert report["verdict"] == "equal"
    assert report["problems"] == []


@pytest.mark.parametrize("field", QUALITY_EVIDENCE_FIELDS)
@pytest.mark.parametrize("references", [(4,), (4, 6, 6), (4, 5)], ids=["missing", "duplicate", "changed"])
def test_quality_reference_membership_is_compared(tmp_path, field, references):
    base, head = Recording(), Recording(prefix="b")
    _quality_evidence(base, field, [base.eid(4), base.eid(6)])
    _quality_evidence(head, field, [head.eid(seq) for seq in references])
    report = _compare(tmp_path, base, head)
    assert report["verdict"] == "different"
    assert report["problems"] == []
    assert any(f".external_constraints.{field}" in path for path in _paths(report))


@pytest.mark.parametrize("field", QUALITY_EVIDENCE_FIELDS)
def test_unknown_quality_reference_is_a_problem(tmp_path, field):
    base, head = Recording(), Recording(prefix="b")
    _quality_evidence(base, field, [base.eid(4), base.eid(6)])
    _quality_evidence(head, field, [head.eid(4), "f" * 32])
    report = _compare(tmp_path, base, head)
    assert report["verdict"] == "different"
    assert {"kind": "REFERENCE_NOT_AN_ID", "id_kind": "event", "side": "head",
            "path": f".day_plan_metrics.<code-identity>.external_constraints.{field}[1]",
            "value": "f" * 32} in report["problems"]


@pytest.mark.parametrize("field", QUALITY_EVIDENCE_FIELDS)
def test_missing_quality_reference_field_differs_from_empty(tmp_path, field):
    base, head = Recording(), Recording()
    _quality_evidence(base, field, [])
    owner = _quality_evidence(head, field, [])
    del owner[field.split(".")[-1]]
    report = _compare(tmp_path, base, head)
    assert report["verdict"] == "different"
    assert any(f".external_constraints.{field}" in path for path in _paths(report))


@pytest.mark.parametrize("location", ["quality-actions", "quality-metric", "quality-outcome", "event", "activity"])
def test_other_reference_lists_keep_their_order(tmp_path, location):
    base, head = Recording(), Recording()
    for recording, sequences in ((base, (4, 6)), (head, (6, 4))):
        references = [recording.eid(seq) for seq in sequences]
        if location == "quality-actions":
            recording.quality["actions"]["source_event_ids"] = references
        elif location == "quality-metric":
            recording.quality["day_plan_metrics"][CODE]["source_event_ids"] = references
        elif location == "quality-outcome":
            recording.quality["run_outcome"] = {"run_ended_events": references}
        elif location == "event":
            _event(recording, "activity_progress")["data"]["source_event_ids"] = references
        else:
            recording.extra_rows["activities.jsonl"] = [{"source_event_ids": references}]
    assert _compare(tmp_path, base, head)["verdict"] == "different"


def test_export_must_cover_the_database_prefix(tmp_path):
    base_run, base_export = _write(tmp_path / "base", Recording())
    head_run, head_export = _write(tmp_path / "head", Recording())
    lines = (head_export / "events.jsonl").read_bytes().splitlines(keepends=True)
    (head_export / "events.jsonl").write_bytes(b"".join(lines[:-1]))
    with pytest.raises(ComparisonError, match="does not cover the run database prefix at event 8"):
        compare(Side(Chain.load([base_run]), base_export), Side(Chain.load([head_run]), head_export),
                Options(None, (), False))


def test_events_file_must_match_its_manifest_digest(tmp_path):
    base_run, base_export = _write(tmp_path / "base", Recording())
    head_run, head_export = _write(tmp_path / "head", Recording())
    with (head_export / "events.jsonl").open("ab") as stream:
        stream.write(b'{"event_seq":9}\n')
    with pytest.raises(ComparisonError, match="Export artifact events.jsonl does not match its manifest digest"):
        compare(Side(Chain.load([base_run]), base_export), Side(Chain.load([head_run]), head_export),
                Options(None, (), False))


def test_day_end_and_restart_states_are_compared(tmp_path):
    base_runs, base_export = _two_segments(tmp_path / "base", Recording())
    head = Recording()
    head.states[7]["balance_fen"] = 999
    head_runs, head_export = _two_segments(tmp_path / "head", head)
    report = compare(Side(Chain.load(base_runs), base_export), Side(Chain.load(head_runs), head_export),
                     Options(None, (), False))
    assert report["layers"]["restart_states"]["different"] == 1
    assert report["layers"]["day_end_states"]["different"] == 1
    assert ".state.balance_fen" in _paths(report)


def test_every_checkpoint_mode_compares_intermediate_states(tmp_path):
    head = Recording()
    head.states[2]["balance_fen"] = 999
    assert _compare(tmp_path / "default", Recording(), head)["verdict"] == "equal"
    report = _compare(tmp_path / "every", Recording(), head, every=True)
    assert report["verdict"] == "different" and report["layers"]["every_checkpoint"]["different"] == 1


def test_chain_reader_requires_every_segment_in_order(tmp_path):
    runs, _ = _two_segments(tmp_path / "chain", Recording())
    assert Chain.load(runs).positions == (7,)
    with pytest.raises(ComparisonError, match="segment 1: the first directory is a branch"):
        Chain.load(runs[1:])
