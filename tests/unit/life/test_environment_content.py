"""容器推荐、真实消费与当前会话推送的回归。"""
from types import SimpleNamespace
from random import Random
import pytest
from worldmock.life.activities import ActivityStartParams, ActivityOrigin, start_activity
from worldmock.life.app_push import issue_app_push, validate_app_pushes
from worldmock.life.body import ActivityFactors, initial_body
from worldmock.life.body_config import assemble_body_config
from worldmock.life.content_select import ContentRequest, content_duration_range, eligible_contents, select_content
from worldmock.life.interact import InteractivityRuntime, _content_data
from worldmock.life.location_activities import location_units
from worldmock.life.models import (AgentState, AppPush, ContentProgress, SessionKind, SessionState, SessionStatus,
    WorldState, WORLD_SCHEMA_VERSION, ActivityStatus)
from worldmock.environment import load_environment
from tests.frozen_inputs import fixture_metadata
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

@pytest.fixture
def context_state():
    inputs = load_environment(ARTIFICIAL_ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)
    config = assemble_body_config(inputs.persona)
    body = initial_body(config, 8.0, inputs.persona, 0.06)
    start = int(inputs.config.start_time.timestamp())
    agent = AgentState(agent_id=1,name='林舟',location_id=inputs.initial_location_id,balance_account_id='agent:1',
        body=body,body_settled_at=start,next_decision_at=start)
    session = SessionState(session_id='stay',agent_id=1,kind=SessionKind.STAY,location_id=agent.location_id,
        status=SessionStatus.ACTIVE,started_at=start,name='家')
    state = WorldState(schema_version=WORLD_SCHEMA_VERSION,version=0,current_time=start,end_time=start+86400,
        run_id='content-test',timezone=inputs.config.timezone,persona_sha256=inputs.persona_sha256,
        config_sha256=inputs.config_sha256,agents={1:agent},accounts={'agent:1':100000,'external:scenario':-100000},
        sessions={'stay':session},random_seed=7,random_state='content-root')
    context = SimpleNamespace(inputs=inputs,catalog=inputs.catalog,persona=inputs.persona,config=inputs.config,body=config)
    return context,state

def test_app_and_location_pools_are_disjoint(context_state):
    """内容 app 的候选只来自它自己绑定的内容；地点的内容是人物点名的单元，不进任何 app 的候选，也不经推荐入口。"""
    context,state=context_state
    home=context.catalog.locations[state.agents[1].location_id]
    app = eligible_contents(context.catalog,state,ContentRequest(1,'bilibili'))
    place = [item for unit in location_units(context.catalog,home.kind) for item in unit.contents]
    assert {c.content_id for c in place} == {'c-chat','c-rest'}
    assert app and all('bilibili' in c.app_ids for c in app)
    assert not {c.content_id for c in app} & {c.content_id for c in place}
    for location in context.catalog.locations.values():
        assert eligible_contents(context.catalog,state,ContentRequest(1,location.location_id))==()
    assert location_units(context.catalog,'park')==()

def test_recent_shared_partial_content_has_priority_without_query_randomness(context_state):
    context,state=context_state
    content=context.catalog.contents['c-course']
    progress=ContentProgress(content_id=content.content_id,position_seconds=120,completed_rounds=0,
        last_consumed_at=state.current_time)
    state.agents[1].content_progress[content.content_id]=progress
    before=state.random_state
    assert content_duration_range(context.inputs,state,1,'bilibili')==(1680,1680)
    selected,pin=select_content(context.catalog,state,ContentRequest(1,'bilibili'),Random(7))
    assert selected.content_id=='c-course' and pin is None
    assert state.random_state==before

def test_current_app_push_pins_start_but_not_other_containers(context_state):
    context,state=context_state
    push=AppPush(push_id='push',content_id='c-drama-2',issued_at=state.current_time,status='pending')
    state.sessions['stay'].app_pushes['bilibili']=push
    selected,pin=select_content(context.catalog,state,ContentRequest(1,'bilibili','activity'),Random(7))
    assert selected.content_id=='c-drama-2' and pin=='push'
    assert content_duration_range(context.inputs,state,1,'bilibili')==(1800,1800)
    other,other_pin=select_content(context.catalog,state,ContentRequest(1,'douyin','other-activity'),Random(7))
    assert other_pin is None and 'douyin' in other.app_ids
    assert content_duration_range(context.inputs,state,1,'douyin')==(90,2400)
    validate_app_pushes(state,context.catalog)

def test_series_continues_same_binding_and_stops_after_last_episode(context_state):
    context,state=context_state
    content=context.catalog.contents['c-drama-1']
    data=_content_data(content,'bilibili',0,None)
    data['accepted_event_id']='actual-accepted-event'
    _,activity=start_activity(state.agents[1],ActivityStartParams('interact',1800,data=data),state.current_time,
        ActivityOrigin('action','decision','event'),context.body)
    activity=activity.model_copy(update={'media_position_seconds':1800,'elapsed_seconds':1800,
        'last_settled_at':state.current_time+1800,'status':ActivityStatus.COMPLETED,'ended_at':state.current_time+1800})
    state.activities[activity.activity_id]=activity
    state=state.model_copy(update={'current_time':state.current_time+1800})
    changed,evidence=InteractivityRuntime(context).advance(state,activity,state.current_time)
    assert evidence['completed'] is True
    assert changed.agents[1].content_progress['c-drama-1'].completed_rounds==1
    next_activity=changed.activities[activity.activity_id]
    assert next_activity.status==ActivityStatus.ACTIVE
    assert next_activity.data['content_id']=='c-drama-2'
    assert next_activity.data['draw_index']==1
    assert next_activity.planned_end_at==state.current_time+1800
    assert next_activity.data['pinned_by_push_id'] is None
    assert changed.random_state==state.random_state

