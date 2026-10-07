"""@brief 用真实登记世界验证动作、收货、驾驶、名称与观察的拒绝边界。"""

from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from tests.catalog.conftest import read_payload, write_payload
from tests.life.action_contract_world import contract_world
from tests.life.action_worlds import action_world, advance, begin
from tests.life.purchase_worlds import at_time, registered_world
from tests.life.test_acs_system_core import parcel_request, pending_alarm, place, stamp, system_world
from tests.life.test_content_select import registered_content_world
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.action_run import resume_action
from worldmock.life.activities import change_activity
from worldmock.life.alarm import set_alarm
from worldmock.life.basket import purchase_order
from worldmock.life.body import last_flows
from worldmock.life.commerce import deliver_order
from worldmock.life.content_select import ContentRequest, eligible_contents
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.models import (
    ActivityStartParams, AgentState, ChangeActivityRequest, DeliveryState, SetAlarmRequest,
)
from worldmock.life.name_resolution import lookup, name_book, resolution_data
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from worldmock.life.projection import ProjectionInputs, interaction_bindings
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.social_actions import shared_chat_seconds
from worldmock.life.tool_layer import LayerContext, tool_layer
from worldmock.life.fuel import charge_driving_days, driving_dates
from worldmock.life.work import work_completion_draft


@pytest.fixture(autouse=True)
def clean_flow_evidence():
    """@brief 隔离纯所有者用例产生的身体分录，每个用例只观察自己的证据。"""
    last_flows()
    yield
    last_flows()


def test_resume_completed_content_cannot_switch_to_another_eligible_work_content(catalog_dir):
    """@brief 陈旧暂停快照即使还有其他合格内容，也不能把已完成指派工作恢复或换成其他内容。"""
    payload = read_payload(catalog_dir, "contents.json")
    original = next(row for row in payload["contents"] if row["content_id"] == "topic-sales")
    payload["contents"].append({**original, "content_id": "work-public", "title": "非指派办公练习",
                                "duration_seconds": 600})
    payload["contents"].sort(key=lambda row: row["content_id"])
    write_payload(catalog_dir, "contents.json", payload)
    semantics, state = registered_content_world(catalog_dir)
    state.agents[1].location_id = state.sessions["stay-1"].location_id = "office"
    state, activity = begin(semantics, state, "office-work", None)
    if activity.data["content_id"] == "work-public":
        state, _ = advance(semantics, state, activity, 600)
        state, activity = begin(semantics, state, "office-work", None, suffix="assigned")
    assert activity.data["content_id"] == "topic-sales"
    _, paused = change_activity(state.agents[1], activity,
        ChangeActivityRequest(activity_id=activity.activity_id, operation="pause"), state.current_time, 900)
    completed, evidence = advance(semantics, state, activity, original["duration_seconds"])
    assert evidence["completed"] is True
    query = ContentRequest(1, "office-work", None, activity.activity_id)
    assert {item.content_id for item in eligible_contents(
        semantics.catalog, semantics.scenario, completed, query)} == {"work-public"}
    before, snapshot = completed.model_dump_json(), paused.model_dump_json()
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        resume_action(completed, paused, semantics.catalog, semantics.scenario)
    assert completed.model_dump_json() == before and paused.model_dump_json() == snapshot


@pytest.mark.parametrize("different_time", [False, True])
def test_alarm_identity_reuse_rejects_another_request_without_changing_schedule(tmp_path, different_time):
    """@brief 同一请求重试不重复排期，另一请求不能借用原闹钟身份或改动其时刻。"""
    _, state = system_world(tmp_path)
    at = "2026-01-05T12:00:00+08:00"
    request = SetAlarmRequest(at=at)
    pending, _ = pending_alarm(state, at)
    scheduled, result = set_alarm(state, pending, request)
    replay, repeated = set_alarm(scheduled, pending, request)
    assert replay is scheduled and repeated == result and len(replay.pending_events) == 1
    other_at = "2026-01-05T12:00:01+08:00" if different_time else at
    other, _ = pending_alarm(state, other_at)
    before = scheduled.model_dump_json()
    with pytest.raises(ProtocolError, match="alarm identity cannot be reused for another request"):
        set_alarm(scheduled, other, SetAlarmRequest(at=other_at))
    assert scheduled.model_dump_json() == before
    event = scheduled.pending_events[0]
    assert event.due_at == stamp(at) and event.data["request_event_id"] == pending.requested_event_id


