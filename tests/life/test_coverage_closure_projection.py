"""当前候选分页、真实相关度排序和模型容量合同的行为检查。"""
from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest
from litellm import token_counter

from tests.life.conftest import (advance_until_ready, context, stage_day_plan, start_activity, submit_round, wake_by_mail)
from worldmock.life import presentation, prompt_capacity
from worldmock.life.candidates import CandidateEntry, CandidateInputs, LocationView, recall, tiebreak_digest
from worldmock.life.presentation import PoolEntry, PresentationContext
from worldmock.life.semantics import CandidateConstants
from worldmock.model_endpoint import DEEPSEEK_CONTEXT_LENGTH, DEEPSEEK_MODEL

NOW = 1_700_000_000


def test_candidate_views_preserve_current_place_and_scenario_local_date(engine_factory):
    """当前候选依照真实地点资格，本地日期来自场景时区，不保留营业时段参数。"""
    engine = engine_factory()
    state = engine.context.state
    context = PresentationContext(state, engine.context.scenario, engine.context.semantics, state.agents[1])
    moment = presentation.candidate_inputs(context, {}, moment=True)
    catalog = presentation.candidate_inputs(context, {}, moment=False)
    assert moment.require_location_instance is True and catalog.require_location_instance is False
    assert moment.location.location_id == state.agents[1].location_id
    assert moment.local_date == presentation.local_date(state.current_time, state.timezone)
    assert not hasattr(moment, "local_hour")


def _four_foods() -> tuple[tuple[PoolEntry, ...], CandidateInputs]:
    """四条食品、每页两条的一次取候选输入（换新名额比例 0.5，即每次一个）。"""
    identities = ("food-a", "food-b", "food-c", "food-d")
    entries = tuple(PoolEntry(object_id=identity, kind="product", row={"food_id": identity})
                    for identity in identities)
    place = LocationView("home", frozenset({"home"}))
    constants = CandidateConstants(presentation_limit=2, pool_limit=10, rotation_quota_ratio=0.5,
                                   rotation_stale_seconds=3600)
    inputs = CandidateInputs(location=place, locations={"home": place}, sources=presentation.entry_sources(entries),
                             current_time=NOW, window_end_time=NOW + 1000, presentation_limit=2,
                             rotation_constants=constants, scenario_id="scene", agent_id=1, local_date="2026-01-06")
    return entries, inputs


def test_continuation_pages_follow_the_cursor_without_a_rotation_offset():
    """续页只凭游标承载的完整排序键定位（游标不承载轮转偏移）：同一世界状态下两页不重不漏；轮转只看各对象的
    上次呈现时刻，续页照同一组时刻重算出同一序列。"""
    entries, inputs = _four_foods()
    first = presentation.present(entries, inputs, "product", None)
    assert set(json.loads(first.next_cursor)) == {"key", "identity"}
    assert len(presentation.cursor_key(first.next_cursor)) == 2
    second = presentation.present(entries, inputs, "product", first.next_cursor)
    assert second.next_cursor is None
    assert sorted(first.presented + second.presented) == sorted(entry.object_id for entry in entries)
    assert presentation.present(entries, inputs, "product", first.next_cursor) == second
    stamped = replace(inputs, last_presented_at={object_id: NOW - 60 for object_id in first.presented})
    again = presentation.present(entries, stamped, "product", None)
    assert again.presented[0] in second.presented  # 换新名额移到尚未呈现的对象


@pytest.mark.parametrize("cursor", [None, "", "   "])
def test_blank_cursor_starts_the_first_page(cursor):
    """首屏（空游标或空白游标）从序列开头取一页。"""
    entries, inputs = _four_foods()
    assert presentation.present(entries, inputs, "product", cursor).presented == presentation.present(
        entries, inputs, "product", None).presented


@pytest.mark.parametrize("cursor,message", [
    ("not-json", "not valid JSON"),
    ('{"key": [1, 0, 0, "ab"]}', "fields do not match"),
    ('{"key": [1, 0, "supply-a"], "offset": 1, "identity": "abc"}', "fields do not match"),
    ('{"key": [1, 0, "supply-a"], "identity": "abc"}', "not a stable order key"),
    ('{"key": [1, 0, true, "ab"], "identity": "abc"}', "not a stable order key"),
    ('{"key": [1, 0, 0, " "], "identity": "abc"}', "not a stable order key"),
])
def test_malformed_cursor_is_refused_instead_of_restarting(cursor, message, caplog):
    """游标载荷不合法即报错并留日志，不静默从头开始；旧形状（三段键、带偏移）同样拒绝。"""
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match=message):
        presentation.cursor_key(cursor)
    assert "Candidate cursor" in caplog.text


