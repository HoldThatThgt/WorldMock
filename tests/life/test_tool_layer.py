"""@brief 动作目标、应用选择、推送与纯资格的真实登记环境合同。"""

import copy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from tests.life.action_contract_world import contract_world
from tests.life.test_name_resolution import start_payload
from worldmock.errors import BusinessRuleError
from worldmock.life import admission, sessions
from worldmock.life.callable_index import INDEX_KEYS, callable_index
from worldmock.life.content_select import ContentRequest, content_duration_range, select_content
from worldmock.life.models import AppPush, ContentProgress, StartActivityRequest
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.prompt_schemas import PROGRESS_LAYER_SCHEMA, TARGET_KINDS, validate_layer
from worldmock.life.readable import duration_phrase, iso_time
from worldmock.life.tool_layer import Candidate, refusal_for, tool_layer
from worldmock.life.vocabulary import WAYS, way_options


def action_row(layer, name, app=None):
    target = next(row for row in layer["targets"] if row["name"] == name)
    return next(row for row in target["actions"] if row["app"] == app)


def test_all_registered_actions_listed_with_explicit_apps_and_no_app_targets():
    context, _ = contract_world()
    index = callable_index(context)
    layer = tool_layer(context, index)
    actions = [row for row in layer["targets"] if row["kind"] == "action"]
    assert {row["name"] for row in actions} == {"刷推荐", "办公", "学课程", "语音通话", "聊天", "跑步", "安静休息"}
    assert not {"content", "game", "catalog", "app", "task", "lifestyle"} & {row["kind"] for row in layer["targets"]}
    assert set(index) == set(INDEX_KEYS) == {"goods", "navigation_apps", "payment_apps", "query_categories"}
    assert index["navigation_apps"] == ["高德地图"] and index["payment_apps"] == ["支付宝", "微信"]
    assert {row["app"] for row in next(row for row in actions if row["name"] == "刷推荐")["actions"]} == {
        "哔哩哔哩", "抖音"}
    validate_layer(layer)


def test_empty_action_tool_reason_matches_actual_content_exhaustion():
    from random import Random

    context, _ = contract_world()
    context = replace(context, agent_id=2)
    row = action_row(tool_layer(context, callable_index(context)), "办公")
    assert row["available"] is False and row["reason"] == "Nothing is left to do for this action."
    assert row["duration"] is None
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        select_content(context.semantics.catalog, context.scenario, context.state,
                       ContentRequest(2, "office-work", None), Random(7))


@pytest.mark.parametrize("status", ["pending", "opened", "released"])
def test_current_push_title_is_visible_only_for_its_bound_action_and_app_until_released(status):
    context, _ = contract_world()
    sessions.current_session(context.state, 1).app_pushes["douyin"] = AppPush(
        push_id="push-real", content_id="c-short", issued_at=context.state.current_time, status=status)
    before = context.state.model_dump(mode="json")
    layer = tool_layer(context, callable_index(context))
    douyin = action_row(layer, "刷推荐", "抖音")
    bilibili = action_row(layer, "刷推荐", "哔哩哔哩")
    assert ("有推送：《测试短视频》" in douyin["note"]) == (status != "released")
    assert "有推送" not in bilibili["note"] and "短视频）" not in douyin["note"]
    assert context.state.model_dump(mode="json") == before


def test_retired_open_watch_game_and_food_ways_have_no_compatibility_synonyms():
    assert set(WAYS) == {"步行", "驾车", "睡觉", "等待", "购买", "进行", "领取"}
    for written in ("游玩", "玩游戏", "观看", "听", "继续看", "重看", "打开", "刷", "用一下", "食用", "给予"):
        assert way_options(written) == frozenset()
    for written in ("进行", "做", "参加"):
        assert way_options(written) == {"进行"}


def test_action_targets_and_push_schema_are_closed_without_content_kind_or_private_identity():
    context, _ = contract_world()
    assert set(TARGET_KINDS) == {"location", "product", "action", "person", "contact", "self", "activity"}
    schema = PROGRESS_LAYER_SCHEMA["properties"]["events"]["items"]
    record = {"occurred_at": iso_time(context.state.current_time, context.scenario.timezone), "type": "app_push",
              "app": "抖音", "action": "刷推荐", "title": "测试短视频", "summary": "字" * 200}
    validator = Draft202012Validator(schema)
    validator.validate(record)
    for changed in ({**record, "content_id": "hidden"}, {**record, "kind": "短视频"},
                    {**record, "summary": "字" * 201}, {key: value for key, value in record.items() if key != "action"}):
        assert list(validator.iter_errors(changed))
    validate_layer(tool_layer(context, callable_index(context)))