def test_station_pickup_refuses_early_arrival_without_receipt_or_effect(tmp_path):
    """@brief 真实长途已付款订单在预计运输完成前不能到站，到站仍须本人领取才收货。"""
    semantics, state = system_world(tmp_path, shipping=90001)
    outcome = purchase_order(state, semantics, 1, parcel_request(semantics, state))
    order = outcome.orders[0]
    assert order.fulfillment == "station_pickup" and outcome.receipts == ()
    early = at_time(outcome.state, order.expected_at - 1)
    before = early.model_dump_json()
    with pytest.raises(BusinessRuleError, match="DELIVERY_NOT_DUE"):
        deliver_order(early, semantics, order.order_id)
    assert early.model_dump_json() == before
    due = at_time(outcome.state, order.expected_at)
    ready = deliver_order(due, semantics, order.order_id)
    assert ready.order.status == "ready_for_pickup" and ready.order.received_at is None
    assert ready.order.arrived_at == order.expected_at and ready.receipts == ()
    assert ready.state.agents == due.agents and ready.state.accounts == due.accounts
    replay = deliver_order(ready.state, semantics, order.order_id)
    assert replay.state is ready.state and replay.receipts == ()


def test_driving_interval_cannot_move_backwards():
    """@brief 驾驶日期只接受半开正向区间，不把倒退时间当作无驾驶或扣除驾驶日。"""
    start = stamp("2026-01-05T23:59:59+08:00")
    assert driving_dates(start, start, "Asia/Shanghai") == ()
    assert driving_dates(start, start + 2, "Asia/Shanghai") == ("2026-01-05", "2026-01-06")
    with pytest.raises(ValueError, match="Driving interval must not move backwards"):
        driving_dates(start, start - 1, "Asia/Shanghai")


def test_actual_driving_charge_rejects_insufficient_days_atomically(tmp_path):
    """@brief 实际跨午夜驾驶需要两天额度；额度不足时保留余额、已扣日期和所有世界事实。"""
    semantics, state = system_world(tmp_path, days=1)
    start = stamp("2026-01-05T23:59:59+08:00")
    before = state.model_dump_json()
    assert charge_driving_days(state, semantics, 1, start, start) is state
    with pytest.raises(PreconditionError, match="DRIVING_DAYS_EXHAUSTED"):
        charge_driving_days(state, semantics, 1, start, start + 2)
    assert state.model_dump_json() == before
    charged = charge_driving_days(state, semantics, 1, start, start + 1)
    assert charged.agents[1].driving_days == 0 and charged.agents[1].last_driving_date == "2026-01-05"
    assert charge_driving_days(charged, semantics, 1, start, start + 1) is charged
    assert state.model_dump_json() == before


@pytest.mark.parametrize("value", ["2026-02-30", "20260105"])
def test_restore_driving_date_requires_a_real_canonical_local_date(value):
    """@brief 严格恢复拒绝不存在日期与可解析但非标准写法，不改原人物状态。"""
    _, state = registered_world()
    agent = state.agents[1]
    before = agent.model_dump_json()
    document = agent.model_dump(mode="json")
    document["last_driving_date"] = value
    with pytest.raises(ValidationError, match="Last driving date must use a canonical ISO date"):
        AgentState.model_validate(document)
    assert agent.model_dump_json() == before
    document["last_driving_date"] = "2026-01-05"
    assert AgentState.model_validate(document).last_driving_date == "2026-01-05"


def test_direct_delivery_cannot_restore_as_ready_for_station_pickup(tmp_path):
    """@brief 真实普通配送订单不能伪装成待领包裹，也不能因此绕过到期收货。"""
    semantics, state = system_world(tmp_path, shipping=3600)
    outcome = purchase_order(state, semantics, 1, parcel_request(semantics, state))
    order = outcome.orders[0]
    assert order.fulfillment == "delivery" and order.status == "pending"
    document = order.model_dump(mode="json")
    document.update(status="ready_for_pickup", arrived_at=order.expected_at)
    before = outcome.state.model_dump_json()
    with pytest.raises(ValidationError, match="Only station pickup orders may be ready for pickup"):
        DeliveryState.model_validate(document)
    assert outcome.state.model_dump_json() == before
    assert outcome.state.deliveries[order.order_id].received_at is None


