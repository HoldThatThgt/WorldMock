"""容器推荐、真实消费与当前会话推送的回归。"""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from random import Random
import pytest
from worldmock.catalog.models import OpeningHours
from worldmock.life.activities import ActivityStartParams, ActivityOrigin, start_activity
from worldmock.life.app_push import issue_app_push, validate_app_pushes
from worldmock.life.body import ActivityFactors, initial_body
from worldmock.life.body_config import assemble_body_config
from worldmock.life.content_prerequisite import prerequisite_unmet
from worldmock.life.content_select import (ContentRequest, OpeningDuration, eligible_contents, opening_duration,
    pinned_push_content, select_content, select_from_contents)
from worldmock.life.interact import InteractivityRuntime, _content_data
from worldmock.life.location_activities import location_units
from worldmock.life.models import (AgentState, AppPush, ContentProgress, SessionKind, SessionState, SessionStatus,
    WorldState, WORLD_SCHEMA_VERSION, ActivityStatus)
from worldmock.environment import load_environment
from tests.frozen_inputs import fixture_metadata
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.life.prerequisite_inputs import (CLOCK, CLOCK_PREREQUISITE, app_content, dingtalk_environment, moment,
    weekday_hours)

def world_at(root, when=None, location_id='home', origin_id='home'):
    """给定环境的最小真实世界：人物在指定时刻停留在指定地点。

    location_id 为 None 时人在路上：从 origin_id 出发去办公室，从办公室出发则回家；照引擎出发时的做法，
    人物记录的地点仍是出发地，只有当前会话是一段路。
    """
    inputs = load_environment(root, metadata_loader=fixture_metadata)
    config = assemble_body_config(inputs.persona)
    body = initial_body(config, 8.0, inputs.persona)
    start = int(inputs.config.start_time.timestamp()) if when is None else when
    agent = AgentState(agent_id=1,name='林舟',location_id=location_id or origin_id,balance_account_id='agent:1',
        body=body,body_settled_at=start,next_decision_at=start)
    if location_id is None:
        destination_id = 'home' if origin_id == 'office' else 'office'
        session = SessionState(session_id='stay',agent_id=1,kind=SessionKind.TRANSIT,origin_id=origin_id,
            destination_id=destination_id,status=SessionStatus.ACTIVE,started_at=start,
            name=f'去{inputs.catalog.locations[destination_id].display_name}的路上')
    else:
        session = SessionState(session_id='stay',agent_id=1,kind=SessionKind.STAY,location_id=location_id,
            status=SessionStatus.ACTIVE,started_at=start,name=inputs.catalog.locations[location_id].display_name)
    state = WorldState(schema_version=WORLD_SCHEMA_VERSION,version=0,current_time=start,end_time=start+86400,
        run_id='content-test',timezone=inputs.config.timezone,persona_sha256=inputs.persona_sha256,
        config_sha256=inputs.config_sha256,agents={1:agent},accounts={'agent:1':100000,'external:scenario':-100000},
        sessions={'stay':session},random_seed=7,random_state='content-root')
    context = SimpleNamespace(inputs=inputs,catalog=inputs.catalog,persona=inputs.persona,config=inputs.config,body=config)
    return context,state

@pytest.fixture
def context_state():
    return world_at(ARTIFICIAL_ENVIRONMENT_ROOT)

def test_app_and_location_pools_are_disjoint(context_state):
    """内容 app 的候选只来自它自己绑定的内容；地点的内容是人物点名的单元，不进任何 app 的候选，也不经推荐入口。"""
    context,state=context_state
    home=context.catalog.locations[state.agents[1].location_id]
    app = eligible_contents(context.catalog,state,ContentRequest(1,'bilibili','home'))
    place = [item for unit in location_units(context.catalog,home.kind) for item in unit.contents]
    assert {c.content_id for c in place} == {'c-chat','c-rest'}
    assert app and all('bilibili' in c.app_ids for c in app)
    assert not {c.content_id for c in app} & {c.content_id for c in place}
    for location in context.catalog.locations.values():
        assert eligible_contents(context.catalog,state,ContentRequest(1,location.location_id,'home'))==()
    assert location_units(context.catalog,'park')==()

def test_recent_shared_partial_content_has_priority_without_query_randomness(context_state):
    context,state=context_state
    content=context.catalog.contents['c-course']
    progress=ContentProgress(content_id=content.content_id,position_seconds=120,completed_rounds=0,
        last_consumed_at=state.current_time)
    state.agents[1].content_progress[content.content_id]=progress
    before=state.random_state
    assert opening_duration(context.catalog,state,ContentRequest(1,'bilibili','home'))==OpeningDuration(True,1680,1680)
    selected,pin=select_content(context.catalog,state,ContentRequest(1,'bilibili','home'),Random(7))
    assert selected.content_id=='c-course' and pin is None
    assert state.random_state==before

