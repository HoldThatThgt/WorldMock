"""@brief 新六键查询的严格类型、应用合同、冻结分页与只读证据。"""

import copy
import json

import pytest
from pydantic import ValidationError

from tests.life.action_contract_world import contract_world
from worldmock.life.world_query import QueryWorldRequest, attach_world_query, query_argument_report, query_world, validate_world_query


def query_payload(**values):
    """@brief 查询恰有六个必填字段，默认按真实商品类别筛选。"""
    return {"name": None, "way": None, "category": "正餐", "app": None,
            "available_only": False, "offset": 0, **values}


def query_observation(location="home"):
    """@brief 冻结实际完整目录商品和动作，所有查询都读真实工具公开目标。"""
    context, observation = contract_world(location)
    before = context.state.model_dump(mode="json")
    attach_world_query(observation, context)
    assert context.state.model_dump(mode="json") == before
    return context, observation


@pytest.mark.parametrize("offset", [True, False, "0", 0.0, 1.5, -1, None])
def test_offset_is_a_strict_nonnegative_json_integer(offset):
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(query_payload(offset=offset))
    assert any(error["loc"] == ("offset",) for error in caught.value.errors())


@pytest.mark.parametrize("field", ["name", "way", "category", "app", "available_only", "offset"])
def test_every_query_field_is_required(field):
    payload = query_payload()
    payload.pop(field)
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(payload)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [((field,), "missing")]


@pytest.mark.parametrize("field", ["from", "from_", "source", "prefer", "store"])
def test_retired_source_fields_have_no_alias_or_compatibility(field):
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(query_payload(**{field: "美团"}))
    assert any(error["loc"] == (field,) and error["type"] == "extra_forbidden" for error in caught.value.errors())
    assert set(QueryWorldRequest.model_fields) == {"name", "way", "category", "app", "available_only", "offset"}


def test_empty_filter_reports_each_position_and_keeps_other_type_errors():
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(query_payload(category=None, available_only="yes", offset=-1))
    assert {error["loc"] for error in caught.value.errors()} == {
        ("name",), ("way",), ("category",), ("app",), ("available_only",), ("offset",)}


@pytest.mark.parametrize("field", ["name", "way", "category"])
def test_nonnull_object_filters_cannot_be_blank(field):
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(query_payload(**{field: "  "}))
    assert (field,) in {error["loc"] for error in caught.value.errors()}


@pytest.mark.parametrize("app", ["未知应用", "一心面馆", "meituan", " 美团 ", ""])
def test_unknown_app_and_store_names_have_the_exact_new_error_contract(app):
    _, observation = query_observation()
    payload = query_payload(app=app)
    request = QueryWorldRequest.model_validate(payload)
    report = validate_world_query(observation, request)
    issue = next(item for item in report.items if item.path == ("app",))
    assert issue.expected == "null or a known app name is required."
    assert issue.why == "The written app is not a known app name."
    assert 0 < len(issue.candidates) <= 3
    names = {row["name"] for row in observation["world_query_apps"].values()}
    for candidate in issue.candidates:
        assert candidate.arguments["app"] in names
        assert candidate.arguments == {**payload, "app": candidate.arguments["app"]}
        QueryWorldRequest.model_validate(candidate.arguments)


def test_app_written_as_object_name_has_one_explicit_move_candidate():
    _, observation = query_observation()
    payload = query_payload(name="美团", category=None)
    report = validate_world_query(observation, QueryWorldRequest.model_validate(payload))
    issue = report.items[0]
    assert issue.path == ("name",) and issue.expected == "An app name belongs in app; name takes an object name."
    assert issue.candidates[0].arguments == {**payload, "name": None, "app": "美团"}
    assert payload["name"] == "美团" and payload["app"] is None


def test_filter_errors_are_aggregated_and_never_hidden_by_offset_or_type_errors():
    _, observation = query_observation()
    payload = query_payload(way="打开", category="食品", app="未知应用", offset=999)
    report = validate_world_query(observation, QueryWorldRequest.model_validate(payload))
    assert [item.path for item in report.items] == [("way",), ("category",), ("app",)]
    broken = {**payload, "offset": "zero"}
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest.model_validate(broken)
    report = query_argument_report(observation, caught.value, broken)
    assert {item.path for item in report.items} == {("offset",), ("way",), ("category",), ("app",)}
    assert all(candidate.arguments["offset"] == "zero" for item in report.items for candidate in item.candidates)


def test_offset_is_checked_against_filtered_count_including_the_empty_last_page():
    _, observation = query_observation()
    request = QueryWorldRequest.model_validate(query_payload(app="美团"))
    total = query_world(observation, request)["total_count"]
    assert validate_world_query(observation, QueryWorldRequest.model_validate(query_payload(app="美团", offset=total))) is None
    assert query_world(observation, QueryWorldRequest.model_validate(query_payload(app="美团", offset=total))) == {
        "matches": [], "total_count": total, "next_offset": None}
    report = validate_world_query(observation, QueryWorldRequest.model_validate(query_payload(app="美团", offset=total + 1)))
    assert report.items[0].path == ("offset",)
    assert report.items[0].expected == f"An integer in the inclusive range 0..{total}."


def test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets():
    context, observation = query_observation()
    observation["world_query_page_size"] = 2
    payload = query_payload(way="进行", category=None)
    before = copy.deepcopy(observation)
    state_before = context.state.model_dump(mode="json")
    offset, names = 0, []
    while True:
        result = query_world(observation, QueryWorldRequest.model_validate({**payload, "offset": offset}))
        assert result["total_count"] == 7
        names.extend(row["name"] for row in result["matches"])
        if result["matches"]:
            result["matches"][0]["name"] = "外部改动"
        if result["next_offset"] is None:
            break
        offset = result["next_offset"]
    assert names == sorted(names) and len(names) == len(set(names)) == 7
    assert observation == before and context.state.model_dump(mode="json") == state_before
    assert json.loads(json.dumps(query_world(observation, QueryWorldRequest.model_validate(payload)), ensure_ascii=False))


def test_known_navigation_and_payment_only_apps_are_valid_filters_with_no_false_goods():
    _, observation = query_observation()
    for app in ("高德地图", "支付宝"):
        request = QueryWorldRequest.model_validate(query_payload(category=None, app=app))
        assert validate_world_query(observation, request) is None
        assert query_world(observation, request)["total_count"] == 0
