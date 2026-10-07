"""@brief 工作主题以真实活动秒数推进；暂停、并行进食和自述都不能制造工作完成。"""

from __future__ import annotations

from dataclasses import fields

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import registered_world
from worldmock.catalog.models import WorkTopic
from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life import activities, sessions
from worldmock.life.activity_control import control_refusal, with_note
from worldmock.life.admission import Context
from worldmock.life.body import ActivityFactors, BodySettlement
from worldmock.life.models import (ActivityChange, ActivityKind, ActivityStartParams, ActivityState,
                                   ActivityStatus, AgentState, ChangeActivityRequest, WorldState)
from worldmock.life.work import (contract_view, validate_work_start, work_completion_draft, work_progress_data,
                                 work_resume_cap, work_start_refusal, work_start_seconds, work_totals)


def _work_world():
    """@brief 目录、场景和严格状态均来自真正的登记人工环境。"""
    semantics, state = registered_world(location="office")
    names = {identity: place.name for identity, place in semantics.catalog.locations.items()}
    state = sessions.open_stay(state, 1, names)
    topic = semantics.catalog.work_topics["topic-sales"]
    config = semantics.scenario.agents[0]
    validate_work_start(config, topic, state.agents[1].location_id)
    return semantics, state, topic, config


def _start(semantics, state, topic, identity="work"):
    """@brief 真实活动构造冻结已有累计，不创建独立任务进度状态。"""
    worked = work_totals(state, 1, topic.topic_id)
    params = ActivityStartParams(activity_type=ActivityKind.WORK, target_id=topic.topic_id,
                                 planned_duration_seconds=work_start_seconds(topic, worked))
    agent, activity = activities.start_activity(state.agents[1], params, state.current_time,
        activities.ActivityOrigin(identity, "decision", "request"), semantics.body)
    activity.data = {"topic_id": topic.topic_id, "worked_before_seconds": worked}
    changed = state.model_copy(deep=True)
    changed.agents[1], changed.activities[activity.activity_id] = agent, activity
    changed = sessions.assign(changed, sessions.current_session(changed, 1).session_id, [activity.activity_id])
    return WorldState.model_validate(changed.model_dump()), activity.activity_id


def _advance(semantics, state, activity_id, seconds):
    """@brief 生产身体与活动推进在同一区间结算并通过严格模型复核。"""
    before = state.activities[activity_id]
    target = state.current_time + seconds
    advanced = activities.advance_activity(before, state.agents[1], target, semantics.body)
    changed = state.model_copy(deep=True)
    changed.current_time = target
    changed.agents[1], changed.activities[activity_id] = advanced.agent, advanced.activity
    return WorldState.model_validate(changed.model_dump()), before


def test_assigned_topic_starts_at_workplace_without_clock_or_skill_gate():
    semantics, state, topic, config = _work_world()
    assert state.current_time < int(semantics.scenario.start_at.timestamp()) + 9 * 3600
    validate_work_start(config, topic, "office")
    for present in ("home", None):
        with pytest.raises(PreconditionError) as error:
            validate_work_start(config, topic, present)
        assert str(error.value) == "ACTIVITY_PRECONDITION_UNMET"
        assert error.value.unmet == ["required_location_id"]
    unassigned = config.model_copy(update={"work_topic_ids": ()})
    with pytest.raises(BusinessRuleError, match="WORK_TOPIC_NOT_FOUND"):
        validate_work_start(unassigned, topic, "office")


def test_topic_duration_is_actual_remaining_capped_at_one_day_and_minute_refusal():
    _, _, topic, _ = _work_world()
    assert work_start_seconds(topic, 9000) == 19800
    assert work_resume_cap(topic, 9000) == 19800
    large = WorkTopic(topic_id="long", topic="长期项目", target_hours=100.0)
    assert work_start_seconds(large, 0) == work_resume_cap(large, 0) == 86400
    assert work_start_refusal(topic, topic.target_seconds - 60) is None
    for worked in (topic.target_seconds - 59, topic.target_seconds, topic.target_seconds + 1):
        refusal = work_start_refusal(topic, worked)
        assert refusal.code == "ACTIVITY_DURATION_INVALID"
        assert refusal.reason == "Less than one minute of the topic remains; work cannot start."
        assert refusal.next_step == "Choose another activity."


