"""@brief 当前动作在工具呈现、受理、预估与身体结算之间共用同一固定事实。"""

from dataclasses import replace

import pytest

from tests.life.action_contract_world import contract_world
from tests.life.test_content_select import registered_content_world, reserve
from worldmock.catalog.affordances import engine_affordances
from worldmock.exchange.constants import CATEGORY_MET
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import activity_factors
from worldmock.life.content_select import ContentRequest, content_duration_range
from worldmock.life.models import ActivityKind, BodyState
from worldmock.life.readable import duration_phrase
from worldmock.life.schedule import DayPlan, PlanGoal, activity_family, progress_day_plan
from worldmock.life.tool_layer import _route, tool_layer


def test_action_duration_matches_selector_and_tool_layer():
    context,_=contract_world()
    bounds=content_duration_range(context.semantics.catalog,context.scenario,context.state,
                                 ContentRequest(1,"run","keep"))
    assert bounds == (1200,1200)
    row=next(row for row in tool_layer(context,{})["targets"] if row["name"]=="跑步")
    assert row["actions"][0]["duration"] == duration_phrase(bounds[0])


def test_declared_sleep_channel_does_not_control_body_sleep_settlement(engine_factory,monkeypatch):
    from worldmock.catalog import affordances
    from worldmock.life import activities,body
    from worldmock.life.body import ActivityFactors,ENTERTAINMENT_RESOURCE,last_flows,settle_body,settle_shared_body
    engine=engine_factory()
    registry=engine_affordances()
    changed=replace(registry,kinds={**registry.kinds,"sleep":replace(registry.kinds["sleep"],sleep_channel="awake")})
    monkeypatch.setattr(affordances,"engine_affordances",lambda:changed)
    monkeypatch.setattr(body,"engine_affordances",lambda:changed)
    monkeypatch.setattr(activities,"_AFFORDANCES",changed)
    config=engine.context.scenario.body
    before=BodyState(energy_kcal=1600,sleep_pressure=0.6,sleep_deficit_hours=4,entertainment_need=0.3)
    factors=ActivityFactors(config.activity_met["sleep"])
    for settle in (lambda:settle_body(before,ActivityKind.SLEEP,3600,config,factors),
                   lambda:settle_shared_body(before,((ActivityKind.SLEEP,factors),),3600,config)):
        last_flows()
        result=settle()
        assert result.after.sleep_deficit_hours == pytest.approx(3)
        assert result.after.sleep_pressure < before.sleep_pressure
        assert result.after.entertainment_need == before.entertainment_need
        assert result.after.energy_kcal < before.energy_kcal
        assert not [flow for flow in last_flows() if flow.resource==ENTERTAINMENT_RESOURCE]


def test_travel_plan_matches_tool_layer_admission_and_engine_validation(engine_factory):
    from tests.life.conftest import submit_round
    from tests.life.test_name_resolution import start_payload
    from worldmock.life.models import StartActivityRequest,ActivityStartParams
    from worldmock.life.travel import require_travel_duration
    def car(document):
        document["agents"][0].update(owns_car=True,initial_driving_days=7)
    engine=engine_factory(mutate=car)
    context=engine.observations._layer_context(engine.context.state.agents[1],[])
    trip,seconds=_route(context,"office","driving")
    _,results=submit_round(engine,{1:("start_activity",StartActivityRequest.model_validate(
        start_payload("公司","驾车")))},"shared-trip")
    assert results[0].error_code is None
    accepted=engine.context.state.activities[results[0].activity_id]
    assert accepted.planned_duration_seconds==trip.duration_seconds
    wrong=ActivityStartParams(activity_type="travel",planned_duration_seconds=trip.duration_seconds+1,
                              destination_id="office",travel_mode="driving")
    with pytest.raises(BusinessRuleError,match="ROUTE_DURATION_MISMATCH"):
        require_travel_duration(engine.context.scenario,engine.context.semantics,"home",wrong,
                                engine.context.state.current_time)


@pytest.mark.parametrize("action,app", [("run","keep"),("quiet-rest",None),("browse-feed","douyin"),
                                       ("office-work",None),("study-course","bilibili")])
def test_fixed_category_met_is_shared_by_current_action_snapshot_and_settlement(action,app):
    semantics,state=registered_content_world()
    registered=semantics.catalog.actions[action]
    content=next(item for item in semantics.catalog.contents.values()
                 if item.action_id==action and (app is None or app in item.app_ids))
    reserve(semantics,state,content.content_id,status="active",activity_id="met-action")
    activity=state.activities["met-action"]
    assert activity_factors(activity,state.agents[1],semantics.body).met == CATEGORY_MET[registered.category][app is not None]


@pytest.mark.parametrize("category", [None,"","unknown"])
def test_unknown_action_category_does_not_advance_the_plan(category):
    plan=DayPlan(day_end=1000,goals=(PlanGoal(intent_kind="运动",goal="运动一下",registered_at=0),))
    activity={"activity_id":"a","kind":"action","data":{"category":category},"status":"active"}
    assert activity_family(activity) is None
    assert progress_day_plan(plan,activity,100,200,())==plan


@pytest.mark.parametrize("mode", ["text","voice",None])
def test_social_category_advances_the_social_goal(mode):
    plan=DayPlan(day_end=1000,goals=(PlanGoal(intent_kind="社交",goal="聊一下",registered_at=0),))
    activity={"activity_id":"a","kind":"action","data":{"category":"social","communication_mode":mode},"status":"active"}
    assert activity_family(activity)=="social"
    assert progress_day_plan(plan,activity,100,200,()).goals[0].progress_activity_id=="a"


@pytest.mark.parametrize("interval", [(True,200),(100,False),(100.0,200),(100,200.0),
                                      (float("nan"),200),(100,float("inf")),(200,200),(200,100)])
def test_invalid_interval_does_not_advance_the_plan(interval):
    plan=DayPlan(day_end=1000,goals=(PlanGoal(intent_kind="社交",goal="聊一下",registered_at=0),))
    activity={"activity_id":"a","kind":"action","data":{"category":"social"},"status":"active"}
    assert progress_day_plan(plan,activity,*interval,())==plan


def test_visible_mail_match_content_revelation_and_observation(engine_factory):
    from worldmock.life.content import visible_mail
    def mail(document):
        document["messages"]=[{"message_id":"shared-mail","thread_id":"shared-thread","recipient_agent_id":1,
            "sent_at":"2026-01-05T00:01:00+08:00","subject":"共享邮件","body":"测试正文"}]
    engine=engine_factory(mutate=mail)
    agent=engine.context.state.agents[1]
    message=engine.context.scenario.messages[0]
    assert visible_mail(agent,engine.context.rules.messages)==engine.observations._event_tool_support(agent,())["messages"]==[]
    visible=agent.model_copy(update={"visible_message_ids":{message.message_id}},deep=True)
    expected=[{"message_id":message.message_id,"thread_id":message.thread_id,"subject":message.subject,"body":message.body}]
    assert visible_mail(visible,engine.context.rules.messages)==expected
    assert engine.observations._event_tool_support(visible,())["messages"]==expected
