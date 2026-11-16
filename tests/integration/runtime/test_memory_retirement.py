"""人物一侧的记忆不再调用模型（docs/design/06-agent.md「宿主模型协议」、docs/design/05-recording-engine.md「事件与来源」）：
当天第一次睡醒那一步与其他步骤相同，只发行动请求；memory_updated 只有步尾登记，数据恰为十一个键，人物第一条的写入前摘要
为 null；环境拒绝来源观察为空或带五键快照的证据；生活引擎没有 due_reflection，只读查询经 read_query。

真实宿主运行（tests/integration/host_run_fixtures.py）：真实引擎、SQLite、记忆存储与直接导入的 LifeAgent，引擎就是
人物拿到的句柄；脚本模型只答行动请求，收到别的请求当场失败。
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import replace

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: E402,F401
from tests.integration.host_run_fixtures import _evening, host_run, memory, tool_names  # noqa: E402
from tests.life.conftest import MAIN_TOOL, stage_day_plan  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.recording_engine.events.memory_evidence import MEMORY_UPDATE_KEYS  # noqa: E402
from worldmock.simulation_engine.engine import LifeEngine  # noqa: E402
from worldmock.state.model.exchange import is_action_request  # noqa: E402

#: 两次收尾各写的六条互不相似的记忆。
_NOTES = (("早上跑了五公里", "给妈妈回了电话", "把书架整理好了", "午饭吃了一碗面", "下午读完一章小说", "晚上去超市买菜"),
          ("周末想去爬山", "答应同事周五交初稿", "楼下新开了花店", "学会了做蛋饼", "阳台的花开了", "地铁上听了播客"))


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


async def _woken(engine_factory, tmp_path, recorder=None):
    """周一 20:00 开始：做两次测试休息，每次收尾写六条重要度 0.7 的记忆（合计 12 条、重要度 8.4，超过已退役的两道阈值），
    再睡 480 分钟到当天第一次睡醒，并完成那一步的决策（给这段睡眠收尾）。recorder 给出时包住引擎的记忆登记：
    人物调用 engine.record_memory_update 时先交给替身检查，再照常登记。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    if recorder is not None:
        original = engine.record_memory_update

        def record(evidence):
            """先交给替身检查，再照常登记。"""
            recorder(engine, evidence)
            original(evidence)

        engine.record_memory_update = record
    for notes in _NOTES:
        await run.start("interact", "家", "测试休息")
        await run.conclude(*(memory(text, importance=0.7) for text in notes))
    slept = await run.sleep(480)
    await run.conclude()
    assert engine.context.state.activities[slept].concluded_at is not None
    return engine, run


async def test_first_wake_up_of_the_day_sends_only_action_requests(engine_factory, tmp_path):
    """当天第一次睡醒那一步与其他步骤相同：脚本模型收到的每个请求的工具都含 start_main_activity；库里不带 purpose 的
    model_request 都是行动请求，其余只有零点的 plan_day 与 memory_digest；MEMORY.md 的人物自述写「暂无人物自述」。"""
    engine, run = await _woken(engine_factory, tmp_path)
    assert run.model.requests and all(MAIN_TOOL in tool_names(request) for _, request in run.model.requests)
    requests = [event.data for event in run.events("model_request")]
    assert all(is_action_request(data["request"]) for data in requests if data.get("purpose") is None)
    assert {data["purpose"] for data in requests if data.get("purpose") is not None} == {"plan_day", "memory_digest"}
    view = (tmp_path / "people" / "agent_0001" / "MEMORY.md").read_text(encoding="utf-8")
    assert view.endswith("## 人物自述（未经核验）\n> 暂无人物自述。\n")


async def test_memory_updates_are_step_tail_registrations_only(engine_factory, tmp_path):
    """每条 memory_updated 都是步尾登记：数据恰为 MEMORY_UPDATE_KEYS 的十一个键（没有 consolidated、reflection、
    state_sha256），来源观察是本决策的观察，写入点是 [] 或 ["activity_conclusion"]；同一决策至多一条；快照恰为四个基本键。"""
    engine, run = await _woken(engine_factory, tmp_path)
    updates = run.events("memory_updated")
    assert len(updates) >= 2
    observations = {event.decision_id: event.event_id for event in run.events("observation")}
    for event in updates:
        data = event.data
        assert len(MEMORY_UPDATE_KEYS) == 11 and set(data) == set(MEMORY_UPDATE_KEYS)
        assert not {"consolidated", "reflection", "state_sha256"} & set(data)
        assert data["observation_event_id"] == observations[event.decision_id]
        assert data["trigger"] in ([], ["activity_conclusion"])
        assert all(set(item) == {"id", "type", "text", "epistemic_status"} for item in data["episodes"])
    assert len({event.decision_id for event in updates}) == len(updates)


async def test_first_memory_update_of_the_agent_has_a_null_previous_view_digest(engine_factory, tmp_path):
    """人物的第一条 memory_updated 写入前摘要为 null（工作区里还没有 MEMORY.md）；此后每条都等于本人上一条的
    memory_sha256，最后一条的 memory_sha256 是工作区里 MEMORY.md 的摘要。"""
    engine, run = await _woken(engine_factory, tmp_path)
    updates = [event.data for event in run.events("memory_updated")]
    assert updates[0]["memory_before_sha256"] is None
    assert all(later["memory_before_sha256"] == earlier["memory_sha256"]
               for earlier, later in zip(updates, updates[1:]))
    view = (tmp_path / "people" / "agent_0001" / "MEMORY.md").read_bytes()
    assert updates[-1]["memory_sha256"] == hashlib.sha256(view).hexdigest()


def _refused(field: str, value):
    """替身检查：把本次的真实步尾证据改写一个键交给引擎，必须被拒且库里不多一条事件；value 可以是由证据算出改写值的函数，
    算出 None 表示这条证据不适用、不检查。返回的检查函数在属性 checked 里记下实际检查的条数。"""
    def check(engine, evidence) -> None:
        changed = value(evidence) if callable(value) else value
        if callable(value) and changed is None:
            return
        before = engine.context.store.last_event_seq
        with pytest.raises(ProtocolError, match="^Memory update evidence is invalid$"):
            LifeEngine.record_memory_update(engine, replace(evidence, **{field: changed}))
        assert engine.context.store.last_event_seq == before
        check.checked += 1

    check.checked = 0
    return check


async def test_memory_update_evidence_without_an_observation_is_rejected(engine_factory, tmp_path, caplog):
    """把一条合法的步尾证据改成 observation_event_id 为空交给 LifeEngine.record_memory_update：协议错误，先写拒绝日志，
    事件序号不变。"""
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, _refused("observation_event_id", None))
    assert "Memory update evidence rejected" in caplog.text


async def test_a_five_key_snapshot_in_memory_update_evidence_is_rejected(engine_factory, tmp_path, caplog):
    """同上，把带条目的证据的每条快照加上第五个键 source=reflection：五键快照不再是合法快照，整条证据被拒。"""
    def five_keys(evidence):
        """带条目的证据改成五键快照；没有条目的证据不适用。"""
        return tuple({**item, "source": "reflection"} for item in evidence.episodes) or None

    check = _refused("episodes", five_keys)
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, check)
    assert check.checked >= 2
    assert "Memory update evidence rejected" in caplog.text


def test_life_channel_offers_only_read_query():
    """生活引擎没有 due_reflection；人物的只读查询经 LifeEngine.read_query。"""
    assert not hasattr(LifeEngine, "due_reflection")
    assert callable(getattr(LifeEngine, "read_query", None))
