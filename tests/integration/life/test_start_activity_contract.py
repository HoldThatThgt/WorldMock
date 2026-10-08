"""@brief 五字段可读动作与真实环境执行、原子控制的合同。"""

from __future__ import annotations

import math
from datetime import datetime

import pytest
from pydantic import ValidationError

from tests.life.action_contract_world import contract_world
from tests.life.request_fixtures import request as _request
from tests.life.conftest import (advance_next, advance_until_ready, context, conclude_request, pause_request,
                                 report_request, stage_day_plan, wake_by_mail)
from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_CONCLUDED, PHASE_PAUSED, PHASE_PENDING, activity_phase
from worldmock.life.vocabulary import LIFE_VERBS, WAYS
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.models import (ActivityKind, ActivityStatus, ConcludeActivityRequest, PauseActivityRequest,
                                   ReportStatusRequest, SessionTaskKind, StartActivityRequest, active_activities)
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.routing import combination_error, require_recipients, routed_selection, routing_names
from worldmock.life.schedule import PlanGoal




def _selection(observation, request):
    outcome = resolve_start(name_book(observation), request)
    if outcome.failures:
        return None
    names = routing_names(observation)
    if combination_error(outcome.request, names) is not None:
        return None
    return routed_selection(list(outcome.rows), outcome.request, names)


def _stage(engine, mutate):
    staged = engine.context.state.model_copy(deep=True)
    mutate(staged)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_start_contract_staged"})])
    engine.context.state = staged


def _engine(engine_factory, location="home"):
    engine = engine_factory()
    stage_day_plan(engine)
    def move(state):
        now = max(state.current_time, int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp()))
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
        state.agents[1].location_id = location
    _stage(engine, move)
    _stage(engine, lambda state: None)
    engine.context.state = sessions.end_all(engine.context.state)
    engine.context.state = sessions.open_stay(engine.context.state, 1,
        {place.location_id: place.name for place in engine.context.scenario.locations})
    _stage(engine, lambda state: None)
    return engine


def _observe(engine, name):
    return engine.observe(context(engine, name))


def _round(engine, name, tool, request):
    from agentsociety2.society import DirectToolCall
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observation, engine.commit_decision_round(name)[0]


def _two_parallel(engine, label):
    _, result = _round(engine, label, "start_activity", _request(
        ("自己", "等待", None, None, None), ("刷推荐", "进行", "抖音", None, None)))
    assert result.status == "in_progress", result.error_code
    return [row["activity_id"] for row in result.data["activities"]]


def _begin_pair(engine, label):
    stage_day_plan(engine)
    identities = _two_parallel(engine, label)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    return tuple(engine.context.state.activities[identity].name for identity in identities)


def _until_ended(engine, identity, label):
    for step in range(96):
        if engine.context.state.activities[identity].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.context.state, 1)
            _round(engine, f"{label}-report-{step}", "report_status",
                   report_request(*(item.name for item in running), summary="继续。"))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


SPEC_WAYS_BY_VERB = {"travel": {"步行", "驾车"}, "interact": {"进行", "等待"},
                     "obtain": {"购买", "领取"}, "sleep": {"睡觉"}}








@pytest.mark.parametrize("target,way", [("不存在地点", "步行"), ("地点：公司", "沿着河边慢慢走"),
                                        ("自己", "阅读"), ("自己", "进行")])
def test_unresolvable_selection_keeps_exact_name_or_way_error(target, way):
    _, observation = contract_world()
    request = _request((target, way, None, None, None))
    assert _selection(observation, request) is None
    (failure,) = resolve_start(name_book(observation), request).failures
    assert failure.item.error_code in {"NAME_NOT_FOUND", "WAY_NOT_OFFERED"}


def test_two_offered_selections_resolve_item_by_item_to_internal_actions():
    _, observation = contract_world()
    selected = _selection(observation, _request(("自己", "等待", None, None, None),
                                                ("地点：公司", "步行", "高德地图", None, None)))
    assert [(row["object_id"], row["selected_action"]["canonical_action"]) for row in selected] == [
        ("agent:1", "wait"), ("office", "travel_to")]
    assert selected[1]["selected_action"]["navigation_app_id"] == "amap"


