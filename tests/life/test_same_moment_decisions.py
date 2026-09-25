"""同刻决策（docs/dev/SPEC-time-and-energy.md 第一章）：决策不耗时，同一秒再决策、同刻上限与受限轮阶梯。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall
from pydantic import ValidationError

from tests.life.conftest import (
    advance_next, advance_until_ready, context, stage_day_plan, start_activity, submit_round, wake_by_mail,
)
from tests.life.test_scheduling import _custom_engine, _document, _write_scenario
from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import same_moment
from worldmock.life.body import apply_mood_pulse
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ActivityStatus, ConcludeActivityRequest, PauseActivityRequest, PlanDayRequest, PlanGoalInput,
    ReportStatusRequest, ReviseDayPlanRequest, SameMomentStage, SendMessageActionRequest, active_activities,
)
from worldmock.life.narration import render_scene
from worldmock.life.projection import SAME_MOMENT_NOTE
from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.life.schedule import WakeSnapshot, evaluate_wake_reasons

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_RESTRICTED = "SAME_MOMENT_ACTION_RESTRICTED"


def _act(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 单人物按真实顺序观察、登记并结算一轮，返回该人物结果。"""
    _, results = submit_round(engine, {agent_id: (tool, request)}, name)
    return next(item for item in results if item.agent_id == agent_id)


def _wait(engine: LifeEngine, name: str, agent_id: int = 1):
    """@brief 以可读对象「自己」开始一段等待。"""
    return _act(engine, name, "start_activity", start_activity("等待", "自己"), agent_id)


def _invalid_start(engine: LifeEngine, name: str, agent_id: int = 1):
    """@brief 选择不存在的对象，得到稳定的业务拒绝 ACTION_REQUEST_INVALID。"""
    return _act(engine, name, "start_activity", start_activity("观看", "不存在的对象"), agent_id)