def test_paused_thirty_minutes_do_not_increase_actual_topic_hours():
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, identity, 900)
    agent, paused = activities.change_activity(state.agents[1], state.activities[identity],
        ChangeActivityRequest(activity_id=identity, operation=ActivityChange.PAUSE), state.current_time, 900)
    state.agents[1], state.activities[identity] = agent, paused
    state, _ = _advance(semantics, state, identity, 1800)
    assert work_totals(state, 1, topic.topic_id) == 900
    assert state.activities[identity].elapsed_seconds == 900
    resumed_agent, resumed = activities.change_activity(state.agents[1], state.activities[identity],
        ChangeActivityRequest(activity_id=identity, operation=ActivityChange.RESUME,
                              resume_cap_seconds=work_resume_cap(topic, 900)), state.current_time, 900)
    state.agents[1], state.activities[identity] = resumed_agent, resumed
    state, _ = _advance(semantics, state, identity, 300)
    assert work_totals(state, 1, topic.topic_id) == 1200
    assert state.activities[identity].planned_end_at == state.current_time + topic.target_seconds - 1200


def test_parallel_nonwork_food_elapsed_and_failed_work_do_not_count():
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, identity, 900)
    meal = state.activities[identity].model_copy(update={"activity_id": "parallel-food", "kind": ActivityKind.CONSUME,
        "target_id": "beef-noodles", "resource_claims": {"consume"}, "origin_action_id": "food-action"}, deep=True)
    failed = state.activities[identity].model_copy(update={"activity_id": "failed-work", "status": ActivityStatus.FAILED,
        "ended_at": state.current_time, "origin_action_id": "failed-action"}, deep=True)
    state.activities.update({meal.activity_id: meal, failed.activity_id: failed})
    state = WorldState.model_validate(state.model_dump())
    assert work_totals(state, 1, topic.topic_id) == 900
    assert work_progress_data(state, state.activities[identity], topic) == {
        "topic_id": topic.topic_id, "worked_seconds": 900, "remaining_seconds": 27900}


def test_work_hours_are_not_discounted_by_sleep_energy_or_effects():
    semantics, state, topic, _ = _work_world()
    state.agents[1].body = state.agents[1].body.model_copy(update={"energy_kcal": 0, "sleep_deficit_hours": 24})
    state, identity = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, identity, 1800)
    assert work_totals(state, 1, topic.topic_id) == 1800
    assert state.agents[1].body.energy_kcal == 0
    assert "work_efficiency" not in {field.name for field in fields(BodySettlement)}
    assert {field.name for field in fields(ActivityFactors)} == {
        "met", "entertainment_relief_per_hour", "food_energy_kcal_per_hour"}


def test_contract_four_keys_and_reports_do_not_change_actual_hours_or_completion():
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, identity, 9000)
    before = state.model_dump()
    noted = with_note(state.activities[identity], "整理了表格", state.current_time, anchor=True)
    changed = with_note(noted, "已完成，进度百分之百", state.current_time, anchor=True)
    state.activities[identity] = changed
    assert work_totals(state, 1, topic.topic_id) == 9000
    assert changed.status == ActivityStatus.ACTIVE
    assert changed.progress_note == "已完成，进度百分之百"
    assert changed.note_history[-1]["note"] == "整理了表格"
    assert contract_view(topic, 9000) == {"topic": "季度销售数据整理", "target": "约 8 小时",
        "done": "约 2.5 小时", "remaining": "约 5.5 小时"}
    assert work_completion_draft(state, changed, topic, "progress") is None
    assert before["activities"][identity]["elapsed_seconds"] == state.activities[identity].elapsed_seconds
    restored = WorldState.model_validate_json(state.model_dump_json())
    assert restored.activities[identity].progress_note == changed.progress_note


def test_real_eight_hours_end_planned_and_emit_completion_once_with_actual_progress():
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    state, before = _advance(semantics, state, identity, topic.target_seconds)
    activity = state.activities[identity]
    assert activity.status == ActivityStatus.COMPLETED and activity.boundary_type == "planned_end"
    assert activity.elapsed_seconds == topic.target_seconds
    assert work_progress_data(state, activity, topic) == {
        "topic_id": topic.topic_id, "worked_seconds": 28800, "remaining_seconds": 0}
    event = work_completion_draft(state, before, topic, "actual-progress")
    assert event.data == {"kind": "work_topic_completed", "topic_id": "topic-sales", "worked_seconds": 28800}
    assert event.agent_id == 1 and event.visibility == "agent" and event.caused_by == ["actual-progress"]
    assert work_completion_draft(state, activity, topic, "later") is None
    assert work_start_refusal(topic, work_totals(state, 1, topic.topic_id)).code == "ACTIVITY_DURATION_INVALID"


