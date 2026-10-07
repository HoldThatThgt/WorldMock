"""同刻决策（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章）：决策不耗时，同一秒再决策、同刻上限与受限轮阶梯。

第一章规则 3 的收尾一支（conclude_pending）依赖第七章的待收尾：活动一结束就要当秒收尾，收尾前时钟不推进；暂停的活动
不占名额，两项都暂停时同一秒再决策，到达上限后与没有活动时一样进入开始或恢复（resume_or_conclude 与 conclude_only 留在
阶段闭集中，现行阶梯不再进入）。
"""

from __future__ import annotations

import dataclasses
from tests.data_paths import ENVIRONMENT_ROOT

import json
from collections import Counter
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall
from pydantic import ValidationError

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_pending, conclude_request,
                                 context, pending_names, progress_calls, settle_day_plans, stage_day_plan,
                                 start_activity, submit_round, target_actions, target_tools, wake_by_mail,
                                 start_request, pause_request, report_request)
from tests.life.scenario_fixtures import create_engine, read_scenario, write_scenario
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import same_moment
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.body import apply_mood_pulse
from worldmock.life.engine import LifeEngine
from worldmock.life.vocabulary import WAYS
from worldmock.life.models import (ActivityKind, ActivityState, ActivityStatus, AgentState, PlanGoalInput,
                                   ReviseDayPlanRequest, SameMomentStage, SendMessageActionRequest, active_activities)
from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS, Message
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs, next_decision_candidates
from worldmock.life.scenario import LifeScenario
from worldmock.life.world_setup import load_scenario
from worldmock.life.schedule import WakeSnapshot, evaluate_wake_reasons

_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"
_RESTRICTED = "SAME_MOMENT_ACTION_RESTRICTED"
_RESTRICTED_REASON = "This same-moment stage only accepts its offered action types."


def _contacts(document):
    """@brief 当前测试明确声明可通话母亲和模型回复老友，不依赖人工环境默认通讯录。"""
    document["agents"][0]["contacts"] = [
        {"contact_id": "mother", "salutation": "母亲", "relation": "母亲", "closeness": 24,
         "channels": ["phone"], "reply_policy": "none"},
        {"contact_id": "friend", "salutation": "好友", "relation": "朋友", "closeness": 16,
         "channels": ["phone"], "reply_policy": "model"}]


def _act(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 单人物按真实顺序观察、登记并结算一轮，返回该人物结果。"""
    _, results = submit_round(engine, {agent_id: (tool, request)}, name)
    return next(item for item in results if item.agent_id == agent_id)


def _wait(engine: LifeEngine, name: str, agent_id: int = 1):
    """@brief 以可读对象「自己」开始一段等待。"""
    return _act(engine, name, "start_activity", start_activity("等待", "自己"), agent_id)


def _invalid_start(engine: LifeEngine, name: str, agent_id: int = 1):
    """@brief 在家或办公室尝试现场购买面馆商品，得到稳定的业务拒绝 ACTIVITY_PRECONDITION_UNMET。"""
    return _act(engine, name, "start_activity", start_activity("购买", "牛肉面"), agent_id)


def _revise(engine: LifeEngine, name: str):
    """@brief 修订当日目标：一次成功的瞬时动作。"""
    return _act(engine, name, "revise_day_plan", ReviseDayPlanRequest(
        goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None),]))


def _stage_agent(engine: LifeEngine, agent_id: int = 1, **updates) -> None:
    """@brief 改造人物状态并经存储落盘（模拟绕过宿主本地校验的请求所在的轮次状态）。"""
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[agent_id] = staged.agents[agent_id].model_copy(update=updates, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_same_moment_staged"})])
    engine.context.state = staged


def _decision_reasons(engine: LifeEngine, agent_id: int = 1) -> list[list[str]]:
    """@brief 按写入顺序读取人物每轮 decision_started 的唤醒原因。"""
    return [event.data["wake_reasons"] for event in engine.context.store.read_agent_events("decision_started", agent_id)]


def _reach_limit(engine: LifeEngine) -> None:
    """@brief 在同一秒连续业务拒绝直到达到同刻上限，进入受限轮 start_or_resume。"""
    limit = engine.context.scenario.max_same_moment_decisions
    while engine.context.state.agents[1].same_moment_rounds < limit:
        _invalid_start(engine, f"reach-limit-{engine.context.state.agents[1].same_moment_rounds}")
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME


def _finish(engine: LifeEngine, activity_id: str, name: str) -> None:
    """@brief 经真实边界推进到活动结束（结束后待收尾）；中途的决策机会以播报继续。"""
    for step in range(64):
        if engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"{name}-continue-{step}", "report_status", report_request(
                activity_name(engine, activity_id), summary="继续。"))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def _rounds_at(engine: LifeEngine, moment: int, agent_id: int = 1) -> int:
    """@brief 人物在某一模拟秒内写下的决策轮数（decision_started 条数）。"""
    return [event.occurred_at for event in engine.context.store.read_agent_events("decision_started", agent_id)].count(moment)


def test_run_start_and_instant_action_redecide_in_same_second(engine_factory) -> None:
    """运行开始与瞬时动作之后同一秒再决策；第二轮只记 no_active_activity（规则 2、6）。启动路径的零点计划不是决策，
    之后的首个决策仍带 run_start（第四章「运行时机」）。"""
    engine = engine_factory(mutate=_contacts)
    start = engine.context.state.current_time
    settle_day_plans(engine)
    assert engine.context.state.agents[1].pending_wake_reasons == ["run_start"]
    assert engine.context.state.agents[1].next_decision_at == start
    revised = _revise(engine, "run-start-revise")
    assert revised.status == "completed"
    agent = engine.context.state.agents[1]
    assert engine.context.state.current_time == start and agent.next_decision_at == start
    assert agent.same_moment_rounds == 1 and agent.pending_wake_reasons == ["no_active_activity"]
    assert engine.scheduling_state().ready_agent_ids == (1,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    waited = _wait(engine, "run-start-wait")
    assert waited.status == "in_progress"
    assert _decision_reasons(engine) == [["run_start"], ["no_active_activity"]]
    assert engine.context.state.agents[1].next_decision_at > start
    advance_next(engine)
    assert engine.context.state.current_time > start


def test_activity_end_boundary_offers_no_active_activity_and_redecides(engine_factory) -> None:
    """活动结束的边界兜底 no_active_activity 写入共同身体结算；当秒先给刚结束的活动收尾，之后的瞬时动作同秒再决策
    （规则 2、5；第七章「活动一结束就要结束它」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "end-wait")
    _finish(engine, waited.activity_id, "end-wait")
    end = engine.context.state.current_time
    settlement = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["interval_end"] == end
    assert {"planned_end", "no_active_activity"} <= set(settlement.data["wake_reasons"])
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert conclude_pending(engine, "end-conclude").status == "completed"
    assert engine.context.state.activities[waited.activity_id].concluded_at == end
    assert engine.context.state.agents[1].next_decision_at == end
    revised = _revise(engine, "end-revise")
    assert revised.status == "completed"
    assert engine.context.state.agents[1].next_decision_at == end
    _wait(engine, "end-rest")
    assert engine.context.state.current_time == end
    reasons = _decision_reasons(engine)
    assert {"planned_end", "no_active_activity"} <= set(reasons[-3])
    assert reasons[-2] == reasons[-1] == ["no_active_activity"]
    # 同刻轮次没有结算区间：该秒只有边界那一条共同身体结算，再决策轮的原因只记于 decision_started。
    assert [event.data["interval_end"] for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") == "body_settlement"].count(end) == 1


def test_business_rejection_redecides_in_same_second_with_rejection_feedback(engine_factory) -> None:
    """业务拒绝后同一秒再决策，原因按枚举顺序；结果数据用同刻轮次三键（规则 2、6，实现约定）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    now = engine.context.state.current_time
    rejected = _invalid_start(engine, "reject-once")
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert rejected.data["same_moment_round"] == 1
    assert rejected.data["max_same_moment_decisions"] == engine.context.scenario.max_same_moment_decisions
    assert rejected.data["next_decision_at"] == now
    assert "consecutive_rejections" not in rejected.data and "maximum_rejections" not in rejected.data
    agent = engine.context.state.agents[1]
    assert agent.pending_wake_reasons == ["rejection_feedback", "no_active_activity"]
    assert agent.next_decision_at == now
    assert _wait(engine, "reject-then-wait").status == "in_progress"
    assert _decision_reasons(engine)[-1] == ["rejection_feedback", "no_active_activity"]
    assert not hasattr(engine.context.state.agents[1], "consecutive_rejections")


def test_rejection_with_active_activity_retries_until_limit_then_follows_rhythm(engine_factory) -> None:
    """有进行中活动时业务拒绝在同一秒再给机会，达到上限后按决策节律，不进受限轮（规则 4）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "active-wait")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    now = engine.context.state.current_time
    assert engine.context.state.activities[waited.activity_id].status == ActivityStatus.ACTIVE
    limit = engine.context.scenario.max_same_moment_decisions
    for index in range(1, limit + 1):
        result = _invalid_start(engine, f"active-reject-{index}")
        agent = engine.context.state.agents[1]
        assert result.data["same_moment_round"] == index
        if index < limit:
            assert agent.next_decision_at == now and agent.pending_wake_reasons == ["rejection_feedback"]
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at > now and agent.pending_wake_reasons == []
    assert agent.same_moment_stage == SameMomentStage.OPEN
    advance_next(engine)
    assert engine.context.state.agents[1].same_moment_rounds == 0


def _unavailable_start(observed: dict) -> tuple[str, str, str]:
    """@brief 本轮工具层中一项因对象自身条件不可用的开始动作（不是同刻限制，也不是受限轮不开放的购买）：（对象，方式，原因）。"""
    return next((row["object"], action["label"], action["note"]) for row in observed["interaction_bindings"]
                for action in row["actions"] if action["note"] not in (None, _RESTRICTED_REASON)
                and action["canonical_action"] != "purchase")


