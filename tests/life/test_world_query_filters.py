"""@brief 真实固定菜单的冻结查询以实际 app 成员店铺筛选。"""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from worldmock.life import activity_control
from worldmock.life.activities import ActivityOrigin, start_activity
from worldmock.life.models import ActivityStartParams, WorldState
from worldmock.life.callable_index import IndexSources, callable_index
from worldmock.life.tool_layer import tool_layer
from worldmock.life.world_query import QueryWorldRequest, attach_world_query, query_world, validate_world_query
from tests.life.purchase_worlds import menu_context


def observation(context):
    value = {"agent_id": 1, "catalog_bindings": [], "home_location_id": "home",
             "locations": [{"location_id": place.location_id, "name": place.display_name}
                           for place in context.scenario.locations],
             "tool_layer": tool_layer(context, callable_index(context, IndexSources()))}
    attach_world_query(value, context)
    return json.loads(json.dumps(value, ensure_ascii=False))


def test_frozen_food_query_app_source_uses_actual_store_binding_and_category():
    context = menu_context()
    value = observation(context)
    request = QueryWorldRequest.model_validate({"name": None, "way": "远程下单", "category": "正餐",
        "from": "美团", "available_only": True, "offset": 0})
    assert validate_world_query(value, request) is None
    result = query_world(value, request)
    assert {row["name"] for row in result["matches"]} == {"一心面馆出售的牛肉面", "三杯咖啡出售的三明治"}
    assert result["total_count"] == 2
    assert value["world_query_apps"]["meituan"]["store_ids"] == ["cafe", "noodle-1"]
    assert "world_query_channels" not in value
    assert all("taste_weights" not in row for row in result["matches"])


def test_query_is_a_frozen_pure_read_and_no_app_store_still_has_onsite_menu():
    context = menu_context("noodle-2")
    value = observation(context)
    before = context.state.model_dump_json()
    request = QueryWorldRequest.model_validate({"name": "牛肉面", "way": "到店", "category": None,
        "from": "二味小炒", "available_only": True, "offset": 0})
    assert validate_world_query(value, request) is None
    result = query_world(value, request)
    assert result["total_count"] == 1 and result["matches"][0]["state"] == "正餐，每份约 600 千卡"
    assert context.state.model_dump_json() == before


def request(**changes):
    return QueryWorldRequest.model_validate({"name": None, "way": None, "category": "正餐",
        "from": None, "available_only": False, "offset": 0, **changes})


def test_source_filter_excludes_real_menus_outside_the_selected_app():
    value = observation(menu_context())
    result = query_world(value, request(**{"from": "美团"}))
    assert result["total_count"] == 2
    assert {row["name"] for row in result["matches"]} == {"一心面馆出售的牛肉面", "三杯咖啡出售的三明治"}


def test_source_resolution_uses_the_purchase_resolver_instead_of_plain_place_equality():
    value = observation(menu_context())
    submitted = request(**{"from": "  美团  "})
    assert validate_world_query(value, submitted) is None
    assert query_world(value, submitted)["total_count"] == 2


def test_query_requires_the_source_and_availability_keys_without_defaults():
    with pytest.raises(ValidationError) as failure:
        QueryWorldRequest.model_validate({"name": "牛肉面", "way": None, "category": None, "offset": 0})
    assert {error["loc"] for error in failure.value.errors()} == {("from",), ("available_only",)}


def test_unknown_source_is_an_argument_error_instead_of_an_empty_page():
    value = observation(menu_context())
    report = validate_world_query(value, request(**{"from": "未登记的外卖平台"}))
    assert report is not None and report.error_code == "ARGUMENTS_INVALID"
    assert [item.path for item in report.items] == [("from",)]
    assert report.items[0].candidates


def test_source_error_candidates_exclude_places_without_actual_food_menus():
    value = observation(menu_context())
    report = validate_world_query(value, request(**{"from": "公司食堂"}))
    assert report is not None
    names = {candidate.arguments["from"] for candidate in report.items[0].candidates}
    assert names and names <= {"一心面馆", "二味小炒", "三杯咖啡", "四季便利", "美团", "饿了么"}
    assert "公司" not in names and "家" not in names and "五里公园" not in names


def test_purchase_way_expands_to_both_real_pickup_and_delivery_actions():
    value = observation(menu_context())
    submitted = request(way="购买")
    assert validate_world_query(value, submitted) is None
    result = query_world(value, submitted)
    assert result["total_count"] == 4
    assert {action["way"] for row in result["matches"] for action in row["actions"]} == {"到店", "远程下单"}


def test_availability_is_filtered_before_paging_and_total_count():
    value = observation(menu_context("noodle-2"))
    value["world_query_page_size"] = 2
    submitted = request(category=None, way="到店", available_only=True)
    seen, offset = [], 0
    while offset is not None:
        page_request = submitted.model_copy(update={"offset": offset})
        assert validate_world_query(value, page_request) is None
        page = query_world(value, page_request)
        assert page["total_count"] == 3
        seen.extend(row["name"] for row in page["matches"])
        offset = page["next_offset"]
    assert len(seen) == len(set(seen)) == 3
    assert all(name.startswith("二味小炒出售的") for name in seen)


def test_availability_only_uses_actions_matching_the_written_way():
    value = observation(menu_context())
    assert query_world(value, request(way="到店", available_only=True))["total_count"] == 0
    assert query_world(value, request(way="远程下单", available_only=True))["total_count"] == 2


def test_without_way_availability_includes_the_actual_activity_control_tools():
    context = menu_context()
    _, activity = start_activity(context.state.agents[1],
        ActivityStartParams(activity_type="consume", planned_duration_seconds=300), context.state.current_time,
        ActivityOrigin("food-control", "decision", "request"), context.semantics.body)
    state = WorldState.model_validate(context.state.model_copy(update={"activities": {activity.activity_id: activity}}).model_dump())
    names = activity_control.visible_names(state, 1, {})
    value = observation(replace(context, state=state, names=names))
    submitted = request(name=names["activities"][activity.activity_id], category=None, available_only=True)
    assert validate_world_query(value, submitted) is None
    result = query_world(value, submitted)
    assert result["total_count"] == 1
    target = result["matches"][0]
    assert not target.get("actions")
    assert any(tool["available"] for tool in target["tools"])


def test_total_count_is_the_filtered_menu_count():
    value = observation(menu_context())
    result = query_world(value, request(name="三明治", **{"from": "美团"}))
    assert result["total_count"] == 1
    assert len(value["world_query_bindings"]) > result["total_count"]
