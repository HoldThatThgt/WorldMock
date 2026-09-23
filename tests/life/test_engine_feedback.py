"""人物可读动作的反馈、会话控制、业务拒绝与共同调度验收。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan, wake_by_mail
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ActivityStatus, ConcludeActivityRequest, PauseActivityRequest, PendingAction,
    ReportStatusRequest, StartActivityRequest,
)


def _observed_action(engine, round_id: str, tool_name: str, request):
    """@brief 按真实顺序观察、登记并结算单人物动作。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool_name, request))
    return observed, engine.commit_decision_round(round_id)[0]


def _start_visible_action(engine, round_id: str, canonical_action: str):
    """@brief 从本轮冻结对象菜单选择人物可读的合法动作。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    choice = next((row, action) for row in observed["interaction_bindings"]
                  for action in row["actions"] if action["canonical_action"] == canonical_action)
    request = StartActivityRequest(targets=[choice[0]["object"]], actions=[choice[1]["label"]])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return observed, engine.commit_decision_round(round_id)[0]


def test_pause_report_resume_and_conclude_use_current_task_name(engine_factory):
    """暂停保留已投入时段，逐项播报恢复原活动，结束只触及选中会话。"""
    engine = engine_factory()
    stage_day_plan(engine)
    for offset in (60, 120, 180):
        wake_by_mail(engine, offset=offset)
    _, started = _start_visible_action(engine, "start-wait", "wait")
    assert started.status == "in_progress", started.error_code
    session = next(item for item in engine.state.sessions.values() if item.activity_ids == [started.activity_id])

    assert advance_until_ready(engine)
    _, paused = _observed_action(engine, "pause-wait", "pause_activity",
                                 PauseActivityRequest(task=[session.task_name], summary=["先处理来信"]))
    assert paused.status == "completed", paused.error_code
    assert engine.state.activities[started.activity_id].status == ActivityStatus.PAUSED
    assert engine.state.sessions[session.session_id].progress_note == "先处理来信"

    assert advance_until_ready(engine)
    _, reported = _observed_action(engine, "resume-wait", "report_status",
                                   ReportStatusRequest(task=[session.task_name], summary=["继续休息"]))
    assert reported.status == "completed", reported.error_code
    assert engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert engine.state.sessions[session.session_id].progress_note == "继续休息"

    assert advance_until_ready(engine)
    _, concluded = _observed_action(engine, "conclude-wait", "conclude_activity",
                                    ConcludeActivityRequest(task=[session.task_name],
                                                            summary=["休息结束"]))
    assert concluded.status == "completed", concluded.error_code
    assert engine.state.sessions[session.session_id].status == "concluded"
    assert engine.state.activities[started.activity_id].status == ActivityStatus.CANCELLED


def test_terminal_feedback_keeps_original_action_identity(engine_factory):
    """自然结束仍把终态回链到最初的 start_activity 动作。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = _start_visible_action(engine, "timed-wait", "wait")
    assert started.status == "in_progress", started.error_code
    while engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    assert engine.state.activities[started.activity_id].status == ActivityStatus.COMPLETED
    assert engine.state.processed_actions["action-timed-wait"].result["status"] == "completed"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == "action-timed-wait"][-1]
    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.action_id == "action-timed-wait"][0]
    assert terminal.activity_id == started.activity_id
    assert requested.event_id in terminal.caused_by


def test_business_rejection_is_reported_in_progress_and_run_continues(engine_factory):
    """对象不存在属于可恢复业务反馈；下一轮进度层公开原因。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, rejected = _observed_action(
        engine, "unknown-object", "start_activity",
        StartActivityRequest(targets=["不存在的对象"], actions=["观看"]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert engine.state.agents[1].next_decision_at == engine.state.current_time + 1
    advance_next(engine)
    decision = context(engine, "after-rejection")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert observed["facts"]["progress"]["results"][0]["error_code"] == "ACTION_REQUEST_INVALID"
    choice = next((row, action) for row in observed["interaction_bindings"]
                  for action in row["actions"] if action["canonical_action"] == "wait")
    request = StartActivityRequest(targets=[choice[0]["object"]], actions=[choice[1]["label"]])
    engine.submit_action(DirectToolCall(decision, "action-recovered", "start_activity", request))
    assert engine.commit_decision_round("after-rejection")[0].status == "in_progress"
    assert engine.state.agents[1].consecutive_rejections == 0


def test_tenth_consecutive_rejection_delays_retry_without_failing_run(engine_factory, monkeypatch):
    """连续业务拒绝进入冷却，人物仍可在下一决策边界选择。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    staged.agents[1].consecutive_rejections = engine.scenario.max_consecutive_rejections - 1
    pending = PendingAction(
        run_id=staged.run_id, round_id="reject-limit", decision_id="decision-reject-limit",
        action_id="action-reject-limit", agent_id=1, observed_at=staged.current_time,
        world_state_version=staged.version, order=0, tool_name="start_activity",
        request={}, request_sha256="0" * 64, requested_event_id="request-reject-limit")

    def reject(*_args):
        """@brief 在领域边界稳定产生一次业务拒绝。"""
        raise BusinessRuleError("ACTION_REQUEST_INVALID")

    monkeypatch.setattr(engine, "_dispatch", reject)
    changed, result, drafts = engine._resolve(staged, pending)
    assert result.status == "rejected"
    assert changed.agents[1].consecutive_rejections == engine.scenario.max_consecutive_rejections
    assert changed.agents[1].next_decision_at == (
        staged.current_time + engine.scenario.activity_feedback_seconds)
    assert [event.event_type for event in drafts] == ["action_rejected", "action_result"]


def test_contexts_must_follow_seeded_scheduling_order(engine_factory):
    """@brief 共同轮使用已公布的种子顺序，不能传入相反次序覆盖。"""
    engine = engine_factory("multi-person.json")
    scheduled = engine.scheduling_state().ready_agent_ids
    assert set(scheduled) == {1, 2}
    reversed_contexts = [context(engine, "wrong-order", agent_id) for agent_id in reversed(scheduled)]
    with pytest.raises(ProtocolError, match="scheduled agent order"):
        engine.begin_decision_round(reversed_contexts)


def _stage_random_seed(engine, seed: int) -> None:
    """@brief 把同一版本的竞争夹具绑定到一个明确随机种子。"""
    staged = engine.state.model_copy(deep=True)
    staged.random_seed = seed
    staged.random_state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_random_seed", "random_seed": seed})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def test_seed_changes_competition_order_and_checkpoint_restores_it(engine_factory):
    """@brief 不同冻结种子改变同秒竞争顺序，共同检查点恢复后顺序一致。"""
    first = engine_factory("multi-person.json")
    second = engine_factory("multi-person.json")
    _stage_random_seed(first, 1)
    _stage_random_seed(second, 2)
    first_order = first.scheduling_state().ready_agent_ids
    second_order = second.scheduling_state().ready_agent_ids
    assert first_order == (2, 1) and second_order == (1, 2)
    snapshots = {f"agent:{agent_id}": first.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "seed_restore_test"}) for agent_id in first.state.agents}
    first.checkpoint(snapshots)
    run_dir = first.store.run_dir
    first.close()
    restored = LifeEngine.open(Path("scenarios/multi-person.json"), run_dir)
    try:
        assert restored.scheduling_state().ready_agent_ids == first_order
    finally:
        restored.close()
