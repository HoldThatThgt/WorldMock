"""@brief 真实多数字人屏障唤醒、隔离沙盒、只读回看、检查点与因果证据合同。"""

from dataclasses import replace

import pytest

from tests.life.conftest import (
    MAIN_TOOL,
    SIDE_TOOL,
    advance_until_ready,
    conclude_request,
    do_at,
    pause_request,
    report_request,
    stage_day_plan,
    stub_day_session,
    use_app,
)
from tests.life.environment_runtime import act, events, observe_only
from worldmock.foundation.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, ReviseDayPlanRequest
from worldmock.state.model.exchange import ModelCallEvidence


def test_prepare_and_open_sandbox_reject_invalid_agents_versions_and_closed_sandbox(engine_factory):
    """未就绪或重复人物不能准备屏障；过时 version 或已封口沙盒均被协议拒绝且零事件写入。"""
    engine = engine_factory()
    before = engine.context.state.model_dump(mode="json")
    seq = engine.context.store.last_event_seq
    for bad_ids in ((), (1, 1), (999,)):
        with pytest.raises(ProtocolError):
            engine.prepare_barrier(bad_ids)
    [wakeup] = engine.prepare_barrier((1,))
    with pytest.raises(ProtocolError):
        engine.open_sandbox(wakeup.model_copy(update={"world_state_version": 999}))
    sandbox = engine.open_sandbox(wakeup)
    with pytest.raises(ProtocolError):
        sandbox.dispatch_world_tool("recall_task_summary", RecallTaskSummaryRequest(task_name="家"), "c-1")
    with pytest.raises(ProtocolError):
        sandbox.dispatch_world_tool("query_world", do_at("家", "测试休息"), "c-2")
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == seq


def test_commit_barrier_rejects_empty_duplicate_or_unready_outcomes(engine_factory):
    """commit_barrier 拒绝空列表、重复人物、未知人物与非就绪人物。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    outcome = sandbox.finish_wakeup(None, stub_day_session(wakeup))
    for bad in ([], [outcome, outcome], [outcome.model_copy(update={"agent_id": 999})]):
        with pytest.raises(ProtocolError):
            engine.commit_barrier(bad)
    assert not events(engine, "action_result")
    assert not engine.context.state.activities


def test_sandbox_model_exchange_enforces_request_pairing_and_ownership(engine_factory):
    """沙盒内模型响应/失败必须与先登记的请求摘要匹配且归属当前人物与唤醒。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    req = ModelCallEvidence(wakeup_id=wakeup.wakeup_id, agent_id=1, call_id="c1",
                            phase="request", attempt=1, request={"messages": []})
    resp = replace(req, phase="response", response={"protocol_feedback": {"error_code": "TOOL_CALL_REQUIRED"}})
    with pytest.raises(ProtocolError):
        sandbox.record_model_exchange(resp)
    with pytest.raises(ProtocolError):
        sandbox.record_model_exchange(replace(req, agent_id=999))
    sandbox.record_model_exchange(req)
    sandbox.record_model_exchange(resp)
    outcome = sandbox.finish_wakeup(None, stub_day_session(wakeup))
    engine.commit_barrier([outcome])
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_response.data["protocol_feedback"] == {"error_code": "TOOL_CALL_REQUIRED"}
    assert stored_request.data["request"] == {"messages": []}
    assert stored_response.data["request"] is None
    with pytest.raises(ProtocolError):
        sandbox.finish_wakeup(None, stub_day_session(wakeup))