def test_current_app_push_pins_start_but_not_other_containers(context_state):
    context,state=context_state
    push=AppPush(push_id='push',content_id='c-drama-2',issued_at=state.current_time,status='pending')
    state.sessions['stay'].app_pushes['bilibili']=push
    selected,pin=select_content(context.catalog,state,ContentRequest(1,'bilibili','home','activity'),Random(7))
    assert selected.content_id=='c-drama-2' and pin=='push'
    assert opening_duration(context.catalog,state,ContentRequest(1,'bilibili','home'))==OpeningDuration(True,1800,1800)
    other,other_pin=select_content(context.catalog,state,ContentRequest(1,'douyin','home','other-activity'),
                                   Random(7))
    assert other_pin is None and 'douyin' in other.app_ids
    assert opening_duration(context.catalog,state,ContentRequest(1,'douyin','home'))==OpeningDuration(False,90,2400)
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
    """没有可接内容的单条与没有别的开始了的系列的最后一集放完就停：Keep 唯一的一条不可推送，最后一集之后没有别的
    开始了没看完的系列。"""
    context,state=context_state
    for content_id,app_id in (('c-run','keep'),('c-drama-3','bilibili')):
        content=context.catalog.contents[content_id]
        _,activity=start_activity(state.agents[1],ActivityStartParams('interact',content.duration_seconds,
            data=_content_data(content,app_id,0,None)),state.current_time,
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


NOTICE = app_content("dingtalk-notice", "公告", duration_seconds=300)
LUNCH = app_content("dingtalk-lunch", "午间打卡", {"opens": weekday_hours(("12:00", "13:00"))}, duration_seconds=120)


def local(day, hour, minute=0, second=0):
    """2026 年 10 月某日的本地时刻（前提判定只收已换算的本地时刻）。"""
    return datetime(2026, 10, day, hour, minute, second)


def dingtalk_world(tmp_path, contents=(CLOCK,), when=(12, 8, 55), location_id="office"):
    """带钉钉内容的夹具副本上的最小真实世界：缺省是周一 08:55 人在办公室。"""
    return world_at(dingtalk_environment(tmp_path / "input", *contents), moment(*when), location_id)


def identities(contents):
    return [item.content_id for item in contents]


def test_prerequisite_unmet_judges_location_and_time_separately_in_fixed_order(tmp_path):
    """内容前提的四种组合：满足、只差地点、只差时间、两项都差；没写前提的内容总是满足。"""
    context, _ = dingtalk_world(tmp_path, (CLOCK, LUNCH, NOTICE))
    catalog = context.catalog
    clock, office, home = catalog.contents["dingtalk-clock"], catalog.locations["office"], catalog.locations["home"]
    inside, outside = local(12, 8, 55), local(12, 12, 30)
    assert prerequisite_unmet(clock, office, inside) == ()
    assert prerequisite_unmet(clock, home, inside) == ("content_location",)
    assert prerequisite_unmet(clock, office, outside) == ("content_time",)
    assert prerequisite_unmet(clock, home, outside) == ("content_location", "content_time")
    assert prerequisite_unmet(catalog.contents["dingtalk-notice"], home, outside) == ()
    assert prerequisite_unmet(catalog.contents["c-article"], None, outside) == ()
    # 只比较地点类型原文，不看该地点自己此刻是否营业：关着门的办公室仍满足地点前提
    closed = office.model_copy(update={"opens": OpeningHours(weekly={})})
    assert not closed.is_open(inside) and prerequisite_unmet(clock, closed, inside) == ()


def test_person_on_the_road_has_no_stay_location_for_a_prerequisite(tmp_path):
    """人在路上既不算在出发地也不算在目的地：带地点前提的内容不满足，只带时段前提的内容照常按时刻判定。"""
    context, _ = dingtalk_world(tmp_path, (CLOCK, LUNCH))
    clock, lunch = (context.catalog.contents[key] for key in ("dingtalk-clock", "dingtalk-lunch"))
    assert prerequisite_unmet(clock, None, local(12, 8, 55)) == ("content_location",)
    assert prerequisite_unmet(clock, None, local(12, 12, 30)) == ("content_location", "content_time")
    assert prerequisite_unmet(lunch, None, local(12, 12, 30)) == ()
    assert prerequisite_unmet(lunch, None, local(12, 13, 0)) == ("content_time",)


def test_prerequisite_window_includes_its_start_and_excludes_its_end(tmp_path):
    """时段含头不含尾，秒不参与：09:59:59 算在 08:00–10:00 内，10:00:00 不算；weekly 没写的星期整天不可用。"""
    context, _ = dingtalk_world(tmp_path)
    clock, office = context.catalog.contents["dingtalk-clock"], context.catalog.locations["office"]
    for inside in (local(12, 8), local(12, 9, 59, 59), local(12, 17), local(12, 19, 59, 59), local(16, 9)):
        assert prerequisite_unmet(clock, office, inside) == (), inside
    for outside in (local(12, 7, 59, 59), local(12, 10), local(12, 16, 59, 59), local(12, 20), local(17, 9),
                    local(18, 17, 30)):
        assert prerequisite_unmet(clock, office, outside) == ("content_time",), outside


def test_prerequisite_exception_date_replaces_the_weekday_hours(tmp_path):
    """特殊日期整日替换该星期的时段：写成空数组那天整天不可用，写了时段的那天只认这些时段。"""
    hours = {"weekly": {"mon": [["08:00", "10:00"]]},
             "exceptions": {"2026-10-12": [], "2026-10-13": [["12:00", "13:00"]]}}
    context, _ = dingtalk_world(tmp_path, (app_content("dingtalk-clock", "考勤打卡", {"opens": hours}),))
    clock = context.catalog.contents["dingtalk-clock"]
    assert prerequisite_unmet(clock, None, local(12, 9)) == ("content_time",)
    assert prerequisite_unmet(clock, None, local(13, 12, 30)) == ()
    assert prerequisite_unmet(clock, None, local(13, 9)) == ("content_time",)
    assert prerequisite_unmet(clock, None, local(19, 9)) == ()


@pytest.mark.parametrize("when,location_id,expected", [
    ((12, 8, 55), "office", ["dingtalk-clock", "dingtalk-notice"]),
    ((12, 8, 55), "home", ["dingtalk-notice"]),
    ((12, 8, 55), None, ["dingtalk-notice"]),
    ((12, 12, 30), "office", ["dingtalk-lunch", "dingtalk-notice"]),
    ((12, 12, 30), None, ["dingtalk-lunch", "dingtalk-notice"]),
    ((17, 12, 30), "office", ["dingtalk-notice"]),
], ids=["office-morning", "home-morning", "road-morning", "office-noon", "road-noon", "saturday"])
def test_eligible_contents_keep_only_what_can_start_here_and_now(tmp_path, when, location_id, expected):
    context, state = dingtalk_world(tmp_path, (CLOCK, LUNCH, NOTICE), when, location_id)
    request = ContentRequest(1, "dingtalk", location_id)
    assert identities(eligible_contents(context.catalog, state, request)) == expected
    # 别的 app 的内容没有前提，停留地点不影响它们
    assert identities(eligible_contents(context.catalog, state, ContentRequest(1, "keep", location_id))) == ["c-run"]


def test_unfinished_content_that_cannot_start_now_does_not_block_the_rest_of_the_app(tmp_path):
    """没做完的打卡在家不合格：不筛时按「最近没做完」挑中它，筛后挑中另一条；回到办公室后从原位置接着做。"""
    context, state = dingtalk_world(tmp_path, (CLOCK, NOTICE), location_id="home")
    state.agents[1].content_progress["dingtalk-clock"] = ContentProgress(
        content_id="dingtalk-clock", position_seconds=20, completed_rounds=0, last_consumed_at=state.current_time)
    request = ContentRequest(1, "dingtalk", "home", "activity")
    everything = tuple(context.catalog.contents[key] for key in ("dingtalk-clock", "dingtalk-notice"))
    assert select_from_contents(everything, state, request, Random(7))[0].content_id == "dingtalk-clock"
    assert select_content(context.catalog, state, request, Random(7)) == (context.catalog.contents["dingtalk-notice"],
                                                                          None)
    assert opening_duration(context.catalog, state, request) == OpeningDuration(False, 300, 300)
    back = replace(request, stay_location_id="office")
    assert select_content(context.catalog, state, back, Random(7))[0].content_id == "dingtalk-clock"
    assert opening_duration(context.catalog, state, back) == OpeningDuration(True, 40, 40)


@pytest.mark.parametrize("seed", [1, 7, 11, 23])
def test_filter_keeps_the_order_and_random_draws_of_an_app_without_prerequisites(tmp_path, seed):
    """合格集合内的挑选次序和随机流消耗与没有前提时相同；筛选本身不抽取。"""
    plain = [app_content(f"dingtalk-{letter}", f"内容{letter}", duration_seconds=60 * index)
             for index, letter in enumerate("bcd", start=1)]
    gated = [{**row, "prerequisite": CLOCK_PREREQUISITE} for row in plain]
    late = app_content("dingtalk-a", "夜间内容", {"opens": weekday_hours(("22:00", "23:00"))})
    open_world = world_at(dingtalk_environment(tmp_path / "plain", *plain), moment(12, 8, 55), "office")
    gated_world = world_at(dingtalk_environment(tmp_path / "gated", late, *gated), moment(12, 8, 55), "office")
    request = ContentRequest(1, "dingtalk", "office", "activity")
    picks, streams = [], []
    for context, state in (open_world, gated_world):
        assert identities(eligible_contents(context.catalog, state, request)) == [row["content_id"] for row in plain]
        stream = Random(seed)
        content, pinned = select_content(context.catalog, state, request, stream)
        picks.append((content.content_id, pinned))
        streams.append(stream.getstate())
        assert opening_duration(context.catalog, state, request) == OpeningDuration(False, 60, 180)
    assert picks[0] == picks[1] and streams[0] == streams[1]
    assert streams[0] != Random(seed).getstate()


def test_duration_range_counts_only_eligible_contents(tmp_path):
    context, state = dingtalk_world(tmp_path, (CLOCK, LUNCH, NOTICE))
    catalog = context.catalog
    assert opening_duration(catalog, state, ContentRequest(1, "dingtalk", "office")) == OpeningDuration(False, 60, 300)
    assert opening_duration(catalog, state, ContentRequest(1, "dingtalk", "home")) == OpeningDuration(False, 300, 300)
    # 没有登记内容的 app 没有合格内容，没有时长
    assert opening_duration(catalog, state, ContentRequest(1, "meituan", None)) is None


def test_per_item_range_counts_contents_autoplay_does_not_chain(tmp_path):
    """60 秒的不可推送内容与 300 秒的可推送内容同时合格：「每条」的范围取全部合格内容，不只取可推送的，是 60 到 300 秒。"""
    context, state = dingtalk_world(tmp_path, (app_content("dingtalk-a", "甲", duration_seconds=60),
                                               app_content("dingtalk-b", "乙", duration_seconds=300, pushable=True)))
    request = ContentRequest(1, "dingtalk", "office")
    assert opening_duration(context.catalog, state, request) == OpeningDuration(False, 60, 300)


def test_app_whose_contents_are_all_ineligible_has_no_duration(tmp_path):
    context, state = dingtalk_world(tmp_path, location_id="home")
    request = ContentRequest(1, "dingtalk", "home")
    assert eligible_contents(context.catalog, state, request) == ()
    assert opening_duration(context.catalog, state, request) is None


def test_pushed_content_is_pinned_only_while_it_is_eligible(tmp_path):
    """被推送的内容不合格时不钉住、按没有这条推送来挑；同一段停留里时段回来后重新钉住。"""
    morning = app_content("dingtalk-morning", "晨会纪要", {"opens": weekday_hours(("08:00", "10:00"))},
                          pushable=True)
    context, state = dingtalk_world(tmp_path, (morning, NOTICE), when=(12, 9, 30))
    catalog = context.catalog
    state.sessions["stay"].app_pushes["dingtalk"] = AppPush(push_id="push", content_id="dingtalk-morning",
                                                         issued_at=moment(12, 9), status="pending")
    request = ContentRequest(1, "dingtalk", "office", "activity")

    def pinned(world):
        return pinned_push_content(eligible_contents(catalog, world, request), world, request)

    assert pinned(state) == (catalog.contents["dingtalk-morning"], "push")
    assert select_content(catalog, state, request, Random(7)) == (catalog.contents["dingtalk-morning"], "push")
    assert opening_duration(catalog, state, request) == OpeningDuration(True, 60, 60)
    lapsed = state.model_copy(update={"current_time": moment(12, 11)}, deep=True)
    assert pinned(lapsed) is None
    assert select_content(catalog, lapsed, request, Random(7)) == (catalog.contents["dingtalk-notice"], None)
    assert opening_duration(catalog, lapsed, request) == OpeningDuration(False, 300, 300)
    assert lapsed.sessions["stay"].app_pushes["dingtalk"].status == "pending"
    returned = state.model_copy(update={"current_time": moment(13, 8, 30)}, deep=True)
    assert pinned(returned) == (catalog.contents["dingtalk-morning"], "push")
    released = state.model_copy(deep=True)
    released.sessions["stay"].app_pushes["dingtalk"].status = "released"
    assert pinned(released) is None
    validate_app_pushes(lapsed, catalog)
