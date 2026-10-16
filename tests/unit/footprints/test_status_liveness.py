"""运行状态的可读失败原因、驱动心跳与 worldmock status 的活性判断。"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

import worldmock.footprints.store as store_module
from tests.text_encoding import default_encoding_reads
from worldmock import status
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.status import DriverHeartbeat, failure_message, read_status, write_status


class _At(datetime):
    """存储写事件时取的「此刻」：固定为测试给出的 UTC 时刻。"""

    moment: datetime

    @classmethod
    def now(cls, tz=None):
        """返回固定时刻。"""
        return cls.moment


def _ago(seconds: float) -> datetime:
    """距此刻 seconds 秒之前的 UTC 时刻。"""
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _run(tmp_path, monkeypatch, events: list[tuple[str, datetime]]):
    """建只读视图用的最小运行库（_status_store 的形状），按给定类型与落盘时刻追加证据事件；返回运行目录。"""
    run = tmp_path / "run"
    manifest = {"schema_version": 1, "run_id": "probe", "source_group_id": "probe", "agent_ids": [1],
                "window": {"start": 100, "end": 200, "timezone": "UTC"}}
    store = FootprintStore.create(run, manifest, {"version": 0, "current_time": 100, "end_time": 200,
                                                  "activities": [], "agents": {}})
    for index, (event_type, moment) in enumerate(events):
        monkeypatch.setattr(_At, "moment", moment, raising=False)
        monkeypatch.setattr(store_module, "datetime", _At)
        data = {"phase": "request", "call_id": f"c{index}"} if event_type == "model_request" else {}
        store.append_evidence([EventDraft(event_type=event_type, occurred_at=100, agent_id=1, decision_id="d1",
                                          visibility="system", source_kind="model", data=data)])
    monkeypatch.setattr(store_module, "datetime", datetime)
    store.close()
    return run


def _beat(run, moment: datetime) -> None:
    """写一份给定时刻的心跳文件（规范 JSON 三键）。"""
    (run / "heartbeat.json").write_bytes(canonical_json(
        {"heartbeat_at": moment.isoformat(), "pid": 1, "started_at": moment.isoformat()}))


def test_status_json_carries_the_one_line_error_message(tmp_path):
    """失败状态的字节恰是四键规范 JSON，带一行可读原因。"""
    write_status(tmp_path, "failed", "stopped", "RuntimeError", "RuntimeError: boom")
    assert (tmp_path / "status.json").read_bytes() == (
        b'{"error_message":"RuntimeError: boom","error_type":"RuntimeError","phase":"stopped","status":"failed"}')
    write_status(tmp_path, "running", "deciding")
    assert json.loads((tmp_path / "status.json").read_bytes())["error_message"] is None


class _RemoteFailure(Exception):
    """模拟 Ray 任务错误：cause 属性带着远端原始异常。"""

    def __init__(self, cause):
        super().__init__("\x1b[31mray traceback\x1b[0m")
        self.cause = cause


def test_failure_message_unwraps_groups_and_ray_causes():
    """异常组逐项以「 | 」连接；带 cause 异常的取它；终端颜色去掉、换行与连串空格折成一个空格，不截断。"""
    group = BaseExceptionGroup("both", [ValueError("x"), KeyError("y")])
    assert failure_message(group) == "ValueError: x | KeyError: 'y'"
    assert failure_message(_RemoteFailure(OSError("disk  full"))) == "OSError: disk full"
    assert failure_message(_RemoteFailure("not an exception")) == "_RemoteFailure: ray traceback"
    long = "a" * 5000
    assert failure_message(RuntimeError(f"\x1b[1;33mline one\x1b[0m\n  line\ttwo   {long}")) == (
        f"RuntimeError: line one line two {long}")


def test_cli_failure_writes_the_root_cause_into_status(tmp_path, monkeypatch):
    """运行失败时 cli 在 status.json 写类型名与一行根因：异常链的类型名到达 error_message。"""
    import worldmock.runtime as runtime
    from worldmock.cli import main

    output = tmp_path / "run"
    message = ("Agent decision failed: [{'error': \"LLMDispatchError('Failed to get valid response after 366 "
               "attempts in 43206.0 s. Last error type: InternalServerError. Error chain: InternalServerError "
               "<- ConnectError')\"}]")

    async def failing_run(request):
        request.run_dir.mkdir(parents=True)
        raise RuntimeError(message)

    monkeypatch.setattr(runtime, "run_world", failing_run)
    assert main(["run", "--environment", str(tmp_path / "environment"), "--output", str(output)]) == 1
    document = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert document["error_type"] == "RuntimeError"
    assert document["status"] == "failed" and document["phase"] == "stopped"
    assert "ConnectError" in document["error_message"]
    assert document["error_message"] == f"RuntimeError: {message}"


def test_fresh_heartbeat_reads_running(tmp_path, monkeypatch):
    """running、心跳 10 秒前、最后一条事件是观察：活性 running，墙钟止于此刻、随时间增长。"""
    run = _run(tmp_path, monkeypatch, [("observation", _ago(100)), ("observation", _ago(50))])
    write_status(run, "running", "deciding")
    _beat(run, _ago(10))
    first = read_status(run)
    assert first["liveness"] == "running"
    assert first["waiting_for_model_since"] is None
    assert 9 <= first["heartbeat"]["age_seconds"] < 60
    assert first["wall_time"]["elapsed_seconds"] >= 100
    assert 50 <= first["wall_time"]["last_event_age_seconds"] < 120
    time.sleep(0.05)
    assert read_status(run)["wall_time"]["elapsed_seconds"] > first["wall_time"]["elapsed_seconds"]


def test_fresh_heartbeat_with_a_pending_request_reads_waiting_for_model(tmp_path, monkeypatch):
    """最后一条事件是模型请求：活性 waiting_for_model，起点是这条请求的落盘时刻。"""
    request_at = _ago(30)
    run = _run(tmp_path, monkeypatch, [("observation", _ago(60)), ("model_request", request_at)])
    write_status(run, "running", "deciding")
    _beat(run, _ago(5))
    result = read_status(run)
    assert result["liveness"] == "waiting_for_model"
    assert result["waiting_for_model_since"] == request_at.isoformat()
    assert result["wall_time"]["last_event_at"] == request_at.isoformat()


def test_old_heartbeat_reads_stale_and_stops_elapsed_time(tmp_path, monkeypatch):
    """心跳 181 秒前：活性 stale，墙钟止于最后一次心跳。"""
    started = _ago(1000)
    run = _run(tmp_path, monkeypatch, [("observation", started), ("model_request", _ago(900))])
    write_status(run, "running", "deciding")
    beat = _ago(181)
    _beat(run, beat)
    result = read_status(run)
    assert result["liveness"] == "stale"
    assert result["wall_time"]["elapsed_seconds"] == (beat - started).total_seconds()


def test_missing_heartbeat_while_running_reads_stale(tmp_path, monkeypatch):
    """running 而没有心跳文件：活性 stale，心跳为空，墙钟止于最后一条事件。"""
    started, last = _ago(300), _ago(200)
    run = _run(tmp_path, monkeypatch, [("observation", started), ("observation", last)])
    write_status(run, "running", "deciding")
    result = read_status(run)
    assert result["liveness"] == "stale" and result["heartbeat"] is None
    assert result["wall_time"]["elapsed_seconds"] == (last - started).total_seconds()


@pytest.mark.parametrize("state", ["completed", "failed", "interrupted"])
def test_finished_runs_read_stopped_regardless_of_heartbeat(tmp_path, monkeypatch, state):
    """终止的运行不论心跳新旧都是 stopped；没有 status.json 时活性为空。"""
    run = _run(tmp_path, monkeypatch, [("observation", _ago(20))])
    assert read_status(run)["liveness"] is None
    write_status(run, state, "stopped")
    _beat(run, _ago(1))
    assert read_status(run)["liveness"] == "stopped"
    _beat(run, _ago(10_000))
    assert read_status(run)["liveness"] == "stopped"


@pytest.mark.parametrize("content", [b"{", b"[]", canonical_json(
    {"heartbeat_at": "2026-10-15T03:00:43", "pid": 1, "started_at": "2026-10-15T03:00:00"})],
    ids=["not-json", "not-object", "naive-time"])
def test_unreadable_heartbeat_is_refused(tmp_path, monkeypatch, caplog, content):
    """心跳不是 JSON、不是对象或时刻不带时区：拒绝并写英文错误日志。"""
    run = _run(tmp_path, monkeypatch, [("observation", _ago(20))])
    write_status(run, "running", "deciding")
    (run / "heartbeat.json").write_bytes(content)
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Run heartbeat is invalid$"):
        read_status(run)
    assert "Run heartbeat is invalid: " in caplog.text


def test_first_and_last_event_are_read_by_primary_key(tmp_path, monkeypatch):
    """首末事件的两条语句按主键读、不扫描事件表；墙钟起止等于首末事件的落盘时刻。"""
    first, middle, last = _ago(90), _ago(60), _ago(30)
    run = _run(tmp_path, monkeypatch, [("observation", first), ("observation", middle), ("model_request", last)])
    store = FootprintStore(run)
    try:
        for statement in (status._FIRST_EVENT_SQL, status._LAST_EVENT_SQL):
            plan = " ".join(row[-1] for row in store.connection.execute(f"EXPLAIN QUERY PLAN {statement}"))
            assert "SCAN events" not in plan
    finally:
        store.close()
    wall = read_status(run)["wall_time"]
    assert (wall["started_at"], wall["last_event_at"]) == (first.isoformat(), last.isoformat())


def test_driver_heartbeat_writes_at_once_and_every_interval(tmp_path, monkeypatch):
    """进入时立即写心跳，之后每个间隔重写；退出后线程结束，文件恰含三键。"""
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    path = tmp_path / "heartbeat.json"
    beat = DriverHeartbeat(tmp_path)
    with beat:
        assert path.exists()
        seen, deadline = {json.loads(path.read_bytes())["heartbeat_at"]}, time.monotonic() + 2
        while len(seen) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
            seen.add(json.loads(path.read_bytes())["heartbeat_at"])
        assert len(seen) >= 3
    assert not beat._thread.is_alive()
    record = json.loads(path.read_bytes())
    assert set(record) == {"heartbeat_at", "pid", "started_at"} and record["pid"] == os.getpid()
    assert path.read_bytes() == canonical_json(record)


def test_heartbeat_write_failure_is_logged_and_beating_continues(tmp_path, monkeypatch, caplog):
    """第一次写心跳失败：记录英文错误日志，线程继续，之后的心跳照常写出。"""
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    original, failures = status.atomic_write, []

    def flaky(path, content):
        if not failures:
            failures.append(path)
            raise OSError("disk busy")
        original(path, content)

    monkeypatch.setattr(status, "atomic_write", flaky)
    path = tmp_path / "heartbeat.json"
    with caplog.at_level(logging.ERROR), DriverHeartbeat(tmp_path):
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    assert failures and path.exists()
    assert "Driver heartbeat write failed: disk busy" in caplog.text


_BEATING_CHILD = """
import sys, time
from pathlib import Path
from worldmock import status
status.HEARTBEAT_INTERVAL_SECONDS = 0.2
with status.DriverHeartbeat(Path(sys.argv[1])):
    time.sleep(60)