def test_rejected_start_stays_in_start_or_resume_and_marks_the_option(engine_factory) -> None:
    """达到上限先只开放开始或恢复；开始被拒后留在原阶段（不再升级为只开放等待），被拒的选项在工具层照原因标不可用；
    之后的等待被受理，同秒轮数不超过上限加三（规则 3，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    now = engine.context.state.current_time
    limit = engine.context.scenario.max_same_moment_decisions
    _reach_limit(engine)
    assert engine.context.state.agents[1].next_decision_at == now
    target, way, reason = _unavailable_start(engine.observe(context(engine, "restricted-preview")))
    rejected_start = _act(engine, "restricted-start", "start_activity", start_activity(way, target))
    assert rejected_start.status == "rejected" and rejected_start.data["reason"] == reason
    agent = engine.context.state.agents[1]
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME and agent.next_decision_at == now
    marked = target_actions(engine.observe(context(engine, "restricted-marked")), target)[way]
    assert (marked["available"], marked["reason"]) == (False, reason)
    waited = _wait(engine, "restricted-wait")
    assert waited.status == "in_progress"
    agent = engine.context.state.agents[1]
    assert agent.same_moment_rounds == limit + 2
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME and agent.next_decision_at > now
    advance_next(engine)
    agent = engine.context.state.agents[1]
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (0, SameMomentStage.OPEN)


def test_out_of_bounds_request_is_rejected_and_counted(engine_factory) -> None:
    """绕过本地校验的越界请求以 SAME_MOMENT_ACTION_RESTRICTED 业务拒绝并计入同刻轮数（实现约定）。"""

    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    limit = engine.context.scenario.max_same_moment_decisions
    observed = engine.observe(context(engine, "bounds-preview"))
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    purchase = next(start_activity(action["label"], row["object"]) for row in observed["interaction_bindings"]
                    for action in row["actions"] if action["canonical_action"] == "purchase")
    cases = [
        (SameMomentStage.START_OR_RESUME, "send_message", SendMessageActionRequest(recipient_name=contact, text="在吗")),
        (SameMomentStage.START_OR_RESUME, "start_activity", purchase),
        (SameMomentStage.CONCLUDE_ONLY, "start_activity", start_activity("睡觉", "自己")),
    ]
    for index, (stage, tool, request) in enumerate(cases):
        _stage_agent(engine, same_moment_rounds=limit, same_moment_stage=stage)
        result = _act(engine, f"bounds-{index}", tool, request)
        assert result.status == "rejected" and result.error_code == _RESTRICTED
        agent = engine.context.state.agents[1]
        assert agent.same_moment_rounds == limit + 1 and result.data["same_moment_round"] == limit + 1
        assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
        assert agent.next_decision_at == engine.context.state.current_time
    assert not active_activities(engine.context.state, 1) and engine.context.state.deliveries == {}


def test_start_or_resume_accepts_start_and_resume_but_not_other_reports(engine_factory) -> None:
    """start_or_resume 接受开始持续活动与恢复暂停活动；点名未暂停活动（已收尾的活动）的播报越界（规则 3）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first = _wait(engine, "resume-first-wait")
    _finish(engine, first.activity_id, "resume-first")
    assert conclude_pending(engine, "resume-first-conclude").status == "completed"
    _stage_agent(engine, same_moment_rounds=engine.context.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    ended = _act(engine, "resume-ended-report", "report_status", report_request(
        activity_name(engine, first.activity_id), summary="结束了。"))
    assert ended.status == "rejected" and ended.error_code == _RESTRICTED
    second = _wait(engine, "resume-second-wait")
    assert second.status == "in_progress"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused = _act(engine, "resume-pause", "pause_activity", pause_request(
        activity_name(engine, second.activity_id), summary="先停一下。"))
    assert paused.status == "completed" and not active_activities(engine.context.state, 1)
    _stage_agent(engine, same_moment_rounds=engine.context.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    resumed = _act(engine, "resume-report", "report_status", report_request(
        activity_name(engine, second.activity_id), summary="继续等。"))
    assert resumed.status == "completed"
    assert engine.context.state.activities[second.activity_id].status == ActivityStatus.ACTIVE
    agent = engine.context.state.agents[1]
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
    assert agent.next_decision_at > engine.context.state.current_time
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert _act(engine, "resume-conclude", "conclude_activity", conclude_request(
        activity_name(engine, second.activity_id), summary="不等了。")).status == "completed"
    _stage_agent(engine, same_moment_rounds=engine.context.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    watched = _act(engine, "resume-watch", "start_activity", start_activity("进行", "刷推荐", app=["抖音"]))
    assert watched.status == "in_progress"


def test_feedback_interval_60_bounds_same_second_rounds(tmp_path) -> None:
    """结算间隔 60 秒时屡次拒绝也不死循环：每次等待结束先被要求收尾（屡做别的以 ACTIVITY_CONCLUSION_REQUIRED 拒绝），
    按受限轮开放的动作收尾、开始再被拒后留在原阶段，最后一个受限轮等待；每人每秒的决策轮数不超过上限加三，运行不失败
    （规则 3）。"""
    def feedback_60(document: dict) -> None:
        """@brief 等待的计划时长取最小的结算间隔。"""
        document["activity_feedback_seconds"] = 60

    engine = create_engine("single-person.json", feedback_60, tmp_path, "feedback-60")
    try:
        stage_day_plan(engine)
        limit = engine.context.scenario.max_same_moment_decisions
        waits, codes = 0, Counter()
        for step in range(200):
            if waits == 4:
                break
            if not engine.scheduling_state().ready_agent_ids:
                advance_next(engine)
                continue
            agent = engine.context.state.agents[1]
            if agent.same_moment_stage == SameMomentStage.CONCLUDE_PENDING:
                assert conclude_pending(engine, f"f60-conclude-{step}").status == "completed"
            elif agent.same_moment_rounds == limit + 2:
                assert _wait(engine, f"f60-wait-{step}").status == "in_progress"
                waits += 1
            else:
                codes[_invalid_start(engine, f"f60-reject-{step}").error_code] += 1
        assert waits == 4
        # 后三段等待各在结束那一秒先被拒 limit 次（收尾前做别的），之后收尾、开始被拒（留在原阶段）、等待：恰好上限加三轮。
        assert codes["ACTIVITY_CONCLUSION_REQUIRED"] == 3 * limit
        rounds = Counter((event.agent_id, event.occurred_at)
                         for event in engine.context.store.read_agent_events("decision_started", 1))
        assert len(rounds) >= 4 and max(rounds.values()) == limit + 3
        events = engine.context.store.read_events(engine.context.store.last_event_seq)
        assert not any(event.event_type == "run_failed" for event in events)
    finally:
        engine.close()


def test_replay_round_counts_toward_same_moment_rounds(engine_factory) -> None:
    """幂等重放计入同刻轮数、按存档结果同秒再决策，不重做脉冲与连续拒绝计数（规则 9）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    request = start_activity("进行", "办公")
    first = _act(engine, "replayed", "start_activity", request)
    assert first.status == "rejected"
    body = engine.context.state.agents[1].body
    _, results = submit_round(engine, {1: ("start_activity", request)}, "replayed")
    assert results[0] == first
    agent = engine.context.state.agents[1]
    assert agent.same_moment_rounds == 2 and agent.next_decision_at == engine.context.state.current_time
    assert agent.pending_wake_reasons == ["rejection_feedback", "no_active_activity"]
    assert agent.body == body
    replayed = [event for event in engine.context.store.read_agent_events("action_rejected", 1)
                if event.data.get("reused")]
    assert len(replayed) == 1


def _keep_going(engine: LifeEngine, agent_id: int) -> tuple[str, object]:
    """@brief 就绪人物的下一请求：先给刚结束的活动收尾，有进行中活动就播报，否则开始等待。"""
    waiting = pending_conclusions(engine.context.state, agent_id)
    if waiting:
        return "conclude_activity", conclude_request(*(activity_name(engine, item.activity_id) for item in waiting))
    running = active_activities(engine.context.state, agent_id)
    if running:
        return "report_status", report_request(activity_name(engine, running[0].activity_id), summary="继续。")
    return "start_activity", start_activity("等待", "自己")


def test_clock_advances_only_when_everyone_has_an_active_activity(engine_factory) -> None:
    """时钟只在人人都有进行中活动且没有待收尾活动时推进；有人同秒就绪时推进是协议错误（规则 1）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("start_activity", start_activity("进行", "办公"))}, "clock-0")
    assert engine.scheduling_state().ready_agent_ids == (2,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    advances, pending_rounds = 0, 0
    for step in range(1, 60):
        if advances >= 4 and pending_rounds:
            break
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            assert all(active_activities(engine.context.state, agent_id) and not pending_conclusions(engine.context.state, agent_id)
                       for agent_id in engine.context.state.agents)
            advance_next(engine)
            advances += 1
            continue
        if any(pending_conclusions(engine.context.state, agent_id) for agent_id in engine.context.state.agents):
            pending_rounds += 1  # 待收尾的人物同秒就绪：收尾前时钟不推进
            with pytest.raises(ProtocolError, match="decision is due"):
                advance_next(engine)
        submit_round(engine, {agent_id: _keep_going(engine, agent_id) for agent_id in ready}, f"clock-{step}")
    assert advances >= 4 and pending_rounds > 0


def test_report_with_active_activity_does_not_redecide_in_same_second(engine_factory) -> None:
    """有进行中活动时播报成功后不在同一秒再给机会，下一次按决策节律（规则 4）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "report-wait")
    wake_by_mail(engine, offset=300)
    assert advance_until_ready(engine)
    now = engine.context.state.current_time
    reported = _act(engine, "report-continue", "report_status", report_request(
        activity_name(engine, waited.activity_id), summary="还在等。"))
    assert reported.status == "completed"
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at > now and agent.pending_wake_reasons == []
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)


def test_wake_reasons_replaced_per_round_single_bedtime_reflection(engine_factory) -> None:
    """醒来那一秒只有首轮（给睡眠收尾）带 wake_from_sleep，之后的同刻轮次按本轮替换原因（规则 6）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    slept = _act(engine, "reflection-sleep", "start_activity", start_activity("睡觉", "自己"))
    assert slept.status == "in_progress"
    while engine.context.state.activities[slept.activity_id].status == ActivityStatus.ACTIVE:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    woke = engine.context.state.current_time
    assert "wake_from_sleep" in engine.context.state.agents[1].pending_wake_reasons
    assert conclude_pending(engine, "reflection-first").status == "completed"
    assert engine.context.state.activities[slept.activity_id].concluded_at == woke
    assert engine.context.state.agents[1].pending_wake_reasons == ["no_active_activity"]
    _wait(engine, "reflection-second")
    assert engine.context.state.current_time == woke
    reasons = _decision_reasons(engine)
    assert "wake_from_sleep" in reasons[-2]
    assert reasons[-1] == ["no_active_activity"]
    assert sum("wake_from_sleep" in item for item in reasons) == 1


def test_sleep_does_not_wake_for_no_active_activity(engine_factory) -> None:
    """no_active_activity 不进睡眠允许集合；睡眠中的静默边界不提供决策（不变项）。"""
    verdict = evaluate_wake_reasons(WakeSnapshot(now=1000, is_asleep=True, no_active_activity=True))
    assert not verdict.decision_offered and verdict.wake_reasons == []
    awake = evaluate_wake_reasons(WakeSnapshot(now=1000, no_active_activity=True))
    assert [item.value for item in awake.wake_reasons] == ["no_active_activity"]
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    slept = _act(engine, "quiet-sleep", "start_activity", start_activity("睡觉", "自己"))
    advance_next(engine)
    assert engine.context.state.activities[slept.activity_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == ()
    settlement = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["decision_offered"] is False and settlement.data["wake_reasons"] == []


def _short_intervals(document: dict) -> None:
    """@brief 重估间隔短于等待与播报，使下一次决策时刻由重估候选决定。"""
    document["activity_feedback_seconds"] = 3600
    document["reconsideration_interval_seconds"] = 600
    document["idle_reconsideration_interval_seconds"] = 1200
    document["progress_report_interval_seconds"] = 86400


def _daylight_intervals(document):
    """@brief 白天与低娱乐需要使重估候选早于体感跨档，明确隔离待验证的节律。"""
    _short_intervals(document)
    document["start_at"] = "2026-01-05T09:00:00+08:00"
    document["body"]["initial_entertainment_need"] = 0.2


def _idle_intervals(document):
    """@brief 明确无工作主题义务，以验证等待的空闲节律。"""
    _daylight_intervals(document)
    document["agents"][0]["work_content_ids"] = []
    document["agents"][0]["work_schedule"] = None


def test_first_decision_includes_reconsideration_candidate(tmp_path) -> None:
    """首次决策（从未决策过）即含重估候选；重估候选恒为本次决策时刻加间隔（规则 7）。"""
    engine = create_engine("single-person.json", _idle_intervals, tmp_path, "reconsider")
    try:
        stage_day_plan(engine)
        assert engine.context.state.agents[1].last_decision_time is None
        first = engine.context.state.current_time
        waited = _wait(engine, "reconsider-wait")
        planned_end = engine.context.state.activities[waited.activity_id].planned_end_at
        assert planned_end == first + 3600
        assert engine.context.state.agents[1].next_decision_at == first + 1200
        assert advance_until_ready(engine)
        later = engine.context.state.current_time
        assert later == first + 1200
        assert engine.context.state.agents[1].pending_wake_reasons == ["reconsideration_interval"]
        _act(engine, "reconsider-report", "report_status", report_request(
            activity_name(engine, waited.activity_id), summary="接着等。"))
        assert engine.context.state.agents[1].next_decision_at == later + 1200
    finally:
        engine.close()


def test_idle_interval_only_while_waiting_without_commitments(tmp_path) -> None:
    """等待无主题义务使用空闲重估间隔，已分配且未完成主题使用常规间隔。"""
    idle = create_engine("single-person.json", _idle_intervals, tmp_path, "idle-interval")
    assigned = create_engine("single-person.json", _daylight_intervals, tmp_path, "assigned-interval")
    try:
        for engine, interval in ((idle, 1200), (assigned, 600)):
            stage_day_plan(engine)
            start = engine.context.state.current_time
            _wait(engine, f"wait-{interval}")
            assert engine.context.state.agents[1].next_decision_at == start + interval
            state = engine.context.state.model_copy(deep=True)
            state.agents[1].last_decision_time = start - 600
            _, verdicts = apply_decision_rhythm(engine_rhythm_inputs(engine.context, state))
            assert ("reconsideration_interval" in verdicts[1].wake_reasons) == (interval == 600)
    finally:
        idle.close()
        assigned.close()


def _app_intervals(document: dict) -> None:
    """@brief 结算间隔一小时、重估十分钟、播报半小时：应用开始后的节律候选早于应用的计划结束。"""
    document["activity_feedback_seconds"] = 3600
    document["reconsideration_interval_seconds"] = 600
    document["idle_reconsideration_interval_seconds"] = 1200
    document["progress_report_interval_seconds"] = 1800


def test_app_start_follows_decision_rhythm(tmp_path) -> None:
    """打开应用后下一决策按决策节律（规则 7）：重估与播报候选照常，不被应用的计划结束取代。"""
    engine = create_engine("single-person.json", _app_intervals, tmp_path, "game-rhythm")
    try:
        stage_day_plan(engine)
        now = engine.context.state.current_time
        played = _act(engine, "game-rhythm-play", "start_activity",
                      start_activity("进行", "学课程", app=["哔哩哔哩"]))
        agent = engine.context.state.agents[1]
        rhythm = next_decision_candidates(agent.model_copy(update={"last_decision_time": now}),
                                          engine_rhythm_inputs(engine.context, engine.context.state))
        assert played.status == "in_progress" and agent.next_decision_at == rhythm == now + 600
        assert engine.context.state.activities[played.activity_id].planned_end_at == now + 1800
        assert advance_until_ready(engine) and engine.context.state.current_time == now + 600
        assert engine.context.state.agents[1].pending_wake_reasons == ["reconsideration_interval"]
    finally:
        engine.close()


def test_service_refused_pulse_at_most_once_per_second(engine_factory) -> None:
    """同一人物同一模拟秒至多施加一次 service_refused 脉冲；下一秒照常施加（拒绝的情绪代价）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    before = engine.context.state.agents[1].body
    _invalid_start(engine, "pulse-1")
    once = engine.context.state.agents[1].body
    assert once == apply_mood_pulse(before, "service_refused")
    _invalid_start(engine, "pulse-2")
    assert engine.context.state.agents[1].body == once
    _wait(engine, "pulse-wait")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    later = engine.context.state.agents[1].body
    _invalid_start(engine, "pulse-3")
    assert engine.context.state.agents[1].body == apply_mood_pulse(later, "service_refused")
    assert same_moment.refusal_pulse_key("RESOURCE_CONFLICT") == "service_refused"




def test_scenario_with_max_consecutive_rejections_is_rejected(tmp_path) -> None:
    """含旧键 max_consecutive_rejections 的场景按未知字段拒绝（场景参数）。"""
    document = read_scenario("single-person.json")
    assert "max_consecutive_rejections" not in document
    assert LifeScenario.model_validate(document).max_same_moment_decisions == 3
    document["max_consecutive_rejections"] = 10
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LifeScenario.model_validate(document)
    path = write_scenario(document, tmp_path)
    with pytest.raises(IntegrityError, match="Scenario validation failed") as caught:
        load_scenario(path)
    assert "Extra inputs are not permitted" in str(caught.value.__cause__)
    assert "max_consecutive_rejections" in json.loads(path.read_text(encoding="utf-8"))


def test_same_moment_state_survives_reopen(engine_factory, tmp_path) -> None:
    """同刻轮数与受限轮阶段随世界状态保存，重开后在同一秒按原阶段继续（实现约定）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    _reach_limit(engine)
    _invalid_start(engine, "reopen-restricted")
    saved = engine.context.state.agents[1]
    assert (saved.same_moment_rounds, saved.same_moment_stage) == (4, SameMomentStage.START_OR_RESUME)
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(tmp_path / "scenario-0" / "scenarios" / "single-person.json", run_dir)
    try:
        agent = restored.context.state.agents[1]
        assert (agent.same_moment_rounds, agent.same_moment_stage) == (4, SameMomentStage.START_OR_RESUME)
        revised = _revise(restored, "reopen-revise")
        assert revised.status == "rejected" and revised.error_code == _RESTRICTED
        assert _wait(restored, "reopen-wait").status == "in_progress"
        assert restored.context.state.agents[1].same_moment_rounds == 6
    finally:
        restored.close()


def test_restricted_stage_check_and_ladder_units() -> None:
    """受限轮复核与阶梯的逐阶段口径：open 全开，start_or_resume 开始/恢复/等待；resume_or_conclude 与 conclude_only 留在
    闭集中、复核口径不变（阶梯不再进入它们）；wait_only 已从阶段闭集删除（规则 3，
    docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章）。"""
    from worldmock.errors import BusinessRuleError

    assert [stage.value for stage in SameMomentStage] == [
        "open", "conclude_pending", "start_or_resume", "resume_or_conclude", "conclude_only"]
    with pytest.raises(ValueError):
        SameMomentStage("wait_only")
    for kind in ("start", "resume", "wait", "other"):
        same_moment.require_stage_action(SameMomentStage.OPEN, kind)
    for kind in ("start", "resume", "wait"):
        same_moment.require_stage_action(SameMomentStage.START_OR_RESUME, kind)
    for stage, kind in ((SameMomentStage.START_OR_RESUME, "other"), (SameMomentStage.RESUME_OR_CONCLUDE, "start"),
                        (SameMomentStage.RESUME_OR_CONCLUDE, "wait"), (SameMomentStage.CONCLUDE_ONLY, "wait")):
        with pytest.raises(BusinessRuleError, match=_RESTRICTED):
            same_moment.require_stage_action(stage, kind)
    assert same_moment.round_wake_reasons(active=True, rejected=False) == []
    assert same_moment.round_wake_reasons(active=False, rejected=True) == [
        "rejection_feedback", "no_active_activity"]


def test_restricted_observation_marks_closed_actions_unavailable(engine_factory) -> None:
    """受限轮的观察私有证据带 same_moment_stage；受限只由工具层的 available 与 reason 表达：本阶段不开放的动作标不可用，
    原因是同刻限制的原文（不写次数），开放的等待照常可用（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    opened = engine.observe(context(engine, "note-open"))
    contact = next(row["object"] for row in opened["interaction_bindings"] if row["kind"] == "contact")
    assert opened["same_moment_stage"] == "open"
    assert target_tools(opened, contact)["send_message"] == {"tool": "send_message", "available": True, "reason": None}
    _reach_limit(engine)
    restricted = engine.observe(context(engine, "note-restricted"))
    assert restricted["same_moment_stage"] == "start_or_resume"
    closed = target_tools(restricted, contact)["send_message"]
    assert closed["available"] is False and closed["reason"] == _RESTRICTED_REASON
    assert not any(char.isdigit() for char in closed["reason"])
    assert target_actions(restricted, "自己")["等待"]["available"] is True
    assert "same_moment_stage" not in json.dumps(restricted["tool_layer"], ensure_ascii=False)


def test_start_or_resume_schema_matches_engine_dispatch(engine_factory) -> None:
    """同一受限轮观察下工具层照常列出购买并标不可用（原因为同刻限制），提交同一购买由引擎以 SAME_MOMENT_ACTION_RESTRICTED
    拒绝，拒绝原因与 reason 一字不差（E5-3，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」可用性单一来源）。"""

    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    opened = engine.observe(context(engine, "schema-open"))
    target, label = next((row["object"], action["label"]) for row in opened["interaction_bindings"]
                         for action in row["actions"]
                         if action["canonical_action"] == "purchase" and action["note"] is None)
    assert target_actions(opened, target)[label]["available"] is True
    _reach_limit(engine)
    decision = context(engine, "schema-restricted")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    listed = target_actions(observed, target)[label]
    assert (listed["available"], listed["reason"]) == (False, _RESTRICTED_REASON)
    assert target_actions(observed, "自己")["等待"]["available"] is True
    engine.submit_action(DirectToolCall(decision, "action-schema-bypass", "start_activity",
                                        start_activity(label, target)))
    result = engine.commit_decision_round("schema-restricted")[0]
    assert result.status == "rejected" and result.error_code == _RESTRICTED
    assert result.data["reason"] == listed["reason"]
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME


def _pick(observed: dict, canonical: str, *, kind: str | None = None, place: str | None = None) -> tuple[str, str]:
    """@brief 从本轮冻结观察取一个对象名与动作短语（出行取步行）。"""
    return next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                if (kind is None or row["kind"] == kind) and (place is None or row["object_id"] == place)
                for action in row["actions"] if action["canonical_action"] == canonical
                and action.get("travel_mode") in (None, "walking"))


def _start(engine: LifeEngine, name: str, *choices: tuple[str, str]) -> list[str]:
    """@brief 以可读对象与动作短语开始一到两项活动，返回开始的活动身份（按请求顺序）。"""
    started = _act(engine, name, "start_activity", start_request(
        targets=[item[0] for item in choices], actions=[WAYS[item[1]][0] for item in choices],
        ways=[item[1] for item in choices], app=["哔哩哔哩" if item[0] == "学课程" else None for item in choices]))
    assert started.status == "in_progress", started.error_code
    return [started.activity_id] if started.activity_id else [row["activity_id"] for row in started.data["activities"]]


def _depart(engine: LifeEngine, name: str, *companions: tuple[str, str]) -> list[str]:
    """@brief 步行去办公室，可在同一请求中一并开始路上的其他活动；返回活动身份（出行在前）。"""
    route = _pick(engine.observe(context(engine, f"{name}-preview")), "travel_to", place="office")
    return _start(engine, name, route, *companions)


def _content(engine: LifeEngine, name: str) -> tuple[str, str]:
    """@brief 本轮可以观看的一项内容（对象名与动作短语）。"""
    return "学课程", "进行"


def _other_requests() -> tuple[tuple[str, object], ...]:
    """@brief 两种「做别的」：开始等待与修订当日目标。"""
    return (("start_activity", start_activity("等待", "自己")),
            ("revise_day_plan", ReviseDayPlanRequest(
                goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None),])))


def test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three(engine_factory) -> None:
    """到达当秒出行与路上的观看都刚结束：做别的屡次以 ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名，收尾前时钟不推进；
    达到上限后只开放收尾（conclude_pending）：工具层把两项活动的 conclude_activity 都标可用、其余标同刻限制，只点名其中
    一项即受理，阶段留在收尾直到没有待收尾活动，再收尾另一项；按受限轮开放的动作走完，同一秒的轮数不超过上限加三
    （第一章规则 1–3，第七章「活动一结束就要结束它」；docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」「列出即可提交」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    travel_id, watch_id = _depart(engine, "pending-depart", _content(engine, "pending-depart"))
    _finish(engine, travel_id, "pending-walk")
    arrived, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    names = [activity_name(engine, travel_id), activity_name(engine, watch_id)]
    assert pending_names(engine.observe(context(engine, "pending-preview"))) == names
    for index in range(1, limit + 1):
        rejected = _act(engine, f"pending-other-{index}", *_other_requests()[index % 2])
        assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
        assert all(name in rejected.message for name in names)
        agent = engine.context.state.agents[1]
        assert (agent.same_moment_rounds, agent.next_decision_at, engine.context.state.current_time) == (
            index, arrived, arrived)
        with pytest.raises(ProtocolError, match="decision is due"):
            advance_next(engine)
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    assert engine.observe(context(engine, "pending-stage"))["same_moment_stage"] == "conclude_pending"
    assert same_moment.stage_refusal(SameMomentStage.CONCLUDE_PENDING, "conclude_pending") is None
    staged = engine.observe(context(engine, "pending-marked"))
    for name in names:
        tools = target_tools(staged, name)
        assert tools["conclude_activity"] == {"tool": "conclude_activity", "available": True, "reason": None}
        assert (tools["report_status"]["available"], tools["report_status"]["reason"]) == (False, _RESTRICTED_REASON)
    partial = _act(engine, "pending-partial", "conclude_activity", conclude_request(names[0]))
    assert partial.status == "completed", (partial.error_code, partial.message)
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    assert [item.activity_id for item in pending_conclusions(engine.context.state, 1)] == [watch_id]
    rest = target_tools(engine.observe(context(engine, "pending-rest-marked")), names[1])["conclude_activity"]
    assert (rest["available"], rest["reason"]) == (True, None)
    assert _act(engine, "pending-rest", "conclude_activity", conclude_request(names[1])).status == "completed"
    assert all(engine.context.state.activities[key].concluded_at == arrived for key in (travel_id, watch_id))
    agent = engine.context.state.agents[1]
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME and agent.next_decision_at == arrived
    assert _wait(engine, "pending-wait").status == "in_progress"
    assert engine.context.state.agents[1].same_moment_rounds == _rounds_at(engine, arrived) == limit + 3
    advance_next(engine)
    agent = engine.context.state.agents[1]
    assert engine.context.state.current_time > arrived
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (0, SameMomentStage.OPEN)


def test_conclude_pending_stage_accepts_only_concluding_every_pending_activity(engine_factory) -> None:
    """conclude_pending 只接受点名待收尾活动的 conclude_activity：开始、播报与修订计划都以
    SAME_MOMENT_ACTION_RESTRICTED 拒绝（受限轮复核先于收尾要求），计入同刻轮数，阶段不变，时钟不推进（第一章规则 3）；
    收尾用掉最后一个受限轮后仍没有进行中活动，按跨决策预算技术停顿（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "only-conclude-wait")
    _finish(engine, waited.activity_id, "only-conclude")
    now, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    name = activity_name(engine, waited.activity_id)
    _stage_agent(engine, same_moment_rounds=limit - 1, same_moment_stage=SameMomentStage.CONCLUDE_PENDING)
    requests = (*_other_requests(), ("report_status", report_request(name, summary="等完了。")))
    for index, (tool, request) in enumerate(requests):
        result = _act(engine, f"only-conclude-{index}", tool, request)
        assert result.status == "rejected" and result.error_code == _RESTRICTED
        agent = engine.context.state.agents[1]
        assert agent.same_moment_rounds == limit + index
        assert agent.same_moment_stage == SameMomentStage.CONCLUDE_PENDING
        assert agent.next_decision_at == now == engine.context.state.current_time
    assert engine.context.state.activities[waited.activity_id].concluded_at is None
    assert _act(engine, "only-conclude-all", "conclude_activity", conclude_request(name)).status == "completed"
    agent = engine.context.state.agents[1]
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
    assert agent.next_decision_at == now + engine.context.scenario.activity_feedback_seconds


def test_pending_conclusion_beside_a_running_activity_holds_the_clock_past_the_limit(engine_factory) -> None:
    """同一请求开始等待与观看，等待先到点待收尾、观看仍在进行：做别的屡次以 ACTIVITY_CONCLUSION_REQUIRED 拒绝，
    达到同刻上限的那次拒绝也不按决策节律排期（虽有进行中活动），人物仍在当秒就绪、推进被拒，阶段转入 conclude_pending；
    收尾后只剩进行中的观看，按决策节律排期，时钟随即推进（第一章规则 2、3，第七章「活动一结束就要结束它」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    wait_id, watch_id = _start(engine, "beside-start", ("自己", "等待"), _content(engine, "beside"))
    _finish(engine, wait_id, "beside")
    ended, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    assert engine.context.state.activities[watch_id].status == ActivityStatus.ACTIVE
    assert [item.activity_id for item in pending_conclusions(engine.context.state, 1)] == [wait_id]
    name = activity_name(engine, wait_id)
    for index in range(1, limit + 1):
        rejected = _act(engine, f"beside-other-{index}", *_other_requests()[index % 2])
        assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
        assert name in rejected.message
        agent = engine.context.state.agents[1]
        assert (agent.same_moment_rounds, agent.next_decision_at, engine.context.state.current_time) == (index, ended, ended)
        assert engine.scheduling_state().ready_agent_ids == (1,)
        with pytest.raises(ProtocolError, match="decision is due"):
            advance_next(engine)
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    concluded = _act(engine, "beside-conclude", "conclude_activity", conclude_request(name))
    assert concluded.status == "completed" and engine.context.state.activities[wait_id].concluded_at == ended
    assert engine.context.state.activities[watch_id].status == ActivityStatus.ACTIVE
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at > ended and agent.pending_wake_reasons == []
    assert agent.same_moment_rounds == _rounds_at(engine, ended) == limit + 1
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)
    agent = engine.context.state.agents[1]
    assert engine.context.state.current_time > ended
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (0, SameMomentStage.OPEN)


def _two_paused(engine: LifeEngine, name: str) -> tuple[str, str, int]:
    """@brief 同一请求开始等待与观看，一分钟后同时暂停两项：没有进行中活动，两个名额都空着；返回两项身份与当时时刻。"""
    first, second = _start(engine, f"{name}-start", ("自己", "等待"), _content(engine, name))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused = _act(engine, f"{name}-pause", "pause_activity", pause_request(
        activity_name(engine, first), activity_name(engine, second), summary=["先停一下。", "也停。"]))
    assert paused.status == "completed" and not active_activities(engine.context.state, 1)
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at == engine.context.state.current_time
    assert agent.pending_wake_reasons == ["no_active_activity"]
    return first, second, engine.context.state.current_time


@pytest.mark.parametrize("first_move", ["resume", "start"])
def test_both_paused_at_the_limit_open_start_or_resume(engine_factory, first_move) -> None:
    """两项都暂停：没有进行中活动，同一秒再决策；暂停的活动不占名额，达到上限后进入开始或恢复（start_or_resume），不再
    按名额分流进入恢复或结束。恢复其一或开始一项新活动都被受理，有了进行中活动，时钟可推进；同一秒的轮数为上限加一
    （第一章规则 2、3，第七章「两项都暂停时」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, second, now = _two_paused(engine, "ladder")
    limit = engine.context.scenario.max_same_moment_decisions
    _reach_limit(engine)
    assert engine.observe(context(engine, "ladder-stage"))["same_moment_stage"] == "start_or_resume"
    if first_move == "resume":
        moved = _act(engine, "ladder-resume", "report_status", report_request(
            activity_name(engine, second), summary="接着看。"))
        assert moved.status == "completed"
        expected = [ActivityStatus.PAUSED, ActivityStatus.ACTIVE]
    else:
        moved = _wait(engine, "ladder-new-wait")
        assert moved.status == "in_progress"
        assert engine.context.state.activities[moved.activity_id].status == ActivityStatus.ACTIVE
        expected = [ActivityStatus.PAUSED, ActivityStatus.PAUSED]
    assert [engine.context.state.activities[key].status for key in (first, second)] == expected
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at > now and agent.pending_wake_reasons == []
    assert agent.same_moment_rounds == _rounds_at(engine, now) == limit + 1
    advance_next(engine)
    assert engine.context.state.current_time > now


def test_concluding_a_paused_activity_in_start_or_resume_is_restricted(engine_factory) -> None:
    """两项都暂停、达到上限后的 start_or_resume 中结束暂停的活动越界（SAME_MOMENT_ACTION_RESTRICTED），留在 start_or_resume，
    待办层的结束照同一原因标不可用、恢复可用；之后的等待被受理，两项仍暂停，同一秒的轮数为上限加二（第一章规则 3）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, second, now = _two_paused(engine, "narrow")
    limit = engine.context.scenario.max_same_moment_decisions
    _reach_limit(engine)
    outside = _act(engine, "narrow-outside", "conclude_activity", conclude_request(activity_name(engine, first)))
    assert outside.status == "rejected" and outside.error_code == _RESTRICTED
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME
    staged = engine.observe(context(engine, "narrow-stage"))
    assert staged["same_moment_stage"] == "start_or_resume"
    tools = target_tools(staged, activity_name(engine, first))
    assert (tools["conclude_activity"]["available"], tools["conclude_activity"]["reason"]) == (
        False, outside.data["reason"])
    assert tools["report_status"]["available"] is True
    assert _wait(engine, "narrow-wait").status == "in_progress"
    assert {engine.context.state.activities[key].status for key in (first, second)} == {ActivityStatus.PAUSED}
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at > now
    assert agent.same_moment_rounds == _rounds_at(engine, now) == limit + 2
    advance_next(engine)
    assert engine.context.state.current_time > now


def test_rejected_resume_with_both_paused_stays_in_start_or_resume(engine_factory) -> None:
    """两项都暂停（一项在家、一项在办公室），达到上限后进入 start_or_resume；在开始地点以外恢复被拒
    （ACTIVITY_PRECONDITION_UNMET）后留在 start_or_resume，被拒的恢复在待办层照原因标不可用；之后的等待被受理，在别处的
    那项仍暂停，同一秒的轮数为上限加二（第一章规则 3「开始或恢复被拒后留在原阶段」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    (home,) = _start(engine, "away-home-wait", ("自己", "等待"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    home_name = activity_name(engine, home)
    assert _act(engine, "away-pause-home", "pause_activity", pause_request(
        home_name, summary="先出门。")).status == "completed"
    (travel,) = _depart(engine, "away-depart")
    _finish(engine, travel, "away-walk")
    assert conclude_pending(engine, "away-arrive").status == "completed"
    (office,) = _start(engine, "away-office-wait", ("自己", "等待"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    now, limit = engine.context.state.current_time, engine.context.scenario.max_same_moment_decisions
    assert _act(engine, "away-pause-office", "pause_activity", pause_request(
        activity_name(engine, office), summary="停一下。")).status == "completed"
    _reach_limit(engine)
    elsewhere = _act(engine, "away-resume-home", "report_status", report_request(home_name, summary="想接着等。"))
    assert elsewhere.status == "rejected" and elsewhere.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME
    marked = target_tools(engine.observe(context(engine, "away-marked")), home_name)["report_status"]
    assert (marked["available"], marked["reason"]) == (False, elsewhere.data["reason"])
    assert _wait(engine, "away-wait").status == "in_progress"
    assert engine.context.state.activities[home].status == ActivityStatus.PAUSED
    assert engine.context.state.agents[1].same_moment_rounds == _rounds_at(engine, now) == limit + 2
    advance_next(engine)
    assert engine.context.state.current_time > now


def _unit_activity(key: str, status: ActivityStatus, *, concluded: bool = False) -> ActivityState:
    """@brief 受限轮复核用的最小活动；终态在 1900 秒结束，concluded 表示已收尾。"""
    running = status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
    return ActivityState(
        activity_id=key, agent_id=1, location_id="home", kind=ActivityKind.WAIT, status=status, started_at=1000,
        ended_at=None if running else 1900, planned_end_at=1900, last_settled_at=1000 if running else 1900,
        elapsed_seconds=0 if running else 900, planned_duration_seconds=900, origin_action_id=f"action-{key}",
        origin_decision_id="decision", origin_request_event_id="event", concluded_at=1900 if concluded else None)


#: 受限轮各阶段开放的请求类（第一章规则 3）：收尾一支只收点名待收尾活动（全部或其中几项）的结束，开始或恢复一支开放
#: 开始、恢复与等待；resume_or_conclude（恢复或结束）与 conclude_only（只开放结束）留在闭集中，阶梯不再进入，复核口径不变。
_STAGE_OPENS = {
    SameMomentStage.CONCLUDE_PENDING: {"conclude_pending"},
    SameMomentStage.RESUME_OR_CONCLUDE: {"resume", "conclude", "conclude_pending"},
    SameMomentStage.CONCLUDE_ONLY: {"conclude", "conclude_pending"},
    SameMomentStage.START_OR_RESUME: {"start", "resume", "wait"},
}


def test_restricted_conclusions_endings_and_waits_pass_the_stage_check() -> None:
    """受限轮复核的收尾与结束：只点名待收尾活动（全部或其中一项）记 conclude_pending，结束能结束的活动（进行中、暂停中或
    待收尾，含暂停与进行中的活动一起点名）记 conclude；resume_or_conclude 与 conclude_only 都接受二者，conclude_pending 只接受
    前者，所以受限轮的收尾不会被业务拒绝（第一章规则 3；逐项收尾与工具层逐项标注同一判定）。"""
    just_ended, road = _unit_activity("a", ActivityStatus.COMPLETED), _unit_activity("b", ActivityStatus.CANCELLED)
    paused, running = _unit_activity("p", ActivityStatus.PAUSED), _unit_activity("x", ActivityStatus.ACTIVE)
    done, pending, none = _unit_activity("d", ActivityStatus.COMPLETED, concluded=True), frozenset("ab"), frozenset()
    cases = [
        (("conclude_activity", [], [just_ended, road], pending), "conclude_pending"),
        (("conclude_activity", [], [just_ended], pending), "conclude_pending"),
        (("conclude_activity", [], [just_ended, running], pending), "conclude"),
        (("conclude_activity", [], [paused, running], none), "conclude"),
        (("conclude_activity", [], [done], none), "other"),
        (("report_status", [], [paused], none), "resume"),
        (("report_status", [], [paused, running], none), "other"),
        (("start_activity", ["wait"], [], none), "wait"),
        (("start_activity", ["wait", "do"], [], none), "start"),
        (("start_activity", ["purchase"], [], none), "other"),
        (("send_message", [], [], none), "other"),
    ]
    for arguments, expected in cases:
        assert same_moment.request_kind(*arguments) == expected
    for stage, opened in _STAGE_OPENS.items():
        for arguments, kind in cases:
            if kind in opened:
                same_moment.require_stage_action(stage, same_moment.request_kind(*arguments))
            else:
                with pytest.raises(BusinessRuleError, match=_RESTRICTED):
                    same_moment.require_stage_action(stage, same_moment.request_kind(*arguments))
                refusal = same_moment.stage_refusal(stage, same_moment.request_kind(*arguments))
                assert (refusal.code, refusal.reason) == (_RESTRICTED, _RESTRICTED_REASON)


def test_conclusion_branch_and_start_or_resume_of_the_ladder() -> None:
    """受限轮阶梯（第一章规则 3）：有待收尾即从收尾一支开始（含交谈对方在同一秒结束造成的新待收尾）；没有进行中与待收尾的
    活动时一律开始或恢复，不再按名额分流，开始或恢复被拒后留在这一阶段；手工写入的 resume_or_conclude 与 conclude_only
    也转入开始或恢复；有进行中活动时阶段不变；有待收尾时同一秒再决策，不按决策节律。轮次结果不再带名额字段，任何
    没有进行中活动的轮次都不会进入 resume_or_conclude 或 conclude_only。"""
    open_, pending_stage = SameMomentStage.OPEN, SameMomentStage.CONCLUDE_PENDING
    resume_or_conclude, start_or_resume = SameMomentStage.RESUME_OR_CONCLUDE, SameMomentStage.START_OR_RESUME
    ladder = [
        (open_, 2, {"pending": True}, open_),
        (open_, 3, {"pending": True}, pending_stage),
        (start_or_resume, 5, {"pending": True, "active": True}, pending_stage),
        (pending_stage, 4, {}, start_or_resume),
        (open_, 3, {}, start_or_resume),
        (start_or_resume, 4, {}, start_or_resume),
        (resume_or_conclude, 4, {}, start_or_resume),
        (SameMomentStage.CONCLUDE_ONLY, 5, {}, start_or_resume),
        (resume_or_conclude, 4, {"active": True}, resume_or_conclude),
        (start_or_resume, 4, {"active": True}, start_or_resume),
    ]
    for stage, rounds, flags, expected in ladder:
        agent = AgentState.model_construct(same_moment_rounds=rounds, same_moment_stage=stage)
        outcome = same_moment.RoundOutcome(now=100, agent=agent, active=flags.get("active", False), rejected=False,
                                           limit=3, pending=flags.get("pending", False))
        assert same_moment.next_stage(outcome) == expected, (stage, rounds, flags)
    assert "slots_full" not in {field.name for field in dataclasses.fields(same_moment.RoundOutcome)}
    dead = {resume_or_conclude, SameMomentStage.CONCLUDE_ONLY}
    for stage in SameMomentStage:
        for rounds in range(6):
            for pending in (False, True):
                agent = AgentState.model_construct(same_moment_rounds=rounds, same_moment_stage=stage)
                outcome = same_moment.RoundOutcome(now=100, agent=agent, active=False, rejected=False, limit=3,
                                                   pending=pending)
                assert same_moment.next_stage(outcome) not in dead, (stage, rounds, pending)
    agent = AgentState.model_construct(same_moment_rounds=1, same_moment_stage=open_)
    waiting = same_moment.RoundOutcome(now=100, agent=agent, active=True, rejected=False, limit=3, pending=True)
    assert same_moment.schedule_after_round(waiting, lambda: pytest.fail("pending conclusion must redecide now")) == (
        100, [], open_)


def _person_name(engine: LifeEngine, observer: int, target: int) -> str:
    """@brief 读取 observer 在同一地点看到的另一位人物的可读名称。"""
    observed = engine.observe(context(engine, f"person-{observer}-{target}-{engine.context.state.version}", observer))
    return next(row["object"] for row in observed["interaction_bindings"]
                if row["kind"] == "person" and row["object_id"] == f"agent:{target}")


def _both_waiting(engine: LifeEngine, woken: tuple[int, ...]) -> int:
    """@brief 两人同秒开始等待，一分钟后用邮件只叫醒 woken 中的人物；返回叫醒时刻。"""
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {agent_id: ("start_activity", start_activity("等待", "自己"))
                          for agent_id in engine.context.state.agents}, "both-wait")
    for agent_id in woken:
        wake_by_mail(engine, agent_id=agent_id, offset=60)
    assert advance_until_ready(engine)
    assert sorted(engine.scheduling_state().ready_agent_ids) == sorted(woken)
    return engine.context.state.current_time


def _message_to(engine: LifeEngine, target: int, text: str) -> tuple[str, SendMessageActionRequest]:
    """@brief 人物 1 发给同地点人物 target 的消息请求（先取可读名称）。"""
    return "send_message", SendMessageActionRequest(recipient_name=_person_name(engine, 1, target), text=text)


def _running_name(engine: LifeEngine, agent_id: int) -> str:
    """@brief 人物唯一进行中活动的可读名称。"""
    return activity_name(engine, active_activities(engine.context.state, agent_id)[0].activity_id)


def _end_wait(engine: LifeEngine, agent_id: int) -> tuple[str, object]:
    """@brief 结束人物正在进行的等待（瞬时动作：没有进行中活动时同一秒再决策，才看得到可交谈的同伴）。"""
    return "conclude_activity", conclude_request(_running_name(engine, agent_id), summary="不等了。")


def test_awake_recipient_gets_same_second_opportunity(engine_factory) -> None:
    """清醒的收件人在消息送达的同一秒获得机会，原因为 message_arrival，并计入其同刻轮数（「人物之间的消息」）。

    该轮观察展示过的消息随本轮提交记为已读：其后无关的边界不再以 message_arrival 第二次唤醒收件人。
    """
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1,))
    _act(engine, "person-end-wait", *_end_wait(engine, 1))
    assert engine.context.state.agents[2].next_decision_at > now
    sent = _act(engine, "person-send", *_message_to(engine, 2, "晚上一起吃饭吗"))
    assert sent.status == "completed"
    recipient = engine.context.state.agents[2]
    message_id = sent.data["message_id"]
    assert recipient.next_decision_at == now and recipient.pending_wake_reasons == ["message_arrival"]
    assert recipient.same_moment_rounds == 0 and sorted(engine.scheduling_state().ready_agent_ids) == [1, 2]
    assert message_id in recipient.inbox and message_id not in recipient.read_inbox_ids
    report = report_request(_running_name(engine, 2), summary="看到消息了。")
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")), 2: ("report_status", report)},
                 "person-read")
    assert _decision_reasons(engine, 2)[-1] == ["message_arrival"]
    assert engine.context.state.agents[2].same_moment_rounds == 1 and engine.context.state.current_time == now
    assert message_id in engine.context.state.agents[2].read_inbox_ids
    wake_by_mail(engine, agent_id=1, offset=45)
    advance_next(engine)
    assert engine.context.state.current_time == now + 45 and engine.scheduling_state().ready_agent_ids == (1,)
    assert engine.context.state.agents[2].pending_wake_reasons == []
    settlement = [event for event in engine.context.store.read_agent_events("environment_event", 2)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["interval_end"] == now + 45 and settlement.data["decision_offered"] is False
    assert "message_arrival" not in settlement.data["wake_reasons"]


def test_recipient_in_same_round_is_not_overwritten_by_its_own_success(engine_factory) -> None:
    """收件人与发件人同轮：本轮全部结算后才合并，收件人自己受理的排期不覆盖消息机会；原因按枚举顺序并入。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1, 2))
    submit_round(engine, {1: _end_wait(engine, 1), 2: _end_wait(engine, 2)}, "person-both-end")
    submit_round(engine, {1: _message_to(engine, 2, "在忙吗"), 2: ("start_activity", start_activity("等待", "自己"))},
                 "person-same-round")
    recipient = engine.context.state.agents[2]
    assert active_activities(engine.context.state, 2) and engine.context.state.current_time == now
    assert recipient.next_decision_at == now and recipient.pending_wake_reasons == ["message_arrival"]
    assert recipient.same_moment_rounds == 2 and 2 in engine.scheduling_state().ready_agent_ids
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[2].pending_wake_reasons = ["no_active_activity"]
    merged = same_moment.merge_message_readiness(staged, [2], engine.context.scenario.max_same_moment_decisions)
    assert merged.agents[2].pending_wake_reasons == ["message_arrival", "no_active_activity"]


