"""@brief 唤醒动作身份形式、跨搜索恢复编号递增、过期唤醒屏障协议拒绝与请求不复用（action_requested 无 reused 为真）。"""

from __future__ import annotations

import re

import pytest

from tests.integration.runtime.current_contract_fixtures import ScriptedModel, ToolResponse, resolved_binding
from tests.life.conftest import (
    SEARCH_TOOL,
    SIDE_TOOL,
    advance_next,
    advance_until_ready,
    do_at,
    report_request,
    sleep,
    stage_day_plan,
    stub_day_session,
    use_app,
)
from tests.life.environment_runtime import act, act_group, events, every_five_minutes
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.barrier import SearchSessionOutcome


@pytest.mark.asyncio
async def test_wakeup_generates_distinct_sequential_action_identities_across_suspend_and_resume(engine_factory):
    """同一唤醒经搜索挂起再恢复：前后动作身份严格保持 <wakeup_id>:action:<四位ASCII数字> 且递增不碰撞。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    frozen = engine.context.inputs
    agent = LifeAgent(person_inputs(frozen.persona_by_id[1]))

    [w1] = engine.prepare_barrier((1,))
    sb1 = engine.open_sandbox(w1)
    m1 = ScriptedModel([ToolResponse(SEARCH_TOOL, {"query": "买柠檬茶", "prefer": "饿了么"}, call_id="c1")])
    outcome1 = await agent.decide_wakeup(w1, sb1, resolved_binding(m1, frozen=frozen, stage="action", agent_id=1))
    assert outcome1.status == "suspended"
    engine.commit_barrier([outcome1])

    advance_next(engine)
    [(task, _)] = engine.due_searches()
    engine.complete_searches([SearchSessionOutcome(
        action_id=task.action_id, agent_id=1, activity_id=task.activity_id,
        goods_ids=("lemon-tea",), place_ids=(), summary="找到柠檬茶",
    )])

    [w2] = engine.prepare_barrier((1,))
    assert w2.wakeup_id == w1.wakeup_id and w2.mode == "resume_search"
    sb2 = engine.open_sandbox(w2)
    m2 = ScriptedModel([ToolResponse(SIDE_TOOL, {"action": "purchase", "target": ["柠檬茶"], "prefer": "饿了么"}, call_id="c2")])
    outcome2 = await agent.decide_wakeup(w2, sb2, resolved_binding(m2, frozen=frozen, stage="action", agent_id=1))
    assert outcome2.status == "completed"
    engine.commit_barrier([outcome2])

    requests = [ev for ev in events(engine, "action_requested") if ev.wakeup_id == w1.wakeup_id]
    assert len(requests) == 2
    pattern = re.compile(rf"^{re.escape(w1.wakeup_id)}:action:(\d{{4}})$")
    seqs = [int(pattern.fullmatch(ev.action_id).group(1)) for ev in requests]
    assert seqs[0] < seqs[1] and len(set(ev.action_id for ev in requests)) == 2


def test_stale_wakeup_snapshot_and_mismatched_agent_commit_are_protocol_faults(engine_factory):
    """过期世界版本的 wakeup 打开沙盒、不匹配的 agent 列表提交屏障，均以协议故障拒绝且不改状态与事件。"""
    engine = engine_factory()
    [stale] = engine.prepare_barrier((1,))
    _, first = act(engine, "first", do_at("家", "测试休息"))
    assert first.status == "in_progress", first.error_code
    assert advance_until_ready(engine)
    seq_before = engine.context.store.last_event_seq
    activities_before = engine.context.state.model_dump(mode="json")["activities"]

    with pytest.raises(ProtocolError, match="Wakeup world state version mismatch"):
        engine.open_sandbox(stale)
    with pytest.raises(ProtocolError, match="Barrier agent_ids must match scheduled ready agents"):
        engine.prepare_barrier((2,))
    [fresh] = engine.prepare_barrier((1,))
    outcome = engine.open_sandbox(fresh).finish_wakeup(None, stub_day_session(fresh))
    with pytest.raises(ProtocolError, match="not scheduled in barrier"):
        engine.commit_barrier([outcome.model_copy(update={"agent_id": 99})])
    assert engine.context.store.last_event_seq == seq_before
    assert engine.context.state.model_dump(mode="json")["activities"] == activities_before


def test_every_action_request_records_no_replay_and_no_result_carries_reused(engine_factory):
    """单条开始、整组被拒与整组受理的开始组、控制请求：action_requested 永不标记复用，受理、拒绝与结果事件都不带 reused 键。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, refused = act_group(engine, "refused", sleep(60), use_app("哔哩哔哩"))
    assert [item.status for item in refused] == ["rejected", "rejected"]
    _, started = act_group(engine, "pair", do_at("家", "测试休息"), use_app("哔哩哔哩"))
    assert [item.status for item in started] == ["in_progress", "in_progress"]
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started[0].activity_id].name
    _, reported = act(engine, "report", report_request(name), "report_status")
    assert reported.status == "completed", reported.error_code
    requested = events(engine, "action_requested")
    assert len(requested) == 5 and all(row.data.get("reused") is not True for row in requested)
    assert not [row for row in events(engine) if row.event_type in {"action_accepted", "action_rejected",
                                                                    "action_result"} and "reused" in row.data]