def _revise(engine: LifeEngine, name: str):
    """@brief 修订当日目标：一次成功的瞬时动作。"""
    return _act(engine, name, "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="休息", goal="歇一歇"),)))


def _stage_agent(engine: LifeEngine, agent_id: int = 1, **updates) -> None:
    """@brief 改造人物状态并经存储落盘（模拟绕过宿主本地校验的请求所在的轮次状态）。"""
    staged = engine.state.model_copy(deep=True)
    staged.agents[agent_id] = staged.agents[agent_id].model_copy(update=updates, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_same_moment_staged"})])
    engine.state = staged


def _session_name(engine: LifeEngine, activity_id: str) -> str:
    """@brief 读取活动所在会话的可读名称。"""
    return next(item.session_name for item in engine.state.sessions.values()
                if activity_id in item.activity_ids)


def _decision_reasons(engine: LifeEngine, agent_id: int = 1) -> list[list[str]]:
    """@brief 按写入顺序读取人物每轮 decision_started 的唤醒原因。"""
    return [event.data["wake_reasons"] for event in engine.store.read_agent_events("decision_started", agent_id)]


def _reach_limit(engine: LifeEngine) -> None:
    """@brief 在同一秒连续业务拒绝直到达到同刻上限，进入受限轮 start_or_resume。"""
    limit = engine.scenario.max_same_moment_decisions
    while engine.state.agents[1].same_moment_rounds < limit:
        _invalid_start(engine, f"reach-limit-{engine.state.agents[1].same_moment_rounds}")
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME


def _finish(engine: LifeEngine, activity_id: str, name: str) -> None:
    """@brief 经真实边界推进到活动结束；中途的决策机会以播报继续。"""
    for step in range(64):
        if engine.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"{name}-continue-{step}", "report_status", ReportStatusRequest(
                task=[_session_name(engine, activity_id)], summary=["继续。"]))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def test_run_start_and_instant_action_redecide_in_same_second(engine_factory) -> None:
    """运行开始与瞬时动作之后同一秒再决策；第二轮只记 no_active_activity（规则 2、6）。"""
    engine = engine_factory()
    start = engine.state.current_time
    assert engine.state.agents[1].pending_wake_reasons == ["run_start"]
    planned = _act(engine, "run-start-plan", "plan_day", PlanDayRequest(goals=()))
    assert planned.status == "completed"
    agent = engine.state.agents[1]
    assert engine.state.current_time == start and agent.next_decision_at == start
    assert agent.same_moment_rounds == 1 and agent.pending_wake_reasons == ["no_active_activity"]
    assert engine.scheduling_state().ready_agent_ids == (1,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    waited = _wait(engine, "run-start-wait")
    assert waited.status == "in_progress"
    assert _decision_reasons(engine) == [["run_start"], ["no_active_activity"]]
    assert engine.state.agents[1].next_decision_at > start
    advance_next(engine)
    assert engine.state.current_time > start


def test_activity_end_boundary_offers_no_active_activity_and_redecides(engine_factory) -> None:
    """活动结束的边界兜底 no_active_activity 写入共同身体结算，之后的瞬时动作同秒再决策（规则 2、5）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "end-wait")
    _finish(engine, waited.activity_id, "end-wait")
    end = engine.state.current_time
    settlement = [event for event in engine.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["interval_end"] == end
    assert {"planned_end", "no_active_activity"} <= set(settlement.data["wake_reasons"])
    assert engine.scheduling_state().ready_agent_ids == (1,)
    revised = _revise(engine, "end-revise")
    assert revised.status == "completed"
    assert engine.state.agents[1].next_decision_at == end
    _wait(engine, "end-rest")
    assert engine.state.current_time == end
    reasons = _decision_reasons(engine)
    assert {"planned_end", "no_active_activity"} <= set(reasons[-2])
    assert reasons[-1] == ["no_active_activity"]
    # 同刻轮次没有结算区间：该秒只有边界那一条共同身体结算，再决策轮的原因只记于 decision_started。
    assert [event.data["interval_end"] for event in engine.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") == "body_settlement"].count(end) == 1


def test_business_rejection_redecides_in_same_second_with_rejection_feedback(engine_factory) -> None:
    """业务拒绝后同一秒再决策，原因按枚举顺序；结果数据用同刻轮次三键（规则 2、6，实现约定）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = engine.state.current_time
    rejected = _invalid_start(engine, "reject-once")
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert rejected.data["same_moment_round"] == 1
    assert rejected.data["max_same_moment_decisions"] == engine.scenario.max_same_moment_decisions
    assert rejected.data["next_decision_at"] == now
    assert "consecutive_rejections" not in rejected.data and "maximum_rejections" not in rejected.data
    agent = engine.state.agents[1]
    assert agent.pending_wake_reasons == ["rejection_feedback", "no_active_activity"]
    assert agent.consecutive_rejections == 1 and agent.next_decision_at == now
    assert _wait(engine, "reject-then-wait").status == "in_progress"
    assert _decision_reasons(engine)[-1] == ["rejection_feedback", "no_active_activity"]
    assert engine.state.agents[1].consecutive_rejections == 0


def test_rejection_with_active_activity_retries_until_limit_then_follows_rhythm(engine_factory) -> None:
    """有进行中活动时业务拒绝在同一秒再给机会，达到上限后按决策节律，不进受限轮（规则 4）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "active-wait")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    now = engine.state.current_time
    assert engine.state.activities[waited.activity_id].status == ActivityStatus.ACTIVE
    limit = engine.scenario.max_same_moment_decisions
    for index in range(1, limit + 1):
        result = _invalid_start(engine, f"active-reject-{index}")
        agent = engine.state.agents[1]
        assert result.data["same_moment_round"] == index
        if index < limit:
            assert agent.next_decision_at == now and agent.pending_wake_reasons == ["rejection_feedback"]
    agent = engine.state.agents[1]
    assert agent.next_decision_at > now and agent.pending_wake_reasons == []
    assert agent.same_moment_stage == SameMomentStage.OPEN
    advance_next(engine)
    assert engine.state.agents[1].same_moment_rounds == 0


def test_restricted_rounds_open_start_then_wait_only(engine_factory) -> None:
    """达到上限先只开放开始或恢复，开始被拒后只开放等待；等待被受理，同秒轮数不超过上限加三（规则 3）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = engine.state.current_time
    limit = engine.scenario.max_same_moment_decisions
    _reach_limit(engine)
    assert engine.state.agents[1].next_decision_at == now
    rejected_start = _invalid_start(engine, "restricted-start")
    assert rejected_start.status == "rejected"
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.WAIT_ONLY
    waited = _wait(engine, "restricted-wait")
    assert waited.status == "in_progress"
    agent = engine.state.agents[1]
    assert agent.same_moment_rounds <= limit + 3
    assert agent.same_moment_stage == SameMomentStage.WAIT_ONLY and agent.next_decision_at > now
    advance_next(engine)
    agent = engine.state.agents[1]
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (0, SameMomentStage.OPEN)


def test_out_of_bounds_request_is_rejected_and_counted(engine_factory) -> None:
    """绕过本地校验的越界请求以 SAME_MOMENT_ACTION_RESTRICTED 业务拒绝并计入同刻轮数（实现约定）。"""
    from tests.life.test_category_runtime import _stage_open_market

    engine = engine_factory()
    stage_day_plan(engine)
    _stage_open_market(engine)
    limit = engine.scenario.max_same_moment_decisions
    observed = engine.observe(context(engine, "bounds-preview"))
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    purchase = next(start_activity(action["label"], row["object"]) for row in observed["interaction_bindings"]
                    for action in row["actions"] if action["canonical_action"] == "purchase")
    cases = [
        (SameMomentStage.START_OR_RESUME, "send_message", SendMessageActionRequest(recipient_name=contact, text="在吗")),
        (SameMomentStage.START_OR_RESUME, "start_activity", purchase),
        (SameMomentStage.WAIT_ONLY, "start_activity", start_activity("睡觉", "自己")),
    ]
    for index, (stage, tool, request) in enumerate(cases):
        _stage_agent(engine, same_moment_rounds=limit, same_moment_stage=stage)
        result = _act(engine, f"bounds-{index}", tool, request)
        assert result.status == "rejected" and result.error_code == _RESTRICTED
        agent = engine.state.agents[1]
        assert agent.same_moment_rounds == limit + 1 and result.data["same_moment_round"] == limit + 1
        assert agent.same_moment_stage == SameMomentStage.WAIT_ONLY
        assert agent.next_decision_at == engine.state.current_time
    assert not active_activities(engine.state, 1) and engine.state.deliveries == {}


def test_start_or_resume_accepts_start_and_resume_but_not_other_reports(engine_factory) -> None:
    """start_or_resume 接受开始持续活动与恢复暂停活动；点名未暂停会话的播报越界（规则 3）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    first = _wait(engine, "resume-first-wait")
    _finish(engine, first.activity_id, "resume-first")
    _stage_agent(engine, same_moment_rounds=engine.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    ended = _act(engine, "resume-ended-report", "report_status", ReportStatusRequest(
        task=[_session_name(engine, first.activity_id)], summary=["结束了。"]))
    assert ended.status == "rejected" and ended.error_code == _RESTRICTED
    second = _wait(engine, "resume-second-wait")
    assert second.status == "in_progress"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused = _act(engine, "resume-pause", "pause_activity", PauseActivityRequest(
        task=[_session_name(engine, second.activity_id)], summary=["先停一下。"]))
    assert paused.status == "completed" and not active_activities(engine.state, 1)
    _stage_agent(engine, same_moment_rounds=engine.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    resumed = _act(engine, "resume-report", "report_status", ReportStatusRequest(
        task=[_session_name(engine, second.activity_id)], summary=["继续等。"]))
    assert resumed.status == "completed"
    assert engine.state.activities[second.activity_id].status == ActivityStatus.ACTIVE
    agent = engine.state.agents[1]
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
    assert agent.next_decision_at > engine.state.current_time
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    session = _session_name(engine, second.activity_id)
    _act(engine, "resume-conclude", "conclude_activity",
         ConcludeActivityRequest(task=[session], summary=["不等了。"]))
    _stage_agent(engine, same_moment_rounds=engine.scenario.max_same_moment_decisions,
                 same_moment_stage=SameMomentStage.START_OR_RESUME)
    watched = _act(engine, "resume-watch", "start_activity", start_activity("观看", "内容：Sintel"))
    assert watched.status == "in_progress"


def test_feedback_interval_60_bounds_same_second_rounds(tmp_path) -> None:
    """结算间隔 60 秒时屡次拒绝也不死循环：每人每秒的决策轮数不超过上限加三，运行不失败（规则 3）。"""
    def feedback_60(document: dict) -> None:
        """@brief 等待的计划时长取最小的结算间隔。"""
        document["activity_feedback_seconds"] = 60

    engine = _custom_engine("white-collar-12-weeks.json", feedback_60, tmp_path, "feedback-60")
    try:
        stage_day_plan(engine)
        limit = engine.scenario.max_same_moment_decisions
        waits = 0
        for step in range(200):
            if waits == 4:
                break
            if not engine.scheduling_state().ready_agent_ids:
                advance_next(engine)
                continue
            if engine.state.agents[1].same_moment_stage == SameMomentStage.WAIT_ONLY:
                assert _wait(engine, f"f60-wait-{step}").status == "in_progress"
                waits += 1
            else:
                _invalid_start(engine, f"f60-reject-{step}")
        assert waits == 4
        rounds = Counter((event.agent_id, event.occurred_at)
                         for event in engine.store.read_agent_events("decision_started", 1))
        assert len(rounds) >= 4 and max(rounds.values()) <= limit + 3
        events = engine.store.read_events(engine.store.last_event_seq)
        assert not any(event.event_type == "run_failed" for event in events)
    finally:
        engine.close()


def test_replay_round_counts_toward_same_moment_rounds(engine_factory) -> None:
    """幂等重放计入同刻轮数、按存档结果同秒再决策，不重做脉冲与连续拒绝计数（规则 9）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    request = start_activity("观看", "不存在的对象")
    first = _act(engine, "replayed", "start_activity", request)
    assert first.status == "rejected"
    body = engine.state.agents[1].body
    _, results = submit_round(engine, {1: ("start_activity", request)}, "replayed")
    assert results[0] == first
    agent = engine.state.agents[1]
    assert agent.same_moment_rounds == 2 and agent.next_decision_at == engine.state.current_time
    assert agent.pending_wake_reasons == ["rejection_feedback", "no_active_activity"]
    assert agent.consecutive_rejections == 1 and agent.body == body
    replayed = [event for event in engine.store.read_agent_events("action_rejected", 1)
                if event.data.get("reused")]
    assert len(replayed) == 1


def test_clock_advances_only_when_everyone_has_an_active_activity(engine_factory) -> None:
    """时钟只在人人都有进行中活动时推进；有人同秒就绪时推进是协议错误（规则 1）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("start_activity", start_activity("观看", "不存在的对象"))}, "clock-0")
    assert engine.scheduling_state().ready_agent_ids == (2,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    advances = 0
    for step in range(1, 40):
        if advances == 4:
            break
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            assert all(active_activities(engine.state, agent_id) for agent_id in engine.state.agents)
            advance_next(engine)
            advances += 1
            continue
        requests = {}
        for agent_id in ready:
            running = active_activities(engine.state, agent_id)
            requests[agent_id] = (("report_status", ReportStatusRequest(
                task=[_session_name(engine, running[0].activity_id)], summary=["继续。"]))
                if running else ("start_activity", start_activity("等待", "自己")))
        submit_round(engine, requests, f"clock-{step}")
    assert advances == 4


def test_report_with_active_activity_does_not_redecide_in_same_second(engine_factory) -> None:
    """有进行中活动时播报成功后不在同一秒再给机会，下一次按决策节律（规则 4）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "report-wait")
    wake_by_mail(engine, offset=300)
    assert advance_until_ready(engine)
    now = engine.state.current_time
    reported = _act(engine, "report-continue", "report_status", ReportStatusRequest(
        task=[_session_name(engine, waited.activity_id)], summary=["还在等。"]))
    assert reported.status == "completed"
    agent = engine.state.agents[1]
    assert agent.next_decision_at > now and agent.pending_wake_reasons == []
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)


