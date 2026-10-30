"""真实单人生命周期的严格拒绝、只读回看与因果证据。"""

import logging
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agentsociety2.agent.base import ModelCallEvidence, MemoryUpdateEvidence
from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    MAIN_TOOL,
    SIDE_TOOL,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    pause_request,
    report_request,
    stage_day_plan,
    use_app,
)
from tests.life.environment_runtime import act, events
from worldmock.foundation.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, ReviseDayPlanRequest


@pytest.mark.parametrize("change,message", [
    ({"run_id": "another-run"}, "run or agent"),
    ({"agent_id": 999}, "run or agent"),
    ({"world_state_version": 999}, "world version"),
    ({"observed_at": "later"}, "observation time"),
])
def test_foreign_or_stale_context_cannot_open_or_change_a_real_run(engine_factory, change, message):
    engine = engine_factory()
    frozen = context(engine, "invalid")
    if change.get("observed_at") == "later":
        change = {"observed_at": frozen.observed_at + timedelta(seconds=1)}
    before = engine.context.state.model_dump(mode="json")
    count = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match=message):
        engine.begin_decision_round([replace(frozen, **change)])
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == count
    assert engine.decisions.round is None


def test_round_rejects_absent_duplicate_unscheduled_and_incomplete_participants(engine_factory):
    engine = engine_factory()
    frozen = context(engine, "round")
    for proposed in ([], [SimpleNamespace()], [frozen, frozen]):
        with pytest.raises(ProtocolError):
            engine.begin_decision_round(proposed)
    with pytest.raises(ProtocolError, match="scheduled agent order"):
        engine.decisions.begin_decision_round([frozen], ())
    engine.begin_decision_round([frozen])
    with pytest.raises(ProtocolError, match="cannot be started"):
        engine.begin_decision_round([frozen])
    with pytest.raises(ProtocolError, match="every expected agent"):
        engine.commit_decision_round("round")
    with pytest.raises(ProtocolError, match="not open"):
        engine.commit_decision_round("wrong")
    assert not events(engine, "action_result")
    assert not engine.context.state.activities
    engine.abort_decision_round("round", "test incomplete decision")
    assert len(events(engine, "run_failed")) == 1


@pytest.mark.parametrize("tool,payload", [
    ("query_world", do_at("家", "测试休息")),
    ("recall_task_summary", RecallTaskSummaryRequest(task_name="家")),
    ("unknown_tool", do_at("家", "测试休息")),
    ("pause_activity", do_at("家", "测试休息")),
    (SIDE_TOOL, do_at("家", "测试休息")),
    (MAIN_TOOL, use_app("哔哩哔哩")),
])
def test_only_typed_mutations_can_enter_the_real_action_queue(engine_factory, tool, payload):
    engine = engine_factory()
    frozen = context(engine, "typed")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    sequence = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError):
        engine.submit_action(DirectToolCall(frozen, f"{frozen.decision_id}:action:0001", tool, payload))
    assert engine.context.store.last_event_seq == sequence
    assert not engine.decisions.round.pending_actions


@pytest.mark.parametrize("tool,message", [
    ("query_world", "^Direct tool call request type does not match its tool$"),
    ("recall_task_summary", "^Read-only queries must use the immediate query channel$"),
])
def test_removed_query_world_cannot_be_registered_as_a_world_action(engine_factory, tool, message):
    """已删除的 query_world 落到「工具名没有对应的请求模型」，回看任务仍走只读通道；两种都不写事件、不入队。"""
    engine = engine_factory()
    frozen = context(engine, "removed")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    sequence = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match=message):
        engine.submit_action(DirectToolCall(frozen, f"{frozen.decision_id}:action:0001", tool,
                                            RecallTaskSummaryRequest(task_name="家")))
    assert engine.context.store.last_event_seq == sequence
    assert not engine.decisions.round.pending_actions


def test_duplicate_submission_reuses_receipt_and_commits_one_actual_activity(engine_factory, caplog):
    """同一开放轮里重复登记同一动作返回第一次的回执；同一身份换了参数是协议故障并写英文错误日志；只结算一项活动。"""
    engine = engine_factory()
    frozen = context(engine, "duplicate")
    request = do_at("家", "测试休息")
    same = f"{frozen.decision_id}:action:0001"
    call = DirectToolCall(frozen, same, MAIN_TOOL, request)
    with pytest.raises(ProtocolError, match="open decision round"):
        engine.submit_action(call)
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    first = engine.submit_action(call)
    assert engine.submit_action(call) == first
    assert len(engine.decisions.round.pending_actions) == 1
    caplog.set_level(logging.ERROR)
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(frozen, same, MAIN_TOOL, do_at("家", "测试闲聊")))
    assert f"Action identity {same} was reused with different parameters" in caplog.text
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(frozen, same, SIDE_TOOL, use_app("哔哩哔哩")))
    with pytest.raises(ProtocolError, match="already submitted"):
        engine.submit_action(DirectToolCall(frozen, f"{frozen.decision_id}:action:0002", MAIN_TOOL, request))
    with pytest.raises(ProtocolError, match="another decision round"):
        engine.submit_action(DirectToolCall(replace(frozen, round_id="foreign"), same, MAIN_TOOL, request))
    [result] = engine.commit_decision_round("duplicate")
    assert result.status == "in_progress"
    assert len(engine.context.state.activities) == 1
    assert len(events(engine, "action_requested")) == 1


