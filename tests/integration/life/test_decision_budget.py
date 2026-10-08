"""跨决策预算（docs/design/06-agent.md 第五章「失败后强制等待与跨决策预算」，裁决 #25）。

同刻上限之后至多三个受限轮；受限轮用完仍没有进行中活动（或仍有待收尾活动）时，环境不替人物造活动，写一条只供系统的
decision_budget_exhausted 事件（带最近的拒绝码），把下一次决策排在一个结算间隔之后，时钟可以推进（技术停顿，身体按静息
结算），质量报告计数，不记成人物主动等待，也不写 run_failed。
"""

from __future__ import annotations

import json

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 pause_request, stage_day_plan, start_activity, wake_by_mail)
from tests.integration.life.test_same_moment_decisions import (_act, _content, _finish, _other_requests,
                                                   _start)
from worldmock.footprints.quality import build_quality_report
from tests.integration.life.test_same_moment_decisions import _contacts
from worldmock.life import same_moment
from worldmock.life.models import ActivityStatus, AgentState, SameMomentStage, active_activities

_KIND = "decision_budget_exhausted"
_RESTRICTED = "SAME_MOMENT_ACTION_RESTRICTED"


def _exhausted(engine, agent_id: int = 1) -> list:
    """@brief 人物的 decision_budget_exhausted 事件（按写入顺序）。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", agent_id)
            if event.data.get("kind") == _KIND]


def _events(engine) -> list:
    """@brief 运行库中已提交的全部事件。"""
    return engine.context.store.read_events(engine.context.store.last_event_seq)


def _invalid_start(engine, round_id):
    """@brief 以当前动作方式点名不存在的对象，真实登记与结算名称拒绝。"""
    return _act(engine, round_id, "start_activity", start_activity("进行", "不存在的对象"))


def test_restricted_rounds_used_up_without_an_activity_pause_technically(engine_factory) -> None:
    """同一秒上限加三轮都被拒且没有进行中活动：最后一轮之后写 decision_budget_exhausted（system，带最近的拒绝码与下一次
    决策时刻），不造等待或任何活动，下一次决策排在一个结算间隔之后；时钟推进，身体按静息结算（没有活动的共同身体结算），
    推进后同刻轮数与阶段复位、边界兜底给出 no_active_activity；没有 run_failed，质量报告计一次，进度层不出现这次停顿。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    now, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    feedback = engine.context.scenario.activity_feedback_seconds
    for round_number in range(1, limit + 3):
        assert _invalid_start(engine, f"budget-{round_number}").error_code == "ACTION_REQUEST_INVALID"
        agent = engine.context.state.agents[1]
        assert (agent.same_moment_rounds, agent.next_decision_at) == (round_number, now)
        assert not _exhausted(engine)
    last = _invalid_start(engine, "budget-last")
    agent = engine.context.state.agents[1]
    assert agent.same_moment_rounds == limit + 3 and last.data["same_moment_round"] == limit + 3
    assert (agent.next_decision_at, agent.pending_wake_reasons) == (now + feedback, [])
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
    (event,) = _exhausted(engine)
    assert (event.occurred_at, event.visibility, event.source_kind) == (now, "system", "environment")
    assert event.data == {"kind": _KIND, "error_code": "ACTION_REQUEST_INVALID", "next_decision_at": now + feedback}
    result = engine.context.store.read_agent_events("action_result", 1)[-1]
    assert result.action_id == "action-budget-last-1" and result.event_seq < event.event_seq
    assert not [item for item in engine.context.state.activities.values() if item.agent_id == 1]
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)
    moment = engine.context.state.current_time
    assert now < moment <= now + feedback
    settlement = [item for item in engine.context.store.read_agent_events("environment_event", 1)
                  if item.data.get("kind") == "body_settlement"][-1]
    assert (settlement.data["interval_start"], settlement.data["interval_end"]) == (now, moment)
    assert settlement.data["activity_ids"] == []
    agent = engine.context.state.agents[1]
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (0, SameMomentStage.OPEN)
    assert engine.scheduling_state().ready_agent_ids == (1,) and "no_active_activity" in agent.pending_wake_reasons
    assert same_moment.latest_rejection_code(engine.context.state, 1) is None
    events = _events(engine)
    assert not any(item.event_type == "run_failed" for item in events)
    assert build_quality_report(events, [], engine.context.store.manifest)["decision_rhythm"][
        "decision_budget_exhausted_events"] == 1
    observed = engine.observe(context(engine, "budget-after"))
    assert event.event_id not in observed["progress_event_ids"]
    assert _KIND not in json.dumps(observed["progress_layer"], ensure_ascii=False)
    assert not [item for item in engine.context.state.activities.values() if item.agent_id == 1]