def test_record_failed_evidence_flushes_model_drafts_without_advancing_world(engine_factory):
    """并发故障追记只写纯模型与唤醒草稿，不修改已提交世界版本且拒绝非证据类型。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    req = ModelCallEvidence(wakeup_id=wakeup.wakeup_id, agent_id=1, call_id="crash",
                            phase="request", attempt=1, request={"messages": [{"role": "user", "content": "hi"}]})
    sandbox.record_model_exchange(req)
    drafts = sandbox.drain_evidence()
    version_before = engine.context.state.version
    engine.record_failed_evidence(1, wakeup.wakeup_id, drafts)
    assert engine.context.state.version == version_before
    assert len(events(engine, "model_request")) == 1
    bad = EventDraft(event_type="action_result", occurred_at=wakeup.observed_at, agent_id=1,
                     wakeup_id=wakeup.wakeup_id, data={"status": "completed"})
    with pytest.raises(ProtocolError):
        engine.record_failed_evidence(1, wakeup.wakeup_id, [bad])


def test_dispatch_rejects_mismatched_tool_payload_type(engine_factory):
    """与工具名不匹配的 Typed 请求模型会被拒绝，沙盒状态保持不变。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    for tool, payload in ((SIDE_TOOL, do_at("家", "测试休息")), (MAIN_TOOL, use_app("哔哩哔哩"))):
        with pytest.raises(ProtocolError):
            sandbox.dispatch_world_tool(tool, payload, f"{wakeup.wakeup_id}:action:0001")


def test_incomplete_person_snapshot_cannot_publish_a_joint_checkpoint(engine_factory):
    engine = engine_factory()
    before = engine.context.store.last_event_seq
    with pytest.raises(IntegrityError, match="snapshot set is incomplete"):
        engine.checkpoint({})
    assert engine.context.store.last_event_seq == before
    assert not events(engine, "checkpoint_created")


def test_failure_is_one_durable_fact_and_empty_failure_is_rejected(engine_factory):
    engine = engine_factory()
    assert set(engine.meal_windows_by_agent()) == {1}
    with pytest.raises(ProtocolError, match="must not be empty"):
        engine.fail_run("")
    engine.fail_run("actual failure")
    engine.fail_run("a repeated failure")
    assert [failed.data["error"] for failed in events(engine, "run_failed")] == ["actual failure"]
    assert engine.context.state.current_time == int(engine.context.config.start_time.timestamp())


def test_activity_recall_is_readonly_and_returns_real_progress_and_note_history(engine_factory):
    engine = engine_factory(mutate=lambda persona, config: config.update(progress_report_interval_seconds=60))
    stage_day_plan(engine)
    _, started = act(engine, "app", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    _, report = act(engine, "reported", report_request(activity.name, summary="看了一段内容。"), "report_status")
    assert report.status == "completed"
    observed = observe_only(engine, "recall")
    before = engine.context.state.model_dump(mode="json")
    count = engine.context.store.last_event_seq
    recalled = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=activity.name), observed)
    assert recalled["recall"]["kind"] == "activity"
    assert recalled["recall"]["progress_note"] == "已在记忆层"
    assert recalled["recall"]["activity"] == "哔哩哔哩"
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == count
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        engine.read_task_summary(1, RecallTaskSummaryRequest(task_name="不存在的活动"), observed)