def test_action_duration_matches_read_only_selector_and_actual_admission():
    context, _ = contract_world()
    row = next(row for row in context.bindings if row["kind"] == "action" and row["object_id"] == "run")
    facts = admission.WorldFacts(context.semantics.catalog, context.scenario, context.state, 1)
    item = admission.selection_item(row, row["actions"][0], facts)
    assert item.claims == {"exercise"}
    assert refusal_for(context, Candidate("start_activity", row, row["actions"][0])) is None
    expected = content_duration_range(context.semantics.catalog, context.scenario, context.state,
                                     ContentRequest(1, "run", "keep"))
    assert expected == (1200, 1200)
    layer = tool_layer(context, callable_index(context))
    assert action_row(layer, "跑步", "Keep")["duration"] == duration_phrase(1200)
    assert action_row(layer, "跑步", "Keep")["note"] == "借助 app 安排或记录线下行为"


def test_most_recent_unfinished_content_duration_is_remaining_and_observation_does_not_select():
    context, _ = contract_world()
    context.state.agents[1].content_progress["c-long"] = ContentProgress(
        content_id="c-long", position_seconds=2350, completed_rounds=0, last_consumed_at=context.state.current_time)
    before = context.state.model_dump(mode="json")
    layer = tool_layer(context, callable_index(context))
    assert action_row(layer, "刷推荐", "哔哩哔哩")["duration"] == duration_phrase(50)
    assert action_row(layer, "刷推荐", "抖音")["duration"] == duration_phrase(50)
    assert context.state.model_dump(mode="json") == before


def test_action_name_and_perform_synonym_use_exact_registered_object_and_leave_app_explicit():
    _, observation = contract_world()
    before = copy.deepcopy(observation)
    request = StartActivityRequest.model_validate(start_payload("刷推荐", "做", app="抖音"))
    outcome = resolve_start(name_book(observation), request)
    assert not outcome.failures and outcome.request.items[0].way == "进行"
    assert outcome.request.items[0].target == "刷推荐" and outcome.request.items[0].app == "抖音"
    assert outcome.rows[0]["object_id"] == "browse-feed" and outcome.rows[0]["selected_action"]["canonical_action"] == "do"
    app_target = StartActivityRequest.model_validate(start_payload("抖音", "进行"))
    assert resolve_start(name_book(observation), app_target).failures[0].item.error_code == "NAME_NOT_FOUND"
    assert observation == before


import shutil
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.life.conftest import context, stage_day_plan, drive, submit_round, activity_name, advance_until_ready
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import SameMomentStage
from worldmock.life.guidance import refusal_for_code


def _host(engine: LifeEngine, decision, observed: dict) -> LifeAgent:
    """宿主：绑定真实 LifeEnv 与本轮宿主身份的 LifeAgent，按本轮观察冻结六层、菜单、私有绑定与工具面（与 run_react_loop
    开头同一准备）。"""
    person = LifeAgent()
    name = engine.context.state.agents[decision.agent_id].name
    person._id, person._name, person._profile = decision.agent_id, name, {"id": decision.agent_id, "name": name}
    environment = LifeEnv()
    environment.engine = engine
    person._env, person._decision_context, person._max_react_turns = environment, decision, 3
    person.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    return person

def _candidates(observed: dict) -> list[tuple[str, str, dict]]:
    """工具层每个目标的每个动作与工具、待办层每项暂停活动的工具：（工具名，目标名，可用性行）。"""
    rows = []
    for target in observed["tool_layer"]["targets"]:
        rows.extend(("start_activity", target["name"], item) for item in target["actions"])
        rows.extend((item["tool"], target["name"], item) for item in target["tools"])
    for item in observed["todo_layer"]["paused_activities"]:
        rows.extend((tool["tool"], item["name"], tool) for tool in item["tools"])
    return rows


def _stage(engine,change):
    """@brief 世界事实通过真实存储连续提交，不替环境派发动作。"""
    staged=engine.context.state.model_copy(deep=True)
    change(staged)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"),[EventDraft(event_type="environment_event",
        occurred_at=staged.current_time,visibility="system",data={"kind":"tool_layer_test_setup"})])
    engine.context.state=staged


