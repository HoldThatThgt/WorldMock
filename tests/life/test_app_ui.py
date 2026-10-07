"""@brief 应用目标、工具可用性、名称和公开 Schema 使用实际登记环境验证。"""

from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from tests.life.purchase_worlds import registered_world
from worldmock.life import activity_control, admission, sessions
from worldmock.life.callable_index import INDEX_KEYS, IndexSources, callable_index
from worldmock.life.engine_context import affordance_contract
from worldmock.life.guidance import refusal_for_code
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.models import AppPush
from worldmock.life.projection import ProjectionInputs, interaction_bindings
from worldmock.life.prompt_schemas import PROGRESS_LAYER_SCHEMA, TARGET_KINDS, TOOL_LAYER_SCHEMA, validate_layer
from worldmock.life.result_projection import public_result
from worldmock.life.content import choose_app_content
from worldmock.life.tool_layer import Candidate, LayerContext, refusal_for, tool_layer
from worldmock.life.vocabulary import WAYS, way_options


def app_context():
    """@brief 正常加载人工环境，使用真实规则、会话、投影和菜单形成应用工具上下文。"""
    semantics, state = registered_world()
    scenario = semantics.scenario
    names = {item.location_id: item.display_name for item in scenario.locations}
    state = sessions.open_stay(state, 1, names)
    rules = admission.WorldFacts(semantics.catalog.work_topics, semantics.catalog.foods)
    menu = resolve_interaction_menu(scenario, 1, "home")
    bindings = interaction_bindings(ProjectionInputs(state, scenario, state.agents[1],
                                                     extras={"interaction_menu": menu}))
    return LayerContext(state, scenario, rules, semantics, 1,
                        activity_control.visible_names(state, 1, {}), bindings, menu)


def test_all_apps_always_listed_without_content_candidates_or_index():
    context = app_context()
    index = callable_index(context, IndexSources())
    layer = tool_layer(context, index)
    apps = [row for row in layer["targets"] if row["kind"] == "app"]
    assert {row["name"] for row in apps} == {"应用：抖音", "应用：哔哩哔哩", "应用：饿了么", "应用：美团"}
    assert not {"content", "game", "catalog"} & {row["kind"] for row in layer["targets"]}
    assert set(index) == set(INDEX_KEYS) == {"lifestyle", "goods", "query_categories"}
    assert all(row["phase"] is None and row["tools"] == [] for row in apps)
    assert next(row for row in apps if row["name"] == "应用：抖音")["state"] == "4 条内容"
    validate_layer(layer)


def test_empty_app_tool_reason_matches_actual_start_refusal():
    from worldmock.errors import BusinessRuleError

    context = app_context()
    app = next(row for row in tool_layer(context, callable_index(context, IndexSources()))["targets"]
               if row["name"] == "应用：美团")
    action = app["actions"][0]
    assert action["way"] == "打开" and action["action"] == "interact"
    assert action["duration"] == "约 30 分钟"
    assert action["available"] is False and action["reason"] == "This app has no content to use."
    assert refusal_for_code("APP_CONTENT_EMPTY").next_step == "Choose another app."
    with pytest.raises(BusinessRuleError, match="APP_CONTENT_EMPTY"):
        choose_app_content(context.semantics.catalog, "meituan", context.state.random_state, "start:meituan")


@pytest.mark.parametrize("status", ("pending", "opened", "released"))
def test_current_push_title_and_kind_are_visible_until_released(status):
    context = app_context()
    session = sessions.current_session(context.state, 1)
    push = AppPush(push_id="push-real", content_id="c-short", issued_at=context.state.current_time, status=status)
    state = context.state.model_copy(update={"sessions": {
        session.session_id: session.model_copy(update={"app_pushes": {"douyin": push}})}})
    layer = tool_layer(replace(context, state=state), callable_index(context, IndexSources()))
    app = next(row for row in layer["targets"] if row["name"] == "应用：抖音")
    assert app["state"] == ("4 条内容" if status == "released" else "有推送：《测试短视频》（短视频）")


