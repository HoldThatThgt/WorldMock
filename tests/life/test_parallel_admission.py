"""@brief 当前目录类别、设备方式与原生动作的真实并行准入合同。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.life.conftest import (advance_next, advance_until_ready, context, last_result, report_request,
                                 stage_day_plan, submit_round, target_actions, tool_targets, wake_by_mail)
from tests.life.current_world import stage_moment
from tests.life.test_acs_amended_runtime import busy, pump
from tests.life.test_acs_runtime import request
from worldmock.errors import BusinessRuleError
from worldmock.life import admission, order_routing
from worldmock.life.interaction_menu import menu_allows_action, resolve_interaction_menu
from worldmock.life.models import ActivityKind, ActivityStatus, SendMessageActionRequest, StartActivityRequest
from worldmock.life.named_purchase import PurchaseAsk, resolve_named_supply
from worldmock.life.vocabulary import WAYS

_OPEN = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
_UNMET, _CONFLICT = "ACTIVITY_PRECONDITION_UNMET", "RESOURCE_CONFLICT"


def _engine(engine_factory, scenario="single-person.json", location=None):
    """@brief 正式场景副本明确拥有汽车、可用天数和联系人；不构造库存。"""
    def declare(document):
        document["agents"][0].update(owns_car=True, initial_driving_days=7)
        document["agents"][0]["contacts"] = [{"contact_id": "colleague", "salutation": "同事", "relation": "同事",
            "closeness": 16, "channels": ["phone"], "reply_policy": "none"}]
    engine = engine_factory(scenario, mutate=declare)
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
        stage_moment(engine, _OPEN, location if identity == 1 else None, identity)
    return engine


def _observe(engine, name, identity=1):
    """@brief 读取真实提交观察，不登记或推测动作。"""
    return engine.observe(context(engine, name, identity))


def _pair(*values):
    """@brief 每一项已经明确当前五个字段，整组原样提交。"""
    return StartActivityRequest(items=[item for value in values for item in value.items], answering=None)


def _act(engine, name, tool, value, identity=1):
    """@brief 当前时刻全部就绪人物原子应答，其他人只作实际等待、播报或收尾。"""
    requests = {other: busy(engine, other) for other in engine.scheduling_state().ready_agent_ids}
    requests[identity] = (tool, value)
    _, results = submit_round(engine, requests, name)
    return next(item for item in results if item.agent_id == identity)


def _world(engine):
    """@brief 整组拒绝保持账户、位置、活动、订单和会话事实不变。"""
    state = engine.context.state
    return (dict(state.accounts), state.agents[1].location_id,
            {key: item.model_dump() for key, item in state.activities.items() if item.agent_id == 1},
            {key: item.model_dump() for key, item in state.deliveries.items()},
            {key: item.model_dump() for key, item in state.sessions.items() if item.agent_id == 1})


def _rejected(engine, name, value, code, tool="start_activity"):
    """@brief 真实拒绝与下一次公开观察中的原因和下一步逐字一致。"""
    before = _world(engine)
    result = _act(engine, name, tool, value)
    assert (result.status, result.error_code) == ("rejected", code), result.data
    assert _world(engine) == before
    assert result.data["reason"] and result.data["next_step"]
    shown = last_result(_observe(engine, f"{name}-shown"))
    assert shown["error_code"] == code
    assert shown["details"]["reason"] == result.data["reason"]
    assert shown["details"]["next_step"] == result.data["next_step"]
    assert "agent:" not in result.data["reason"] + result.data["next_step"]
    return result


def _drive(engine, name, mode="驾车", destination="公司"):
    """@brief 真实出发后由邮件在一分钟时提供途中决策机会。"""
    result = _act(engine, f"{name}-go", "start_activity", request(destination, None, mode))
    assert result.status == "in_progress", result.data
    wake_by_mail(engine, offset=60)
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    assert engine.context.state.activities[result.activity_id].status == ActivityStatus.ACTIVE
    return result.activity_id


def test_driving_alone_ends_at_arrival(engine_factory):
    """@brief 驾车单独受理，路上会话和实际到达同秒完成并待收尾。"""
    engine = _engine(engine_factory)
    result = _act(engine, "drive-start", "start_activity", request("公司", None, "驾车"))
    assert result.status == "in_progress", result.data
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == ActivityKind.TRAVEL and activity.session_ids
    pump(engine, lambda: engine.context.state.activities[activity.activity_id].status != ActivityStatus.ACTIVE)
    arrived = engine.context.state.activities[activity.activity_id]
    assert arrived.status == ActivityStatus.COMPLETED
    assert arrived.ended_at == arrived.planned_end_at
    assert arrived.concluded_at is None and engine.context.state.agents[1].location_id == "office"


@pytest.mark.parametrize("partner", ["feed", "call", "sleep"])
@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_eyes_or_hands_activities_is_a_resource_conflict_in_either_order(
        engine_factory, partner, drive_first):
    """@brief 驾车占手机和非社交类别；持续手机通话也争用手机，睡眠争用全部占用。"""
    engine = _engine(engine_factory)
    values = {"feed": request(), "call": request("语音通话", "微信", to="联系人：同事"),
              "sleep": request("自己", None, "睡觉")}
    drive = request("公司", None, "驾车")
    other = values[partner]
    value = _pair(*((drive, other) if drive_first else (other, drive)))
    result = _rejected(engine, "drive-conflict", value, _CONFLICT)
    assert result.data["unmet"] == ["resource_claims"]
    assert result.data["next_step"] == "Choose only one of the conflicting activities."


@pytest.mark.parametrize("case", ["work", "chat"])
@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_office_work_or_face_chat_fails_target_presence(engine_factory, case, drive_first):
    """@brief 整组出发后不在办公室，也不与同伴同处；现场资格优先于占用判定。"""
    engine = _engine(engine_factory, "multi-person.json" if case == "chat" else "single-person.json",
                     location="office" if case == "work" else None)
    other = (request("办公", None) if case == "work" else
             request("聊天", None, to=f"人物：{engine.context.state.agents[2].name}"))
    drive = request("家" if case == "work" else "公司", None, "驾车")
    result = _rejected(engine, "presence", _pair(*((drive, other) if drive_first else (other, drive))), _UNMET)
    assert result.data["unmet"] in (["required_location_id"], ["partner_present"])


def test_work_with_phone_entertainment_settles_one_shared_body(engine_factory):
    """@brief 无 app 工作与手机娱乐占不同类别，每段留进度且身体只结算一次。"""
    engine = _engine(engine_factory, location="office")
    started = _act(engine, "work-pair", "start_activity", _pair(request("办公", None), request()))
    assert started.status == "in_progress", started.data
    identities = {row["activity_id"] for row in started.data["activities"]}
    advance_next(engine)
    now = engine.context.state.current_time
    events = engine.context.store.read_agent_events("environment_event", 1)
    body = [event for event in events if event.data.get("kind") == "body_settlement"
            and event.data["interval_end"] == now]
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.occurred_at == now and event.activity_id in identities]
    assert len(body) == 1 and {event.activity_id for event in progress} == identities
    assert {event.data["body_settlement_event_id"] for event in progress} == {body[0].event_id}
    assert all(engine.context.state.activities[key].elapsed_seconds > 0 for key in identities)


@pytest.mark.parametrize("case", ["two_apps", "two_trips", "chat_call"])
def test_overlapping_pairs_are_resource_conflicts(engine_factory, case):
    """@brief 手机使用、出行类别与社交类别分别有真实互斥反例。"""
    engine = _engine(engine_factory, "multi-person.json" if case == "chat_call" else "single-person.json")
    call = request("语音通话", "微信", to="联系人：同事")
    pairs = {"two_apps": (request(), call),
             "two_trips": (request("公司", None, "步行"), request("三杯咖啡", None, "驾车")),
             "chat_call": (request("聊天", None, to=f"人物：{engine.context.state.agents.get(2, engine.context.state.agents[1]).name}"), call)}
    _rejected(engine, "pair-conflict", _pair(*pairs[case]), _CONFLICT)


def test_driving_rejects_messages_and_exact_named_remote_orders(engine_factory):
    """@brief 途中发消息与明确渠道下单都拒绝，不扣款或生成订单。"""
    engine = _engine(engine_factory)
    _drive(engine, "hands")
    observed = _observe(engine, "hands-preview")
    assert not [row for row in observed["interaction_bindings"] if row["kind"] == "supply"]
    result = _rejected(engine, "hands-message", SendMessageActionRequest(
        recipient_name="联系人：同事", text="在路上"), _UNMET, "send_message")
    assert result.data["reason"] == "Cannot send a message while driving."
    assert result.data["next_step"].startswith("Wait until arrival at ")
    _rejected(engine, "hands-order", request("牛肉面", "美团", "购买", payment="微信"), _UNMET)


def test_walking_accepts_messages_and_refuses_self_delivery_during_transit(engine_factory):
    """@brief 步行允许消息，短途订单仍需要本人实际可收货地址。"""
    engine = _engine(engine_factory)
    _drive(engine, "walk", mode="步行")
    sent = _act(engine, "walk-message", "send_message", SendMessageActionRequest(
        recipient_name="联系人：同事", text="路上"))
    assert sent.status == "completed"
    wake_by_mail(engine, offset=60)
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    _rejected(engine, "walk-order", request("牛肉面", "美团", "购买", payment="微信"), _UNMET)
    assert not engine.context.state.deliveries


def test_en_route_person_is_nowhere_and_has_no_inventory(engine_factory):
    """@brief 真正在途中没有当前地点；出发地停留菜单保留，但不能恢复现场工作或列出持有物。"""
    engine = _engine(engine_factory, "multi-person.json")
    _drive(engine, "nowhere")
    assert admission.present_location(engine.context.state, 1) is None
    observed = _observe(engine, "nowhere-during")
    assert not hasattr(engine.context.state.agents[1], "inventory")
    assert not any(row["kind"] == "holding" for row in observed["interaction_bindings"])
    origin = engine.context.state.agents[1].location_id
    assert observed["interaction_menu"] == resolve_interaction_menu(engine.context.scenario, 1, origin).model_dump(mode="json")
    assert target_actions(observed, "办公")["进行"]["available"] is False


def test_departure_with_a_running_activity_names_it_in_reason_and_next_step(engine_factory):
    """@brief 未处理的等待必须先暂停或结束，公开拒绝点名同一活动。"""
    engine = _engine(engine_factory)
    waited = _act(engine, "busy-wait", "start_activity", request("自己", None, "等待"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = engine.context.state.activities[waited.activity_id].name
    observed = _observe(engine, "busy-preview")
    reason = target_actions(observed, "地点：公司")["步行"]["reason"]
    assert reason == f"Conclude or pause the current activities {name} before departure."
    result = _rejected(engine, "busy-go", request("公司", None, "步行"), _UNMET)
    assert result.data["reason"] == reason and result.data["next_step"] == f"Conclude or pause {name} first."


def test_listed_actions_are_menu_allowed_and_self_is_always_offered(engine_factory):
    """@brief 自己恒有等待和睡觉，全部实际绑定的当前动作都由有效菜单允许。"""
    engine = _engine(engine_factory, location="office")
    observed = _observe(engine, "listed")
    menu = resolve_interaction_menu(engine.context.scenario, 1, "office")
    assert [item["way"] for item in tool_targets(observed)["自己"]["actions"]] == ["等待", "睡觉"]
    for row in observed["interaction_bindings"]:
        for action in row["actions"]:
            assert WAYS[action["label"]] == (action["verb"], action["canonical_action"])
            assert menu_allows_action(menu, "start_activity", [action["canonical_action"]])
    assert _act(engine, "listed-work", "start_activity", request("办公", None)).status == "in_progress"


def test_two_goods_share_one_delivery_fee_and_distinct_receipts(engine_factory):
    """@brief 同店同应用两项购买组成一篮，配送费收一次，各商品单独收货而无吃这一步。"""
    engine = _engine(engine_factory, location="noodle-1")
    bought = _act(engine, "basket", "start_activity", _pair(
        request("牛肉面", "美团", "购买", payment="微信"), request("薯片", "美团", "购买", payment="微信")))
    assert bought.status == "completed", bought.data
    orders = bought.data["orders"]
    assert [row["goods_id"] for row in orders] == ["beef-noodles", "chips"]
    assert [row["delivery_fee_fen"] for row in orders] == [500, 0]
    pump(engine, lambda: all(engine.context.state.deliveries[row["order_id"]].status == "delivered" for row in orders))
    receipts = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                if event.data.get("kind") == "goods_received"]
    assert {event.data["order_id"] for event in receipts} == {row["order_id"] for row in orders}
    assert len(receipts) == 2 and not hasattr(engine.context.state.agents[1], "inventory")


def test_order_routing_groups_only_matching_app_payment_and_fulfillment(engine_factory):
    """@brief 唯一商品解析的真实履约行按店铺、收货地、应用与支付选择分组。"""
    engine = _engine(engine_factory, location="noodle-1")
    layer = engine.observations._layer_context(engine.context.state.agents[1], ())
    def binding(name, app, payment):
        row = resolve_named_supply(layer, PurchaseAsk(name, app, payment, "agent:1"))
        return {**row, "recipient_id": "agent:1"}
    same = order_routing.order_requests(engine.context.state, engine.context.semantics, 1,
        [binding("牛肉面", "美团", "微信"), binding("薯片", "美团", "微信")])
    assert len(same) == 1 and [item.goods_id for item in same[0].items] == ["beef-noodles", "chips"]
    mixed = order_routing.order_requests(engine.context.state, engine.context.semantics, 1,
        [binding("牛肉面", None, None), binding("薯片", "美团", "微信")])
    assert [row.fulfillment for row in mixed] == ["pickup", "delivery"]
    assert [row.app_id for row in mixed] == [None, "meituan"]


@pytest.mark.parametrize("walk_first", [True, False])
def test_daily_care_with_walk_is_accepted_in_either_order(engine_factory, walk_first):
    """@brief 日常照护的类别不限地点，与步行不争占用，真实活动各自开始。"""
    engine = _engine(engine_factory)
    walk, care = request("公司", None, "步行"), request("安静休息", None)
    result = _act(engine, "stay", "start_activity", _pair(*((walk, care) if walk_first else (care, walk))))
    assert result.status == "in_progress", result.data
    kinds = [engine.context.state.activities[row["activity_id"]].kind for row in result.data["activities"]]
    assert set(kinds) == {ActivityKind.TRAVEL, ActivityKind.ACTION}


def test_products_show_one_purchase_way_and_explicit_app_choices(engine_factory):
    """@brief 每件商品只有购买方式；渠道由 app 选择，现场购买没有渠道猜测。"""
    engine = _engine(engine_factory)
    observed = _observe(engine, "away-preview")
    row = tool_targets(observed)["牛肉面"]
    assert {action["way"] for action in row["actions"]} == {"购买"}
    assert {action["app"] for action in row["actions"]} == {None, "美团"}
    _rejected(engine, "away-buy", request("牛肉面", None, "购买"), _UNMET)


@pytest.mark.parametrize("destination", ["cafe", "home", "noodle-1", "noodle-2", "office", "park", "store"])
def test_remote_order_is_available_and_delivered_at_every_selected_place(engine_factory, destination):
    """@brief 七个已选地点都使用实际收货地址及唯一配送路径，不创建库存。"""
    engine = _engine(engine_factory, location=destination)
    bought = _act(engine, "reachable-order", "start_activity", request("牛肉面", "美团", "购买", payment="微信"))
    assert bought.status == "completed", bought.data
    delivery, = engine.context.state.deliveries.values()
    assert delivery.destination_id == destination
    assert delivery.path == engine.context.semantics.catalog.graph.path(delivery.store_id, destination)
    pump(engine, lambda: engine.context.state.deliveries[delivery.order_id].status == "delivered")
    receipts = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                if event.data.get("kind") == "goods_received"]
    assert len(receipts) == 1 and receipts[0].data["order_id"] == delivery.order_id


@pytest.mark.parametrize("companion", ["步行", "等待"])
def test_an_instant_obtain_joins_a_timed_activity_in_one_request(engine_factory, companion):
    """@brief 购买是零秒动作，与等待或步行同轮受理，订单仍保留购买时的收货地址。"""
    engine = _engine(engine_factory)
    other = request("公司" if companion == "步行" else "自己", None, companion)
    result = _act(engine, "mixed", "start_activity", _pair(
        request("牛肉面", "美团", "购买", payment="微信"), other))
    assert result.status == "in_progress", result.data
    assert len(result.data["activities"]) == len(result.data["orders"]) == 1
    delivery, = engine.context.state.deliveries.values()
    assert delivery.destination_id == "home"
    activity = engine.context.state.activities[result.data["activities"][0]["activity_id"]]
    assert activity.kind == (ActivityKind.TRAVEL if companion == "步行" else ActivityKind.WAIT)
    assert activity.session_ids


@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_a_remote_order_in_one_request_says_order_first(engine_factory, drive_first):
    """@brief 手动购买与驾车同时开始时先下单再出发的原子拒绝与顺序无关。"""
    engine = _engine(engine_factory)
    drive, buy = request("公司", None, "驾车"), request("牛肉面", "美团", "购买", payment="微信")
    result = _rejected(engine, "drive-order", _pair(*((drive, buy) if drive_first else (buy, drive))), _UNMET)
    assert (result.data["reason"], result.data["next_step"], result.data["unmet"]) == (
        "Orders cannot be placed while driving.", "Place the order before departure.", ["hands_free"])


@pytest.mark.parametrize("fields,path,written,reason", [
    ({"items": [], "answering": None}, ["items"], [],
     "items requires one or two activity objects; acceptance is atomic"),
    ({"items": [{"target": 7, "way": "等待", "app": None, "to": None, "pay_app": None}], "answering": None},
     ["items", 0, "target"], 7, "A JSON string is required."),
    ({"items": [{"target": "自己", "way": "等待", "app": None, "pay_app": None}], "answering": None},
     ["items", 0, "to"], None, "This required field was omitted."),
], ids=["empty-items", "wrong-target-type", "missing-null-field"])
def test_malformed_request_gets_the_default_reason_without_the_validation_text(
        engine_factory, fields, path, written, reason):
    """@brief 绕过模型构造校验的请求仍被真实入口拒绝，公开字段错误而不泄露库转储。"""
    engine = _engine(engine_factory)
    malformed = StartActivityRequest.model_construct(**fields)
    before = _world(engine)
    result = _act(engine, "malformed", "start_activity", malformed)
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert _world(engine) == before and result.message == result.data["reason"] == reason
    assert not any(text in result.message for text in ("validation", "Action rejected", "ACTION_REQUEST_INVALID"))
    assert result.data["fix"] == "Correct every reported parameter."
    item, = result.data["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", *path] and item["written"] == written
    assert item["error_code"] == "ACTION_REQUEST_INVALID" and item["why"] == reason
    assert item["expected"] and item["allowed_values"] == [] and item["candidates"] == []
    shown = last_result(_observe(engine, "malformed-shown"))["details"]
    assert shown["items"] == result.data["items"]


def test_a_repeated_observation_of_one_decision_is_ambiguous_on_every_read(engine_factory):
    """@brief 同一决策重复提交观察后，每次读取均按真实存储拒绝歧义，不沿用缓存。"""
    engine = _engine(engine_factory)
    decision = context(engine, "twice")
    engine.observe(decision)
    pending = SimpleNamespace(agent_id=1, decision_id=decision.decision_id, observed_at=engine.context.state.current_time)
    assert engine.actions._frozen_observation(pending)["interaction_bindings"]
    engine.observe(decision)
    with pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine.actions._frozen_observation(pending)
