"""@brief 当前动作、设备、逐单收货与真实导出适配边界。"""

import json
from datetime import datetime, timedelta
from dataclasses import replace

import pytest

from tests.evaluation.test_adapters import _public_event
from tests.evaluation.conftest import make_evaluation_fixture
from tests.footprints.test_acs_export import finish, request, short_run
from tests.life.conftest import submit_round
from worldmock.evaluation.adapters import adapt_worldmock, parse_world_event
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.models import TimeWindow
from worldmock.evaluation.public_evidence import worldmock_activity_attributes, worldmock_public_evidence
from worldmock.evaluation.scenario import load_scenario
from worldmock.footprints.export import export_run


def action_events(category="entertainment", mode=None, app=None, manner="phone_continuous"):
    """@brief 名称故意与类别无关，证据必须只读实际快照。"""
    payload = {"items":[{"target":"办公看视频运动","way":"进行","app":app,"to":"同事","pay_app":None}],
               "answering":None}
    data = {"category":category,"content_id":"content-real","counterpart":"contact-real", "app_id":app,
            "manner":manner if app is not None else None,"communication_mode":mode,"title":"TITLE_CANARY"}
    rows = [_public_event(1,"action_requested",{"tool_name":"start_activity","request":payload},"action-real"),
            _public_event(2,"action_result",{"status":"completed","data":{"activity":{
                "kind":"action","status":"active","data":data}}},"action-real")]
    return [parse_world_event(row) for row in rows]


@pytest.mark.parametrize("manner,device", [("phone_continuous","phone"),("offline_assisted","phone"),
                                            ("other_device","other")])
def test_device_uses_unique_manner_mapping_only_when_snapshot_has_app(manner,device):
    assert worldmock_activity_attributes({"app_id":"real-app","manner":manner}) == {"device":device}
    assert worldmock_activity_attributes({"app_id":None,"manner":manner}) == {}
    rows = worldmock_public_evidence(action_events(app="real-app",manner=manner),[],{})
    assert all(item.attributes == {"device":device} for item in rows)
    with pytest.raises(EvaluationError,match="declared use manner"):
        worldmock_activity_attributes({"app_id":"real-app","manner":"phone"})


@pytest.mark.parametrize("category,interaction", [("entertainment","view"),("work_study","work"),
                                                   ("exercise","do"),("daily_care","do")])
def test_actual_category_and_content_identity_ignore_target_title_and_app(category,interaction):
    rows = worldmock_public_evidence(action_events(category),[],{"action":"unrelated"})
    assert {(item.category,item.interaction,item.object_type,item.object_key) for item in rows} == {
        (category,interaction,"content","content-real")}
    assert "TITLE_CANARY" not in json.dumps([item.field_values() for item in rows])


@pytest.mark.parametrize("mode,interaction", [("voice","call"),("text","chat"),(None,"chat")])
def test_social_partner_and_explicit_communication_mode_drive_public_interaction(mode,interaction):
    rows = worldmock_public_evidence(action_events("social",mode,app="chat-app" if mode else None),[],{})
    assert {(item.interaction,item.object_type,item.object_key) for item in rows} == {
        (interaction,"person","contact-real")}


def test_delivery_arrival_orders_and_goods_received_remain_distinct_per_order():
    rows = [_public_event(1,"environment_event",{"kind":"delivery_arrival","orders":[
        {"order_id":"o1","status":"delivered","fulfillment":"delivery"},
        {"order_id":"o2","status":"ready_for_pickup","fulfillment":"station_pickup"}]}),
        _public_event(2,"environment_event",{"kind":"goods_received","order_id":"o1","category":"meal"}),
        _public_event(3,"environment_event",{"kind":"goods_received","order_id":"o2","category":"fuel"})]
    evidence = worldmock_public_evidence([parse_world_event(row) for row in rows],[],{})
    assert {(item.object_key,item.interaction,item.state) for item in evidence} == {
        ("o1","deliver","delivered"),("o2","notify","ready_for_pickup"),
        ("o1","receive","received"),("o2","receive","received")}
    assert all(item.interaction != "pay" for item in evidence)
    assert [item.attributes["goods_category"] for item in evidence if item.interaction == "receive"] == ["meal","fuel"]
    assert next(item.attributes for item in evidence if item.object_key=="o2" and item.interaction=="notify") == {
        "fulfillment_mode":"station_pickup"}


def test_real_engine_export_adapts_current_category_device_payment_and_receipt(engine_factory,tmp_path):
    def full_day(document):
        short_run(document)
        document["end_at"] = (datetime.fromisoformat(document["start_at"])+timedelta(days=1)).isoformat()
    engine = engine_factory(mutate=full_day)
    _, bought = submit_round(engine,{1:("start_activity",request("牛肉面","购买","美团","支付宝"))},"buy-real")
    assert bought[0].error_code is None
    _, started = submit_round(engine,{1:("start_activity",request("刷推荐","进行","抖音"))},"play-real")
    assert started[0].error_code is None
    point = finish(engine)
    directory = tmp_path / "engine-export"
    manifest = export_run(engine.context.store,point.last_event_seq,directory)
    scenario = load_scenario(make_evaluation_fixture(tmp_path / "comparison").scenario_path)
    state = engine.context.state
    window = TimeWindow(datetime.fromisoformat(engine.context.scenario.start_at.isoformat()),
                        datetime.fromtimestamp(state.end_time,scenario.window.end.tzinfo),scenario.window.timezone)
    view = adapt_worldmock(directory,replace(scenario,window=window))
    assert manifest["schema_version"] == 4 and manifest["checkpoint"]["state"]["schema_version"] == 11
    assert any(activity.category=="entertainment" and activity.duration_kind=="world_internet_session"
               for activity in view.activities)
    orders = {item.object_key for item in view.public_evidence if item.interaction=="pay"}
    received = {item.object_key for item in view.public_evidence if item.interaction=="receive"}
    assert orders == received and len(received)==1
    assert any(item.attributes.get("device")=="phone" for item in view.public_evidence)
