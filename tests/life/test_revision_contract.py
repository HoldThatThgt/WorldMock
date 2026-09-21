"""《人物真实性修订合同（2026-09-18）》引擎迁移验收：决策节律、观察投影与
社交/日程/多件结算的正反例。基线工厂与 wake_by_mail 见 conftest。"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    advance_next,
    advance_until_ready,
    browse_activities,
    context,
    wake_by_mail,
)
from worldmock.life import projection
from worldmock.life.models import (
    ActivityKind,
    ContinueActivityRequest,
    PlanDayRequest,
    PlanGoalInput,
    ReviseDayPlanRequest,
    SendMessageRequest,
    StartBasicActivityRequest,
    StartSocialActivityRequest,
    StartTargetedActivityRequest,
    WorldState,
)
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS
from worldmock.life.scenario import load_scenario


def _act(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """单人物真实轮：观察、登记并结算。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _commit_staged(engine, staged) -> None:
    """把测试改造过的状态经存储落盘（保持版本连续），再换入引擎。"""
    from worldmock.footprints.models import EventDraft

    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_state_staged"})])
    engine.state = staged


def _round_ready(engine, round_id, tool, request, agent_id=1):
    """向当前就绪的每个人物提交一轮（其他人物等待 60 秒），返回主角结果。"""
    from tests.life.conftest import submit_round

    ready = list(engine.scheduling_state().ready_agent_ids)
    requests = {}
    for rid in ready:
        requests[rid] = (tool, request) if rid == agent_id else (
            "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
    _, results = submit_round(engine, requests, round_id)
    return next(item for item in results if item.agent_id == agent_id)


def _events(engine, event_type: str, agent_id: int = 1):
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == event_type and event.agent_id == agent_id]


def _body_events(engine):
    """读取本人唯一的共同身体结算证据。"""
    return [event for event in _events(engine, "environment_event")
            if event.agent_id == 1 and event.data.get("kind") == "body_settlement"]


def test_silent_boundary_settles_without_decision_events(engine_factory) -> None:
    """静默边界照常结算（activity_progress + decision_offered=false）且不产生决策事件。"""
    engine = engine_factory()
    _act(engine, "wait-long", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=7200))
    boundary = engine.state.current_time + 900
    advance_next(engine)  # 首个 900 边界：无唤醒条件即静默
    assert not engine.scheduling_state().ready_agent_ids
    progress = [event for event in _events(engine, "activity_progress") if event.agent_id == 1]
    assert progress, "settlement boundary must leave activity_progress evidence"
    assert _body_events(engine)[-1].data["decision_offered"] is False
    assert _body_events(engine)[-1].data["wake_reasons"] == []
    for event_type in ("decision_started", "observation", "model_request", "memory_updated"):
        at_boundary = [event for event in _events(engine, event_type)
                       if event.occurred_at >= boundary]
        assert at_boundary == []


def test_offered_boundary_records_actual_wake_reasons(engine_factory) -> None:
    """唤醒命中的边界记录 decision_offered=true 与实际唤醒原因。"""
    engine = engine_factory()
    _act(engine, "wait-short", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=900))
    advance_next(engine)  # 计划结束唤醒
    assert engine.scheduling_state().ready_agent_ids == (1,)
    progress = [event for event in _events(engine, "activity_progress")][-1]
    assert _body_events(engine)[-1].data["decision_offered"] is True
    assert "planned_end" in _body_events(engine)[-1].data["wake_reasons"]