def test_resume_refusals_follow_location_then_topic_then_slots_then_claims():
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, identity, topic.target_seconds - 59)
    agent, activity = activities.change_activity(state.agents[1], state.activities[identity],
        ChangeActivityRequest(activity_id=identity, operation="pause"), state.current_time, 900)
    state.agents[1], state.activities[identity] = agent, activity
    for action, kind in (("blocking-wait", ActivityKind.WAIT), ("blocking-work", ActivityKind.WORK)):
        params = ActivityStartParams(activity_type=kind, planned_duration_seconds=3600, target_id=topic.topic_id)
        person, blocking = activities.start_activity(state.agents[1], params, state.current_time,
            activities.ActivityOrigin(action, "decision", "request"), semantics.body)
        state.agents[1], state.activities[blocking.activity_id] = person, blocking
        state = sessions.assign(state, sessions.current_session(state, 1).session_id, [blocking.activity_id])
    names = {location_id: item.name for location_id, item in semantics.catalog.locations.items()}
    ctx = Context(state, 1, names, semantics.catalog.graph, {identity: "季度销售数据整理"}, semantics.catalog.work_topics)
    state = sessions.end_all(state)
    state.agents[1].location_id = "home"
    state = sessions.open_stay(state, 1, names)
    ctx = Context(state, 1, names, semantics.catalog.graph, {identity: "季度销售数据整理"}, semantics.catalog.work_topics)
    assert control_refusal(ctx, "report_status", activity).code == "ACTIVITY_PRECONDITION_UNMET"
    state = sessions.end_all(state)
    state.agents[1].location_id = "office"
    state = sessions.open_stay(state, 1, names)
    ctx = Context(state, 1, names, semantics.catalog.graph, {identity: "季度销售数据整理"}, semantics.catalog.work_topics)
    assert control_refusal(ctx, "report_status", activity).code == "ACTIVITY_DURATION_INVALID"


def test_old_work_state_and_request_fields_are_rejected_without_aliases():
    _, state, _, _ = _work_world()
    for field, value in (("paid_task_ids", []),):
        with pytest.raises(ValidationError):
            WorldState.model_validate({**state.model_dump(), field: value})
    for field in ("skill_levels", "visible_task_ids", "known_task_ids", "material_reveals"):
        with pytest.raises(ValidationError):
            AgentState.model_validate({**state.agents[1].model_dump(), field: {}})


def _engine_act(engine, name, tool, request):
    """@brief 所有集成请求经过真实观察、公开工具准入和事件提交。"""
    from agentsociety2.society import DirectToolCall
    from tests.life.conftest import context
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return engine.commit_decision_round(name)[0]


def _engine_until(engine, done, name):
    """@brief 只推进真实边界；中间决策播报在做的活动并收尾已结束活动。"""
    from tests.life.conftest import activity_name, advance_next, conclude_request, report_request, start_activity
    from worldmock.life.activity_control import pending_conclusions
    from worldmock.life.models import active_activities
    for index in range(256):
        if done():
            return
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        pending = pending_conclusions(engine.context.state, 1)
        running = active_activities(engine.context.state, 1)
        if pending:
            tool, request = "conclude_activity", conclude_request(
                *(activity_name(engine, item.activity_id) for item in pending))
        elif running:
            tool, request = "report_status", report_request(
                *(activity_name(engine, item.activity_id) for item in running), summary="继续处理。")
        else:
            tool, request = "start_activity", start_activity("等待", "自己")
        result = _engine_act(engine, f"{name}-{index}", tool, request)
        assert result.status in {"completed", "in_progress"}, result.error_code
    raise AssertionError("The actual engine did not reach the requested boundary")