def _arguments(engine,observed,candidate):
    """@brief 每次真实提交明确填写五字段；人物与支付来自真实已登记选择。"""
    tool,name,item=candidate
    if tool=="start_activity":
        app=item["app"]
        pay=recipient=None
        catalog=engine.context.semantics.catalog
        if item["way"]=="购买" and app is not None:
            selected=next(value for value in catalog.apps.values() if value.name==app)
            pay=catalog.apps[selected.payment_app_ids[0]].name
        binding=next(row for row in observed["interaction_bindings"] if row["object"]==name)
        if binding["kind"]=="action" and catalog.actions[binding["object_id"]].category=="social":
            kinds={"person"} if app is None else {"contact"}
            recipient=next(row["object"] for row in observed["interaction_bindings"] if row["kind"] in kinds)
        return {"items":[{"way":item["way"],"target":name,"app":app,"to":recipient,"pay_app":pay}],"answering":None}
    if tool in {"report_status","pause_activity"}:
        return {"items":[{"activity_name":name,"summary":"继续。"}],"errands":[]}
    if tool=="conclude_activity":
        return {"items":[{"activity_name":name,"summary":"收尾。","memories":[],"views":[]}],"errands":[]}
    return {"recipient_name":name,"text":"你好"}


def _assert_admission_matches(engine,tmp_path,label,tools=None):
    """@brief 每个菜单项从同一真实已提交世界独立派发，宿主接受且环境原因与菜单一致。"""
    observed=engine.observe(context(engine,f"{label}-preview"))
    listing=_candidates(observed)
    checked=[]
    selected=[candidate for candidate in listing if tools is None or candidate[0] in tools]
    for index,candidate in enumerate(selected):
        directory=tmp_path/f"{label}-branch-{index}"
        shutil.copytree(engine.context.store.run_dir,directory)
        branch=LifeEngine(engine.context.semantics,engine.context.scenario_sha256,FootprintStore(directory))
        try:
            decision=context(branch,f"{label}-{index}")
            contexts=[context(branch,decision.round_id,identity)
                      for identity in branch.scheduling_state().ready_agent_ids]
            branch.begin_decision_round(contexts)
            frozen=None
            for other in contexts:
                value=branch.observe(other)
                if other.agent_id == 1:
                    frozen=value
            assert _candidates(frozen)==listing
            host=_host(branch,decision,frozen)
            arguments=_arguments(branch,frozen,candidate)
            tool,name,item=candidate
            assert host.validate_react_decisions([ReactDecision("",tool,arguments,"")],False)=="",candidate
            drive(host.dispatch_react_tool(tool,arguments))
            for other in contexts:
                if other.agent_id != 1:
                    branch.submit_action(DirectToolCall(other,f"peer-{index}","start_activity",
                        StartActivityRequest.model_validate(start_payload("自己","等待"))))
            result=branch.commit_decision_round(decision.round_id)[0]
            if item["available"]:
                assert result.status!="rejected",(candidate,result.error_code,result.data)
            else:
                assert result.status=="rejected" and result.data["reason"]==item["reason"],(candidate,result.data)
            checked.append(item)
        finally:
            branch.close()
            shutil.rmtree(directory)
    return checked


def _engine(engine_factory,car=False):
    """@brief 两位真实人物共同在家，给离线社交明确合法对方；远程社交使用真实通讯录。"""
    def transport(document):
        document["agents"][0].update(owns_car=car,initial_driving_days=7 if car else 0)
    engine=engine_factory("multi-person.json",mutate=transport)
    for identity in engine.context.state.agents:
        stage_day_plan(engine,identity)
    def together(state):
        state.agents[2].location_id=state.agents[1].location_id
        state.agents[2].next_decision_at=state.end_time
    _stage(engine,together)
    return engine


def test_every_listed_item_matches_admission_at_run_start(engine_factory,tmp_path):
    engine=_engine(engine_factory)
    rows=_assert_admission_matches(engine,tmp_path,"start")
    assert any(row["available"] for row in rows) and any(not row["available"] for row in rows)


def test_pending_conclusion_marks_everything_but_conclusion_unavailable(engine_factory,tmp_path):
    engine=_engine(engine_factory)
    _,started=submit_round(engine,{1:("start_activity",StartActivityRequest.model_validate(
        start_payload("自己","等待")))},"wait")
    assert started[0].error_code is None and advance_until_ready(engine)
    rows=_assert_admission_matches(engine,tmp_path,"pending")
    assert [row["tool"] for row in rows if row["available"]]==["conclude_activity"]