def test_engine_wake_reasons_cover_each_trigger_class(engine_factory) -> None:
    """每类唤醒条件经引擎真实触发一次（run_start/消息/配送/重估/拒绝）。"""
    engine = engine_factory()
    _act(engine, "plan", "plan_day",
         _goal_plan())
    first = _events(engine, "decision_started")
    assert first and first[-1].data["wake_reasons"] == ["run_start"]
    advance_until_ready(engine)
    _act(engine, "hold-1", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    # message_arrival：邮件到达唤醒
    wake_by_mail(engine)
    advance_next(engine)
    assert "message_arrival" in engine.state.agents[1].pending_wake_reasons
    _act(engine, "hold-2", "continue_activity",
         ContinueActivityRequest(activity_id=next(item.activity_id for item in
                                 engine.state.activities.values() if item.status.value == "active")))
    # delivery_arrival：配送到达唤醒
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": []}, deep=True)
    from worldmock.life.models import DeliveryState, FulfillmentKind
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind
    order = DeliveryState(order_id="order-wake", agent_id=1, product_id="food-x",
                          quantity=1, destination_id="home",
                          fulfillment=FulfillmentKind.DELIVERY,
                          created_at=staged.current_time,
                          expected_at=staged.current_time + 60, status="pending",
                          total_price_fen=100, supply_id="supply-wake", location_id="market",
                          seller_account_id="merchant:grocery", price_version="v1", route_id=None, source={})
    staged.deliveries[order.order_id] = order
    staged.pending_events.append(ScheduledEvent(
        scheduled_id="delivery-wake", kind=ScheduledKind.DELIVERY_ARRIVAL,
        due_at=order.expected_at, sequence=900, agent_id=1,
        data={"order_ids": [order.order_id]}))
    _commit_staged(engine, staged)
    advance_next(engine)
    assert "delivery_arrival" in engine.state.agents[1].pending_wake_reasons
    _act(engine, "hold-3", "continue_activity",
         ContinueActivityRequest(activity_id=next(item.activity_id for item in
                                 engine.state.activities.values() if item.status.value == "active")))
    # reconsideration_interval：距上次决策满 5400 秒
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": [], "last_decision_time":
                staged.current_time - 5400}, deep=True)
    _commit_staged(engine, staged)
    advance_next(engine)
    assert "reconsideration_interval" in engine.state.agents[1].pending_wake_reasons
    # rejection_feedback：业务拒绝后一秒边界
    _act(engine, "reject", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="watch", target_id="missing",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_next(engine)
    assert "rejection_feedback" in engine.state.agents[1].pending_wake_reasons


def test_sleep_is_not_interrupted_by_reconsideration(engine_factory) -> None:
    """睡眠不受重估间隔影响：只在计划结束或唤醒事件提供决策。"""
    engine = engine_factory()
    start = engine.state.current_time
    _act(engine, "sleep-9k", "start_basic_activity",
         StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=9000))
    while engine.state.current_time < start + 9000:  # 跨过 5400 秒重估点
        advance_next(engine)
        if engine.state.current_time < start + 9000:
            assert not engine.scheduling_state().ready_agent_ids, (
                "sleep must not be interrupted by the reconsideration interval")
    advance_until_ready(engine)
    assert not any(item.agent_id == 1 and item.status.value == "active"
                   for item in engine.state.activities.values())
    assert engine.state.activities["activity-action-sleep-9k"].status.value == "completed"
    assert "planned_end" in engine.state.agents[1].pending_wake_reasons