"""


def test_killed_driver_reads_stale_within_three_intervals(tmp_path, monkeypatch):
    """驱动进程写过第一次心跳后被结束：读者按同一间隔在两秒内报告 stale。"""
    run = _run(tmp_path, monkeypatch, [("observation", _ago(1))])
    write_status(run, "running", "deciding")
    child = subprocess.Popen([sys.executable, "-c", _BEATING_CHILD, str(run)])
    try:
        deadline = time.monotonic() + 60
        while not (run / "heartbeat.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (run / "heartbeat.json").exists()
    finally:
        child.kill()
        child.wait()
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL_SECONDS", 0.2)
    deadline, liveness = time.monotonic() + 2, None
    while time.monotonic() < deadline and liveness != "stale":
        liveness = read_status(run)["liveness"]
        time.sleep(0.05)
    assert liveness == "stale"


def test_status_reads_heartbeat_as_utf8(tmp_path, monkeypatch):
    """有心跳时 status 的读取全部写明 UTF-8，不依赖系统默认编码。"""
    run = _run(tmp_path, monkeypatch, [("observation", _ago(5))])
    write_status(run, "running", "deciding")
    _beat(run, _ago(1))
    calls = default_encoding_reads(monkeypatch)
    assert read_status(run)["heartbeat"]["pid"] == 1
    assert calls == []
