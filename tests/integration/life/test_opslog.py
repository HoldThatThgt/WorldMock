"""操作日志写入器与生活引擎埋点的字段、切分、容错和关联键测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentsociety2.agent.base import MemoryUpdateEvidence, ModelCallEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    action_identity,
    advance_until_ready,
    conclude_request,
    context,
    pending_names,
    purchase,
    query,
    stage_day_plan,
    submit_round,
    travel,
    use_app,
)
from tests.life.environment_runtime import act, act_group
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.recording_engine.storage.opslog import OPS_COMPONENTS, OPS_LEVELS, OpsLogger


def _records(engine: LifeEngine) -> list[dict]:
    """读取当前运行已落盘的全部操作日志记录。"""
    logs = engine.context.store.run_dir / "logs"
    return [json.loads(line) for path in sorted(logs.glob("ops-*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_opslog_writer_emits_complete_fields(tmp_path: Path) -> None:
    """一条记录包含全部固定字段、非空关联键，且 None 键不落盘。"""
    day1 = int(datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc).timestamp())
    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(day1, "engine", "info", {"code": "DECISION_ROUND_COMMITTED", "detail": "Round committed",
               "agent_id": 1, "decision_id": "decision-x", "round_id": "ops", "action_id": "a-1",
               "event_seq_first": 3, "event_seq_last": 9, "tokens_in": 11, "tokens_out": 7,
               "model": "deepseek-flash", "omitted_key": None})
    writer.close()
    line = (tmp_path / "logs" / "ops-20260105.jsonl").read_text(encoding="utf-8")
    record = json.loads(line)
    datetime.fromisoformat(record["at"])
    assert record["sim_time"] == "2026-01-05T00:30:00+08:00"
    assert record["component"] == "engine" and record["component"] in OPS_COMPONENTS
    assert record["level"] == "info" and record["level"] in OPS_LEVELS
    assert record["code"] == "DECISION_ROUND_COMMITTED"
    assert record["detail"] == "Round committed"
    assert record["agent_id"] == 1 and record["decision_id"] == "decision-x"
    assert record["round_id"] == "ops" and record["action_id"] == "a-1"
    assert record["event_seq_first"] == 3 and record["event_seq_last"] == 9
    assert record["tokens_in"] == 11 and record["tokens_out"] == 7
    assert record["model"] == "deepseek-flash" and "omitted_key" not in record


def test_opslog_writer_splits_files_by_simulation_day(tmp_path: Path) -> None:
    """按场景时区的模拟日期切分文件，同日记录进入同一文件。"""
    day1 = int(datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc).timestamp())
    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(day1, "runtime", "info", {"code": "RUN_STARTED", "detail": "Run started"})
    writer.log(day1, "runtime", "info", {"code": "RUN_ENDED", "detail": "Run finished"})
    writer.log(day1 + 24 * 3600, "runtime", "info", {"code": "RUN_ENDED", "detail": "Run finished"})
    writer.close()
    names = sorted(path.name for path in (tmp_path / "logs").iterdir())
    assert names == ["ops-20260105.jsonl", "ops-20260106.jsonl"]
    first = (tmp_path / "logs" / names[0]).read_text(encoding="utf-8").splitlines()
    second = (tmp_path / "logs" / names[1]).read_text(encoding="utf-8").splitlines()
    assert len(first) == 2 and len(second) == 1


def test_opslog_writer_swallows_write_failures(tmp_path: Path) -> None:
    """logs 路径被文件占据等写入故障只被吞掉，不影响主流程。"""
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    writer = OpsLogger(tmp_path, "UTC")
    writer.log(0, "engine", "error", {"code": "RUN_FAILED", "detail": "must not raise"})
    writer.close()


def test_opslog_writer_appends_across_instances(tmp_path: Path) -> None:
    """新实例以追加模式继续写入同一模拟日文件。"""
    first = OpsLogger(tmp_path, "UTC")
    first.log(0, "runtime", "info", {"code": "RUN_STARTED", "detail": "Run started"})
    first.close()
    second = OpsLogger(tmp_path, "UTC")
    second.log(60, "runtime", "info", {"code": "RUN_ENDED", "detail": "Run finished"})
    second.close()
    lines = (tmp_path / "logs" / "ops-19700101.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["code"] for line in lines] == ["RUN_STARTED", "RUN_ENDED"]


def test_opslog_component_follows_each_start_call(engine_factory) -> None:
    """操作日志的组件按调用判定：出行记在 travel，购买记在 commerce，其余（这里是用内容 app 与查询）记在 engine；
    开始组的两条动作各写一条，带各自的动作身份与工具名。"""
    engine = engine_factory()
    _, refused = act(engine, "ops-app", use_app("不存在"))
    assert refused.status == "rejected" and refused.error_code == "ACTION_REQUEST_INVALID"
    _, started = act_group(engine, "ops-group", travel("公司"), purchase("平台笔记本"))
    assert [item.status for item in started] == ["in_progress", "in_progress"]
    records = [record for record in _records(engine) if record["code"] in {"ACTION_ACCEPTED", "ACTION_REJECTED"}]
    assert [(record["code"], record["component"], record["level"], record["action_id"], record["tool_name"])
            for record in records] == [
        ("ACTION_REJECTED", "engine", "error", action_identity("ops-app"), "start_side_activity"),
        ("ACTION_ACCEPTED", "travel", "info", action_identity("ops-group", 1), "start_main_activity"),
        ("ACTION_ACCEPTED", "commerce", "info", action_identity("ops-group", 2), "start_side_activity")]
    assert records[0]["error_code"] == "ACTION_REQUEST_INVALID"
    assert all("error_code" not in record for record in records[1:])
    assert {record["component"] for record in records} <= OPS_COMPONENTS
    assert len({record["decision_id"] for record in records[1:]}) == 1
    asking = engine_factory()
    _, asked = act(asking, "ops-query", query("柠檬茶"))
    assert asked.status == "in_progress"
    [accepted] = [record for record in _records(asking) if record["code"] == "ACTION_ACCEPTED"]
    assert (accepted["component"], accepted["action_id"], accepted["tool_name"]) == (
        "engine", action_identity("ops-query"), "start_side_activity")