def test_felt_band_hysteresis_uses_last_decision_reference(engine_factory) -> None:
    """体感分级滞回：结算重算产生的中间分级与决策参照一致时不构成唤醒。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    from worldmock.life import felt as felt_module
    from worldmock.life.projection import body_projection
    hour = projection.hour_of_day(staged.current_time, engine.scenario.timezone)
    levels = body_projection(staged.agents[1], hour)["levels"]
    staged.agents[1] = staged.agents[1].model_copy(
        update={"felt_bands_at_last_decision": levels}, deep=True)
    staged.version += 1
    engine.state = staged
    _act(engine, "steady-wait", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    before = engine.state.agents[1].body.entertainment_need
    advance_next(engine)
    settled = engine.state.agents[1].body.entertainment_need
    assert settled > before, "settlement must keep evolving the numeric value"
    assert engine.scheduling_state().ready_agent_ids == (), (
        "band unchanged versus the decision reference must stay silent")
    assert engine.state.agents[1].pending_wake_reasons == []


def test_observation_projection_blacklist_and_product_ttl(engine_factory) -> None:
    """观察事件不产生黑名单字段；商品浏览 1800 秒后清空。"""
    engine = engine_factory()
    ctx = context(engine, "observe-clean")
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("observe-clean", "blacklist probe")
    import json as json_module

    prompt_payload = json_module.dumps(data["scene"], default=str) + json_module.dumps(
        data["facts"], default=str)
    for key in FORBIDDEN_OBSERVATION_KEYS:
        assert key not in data["scene"] and key not in data["facts"], (
            f"blacklisted field {key} leaked into prompt inputs")
        assert f'"{key}"' not in prompt_payload
    assert "end_time" not in data and "world_state_version" not in data
    # facts 路线只保留五键白名单
    for route in data["facts"]["routes"]:
        assert set(route) <= {"destination", "mode", "minutes", "fare", "feasible", "status", "reason", "distance_meters"}
    # 商品 TTL：浏览后 1800 秒内可见，超时清空
    _act(engine, "browse-ttl", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine)  # 浏览完成边界登记可见集与浏览时刻
    staged = engine.state.model_copy(deep=True)
    staged.current_time = max(staged.agents[1].supply_browsed_at.values()) + 1801
    staged.version += 1
    engine.state = staged
    visible = projection.visible_supplies(engine.state, engine.state.agents[1],
                                          engine.scenario)
    assert visible == [], "products must clear 1800s after browse"


def test_work_material_reveal_capped_at_2000_chars_per_decision(engine_factory) -> None:
    """工作材料只揭示自上次决策以来新读区间，单次累计 ≤2000 字符。"""
    engine = engine_factory()
    agent = engine.state.agents[1].model_copy(
        update={"material_reveals": [
            {"task_id": "task-a", "at": 200, "start_char": 0, "end_char": 1500, "chars": 1500},
            {"task_id": "task-a", "at": 300, "start_char": 1500, "end_char": 3400, "chars": 1900},
            {"task_id": "task-b", "at": 100, "start_char": 0, "end_char": 900, "chars": 900},
        ], "last_decision_time": 150}, deep=True)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = agent
    staged.version += 1
    engine.state = staged
    ranges = projection.new_material_ranges(engine.state.agents[1])
    assert sum(row["chars"] for row in ranges["task-a"]) == 2000  # 截断到上限
    assert not ranges.get("task-b")  # 上次决策前的不重复揭示


def test_subtitle_does_not_repeat_across_boundary(engine_factory) -> None:
    """横跨结算边界的同一条字幕只交付一次。"""
    engine = engine_factory()
    from worldmock.life.models import StartTargetedActivityRequest
    _act(engine, "watch-dedupe", "start_targeted_activity",
         StartTargetedActivityRequest(
             activity_type="watch", target_id="sintel-en-subtitles",
             planned_duration_seconds=7200, playback_speed=1))
    wake_by_mail(engine)
    advance_next(engine)
    _act(engine, "keep-watching", "continue_activity",
         ContinueActivityRequest(activity_id="activity-action-watch-dedupe"))
    advance_next(engine)
    progresses = [event for event in _events(engine, "activity_progress")]
    delivered: dict[int, set[int]] = {}
    for event in progresses:
        cues = event.data["activity"]["data"].get("revealed", {}).get("cues", [])
        for cue in cues:
            delivered.setdefault(cue["cue_id"], set()).add(event.data["interval_end"])
    repeated = {cue_id: stamps for cue_id, stamps in delivered.items() if len(stamps) > 1}
    assert repeated == {}, f"cues delivered on multiple boundaries: {sorted(repeated)}"


def test_plan_day_registers_goals_and_rejects_same_day_resubmission(engine_factory) -> None:
    """plan_day 登记当日目标，同日重复提交被拒绝。"""
    engine = engine_factory()
    plan = _goal_plan()
    result = _act(engine, "plan-ok", "plan_day", plan)
    assert result.status.value == "completed"
    assert engine.state.agents[1].day_plan is not None
    assert len(engine.state.agents[1].day_plan.goals) == 1
    # 反例：同日重复提交
    advance_until_ready(engine)
    again = _act(engine, "plan-dup", "plan_day", plan)
    assert again.status.value == "rejected"
    assert again.error_code == "PLAN_ALREADY_SUBMITTED"


def test_plan_day_reopens_on_day_two_after_rollover(engine_factory) -> None:
    """跨日边界把 day_plan 归零：第二天首次醒来 plan_day 工具重新开放且可再提交。

    docs/dev/SPEC-life-realism.md「当日目标」"每天第一次醒来的决策必须先提交 plan_day"要求计划按日归零：
    day1 提交后跨日睡眠，day2 醒来时观察的 day_plan 必须为空（只反映当日
    计划），人物侧 plan_day 契约重新开放，引擎侧接受新一天的首个提交。
    """
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    from custom.agents.life_agent import (
        _available_action_models,
        _visible_action_contracts,
        _visible_plan_day_contract,
        _visible_revise_plan_contract,
    )

    engine = engine_factory()
    day1 = _goal_plan()
    first = _act(engine, "plan-day1", "plan_day", day1)
    assert first.status.value == "completed"
    assert engine.state.agents[1].day_plan is not None
    advance_until_ready(engine)
    _act(engine, "sleep-cross", "start_basic_activity",
         StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=86_400))
    for _ in range(400):  # 穿过整夜睡眠的静默边界直到跨日醒来
        if engine.scheduling_state().ready_agent_ids:
            break
        advance_next(engine)
    local = dt.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    assert local.date().isoformat() == "2026-01-06"
    assert "wake_from_sleep" in engine.state.agents[1].pending_wake_reasons
    assert engine.state.agents[1].day_plan is None, "跨日边界必须把前一天的 day_plan 归零"
    ctx = context(engine, "observe-day2")
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("observe-day2", "probe")
    assert data["facts"]["day_plan"] is None, "观察的 day_plan 只反映当日计划"
    assert _visible_plan_day_contract(data) != {}
    assert _visible_revise_plan_contract(data) == {}
    contracts = _visible_action_contracts(data)
    available = _available_action_models({}, {"active": {}, "paused_ids": [], "continuable_ids": [], "watch_ids": [], "game_commands": []}, contracts)
    assert "plan_day" in available
    day2 = _goal_plan()
    second = _act(engine, "plan-day2", "plan_day", day2)
    assert second.status.value == "completed"
    assert sorted(engine.state.agents[1].plan_submitted_dates) == ["2026-01-05", "2026-01-06"]


def test_plan_day_retry_allowed_after_engine_rejection(engine_factory) -> None:
    """绕过请求构造器的非法目标被引擎拒绝，下一秒可修正并完成首次登记。"""
    from custom.agents.life_agent import _visible_plan_day_contract
    engine = engine_factory()
    invalid = PlanDayRequest.model_construct(goals=(
        PlanGoalInput.model_construct(intent_kind="工作", goal="字" * 301),))
    rejected = _act(engine, "plan-invalid", "plan_day", invalid)
    assert rejected.status.value == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    advance_next(engine)
    assert "rejection_feedback" in engine.state.agents[1].pending_wake_reasons
    data = _observe_current(engine, "observe-retry")
    assert _visible_plan_day_contract(data)
    assert _act(engine, "plan-retry", "plan_day", _goal_plan()).status.value == "completed"



def test_revise_day_plan_keeps_progressed_goals_and_rejects_resubmission(engine_factory) -> None:
    """真实活动推进后目标不能改写，修订请求只替换未推进条目。"""
    engine = engine_factory()
    request = PlanDayRequest(goals=(PlanGoalInput(intent_kind="休息", goal="歇一歇"),))
    _act(engine, "plan-base", "plan_day", request)
    advance_until_ready(engine)
    _act(engine, "rest-goal", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
    advance_until_ready(engine)
    evidence = engine.state.agents[1].day_plan.goals[0]
    rejected = _act(engine, "rewrite-progress", "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="休息", goal="改写休息"),)))
    assert rejected.error_code == "PLAN_GOAL_ALREADY_PROGRESSED"
    advance_next(engine)
    result = _act(engine, "replace-pending", "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="工作", goal="写报告"),)))
    assert result.status.value == "completed"
    assert engine.state.agents[1].day_plan.goals[0] == evidence
    assert engine.state.agents[1].day_plan.goals[1].intent_kind == "工作"



def test_send_message_delivers_inbox_and_arrival_event(engine_factory) -> None:
    """send_message 瞬时登记：收件方下次观察 inbox 可见并留 message_arrival。"""
    from tests.life.conftest import submit_round
    from worldmock.life.models import StartBasicActivityRequest as Basic

    engine = engine_factory("multi-person.json")
    _, results = submit_round(engine, {
        1: ("send_message", SendMessageRequest(recipient_id="agent:2", text="中午一起吃饭？")),
        2: ("start_basic_activity", Basic(activity_type="wait", planned_duration_seconds=60)),
    }, "say-hi")
    result = next(item for item in results if item.agent_id == 1)
    assert result.status.value == "completed"
    arrivals = [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.data.get("kind") == "message_arrival"]
    assert arrivals and arrivals[-1].data["recipient_id"] == "agent:2"
    assert any(message.kind == "direct" and message.sender_id == "agent:1"
               for message in engine.state.agents[2].inbox.values())
    from tests.life.conftest import advance_until_ready

    while tuple(engine.scheduling_state().ready_agent_ids) != (2,):
        advance_until_ready(engine)
    ctx = context(engine, "see-inbox", 2)
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("see-inbox", "probe")
    assert data["facts"]["inbox"], "recipient must see the message in the next observation"


def test_chat_requires_copresence_and_settles_relief(engine_factory) -> None:
    """交谈正例：共同在场且对方可交谈（对方收到 chat_invite）；不在场被拒绝。"""
    from tests.life.conftest import submit_round
    from worldmock.life.models import StartBasicActivityRequest as Basic

    engine = engine_factory("multi-person.json")
    _, results = submit_round(engine, {
        1: ("start_social_activity", StartSocialActivityRequest(
            kind="chat", partner_id="agent:2", duration_minutes=15)),
        2: ("start_basic_activity", Basic(activity_type="wait", planned_duration_seconds=60)),
    }, "chat-ok-round")
    ok = next(item for item in results if item.agent_id == 1)
    assert ok.status.value == "in_progress"
    activity = engine.state.activities[ok.activity_id]
    assert activity.kind == ActivityKind.CHAT
    assert activity.data["partner_id"] == "agent:2"
    assert any(message.kind == "chat_invite" for message in engine.state.agents[2].inbox.values())
    # 反例：对方不在场（去了办公室）即拒绝
    staged = engine.state.model_copy(deep=True)
    staged.agents[2] = staged.agents[2].model_copy(update={"location_id": "office"}, deep=True)
    _commit_staged(engine, staged)
    while 1 not in engine.scheduling_state().ready_agent_ids:
        ready_now = list(engine.scheduling_state().ready_agent_ids)
        if ready_now:
            _round_ready(engine, f"hold-{engine.state.current_time}",
                         "start_basic_activity",
                         StartBasicActivityRequest(activity_type="wait",
                                                   planned_duration_seconds=3600))
        assert advance_until_ready(engine), "agent 1 never regained a decision"
    staged = engine.state.model_copy(deep=True)
    staged.agents[2] = staged.agents[2].model_copy(update={"location_id": "office"}, deep=True)
    _commit_staged(engine, staged)
    rejected = _round_ready(engine, "chat-remote", "start_social_activity",
                            StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                                       duration_minutes=15))
    assert rejected.status.value == "rejected"


def test_call_facing_contact_validates_duration(engine_factory) -> None:
    """通话面向联系人：10-60 分钟合同由 social 域校验（越界拒绝）。"""
    engine = engine_factory("multi-person.json")
    staged = engine.state.model_copy(deep=True)
    from worldmock.life.social import Contact
    contacts = (Contact(contact_id="contact-mother", salutation="妈妈", relation="家人",
                        closeness=25, channels=("phone",), reply_policy="none"),)
    document = engine.semantics.document.model_copy(update={
        "agents": tuple(item.model_copy(update={"contacts": contacts})
                        if item.agent_id == 1 else item
                        for item in engine.semantics.document.agents)})
    runtime = engine.scenario.model_copy(update={
        "agents": tuple(item.model_copy(update={"contacts": contacts})
                        if item.agent_id == 1 else item
                        for item in engine.scenario.agents)})
    engine.semantics = replace(engine.semantics, document=document, scenario=runtime)
    engine.scenario = runtime
    too_short = _round_ready(engine, "call-short", "start_social_activity",
                             StartSocialActivityRequest(kind="call", partner_id="contact-mother",
                                                        duration_minutes=5))
    assert too_short.status.value == "rejected"
    advance_next(engine)  # 拒绝后的一秒反应边界
    ok = _round_ready(engine, "call-ok", "start_social_activity",
                      StartSocialActivityRequest(kind="call", partner_id="contact-mother",
                                                 duration_minutes=20))
    assert ok.status.value == "in_progress"
    assert engine.state.activities[ok.activity_id].kind == ActivityKind.CALL


def _stage_open_hours(engine, hour: int = 12) -> None:
    """把世界移到营业时段（商家 7-23 营业，正午全开）。"""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    staged = engine.state.model_copy(deep=True)
    local = dt.fromtimestamp(staged.current_time, ZoneInfo(engine.scenario.timezone))
    noon = int(local.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp())
    staged.current_time = noon
    staged.pending_events = [item for item in staged.pending_events if item.due_at > noon]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(update={"next_decision_at": noon, "body_settled_at": noon}, deep=True)
    _commit_staged(engine, staged)


def test_multi_item_purchase_rejects_whole_order(engine_factory) -> None:
    """多件购买：同一商家一次付款；任一件不可购整单拒绝且不自动删件。"""
    engine = engine_factory()
    from worldmock.life.models import FulfillmentKind, PurchaseItem, PurchaseOrderRequest
    _stage_open_hours(engine)
    _act(engine, "browse-order", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine)
    visible = engine.state.agents[1].visible_supply_ids
    supplies = [item for item in engine.scenario.supplies
                if item.location_id == "market" and item.supply_id in visible][:2]
    assert len(supplies) == 2
    ok_request = PurchaseOrderRequest(
        items=tuple(PurchaseItem(supply_id=item.supply_id, quantity=1,
                                 price_version=engine.state.supply_quotes[item.supply_id].price_version)
                    for item in supplies), fulfillment=FulfillmentKind.DELIVERY, destination_id="home")
    result = _act(engine, "order-ok", "purchase", ok_request)
    assert result.status.value == "completed"
    assert len(result.data["orders"]) == 2
    assert len({order["expected_at"] for order in result.data["orders"]}) == 1
    assert result.data["resource_changes"][f"account:{supplies[0].seller_account_id}"] == result.data["total_price_fen"]
    engine2 = engine_factory()
    _stage_open_hours(engine2)
    _act(engine2, "browse-stale", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine2)
    visible = engine2.state.agents[1].visible_supply_ids
    two = [item for item in engine2.scenario.supplies
           if item.location_id == "market" and item.supply_id in visible][:2]
    assert len(two) == 2
    stale = PurchaseItem(supply_id=two[1].supply_id, quantity=1, price_version="stale")
    fresh = PurchaseItem(supply_id=two[0].supply_id, quantity=1,
                         price_version=engine2.state.supply_quotes[two[0].supply_id].price_version)
    before = dict(engine2.state.supply_stock), dict(engine2.state.accounts)
    rejected = _act(engine2, "order-stale", "purchase", PurchaseOrderRequest(
        items=(fresh, stale), fulfillment=FulfillmentKind.DELIVERY, destination_id="home"))
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "PRICE_VERSION_MISMATCH"
    assert (engine2.state.supply_stock, engine2.state.accounts) == before


def test_multi_item_meal_accumulates_energy(engine_factory) -> None:
    """多件进食：时长取最长、热量与效果累加。"""
    engine = engine_factory()
    foods = [item for item in sorted(engine.scenario.products,
                                     key=lambda item: item.product_id)
             if "eat" in item.use_modes and item.energy_kcal_per_unit > 0][:2]
    staged = engine.state.model_copy(deep=True)
    inventory = {"home": {item.product_id: 1.0 for item in foods}}
    staged.agents[1] = staged.agents[1].model_copy(
        update={"inventory": inventory, "location_id": "home"}, deep=True)
    staged.version += 1
    engine.state = staged
    from worldmock.life.models import ConsumeItem, ConsumeRequest
    request = ConsumeRequest(
        items=tuple(ConsumeItem(product_id=item.product_id, quantity=1) for item in foods),
        use_mode="eat", duration_seconds=max(item.use_duration_seconds for item in foods))
    result = _act(engine, "meal-multi", "consume", request)
    assert result.status.value == "in_progress"
    duration = engine.state.activities[result.activity_id].planned_duration_seconds
    assert duration == max(item.use_duration_seconds for item in foods)
    while engine.state.activities[result.activity_id].status.value == "active":
        advance_next(engine)
    agent = engine.state.agents[1]
    energy_gain = agent.body.energy_kcal - engine.semantics.body.initial_energy_kcal
    raw = sum(item.energy_kcal_per_unit for item in foods)
    from worldmock.life import sleep as sleep_module
    expected = raw * sleep_module.food_utilization_efficiency(0)
    assert 0 < energy_gain <= expected  # 热量按最长时长进度累加（效果累加）
    for item in foods:
        assert agent.inventory["home"][item.product_id] == 0


def test_world_state_schema_version_five_rejects_old_checkpoints(engine_factory) -> None:
    """旧检查点（无 schema_version 或版本 4）加载即拒绝；版本 5 是唯一合法结构版本。

    版本 5 引入活动集合、共同身体时刻与已交付威胁状态；不保留兼容读取
    路径，旧检查点一律拒载（docs/CONTRACTS.md「派生量字段」）。
    """
    engine = engine_factory()
    legacy = engine.state.model_dump(mode="json")
    legacy.pop("schema_version")
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(legacy)
    previous = engine.state.model_dump(mode="json")
    previous["schema_version"] = 4
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(previous)
    fresh = engine.state.model_dump(mode="json")
    assert fresh["schema_version"] == 5
    assert fresh["presentation_state"] == {}





def _goal_plan() -> PlanDayRequest:
    """构造不包含时刻的当日目标。"""
    return PlanDayRequest(goals=(PlanGoalInput(intent_kind="工作", goal="推进报告"),))



def _stage_bedtime_window(engine, window: tuple[float, float]) -> None:
    """给人物 1 声明就寝窗口（B4 人物卡片声明面），并落盘换入引擎。"""
    runtime = engine.scenario.model_copy(update={
        "agents": tuple(item.model_copy(update={"bedtime_window": window})
                        if item.agent_id == 1 else item
                        for item in engine.scenario.agents)})
    engine.scenario = runtime
    staged = engine.state.model_copy(deep=True)
    _commit_staged(engine, staged)


def _observe_current(engine, round_id: str, agent_id: int = 1) -> dict:
    """在当前状态做一次只读观察并返回 data（不结算动作）。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round(round_id, "probe")
    return data