def _engine_work(engine, name):
    """@brief 人物实际从家走到工作地点，再从冻结主题名称开始工作。"""
    from tests.life.conftest import activity_name, conclude_request, stage_day_plan, start_activity
    stage_day_plan(engine)
    config = engine.context.scenario.agents[0]
    workplace = engine.context.semantics.catalog.locations[config.work_schedule.workplace_location_id]
    trip = _engine_act(engine, f"{name}-commute", "start_activity", start_activity("步行", f"地点：{workplace.name}"))
    assert trip.status == "in_progress", trip.error_code
    _engine_until(engine, lambda: engine.context.state.activities[trip.activity_id].status != ActivityStatus.ACTIVE,
                  f"{name}-arrival")
    assert _engine_act(engine, f"{name}-arrived", "conclude_activity", conclude_request(
        activity_name(engine, trip.activity_id))).status == "completed"
    topic = engine.context.semantics.catalog.work_topics[config.work_topic_ids[0]]
    result = _engine_act(engine, f"{name}-start", "start_activity", start_activity("工作", f"工作：{topic.topic}"))
    assert result.status == "in_progress", result.error_code
    return topic, result.activity_id


def _engine_after(engine, seconds, name):
    """@brief 真实消息提供中途决策机会；经过时间来自引擎共同结算。"""
    from tests.life.conftest import wake_by_mail
    target = engine.context.state.current_time + seconds
    wake_by_mail(engine, offset=seconds)
    _engine_until(engine, lambda: engine.context.state.current_time == target, name)


def test_actual_engine_eight_hours_emit_own_completion_progress_and_remove_start_target(engine_factory):
    """@brief SQLite 留下完整真实工时、单次本人完成事件与进度层完成记录，完成后无法再次开工。"""
    from tests.life.conftest import context, start_activity
    engine = engine_factory()
    topic, identity = _engine_work(engine, "completion")
    started = engine.context.state.activities[identity]
    assert started.data == {"topic_id": topic.topic_id, "worked_before_seconds": 0}
    _engine_until(engine, lambda: engine.context.state.activities[identity].status != ActivityStatus.ACTIVE,
                  "completion-run")
    activity = engine.context.state.activities[identity]
    assert activity.status == ActivityStatus.COMPLETED and activity.boundary_type == "planned_end"
    assert work_totals(engine.context.state, 1, topic.topic_id) == activity.elapsed_seconds == 28800
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.activity_id == identity]
    assert progress and sum(event.data["interval_end"]-event.data["interval_start"] for event in progress) == 28800
    assert progress[-1].data["work"] == {"topic_id": topic.topic_id, "worked_seconds": 28800,
                                        "remaining_seconds": 0}
    completed = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                 if event.data.get("kind") == "work_topic_completed"]
    assert len(completed) == 1 and completed[0].agent_id == 1 and completed[0].visibility == "agent"
    assert completed[0].caused_by == [progress[-1].event_id]
    view = engine.observe(context(engine, "completion-view"))
    assert {"type": "work_completed", "topic": topic.topic} == {
        key: value for row in view["progress_layer"]["events"] if row["type"] == "work_completed"
        for key, value in row.items() if key != "occurred_at"}
    assert not [row for row in view["tool_layer"]["targets"] if row["kind"] == "task"]
    from tests.life.conftest import activity_name, conclude_request
    assert _engine_act(engine, "completion-conclude", "conclude_activity", conclude_request(
        activity_name(engine, identity))).status == "completed"
    restarted = _engine_act(engine, "completion-restart", "start_activity", start_activity("工作", f"工作：{topic.topic}"))
    assert restarted.status == "rejected" and restarted.error_code == "ACTIVITY_DURATION_INVALID"
    assert restarted.data["reason"] == "Less than one minute of the topic remains; work cannot start."


