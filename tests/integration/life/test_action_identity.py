"""动作身份的形式与请求身份的复用范围：环境只受理 {decision_id}:action:{四位序号} 形式的动作身份，同一身份只在开放的
决策轮里复用；没有跨轮的请求重放，action_requested 的 reused 恒为 false（docs/design/04-simulation-engine.md
「原子性与恢复」）。全部经真实请求、引擎与 SQLite。"""

from __future__ import annotations

import logging

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    MAIN_TOOL,
    action_identity,
    advance_until_ready,
    context,
    do_at,
    report_request,
    sleep,
    use_app,
)
from tests.life.environment_runtime import act, act_group, events, every_five_minutes
from worldmock.foundation.errors import ProtocolError

#: 动作身份不由本决策形成时的协议故障原文。
_FORM = "^Action identity must have the form <decision_id>:action:<four digits>$"


@pytest.mark.parametrize("written", [
    "action-plain", "{d}:action:1", "{d}:action:00001", "{d}:action:00a1", "{d}:action:０００１",
    "{d}x:action:0001", "{other}:action:0001",
], ids=["plain", "one-digit", "five-digits", "letter", "full-width-digits", "longer-decision", "other-decision"])
def test_an_action_identity_not_formed_from_its_decision_is_refused_before_any_event(engine_factory, caplog, written):
    """动作身份不是「本决策身份:action:四位 ASCII 数字」：登记以协议故障拒绝，不写事件、不进入本轮，并写英文错误日志；
    同一决策随后用形式正确的身份登记照常受理，回执带这个身份。"""
    engine = engine_factory()
    decision = context(engine, "formed")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    sequence = engine.context.store.last_event_seq
    identity = written.format(d=decision.decision_id, other=context(engine, "other").decision_id)
    caplog.set_level(logging.ERROR)
    with pytest.raises(ProtocolError, match=_FORM):
        engine.submit_action(DirectToolCall(decision, identity, MAIN_TOOL, do_at("家", "测试休息")))
    assert engine.context.store.last_event_seq == sequence
    assert engine.decisions.round.pending_actions == []
    assert "is not formed from decision" in caplog.text
    formed = f"{decision.decision_id}:action:0001"
    receipt = engine.submit_action(DirectToolCall(decision, formed, MAIN_TOOL, do_at("家", "测试休息")))
    assert receipt.action_id == formed


def test_a_settled_action_identity_cannot_be_submitted_in_a_later_round(engine_factory):
    """已结算的动作身份属于它的决策轮：之后的一轮用同一身份、同样的参数登记，不是重放而是协议故障；不写请求事件，同刻轮数
    与活动不变，这一轮随后照常中止。"""
    engine = engine_factory()
    _, first = act(engine, "first", do_at("家", "测试休息"))
    assert first.status == "in_progress", first.error_code
    assert advance_until_ready(engine)
    decision = context(engine, "second")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    requested = len(events(engine, "action_requested"))
    rounds = engine.context.state.agents[1].same_moment_rounds
    activities = engine.context.state.model_dump(mode="json")["activities"]
    with pytest.raises(ProtocolError, match=_FORM):
        engine.submit_action(DirectToolCall(decision, action_identity("first"), MAIN_TOOL, do_at("家", "测试休息")))
    assert len(events(engine, "action_requested")) == requested
    assert engine.context.state.agents[1].same_moment_rounds == rounds
    assert engine.context.state.model_dump(mode="json")["activities"] == activities
    engine.abort_decision_round("second", "expected refusal")
    assert engine.decisions.round is None


def test_every_action_request_records_reused_false_and_no_result_carries_it(engine_factory):
    """单条开始、整组被拒与整组受理的开始组、控制请求：每条 action_requested 的 data.reused 都是 false，受理、拒绝与结果
    事件都不带 reused 键。"""
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
    assert len(requested) == 5 and all(row.data["reused"] is False for row in requested)
    assert not [row for row in events(engine) if row.event_type in {"action_accepted", "action_rejected",
                                                                    "action_result"} and "reused" in row.data]