def test_facts_day_plan_remains_visible_after_bedtime_until_midnight(engine_factory) -> None:
    """就寝之后仍展示当日目标，只有跨本地午夜才失效。"""
    engine = engine_factory()
    _stage_bedtime_window(engine, (22.0, 23.0))
    assert _act(engine, "plan-visible", "plan_day", _goal_plan()).status.value == "completed"
    end = engine.state.agents[1].day_plan.day_end
    staged = engine.state.model_copy(deep=True)
    staged.current_time = end - 60
    staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": staged.current_time})
    _commit_staged(engine, staged)
    before = _observe_current(engine, "before-midnight")
    assert before["facts"]["day_plan"] is not None
    assert before["scene"]["day_plan"] is not None
    staged = engine.state.model_copy(deep=True)
    staged.current_time = end
    _commit_staged(engine, staged)
    after = _observe_current(engine, "after-midnight")
    assert after["facts"]["day_plan"] is None
    assert after["scene"]["day_plan"] is None



def test_rejection_message_carries_error_code_and_reason(engine_factory) -> None:
    """目标校验错误经业务结果和下次观察完整反馈，修正后可重试。"""
    engine = engine_factory()
    invalid = PlanDayRequest.model_construct(goals=(
        PlanGoalInput.model_construct(intent_kind="工作", goal="字" * 301),))
    rejected = _act(engine, "plan-invalid-reason", "plan_day", invalid)
    assert rejected.status.value == "rejected"
    assert "ACTION_REQUEST_INVALID" in rejected.message
    assert "300" in rejected.message
    advance_next(engine)
    rows = _observe_current(engine, "observe-rejection")["facts"]["recent_action_results"]
    assert rows[-1]["error_code"] == "ACTION_REQUEST_INVALID"
    assert rows[-1]["occurred_at"] == rejected.occurred_at
    assert set(rows[-1]) == {"action_id", "occurred_at", "status", "error_code", "message"}
    assert _act(engine, "plan-corrected", "plan_day", _goal_plan()).status.value == "completed"