@pytest.mark.parametrize("kind", ["wait", "sleep", "travel"])
def test_system_activity_parameters_require_an_explicit_derived_duration(kind, caplog):
    """@brief 三种系统活动的严格内部请求都拒绝空派生时长，不自动猜测持续多久。"""
    _, state = registered_world()
    before = state.model_dump_json()
    fields = {"activity_type": kind, "planned_duration_seconds": None}
    if kind == "travel":
        fields.update(destination_id="office", travel_mode="walking")
    with pytest.raises(ValidationError, match="system activity requires planned_duration_seconds"):
        ActivityStartParams.model_validate(fields)
    assert "System activity requires a derived duration" in caplog.text
    assert state.model_dump_json() == before


@pytest.mark.parametrize("destination,mode", [(None, "walking"), ("office", None), (None, None)])
def test_travel_parameters_require_both_destination_and_mode(destination, mode):
    """@brief 出行内部请求也必须有明确目的地与出行方式，不能猜测任何一项。"""
    _, state = registered_world()
    before = state.model_dump_json()
    fields = {"activity_type": "travel", "planned_duration_seconds": 60,
              "destination_id": destination, "travel_mode": mode}
    with pytest.raises(ValidationError, match="destination_id and travel_mode are required for travel"):
        ActivityStartParams.model_validate(fields)
    assert state.model_dump_json() == before
    fields.update(destination_id="office", travel_mode="walking")
    valid = ActivityStartParams.model_validate(fields)
    assert valid.destination_id == "office" and valid.travel_mode == "walking"


def test_home_alias_uses_the_personas_registered_home_and_never_an_action():
    """@brief 本人在办公室时写住处仍精确指向本人家庭地点，不成为动作名或其他人物地点。"""
    context, observation = contract_world("office")
    book = name_book(observation)
    before = context.state.model_dump_json()
    found = lookup("住处", book.targets, frozenset({"location"}), book.home)
    assert len(found) == 1 and found[0].object_id == "home" and found[0].label == book.home
    assert lookup(f" {book.home} ", book.targets, frozenset({"location"}), book.home) == found
    assert lookup("住处", book.targets, frozenset({"action"}), book.home) == ()
    assert context.state.model_dump_json() == before


def test_name_resolution_appends_actual_normalization_and_notes_without_mutating_prior_result():
    """@brief 返回记录保留已有归一与说明，并追加本次真实住处解析证据；空输入不增加字段。"""
    _, observation = contract_world("office")
    book = name_book(observation)
    home = lookup("住处", book.targets, frozenset({"location"}), book.home)[0]
    prior = {"resolved": [{"written": "自己", "as": "自己"}], "notes": ["Prior accepted result."]}
    original = deepcopy(prior)
    resolved = [{"written": "住处", "as": home.label}]
    notes = ["The home alias identifies this persona's registered home."]
    merged = resolution_data(prior, resolved, notes)
    assert merged["resolved"] == [*prior["resolved"], *resolved]
    assert merged["notes"] == [*prior["notes"], *notes]
    assert prior == original and merged["resolved"] is not prior["resolved"]
    assert resolution_data({"status": "accepted"}, (), ()) == {"status": "accepted"}


def _fuel_context(semantics, state):
    """@brief 用严格加载的燃油世界建立受理路径实际使用的上下文。"""
    scenario = semantics.scenario
    menu = resolve_interaction_menu(scenario, 1, state.agents[1].location_id)
    return LayerContext(state, scenario, LifeRuleEngine(scenario, semantics.catalog), semantics, 1,
                        {"activities": {}, "sessions": {}}, [], menu)


@pytest.mark.parametrize("invalid", ["no_car", "away", "remote", "other_recipient"])
def test_named_fuel_requires_an_owned_car_self_receipt_and_actual_station_presence(tmp_path, invalid):
    """@brief 名称受理同样拒绝无车、离站、远程和代买燃油，在报价与付款之前保持原子。"""
    semantics, state = system_world(tmp_path, owns_car=invalid != "no_car", days=0)
    place(state, 1, "home" if invalid == "away" else "gas")
    context = _fuel_context(semantics, state)
    ask = PurchaseAsk("测试燃油", "美团" if invalid == "remote" else None, "加油支付",
                      "agent:2" if invalid == "other_recipient" else "agent:1")
    before = state.model_dump_json()
    with pytest.raises(PreconditionError, match="FUEL_PURCHASE_INVALID"):
        resolve_named_supply(context, ask)
    assert state.model_dump_json() == before and state.deliveries == {}


