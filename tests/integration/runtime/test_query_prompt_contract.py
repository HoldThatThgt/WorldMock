"""真实冻结观察的查询、无损分组与完整提示容量合同。"""

import copy
import json

import pytest
from pydantic import ValidationError

from tests.integration.runtime.current_contract_fixtures import inputs
from tests.life.conftest import advance_until_ready, context, stage_day_plan, start_activity
from tests.life.environment_runtime import act
from worldmock.life import prompt_capacity
from worldmock.life.activity_control import PHASE_PENDING
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.tool_groups import compact, compact_render, compact_tool_layer, expand_tool_layer
from worldmock.life.world_query import QueryWorldRequest, query_argument_report, query_world, validate_world_query


def query(**values):
    return QueryWorldRequest(name="家", action=None, category=None, available_only=False, offset=0, **values)


def test_real_query_returns_an_intersection_and_allows_the_result_end(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "read-query"))
    original = copy.deepcopy(observed)
    requested = query()
    assert validate_world_query(observed, requested) is None
    result = query_world(observed, requested)
    assert [item["name"] for item in result["matches"]] == ["家"]
    assert result["total_count"] == 1 and result["next_offset"] is None
    end = requested.model_copy(update={"offset": result["total_count"]})
    assert validate_world_query(observed, end) is None
    assert query_world(observed, end) == {"matches": [], "total_count": 1, "next_offset": None}
    empty = requested.model_copy(update={"action": "purchase"})
    assert validate_world_query(observed, empty) is None
    assert query_world(observed, empty) == {"matches": [], "total_count": 0, "next_offset": None}
    result["matches"][0]["name"] = "改写结果"
    assert observed == original


def test_query_schema_and_declared_filter_errors_are_reported_together():
    payload = {"name": "家", "action": "removed-action", "category": "removed-category",
               "available_only": False, "offset": "zero"}
    with pytest.raises(ValidationError) as caught:
        QueryWorldRequest(**payload)
    report = query_argument_report({}, caught.value, payload)
    assert report.error_code == "ARGUMENTS_INVALID"
    assert [item.path for item in report.items] == [("offset",), ("action",), ("category",)]
    assert all(item.error_code == "ARGUMENTS_INVALID" for item in report.items)


def test_actual_active_tools_roundtrip_losslessly_and_repeated_groups_do_not_duplicate_actions(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "active", start_activity("interact", "抖音"))
    observed = engine.observe(context(engine, "active-tools"))
    original = copy.deepcopy(observed["tool_layer"])
    grouped = compact_tool_layer(original)
    validate_layer(grouped)
    expanded = expand_tool_layer(grouped)
    assert expanded == original
    active = next(row for row in expanded["targets"] if row["kind"] == "activity")
    assert active["tools"] and active["name"] == engine.context.state.activities[started.activity_id].name
    repeated = {**grouped, "groups": [*grouped["groups"], copy.deepcopy(grouped["groups"][0])]}
    assert expand_tool_layer(repeated) == expanded
    nested = {"layers": (original,), "zero": 0, "enabled": False}
    assert compact_render(nested) == {"layers": [grouped], "zero": 0, "enabled": False}
    assert compact({"empty": [], "nothing": None, "zero": 0, "enabled": False}) == {"zero": 0, "enabled": False}
    assert observed["tool_layer"] == original


def test_real_full_slots_and_pending_conclusion_use_the_same_tool_refusal(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    from worldmock.life.models import StartActivityRequest
    request = StartActivityRequest(items=[{"action": "interact", "target": "抖音"},
                                         {"action": "walk", "target": "公司"}])
    _, accepted = act(engine, "both-slots", request)
    assert accepted.status == "in_progress"
    full = engine.observe(context(engine, "slots-full"))
    home = next(row for row in full["tool_layer"]["targets"] if row["name"] == "家")
    assert all(not row["available"] for row in home["actions"])
    assert all(row["reason"] for row in home["actions"])
    assert advance_until_ready(engine)
    pending = engine.observe(context(engine, "completion"))
    assert any(row["phase"] == PHASE_PENDING for row in pending["tool_layer"]["targets"])
    assert all(not row["available"] for target in pending["tool_layer"]["targets"]
               for row in target["actions"])


@pytest.mark.parametrize("reserve", [None, 0, -1, True, 32769])
def test_output_reserve_must_fit_the_actual_service_capacity(reserve):
    request = {"model": "test-model", "messages": [], "max_tokens": reserve}
    with pytest.raises(RuntimeError, match="output reserve exceeds verified service capacity"):
        prompt_capacity.fit_request(request, inputs().model_metadata)


def test_actual_usage_is_checked_against_the_sent_prompt_estimate(monkeypatch, caplog):
    measured = []

    def count(**arguments):
        measured.append(arguments)
        return 100

    monkeypatch.setattr(prompt_capacity, "token_counter", count)
    request = {"model": "test-model", "messages": [{"role": "user", "content": "本轮实际请求"}],
               "tools": [], "max_tokens": 40}
    assert prompt_capacity.request_token_count(request) == 140
    measured.clear()
    for response in (None, {}, {"usage": {}}, {"usage": {"prompt_tokens": "101"}}):
        prompt_capacity.compare_usage(request, response)
    assert measured == []
    with caplog.at_level("INFO"):
        prompt_capacity.compare_usage(request, {"usage": {"prompt_tokens": 101}})
    assert measured == [{"model": "test-model", "messages": request["messages"], "tools": []}]
    assert "estimated=100 actual=101 difference=1" in caplog.text


def test_capacity_failure_preserves_an_oversized_readonly_question_and_logs_the_parts(monkeypatch, caplog):
    metadata = inputs().model_metadata

    def character_counter(*, model, messages=None, tools=None, text=None):
        assert model == metadata.requested_model
        content = text if text is not None else json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        return len(content)

    monkeypatch.setattr(prompt_capacity, "token_counter", character_counter)
    request = {"model": metadata.requested_model, "max_tokens": 1024, "tools": [], "messages": [
        {"role": "system", "content": "Read the submitted question."},
        {"role": "user", "content": "Q" * metadata.context_window}]}
    original = copy.deepcopy(request)
    with pytest.raises(RuntimeError, match="model request capacity exceeded"):
        prompt_capacity.fit_request(request, metadata)
    assert request == original
    assert "Model request capacity exceeded" in caplog.text
    assert "system" in caplog.text and "tools" in caplog.text and "reserved_output" in caplog.text