def test_sleep_debt_hours_has_no_residual_references() -> None:
    """sleep_debt_hours 删除后：四个迁移文件无状态字段残留引用（grep 断言）。

    motivation.py（冻结、断链待其所有者修复）、semantics/scenario 的目录资源
    标识与场景初值字段不在本断言范围（目录数据身份，非状态字段）。
    """
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-rn", "-e", ".sleep_debt_hours", "-e", "sleep_debt_hours=",
         "-e", "sleep_debt_hours:", "-e", '\"sleep_debt_hours\"',
         str(root / "src/worldmock/life/models.py"),
         str(root / "src/worldmock/life/body.py"),
         str(root / "src/worldmock/life/engine.py")],
        capture_output=True, text=True, check=False)
    code_lines = [line for line in result.stdout.splitlines()
                  if line and not line.split(":", 2)[-1].lstrip().startswith("#")
                  and "CATALOG_SLEEP_RESOURCE" not in line
                  and "initial_sleep_debt_hours" not in line]
    assert code_lines == [], f"residual sleep_debt_hours references: {code_lines}"


def _browsed_state(engine, location_id: str):
    """把人物放到一个非商家地点并在该处完成一次配送浏览（浏览集为整份目录）。"""
    supply_ids = {item.supply_id for item in engine.scenario.supplies}
    staged = engine.state.model_copy(deep=True)
    now = staged.current_time
    staged.agents[1] = staged.agents[1].model_copy(update={
        "location_id": location_id, "visible_supply_ids": set(supply_ids),
        "supply_browsed_at": {key: now for key in supply_ids},
        "supply_browsed_location": {key: location_id for key in supply_ids},
        "inventory": {}}, deep=True)
    engine.state = staged
    return supply_ids