@pytest.mark.parametrize("recipients,answering,expected", [
    ([None], None, [("agent:1", True, None)]),
    (["王磊"], "王磊 10:35 的消息", [("contact-colleague", False, "reply-1")]),
    ([None, "王磊"], "王磊 10:35 的消息", [("agent:1", True, None), ("contact-colleague", False, "reply-1")]),
    (["Second Test Person"], None, [("agent:2", False, None)]),
])
def test_legal_app_payment_recipient_and_answering_combinations_route_each_item(recipients, answering, expected):
    _, observation = contract_world()
    observation["message_bindings"] = [{"name": "王磊 10:35 的消息", "message_id": "reply-1",
                                         "sender_id": "contact-colleague"}]
    request = _request(*(("牛肉面", "购买", "美团", person, "微信") for person in recipients), answering=answering)
    outcome = resolve_start(name_book(observation), request)
    assert not outcome.failures
    names = routing_names(observation)
    assert combination_error(outcome.request, names) is None
    selected = routed_selection(list(outcome.rows), outcome.request, names)
    assert [(row["recipient_id"], row["to_self"], row["request_message_id"]) for row in selected] == expected
    assert all(row["app_id"] == "meituan" and row["payment_app_id"] == "wechat" for row in selected)
    require_recipients(selected)


@pytest.mark.parametrize("item,answering,code", [
    (("地点：公司", "步行", None, "Second Test Person", None), None, "ROUTE_FIELDS_INVALID"),
    (("地点：公司", "步行", "微信", None, None), None, "APP_NOT_OFFERED"),
    (("刷推荐", "进行", None, None, None), None, "APP_REQUIRED"),
    (("牛肉面", "购买", "美团", None, None), None, "PAYMENT_APP_REQUIRED"),
    (("牛肉面", "购买", "美团", None, "微信"), "unknown", "ROUTE_FIELDS_INVALID"),
])
def test_illegal_combinations_keep_the_same_host_and_environment_business_code(item, answering, code):
    _, observation = contract_world()
    outcome = resolve_start(name_book(observation), _request(item, answering=answering))
    assert not outcome.failures
    error = combination_error(outcome.request, routing_names(observation))
    assert error is not None
    assert error[0] == code
    assert _selection(observation, _request(item, answering=answering)) is None




@pytest.mark.parametrize("recipient", ["人物：陌生人", "自己"])
def test_unknown_recipient_is_recipient_not_found_before_admission(recipient):
    _, observation = contract_world()
    outcome = resolve_start(name_book(observation), _request(("牛肉面", "购买", "美团", recipient, "微信")))
    assert not outcome.failures
    names = routing_names(observation)
    assert combination_error(outcome.request, names)[0] == "RECIPIENT_NOT_FOUND"
    selection = routed_selection(list(outcome.rows), outcome.request, names)
    assert selection[0]["recipient_id"] is None
    with pytest.raises(BusinessRuleError, match="RECIPIENT_NOT_FOUND"):
        require_recipients(selection)


def test_route_choice_preserves_mode_but_engine_derives_exact_seconds(engine_factory):
    """真实图上出行方式决定精确秒数，人物看到的分钟短语不能回填执行时长。"""
    engine = _engine(engine_factory, location="home")
    observed = _observe(engine, "travel-choice")
    row = next(row for row in observed["interaction_bindings"]
               if row["kind"] == "location" and row["object_id"] == "office")
    _, result = _round(engine, "travel-start", "start_activity", _request((row["object"], "步行", None, None, None)))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert (activity.kind, activity.destination_id, activity.travel_mode) == (ActivityKind.TRAVEL, "office", "walking")
    assert activity.planned_duration_seconds == 2729
    assert activity.data["distance_meters"] == 3274
    assert activity.planned_duration_seconds != math.ceil(activity.planned_duration_seconds / 60) * 60
    assert TRAVEL_WAYS == {"walking": "步行", "driving": "驾车"}


