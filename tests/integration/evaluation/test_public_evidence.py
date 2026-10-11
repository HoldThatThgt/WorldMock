"""实际动作、对象、独立付款与无分类内容的公开证据。"""

import json

import pytest

from worldmock.evaluation.adapters import parse_world_event
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.public_evidence import worldmock_activity_action, worldmock_public_evidence
from tests.integration.evaluation.test_adapters import _public_event
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments

#: 四个出行动作在 start_main_activity 的 detail 里写的固定出行方式名称。
TRAVEL_NAMES = {"walk": "步行", "drive": "驾车", "take_taxi": "打车", "take_bus": "公共交通"}


def start_call(action, app="real-app"):
    """按活动实际记下的动作给出现行开始工具与完整参数：出行、睡眠和在地点做事用主工具，购买和用内容 app 用副工具。"""
    if action in TRAVEL_NAMES:
        return MAIN_TOOL, main_arguments("travel", "TITLE_CANARY", TRAVEL_NAMES[action])
    if action == "sleep":
        return MAIN_TOOL, main_arguments("sleep", "450")
    if action == "interact" and app is None:
        return MAIN_TOOL, main_arguments("interact", "TITLE_CANARY", "DETAIL_CANARY")
    return SIDE_TOOL, side_arguments(action, "TITLE_CANARY")


def action_events(action="interact", content="real-content", app="real-app", **extra):
    """一条开始调用及其受理结果：一条动作恰好开始一项活动，结果带这一项活动的快照。"""
    data = {"action": action, "content_id": content, "app_id": app, **extra}
    kind = "travel" if action in TRAVEL_NAMES else action
    snapshot = {"activity_id": "actual-activity", "agent_id": 1, "kind": kind,
                "status": "active", "data": data, "travel_mode": extra.get("travel_mode")}
    tool, request = start_call(action, app)
    rows = [_public_event(1, "action_requested", {"tool_name": tool, "request": request}, "actual"),
            _public_event(2, "action_result", {"status": "in_progress", "activity_id": "actual-activity",
                                               "data": {"activities": [snapshot], "orders": []}}, "actual")]
    return [parse_world_event(row) for row in rows]


@pytest.mark.parametrize("content,app,object_type,key", [
    pytest.param("real-content", "real-app", "content", "real-content", id="app-content"),
    pytest.param(None, "real-app", "app", "real-app", id="app-browse"),
    pytest.param("real-content", None, "content", "real-content", id="location-unit"),
])
def test_interaction_reads_actual_object_and_never_infers_category_or_device(content, app, object_type, key):
    """用内容 app（副工具）与在地点做事（主工具）都只读活动快照里的实际对象，请求里写的名称不进公开证据。"""
    rows = worldmock_public_evidence(action_events(content=content, app=app), [])
    assert [(row.action, row.interaction, row.object_type, row.object_key, row.state) for row in rows] == [
        ("interact", "interact", object_type, key, "requested"),
        ("interact", "interact", object_type, key, "started")]
    assert all(row.attributes == {} for row in rows)
    public = json.dumps([row.field_values() for row in rows])
    assert "TITLE_CANARY" not in public and "DETAIL_CANARY" not in public


@pytest.mark.parametrize("action,mode,public", [("walk", "walking", "walking"),
    ("drive", "driving", "driving"), ("take_taxi", "taxi", "driving"), ("take_bus", "bus", "transit")])
def test_each_real_travel_action_uses_the_actual_trip_and_public_mode(action, mode, public):
    """请求只写 travel 与方式名称；公开动作取活动快照记下的具体出行动作，对象是实际行程。"""
    rows = worldmock_public_evidence(action_events(action, trip_id="real-trip", travel_mode=mode), [])
    assert [row.state for row in rows] == ["requested", "started"]
    assert all(row.action == action and row.interaction == "travel" and row.object_key == "real-trip" for row in rows)
    assert all(row.attributes == {"travel_mode": public} for row in rows)