@pytest.mark.parametrize("change", [
    {"phase": "invalid"}, {"attempt": 0}, {"call_id": ""}, {"call_id": 3},
])
def test_invalid_model_exchange_is_not_recorded_as_evidence(engine_factory, change):
    engine = engine_factory()
    frozen = context(engine, "exchange")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    evidence = ModelCallEvidence(frozen, "call", "request", 1, {"messages": []})
    count = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="phase or attempt"):
        engine.record_model_exchange(replace(evidence, **change))
    assert engine.context.store.last_event_seq == count
    assert not events(engine, "model_request")


def test_model_response_requires_one_request_and_keeps_its_actual_cause(engine_factory):
    engine = engine_factory()
    frozen = context(engine, "exchange")
    engine.begin_decision_round([frozen])
    engine.observe(frozen)
    request = ModelCallEvidence(frozen, "call", "request", 1, {"messages": []})
    response = replace(request, phase="response", response={"protocol_feedback": {"error_code": "TOOL_CALL_REQUIRED"}})
    with pytest.raises(ProtocolError, match="no recorded request"):
        engine.record_model_exchange(response)
    engine.record_model_exchange(request)
    with pytest.raises(ProtocolError, match="recorded twice"):
        engine.record_model_exchange(request)
    engine.record_model_exchange(response)
    [stored_request] = events(engine, "model_request")
    [stored_response] = events(engine, "model_response")
    assert stored_response.caused_by == [stored_request.event_id]
    assert stored_response.data["protocol_feedback"] == {"error_code": "TOOL_CALL_REQUIRED"}
    assert stored_request.data["request"] == {"messages": []}
    assert stored_response.data["request"] is None
    assert not engine.evidence.request_events
    assert not engine.context.state.activities


@pytest.mark.parametrize("claim", ["missing", "not-an-observation", "another-decision", "source-window"])
def test_memory_evidence_cannot_claim_another_observation_or_source_window(engine_factory, claim):
    """步尾登记的观察必须是本决策的那份观察事件：不存在的身份、别的事件种类、别的决策的观察，以及事实窗口不一致，
    都按协议违约拒绝且不留登记。"""
    engine = engine_factory()
    frozen = context(engine, "memory")
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)

    def requested() -> str:
        """同一人物、同一决策但不是观察的事件：本决策的行动请求。"""
        engine.submit_action(DirectToolCall(frozen, f"{frozen.decision_id}:action:0001", MAIN_TOOL,
                                            do_at("家", "测试休息")))
        return events(engine, "action_requested")[-1].event_id

    claims = {"missing": lambda: "missing-observation",
              "not-an-observation": requested,
              "another-decision": lambda: engine.observe(context(engine, "elsewhere"))["observation_event_id"],
              "source-window": lambda: observed["observation_event_id"]}
    evidence = MemoryUpdateEvidence(context=frozen, operation="refresh_summary", created_episode_ids=(),
        merged_episode_ids=(), episodes=(), memory_before_sha256="a" * 64, memory_sha256="b" * 64,
        episodes_sha256="c" * 64, state_sha256="d" * 64, consolidated=False,
        observation_event_id=claims[claim](),
        verified_event_ids=("not-visible",) if claim == "source-window" else ())
    with pytest.raises(ProtocolError, match="Memory update evidence is invalid"):
        engine.record_memory_update(evidence)
    assert not events(engine, "memory_updated")
    assert not engine.context.state.activities


def test_summary_refresh_is_recorded_without_a_life_transition_or_fabricated_write_point(engine_factory):
    engine = engine_factory()
    frozen = context(engine, "memory-summary")
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    before = engine.context.state.model_dump(mode="json")
    evidence = MemoryUpdateEvidence(context=frozen, operation="refresh_summary", created_episode_ids=(),
        merged_episode_ids=(), episodes=(), memory_before_sha256="a" * 64, memory_sha256="b" * 64,
        episodes_sha256="c" * 64, state_sha256="d" * 64, consolidated=False,
        observation_event_id=observed["observation_event_id"], verified_event_ids=())
    engine.record_memory_update(evidence)
    [recorded] = events(engine, "memory_updated")
    assert recorded.data["operation"] == "refresh_summary"
    assert recorded.data["trigger"] == []
    assert recorded.data["decision_id"] == frozen.decision_id
    assert engine.context.state.model_dump(mode="json") == before


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
    observed = engine.observe(context(engine, "recall"))
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
