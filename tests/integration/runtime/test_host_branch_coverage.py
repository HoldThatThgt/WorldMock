"""C4e 宿主簇分支覆盖：runtime 恢复/失败传播、cli 命令出口、status 只读视图、
validation 拒绝分支与 opslog 边界。模型客户端全部打桩，零真实调用。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
import worldmock.runtime.framework.setup as worldmock_run_setup_module

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.simulation_engine.engine import LifeEngine
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace, write_project
from worldmock.runtime.lifecycle.runner import (
    CounterfactualRequest,
    ResumeRequest,
    _execute,
    _ops_crash_log,
    _require_run_shell,
)
from worldmock.runtime.framework.setup import runtime_code_sha256
from worldmock.runtime.monitoring.status import read_status, write_status

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT


# ---------------------------------------------------------------------------
# runtime 入口包装与反事实请求合同
# ---------------------------------------------------------------------------


def test_counterfactual_request_rejects_foreign_payloads():
    """反事实请求拒绝绕开严格模型的输入。"""
    with pytest.raises(TypeError, match="ResumeRequest"):
        CounterfactualRequest(SimpleNamespace(), CounterfactualIntervention(
            kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="h",
            source_event_ids=("e",)))
    request = ResumeRequest(SCENARIO, ROOT, "checkpoint", ROOT / "elsewhere")
    with pytest.raises(TypeError, match="strict life model"):
        CounterfactualRequest(request, "not-an-intervention")


def _prepared_stub(tmp_path, **overrides):
    """构造 _execute 使用的最小启动输入（普通恢复：没有运行身份、没有干预）。"""
    base = dict(
        run_id="child", run_dir=tmp_path, run_identity=None, intervention=None, intervention_sha256=None,
        inputs=SimpleNamespace(config=SimpleNamespace(timezone="UTC")),
        start_time=datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")),
        agents={1: "frozen-inputs"}, memory_records={1: ()},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _host_stub(monkeypatch, close_error=None) -> list:
    """把运行宿主换成记录调用的替身（关闭可抛出给定异常），把构造模型注册表的唯一一处换成返回空注册表的函数；
    返回构造出的替身列表。"""
    import worldmock.runtime.lifecycle.runner as runtime
    from worldmock.runtime.lifecycle import run_host

    built: list = []

    class _Host:
        current_time = datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC"))
        step_count = 1
        engine = None

        def __init__(self, *arguments):
            self.calls = []
            built.append(self)

        async def start_new(self, *arguments):
            self.calls.append("start_new")

        async def start_resume(self):
            self.calls.append("start_resume")

        async def start_counterfactual(self, *arguments):
            self.calls.append("start_counterfactual")

        async def run(self):
            self.calls.append("run")

        async def close(self):
            self.calls.append("close")
            if close_error is not None:
                raise close_error

    async def _noop() -> None:
        return None

    monkeypatch.setattr(run_host, "RunHost", _Host)
    monkeypatch.setattr(runtime, "_model_registry", lambda prepared: SimpleNamespace(close=_noop))
    return built


# ---------------------------------------------------------------------------
# 新建/恢复准备路径
# ---------------------------------------------------------------------------


def test_runtime_code_sha256_requires_complete_sources(tmp_path, monkeypatch):
    """代码身份源缺失或不安全时拒绝；最小工程根给出 64 位代码身份；src 下的源码或 uv.lock 是符号链接时拒绝
    （替换 Path.is_symlink，Windows 无需链接权限）。"""
    monkeypatch.setattr(worldmock_run_setup_module, "project_root", lambda: tmp_path)
    with pytest.raises(IntegrityError, match="missing or unsafe"):
        runtime_code_sha256()
    project = write_project(tmp_path / "project")
    monkeypatch.setattr(worldmock_run_setup_module, "project_root", lambda: project)
    assert len(runtime_code_sha256()) == 64
    original = Path.is_symlink
    for name in ("probe.py", "uv.lock"):
        monkeypatch.setattr(Path, "is_symlink", lambda path, name=name: path.name == name or original(path))
        with pytest.raises(IntegrityError, match="missing or unsafe"):
            runtime_code_sha256()


def test_run_shell_rejects_non_directory_and_foreign_files(tmp_path):
    """运行壳只允许不存在目录或仅含 status.json 的目录。"""
    (tmp_path / "occupied").write_text("not a directory")
    with pytest.raises(FileExistsError, match="not a safe directory"):
        _require_run_shell(tmp_path / "occupied")
    empty = tmp_path / "fresh"
    _require_run_shell(empty)
    assert not empty.exists() or empty.is_dir()


# ---------------------------------------------------------------------------
# 崩溃日志与执行清理
# ---------------------------------------------------------------------------


def _first_ops_entry(run_dir: Path) -> dict:
    """运行目录里第一份运维日志的第一条记录。"""
    return json.loads(sorted((run_dir / "logs").glob("ops-*.jsonl"))[0].read_text().splitlines()[0])


def test_ops_crash_log_records_and_never_masks_original_error(tmp_path):
    """崩溃日志写 RUN_CRASHED：宿主的引擎开着时模拟时刻取宿主时刻，引擎为空时取启动时刻；自身故障只记日志不掩盖原始异常。"""
    prepared = _prepared_stub(tmp_path / "open")
    host = SimpleNamespace(engine=object(), current_time=datetime(2026, 1, 5, 9, tzinfo=ZoneInfo("UTC")))
    _ops_crash_log(prepared, host, ValueError("original failure"))
    entry = _first_ops_entry(tmp_path / "open")
    assert entry["code"] == "RUN_CRASHED" and entry["level"] == "error"
    assert "original failure" in entry["detail"] and entry["sim_time"] == host.current_time.isoformat()

    closed = _prepared_stub(tmp_path / "closed")
    _ops_crash_log(closed, SimpleNamespace(engine=None, current_time=host.current_time), ValueError("closed"))
    assert _first_ops_entry(tmp_path / "closed")["sim_time"] == closed.start_time.isoformat()

    broken = _prepared_stub(tmp_path / "no-matter",
                            inputs=SimpleNamespace(config=SimpleNamespace(timezone="Bogus/Zone")))
    with pytest.raises(ZoneInfoNotFoundError):
        ZoneInfo("Bogus/Zone")
    _ops_crash_log(broken, None, ValueError("swallowed original"))


@pytest.mark.asyncio
async def test_execute_requires_intervention_identity(monkeypatch, tmp_path):
    """干预已设置但身份缺失时拒绝进入运行：宿主没有收到任何启动调用，只关闭一次。"""
    hosts = _host_stub(monkeypatch)
    with pytest.raises(RuntimeError, match="identity is unavailable"):
        await _execute(_prepared_stub(tmp_path, intervention=object(),
                                      intervention_sha256=None))
    entry = _first_ops_entry(tmp_path)
    assert entry["code"] == "RUN_CRASHED" and entry["level"] == "error"
    (host,) = hosts
    assert host.calls == ["close"]


@pytest.mark.asyncio
async def test_execute_cleanup_failures_surface_without_run_failure(monkeypatch, tmp_path):
    """运行成功但关闭失败：关闭故障原样上抛。"""
    failure = OSError("close failed")
    _host_stub(monkeypatch, close_error=failure)
    with pytest.raises(OSError, match="close failed") as raised:
        await _execute(_prepared_stub(tmp_path))
    assert raised.value is failure


# ---------------------------------------------------------------------------
# opslog 边界
# ---------------------------------------------------------------------------


def test_opslog_accepts_naive_datetime_and_reports_close_failures(tmp_path, capsys):
    """朴素 datetime 按 UTC 解释；关闭失败只记 stderr。"""
    from worldmock.recording_engine.storage.opslog import OpsLogger

    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(datetime(2026, 1, 5, 16, 30), "runtime", "info", {"code": "PROBE_NAIVE", "detail": "probe"})
    day = "20260106"  # UTC 16:30+8 = 次日 00:30 上海
    writer._handles[day] = _FailingHandle()
    writer.close()
    output = capsys.readouterr().err
    assert "opslog close failed" in output
    logger = OpsLogger(tmp_path, "UTC")
    logger.log(datetime(2026, 1, 5, 1, 0), "runtime", "info", {"code": "PROBE_NAIVE_UTC", "detail": "probe"})
    logger.close()
    assert (tmp_path / "logs" / "ops-20260105.jsonl").is_file()


class _FailingHandle:
    """关闭即失败的句柄桩。"""

    def close(self):
        raise OSError("handle broken")


# ---------------------------------------------------------------------------
# status 只读视图
# ---------------------------------------------------------------------------


def test_write_status_rejects_unknown_status_and_phase(tmp_path):
    """状态写入只接受封闭集合。"""
    write_status(tmp_path, "created", "stopped")
    document = json.loads((tmp_path / "status.json").read_text())
    assert document == {"status": "created", "phase": "stopped", "error_type": None, "error_message": None}
    with pytest.raises(IntegrityError, match="Unknown run status"):
        write_status(tmp_path, "exploded", "stopped")
    with pytest.raises(IntegrityError, match="Unknown run phase"):
        write_status(tmp_path, "running", "daydreaming")


def _status_store(tmp_path, agents_state=None, current_time=100):
    """建立带人物状态的只读视图数据库（以最终状态直接建库）。"""
    manifest = {"schema_version": 1, "run_id": "probe", "source_group_id": "probe",
                "agent_ids": [1], "model_identity": {"name": "fixture"},
                "window": {"start": 100, "end": 200, "timezone": "UTC"}}
    state = {"version": 0, "current_time": current_time, "end_time": 200,
             "activities": [], "agents": agents_state or {}}
    store = FootprintStore.create(tmp_path / "run", manifest, state)
    return store, tmp_path / "run"


def test_read_status_requires_existing_database(tmp_path):
    """数据库缺失时拒绝查询。"""
    with pytest.raises(IntegrityError, match="database does not exist"):
        read_status(tmp_path)


def test_read_status_reports_empty_world_and_wall_time(tmp_path):
    """空事件世界：墙钟为空、人物与检查点为空列表。"""
    store, run_dir = _status_store(tmp_path)
    store.close()
    result = read_status(run_dir)
    assert result["run_id"] == "probe" and result["state_version"] == 0
    assert result["wall_time"] == {"started_at": None, "last_event_at": None,
                                   "elapsed_seconds": None, "last_event_age_seconds": None}
    assert result["people"] == [] and result["recent_checkpoints"] == []
    assert result["observed_model_usage"] == []
    assert result["token_stats"] is None and result["runtime"] is None


def test_read_status_decision_boundaries(tmp_path):
    """决策边界：终态返回 None、非法持久时间拒绝、未来边界截到终点。"""
    agent = {"agent_id": 1, "location_id": "home",
             "next_decision_at": 100}
    store, run_dir = _status_store(tmp_path, {1: agent})
    store.close()
    result = read_status(run_dir)
    assert result["people"][0]["next_decision_at"] == 100

    finished, finished_dir = _status_store(
        tmp_path / "finished", {1: dict(agent, next_decision_at=250)}, current_time=200)
    finished.close()
    assert read_status(finished_dir)["people"][0]["next_decision_at"] is None

    invalid, invalid_dir = _status_store(
        tmp_path / "invalid", {1: dict(agent, next_decision_at=50)})
    invalid.close()
    with pytest.raises(IntegrityError, match="decision time is invalid"):
        read_status(invalid_dir)


# ---------------------------------------------------------------------------
# validation 拒绝分支
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# cli 命令出口
# ---------------------------------------------------------------------------


def test_replay_reads_its_intervention_file_as_utf8(tmp_path, monkeypatch, caplog):
    """进程内经真实命令入口重放：带中文假设的干预文件按 UTF-8 读，期间没有依赖系统默认编码的文本读取；
    父运行不存在时在加载环境之前失败，日志里是存储的拒绝原因。"""
    from tests.text_encoding import default_encoding_reads
    from worldmock.cli import main

    intervention = tmp_path / "intervention.json"
    intervention.write_text(json.dumps({"kind": "set_balance_fen", "agent_id": 1, "balance_fen": 100,
                                        "hypothesis": "余额变了，花钱的选择也会变。",
                                        "source_event_ids": ["observation-source"]}, ensure_ascii=False),
                            encoding="utf-8")
    monkeypatch.delenv("WORLDMOCK_TEST_API_KEY", raising=False)
    calls = default_encoding_reads(monkeypatch)
    with caplog.at_level(logging.ERROR):
        code = main(["replay", "--environment", str(ENVIRONMENT_ROOT), "--output", str(tmp_path / "child"),
                     "--parent", str(tmp_path / "parent"), "--checkpoint", "checkpoint",
                     "--intervention", str(intervention)])
    assert code == 1 and calls == []
    assert "Footprint database does not exist" in caplog.text
    assert not (tmp_path / "child").exists()



















# ---------------------------------------------------------------------------
# 恢复发布前的严格工作区校验与状态墙钟
# ---------------------------------------------------------------------------










def test_read_status_reports_event_wall_time_and_runtime(tmp_path):
    """有事件的世界：墙钟由事件落盘时刻计算，status.json 决定终止时刻。"""
    manifest = {"schema_version": 1, "run_id": "probe", "source_group_id": "probe",
                "agent_ids": [1], "model_identity": {"name": "fixture"},
                "window": {"start": 100, "end": 200, "timezone": "UTC"}}
    store = FootprintStore.create(tmp_path / "run", manifest,
                                  {"version": 0, "current_time": 100, "end_time": 200,
                                   "activities": [], "agents": {}})
    store.append_evidence([EventDraft(event_type="observation", occurred_at=100,
                                      agent_id=1, wakeup_id="d1",
                                      data={"balance_fen": 800})])
    store.close()
    write_status(tmp_path / "run", "completed", "stopped")
    result = read_status(tmp_path / "run")
    assert result["runtime"] == {"status": "completed", "phase": "stopped",
                                 "error_type": None, "error_message": None}
    assert result["wall_time"]["started_at"] == result["wall_time"]["last_event_at"]
    assert result["wall_time"]["elapsed_seconds"] == 0.0
    write_status(tmp_path / "run", "running", "deciding")
    running = read_status(tmp_path / "run")
    assert running["wall_time"]["elapsed_seconds"] >= 0.0
    assert running["event_counts"] == {"observation": 1}
