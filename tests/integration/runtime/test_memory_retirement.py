"""人物一侧的记忆不再调用模型：只发行动请求；memory_updated 仅在步尾登记（11 键）；损坏证据与失败唤醒均不写工作区。"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import replace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.host_run_fixtures import _evening, host_run, memory, tool_names  # noqa: E402
from tests.life.conftest import MAIN_TOOL, stage_day_plan  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.recording_engine.events.memory_evidence import MEMORY_UPDATE_KEYS  # noqa: E402
from worldmock.recording_engine.reports.quality import _valid_memory_update_payload  # noqa: E402
from worldmock.simulation_engine.engine import LifeEngine  # noqa: E402
from worldmock.state.model.exchange import is_action_request  # noqa: E402
from worldmock.state.model.memory import valid_subjective_snapshots  # noqa: E402

#: 两次收尾各写的六条互不相似的记忆。
_NOTES = (("早上跑了五公里", "给妈妈回了电话", "把书架整理好了", "午饭吃了一碗面", "下午读完一章小说", "晚上去超市买菜"),
          ("周末想去爬山", "答应同事周五交初稿", "楼下新开了花店", "学会了做蛋饼", "阳台的花开了", "地铁上听了播客"))


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


async def _woken(engine_factory, tmp_path, checker=None):
    """周一 20:00 开始做两次休息写 12 条记忆并睡 480 分钟，检查每一条提交后的 memory_updated 证据。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    await run.start("interact", "家", "测试休息")
    for index, notes in enumerate(_NOTES):
        entries = [memory(text, importance=0.7) for text in notes]
        if index + 1 < len(_NOTES):
            await run.conclude_and_start(*entries)
        else:
            slept = await run.conclude_and_sleep(480, *entries)
    await run.conclude()
    assert engine.context.state.activities[slept].concluded_at is not None
    if checker is not None:
        for event in run.events("memory_updated"):
            checker(engine, event)
    return engine, run


async def test_first_wake_up_of_the_day_sends_only_action_requests(engine_factory, tmp_path):
    """当天第一次睡醒那一步只发行动请求，不带 purpose；其余只有零点计划与日回顾。"""
    engine, run = await _woken(engine_factory, tmp_path)
    assert run.model.requests and all(MAIN_TOOL in tool_names(request) for _, request in run.model.requests)
    requests = [event.data for event in run.events("model_request")]
    assert all(is_action_request(data["request"]) for data in requests if data.get("purpose") is None)
    assert {data["purpose"] for data in requests if data.get("purpose") is not None} == {"plan_day", "memory_digest"}
    view = (tmp_path / "people" / "agent_0001" / "MEMORY.md").read_text(encoding="utf-8")
    assert view.endswith("## 人物自述（未经核验）\n> 暂无人物自述。\n")


async def test_memory_updates_are_step_tail_registrations_only(engine_factory, tmp_path):
    """每条 memory_updated 均为 MEMORY_UPDATE_KEYS 十一键，同一唤醒至多一条，条目快照恰为四键。"""
    engine, run = await _woken(engine_factory, tmp_path)
    updates = run.events("memory_updated")
    assert len(updates) >= 2
    observations = {event.wakeup_id: event.event_id for event in run.events("observation")}
    for event in updates:
        data = event.data
        assert len(MEMORY_UPDATE_KEYS) == 11 and set(data) == set(MEMORY_UPDATE_KEYS)
        assert not {"consolidated", "reflection", "state_sha256"} & set(data)
        assert data["observation_event_id"] == observations[event.wakeup_id]
        assert data["trigger"] in ([], ["activity_conclusion"])
        assert all(set(item) == {"id", "type", "text", "epistemic_status"} for item in data["episodes"])
    assert len({event.wakeup_id for event in updates}) == len(updates)


