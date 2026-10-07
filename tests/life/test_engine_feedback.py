"""人物可读动作的反馈、活动控制、业务拒绝与共同调度验收。"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import hashlib
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, wake_by_mail, start_request, pause_request, report_request)
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityStatus


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
    request = start_request(targets=[choice[0]["object"]], actions=[choice[1]["verb"]],
                                   ways=[choice[1]["label"]])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return observed, engine.commit_decision_round(round_id)[0]


def _lifecycle_records(engine, activity_id: str) -> list:
    """@brief 本人某项活动的生命周期记录（结束、暂停、恢复），按写入顺序。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.activity_id == activity_id
            and event.data.get("kind") in {"activity_concluded", "activity_paused", "activity_resumed"}]


def _request_event_id(engine, action_id: str) -> str:
    """@brief 某次动作的 action_requested 事件身份。"""
    return next(event.event_id for event in engine.context.store.read_agent_events("action_requested", 1)
                if event.action_id == action_id)


def test_pause_report_resume_and_conclude_name_the_activity(engine_factory):
    """暂停、播报恢复与结束都按活动名点名；每次改变写一条带活动快照、含本次请求事件的记录；
    结束时原始 start_activity 动作写入终态结果（第七章「播报与控制都作用于活动」，第八章「结束、暂停与恢复都留记录」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    for offset in (60, 120):
        wake_by_mail(engine, offset=offset)
    _, started = _start_visible_action(engine, "start-wait", "wait")
    assert started.status == "in_progress", started.error_code
    name = activity_name(engine, started.activity_id)

    assert advance_until_ready(engine)
    _, paused = _observed_action(engine, "pause-wait", "pause_activity",
                                 pause_request(name, summary="先处理来信"))
    assert paused.status == "completed", paused.error_code
    assert paused.data == {"activity_ids": [started.activity_id], "errands": []}
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status == ActivityStatus.PAUSED and activity.progress_note == "先处理来信"
    assert engine.scheduling_state().ready_agent_ids == (1,)  # 没有进行中活动，同一秒再决策

    _, reported = _observed_action(engine, "resume-wait", "report_status",
                                   report_request(name, summary="继续休息"))
    assert reported.status == "completed", reported.error_code
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status == ActivityStatus.ACTIVE and activity.progress_note == "继续休息"
    assert activity.report_anchor_at == engine.context.state.current_time
    assert activity.note_history[-1]["note"] == "先处理来信"

    assert advance_until_ready(engine)
    _, concluded = _observed_action(engine, "conclude-wait", "conclude_activity",
                                    conclude_request(name, summary="休息结束"))
    assert concluded.status == "completed", concluded.error_code
    now = engine.context.state.current_time
    activity = engine.context.state.activities[started.activity_id]
    assert (activity.status, activity.interruption_reason, activity.concluded_at) == (
        ActivityStatus.CANCELLED, "agent_cancelled", now)
    records = _lifecycle_records(engine, started.activity_id)
    assert [event.data["kind"] for event in records] == ["activity_paused", "activity_resumed", "activity_concluded"]
    for event, action_id in zip(records, ("action-pause-wait", "action-resume-wait", "action-conclude-wait")):
        assert event.action_id == action_id and _request_event_id(engine, action_id) in event.caused_by
        assert event.data["activity_id"] == event.data["activity"]["activity_id"] == started.activity_id
    assert [event.data["activity"]["status"] for event in records] == ["paused", "active", "cancelled"]
    assert records[-1].data["activity"]["concluded_at"] == now
    stored = engine.context.state.processed_actions["action-start-wait"].result
    assert stored["status"] == "cancelled" and stored["activity_id"] == started.activity_id
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == "action-start-wait"][-1]
    assert terminal.occurred_at == now and terminal.activity_id == started.activity_id
    assert terminal.data["status"] == "cancelled" and terminal.data["activity"]["concluded_at"] == now
    assert {_request_event_id(engine, "action-start-wait"),
            _request_event_id(engine, "action-conclude-wait")} <= set(terminal.caused_by)


def test_terminal_feedback_keeps_original_action_identity(engine_factory):
    """自然结束仍把终态回链到最初的 start_activity 动作。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = _start_visible_action(engine, "timed-wait", "wait")
    assert started.status == "in_progress", started.error_code
    while engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.COMPLETED
    assert engine.context.state.processed_actions["action-timed-wait"].result["status"] == "completed"
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == "action-timed-wait"][-1]
    requested = [event for event in engine.context.store.read_agent_events("action_requested", 1)
                 if event.action_id == "action-timed-wait"][0]
    assert terminal.activity_id == started.activity_id
    assert requested.event_id in terminal.caused_by