def test_pause_and_resume_preserve_elapsed_content_and_reject_an_occupied_second_slot(engine_factory):
    engine = engine_factory(mutate=lambda persona, config: config.update(progress_report_interval_seconds=60))
    stage_day_plan(engine)
    _, started = act(engine, "app", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    current = engine.context.state.activities[started.activity_id]
    elapsed = current.elapsed_seconds
    _, paused = act(engine, "paused", pause_request(current.name), "pause_activity")
    assert paused.status == "completed"
    _, other = act(engine, "other", use_app("抖音"))
    assert other.status == "in_progress"
    assert engine.context.state.activities[other.activity_id].resource_claims == {"second"}
    assert advance_until_ready(engine)
    _, blocked = act(engine, "blocked", report_request(current.name), "report_status")
    assert blocked.status == "rejected" and blocked.error_code == "RESOURCE_CONFLICT"
    assert engine.context.state.activities[started.activity_id].status == "paused"
    assert engine.context.state.activities[started.activity_id].elapsed_seconds == elapsed
    _, ended = act(engine, "ended", conclude_request(engine.context.state.activities[other.activity_id].name),
                   "conclude_activity")
    assert ended.status == "completed"
    _, resumed = act(engine, "resumed", report_request(current.name), "report_status")
    assert resumed.status == "completed"
    changed = engine.context.state.activities[started.activity_id]
    assert changed.status == "active" and changed.elapsed_seconds == elapsed
    assert changed.media_position_seconds == current.media_position_seconds


def test_unavailable_activity_and_missing_day_plan_are_business_refusals(engine_factory):
    engine = engine_factory()
    _, unknown = act(engine, "unknown", pause_request("不存在的活动"), "pause_activity")
    assert unknown.status == "rejected" and unknown.error_code == "ACTION_REQUEST_INVALID"
    _, no_plan = act(engine, "no-plan", ReviseDayPlanRequest(goals=[]), "revise_day_plan")
    assert no_plan.status == "rejected" and no_plan.error_code == "PLAN_NOT_FOUND"
    assert not engine.context.state.activities
    assert not events(engine, "run_failed")


def test_uncovered_search_recorder_failed_evidence_dispatcher_and_presentation_branches(engine_factory):
    """覆盖 SearchSessionEvidenceRecorder 传输重试、失败追记归属/空草稿/未知人物与 dispatch 控制分支。"""
    from tests.life.conftest import advance_next
    from worldmock.agents.search.search_session import _SearchEvidenceCollector
    from worldmock.simulation_engine.actions.start_group import ToolInvocation
    from worldmock.simulation_engine.evidence.model import validate_failed_evidence
    from worldmock.state.decisions.barrier import SearchTaskSpec

    spec = SearchTaskSpec(
        action_id="act-s", agent_id=1, activity_id="search-1", origin_wakeup_id="wake-s",
        query="咖啡", prefer=None, scoped_app_id=None, current_location_id="home",
        location_name="家", opened_at_clock="08:00", local_date="2026-01-05",
        weather_label="晴", open_apps=("美团",), category_overview={}, turn_budget=4,
    )
    rec = _SearchEvidenceCollector(spec)
    ev_req = ModelCallEvidence(wakeup_id="wake-s", agent_id=1, call_id="c-1",
                               phase="request", attempt=1, request={"messages": []})
    ev_res = replace(ev_req, phase="response", response={"choices": []},
                     transport_attempts=({"attempt": 1, "wait_seconds": 0.5},))
    rec.record(ev_req)
    rec.record(ev_res)
    assert rec.drafts[-1].data["transport_attempts"] == [{"attempt": 1, "wait_seconds": 0.5}]

    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    state = engine.context.state

    engine.record_failed_evidence(1, wakeup.wakeup_id, ())
    wrong_owner = EventDraft(event_type="model_request", occurred_at=wakeup.observed_at,
                             agent_id=2, wakeup_id=wakeup.wakeup_id, data={})
    with pytest.raises(ProtocolError, match="Failed evidence ownership mismatch"):
        validate_failed_evidence(1, wakeup.wakeup_id, [wrong_owner])
    with pytest.raises(ProtocolError, match="Failed evidence targets unknown agent"):
        engine.record_failed_evidence(999, wakeup.wakeup_id, ())

    obs = observe_only(engine, "disp")
    inv_ctl = ToolInvocation(1, wakeup.wakeup_id, f"{wakeup.wakeup_id}:action:0001",
                             "revise_day_plan", {"goals": []}, "a" * 64, "ev-ctl", obs, None)
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        engine.actions.dispatch(state, [inv_ctl, inv_ctl])
    bad_ctl = ToolInvocation(1, wakeup.wakeup_id, f"{wakeup.wakeup_id}:action:0002",
                             "revise_day_plan", {"goals": "bad"}, "b" * 64, "ev-bad", obs, None)
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        engine.actions.dispatch(state, [bad_ctl])

    _, started = act(engine, "rest", do_at("家", "测试休息"))
    while engine.context.state.activities[started.activity_id].status == "active":
        next_at = engine.scheduling_state().next_event_at
        if next_at is not None:
            advance_next(engine)
        else:
            break
    with pytest.raises(BusinessRuleError, match="ACTIVITY_CONCLUSION_REQUIRED"):
        engine.actions.dispatch(engine.context.state, [inv_ctl])