def test_wake_reasons_replaced_per_round_single_bedtime_reflection(engine_factory) -> None:
    """醒来那一秒只有首轮带 wake_from_sleep，之后的同刻轮次按本轮替换原因（规则 6）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    slept = _act(engine, "reflection-sleep", "start_activity", start_activity("睡觉", "自己"))
    assert slept.status == "in_progress"
    while engine.state.activities[slept.activity_id].status == ActivityStatus.ACTIVE:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    woke = engine.state.current_time
    assert "wake_from_sleep" in engine.state.agents[1].pending_wake_reasons
    _revise(engine, "reflection-first")
    assert engine.state.agents[1].pending_wake_reasons == ["no_active_activity"]
    _wait(engine, "reflection-second")
    assert engine.state.current_time == woke
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
    engine = engine_factory()
    stage_day_plan(engine)
    slept = _act(engine, "quiet-sleep", "start_activity", start_activity("睡觉", "自己"))
    advance_next(engine)
    assert engine.state.activities[slept.activity_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == ()
    settlement = [event for event in engine.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["decision_offered"] is False and settlement.data["wake_reasons"] == []


def _short_intervals(document: dict) -> None:
    """@brief 重估间隔短于等待与播报，使下一次决策时刻由重估候选决定。"""
    document["activity_feedback_seconds"] = 3600
    document["reconsideration_interval_seconds"] = 600
    document["idle_reconsideration_interval_seconds"] = 1200
    document["progress_report_interval_seconds"] = 86400


def test_first_decision_includes_reconsideration_candidate(tmp_path) -> None:
    """首次决策（从未决策过）即含重估候选；重估候选恒为本次决策时刻加间隔（规则 7）。"""
    engine = _custom_engine("white-collar-12-weeks.json", _short_intervals, tmp_path, "reconsider")
    try:
        stage_day_plan(engine)
        assert engine.state.agents[1].last_decision_time is None
        first = engine.state.current_time
        waited = _wait(engine, "reconsider-wait")
        planned_end = engine.state.activities[waited.activity_id].planned_end_at
        assert planned_end == first + 3600
        assert engine.state.agents[1].next_decision_at == first + 1200
        assert advance_until_ready(engine)
        later = engine.state.current_time
        assert later == first + 1200
        assert engine.state.agents[1].pending_wake_reasons == ["reconsideration_interval"]
        _act(engine, "reconsider-report", "report_status", ReportStatusRequest(
            task=[_session_name(engine, waited.activity_id)], summary=["接着等。"]))
        assert engine.state.agents[1].next_decision_at == later + 1200
    finally:
        engine.close()


def test_idle_interval_only_while_waiting_without_commitments(tmp_path) -> None:
    """只在等待且没有其他在手承诺时用空闲重估间隔；有可见未完成任务时用常规间隔。"""
    engine = _custom_engine("white-collar-12-weeks.json", _short_intervals, tmp_path, "idle-interval")
    try:
        stage_day_plan(engine)
        start = engine.state.current_time
        assert not engine.state.agents[1].visible_task_ids
        _wait(engine, "idle-wait")
        assert engine.state.agents[1].next_decision_at == start + 1200
        state = engine.state.model_copy(deep=True)
        state.agents[1].last_decision_time = start - 600
        _, verdicts = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
        assert "reconsideration_interval" not in verdicts[1].wake_reasons
        state.agents[1].visible_task_ids = {engine.scenario.tasks[0].task_id}
        _, verdicts = apply_decision_rhythm(engine_rhythm_inputs(engine, state))
        assert "reconsideration_interval" in verdicts[1].wake_reasons
    finally:
        engine.close()


def test_service_refused_pulse_at_most_once_per_second(engine_factory) -> None:
    """同一人物同一模拟秒至多施加一次 service_refused 脉冲；下一秒照常施加（拒绝的情绪代价）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.state.agents[1].body
    _invalid_start(engine, "pulse-1")
    once = engine.state.agents[1].body
    assert once == apply_mood_pulse(before, "service_refused")
    _invalid_start(engine, "pulse-2")
    assert engine.state.agents[1].body == once
    _wait(engine, "pulse-wait")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    later = engine.state.agents[1].body
    _invalid_start(engine, "pulse-3")
    assert engine.state.agents[1].body == apply_mood_pulse(later, "service_refused")
    assert same_moment.refusal_pulse_key("WORK_REQUIRES_REWORK") == "work_review_returned"
    assert same_moment.refusal_pulse_key("CATEGORY_PAIRING_NO_EFFECT") is None
    assert same_moment.refusal_pulse_key("CATEGORY_PAIRING_UNDEFINED") is None
    assert same_moment.refusal_pulse_key("RESOURCE_CONFLICT") == "service_refused"