def test_game_and_watch_ways_are_retired_and_open_synonyms_resolve():
    assert set(WAYS) == {"步行", "驾车", "工作", "交谈", "通话", "阅读", "进行", "等待", "睡觉", "到店",
                         "远程下单", "购买", "打开", "食用", "使用", "给予"}
    for written in ("游玩", "玩", "玩游戏", "观看", "看", "收看", "听", "收听", "继续看", "重看", "重听"):
        assert way_options(written) == frozenset()
    for written in ("打开", "刷", "刷一刷", "用一下", "打开看看"):
        assert way_options(written) == {"打开"}


def test_app_target_and_app_content_schemas_are_closed():
    context = app_context()
    data = {"app": "抖音", "title": "测试短视频", "kind": "短视频", "summary": "字" * 200, "pushed": True}
    result = public_result({"occurred_at": context.state.current_time, "status": "in_progress", "message": "Started",
                            "data": {"app_content": {**data, "content_id": "hidden"}}}, context.scenario)
    assert result["details"]["app_content"] == data
    record = {"occurred_at": result["occurred_at"], "type": "app_push", **{k: data[k] for k in data if k != "pushed"}}
    schema = PROGRESS_LAYER_SCHEMA["properties"]["events"]["items"]
    Draft202012Validator(schema).validate(record)
    for changed in ({**record, "content_id": "hidden"}, {**record, "summary": "字" * 201},
                    {**record, "kind": "播客"}):
        assert list(Draft202012Validator(schema).iter_errors(changed))
    assert set(TARGET_KINDS) == {"location", "product", "holding", "person", "contact", "self", "task", "activity",
                                 "message", "app", "lifestyle"}
    result_schema = next(row for row in schema["oneOf"] if row["properties"]["type"]["const"] == "tool_call")
    app_schema = result_schema["properties"]["result"]["properties"]["details"]["properties"]["app_content"]
    assert app_schema["additionalProperties"] is False
    assert set(app_schema["required"]) == {"app", "title", "kind", "summary", "pushed"}
    for changed in ({**data, "repeat": True}, {**data, "kind": "播客"}, {**data, "summary": "字" * 201}):
        assert list(Draft202012Validator(app_schema).iter_errors(changed))


def test_app_duration_matches_tool_layer_and_admission():
    context = app_context()
    row = next(row for row in context.bindings if row["kind"] == "app" and row["object_id"] == "douyin")
    item = admission.selection_item(row, row["actions"][0], admission.WorldFacts({}, {}))
    assert item.claims == {"use_app"}
    assert refusal_for(context, Candidate("start_activity", row, row["actions"][0])) is None
    assert next(target for target in tool_layer(context, callable_index(context, IndexSources()))["targets"]
                if target["name"] == row["object"])["actions"][0]["duration"] == "约 30 分钟"


def test_opened_app_habit_is_entertainment_without_app_candidates():
    from worldmock.catalog.affordances import engine_affordances
    from worldmock.life.motivation import classify_action_event, habit_shares
    from worldmock.life.candidates import CANDIDATE_KINDS

    request = {"items": [{"target": "应用：抖音", "way": "打开"}]}
    assert classify_action_event("start_activity", request) == ("entertainment",)
    assert habit_shares((("start_activity", request),), 0.9)["entertainment"] == 1
    assert "app" not in CANDIDATE_KINDS and "content" not in CANDIDATE_KINDS
    assert "entertainment" not in {family.name for family in engine_affordances().families_by_rank()}


def test_app_name_and_open_synonym_use_the_actual_name_resolver():
    from worldmock.life.name_resolution import name_book, resolve_start
    from worldmock.life.models import StartActivityRequest

    context = app_context()
    layer = tool_layer(context, callable_index(context, IndexSources()))
    book = name_book({"interaction_bindings": context.bindings, "tool_layer": layer})
    request = StartActivityRequest.model_validate({"items": [
        {"target": "抖音", "way": "刷一刷", "from": None, "to": None}], "answering": None, "prefer": None})
    outcome = resolve_start(book, request)
    assert not outcome.failures
    assert outcome.request.items[0].target == "应用：抖音" and outcome.request.items[0].way == "打开"
    assert outcome.rows[0]["object_id"] == "douyin"
    assert outcome.rows[0]["selected_action"]["canonical_action"] == "use_app"