async def test_first_memory_update_of_the_agent_has_a_null_previous_view_digest(engine_factory, tmp_path):
    """第一条 memory_updated 写入前摘要为 null，其后每条等于前条 memory_sha256。"""
    engine, run = await _woken(engine_factory, tmp_path)
    updates = [event.data for event in run.events("memory_updated")]
    assert updates[0]["memory_before_sha256"] is None
    assert all(later["memory_before_sha256"] == earlier["memory_sha256"]
               for earlier, later in zip(updates, updates[1:]))
    view = (tmp_path / "people" / "agent_0001" / "MEMORY.md").read_bytes()
    assert updates[-1]["memory_sha256"] == hashlib.sha256(view).hexdigest()


def _refused_ev(field: str, value):
    """按既有 memory_updated 构造负载替换指定字段并交给 _valid_memory_update_payload 核对被拒。"""
    def check(_engine, event) -> None:
        data = dict(event.data)
        assert _valid_memory_update_payload(data) is True
        changed = value(data) if callable(value) else value
        if callable(value) and changed is None:
            return
        data[field] = list(changed) if field == "episodes" and isinstance(changed, tuple) else changed
        assert _valid_memory_update_payload(data) is False
        if field == "episodes":
            ids = list(data["created_episode_ids"]) + list(data["merged_episode_ids"])
            assert not valid_subjective_snapshots(data["episodes"], ids)
        check.checked += 1

    check.checked = 0
    return check


async def test_memory_update_evidence_without_an_observation_is_rejected(engine_factory, tmp_path, caplog):
    """observation_event_id 为空时拒绝。"""
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, _refused_ev("observation_event_id", None))


async def test_a_five_key_snapshot_in_memory_update_evidence_is_rejected(engine_factory, tmp_path, caplog):
    """五键条目快照被拒绝。"""
    check = _refused_ev("episodes", lambda d: tuple({**item, "source": "reflection"} for item in d["episodes"]) or None)
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, check)
    assert check.checked >= 2


@pytest.mark.parametrize("value", ["not-a-digest", "", "A" * 64])
async def test_memory_update_evidence_with_a_malformed_previous_view_digest_is_rejected(
    engine_factory, tmp_path, caplog, value,
):
    """格式错误的写入前视图摘要被拒绝。"""
    check = _refused_ev("memory_before_sha256", value)
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, check)
    assert check.checked >= 1


async def test_a_summary_refresh_whose_view_did_not_change_is_rejected(engine_factory, tmp_path, caplog):
    """视图未变的刷新摘要被拒绝。"""
    check = _refused_ev("memory_before_sha256",
                        lambda d: d["memory_sha256"] if d["operation"] == "refresh_summary" else None)
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, check)
    assert check.checked >= 1


async def test_a_failed_wakeup_saves_no_step_and_registers_no_memory_update(engine_factory, tmp_path, monkeypatch):
    """唤醒过程中沙盒派发失败：上抛 TOOL_SERVICE_FAILED，工作区无 MEMORY.md 或 episodes.jsonl，库无 memory_updated。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    workspace = tmp_path / "people" / "agent_0001"
    run = await host_run(engine, workspace)
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sb = real_open(wakeup)
        sb.dispatch_world_tool = lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
        return sb

    monkeypatch.setattr(engine, "open_sandbox", wrapped_open)
    with pytest.raises(RuntimeError, match="^TOOL_SERVICE_FAILED"):
        await run.start("interact", "家", "测试休息")
    assert len(run.model.requests) == 1
    assert run.events("memory_updated") == []
    assert not (workspace / "MEMORY.md").exists()
    assert not (workspace / "memory" / "episodes.jsonl").exists()


def test_life_engine_surface_lacks_retired_query_and_reflection_methods():
    """生活引擎没有 due_reflection 或 read_query，记忆检索直接走 search_memory。"""
    assert not hasattr(LifeEngine, "due_reflection")
    assert not hasattr(LifeEngine, "read_query")
    assert callable(getattr(LifeEngine, "search_memory", None))
    assert callable(getattr(LifeEngine, "read_task_summary", None))