def test_motivation_internal_survives_parameter_swap(engine_factory) -> None:
    """受挫度分母换为同刻上限后，有动机档案的人物观察仍产出 motivation_internal（「一·动机」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    assert any(item.agent_id == 1 and item.motivation is not None for item in engine.scenario.agents)
    _invalid_start(engine, "motivation-reject")
    assert engine.state.agents[1].consecutive_rejections == 1
    observed = engine.observe(context(engine, "motivation-observe"))
    assert observed["motivation_internal"] is not None
    assert observed["motivation_internal"]["candidates"]


def test_scenario_with_max_consecutive_rejections_is_rejected(tmp_path) -> None:
    """含旧键 max_consecutive_rejections 的场景按未知字段拒绝（场景参数）。"""
    document = _document("white-collar-12-weeks.json")
    assert "max_consecutive_rejections" not in document
    assert LifeScenario.model_validate(document).max_same_moment_decisions == 3
    document["max_consecutive_rejections"] = 10
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LifeScenario.model_validate(document)
    path = _write_scenario(document, tmp_path)
    with pytest.raises(IntegrityError, match="Scenario validation failed") as caught:
        load_scenario(path)
    assert "Extra inputs are not permitted" in str(caught.value.__cause__)
    assert "max_consecutive_rejections" in json.loads(path.read_text(encoding="utf-8"))


def test_same_moment_state_survives_reopen(engine_factory) -> None:
    """同刻轮数与受限轮阶段随世界状态保存，重开后在同一秒按原阶段继续（实现约定）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _reach_limit(engine)
    _invalid_start(engine, "reopen-restricted")
    saved = engine.state.agents[1]
    assert (saved.same_moment_rounds, saved.same_moment_stage) == (4, SameMomentStage.WAIT_ONLY)
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(_FORMAL, run_dir)
    try:
        agent = restored.state.agents[1]
        assert (agent.same_moment_rounds, agent.same_moment_stage) == (4, SameMomentStage.WAIT_ONLY)
        slept = _act(restored, "reopen-sleep", "start_activity", start_activity("睡觉", "自己"))
        assert slept.status == "rejected" and slept.error_code == _RESTRICTED
        assert _wait(restored, "reopen-wait").status == "in_progress"
        assert restored.state.agents[1].same_moment_rounds == 6
    finally:
        restored.close()