@pytest.mark.parametrize("detail,public", [("步行", "walk"), ("驾车", "drive"), ("打车", "take_taxi"),
                                            ("公共交通", "take_bus"), ("骑车", "travel")])
def test_rejected_travel_without_an_activity_maps_the_written_method_name(detail, public):
    """被拒的出行没有活动快照：公开动作按请求所写的固定出行方式名称对应，对不上的名称归为 travel。"""
    request = main_arguments("travel", "TITLE_CANARY", detail)
    rows = [_public_event(1, "action_requested", {"tool_name": MAIN_TOOL, "request": request}, "rejected-travel"),
            _public_event(2, "action_result", {"status": "rejected", "data": {}}, "rejected-travel")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert [(row.action, row.interaction, row.object_type, row.object_key, row.state) for row in evidence] == [
        (public, "travel", "route", None, "requested"), (public, "travel", "route", None, "rejected")]
    assert all(row.attributes == {} for row in evidence)


def test_sleep_start_has_no_additional_public_point_fact():
    assert worldmock_public_evidence(action_events("sleep"), []) == ()


def test_real_start_group_projects_each_action_with_its_own_activity(engine_factory):
    """真实开始组（同一决策里主、副工具各一条）：两条动作各有请求与受理两条公开事实，对象取各自开始的那一项活动，
    证据事件只来自本条动作，两项互不串用。"""
    from tests.life.conftest import travel, use_app
    from tests.life.environment_runtime import act_group, events
    from worldmock.life.models import PlanDayRequest

    engine = engine_factory()
    engine.register_day_plan(1, PlanDayRequest(goals=()), [], [])
    _, (walk, watch) = act_group(engine, "pair", travel("公司"), use_app("哔哩哔哩"))
    assert (walk.status, watch.status) == ("in_progress", "in_progress"), (walk.error_code, watch.error_code)
    assert (walk.action_id, watch.action_id) == ("action-pair-1", "action-pair-2")
    trip = engine.context.state.activities[walk.activity_id]
    media = engine.context.state.activities[watch.activity_id]
    assert trip.data["action"] == "walk" and media.data["app_id"] == "bilibili" and media.data["content_id"]
    recorded = events(engine)
    owner = {row.event_id: row.action_id for row in recorded}
    evidence = worldmock_public_evidence(recorded, [])
    facts = {(row.action, row.interaction, row.object_type, row.object_key, row.state,
              tuple(owner[identity] for identity in row.source_event_ids)) for row in evidence}
    assert facts == {
        ("walk", "travel", "route", trip.data["trip_id"], "requested", ("action-pair-1",)),
        ("walk", "travel", "route", trip.data["trip_id"], "started", ("action-pair-1",)),
        ("interact", "interact", "content", media.data["content_id"], "requested", ("action-pair-2",)),
        ("interact", "interact", "content", media.data["content_id"], "started", ("action-pair-2",))}
    assert len(evidence) == 4


@pytest.mark.parametrize("kind", ["app_touch", "payment_notification", "balance_decreased"])
def test_system_and_removed_subsystem_events_do_not_create_public_behavior(kind):
    event = parse_world_event(_public_event(1, "environment_event", {"kind": kind}))
    assert worldmock_public_evidence([event], []) == ()


def test_purchase_payments_are_only_real_independent_facts_even_without_payment_app():
    event = parse_world_event(_public_event(1, "environment_event", {"kind": "purchase_payment",
        "activity_id": "real-purchase", "order_ids": ["order-a", "order-b"], "amount_fen": 1200}))
    rows = worldmock_public_evidence([event], [])
    assert {(row.object_key, row.interaction, row.state) for row in rows} == {
        ("order-a", "pay", "paid"), ("order-b", "pay", "paid")}


def test_delivery_and_receipts_remain_distinct_actual_order_facts():
    rows = [_public_event(1, "environment_event", {"kind": "delivery_arrival", "orders": [
        {"order_id": "order-a", "status": "delivered", "fulfillment": "delivery"},
        {"order_id": "order-b", "status": "ready_for_pickup", "fulfillment": "station_pickup"}]}),
        _public_event(2, "environment_event", {"kind": "goods_received", "order_id": "order-a", "category": "meal"}),
        _public_event(3, "environment_event", {"kind": "goods_received", "order_id": "order-b", "category": "goods"})]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {(row.object_key, row.interaction, row.state) for row in evidence} == {
        ("order-a", "deliver", "delivered"), ("order-b", "notify", "ready_for_pickup"),
        ("order-a", "receive", "received"), ("order-b", "receive", "received")}
    assert [row.attributes["goods_category"] for row in evidence if row.interaction == "receive"] == ["meal", "goods"]


@pytest.mark.parametrize("kind", ["wait", "action"])
def test_retired_activity_kind_is_rejected_before_object_inference(kind):
    with pytest.raises(EvaluationError, match="Activity snapshot uses a retired activity kind"):
        worldmock_activity_action(kind, {})


@pytest.mark.parametrize("kind,action", [("interact", "interact"), ("purchase", "purchase"),
    ("sleep", "sleep"), ("travel", "walk"), ("travel", "drive"), ("travel", "take_taxi"), ("travel", "take_bus")])
def test_activity_action_is_taken_directly_from_the_current_closed_contract(kind, action):
    assert worldmock_activity_action(kind, {"action": action}) == action
    with pytest.raises(EvaluationError, match="actual action"):
        worldmock_activity_action(kind, {})


def test_unknown_activity_kind_and_wrong_actual_action_are_rejected():
    with pytest.raises(EvaluationError, match="outside the current closed set"):
        worldmock_activity_action("invented", {"action": "interact"})
    with pytest.raises(EvaluationError, match="disagrees"):
        worldmock_activity_action("travel", {"action": "interact"})


@pytest.mark.parametrize("kind", ["mail_arrival", "work_content_completed"])
def test_retired_subsystem_facts_are_rejected_before_public_projection(kind):
    """退役事件在真实 Event 读取边界拒绝，不能成为隐式公开行为。"""
    with pytest.raises(ValueError, match="Environment event uses a retired kind"):
        _public_event(1, "environment_event", {"kind": kind})


def test_actual_system_callback_effect_does_not_create_public_behavior():
    """有效回调钳位由系统持有，不能推断用户公开动作。"""
    from worldmock.footprints.models import Event
    event = Event.model_validate({**_public_event(1, "environment_event", {"kind": "test_system_fact"}),
        "visibility": "system", "caused_by": ["real-trigger"], "data": {
            "kind": "callback_effect", "type": "state_delta", "rule_id": "actual-rule",
            "state": "energy", "delta": -1, "before": 100, "after": 99}})
    assert worldmock_public_evidence([event], []) == ()


@pytest.mark.parametrize("status", ["pending", "cancelled", "failed", "delivered"])
def test_order_snapshots_do_not_imply_payment_or_receipt(status):
    """接受、取消、失败和配送状态只有实际订单点事实，没有推断付款或收货。"""
    rows = [_public_event(1, "action_requested", {"tool_name": SIDE_TOOL,
                                                  "request": side_arguments("purchase", "实际商品")}, "purchase-action"),
        _public_event(2, "action_result", {"status": "completed", "data": {"orders": [
            {"order_id": "actual-order", "status": status, "fulfillment": "pickup"}]}}, "purchase-action")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {(row.interaction, row.state) for row in evidence} == {("order", "requested"), ("order", status)}
    assert all(row.object_key == "actual-order" for row in evidence)


def test_rejected_purchase_without_an_order_keeps_no_invented_order_identity():
    rows = [_public_event(1, "action_requested", {"tool_name": SIDE_TOOL,
                                                  "request": side_arguments("purchase", "实际商品")}, "rejected-action"),
        _public_event(2, "action_result", {"status": "rejected", "data": {}}, "rejected-action")]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows], [])
    assert {row.state for row in evidence} == {"requested", "rejected"}
    assert all(row.object_key is None for row in evidence)