def test_recipient_at_limit_gets_message_at_next_boundary(engine_factory) -> None:
    """已达同刻上限的收件人不在同一秒获得机会；未读消息在下一边界以 message_arrival 提供，判定之后才记为已读。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1,))
    _act(engine, "person-limit-end-wait", *_end_wait(engine, 1))
    _stage_agent(engine, 2, same_moment_rounds=engine.context.scenario.max_same_moment_decisions)
    before = engine.context.state.agents[2].next_decision_at
    _act(engine, "person-at-limit", *_message_to(engine, 2, "明天见"))
    recipient = engine.context.state.agents[2]
    message_id = next(iter(recipient.inbox))
    assert recipient.next_decision_at == before > now and message_id not in recipient.read_inbox_ids
    assert engine.scheduling_state().ready_agent_ids == (1,)
    _wait(engine, "person-limit-sender-wait")
    advance_next(engine)
    recipient = engine.context.state.agents[2]
    assert engine.context.state.current_time > now and 2 in engine.scheduling_state().ready_agent_ids
    assert "message_arrival" in recipient.pending_wake_reasons and message_id in recipient.read_inbox_ids


def test_restricted_round_does_not_use_up_message_opportunity(engine_factory) -> None:
    """已达上限的收件人在同秒受限轮里看到消息也回应不了：不记已读，下一边界照常以 message_arrival 提供机会。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1, 2))
    submit_round(engine, {1: _end_wait(engine, 1), 2: _end_wait(engine, 2)}, "restricted-both-end")
    _stage_agent(engine, 2, same_moment_rounds=engine.context.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    _, results = submit_round(engine, {1: _message_to(engine, 2, "看到回我"),
                                       2: ("start_activity", start_activity("进行", "办公"))},
                              "restricted-send")
    message_id = next(item.data["message_id"] for item in results if item.agent_id == 1)
    assert engine.context.state.agents[2].same_moment_stage == SameMomentStage.START_OR_RESUME
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("start_activity", start_activity("等待", "自己"))}, "restricted-wait")
    assert message_id not in engine.context.state.agents[2].read_inbox_ids
    wake_by_mail(engine, agent_id=1, offset=45)
    advance_next(engine)
    recipient = engine.context.state.agents[2]
    assert engine.context.state.current_time == now + 45 and 2 in engine.scheduling_state().ready_agent_ids
    assert "message_arrival" in recipient.pending_wake_reasons and message_id in recipient.read_inbox_ids


