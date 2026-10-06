"""@brief 真实引擎只解析本决策的冻结观察，历史记录及缺失、歧义合同保持不变。"""

from __future__ import annotations

import logging

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import context, report_request, stage_day_plan, start_activity, submit_round
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import Event
from worldmock.life.models import SendMessageActionRequest, active_activities
from worldmock.life.recipients import person_bindings


def _pending_with_history(engine_factory, history, present_message=False):
    """@brief 经真实观察和宿主请求登记，得到待结算动作及其实际来源观察。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    if present_message:
        recipient = next(row["name"] for row in person_bindings(engine.context.state, 2) if row["person_id"] == "agent:1")
        _, sent = submit_round(engine, {
            1: ("start_activity", start_activity("等待", "自己")),
            2: ("send_message", SendMessageActionRequest(recipient_name=recipient, text="晚上一起吃饭吗？")),
        }, "presented-message")
        assert next(row for row in sent if row.agent_id == 2).status == "completed"
    for agent_id in engine.context.state.agents:
        for index in range(history):
            engine.observe(context(engine, f"history-{index}", agent_id))
    decisions = [context(engine, "lookup", agent_id) for agent_id in engine.scheduling_state().ready_agent_ids]
    engine.begin_decision_round(decisions)
    observed = {}
    for decision in decisions:
        observed[decision.agent_id] = engine.observe(decision)
        running = active_activities(engine.context.state, decision.agent_id)
        tool, request = (("report_status", report_request(*(row.name for row in running))) if running
                         else ("start_activity", start_activity("等待", "自己")))
        engine.submit_action(DirectToolCall(
            decision, f"lookup-action-{decision.agent_id}", tool, request))
    pending = next(row for row in engine.decisions.round.pending_actions if row.agent_id == 1)
    raw = engine.context.store.connection.execute(
        "SELECT body FROM events WHERE event_id=?", (observed[1]["observation_event_id"],)).fetchone()[0]
    return engine, decisions, pending, Event.model_validate_json(raw)


def _parsed_events(monkeypatch):
    """@brief 包装真实 Event 解析器，计数而不替换任何观察内容或校验。"""
    parsed = []
    original = Event.model_validate_json

    def tracked(cls, body, *arguments, **keywords):
        event = original(body, *arguments, **keywords)
        parsed.append(event)
        return event

    monkeypatch.setattr(Event, "model_validate_json", classmethod(tracked))
    return parsed


@pytest.mark.parametrize("history", [0, 32, 128])
def test_lookup_parses_only_the_exact_actual_observation_after_real_history(engine_factory, monkeypatch, history):
    """@brief 历史长度不增加本次解析数，返回真实原始数据且读取不改变世界或任何事件字节。"""
    engine, _, pending, expected = _pending_with_history(engine_factory, history)
    store = engine.context.store
    before = store.connection.execute("SELECT event_seq,event_id,body FROM events ORDER BY event_seq").fetchall()
    world = engine.context.state.model_dump(mode="json")
    parsed = _parsed_events(monkeypatch)

    assert engine.actions._frozen_observation(pending) == expected.data
    assert [row.event_id for row in parsed] == [expected.event_id]
    parsed.clear()
    assert engine.decisions._presented_messages(pending) == frozenset(expected.data["presented_message_ids"])
    assert [row.event_id for row in parsed] == [expected.event_id]
    assert store.connection.execute("SELECT event_seq,event_id,body FROM events ORDER BY event_seq").fetchall() == before
    assert engine.context.state.model_dump(mode="json") == world
    assert len([row for row in before if '"event_type":"observation"' in row[2]]) == (history + 1) * 2


@pytest.mark.parametrize("field,value", [("agent_id", 2), ("decision_id", "missing"), ("observed_at", 1)])
def test_wrong_exact_observation_identity_is_missing_without_parsing_history(
        engine_factory, monkeypatch, caplog, field, value):
    """@brief 人物、决策或时刻不匹配均拒绝，不解析相邻人物或历史观察来替代。"""
    engine, _, pending, _ = _pending_with_history(engine_factory, 12)
    changed = pending.model_copy(update={field: pending.observed_at + value if field == "observed_at" else value})
    parsed = _parsed_events(monkeypatch)
    with caplog.at_level(logging.ERROR), pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine.actions._frozen_observation(changed)
    assert parsed == []
    assert "Decision observation is missing or ambiguous" in caplog.text
    assert engine.decisions._presented_messages(changed) == frozenset()
    assert parsed == []


def test_a_second_real_matching_observation_is_reparsed_and_rejected(engine_factory, monkeypatch, caplog):
    """@brief 同一真实决策追加观察后即歧义，必须重读两个匹配记录，不限条数或沿用先前结果。"""
    engine, decisions, pending, first = _pending_with_history(engine_factory, 12)
    assert engine.actions._frozen_observation(pending) == first.data
    decision = next(row for row in decisions if row.agent_id == 1)
    repeated = engine.observe(decision)
    parsed = _parsed_events(monkeypatch)
    with caplog.at_level(logging.ERROR), pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine.actions._frozen_observation(pending)
    assert [row.event_id for row in parsed] == [first.event_id, repeated["observation_event_id"]]
    assert "Decision observation is missing or ambiguous" in caplog.text
    parsed.clear()
    with pytest.raises(ProtocolError, match="Decision observation is ambiguous"):
        engine.decisions._presented_messages(pending)
    assert [row.event_id for row in parsed] == [first.event_id, repeated["observation_event_id"]]


def test_actual_presented_message_is_read_only_when_the_real_round_commits(engine_factory, monkeypatch):
    """@brief 真实人物消息全文呈现后，仅查询本决策记录，提交时才记为已读，消息正文不改变。"""
    engine, _, pending, expected = _pending_with_history(engine_factory, 32, present_message=True)
    agent = engine.context.state.agents[1]
    ids = frozenset(expected.data["presented_message_ids"])
    assert ids == frozenset(agent.inbox) and ids
    assert agent.read_inbox_ids == set()
    inbox = dict(agent.inbox)
    parsed = _parsed_events(monkeypatch)
    assert engine.decisions._presented_messages(pending) == ids
    assert [row.event_id for row in parsed] == [expected.event_id]
    assert engine.context.state.agents[1].read_inbox_ids == set()
    assert all(row.status in {"completed", "in_progress"} for row in engine.commit_decision_round("lookup"))
    assert engine.context.state.agents[1].read_inbox_ids == set(ids)
    assert engine.context.state.agents[1].inbox == inbox