@pytest.mark.parametrize("before,after", [(0, 0), (1, 0), (0, 1)])
def test_public_content_progress_requires_actual_positive_position(before, after):
    row = _public_event(1, "activity_progress", {"interval_start": 1, "interval_end": 2, "content": {
        "content_id": "actual-content", "position_before_seconds": before, "position_after_seconds": after,
        "completed": False}},
        activity_id="actual-activity")
    evidence = worldmock_public_evidence([parse_world_event(row)], [])
    assert len(evidence) == (1 if after > before else 0)
    if evidence:
        assert evidence[0].action == "interact" and evidence[0].object_key == "actual-content"


@pytest.mark.parametrize("tool,request_body", [
    pytest.param(MAIN_TOOL, {"items": [{"action": "sleep", "target": "家"}]}, id="main-retired-items"),
    pytest.param(MAIN_TOOL, {"action": "travel", "target": "公司"}, id="main-detail-missing"),
    pytest.param(MAIN_TOOL, {"action": "sleep", "target": "450", "detail": None, "way": "睡觉"}, id="main-extra-key"),
    pytest.param(MAIN_TOOL, {}, id="main-empty"),
    pytest.param(SIDE_TOOL, {"items": [{"action": "purchase", "target": "对象"}]}, id="side-retired-items"),
    pytest.param(SIDE_TOOL, {"action": "purchase", "target": ["对象"], "detail": None}, id="side-extra-detail"),
    pytest.param(SIDE_TOOL, {"action": "purchase"}, id="side-target-missing"),
])
def test_public_projection_rejects_start_arguments_outside_the_current_contract(tool, request_body):
    """两个开始工具各自的参数键集合是闭集：少键、多键和已删除的 items 形状都不读。"""
    row = _public_event(1, "action_requested", {"tool_name": tool, "request": request_body}, "invalid-action")
    with pytest.raises(EvaluationError, match="^Start request arguments are outside the current contract$") as error:
        worldmock_public_evidence([parse_world_event(row)], [])
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("tool,request_body", [
    pytest.param(MAIN_TOOL, main_arguments("purchase", "对象"), id="main-purchase"),
    pytest.param(MAIN_TOOL, main_arguments("walk", "公司", "步行"), id="main-retired-walk"),
    pytest.param(MAIN_TOOL, main_arguments("wait", "自己"), id="main-retired-wait"),
    pytest.param(SIDE_TOOL, side_arguments("sleep", "450"), id="side-sleep"),
    pytest.param(SIDE_TOOL, side_arguments("travel", "公司"), id="side-travel"),
    pytest.param(SIDE_TOOL, side_arguments("drive", "公司"), id="side-retired-drive"),
])
def test_public_projection_rejects_start_action_outside_the_tool_closed_set(tool, request_body):
    """动作只认各工具自己的枚举：主工具是 travel、interact、sleep，副工具是 purchase、interact；旧的四个出行动作不认。"""
    row = _public_event(1, "action_requested", {"tool_name": tool, "request": request_body}, "invalid-action")
    with pytest.raises(EvaluationError, match="^Start request action is outside the current closed set$") as error:
        worldmock_public_evidence([parse_world_event(row)], [])
    assert error.value.code == "invalid_input"


def test_public_projection_rejects_retired_activity_before_reading_other_fields():
    row = _public_event(1, "activity_progress", {"activity": {"kind": "work"}})
    with pytest.raises(EvaluationError, match="retired activity"):
        worldmock_public_evidence([parse_world_event(row)], [])