def test_a_successful_last_restricted_round_carries_the_latest_rejection_code(engine_factory) -> None:
    """等待到点待收尾、观看已暂停：同一秒做别的被拒到上限（ACTIVITY_CONCLUSION_REQUIRED），收尾阶段里越界两次，最后一个
    受限轮收尾（受理）后仍没有进行中活动，预算耗尽；事件带本秒最近一次的拒绝码（SAME_MOMENT_ACTION_RESTRICTED），暂停的
    观看仍暂停，环境不替人物恢复或开始任何活动。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    wait_id, watch_id = _start(engine, "latest-start", ("自己", "等待"), _content(engine, "latest"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert _act(engine, "latest-pause", "pause_activity", pause_request(
        activity_name(engine, watch_id), summary="先停一下。")).status == "completed"
    _finish(engine, wait_id, "latest")
    now, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    feedback = engine.context.scenario.activity_feedback_seconds
    codes = [_act(engine, f"latest-other-{index}", *_other_requests()[index % 2]).error_code
             for index in range(limit + 2)]
    assert codes == ["ACTIVITY_CONCLUSION_REQUIRED"] * limit + [_RESTRICTED] * 2
    assert not _exhausted(engine)
    ended = _act(engine, "latest-conclude", "conclude_activity", conclude_request(activity_name(engine, wait_id)))
    assert ended.status == "completed" and engine.context.state.agents[1].same_moment_rounds == limit + 3
    (event,) = _exhausted(engine)
    assert event.data == {"kind": _KIND, "error_code": _RESTRICTED, "next_decision_at": now + feedback}
    assert engine.context.state.agents[1].next_decision_at == now + feedback
    assert engine.context.state.activities[watch_id].status == ActivityStatus.PAUSED
    assert not active_activities(engine.context.state, 1)
    advance_next(engine)
    assert engine.context.state.current_time > now
    assert engine.context.state.activities[watch_id].status == ActivityStatus.PAUSED


def test_a_replayed_rejection_in_the_last_restricted_round_exhausts_the_budget(engine_factory) -> None:
    """重放计入同刻轮数：最后一个受限轮是一次被拒动作的幂等重放（同一动作身份、新的决策），按存档结果排期，预算耗尽，
    事件随重放的证据一起提交，带存档的拒绝码。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    now, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    request = start_activity("进行", "不存在的对象")
    for round_number in range(1, limit + 3):
        assert _act(engine, f"replay-{round_number}", "start_activity", request).status == "rejected"
    decision = context(engine, "replay-again")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "action-replay-1-1", "start_activity", request))
    replayed = engine.commit_decision_round("replay-again")[0]
    assert replayed.status == "rejected" and replayed.error_code == "ACTION_REQUEST_INVALID"
    assert engine.context.state.agents[1].same_moment_rounds == limit + 3
    (event,) = _exhausted(engine)
    assert event.data == {"kind": _KIND, "error_code": "ACTION_REQUEST_INVALID",
                          "next_decision_at": now + engine.context.scenario.activity_feedback_seconds}
    assert [item.event_type for item in _events(engine)[-3:]] == ["action_rejected", "action_result",
                                                                  "environment_event"]


def test_budget_exhausted_with_a_pending_conclusion_beside_a_running_activity(engine_factory) -> None:
    """等待到点待收尾、观看仍在进行：人物一直做别的，上限加三轮之后仍有待收尾活动，预算同样耗尽（同一秒的轮数不超过上限
    加三）；观看继续进行，待收尾活动保留到下一次决策，时钟推进，没有 run_failed。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    wait_id, watch_id = _start(engine, "beside-start", ("自己", "等待"), _content(engine, "beside"))
    _finish(engine, wait_id, "beside")
    ended, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    codes = [_act(engine, f"beside-other-{index}", *_other_requests()[index % 2]).error_code
             for index in range(limit + 3)]
    assert codes == ["ACTIVITY_CONCLUSION_REQUIRED"] * limit + [_RESTRICTED] * 3
    (event,) = _exhausted(engine)
    assert event.data["error_code"] == _RESTRICTED
    assert engine.context.state.agents[1].next_decision_at == ended + engine.context.scenario.activity_feedback_seconds
    assert engine.context.state.activities[watch_id].status == ActivityStatus.ACTIVE
    assert engine.context.state.activities[wait_id].concluded_at is None
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)
    assert engine.context.state.current_time > ended
    assert not any(item.event_type == "run_failed" for item in _events(engine))


def test_budget_exhaustion_units() -> None:
    """预算判定与排期：本秒轮数达到上限加三且没有进行中活动或仍有待收尾时耗尽；有进行中活动且没有待收尾、或轮数未到时不耗尽；
    耗尽的排期是 now + 结算间隔、原因清空、阶段照阶梯，事件只供系统；本秒没有拒绝时拒绝码为 None。"""
    def outcome(rounds: int, **flags) -> same_moment.RoundOutcome:
        """@brief 同刻上限 3 的一轮结算结果。"""
        agent = AgentState.model_construct(agent_id=1, same_moment_rounds=rounds,
                                           same_moment_stage=SameMomentStage.START_OR_RESUME)
        return same_moment.RoundOutcome(now=100, agent=agent, active=flags.get("active", False),
                                        rejected=True, limit=3, pending=flags.get("pending", False))

    assert same_moment.RESTRICTED_ROUNDS == 3
    assert same_moment.budget_exhausted(outcome(6))
    assert same_moment.budget_exhausted(outcome(7, active=True, pending=True))
    assert not same_moment.budget_exhausted(outcome(5))
    assert not same_moment.budget_exhausted(outcome(6, active=True))
    schedule, draft = same_moment.exhausted_schedule(outcome(6), 900, None)
    assert schedule == (1000, [], SameMomentStage.START_OR_RESUME)
    assert (draft.event_type, draft.occurred_at, draft.agent_id, draft.visibility, draft.source_kind) == (
        "environment_event", 100, 1, "system", "environment")
    assert draft.data == {"kind": _KIND, "error_code": None, "next_decision_at": 1000}