def test_actual_engine_pause_then_parallel_meal_only_counts_running_work(engine_factory):
    """@brief 暂停半小时不工作，恢复后与真正进食并行的时间只计一次工作且仍有身体消耗。"""
    from tests.life.conftest import activity_name, conclude_request, pause_request, report_request, start_activity
    def held_office_food(document):
        document["activity_feedback_seconds"] = 7200
        document["agents"][0]["initial_inventory"] = {"office": {"beef-noodles": 1}}
    engine = engine_factory(mutate=held_office_food)
    topic, identity = _engine_work(engine, "parallel")
    _engine_after(engine, 600, "parallel-first-work")
    name = activity_name(engine, identity)
    assert _engine_act(engine, "parallel-pause", "pause_activity", pause_request(name)).status == "completed"
    waiting = _engine_act(engine, "parallel-wait", "start_activity", start_activity("等待", "自己"))
    assert waiting.status == "in_progress"
    _engine_after(engine, 1800, "parallel-paused-time")
    assert work_totals(engine.context.state, 1, topic.topic_id) == 600
    assert _engine_act(engine, "parallel-wait-end", "conclude_activity", conclude_request(
        activity_name(engine, waiting.activity_id))).status == "completed"
    assert _engine_act(engine, "parallel-resume", "report_status", report_request(name)).status == "completed"
    _engine_after(engine, 60, "parallel-resumed")
    meal = _engine_act(engine, "parallel-meal", "start_activity", start_activity("食用", "持有物：牛肉面"))
    assert meal.status == "in_progress", meal.error_code
    before = work_totals(engine.context.state, 1, topic.topic_id)
    _engine_after(engine, 300, "parallel-eating")
    assert engine.context.state.activities[meal.activity_id].kind == ActivityKind.CONSUME
    assert engine.context.state.activities[meal.activity_id].elapsed_seconds == 300
    assert work_totals(engine.context.state, 1, topic.topic_id) == before+300
    assert engine.context.state.activities[identity].elapsed_seconds == 960


def test_resume_checks_slots_before_resource_claims_after_place_and_topic_are_satisfied():
    """@brief 真实活动快照的两项占用先报名额；腾出一个名额后才报告同类工作活动的资源冲突。"""
    semantics, state, topic, _ = _work_world()
    state, identity = _start(semantics, state, topic)
    person, paused = activities.change_activity(state.agents[1], state.activities[identity],
        ChangeActivityRequest(activity_id=identity, operation="pause"), state.current_time, 900)
    state.agents[1], state.activities[identity] = person, paused
    for action, kind in (("waiting", ActivityKind.WAIT), ("another-work", ActivityKind.WORK)):
        params = ActivityStartParams(activity_type=kind, planned_duration_seconds=3600, target_id=topic.topic_id)
        person, other = activities.start_activity(state.agents[1], params, state.current_time,
            activities.ActivityOrigin(action, "decision", "request"), semantics.body)
        state.agents[1], state.activities[other.activity_id] = person, other
        state = sessions.assign(state, sessions.current_session(state, 1).session_id, [other.activity_id])
    names = {location_id: item.name for location_id, item in semantics.catalog.locations.items()}
    context = Context(state, 1, names, semantics.catalog.graph, {}, semantics.catalog.work_topics)
    assert control_refusal(context, "report_status", paused).code == "SESSION_LIMIT_EXCEEDED"
    waiting = next(item for item in state.activities.values() if item.kind == ActivityKind.WAIT)
    state.activities[waiting.activity_id] = waiting.model_copy(update={"status": ActivityStatus.PAUSED})
    assert control_refusal(context, "report_status", paused).code == "RESOURCE_CONFLICT"


def test_actual_engine_old_paused_work_cannot_resume_after_another_activity_finishes_topic(engine_factory):
    """@brief 暂停旧工作后用另一活动完成同主题，旧活动恢复仍按累计工时拒绝，不能制造额外完成记录。"""
    from tests.life.conftest import activity_name, context, pause_request, report_request, start_activity
    engine = engine_factory()
    topic, first = _engine_work(engine, "stale")
    _engine_after(engine, 600, "stale-first-work")
    first_name = activity_name(engine, first)
    assert _engine_act(engine, "stale-pause", "pause_activity", pause_request(first_name)).status == "completed"
    second = _engine_act(engine, "stale-new-work", "start_activity", start_activity("工作", f"工作：{topic.topic}"))
    assert second.status == "in_progress" and engine.context.state.activities[second.activity_id].data == {
        "topic_id": topic.topic_id, "worked_before_seconds": 600}
    _engine_until(engine, lambda: engine.context.state.activities[second.activity_id].status != ActivityStatus.ACTIVE,
                  "stale-complete")
    assert work_totals(engine.context.state, 1, topic.topic_id) == 28800
    from tests.life.conftest import conclude_request
    assert _engine_act(engine, "stale-conclude-completed", "conclude_activity", conclude_request(
        activity_name(engine, second.activity_id))).status == "completed"
    refused = _engine_act(engine, "stale-resume", "report_status", report_request(first_name))
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_DURATION_INVALID"
    assert engine.context.state.activities[first].status == ActivityStatus.PAUSED
    completed = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                 if event.data.get("kind") == "work_topic_completed"]
    assert len(completed) == 1