def test_browsed_products_expire_on_leaving_merchant_location(engine_factory) -> None:
    """浏览可见期是两支并列：离开商家地点即清空，与 1800 秒 TTL 无关。"""
    engine = engine_factory()
    browsed_at = "office"  # 办公室不是任何商品商家，只有浏览支能把目录带进可见集
    product_ids = _browsed_state(engine, browsed_at)
    agent = engine.state.agents[1]

    # 在浏览地点且未超 TTL：浏览支可见（店内常驻支为空，可见集完全来自浏览）。
    assert set(projection.visible_supplies(engine.state, agent, engine.scenario)) == product_ids

    # 未超 TTL 但已离开浏览地点：浏览支清空，可见集回到空。
    moved = engine.state.model_copy(deep=True)
    moved.agents[1] = moved.agents[1].model_copy(update={"location_id": "home"}, deep=True)
    engine.state = moved
    agent = engine.state.agents[1]
    assert agent.supply_browsed_at is not None
    assert all(engine.state.current_time - stamp <= 1800 for stamp in agent.supply_browsed_at.values())
    assert projection.visible_supplies(engine.state, agent, engine.scenario) == [], (
        "leaving the browsed location must clear browsed products before the TTL elapses")

    # 回到浏览地点且未超 TTL：浏览支恢复可见（可见期由地点与时刻两支共同判定）。
    back = engine.state.model_copy(deep=True)
    back.agents[1] = back.agents[1].model_copy(update={"location_id": browsed_at}, deep=True)
    engine.state = back
    assert set(projection.visible_supplies(
        engine.state, engine.state.agents[1], engine.scenario)) == product_ids