def test_restricted_stage_check_and_ladder_units() -> None:
    """受限轮复核与阶梯的逐阶段口径：open 全开，start_or_resume 开始/恢复/等待，wait_only 只等待（规则 3）。"""
    from worldmock.errors import BusinessRuleError

    for kind in ("start", "resume", "wait", "other"):
        same_moment.require_stage_action(SameMomentStage.OPEN, kind)
    for kind in ("start", "resume", "wait"):
        same_moment.require_stage_action(SameMomentStage.START_OR_RESUME, kind)
    same_moment.require_stage_action(SameMomentStage.WAIT_ONLY, "wait")
    for stage, kind in ((SameMomentStage.START_OR_RESUME, "other"), (SameMomentStage.WAIT_ONLY, "start"),
                        (SameMomentStage.WAIT_ONLY, "resume"), (SameMomentStage.CONCLUDE_ONLY, "wait")):
        with pytest.raises(BusinessRuleError, match=_RESTRICTED):
            same_moment.require_stage_action(stage, kind)
    assert same_moment.round_wake_reasons(active=True, rejected=False) == []
    assert same_moment.round_wake_reasons(active=False, rejected=True) == [
        "rejection_feedback", "no_active_activity"]


def test_restricted_observation_carries_stage_and_scene_note(engine_factory) -> None:
    """受限轮的观察私有证据带 same_moment_stage，scene 加一句不写次数的说明；未达上限时该句为空串（实现约定）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    opened = engine.observe(context(engine, "note-open"))
    assert opened["same_moment_stage"] == "open" and opened["scene"]["same_moment_note"] == ""
    assert SAME_MOMENT_NOTE not in render_scene(opened["scene"])
    _reach_limit(engine)
    restricted = engine.observe(context(engine, "note-restricted"))
    note = restricted["scene"]["same_moment_note"]
    assert restricted["same_moment_stage"] == "start_or_resume" and note == SAME_MOMENT_NOTE
    assert note and not any(char.isdigit() for char in note)
    assert note in render_scene(restricted["scene"])
    assert "same_moment_stage" not in restricted["scene"] and "same_moment_stage" not in restricted["facts"]


def test_start_or_resume_schema_matches_engine_dispatch(engine_factory) -> None:
    """同一受限轮观察下宿主 Schema 不列购买，绕过宿主的同一购买请求由引擎以 SAME_MOMENT_ACTION_RESTRICTED 拒绝（E5-3）。"""
    from tests.life.test_category_runtime import _stage_open_market
    from tests.life.test_grounded_observation import _prepare_agent

    engine = engine_factory()
    stage_day_plan(engine)
    _stage_open_market(engine)
    opened = engine.observe(context(engine, "schema-open"))
    target, label = next((row["object"], action["label"]) for row in opened["interaction_bindings"]
                         for action in row["actions"] if action["canonical_action"] == "purchase")
    assert label in dict(_start_enums(_prepare_agent(opened, context(engine, "x").observed_at)))["actions"]
    _reach_limit(engine)
    decision = context(engine, "schema-restricted")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert any(row["object"] == target for row in observed["interaction_bindings"])
    enums = dict(_start_enums(_prepare_agent(observed, decision.observed_at)))
    assert target not in enums["targets"] and label not in enums["actions"]
    assert "等待" in enums["actions"] and "自己" in enums["targets"]
    engine.submit_action(DirectToolCall(decision, "action-schema-bypass", "start_activity",
                                        start_activity(label, target)))
    result = engine.commit_decision_round("schema-restricted")[0]
    assert result.status == "rejected" and result.error_code == _RESTRICTED
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.WAIT_ONLY


def _start_enums(agent) -> list[tuple[str, list[str]]]:
    """读取宿主实际发给模型的 start_activity 对象与动作枚举。"""
    start = next(item["function"] for item in agent.build_react_tool_schemas(readonly=False)
                 if item["function"]["name"] == "start_activity")
    properties = start["parameters"]["properties"]
    return [(field, properties[field]["items"].get("enum", [])) for field in ("targets", "actions")]


def _person_name(engine: LifeEngine, observer: int, target: int) -> str:
    """@brief 读取 observer 在同一地点看到的另一位人物的可读名称。"""
    observed = engine.observe(context(engine, f"person-{observer}-{target}-{engine.state.version}", observer))
    return next(row["object"] for row in observed["interaction_bindings"]
                if row["kind"] == "person" and row["object_id"] == f"agent:{target}")


def _both_waiting(engine: LifeEngine, woken: tuple[int, ...]) -> int:
    """@brief 两人同秒开始等待，一分钟后用邮件只叫醒 woken 中的人物；返回叫醒时刻。"""
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {agent_id: ("start_activity", start_activity("等待", "自己"))
                          for agent_id in engine.state.agents}, "both-wait")
    for agent_id in woken:
        wake_by_mail(engine, agent_id=agent_id, offset=60)
    assert advance_until_ready(engine)
    assert sorted(engine.scheduling_state().ready_agent_ids) == sorted(woken)
    return engine.state.current_time


def _message_to(engine: LifeEngine, target: int, text: str) -> tuple[str, SendMessageActionRequest]:
    """@brief 人物 1 发给同地点人物 target 的消息请求（先取可读名称）。"""
    return "send_message", SendMessageActionRequest(recipient_name=_person_name(engine, 1, target), text=text)


def _running_session(engine: LifeEngine, agent_id: int) -> str:
    """@brief 人物唯一进行中活动所在会话的可读名称。"""
    return _session_name(engine, active_activities(engine.state, agent_id)[0].activity_id)


def _end_wait(engine: LifeEngine, agent_id: int) -> tuple[str, ConcludeActivityRequest]:
    """@brief 结束人物正在进行的等待（瞬时动作：没有进行中活动时同一秒再决策，才看得到可交谈的同伴）。"""
    return "conclude_activity", ConcludeActivityRequest(task=[_running_session(engine, agent_id)], summary=["不等了。"])


def test_awake_recipient_gets_same_second_opportunity(engine_factory) -> None:
    """清醒的收件人在消息送达的同一秒获得机会，原因为 message_arrival，并计入其同刻轮数（「人物之间的消息」）。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1,))
    _act(engine, "person-end-wait", *_end_wait(engine, 1))
    assert engine.state.agents[2].next_decision_at > now
    sent = _act(engine, "person-send", *_message_to(engine, 2, "晚上一起吃饭吗"))
    assert sent.status == "completed"
    recipient = engine.state.agents[2]
    assert recipient.next_decision_at == now and recipient.pending_wake_reasons == ["message_arrival"]
    assert recipient.same_moment_rounds == 0 and sorted(engine.scheduling_state().ready_agent_ids) == [1, 2]
    report = ReportStatusRequest(task=[_running_session(engine, 2)], summary=["看到消息了。"])
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")), 2: ("report_status", report)},
                 "person-read")
    assert _decision_reasons(engine, 2)[-1] == ["message_arrival"]
    assert engine.state.agents[2].same_moment_rounds == 1 and engine.state.current_time == now