def test_conclusion_only_round_does_not_use_up_message_opportunity(engine_factory) -> None:
    """有待收尾活动的轮次只能收尾、回应不了消息：收件人在这一轮看到同秒早先送达的消息也不记已读；收尾后另一项活动
    仍在进行、按决策节律排期，下一边界照常以 message_arrival 提供机会（第一章「人物之间的消息」，第七章「活动一结束
    就要结束它」）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    target, action = _content(engine, "unread-content")
    both = start_request(targets=["自己", target], actions=["interact", WAYS[action][0]], ways=["等待", action],
                         app=[None, "哔哩哔哩"])
    submit_round(engine, {1: ("start_activity", both), 2: ("start_activity", start_activity("等待", "自己"))},
                 "unread-start")
    watch = next(item for item in active_activities(engine.context.state, 1) if item.kind == ActivityKind.ACTION)
    staged = engine.context.state.model_copy(deep=True)
    ends = staged.current_time + 300
    staged.activities[watch.activity_id] = watch.model_copy(
        update={"planned_end_at": ends, "planned_duration_seconds": 300}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_same_moment_staged"})])
    engine.context.state = staged
    wake_by_mail(engine, agent_id=2, offset=300)
    assert advance_until_ready(engine) and engine.context.state.current_time == ends
    assert sorted(engine.scheduling_state().ready_agent_ids) == [1, 2]
    other = ("start_activity", start_activity("等待", "自己"))
    _, freed = submit_round(engine, {1: other, 2: _end_wait(engine, 2)}, "unread-free")
    sender = SendMessageActionRequest(recipient_name=_person_name(engine, 2, 1), text="看到回我")
    _, sent = submit_round(engine, {1: other, 2: ("send_message", sender)}, "unread-send")
    assert [item.error_code for item in freed + sent if item.agent_id == 1] == ["ACTIVITY_CONCLUSION_REQUIRED"] * 2
    message_id = next(item.data["message_id"] for item in sent if item.agent_id == 2)
    recipient = engine.context.state.agents[1]
    assert message_id in recipient.inbox and message_id not in recipient.read_inbox_ids
    assert recipient.next_decision_at == ends and recipient.same_moment_rounds == 2
    conclude = conclude_request(activity_name(engine, watch.activity_id))
    _, last = submit_round(engine, {1: ("conclude_activity", conclude), 2: other}, "unread-conclude")
    assert all(item.status in {"completed", "in_progress"} for item in last) and active_activities(engine.context.state, 1)
    recipient = engine.context.state.agents[1]
    assert recipient.next_decision_at > ends and message_id not in recipient.read_inbox_ids
    wake_by_mail(engine, agent_id=2, offset=45)
    advance_next(engine)
    recipient = engine.context.state.agents[1]
    assert engine.context.state.current_time == ends + 45 and 1 in engine.scheduling_state().ready_agent_ids
    assert "message_arrival" in recipient.pending_wake_reasons and message_id in recipient.read_inbox_ids


def test_sleeping_recipient_is_not_woken_by_person_message(engine_factory) -> None:
    """同轮开始睡觉的收件人不因人物消息在同一秒或其后的边界被唤醒（「人物之间的消息」，睡眠中过滤）；睡眠中也不读，
    消息保持未读（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    now = engine.context.state.current_time
    _, results = submit_round(engine, {1: _message_to(engine, 2, "早点休息"),
                                       2: ("start_activity", start_activity("睡觉", "自己"))}, "person-sleep")
    message_id = next(item.data["message_id"] for item in results if item.agent_id == 1)
    assert engine.context.state.agents[2].next_decision_at > now and engine.scheduling_state().ready_agent_ids == (1,)
    _wait(engine, "person-sleep-sender-wait")
    advance_next(engine)
    settlement = [event for event in engine.context.store.read_agent_events("environment_event", 2)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["decision_offered"] is False and settlement.data["wake_reasons"] == []
    assert 2 not in engine.scheduling_state().ready_agent_ids
    assert message_id in engine.context.state.agents[2].inbox and message_id not in engine.context.state.agents[2].read_inbox_ids


def test_rejected_paired_start_leaves_no_chat_invite(engine_factory) -> None:
    """交谈与通话是同一种动作，整组开始被拒（都占 talk，RESOURCE_CONFLICT）时，交谈邀请随整组撤回：对方收件箱不变，
    同秒与其后的边界都不以 message_arrival 唤醒对方；交谈与观看不是同一种，同一请求照常受理（同时做两件事规格「乙」）。"""
    engine = engine_factory("multi-person.json", mutate=_contacts)
    now = _both_waiting(engine, woken=(1,))
    _act(engine, "paired-end-wait", *_end_wait(engine, 1))
    inbox = dict(engine.context.state.agents[2].inbox)
    request = start_request(targets=["聊天", "语音通话"], actions=["interact", "interact"],
                            ways=["进行", "进行"], app=[None, "微信"],
                            to=[_person_name(engine, 1, 2), "联系人：母亲"])
    rejected = _act(engine, "paired-start", "start_activity", request)
    assert rejected.status == "rejected" and rejected.error_code == "RESOURCE_CONFLICT"
    partner = engine.context.state.agents[2]
    assert partner.inbox == inbox and partner.next_decision_at > now and partner.pending_wake_reasons == []
    assert not [event for event in engine.context.store.read_agent_events("environment_event", 2)
                if event.data.get("kind") == "message_arrival"]
    _wait(engine, "paired-sender-wait")
    wake_by_mail(engine, agent_id=1, offset=30)
    advance_next(engine)
    assert engine.context.state.current_time == now + 30 and engine.scheduling_state().ready_agent_ids == (1,)
    assert engine.context.state.agents[2].inbox == inbox and engine.context.state.agents[2].pending_wake_reasons == []
    _act(engine, "paired-watch-end-wait", *_end_wait(engine, 1))
    content, way = _content(engine, "paired-content")
    accepted = _act(engine, "paired-watch-start", "start_activity", start_request(
        targets=["聊天", content], actions=["interact", "interact"], ways=["进行", way],
        app=[None, "哔哩哔哩"], to=[_person_name(engine, 1, 2), None]))
    assert accepted.status == "in_progress", (accepted.error_code, accepted.message)
    assert sorted(item.kind.value for item in active_activities(engine.context.state, 1)) == ["action", "action"]


@pytest.mark.asyncio
async def test_contact_reply_arrival_filtered_during_sleep(engine_factory) -> None:
    """联系人回复在 CONTACT_REPLY_DELAY_SECONDS 后送达收件箱并留送达记录；收件人在睡眠中不被唤醒（裁决 #21），
    也不读（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-不适脉冲与睡眠中的消息 ）。"""
    from tests.life.test_export_integration import _StubReplyClient
    from worldmock.runtime import settle_pending_model_replies

    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    assert _act(engine, "reply-sleep", "send_message", SendMessageActionRequest(
        recipient_name="联系人：好友", text="周五晚上一起吃饭？")).status == "completed"
    due = engine.context.state.current_time + CONTACT_REPLY_DELAY_SECONDS
    reply_id = (await settle_pending_model_replies(engine, client=_StubReplyClient()))[0]["reply_message_id"]
    assert _act(engine, "reply-sleep-start", "start_activity", start_activity("睡觉", "自己")).status == "in_progress"
    while engine.context.state.current_time < due:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    assert engine.context.state.current_time == due and reply_id in engine.context.state.agents[1].inbox
    assert engine.scheduling_state().ready_agent_ids == () and reply_id not in engine.context.state.agents[1].read_inbox_ids
    events = engine.context.store.read_agent_events("environment_event", 1)
    assert [event.occurred_at for event in events if event.data.get("kind") == "message_arrival"
            and event.data.get("message_id") == reply_id] == [due]
    settlement = [event for event in events if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["interval_end"] == due and settlement.data["decision_offered"] is False


def _observed_act(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 单人物观察、登记并结算一轮，返回本轮观察与结果。"""
    decision = context(engine, name, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _supply_order(observed: dict) -> list[str]:
    """@brief 本轮交互对象层中门店供给的呈现顺序。"""
    return [row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "supply"]


def test_same_second_rounds_keep_presentation_window_and_rotation(engine_factory) -> None:
    """同一秒各轮沿用该秒首轮的呈现窗口与轮转判据（各对象的上次呈现时刻）；时钟推进时留痕并入，轮转判据才变
    （「同一秒内的呈现」；轮转按每个对象的 last_presented_at，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    now, last = engine.context.state.current_time, engine.observations._last_presented(1)
    first, _ = _observed_act(engine, "window-1", "start_activity", start_activity("进行", "办公"))
    second, _ = _observed_act(engine, "window-2", "start_activity", start_activity("进行", "办公"))
    assert engine.context.state.current_time == now
    assert _presented(second) == _presented(first)
    assert engine.observations._last_presented(1) == last
    staged = engine.context.state.same_moment_presentation[1].stamps
    assert staged and set(staged.values()) == {now}
    _wait(engine, "window-wait")
    advance_next(engine)
    assert engine.context.state.same_moment_presentation == {}
    assert set(staged) <= set(engine.context.state.presentation_state[1])
    assert engine.observations._last_presented(1) != last and all(engine.observations._last_presented(1)[item] == now for item in staged)



def test_two_agents_same_round_keep_their_presentation_stamps(engine_factory) -> None:
    """两人同轮观察时各自的呈现留痕都保留，后观察者不覆盖前者；时钟推进后都并入各自的轮转凭据。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {agent_id: ("start_activity", start_activity("等待", "自己"))
                          for agent_id in engine.context.state.agents}, "stamps-both")
    staged = dict(engine.context.state.same_moment_presentation)
    assert set(staged) == {1, 2} and all(entry.stamps for entry in staged.values())
    advance_next(engine)
    assert engine.context.state.same_moment_presentation == {}
    for agent_id in (1, 2):
        assert set(staged[agent_id].stamps) <= set(engine.context.state.presentation_state[agent_id])


def test_presentation_staging_survives_reopen(engine_factory, tmp_path) -> None:
    """本秒的呈现暂存随世界状态保存；重开后同一秒仍沿用该秒首轮的呈现窗口（检查点封存，实现约定）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, _ = _observed_act(engine, "reopen-window-1", "start_activity", start_activity("进行", "办公"))
    staged = engine.context.state.same_moment_presentation[1]
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(tmp_path / "scenario-0" / "scenarios" / "single-person.json", run_dir)
    try:
        assert restored.context.state.same_moment_presentation[1] == staged
        second, _ = _observed_act(restored, "reopen-window-2", "start_activity", start_activity("等待", "自己"))
        assert _presented(second) == _presented(first)
    finally:
        restored.close()


def _contact_message(observed: dict) -> SendMessageActionRequest:
    """@brief 发给本轮可见的一位通讯录联系人的消息请求。"""
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    return SendMessageActionRequest(recipient_name=contact, text="今天有空吗")


def _presented(observed: dict) -> list[str]:
    """@brief 本轮工具层呈现的目标名（按顺序）。"""
    return [row["name"] for row in observed["tool_layer"]["targets"]]


def test_same_second_results_stay_in_the_progress_layer(engine_factory) -> None:
    """同一秒的后续决策从进度层看到此前本人世界请求的公开结果：被拒绝的带原因与下一步，消息与修订写已受理；结果在整个
    session 内保留，时间推进后仍在；进度层不含内部身份（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」，取代「刚才」一句）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, rejected = _observed_act(engine, "now-reject", "start_activity", start_activity("进行", "不存在的对象"))
    assert progress_calls(first) == [] and rejected.status == "rejected"
    second, sent = _observed_act(engine, "now-message", "send_message", _contact_message(first))
    (refused,) = progress_calls(second)
    assert refused["result"]["status"] == "rejected"
    assert refused["result"]["details"] == {"reason": rejected.data["reason"], "next_step": rejected.data["next_step"],
                                            "fix": rejected.data["fix"], "items": rejected.data["items"]}
    assert [item["error_code"] for item in rejected.data["items"]] == ["NAME_NOT_FOUND"]
    assert sent.status == "completed"
    third, revised = _observed_act(engine, "now-revise", "revise_day_plan", ReviseDayPlanRequest(
        goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None),]))
    assert [row["tool"] for row in progress_calls(third)] == ["start_activity", "send_message"]
    assert progress_calls(third)[-1]["result"]["status"] == "completed" and revised.status == "completed"
    fourth, waited = _observed_act(engine, "now-wait", "start_activity", start_activity("等待", "自己"))
    assert progress_calls(fourth)[-1]["tool"] == "revise_day_plan" and waited.status == "in_progress"
    for observed in (second, third, fourth):
        rendered = json.dumps(observed["progress_layer"], ensure_ascii=False)
        assert "agent:" not in rendered and "activity-" not in rendered and "action-" not in rendered
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    later = engine.observe(context(engine, "now-next-second"))
    assert [row["tool"] for row in progress_calls(later)] == [
        "start_activity", "send_message", "revise_day_plan", "start_activity"]
    assert engine.context.state.agents[1].same_moment_rounds == 0


def test_purchase_result_names_the_order_in_the_progress_layer(engine_factory) -> None:
    """购买是瞬时动作：同一秒的下一轮进度层里该调用的 details.purchases 写商品名、份数、付款、送达地点与时刻、收货人与
    所回应的消息，不含供给或订单身份；方式是请求参数，只在该调用的 arguments.ways 中，结果不复述（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求
    「进度层」：details 不复述参数）。"""

    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "order-preview"))
    row, action = next((row, action) for row in observed["interaction_bindings"] if row["kind"] == "supply" and row["goods_id"] == "beef-noodles"
                       for action in row["actions"] if action["canonical_action"] == "purchase"
                       and action["note"] is None)
    bought = _act(engine, "order-buy", "start_activity",
                  start_activity(action["label"], row["object"], app=["美团"], pay_app=["微信"]))
    assert bought.status == "completed", bought.error_code
    after = engine.observe(context(engine, "order-after"))
    product = engine.context.semantics.catalog.goods[row["goods_id"]].name
    record = progress_calls(after)[-1]
    (purchase,) = record["result"]["details"]["purchases"]
    assert set(purchase) == {"product", "quantity", "paid", "destination", "expected_at", "recipient", "request"}
    assert (purchase["product"], purchase["quantity"]) == (product, "1 份") and purchase["paid"].endswith(" 元")
    assert [item["way"] for item in record["arguments"]["items"]] == [action["label"]]
    assert action["label"] not in json.dumps(record["result"], ensure_ascii=False)
    assert "supply" not in json.dumps(after["progress_layer"], ensure_ascii=False)


def test_partner_ended_chat_redecides_same_second_with_planned_end(engine_factory) -> None:
    """交谈一方结束时，对方以其为伙伴的交谈在同一秒结束（completed，boundary_type 与 interruption_reason 都记
    partner_ended）并成为待收尾；对方在同一秒获得机会，仍有其他进行中活动时本轮原因为 planned_end（第五章「交谈」，
    第一章规则 6），收尾后照常继续。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {1: ("start_activity", start_activity("进行", "聊天", to=[_person_name(engine, 1, 2)])),
                          2: ("start_activity", start_activity("等待", "自己"))}, "partner-invite")
    assert engine.scheduling_state().ready_agent_ids == (2,)
    submit_round(engine, {2: ("start_activity", start_activity("进行", "聊天", to=[_person_name(engine, 2, 1)]))}, "partner-accept")
    chats = {item.agent_id: item for item in engine.context.state.activities.values() if item.kind == ActivityKind.ACTION and item.data.get("category") == "social"}
    assert chats[2].status == ActivityStatus.ACTIVE and chats[2].planned_end_at == chats[1].planned_end_at
    wake_by_mail(engine, agent_id=1, offset=60)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (1,)
    ended_at = engine.context.state.current_time
    _act(engine, "partner-end", "conclude_activity", conclude_request(activity_name(engine, chats[1].activity_id)))
    partner = engine.context.state.activities[chats[2].activity_id]
    assert partner.status == ActivityStatus.COMPLETED and partner.ended_at == ended_at
    assert partner.boundary_type == partner.interruption_reason == "partner_ended"
    agent = engine.context.state.agents[2]
    assert agent.next_decision_at == ended_at and agent.pending_wake_reasons == ["planned_end"]
    assert set(engine.scheduling_state().ready_agent_ids) == {1, 2}
    names = pending_names(engine.observe(context(engine, "partner-pending", 2)))
    assert names == [activity_name(engine, partner.activity_id)]
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("conclude_activity", conclude_request(*names))}, "partner-conclude")
    assert _decision_reasons(engine, 2)[-1] == ["planned_end"]
    assert engine.context.state.activities[partner.activity_id].concluded_at == ended_at
    assert not pending_conclusions(engine.context.state, 2) and active_activities(engine.context.state, 2)