def test_purchase_uses_each_observed_quote_and_single_unit(engine_factory):
    """@brief 同店两件目录商品采用固定价格，购物和支付点名后只付一次配送费。"""
    engine = _engine(engine_factory, location="home")
    observed = _observe(engine, "fixed-price-choice")
    rows = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and row["goods_id"] in {"chips", "lemon-tea"}]
    assert len(rows) == 2
    request = _request(*((row["object"], "购买", "美团", None, "微信") for row in rows))
    _, result = _round(engine, "fixed-price-purchase", "start_activity", request)
    assert result.status == "completed", (result.error_code, result.message, result.data)
    orders = [engine.context.state.deliveries[row["order_id"]] for row in result.data["orders"]]
    catalog = engine.context.semantics.catalog
    assert [(order.goods_id, order.quantity, order.unit_price_fen, order.recipient_id) for order in orders] == [
        (row["goods_id"], 1, catalog.goods[row["goods_id"]].price_fen, "agent:1") for row in rows]
    assert all(order.fulfillment == "delivery" and order.destination_id == "home" for order in orders)
    assert all(order.app_id == "meituan" and order.payment_app_id == "wechat" for order in orders)
    assert [order.delivery_fee_fen for order in orders] == [500, 0]
    assert all(order.total_price_fen == order.unit_price_fen + order.delivery_fee_fen for order in orders)
    assert "activity_results" not in result.data


def test_purchase_preserves_chosen_pickup_when_delivery_is_also_available(engine_factory):
    """@brief 同一购买方式以 app 空值声明到店，不会代选线上应用。"""
    engine = _engine(engine_factory, location="noodle-1")
    observed = _observe(engine, "pickup-choice")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and row["goods_id"] == "beef-noodles")
    assert {action["label"] for action in row["actions"]} == {"购买"}
    assert {action["app_id"] for action in row["actions"]} == {None, "meituan"}
    _, result = _round(engine, "pickup-start", "start_activity", _request((row["object"], "购买", None, None, None)))
    assert result.status == "completed", (result.error_code, result.message, result.data)
    (receipt,) = result.data["orders"]
    order = engine.context.state.deliveries[receipt["order_id"]]
    assert (order.fulfillment, order.app_id, order.payment_app_id, order.quantity) == ("pickup", None, None, 1)
    assert order.unit_price_fen == engine.context.semantics.catalog.goods["beef-noodles"].price_fen
    assert order.created_at == order.expected_at == order.received_at == engine.context.state.current_time
    assert order.delivery_fee_fen == 0


