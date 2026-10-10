"""冻结名称、只读查询、紧凑渲染与真实服务容量的产品行为。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.life.action_contract import ACTIVITY_ACTIONS
from worldmock.life.name_resolution import NameBook, Target, resolve_start
from worldmock.life.models import StartActivityRequest
from worldmock.life.narration import ForbiddenObservationFieldError, render_layer_json
from worldmock.life.prompt_capacity import fit_request
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query


def target(name, kind, actions, *, listed=True):
    row = {"object": name, "kind": kind, "object_id": name,
           "actions": [{"action": action, "note": None} for action in actions]}
    return Target(name, kind, name, tuple(actions), tuple(None for _ in actions),
                  tuple(None for _ in actions), listed, row)


def request(action, name):
    return SimpleNamespace(items=[SimpleNamespace(action=action, target=name)])


@pytest.mark.parametrize("action", ACTIVITY_ACTIONS)
def test_each_concrete_action_resolves_only_to_its_declared_target(action):
    kind = "supply" if action == "purchase" else "app" if action == "interact" else "location"
    book = NameBook((target("完整名称", kind, [action]),))
    outcome = resolve_start(book, request(action, "完整名称"))
    assert not outcome.failures
    assert outcome.rows[0]["selected_action"] == {"action": action, "note": None}
    assert outcome.request.items[0].target == "完整名称"


def test_action_normalization_preserves_evidence_and_unknown_action_uses_name_feedback():
    book = NameBook((target("家", "location", ["sleep"]),), "家", "家")
    normalized = resolve_start(book, request(" ｓｌｅｅｐ ", "住处"))
    assert normalized.request.items[0].action == "sleep"
    assert normalized.resolved == ({"written": " ｓｌｅｅｐ ", "as": "sleep"},
                                   {"written": "住处", "as": "家"})
    bad = resolve_start(book, request("wait", "家")).failures[0].item
    assert bad.error_code == "ACTION_NOT_OFFERED"
    assert bad.path == ("items", 0, "action")
    assert bad.allowed_values == ACTIVITY_ACTIONS
    assert not bad.candidates


def test_request_schema_preserves_written_action_for_shared_resolution():
    payload = StartActivityRequest.model_validate({"items": [{"action": " ｓｌｅｅｐ ", "target": "家"}]})
    assert payload.items[0].action == " ｓｌｅｅｐ "
    unknown = StartActivityRequest.model_validate({"items": [{"action": "wait", "target": "家"}]})
    assert resolve_start(NameBook(()), unknown).failures[0].item.error_code == "ACTION_NOT_OFFERED"
    schema = StartActivityRequest.model_json_schema()
    assert schema["properties"]["items"]["items"]["properties"]["action"]["type"] == "string"


def test_renderer_allows_four_felt_labels_and_rejects_numeric_body_state():
    assert "想放松" in render_layer_json({"relaxation": "想放松", "social": "不想社交"})
    for key in ("relaxation", "social", "relaxation_need", "social_need"):
        with pytest.raises(ForbiddenObservationFieldError):
            render_layer_json({key: 0.4})


def test_exact_known_object_wrong_action_returns_complete_new_request():
    book = NameBook((target("视频平台", "app", ["interact"]),))
    error = resolve_start(book, request("sleep", "视频平台")).failures[0].item
    assert error.error_code == "ACTION_NOT_OFFERED"
    assert error.candidates[0].payload() == {
        "tool": "start_activity", "arguments": {"items": [{"action": "interact", "target": "视频平台"}]},
        "note": None}


def test_branch_base_name_does_not_select_an_instance_or_an_unlisted_near_candidate():
    book = NameBook((target("店铺（东店）", "location", ["walk"]),
                     target("店铺（西店）", "location", ["walk"], listed=False)))
    error = resolve_start(book, request("walk", "店铺")).failures[0].item
    assert error.error_code == "NAME_NOT_FOUND"
    assert [candidate.arguments["items"][0]["target"] for candidate in error.candidates] == ["店铺（东店）"]


def action_row(action="purchase", available=True):
    return {"action": action, "available": available, "reason": None if available else "Closed.",
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