def test_business_rejection_is_reported_in_progress_and_run_continues(engine_factory):
    """对象不存在属于可恢复业务反馈；同一秒再决策，进度层公开原因、下一步、改法与逐项明细（时间与能量规格第一章规则 2、
    第五章；绕过宿主的名称类失败逐项写进 items，候选可以照填，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析 ）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.context.state.current_time
    _, rejected = _observed_action(
        engine, "unknown-object", "start_activity",
        start_request(targets=["不存在的对象"], actions=["interact"], ways=["进行"]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert engine.context.state.current_time == before
    assert engine.context.state.agents[1].next_decision_at == engine.context.state.current_time
    assert engine.scheduling_state().ready_agent_ids == (1,)
    decision = context(engine, "after-rejection")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    (call,) = [row for row in observed["progress_layer"]["events"] if row["type"] == "tool_call"]
    shown = call["result"]
    assert shown["error_code"] == "ACTION_REQUEST_INVALID" and shown["status"] == "rejected"
    [item] = shown["details"]["items"]
    assert shown["details"] == {
        "reason": "No known object named 不存在的对象 supports this way; changing spelling cannot create an object.",
        "next_step": "Choose a known object and an offered way.",
        "fix": "Use the published exact object name and offered way.", "items": [item]}
    assert (item["path"], item["written"], item["error_code"]) == (
        ["tool_calls", 0, "function", "arguments", "items", 0, "target"], "不存在的对象", "NAME_NOT_FOUND")
    assert item["candidates"] == []
    assert item["expected"] == "A known object name in the selected way's category is required."
    assert shown["message"] == shown["details"]["reason"]
    assert call["arguments"]["items"][0]["target"] == "不存在的对象"
    choice = next((row, action) for row in observed["interaction_bindings"]
                  for action in row["actions"] if action["canonical_action"] == "wait")
    request = start_request(targets=[choice[0]["object"]], actions=[choice[1]["verb"]],
                                   ways=[choice[1]["label"]])
    engine.submit_action(DirectToolCall(decision, "action-recovered", "start_activity", request))
    assert engine.commit_decision_round("after-rejection")[0].status == "in_progress"
    assert engine.context.state.current_time == before
    assert "consecutive_rejections" not in engine.context.state.agents[1].model_dump()
    assert engine.context.state.agents[1].same_moment_rounds == 2
    assert engine.context.state.agents[1].next_decision_at > engine.context.state.current_time


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
    staged = engine.context.state.model_copy(deep=True)
    staged.random_seed = seed
    staged.random_state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_random_seed", "random_seed": seed})
    engine.context.store.commit(staged.model_dump(mode="json"), [event])
    engine.context.state = staged


def test_seed_changes_competition_order_and_checkpoint_restores_it(engine_factory):
    """@brief 不同冻结种子改变同秒竞争顺序，共同检查点恢复后顺序一致。"""
    first = engine_factory("multi-person.json")
    second = engine_factory("multi-person.json")
    _stage_random_seed(first, 1)
    _stage_random_seed(second, 2)
    first_order = first.scheduling_state().ready_agent_ids
    second_order = second.scheduling_state().ready_agent_ids
    assert first_order == (2, 1) and second_order == (1, 2)
    snapshots = {f"agent:{agent_id}": first.context.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "seed_restore_test"}) for agent_id in first.context.state.agents}
    first.checkpoint(snapshots)
    run_dir = first.context.store.run_dir
    first.close()
    restored = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/multi-person.json", run_dir)
    try:
        assert restored.scheduling_state().ready_agent_ids == first_order
    finally:
        restored.close()