def test_restricted_round_marks_closed_items_with_the_same_moment_reason(engine_factory,tmp_path):
    engine=_engine(engine_factory)
    def restricted(state):
        person=state.agents[1]
        person.same_moment_rounds=engine.context.scenario.max_same_moment_decisions
        person.same_moment_stage=SameMomentStage.START_OR_RESUME
    _stage(engine,restricted)
    rows=_assert_admission_matches(engine,tmp_path,"restricted")
    reason=refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason
    assert reason in {row["reason"] for row in rows}


def test_app_bound_store_pickup_is_available_and_matches_real_acceptance(engine_factory):
    engine=engine_factory()
    stage_day_plan(engine)
    _stage(engine,lambda state:setattr(state.agents[1],"location_id","noodle-1"))
    observed=engine.observe(context(engine,"pickup-preview"))
    row=action_row(observed["tool_layer"],"牛肉面")
    assert row["available"] and row["reason"] is None
    _,results=submit_round(engine,{1:("start_activity",StartActivityRequest.model_validate(
        start_payload("牛肉面","购买")))},"pickup")
    assert results[0].error_code is None
    order=results[0].data["orders"][0]
    assert order["fulfillment"]=="pickup" and order["app_id"] is None and order["status"]=="delivered"
    assert not hasattr(engine.context.state.agents[1],"inventory")
    receipts=[event for event in engine.context.store.read_agent_events("environment_event",1)
              if event.data.get("kind")=="goods_received"]
    assert len(receipts)==1 and receipts[0].data["order_id"]==order["order_id"]


def _tool_round(engine,label,tool,request):
    """@brief 所有就绪人物按真实顺序提交，只有人物一使用当前待验证请求。"""
    peer=StartActivityRequest.model_validate(start_payload("自己","等待"))
    requests={identity:("start_activity",peer) for identity in engine.scheduling_state().ready_agent_ids}
    requests[1]=(tool,request)
    return submit_round(engine,requests,label)[1][0]


@pytest.mark.parametrize("way",["步行","驾车"])
def test_road_activity_cannot_be_paused_and_matches_admission(engine_factory,tmp_path,way):
    from tests.life.conftest import wake_by_mail,advance_next
    engine=_engine(engine_factory,car=way=="驾车")
    result=_tool_round(engine,"depart","start_activity",StartActivityRequest.model_validate(
        start_payload("公司",way)))
    assert result.error_code is None
    wake_by_mail(engine,offset=60)
    advance_next(engine)
    observed=engine.observe(context(engine,"road-preview"))
    name=activity_name(engine,result.activity_id)
    target=next(row for row in observed["tool_layer"]["targets"] if row["name"]==name)
    pause=next(row for row in target["tools"] if row["tool"]=="pause_activity")
    assert not pause["available"] and "cannot be paused while travelling" in pause["reason"]
    rows=_assert_admission_matches(engine,tmp_path,"road",{"report_status","pause_activity","conclude_activity"})
    assert pause in rows


def test_driving_marks_messages_unavailable_with_the_admission_reason(engine_factory,tmp_path):
    from tests.life.conftest import wake_by_mail,advance_next
    engine=_engine(engine_factory,car=True)
    result=_tool_round(engine,"drive","start_activity",StartActivityRequest.model_validate(start_payload("公司","驾车")))
    assert result.error_code is None
    wake_by_mail(engine,offset=60)
    advance_next(engine)
    rows=_assert_admission_matches(engine,tmp_path,"drive-message",{"send_message"})
    assert rows and all(not row["available"] and "driving" in row["reason"] for row in rows)


def test_paused_activity_controls_match_real_resume_and_conclusion(engine_factory,tmp_path):
    from tests.life.conftest import wake_by_mail,advance_next,pause_request
    engine=_engine(engine_factory)
    result=_tool_round(engine,"start-content","start_activity",StartActivityRequest.model_validate(
        start_payload("刷推荐","进行",app="哔哩哔哩")))
    assert result.error_code is None
    wake_by_mail(engine,offset=60)
    advance_next(engine)
    name=activity_name(engine,result.activity_id)
    paused=_tool_round(engine,"pause-content","pause_activity",pause_request(name,summary="先停一下。"))
    assert paused.error_code is None and engine.context.state.activities[result.activity_id].status=="paused"
    rows=_assert_admission_matches(engine,tmp_path,"paused",{"report_status","conclude_activity"})
    assert {row["tool"] for row in rows if row["available"]}=={"report_status","conclude_activity"}
