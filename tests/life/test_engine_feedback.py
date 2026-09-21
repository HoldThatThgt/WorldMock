"""活动反馈、反应时钟、到期事件与生命周期闭环测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, wake_by_mail
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ActivityStatus,
    ContinueActivityRequest,
    GameActionRequest,
    PlanDayRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)


def _goals_with_wake_events(engine, offsets: tuple[int, ...]) -> PlanDayRequest:
    """登记空目标前排定真实邮件事件，让活动控制仍在精确反馈边界发生。"""
    for offset in offsets:
        wake_by_mail(engine, offset=offset)
    return PlanDayRequest(goals=())


def _observed_action(engine, round_id: str, tool_name: str, request):
    """按真实顺序观察、登记并结算单人物动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    action_id = f"action-{round_id}"
    engine.submit_action(DirectToolCall(ctx, action_id, tool_name, request))
    return observation, engine.commit_decision_round(round_id)[0]


def _stage_time(engine, epoch: int, pending_events=None) -> None:
    """为专项边界把隔离夹具移到明确时刻，不改变账户或生产目录。"""
    staged = engine.state.model_copy(deep=True)
    staged.current_time = epoch
    staged.pending_events = list(staged.pending_events if pending_events is None else pending_events)
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(
            update={"next_decision_at": epoch, "body_settled_at": epoch}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=epoch, visibility="system",
                       source_kind="scenario", data={"kind": "test_clock_position"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def test_continue_feedback_keeps_slot_and_terminal_result_links_origin(engine_factory) -> None:
    """长活动在唤醒点显式继续，最终结果回链最初动作且区间不重复。

    静默反馈边界不自动提供决策；真实邮件到达提供继续决策的机会。
    """
    engine = engine_factory()
    started = engine.state.current_time
    _observed_action(engine, "plan", "plan_day",
                     _goals_with_wake_events(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    assert engine.state.current_time == started + 900
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=2000)
    _, result = _observed_action(engine, "long-wait", "start_basic_activity", request)
    activity_id = result.activity_id
    assert engine.state.agents[1].next_decision_at == started + 1800

    advance_next(engine)
    assert engine.state.activities[activity_id].elapsed_seconds == 900
    first = ContinueActivityRequest(activity_id=activity_id)
    before = engine.state.current_time
    observation, result = _observed_action(engine, "continue-one", "continue_activity", first)
    assert observation["active_activities"][0]["elapsed_seconds"] == 900
    assert engine.state.current_time == before
    assert result.status == "completed"
    assert engine.state.activities[activity_id].status == ActivityStatus.ACTIVE

    advance_next(engine)
    _observed_action(engine, "continue-two", "continue_activity", first)
    advance_next(engine)
    activity = engine.state.activities[activity_id]
    assert activity.status == "completed" and activity.elapsed_seconds == 2000
    assert engine.state.processed_actions["action-long-wait"].result["status"] == "completed"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == "action-long-wait"][-1]
    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.action_id == "action-long-wait"][0]
    assert terminal.activity_id == activity_id
    assert terminal.decision_id == "decision-long-wait-1"
    assert requested.event_id in terminal.caused_by


def test_feedback_boundary_compatible_watch_runs_alongside_wait(
    engine_factory,
) -> None:
    """唤醒边界直接提交观看时，等待保留自己的进度并同步继续。"""
    engine = engine_factory()
    _observed_action(engine, "plan", "plan_day",
                     _goals_with_wake_events(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    _, started = _observed_action(
        engine, "switch-wait", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=2700))
    wait_id = started.activity_id
    advance_next(engine)

    observation, switched = _observed_action(
        engine, "switch-watch", "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="watch", target_id="sintel-en-subtitles",
            planned_duration_seconds=600, playback_speed=1))
    watch_id = switched.activity_id
    assert switched.status == "in_progress"
    assert observation["active_activities"][0]["activity_id"] == wait_id
    parallel_wait = engine.state.activities[wait_id]
    assert parallel_wait.status == "active" and parallel_wait.elapsed_seconds == 900
    assert "auto_paused_activity" not in switched.data
    watch = engine.state.activities[watch_id]
    assert watch.kind.value == "watch" and watch.status == ActivityStatus.ACTIVE

    while engine.state.activities[watch_id].status == ActivityStatus.ACTIVE:
        advance_until_ready(engine)
    finished_watch = engine.state.activities[watch_id]
    assert finished_watch.status == ActivityStatus.COMPLETED
    assert finished_watch.elapsed_seconds == 600
    assert finished_watch.media_position_seconds == 600
    assert engine.state.activities[wait_id].status == "active"
    _observed_action(engine, "switch-continue", "continue_activity",
                     ContinueActivityRequest(activity_id=wait_id))
    assert engine.state.activities[wait_id].status == ActivityStatus.ACTIVE
    assert engine.state.activities[wait_id].elapsed_seconds > 900

    while engine.state.activities[wait_id].status == ActivityStatus.ACTIVE:
        advance_until_ready(engine)
        if engine.state.activities[wait_id].status == ActivityStatus.ACTIVE:
            _observed_action(
                engine, f"switch-continue-{engine.state.current_time}",
                "continue_activity", ContinueActivityRequest(activity_id=wait_id))
    final_wait = engine.state.activities[wait_id]
    assert final_wait.status == ActivityStatus.COMPLETED
    assert final_wait.elapsed_seconds == 2700


def test_game_feedback_boundary_rejects_resource_conflict(engine_factory) -> None:
    """游戏占用位置，直接提交等待被资源冲突拒绝。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine, "game-start", "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="play_game", target_id="textworld-1201",
            planned_duration_seconds=3600, playback_speed=1))
    assert started.status == "in_progress"
    advance_next(engine)
    _, rejected = _observed_action(
        engine, "game-switch", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
    assert rejected.status == "rejected"
    assert rejected.error_code == "RESOURCE_CONFLICT"
    assert engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE


def test_pause_resume_and_cancel_preserve_progress_and_origin_result(engine_factory) -> None:
    """暂停不结算未来区间，恢复后继续，取消终结最初动作并保留已完成进度。

    暂停、恢复、取消都发生在真实邮件到达的唤醒边界。
    """
    engine = engine_factory()
    _observed_action(engine, "plan", "plan_day",
                     _goals_with_wake_events(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    _, started = _observed_action(
        engine, "pausable", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=2700))
    activity_id = started.activity_id
    advance_next(engine)
    pause = SetActivityStateRequest(activity_id=activity_id, operation="pause")
    _observed_action(engine, "pause", "set_activity_state", pause)
    assert engine.state.activities[activity_id].status == "paused"
    assert engine.state.activities[activity_id].elapsed_seconds == 900

    advance_until_ready(engine)
    resume = SetActivityStateRequest(activity_id=activity_id, operation="resume")
    _observed_action(engine, "resume", "set_activity_state", resume)
    advance_until_ready(engine)
    cancel = SetActivityStateRequest(activity_id=activity_id, operation="cancel")
    _observed_action(engine, "cancel", "set_activity_state", cancel)
    activity = engine.state.activities[activity_id]
    assert activity.status == "cancelled" and activity.elapsed_seconds == 1800
    assert engine.state.processed_actions["action-pausable"].result["status"] == "cancelled"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == "action-pausable"][-1]
    cancel_request = [event for event in engine.store.read_agent_events("action_requested", 1)
                      if event.action_id == "action-cancel"][0]
    assert cancel_request.event_id in terminal.caused_by


def test_rejection_waits_one_second_and_next_observation_reports_once(engine_factory) -> None:
    """业务拒绝经一秒边界再反馈，人物观察到稳定原因且下一观察不重复旧结果。"""
    engine = engine_factory()
    now = engine.state.current_time
    _, rejected = _observed_action(
        engine, "missing-content", "start_targeted_activity",
        StartTargetedActivityRequest(
            activity_type="watch", target_id="missing-content",
            planned_duration_seconds=60, playback_speed=1))
    assert rejected.status == "rejected" and rejected.error_code == "CONTENT_NOT_FOUND"
    assert engine.state.agents[1].next_decision_at == now + 1
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)

    ctx = context(engine, "after-reject")
    engine.begin_decision_round([ctx])
    first = engine.observe(ctx)
    assert first["observation_event_id"]
    assert [(item["action_id"], item["error_code"]) for item in first["recent_action_results"]] == [
        ("action-missing-content", "CONTENT_NOT_FOUND")]
    assert first["facts"]["recent_action_results"][0]["error_code"] == "CONTENT_NOT_FOUND"
    repeated = engine.observe(ctx)
    assert repeated["recent_action_results"] == []
    assert repeated["facts"]["recent_action_results"] == []
    assert repeated["scene"]["recent_action_results"] == []
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    engine.submit_action(DirectToolCall(ctx, "recovered", "start_basic_activity", request))
    engine.commit_decision_round("after-reject")
    assert engine.state.agents[1].consecutive_rejections == 0


def test_tenth_consecutive_rejection_fails_agent_and_run(engine_factory) -> None:
    """连续拒绝预算耗尽形成失败结果和运行失败，不替人物选择任何活动。"""
    engine = engine_factory()
    result = None
    for index in range(10):
        _, result = _observed_action(
            engine, f"reject-{index}", "start_targeted_activity",
            StartTargetedActivityRequest(
                activity_type="watch", target_id="missing-content",
                planned_duration_seconds=60, playback_speed=1))
        if index < 9:
            assert result.status == "rejected"
            advance_next(engine)
    assert result.status == "failed"
    assert engine.state.agents[1].decision_failed is True
    assert engine.state.activities == {}
    assert engine.scheduling_state().ready_agent_ids == ()
    failures = [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.event_type == "run_failed"]
    assert len(failures) == 1 and failures[0].data["error_type"] == "DecisionFailure"


def test_new_game_waits_one_second_before_first_real_command(engine_factory) -> None:
    """创建真实 TextWorld 会话后首个命令只能在下一秒执行，不能同秒免费连用。"""
    engine = engine_factory()
    now = engine.state.current_time
    _, result = _observed_action(
        engine, "game-start", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="play_game", target_id="textworld-1201",
                                     planned_duration_seconds=3600, playback_speed=1))
    activity = engine.state.activities[result.activity_id]
    assert activity.next_interaction_at == now + 1
    assert engine.state.agents[1].next_decision_at == now + 1
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)
    progress = next(iter(engine.state.agents[1].game_sessions.values()))
    request = GameActionRequest(game_session_id=progress.session_id,
                                command=progress.admissible_commands[0])
    _, outcome = _observed_action(engine, "game-command", "game_action", request)
    assert outcome.status == "completed" and outcome.data["game"]["moves"] == 1


def test_task_expiry_advances_during_sleep_without_forcing_wake(engine_factory) -> None:
    """睡眠跨过任务截止时只处理一次到期事实，人物仍睡到计划结束。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    expiry = next(event for event in engine.state.pending_events
                  if event.scheduled_id == f"expired-{task.task_id}")
    _stage_time(engine, due - 600, [expiry])
    _, result = _observed_action(
        engine, "sleep-over-due", "start_basic_activity",
        StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=3600))
    activity_id = result.activity_id
    advance_next(engine)
    assert engine.state.current_time == due
    assert engine.state.activities[activity_id].status == "active"
    assert engine.scheduling_state().ready_agent_ids == ()
    expired = [event for event in engine.store.read_events(engine.store.last_event_seq)
               if event.data.get("kind") == "task_expired"]
    assert len(expired) == 1 and expired[0].data["task_id"] == task.task_id
    advance_next(engine)
    assert engine.state.activities[activity_id].status == "completed"
    assert len([event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.data.get("kind") == "task_expired"]) == 1