def test_interaction_bindings_reject_duplicate_registered_action_names():
    """@brief 重复装配同一个真实目录动作必须拒绝，不能形成不唯一的可读调用目标。"""
    context, _ = contract_world()
    action = context.semantics.catalog.actions["browse-feed"]
    inputs = ProjectionInputs(context.state, context.scenario, context.state.agents[1],
                              extras={"actions": (action,)})
    baseline = interaction_bindings(inputs)
    assert sum(row["object_id"] == action.action_id for row in baseline) == 1
    duplicated = replace(inputs, extras={"actions": (action, action)})
    before = context.state.model_dump_json()
    with pytest.raises(ValueError, match="interactive object labels must be unique"):
        interaction_bindings(duplicated)
    assert context.state.model_dump_json() == before and inputs.extras["actions"] == (action,)


def test_contact_reference_never_creates_shared_in_person_chat_seconds():
    """@brief 实际双方交谈的重叠可计入社交，联系人引用不能被当作同世界在场人物。"""
    semantics, state = action_world(multiple=True)
    state, first = begin(semantics, state, "small-talk", None, "agent:2")
    state, _ = begin(semantics, state, "small-talk", None, "agent:1", agent_id=2, suffix="second")
    assert shared_chat_seconds(state, first, state.current_time + 60) == 60
    contact = semantics.scenario.agents[0].contacts[0].contact_id
    external = first.model_copy(update={"data": {**first.data, "counterpart": contact}}, deep=True)
    before, snapshot = state.model_dump_json(), external.model_dump_json()
    assert shared_chat_seconds(state, external, state.current_time + 60) == 0
    assert state.model_dump_json() == before and external.model_dump_json() == snapshot


def test_tool_layer_rejects_duplicate_target_names_without_changing_world_or_bindings():
    """@brief 最终宿主工具层独立拒绝重名目标，不能靠索引或呈现顺序掩盖歧义。"""
    context, _ = contract_world()
    index = {"actions": [context.semantics.catalog.actions["browse-feed"].name]}
    baseline = tool_layer(context, index)
    assert baseline["callable_index"] == index
    first = context.bindings[0]
    duplicate = replace(context, bindings=[*context.bindings, first])
    before, bindings = context.state.model_dump_json(), deepcopy(context.bindings)
    with pytest.raises(IntegrityError, match="Tool layer target names must be unique"):
        tool_layer(duplicate, index)
    assert context.state.model_dump_json() == before and context.bindings == bindings
    assert len({row["name"] for row in baseline["targets"]}) == len(baseline["targets"])


def test_work_completion_requires_actual_first_progress_to_the_assigned_content_end():
    """@brief 只有首次真实完成指派内容才有完成证据，未推进、部分推进与重复读取不能伪造完成。"""
    semantics, state = action_world("office")
    state, activity = begin(semantics, state, "office-work", None)
    content = semantics.catalog.contents[activity.data["content_id"]]
    assigned = semantics.scenario.agents[0].work_content_ids
    source = EventDraft(event_type="activity_progress", agent_id=1, activity_id=activity.activity_id,
                        occurred_at=state.current_time, data={"activity": activity.model_dump(mode="json")})
    before = state.model_dump_json()
    assert work_completion_draft(state, activity, content, source.event_id, assigned) is None
    assert state.model_dump_json() == before
    partial, _ = advance(semantics, state, activity, 60)
    assert work_completion_draft(partial, activity, content, source.event_id, assigned) is None
    completed, evidence = advance(semantics, partial, partial.activities[activity.activity_id],
                                  content.duration_seconds - 60)
    source = EventDraft(event_type="activity_progress", agent_id=1, activity_id=activity.activity_id,
        occurred_at=completed.current_time,
        data={"activity": completed.activities[activity.activity_id].model_dump(mode="json"), "content": evidence})
    before = completed.model_dump_json()
    draft = work_completion_draft(completed, activity, content, source.event_id, assigned)
    assert draft.data == {"kind": "work_content_completed", "content_id": content.content_id}
    assert draft.caused_by == [source.event_id] and draft.agent_id == 1
    assert draft.activity_id == activity.activity_id and draft.occurred_at == completed.current_time
    assert draft.source_kind == "environment" and draft.visibility == "agent"
    ended = completed.activities[activity.activity_id]
    assert work_completion_draft(completed, ended, content, source.event_id, assigned) is None
    assert completed.model_dump_json() == before