def test_report_two_activities_validates_all_before_updating_anything(engine_factory):
    """真实播报分别写两项进行中活动，随后混入已收尾名称时整条拒绝，另一项不会部分写入。"""
    engine = engine_factory()
    names = _begin_pair(engine, "atomic-report-start")
    ids = [next(item.activity_id for item in engine.context.state.activities.values() if item.name == name)
           for name in names]
    _, result = _round(engine, "atomic-report", "report_status", report_request(*names, summary=["还在等", "看到一段"]))
    assert result.status == "completed" and result.data == {"activity_ids": ids, "errands": []}
    now = engine.context.state.current_time
    assert [(engine.context.state.activities[identity].progress_note,
             engine.context.state.activities[identity].report_anchor_at) for identity in ids] == [
        ("还在等", now), ("看到一段", now)]
    # 经真实结束和收尾后，已收尾名称不再属于本轮控制合同。
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    _, concluded = _round(engine, "atomic-report-conclude", "conclude_activity",
                          conclude_request(names[1], summary="先关掉应用", memories=[[]]))
    assert concluded.status == "completed"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    before = {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()}
    _, rejected = _round(engine, "atomic-report-reject", "report_status",
                          report_request(*names, summary=["不应写入", "也不应写入"]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert [(item["written"], item["error_code"]) for item in rejected.data["items"]] == [
        (name, "ACTION_REQUEST_INVALID") for name in names]
    assert {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()} == before




def test_report_writes_original_summary_to_independent_day_goal_note(engine_factory):
    """真实播报沿用人物原文写入关联目标，不改变登记时刻和此前已核验的推进身份。"""
    engine = _engine(engine_factory, location="home")
    now = engine.context.state.current_time
    goal = PlanGoal(intent_kind="休息", goal="午后稍作休息", registered_at=now - 100,
                    progressed_at=now - 50, progress_activity_id="earlier-rest")
    stage_day_plan(engine, goals=(goal,))
    _, started = _round(engine, "goal-rest-start", "start_activity", _request(("自己", "等待", None, None, None)))
    assert started.status == "in_progress"
    identity = started.activity_id
    def attach_goal(state):
        state.activities[identity] = state.activities[identity].model_copy(update={
            "task_kind": SessionTaskKind.GOAL, "task_ref": "休息", "plan_goal_intent_kind": "休息"})
    _stage(engine, attach_goal)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = engine.context.state.activities[identity].name
    _, result = _round(engine, "goal-rest-report", "report_status", report_request(name, summary="还想再歇一会儿"))
    assert result.status == "completed"
    current = engine.context.state.agents[1].day_plan.goals[0]
    assert current.progress_note == engine.context.state.activities[identity].progress_note == "还想再歇一会儿"
    assert (current.registered_at, current.progressed_at, current.progress_activity_id) == (
        goal.registered_at, goal.progressed_at, goal.progress_activity_id)


def _pending_and_paused(engine):
    """@brief 真实开始等待与应用、暂停应用，再另开应用；等待结束时保留未点名的运行活动。"""
    stage_day_plan(engine)
    ended_id, paused_id = _two_parallel(engine, "selective-start")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused = engine.context.state.activities[paused_id]
    _, result = _round(engine, "selective-pause", "pause_activity", pause_request(paused.name, summary="先停一会儿"))
    assert result.status == "completed"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    observed = _observe(engine, "selective-other-choice")
    assert any(row["kind"] == "action" and row["object_id"] == "browse-feed"
               for row in observed["interaction_bindings"])
    _, running = _round(engine, "selective-other", "start_activity",
                        _request(("刷推荐", "进行", "哔哩哔哩", None, None)))
    assert running.status == "in_progress", running.error_code
    _until_ended(engine, ended_id, "selective-end")
    return ended_id, paused_id, running.activity_id


def test_conclude_ends_only_selected_activities_with_own_summaries(engine_factory):
    """真实收尾保留已结束终态、取消暂停活动，未点名活动不变；记忆身份来自真实观察与活动会话。"""
    engine = engine_factory()
    ended_id, paused_id, running_id = _pending_and_paused(engine)
    before = engine.context.state.activities
    ended, paused, running = (before[identity] for identity in (ended_id, paused_id, running_id))
    assert (activity_phase(ended), activity_phase(paused), activity_phase(running)) == (
        PHASE_PENDING, PHASE_PAUSED, PHASE_ACTIVE)
    now = engine.context.state.current_time
    observed, result = _round(engine, "selective-conclude", "conclude_activity",
                               conclude_request(ended.name, paused.name, summary=["等完了", "先不看了"], memories=[[], []]))
    assert result.status == "completed", (result.error_code, result.message, result.data)
    done, stopped = (engine.context.state.activities[identity] for identity in (ended_id, paused_id))
    assert (done.status, done.ended_at, done.concluded_at, done.progress_note) == (
        ActivityStatus.COMPLETED, ended.ended_at, now, "等完了")
    assert (stopped.status, stopped.interruption_reason, stopped.concluded_at, stopped.progress_note) == (
        ActivityStatus.CANCELLED, "agent_cancelled", now, "先不看了")
    assert (activity_phase(done), activity_phase(stopped)) == (PHASE_CONCLUDED, PHASE_CONCLUDED)
    assert engine.context.state.activities[running_id] == running
    assert "task" not in result.data and "items" not in result.data
    assert result.data["activity_ids"] == [ended_id, paused_id]
    assert result.data["memory_sources"] == [
        {"task": item.name, "activity_id": item.activity_id, "session_id": item.session_ids[-1],
         "verified_event_ids": [row["source_event_id"] for row in observed["verified_experiences"]
                                if row["activity_id"] == item.activity_id]} for item in (ended, paused)]
    assert result.data["outcome"] and result.data["errands"] == []


def test_conclude_naming_none_of_the_just_ended_activities_is_rejected(engine_factory):
    """真实待收尾活动存在时，只收尾暂停活动会点名必须先收尾的活动，且全组活动不变。"""
    engine = engine_factory()
    ended_id, paused_id, _ = _pending_and_paused(engine)
    ended, paused = (engine.context.state.activities[identity] for identity in (ended_id, paused_id))
    assert (activity_phase(ended), activity_phase(paused)) == (PHASE_PENDING, PHASE_PAUSED)
    before = {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()}
    _, rejected = _round(engine, "selective-reject", "conclude_activity",
                          conclude_request(paused.name, summary="先不看了", memories=[[]]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert rejected.data["reason"] == f"Conclude the finished activities {ended.name} first."
    assert {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()} == before