def test_paused_activity_is_truncated_at_end_with_original_action_identity(engine_factory) -> None:
    """实验终点截断暂停活动，并把终态写回最初动作而非暂停控制动作。"""
    engine = engine_factory()
    end = engine.state.end_time
    _stage_time(engine, end - 2000, [])
    _observed_action(engine, "end-plan", "plan_day",
                     _goals_with_wake_events(engine, (1000, 1200, 1400, 1450, 1550)))
    advance_next(engine)
    _, started = _observed_action(
        engine, "end-wait", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=1800))
    activity_id = started.activity_id
    advance_until_ready(engine)
    _observed_action(
        engine, "end-pause", "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    advance_until_ready(engine)
    remaining = end - engine.state.current_time
    _observed_action(
        engine, "end-sleep", "start_basic_activity",
        StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=remaining))
    while engine.scheduling_state().next_event_at is not None:
        advance_next(engine)  # 睡眠期间的静默 tick 一路推进到实验终点
    activity = engine.state.activities[activity_id]
    assert activity.status == "truncated" and activity.ended_at == end
    assert activity.interruption_reason == "experiment_end"
    assert activity.elapsed_seconds == 200
    stored = engine.state.processed_actions["action-end-wait"].result
    assert stored["status"] == "cancelled"
    assert stored["data"]["activity"]["interruption_reason"] == "experiment_end"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == "action-end-wait"][-1]
    assert terminal.occurred_at == end and terminal.activity_id == activity_id
    assert terminal.data["activity"]["interruption_reason"] == "experiment_end"


def test_contexts_must_follow_seeded_scheduling_order(engine_factory) -> None:
    """共同轮使用已公布的种子顺序，不能由调用方传入相反次序覆盖。"""
    engine = engine_factory("multi-person.json")
    scheduled = engine.scheduling_state().ready_agent_ids
    assert set(scheduled) == {1, 2}
    reversed_contexts = [context(engine, "wrong-order", agent_id) for agent_id in reversed(scheduled)]
    with pytest.raises(ProtocolError, match="scheduled agent order"):
        engine.begin_decision_round(reversed_contexts)


def _stage_random_seed(engine, seed: int) -> None:
    """把同一版本的竞争夹具绑定到一个明确随机种子。"""
    staged = engine.state.model_copy(deep=True)
    staged.random_seed = seed
    staged.random_state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_random_seed", "random_seed": seed})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def test_seed_changes_competition_order_and_checkpoint_restores_it(engine_factory) -> None:
    """不同冻结种子可改变同秒竞争顺序，共同检查点恢复后顺序完全一致。"""
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
