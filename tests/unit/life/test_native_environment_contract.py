"""冻结名称、只读查询、紧凑渲染与真实服务容量的产品行为。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tests.life.request_fixtures import main_request, side_request
from worldmock.life.action_contract import ACTIVITY_ACTIONS, START_TOOLS, binding_action
from worldmock.life.name_resolution import NameBook, Option, Target, resolve_start, sleep_row
from worldmock.life.models import StartMainActivityRequest, StartSideActivityRequest
from worldmock.life.narration import ForbiddenObservationFieldError, render_layer_json
from worldmock.life.prompt_capacity import fit_request
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query

MAIN_TOOL, SIDE_TOOL = START_TOOLS


def target(name, kind, actions, *, listed=True):
    """手写的一个对象：actions 的每项是（动作，detail），与私有绑定的动作行同形。"""
    rows = [binding_action(action, detail) for action, detail in actions]
    options = tuple(Option(row["action"], row["detail"], None, None, (), row) for row in rows)
    return Target(name, kind, name, options, listed,
                  {"object": name, "kind": kind, "object_id": name, "actions": rows})


@pytest.mark.parametrize("tool,kind,written,selected", [
    (MAIN_TOOL, "location", main_request("travel", "完整名称", "步行"), binding_action("travel", "步行")),
    (MAIN_TOOL, "location", main_request("interact", "完整名称", "一件事"), binding_action("interact", "一件事")),
    (SIDE_TOOL, "supply", side_request("purchase", "完整名称"), binding_action("purchase")),
    (SIDE_TOOL, "app", side_request("interact", "完整名称"), binding_action("interact")),
])
def test_each_concrete_action_resolves_only_to_its_declared_target(tool, kind, written, selected):
    """出行与在地点做事只落到地点行，购买只落到商品行，用内容 app 只落到 app 行；所写名称完整时不记归一。"""
    option = (selected["action"], selected["detail"])
    book = NameBook((target("完整名称", kind, [option]),))
    outcome = resolve_start(book, tool, written)
    assert not outcome.failures and outcome.resolved == ()
    assert [(row["object"], row["kind"], row["selected_action"]) for row in outcome.rows] == [
        ("完整名称", kind, selected)]
    other = (target("完整名称", "app", [("interact", None)]) if kind == "location"
             else target("完整名称", "location", [("interact", "一件事")]))
    refused = resolve_start(NameBook((other,)), tool, written)
    assert refused.rows == () and [failure.item.error_code for failure in refused.failures] == ["ACTION_NOT_OFFERED"]


def test_sleep_resolves_to_its_minutes_without_any_named_object():
    """四个动作里只有睡眠不查名称：没有任何对象时分钟数合格就解析成功，只带分钟数。"""
    assert ACTIVITY_ACTIONS == ("travel", "purchase", "sleep", "interact")
    outcome = resolve_start(NameBook(()), MAIN_TOOL, main_request("sleep", "450"))
    assert outcome.failures == () and outcome.rows == (sleep_row(450),)


def test_name_normalization_preserves_evidence_and_the_action_is_never_normalized():
    """对象名与 detail 的无损归一留下所写与解析结果；动作不作归一，闭集之外的写法到不了名称解析。"""
    book = NameBook((target("家", "location", [("interact", "休息")]),), "家", "家")
    normalized = resolve_start(book, MAIN_TOOL, main_request("interact", "住处", "休 息"))
    assert not normalized.failures
    assert normalized.rows[0]["selected_action"] == binding_action("interact", "休息")
    assert normalized.resolved == ({"written": "住处", "as": "家"}, {"written": "休 息", "as": "休息"})
    asleep = resolve_start(book, MAIN_TOOL, main_request("sleep", "４５０"))
    assert asleep.resolved == ({"written": "４５０", "as": "450"},)
    for written in (" ｓｌｅｅｐ ", "Sleep", "wait"):
        with pytest.raises(ValidationError):
            main_request(written, "450")


def test_request_schema_closes_the_action_set_of_each_start_tool():
    """动作是各工具自己的闭集枚举，不再是留给名称解析的自由字符串。"""
    main = StartMainActivityRequest.model_json_schema()["properties"]["action"]
    side = StartSideActivityRequest.model_json_schema()["properties"]["action"]
    assert (main["enum"], main["type"]) == (["travel", "interact", "sleep"], "string")
    assert (side["enum"], side["type"]) == (["purchase", "interact"], "string")
    with pytest.raises(ValidationError):
        StartMainActivityRequest.model_validate({"action": "purchase", "target": "家", "detail": None})
    with pytest.raises(ValidationError):
        StartSideActivityRequest.model_validate({"action": "travel", "target": ["家"], "prefer": None})


def test_renderer_allows_four_felt_labels_and_rejects_numeric_body_state():
    assert "想放松" in render_layer_json({"relaxation": "想放松", "social": "不想社交"})
    for key in ("relaxation", "social", "relaxation_need", "social_need"):
        with pytest.raises(ForbiddenObservationFieldError):
            render_layer_json({key: 0.4})


def test_exact_known_object_wrong_action_returns_complete_new_request():
    """已知对象不提供所写动作：给出它在这个工具下提供的动作，候选是可以照抄的完整调用。"""
    book = NameBook((target("视频平台", "app", [("interact", None)]),))
    error = resolve_start(book, SIDE_TOOL, side_request("purchase", "饼干", "视频平台")).failures[0].item
    assert error.error_code == "ACTION_NOT_OFFERED"
    assert (error.path, error.written, error.allowed_values) == (("action",), "purchase", ("interact",))
    assert [candidate.payload() for candidate in error.candidates] == [
        {"tool": "start_side_activity", "arguments": {"action": "interact", "target": ["视频平台"], "prefer": None},
         "note": None}]


def test_branch_base_name_does_not_select_an_instance_or_an_unlisted_near_candidate():
    book = NameBook((target("店铺（东店）", "location", [("travel", "步行")]),
                     target("店铺（西店）", "location", [("travel", "步行")], listed=False)))
    outcome = resolve_start(book, MAIN_TOOL, main_request("travel", "店铺", "步行"))
    error = outcome.failures[0].item
    assert outcome.rows == () and error.error_code == "NAME_NOT_FOUND" and error.path == ("target",)
    assert [candidate.payload() for candidate in error.candidates] == [
        {"tool": "start_main_activity", "note": None,
         "arguments": {"action": "travel", "target": "店铺（东店）", "detail": "步行"}}]


def action_row(action="purchase", available=True):
    """工具层的一条动作行：八个键，detail 只有出行与在地点做事才写。"""
    return {"action": action, "detail": None, "available": available, "reason": None if available else "Closed.",
            "price": None, "duration": "1–5 分钟", "quantity": "1 件", "note": None}


def query_observation():
    match = {"name": "饼干", "kind": "product", "phase": None, "state": None,
             "actions": [action_row()], "tools": []}
    app = {"name": "视频平台", "kind": "app", "phase": None, "state": None,
           "actions": [action_row("interact")], "tools": []}
    return {"world_query_bindings": [
        {"identity": "goods", "aliases": ["饼干"], "category": "零食", "match": match},
        {"identity": "app", "aliases": ["视频平台"], "category": None, "match": app}],
        "world_query_page_size": 1}


def test_query_category_action_and_name_are_intersected_without_content_category_inference():
    observed = query_observation()
    filtered = QueryWorldRequest(name="视频平台", action="interact", category="零食",
                                 available_only=True, offset=0)
    assert validate_world_query(observed, filtered) is None
    assert query_world(observed, filtered) == {"matches": [], "total_count": 0, "next_offset": None}
    old_category = filtered.model_copy(update={"category": "娱乐"})
    assert validate_world_query(observed, old_category).items[0].path == ("category",)


def test_query_is_readonly_and_rejects_retired_filter_keys():
    observed = query_observation()
    before = deepcopy(observed)
    values = {"name": None, "action": "purchase", "category": "零食", "available_only": False, "offset": 0}
    response = query_world(observed, QueryWorldRequest(**values))
    response["matches"][0]["name"] = "changed"
    assert observed == before
    for old in ("app", "way"):
        with pytest.raises(ValidationError):
            QueryWorldRequest(**values, **{old: None})


def test_compact_groups_conserve_false_zero_and_all_new_action_fields():
    target_row = query_observation()["world_query_bindings"][0]["match"]
    target_row["actions"][0].update(available=False, reason="Closed.", price="0 元")
    layer = {"layer": "工具层", "targets": [target_row], "callable_index": {"goods": [], "query_categories": []}}
    grouped = compact_tool_layer(layer)
    assert "way" not in grouped["groups"][0]
    assert expand_tool_layer(grouped) == layer


def metadata(window=262144, output=8000):
    return {"requested_model": "contract-model", "metadata_url": "https://service.invalid/v1/models",
            "context_window": window, "max_output_tokens": output}


def test_capacity_uses_verified_actual_context_and_enforces_verified_output_limit(monkeypatch):
    monkeypatch.setattr("worldmock.life.prompt_capacity.prompt_token_count", lambda request: 220000)
    payload = {"model": "contract-model", "messages": [], "max_tokens": 8000}
    assert fit_request(payload, metadata(window=500000)) is payload
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        fit_request(payload, metadata())
    with pytest.raises(RuntimeError, match="output reserve exceeds"):
        fit_request(payload, metadata(output=4000))