def test_recipient_in_same_round_is_not_overwritten_by_its_own_success(engine_factory) -> None:
    """收件人与发件人同轮：本轮全部结算后才合并，收件人自己受理的排期不覆盖消息机会；原因按枚举顺序并入。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1, 2))
    submit_round(engine, {1: _end_wait(engine, 1), 2: _end_wait(engine, 2)}, "person-both-end")
    submit_round(engine, {1: _message_to(engine, 2, "在忙吗"), 2: ("start_activity", start_activity("等待", "自己"))},
                 "person-same-round")
    recipient = engine.state.agents[2]
    assert active_activities(engine.state, 2) and engine.state.current_time == now
    assert recipient.next_decision_at == now and recipient.pending_wake_reasons == ["message_arrival"]
    assert recipient.same_moment_rounds == 2 and 2 in engine.scheduling_state().ready_agent_ids
    staged = engine.state.model_copy(deep=True)
    staged.agents[2].pending_wake_reasons = ["no_active_activity"]
    merged = same_moment.merge_message_readiness(staged, [2], engine.scenario.max_same_moment_decisions)
    assert merged.agents[2].pending_wake_reasons == ["message_arrival", "no_active_activity"]


def test_recipient_at_limit_gets_message_at_next_boundary(engine_factory) -> None:
    """已达同刻上限的收件人不在同一秒获得机会；未读消息在下一边界以 message_arrival 提供，判定之后才记为已读。"""
    engine = engine_factory("multi-person.json")
    now = _both_waiting(engine, woken=(1,))
    _act(engine, "person-limit-end-wait", *_end_wait(engine, 1))
    _stage_agent(engine, 2, same_moment_rounds=engine.scenario.max_same_moment_decisions)
    before = engine.state.agents[2].next_decision_at
    _act(engine, "person-at-limit", *_message_to(engine, 2, "明天见"))
    recipient = engine.state.agents[2]
    message_id = next(iter(recipient.inbox))
    assert recipient.next_decision_at == before > now and message_id not in recipient.read_inbox_ids
    assert engine.scheduling_state().ready_agent_ids == (1,)
    _wait(engine, "person-limit-sender-wait")
    advance_next(engine)
    recipient = engine.state.agents[2]
    assert engine.state.current_time > now and 2 in engine.scheduling_state().ready_agent_ids
    assert "message_arrival" in recipient.pending_wake_reasons and message_id in recipient.read_inbox_ids


def test_sleeping_recipient_is_not_woken_by_person_message(engine_factory) -> None:
    """同轮开始睡觉的收件人不因人物消息在同一秒或其后的边界被唤醒（「人物之间的消息」，睡眠中过滤）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    now = engine.state.current_time
    submit_round(engine, {1: _message_to(engine, 2, "早点休息"),
                          2: ("start_activity", start_activity("睡觉", "自己"))}, "person-sleep")
    assert engine.state.agents[2].next_decision_at > now and engine.scheduling_state().ready_agent_ids == (1,)
    _wait(engine, "person-sleep-sender-wait")
    advance_next(engine)
    settlement = [event for event in engine.store.read_agent_events("environment_event", 2)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["decision_offered"] is False and settlement.data["wake_reasons"] == []
    assert 2 not in engine.scheduling_state().ready_agent_ids


@pytest.mark.asyncio
async def test_contact_reply_arrival_filtered_during_sleep(engine_factory) -> None:
    """联系人回复在 CONTACT_REPLY_DELAY_SECONDS 后送达收件箱并留送达记录；收件人在睡眠中不被唤醒（裁决 #21）。"""
    from tests.life.test_export_integration import _StubReplyClient, _send_message_to_model_contact
    from worldmock.runtime import settle_pending_model_replies

    engine = engine_factory()
    stage_day_plan(engine)
    _send_message_to_model_contact(engine, "reply-sleep")
    due = engine.state.current_time + CONTACT_REPLY_DELAY_SECONDS
    reply_id = (await settle_pending_model_replies(engine, client=_StubReplyClient()))[0]["reply_message_id"]
    assert _act(engine, "reply-sleep-start", "start_activity", start_activity("睡觉", "自己")).status == "in_progress"
    while engine.state.current_time < due:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    assert engine.state.current_time == due and reply_id in engine.state.agents[1].inbox
    assert engine.scheduling_state().ready_agent_ids == ()
    events = engine.store.read_agent_events("environment_event", 1)
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
    """同一秒各轮沿用该秒首轮的呈现窗口与轮转游标；时钟推进时留痕并入，游标才前进（「同一秒内的呈现」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now, offset = engine.state.current_time, engine._rotation_offset(1)
    first, _ = _observed_act(engine, "window-1", "start_activity", start_activity("观看", "不存在的对象"))
    second, _ = _observed_act(engine, "window-2", "start_activity", start_activity("观看", "不存在的对象"))
    assert engine.state.current_time == now
    assert second["facts"]["interactive_objects"] == first["facts"]["interactive_objects"]
    assert second["facts"]["products"] == first["facts"]["products"]
    assert engine._rotation_offset(1) == offset
    staged = engine.state.same_moment_presentation[1].stamps
    assert staged and set(staged.values()) == {now}
    _wait(engine, "window-wait")
    advance_next(engine)
    assert engine.state.same_moment_presentation == {}
    assert set(staged) <= set(engine.state.presentation_state[1])
    assert engine._rotation_offset(1) > offset


def test_browse_results_stay_pinned_in_later_same_second_rounds(engine_factory) -> None:
    """本秒首轮回灌的浏览结果在同一秒后续各轮继续置顶且可选；进度层截止点前移后也不丢（「同一秒内的呈现」）。"""
    from agentsociety2.agent.base import ModelCallEvidence
    from tests.life.test_commerce import _choice, _stage_open

    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _, browse = _observed_act(engine, "pin-browse", "start_activity",
                              _choice(engine.observe(context(engine, "pin-preview")), "browse"))
    assert browse.status == "in_progress"
    _finish(engine, browse.activity_id, "pin-browse")
    pins = [row["supply_id"] for row in engine.state.activities[browse.activity_id].data["revealed"]["items"]]
    decision = context(engine, "pin-first")
    engine.begin_decision_round([decision])
    order = _supply_order(engine.observe(decision))
    shown = [pin for pin in pins if pin in order]
    assert shown and order[:len(shown)] == shown
    # 宿主本轮发出含进度层的请求：之后的观察不再从进度层回灌这次浏览结果。
    request = {"model": "deepseek-flash", "messages": [{"role": "system", "content": ""},
                                                       {"role": "user", "content": '{"progress": {}}'}]}
    engine.record_model_exchange(ModelCallEvidence(decision, "pin-call", "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(decision, "pin-call", "response", 1, request,
                                                   response={"choices": []}))
    engine.submit_action(DirectToolCall(decision, "action-pin-first", "conclude_activity", ConcludeActivityRequest(
        task=[_session_name(engine, browse.activity_id)], summary=["看完了。"])))
    engine.commit_decision_round("pin-first")
    assert not any("browse_result" in row["result"]["data"] for row in engine._recent_action_results(1))
    second, _ = _observed_act(engine, "pin-second", "start_activity", start_activity("等待", "自己"))
    assert _supply_order(second) == order
    selectable = {row["object"]: row["actions"] for row in second["facts"]["interactive_objects"]}
    assert all(selectable.get(row["object"]) for row in second["interaction_bindings"] if row["object_id"] in shown)


def test_two_agents_same_round_keep_their_presentation_stamps(engine_factory) -> None:
    """两人同轮观察时各自的呈现留痕都保留，后观察者不覆盖前者；时钟推进后都并入各自的轮转凭据。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {agent_id: ("start_activity", start_activity("等待", "自己"))
                          for agent_id in engine.state.agents}, "stamps-both")
    staged = dict(engine.state.same_moment_presentation)
    assert set(staged) == {1, 2} and all(entry.stamps for entry in staged.values())
    advance_next(engine)
    assert engine.state.same_moment_presentation == {}
    for agent_id in (1, 2):
        assert set(staged[agent_id].stamps) <= set(engine.state.presentation_state[agent_id])


def test_presentation_staging_survives_reopen(engine_factory) -> None:
    """本秒的呈现暂存随世界状态保存；重开后同一秒仍沿用该秒首轮的呈现窗口（检查点封存，实现约定）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    first, _ = _observed_act(engine, "reopen-window-1", "start_activity", start_activity("观看", "不存在的对象"))
    staged = engine.state.same_moment_presentation[1]
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(_FORMAL, run_dir)
    try:
        assert restored.state.same_moment_presentation[1] == staged
        second, _ = _observed_act(restored, "reopen-window-2", "start_activity", start_activity("等待", "自己"))
        assert second["facts"]["interactive_objects"] == first["facts"]["interactive_objects"]
    finally:
        restored.close()