def test_partner_ended_at_the_limit_restarts_the_ladder_from_conclusion(engine_factory) -> None:
    """对方在同刻上限之后才同秒结束交谈时，本人的新待收尾按规则 3 的阶梯从收尾一支重新开始（conclude_pending），
    同一秒另加的轮数不超过三轮，收尾不被业务拒绝（第一章规则 3、第五章「交谈」）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {1: ("start_activity", start_activity("进行", "聊天", to=[_person_name(engine, 1, 2)])),
                          2: ("start_activity", start_activity("等待", "自己"))}, "ladder-invite")
    submit_round(engine, {2: ("start_activity", start_activity("进行", "聊天", to=[_person_name(engine, 2, 1)]))}, "ladder-accept")
    chats = {item.agent_id: item for item in engine.context.state.activities.values() if item.kind == ActivityKind.ACTION and item.data.get("category") == "social"}
    wake_by_mail(engine, agent_id=1, offset=60)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (1,)
    moment = engine.context.state.current_time
    limit = engine.context.scenario.max_same_moment_decisions
    _stage_agent(engine, 2, same_moment_rounds=limit)
    _act(engine, "ladder-end", "conclude_activity", conclude_request(activity_name(engine, chats[1].activity_id)))
    partner = engine.context.state.agents[2]
    assert partner.same_moment_stage == SameMomentStage.CONCLUDE_PENDING and partner.next_decision_at == moment
    observed = engine.observe(context(engine, "ladder-pending", 2))
    assert observed["same_moment_stage"] == "conclude_pending"
    names = pending_names(observed)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                                       2: ("conclude_activity", conclude_request(*names))}, "ladder-conclude")
    assert all(result.status in {"completed", "in_progress"} for result in results)
    assert not pending_conclusions(engine.context.state, 2) and 2 not in engine.scheduling_state().ready_agent_ids
    assert engine.context.state.agents[2].same_moment_rounds <= limit + 3


def _with_inbox(state, agent_id: int, *message_ids: str):
    """@brief 给人物收件箱加入几条直接消息（不提交，只用于纯函数判定）。"""
    agent = state.agents[agent_id]
    added = {item: Message(message_id=item, kind="direct", sender_id="agent:9", recipient_id=f"agent:{agent_id}",
                           text=f"消息{item}", created_at=state.current_time) for item in message_ids}
    agents = {**state.agents, agent_id: agent.model_copy(update={"inbox": {**agent.inbox, **added}}, deep=True)}
    return state.model_copy(update={"agents": agents}, deep=True)


def test_read_observed_messages_marks_only_presented_messages_of_open_rounds(engine_factory) -> None:
    """「已读」即全文已呈现（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」）：只把本轮观察呈现过全文、且已在
    轮前收件箱里的消息记为已读；没有呈现的、本轮内才送达的保持未读，映射中没有的人物不变；轮前处于受限轮或有待收尾活动
    的人物回应不了消息，不记已读。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    observed = _with_inbox(_with_inbox(engine.context.state, 1, "m1", "m2"), 2, "n1")
    settled = _with_inbox(observed, 1, "m3")
    read = same_moment.read_observed_messages(settled, observed, {1: frozenset({"m1", "m3"})})
    assert read.agents[1].read_inbox_ids == {"m1"}
    assert read.agents[1].body != settled.agents[1].body and read.agents[2] == settled.agents[2]
    assert same_moment.read_observed_messages(settled, observed, {1: frozenset(), 2: frozenset()}) == settled
    restricted = observed.model_copy(deep=True)
    restricted.agents[1].same_moment_stage = SameMomentStage.START_OR_RESUME
    assert same_moment.read_observed_messages(settled, restricted, {1: frozenset({"m1"})}) == settled
    ended = _unit_activity("ended-wait", ActivityStatus.COMPLETED)
    pending = observed.model_copy(update={"activities": {**observed.activities, ended.activity_id: ended}})
    assert pending_conclusions(pending, 1)
    assert same_moment.read_observed_messages(settled, pending, {1: frozenset({"m1"})}) == settled
