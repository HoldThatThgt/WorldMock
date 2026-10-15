"""真实冻结观察的无损分组与完整提示容量合同。"""

import copy
import json

import pytest

from tests.integration.runtime.current_contract_fixtures import inputs
from tests.life.conftest import advance_until_ready, context, stage_day_plan, travel, use_app
from tests.life.environment_runtime import act, act_group
from worldmock.life import prompt_capacity
from worldmock.life.activity_control import PHASE_PENDING
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.tool_groups import compact, compact_render, compact_tool_layer, expand_tool_layer


def test_actual_active_tools_roundtrip_losslessly_and_repeated_groups_do_not_duplicate_actions(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "active", use_app("抖音"))
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
    """两个槽位都被占时每一行的不可用原因点名该槽位的占用者；有活动待收尾时每一行的原因与真实开始请求得到的拒绝
    原因一字不差。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, accepted = act_group(engine, "both-slots", use_app("Keep"), travel("公司"))
    assert [result.status for result in accepted] == ["in_progress", "in_progress"]
    watching, walking = (engine.context.state.activities[result.activity_id].name for result in accepted)
    full = engine.observe(context(engine, "slots-full"))
    rows = {kind: [row for target in full["tool_layer"]["targets"] if target["kind"] == kind
                   for row in target["actions"]] for kind in ("location", "product", "app")}
    assert all(rows.values()) and not any(row["available"] for group in rows.values() for row in group)
    assert {row["action"] for row in rows["location"]} == {"travel"}
    assert {row["reason"] for row in rows["location"]} == {f"The first activity slot is occupied by {walking}."}
    assert {row["reason"] for row in rows["product"] + rows["app"]} == {
        f"The second activity slot is occupied by {watching}."}
    assert advance_until_ready(engine)
    pending = engine.observe(context(engine, "completion"))
    assert [row["name"] for row in pending["tool_layer"]["targets"] if row["phase"] == PHASE_PENDING] == [watching]
    waiting = [row for target in pending["tool_layer"]["targets"] for row in target["actions"]]
    assert waiting and all(not row["available"] for row in waiting)
    _, refused = act(engine, "completion-start", use_app("哔哩哔哩"))
    assert refused.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert {row["reason"] for row in waiting} == {refused.message}


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
