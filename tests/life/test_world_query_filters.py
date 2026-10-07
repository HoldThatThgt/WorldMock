"""@brief 真实目录商品与动作共同按应用、类别、方式和当前资格取交集。"""

import copy
from dataclasses import replace

from tests.life.action_contract_world import contract_world
from tests.life.test_world_query import query_observation, query_payload
from worldmock.life.discovery import DiscoveryContext, product_bindings, product_rows
from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query


def names(observation, **values):
    """@brief 使用公开查询入口得到完整匹配目标名。"""
    request = QueryWorldRequest.model_validate(query_payload(**values))
    assert validate_world_query(observation, request) is None
    result = query_world(observation, request)
    return {row["name"] for row in result["matches"]}


def test_shopping_app_filters_the_real_goods_target_without_store_expansion():
    _, observation = query_observation()
    assert names(observation, app="美团", way="购买", category="正餐") == {"牛肉面"}
    assert names(observation, app="饿了么", way="购买", category=None) == {"薯片", "柠檬茶"}
    assert all("出售的" not in entry["match"]["name"] for entry in observation["world_query_bindings"])
    assert observation["world_query_apps"]["meituan"] == {"name": "美团"}
    assert "world_query_channels" not in observation


def test_action_app_and_category_filters_use_real_action_names_and_labels():
    _, observation = query_observation()
    assert names(observation, app="哔哩哔哩", category=None) == {"刷推荐", "学课程"}
    assert names(observation, app="哔哩哔哩", category="娱乐") == {"刷推荐"}
    assert names(observation, app="Keep", category="运动", way="进行") == {"跑步"}
    assert names(observation, app="微信", category="社交") == {"语音通话"}
    assert names(observation, category="日常照护") == {"安静休息"}
    assert names(observation, way="进行", category="工作与学习") == {"学课程", "办公"}


def test_available_only_is_specific_to_the_selected_app_and_way():
    _, observation = query_observation()
    entry = next(entry for entry in observation["world_query_bindings"] if entry["match"]["name"] == "刷推荐")
    for action in entry["match"]["actions"]:
        action["available"] = action["app"] == "抖音"
        action["reason"] = None if action["available"] else "Nothing is left to do for this action."
    assert names(observation, app="哔哩哔哩", category="娱乐", available_only=True) == set()
    assert names(observation, app="抖音", category="娱乐", available_only=True) == {"刷推荐"}
    assert names(observation, app="哔哩哔哩", category="娱乐", available_only=False) == {"刷推荐"}
    tool_only = next(row["match"] for row in observation["world_query_bindings"]
                     if not row["match"]["actions"] and any(tool["available"] for tool in row["match"]["tools"]))
    assert names(observation, name=tool_only["name"], way=None, category=None, app=None,
                 available_only=True) == {tool_only["name"]}


def test_full_query_contains_goods_beyond_the_current_presentation_window():
    context, observation = query_observation()
    full = names(observation, category=None, way="购买")
    observation["tool_layer"]["targets"] = [target for target in observation["tool_layer"]["targets"]
                                            if target["kind"] != "product"]
    assert full == {"牛肉面", "薯片", "柠檬茶"}
    assert names(observation, category=None, way="购买") == full
    assert len(product_bindings(context)) == len(full)


def test_product_set_is_the_single_catalog_union_and_observation_does_not_change_it():
    context, _ = contract_world("store")
    discovery = DiscoveryContext(context.state, context.scenario, context.semantics, context.state.agents[1])
    before = context.state.model_dump(mode="json")
    rows = product_rows(discovery)
    assert {row["goods_id"] for row in rows} == set(context.semantics.catalog.goods)
    assert len(rows) == len({row["goods_id"] for row in rows})
    chips = next(row for row in rows if row["goods_id"] == "chips")
    assert chips["app_ids"] == ["eleme", "meituan"]
    assert context.state.model_dump(mode="json") == before
    bindings = product_bindings(context)
    assert {row["object_id"] for row in bindings} == {row["goods_id"] for row in rows}
    for row in bindings:
        assert {action["app_id"] for action in row["actions"]} == {None, *row["app_ids"]}
        assert all(action["label"] == "购买" for action in row["actions"])


def test_menu_can_remove_all_goods_without_removing_unrelated_actions():
    context, _ = contract_world()
    menu = context.menu.model_copy(update={"allowed_actions": tuple(action for action in context.menu.allowed_actions
                                                                   if action != "purchase")})
    assert product_bindings(replace(context, menu=menu)) == []


def test_query_remains_frozen_after_the_source_world_and_catalog_order_change():
    context, observation = query_observation()
    before = copy.deepcopy(observation)
    expected = names(observation, app="美团", category=None)
    context.state.agents[1].location_id = "office"
    context.state.sessions["stay-1"].location_id = "office"
    assert names(observation, app="美团", category=None) == expected
    assert observation == before
