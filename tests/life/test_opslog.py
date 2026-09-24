"""操作日志写入器与生活引擎埋点的字段、切分、容错和关联键测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentsociety2.agent.base import MemoryUpdateEvidence, ModelCallEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import context, submit_round
from worldmock.life.engine import LifeEngine
from worldmock.life.models import StartActivityRequest
from worldmock.opslog import OPS_COMPONENTS, OPS_LEVELS, OpsLogger


def _records(engine: LifeEngine) -> list[dict]:
    """读取当前运行已落盘的全部操作日志记录。"""
    logs = engine.store.run_dir / "logs"
    return [json.loads(line) for path in sorted(logs.glob("ops-*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_opslog_writer_emits_complete_fields(tmp_path: Path) -> None:
    """一条记录包含全部固定字段、非空关联键，且 None 键不落盘。"""
    day1 = int(datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc).timestamp())
    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(day1, "engine", "info", "DECISION_ROUND_COMMITTED", "Round committed",
               agent_id=1, decision_id="decision-x", round_id="ops", action_id="a-1",
               event_seq_first=3, event_seq_last=9, tokens_in=11, tokens_out=7,
               model="deepseek-flash", omitted_key=None)
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
    writer.log(day1, "runtime", "info", "RUN_STARTED", "Run started")
    writer.log(day1, "runtime", "info", "RUN_ENDED", "Run finished")
    writer.log(day1 + 24 * 3600, "runtime", "info", "RUN_ENDED", "Run finished")
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
    writer.log(0, "engine", "error", "RUN_FAILED", "must not raise")
    writer.close()


def test_opslog_writer_appends_across_instances(tmp_path: Path) -> None:
    """新实例以追加模式继续写入同一模拟日文件。"""
    first = OpsLogger(tmp_path, "UTC")
    first.log(0, "runtime", "info", "RUN_STARTED", "Run started")
    first.close()
    second = OpsLogger(tmp_path, "UTC")
    second.log(60, "runtime", "info", "RUN_ENDED", "Run finished")
    second.close()
    lines = (tmp_path / "logs" / "ops-19700101.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["code"] for line in lines] == ["RUN_STARTED", "RUN_ENDED"]


def test_opslog_records_decision_round_and_model_exchange(engine_factory) -> None:
    """真实决策轮与模型交换产生带关联键的操作日志和足迹事件区间。"""
    engine = engine_factory()
    ctx = context(engine, "ops")
    engine.begin_decision_round([ctx])
    request = {"model": "deepseek-flash", "messages": [], "tools": []}
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(
        ctx, call_id, "response", 1, request,
        response={"model": "deepseek-flash", "usage": {"prompt_tokens": 11, "completion_tokens": 7}}))
    engine.observe(ctx)
    wait = StartActivityRequest(targets=["自己"], actions=["等待"])
    engine.submit_action(DirectToolCall(ctx, "ops-wait", "start_activity", wait))
    engine.commit_decision_round("ops")
    records = _records(engine)
    codes = [record["code"] for record in records]
    assert codes.count("DECISION_ROUND_BEGAN") == 1 and codes.count("DECISION_ROUND_COMMITTED") == 1
    committed = next(record for record in records if record["code"] == "DECISION_ROUND_COMMITTED")
    assert committed["round_id"] == "ops"
    assert 1 <= committed["event_seq_first"] <= committed["event_seq_last"] <= engine.store.last_event_seq
    exchanges = [record for record in records if record["code"] == "MODEL_EXCHANGE"]
    assert [record["detail"].split()[1] for record in exchanges] == ["request", "response"]
    response = exchanges[1]
    assert response["agent_id"] == ctx.agent_id and response["decision_id"] == ctx.decision_id
    assert response["model"] == "deepseek-flash"
    assert response["tokens_in"] == 11 and response["tokens_out"] == 7
    accepted = next(record for record in records if record["code"] == "ACTION_ACCEPTED")
    assert accepted["agent_id"] == ctx.agent_id and accepted["decision_id"] == ctx.decision_id
    assert accepted["round_id"] == "ops" and accepted["action_id"] == "ops-wait"
    assert accepted["tool_name"] == "start_activity"


def test_opslog_records_rejection_with_error_code(engine_factory) -> None:
    """业务拒绝产生 warn 级 ACTION_REJECTED 记录并携带稳定错误码。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("start_activity", StartActivityRequest(
            targets=["missing-content"], actions=["观看"]))}, "missing-content")
    assert results[0].status == "rejected"
    records = _records(engine)
    rejected = next(record for record in records if record["code"] == "ACTION_REJECTED")
    assert rejected["level"] == "warn" and rejected["error_code"] == "ACTION_REQUEST_INVALID"
    assert rejected["round_id"] == "missing-content" and rejected["agent_id"] == 1


def test_opslog_records_memory_update(engine_factory) -> None:
    """记忆更新记录带人物与决策关联键及 episode 数量。"""
    engine = engine_factory()
    ctx = context(engine, "memory-ops")
    observation = engine.observe(ctx)
    evidence = MemoryUpdateEvidence(
        context=ctx, operation="append_episodes", created_episode_ids=("episode-1",),
        merged_episode_ids=(), episodes=({"id": "episode-1", "type": "reflection",
                                          "text": "记得今天的事", "epistemic_status": "subjective"},),
        memory_before_sha256="0" * 64, memory_sha256="a" * 64,
        episodes_sha256="b" * 64, state_sha256="c" * 64, consolidated=False,
        observation_event_id=observation["observation_event_id"], verified_event_ids=())
    engine.record_memory_update(evidence)
    records = _records(engine)
    updated = next(record for record in records if record["code"] == "MEMORY_UPDATED")
    assert updated["component"] == "memory" and updated["level"] == "info"
    assert updated["agent_id"] == ctx.agent_id and updated["decision_id"] == ctx.decision_id
    assert "1 episodes" in updated["detail"]