def test_consumption_uses_shared_actual_position_and_repeated_boundary_has_no_effect(context_state):
    context,state=context_state
    content=context.catalog.contents['c-course']
    _,activity=start_activity(state.agents[1],ActivityStartParams('interact',1800,
        data={**_content_data(content,'bilibili',0,None), 'accepted_event_id':'actual-accepted-event'}),state.current_time,
        ActivityOrigin('action','decision','event'),context.body)
    activity=activity.model_copy(update={'media_position_seconds':60,'elapsed_seconds':60,
        'last_settled_at':state.current_time+60})
    state.activities[activity.activity_id]=activity
    state=state.model_copy(update={'current_time':state.current_time+60})
    changed,evidence=InteractivityRuntime(context).advance(state,activity,state.current_time)
    assert evidence['position_before_seconds']==0
    assert evidence['position_after_seconds']==60
    assert evidence['completed'] is False
    repeated,repeated_evidence=InteractivityRuntime(context).advance(changed,activity,state.current_time)
    assert repeated_evidence is None
    assert repeated.agents[1].content_progress==changed.agents[1].content_progress
    assert repeated.agents[1].body==state.agents[1].body


def test_single_content_and_last_series_episode_stop(context_state):
    context,state=context_state
    for content_id in ('c-course','c-drama-3'):
        content=context.catalog.contents[content_id]
        _,activity=start_activity(state.agents[1],ActivityStartParams('interact',content.duration_seconds,
            data=_content_data(content,'bilibili',0,None)),state.current_time,
            ActivityOrigin('action','decision','event'),context.body)
        activity=activity.model_copy(update={'media_position_seconds':content.duration_seconds,
            'elapsed_seconds':content.duration_seconds,'last_settled_at':state.current_time+content.duration_seconds,
            'status':ActivityStatus.COMPLETED,'ended_at':state.current_time+content.duration_seconds})
        staged=state.model_copy(update={'current_time':activity.last_settled_at,
            'activities':{activity.activity_id:activity}})
        changed,evidence=InteractivityRuntime(context).advance(staged,activity,staged.current_time)
        assert evidence['completed'] is True
        assert changed.activities[activity.activity_id].status==ActivityStatus.COMPLETED
        assert changed.activities[activity.activity_id].data['content_id']==content_id


def test_ordinary_push_only_selects_explicit_pushable_app_content(context_state):
    context,state=context_state
    event=SimpleNamespace(agent_id=1,scheduled_id='push-1-2026-01-05T08',due_at=state.current_time)
    changed,data=issue_app_push(state,event,context.catalog)
    content=context.catalog.contents[data['content_id']]
    assert content.pushable is True
    assert data['app_id'] in content.app_ids
    assert set(data)=={'kind','push_id','app_id','app','content_id','title','summary','session_id','issued_at'}
    assert changed.random_state==state.random_state
    validate_app_pushes(changed,context.catalog)


def test_strict_restore_rejects_retired_fields_and_wrong_effect_snapshot(context_state):
    from worldmock.errors import IntegrityError
    from worldmock.life.activity_validation import validate_activity_targets
    context,state=context_state
    content=context.catalog.contents['c-course']
    _,activity=start_activity(state.agents[1],ActivityStartParams('interact',1800,
        data={**_content_data(content,'bilibili',0,None), 'accepted_event_id':'actual-accepted-event'}),state.current_time,
        ActivityOrigin('action','decision','event'),context.body)
    state.activities[activity.activity_id]=activity
    validate_activity_targets(state,context.catalog,context.persona)
    for field,value in (('category','work_study'),('relaxation_relief_per_hour',0.5)):
        corrupt=activity.model_copy(update={'data':{**activity.data,field:value}})
        staged=state.model_copy(update={'activities':{activity.activity_id:corrupt}})
        with pytest.raises(IntegrityError,match='Persisted activity target is invalid'):
            validate_activity_targets(staged,context.catalog,context.persona)


def test_resume_reads_latest_shared_position_without_redraw(context_state):
    context,state=context_state
    content=context.catalog.contents['c-course']
    _,activity=start_activity(state.agents[1],ActivityStartParams('interact',1800,
        data=_content_data(content,'bilibili',0,None)),state.current_time,
        ActivityOrigin('action','decision','event'),context.body)
    state.activities[activity.activity_id]=activity
    state.agents[1].content_progress['c-course']=ContentProgress(content_id='c-course',position_seconds=300,
        completed_rounds=0,last_consumed_at=state.current_time)
    changed=InteractivityRuntime(context).resume(state,activity)
    assert changed.activities[activity.activity_id].media_position_seconds==300
    assert changed.activities[activity.activity_id].planned_end_at==state.current_time+1500
    assert changed.activities[activity.activity_id].data['draw_index']==0
    assert changed.random_state==state.random_state