def test_retained_rows_carry_real_relevance_and_stable_tiebreak():
    """实际相关度与哈希是唯一排序原料，零相关度仍保留。"""
    place = LocationView("home", frozenset({"home"}))
    entries = tuple(CandidateEntry(name, "lifestyle", location_kinds=frozenset({"anywhere"}))
                    for name in ("walk", "tea", "yoga"))
    inputs = CandidateInputs(location=place, locations={"home": place}, sources={"activated": entries},
                             current_time=NOW, window_end_time=NOW + 600, require_location_instance=False,
                             relevance={"walk": 2})
    page = recall(inputs)
    assert page.excluded == ()
    expected = [{"object_id": name, "kind": "lifestyle", "relevance": 2 if name == "walk" else 0,
                 "tiebreak": tiebreak_digest("", 0, "", name)} for name in ("walk", "tea", "yoga")]
    assert [item.as_row() for item in page.retained] == sorted(expected, key=lambda row: (-row["relevance"], row["tiebreak"]))


def _model_request(**overrides) -> dict:
    """一份按冻结模型身份发送的最小决策请求（含明确的输出预留）。"""
    request = {"model": DEEPSEEK_MODEL, "max_tokens": 200,
               "messages": [{"role": "system", "content": "你是一位在上海工作的白领。"},
                            {"role": "user", "content": "现在是早上七点半，你打算做什么？"}]}
    request.update(overrides)
    return request


def test_context_length_comes_only_from_the_frozen_model_contract(caplog):
    """上下文长度只取冻结模型合同里的官方值；身份不符的模型在发送前被容量预检拒绝，不猜一个窗口。"""
    assert prompt_capacity.model_context_length(prompt_capacity.MODEL_ALIAS) == DEEPSEEK_CONTEXT_LENGTH
    request = _model_request()
    assert prompt_capacity.fit_request(request) is request
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="model context metadata is unavailable"):
        prompt_capacity.fit_request(_model_request(model="gpt-4o-mini"))
    assert "gpt-4o-mini" in caplog.text


def test_prompt_count_measures_the_sent_messages_and_tool_definitions():
    """计量实际发送的消息与工具定义：与分词器对同一消息的计数一致，工具定义计入总数，输出预留另行相加。"""
    plain = _model_request()
    tool = {"type": "function", "function": {
        "name": "start_activity", "description": "开始一项活动",
        "parameters": {"type": "object", "properties": {"targets": {"type": "array", "items": {"type": "string"}}},
                       "required": ["targets"]}}}
    with_tools = _model_request(tools=[tool])
    counted = prompt_capacity.prompt_token_count(plain)
    assert counted == token_counter(model=prompt_capacity.TOKENIZER_MODEL, messages=plain["messages"]) > 0
    assert prompt_capacity.prompt_token_count(with_tools) > counted
    assert prompt_capacity.request_token_count(with_tools) == prompt_capacity.prompt_token_count(with_tools) + 200


def test_unmeasurable_request_fails_closed(caplog):
    """分词器无法计量的请求不以零计：计量失败明确报错（保留原因）并留日志，请求不会未经计量就发出。"""
    broken = _model_request(messages=[{"role": "user", "content": [{"type": "image_url"}]}])
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="token count failed") as raised:
        prompt_capacity.prompt_token_count(broken)
    assert raised.value.__cause__ is not None
    assert "Model request token count failed" in caplog.text
    with pytest.raises(RuntimeError, match="token count failed"):
        prompt_capacity.fit_request(broken)


@pytest.mark.parametrize("reserve", ["missing", 0, -1, "200", 200.0])
def test_capacity_check_requires_an_explicit_positive_output_reserve(reserve, caplog):
    """容量预检必须计入明确预留的最大输出：缺省、非正数或非整数一律拒绝，不按零预留放行。"""
    request = _model_request()
    if reserve == "missing":
        del request["max_tokens"]
    else:
        request["max_tokens"] = reserve
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="output reserve is missing"):
        prompt_capacity.request_token_count(request)
    assert "output reserve is missing" in caplog.text
    with pytest.raises(RuntimeError, match="output reserve is missing"):
        prompt_capacity.fit_request(request)



def test_real_mail_wakes_waiting_person_without_delaying_planned_end(engine_factory):
    """真实等待按固定反馈时长结束；邮件提前叫醒人物，不改写已受理的结束时刻。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, [result] = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "wait-before-mail")
    assert result.status == "in_progress"
    key = result.activity_id
    initial = engine.context.state.activities[key].planned_end_at
    assert initial == engine.context.state.current_time + engine.context.scenario.activity_feedback_seconds
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    state = engine.context.state
    wait = state.activities[key]
    assert wait.status == "active"
    assert wait.planned_end_at == initial and wait.elapsed_seconds == 60
    assert wait.next_interaction_at is None
    assert wait.boundary_type is None
    assert state.agents[1].next_decision_at == state.current_time
    assert engine.scheduling_state().ready_agent_ids == (1,)