def test_actual_engine_remaining_fifty_nine_seconds_refuses_start_and_resume(engine_factory):
    """@brief 经先前真实工作剩下五十九秒时，开始与恢复都使用主题不足一分钟的精确拒绝。"""
    from tests.life.conftest import activity_name, pause_request, report_request, start_activity
    engine = engine_factory()
    topic, identity = _engine_work(engine, "minute-boundary")
    _engine_after(engine, 28800-59, "minute-worked")
    assert work_totals(engine.context.state, 1, topic.topic_id) == 28741
    name = activity_name(engine, identity)
    assert _engine_act(engine, "minute-pause", "pause_activity", pause_request(name)).status == "completed"
    started = _engine_act(engine, "minute-start", "start_activity", start_activity("工作", f"工作：{topic.topic}"))
    assert started.status == "rejected" and started.error_code == "ACTIVITY_DURATION_INVALID"
    assert started.data["reason"] == "Less than one minute of the topic remains; work cannot start."
    resumed = _engine_act(engine, "minute-resume", "report_status", report_request(name))
    assert resumed.status == "rejected" and resumed.error_code == "ACTIVITY_DURATION_INVALID"
    assert engine.context.state.activities[identity].status == ActivityStatus.PAUSED


def test_actual_day_plan_inputs_use_topic_target_and_worked_hours_without_acceptance(engine_factory):
    """@brief 真正工作两个半小时后，计划只读输入取实际主题工时，自述完成不制造验收或改状态。"""
    import json
    from tests.life.conftest import activity_name, report_request
    from worldmock.life.day_plan_inputs import plan_inputs
    engine = engine_factory()
    topic, identity = _engine_work(engine, "plan-input")
    _engine_after(engine, 9000, "plan-input-hours")
    result = _engine_act(engine, "plan-input-report", "report_status", report_request(
        activity_name(engine, identity), summary="已完成全部工作并通过验收。"))
    assert result.status == "completed" and work_totals(engine.context.state, 1, topic.topic_id) == 9000
    before, sequence = engine.context.state.model_dump_json(), engine.context.store.last_event_seq
    payload = plan_inputs(engine.context, engine.observations, False, 1).payload
    assert payload["待办任务"] == [{"主题": "季度销售数据整理", "目标": "约 8 小时", "已做": "约 2.5 小时"}]
    results = json.dumps(payload["动作结果"], ensure_ascii=False)
    assert all(word not in results for word in ("验收", "提交成果", "工作报酬", "已完成全部工作"))
    assert engine.context.state.model_dump_json() == before and engine.context.store.last_event_seq == sequence


def test_actual_scheduled_payroll_transfers_fixed_salary_without_work_or_submission(engine_factory):
    """@brief 普通工资到期按原账本守恒入账，与工作主题完成、自述或提交无关。"""
    from tests.life.conftest import stage_day_plan
    from worldmock.scheduling.events import ScheduledKind
    engine = engine_factory()
    stage_day_plan(engine)
    [payroll] = [event for event in engine.context.state.pending_events if event.kind == ScheduledKind.PAYROLL]
    balances = dict(engine.context.state.accounts)
    _engine_until(engine, lambda: engine.context.state.current_time == payroll.due_at, "salary-time")
    amount, employer = payroll.data["amount_fen"], payroll.data["employer_account_id"]
    assert engine.context.state.accounts["agent:1"] == balances["agent:1"] + amount
    assert engine.context.state.accounts[employer] == balances[employer] - amount
    assert sum(engine.context.state.accounts.values()) == sum(balances.values())
    assert not [item for item in engine.context.state.activities.values() if item.kind == ActivityKind.WORK]
    [settled] = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                if event.data.get("kind") == "payroll" and event.data.get("payroll_id") == payroll.data["payroll_id"]]
    assert settled.data["payroll_paid"] is True and settled.occurred_at == payroll.due_at
    assert settled.data["resource_changes"] == {f"account:{employer}": -amount, "account:agent:1": amount}
