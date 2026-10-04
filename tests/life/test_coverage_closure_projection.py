"""公开接口行为补齐：呈现游标与本地时刻、道路范围、保留项返回行、公开供给行、提示容量与边界判定段。

这些接口此前只被部分执行（有的没有生产调用方，只有文档化的行为）。本文件按各自文档说明的行为，
经最自然的入口逐一断言可观察结果：
- `presentation.local_hour` / `cursor_key`：观察面带场景时区的本地时刻；续页只凭游标里的完整排序键定位，游标不承载轮转偏移。
- `discovery.route_row` / `range_status`：只用已准入的道路记录，范围只比较分时预计时长，未知不算超范围。
- `candidates.OrderedCandidate.as_row`：保留项返回行带排序键原料（定点口味与注意力、相关度与哈希决胜键），零权重照常保留。
- `result_projection.public_supply_row`：报价未知时显式为空，来源只给可回溯引用。
- `prompt_capacity`：上下文长度只认冻结模型合同，计量失败与缺少输出预留都失败关闭。
- `rhythm.decide_engine_rhythm`：被唤醒人物的反馈活动对齐决策边界，游戏回合保持到期。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from litellm import token_counter

from tests.life.conftest import advance_next
from tests.life.test_storefront_discovery import NOW, _location, _route, _source, _supply
from worldmock.catalog.affordances import default_affordances
from worldmock.categories import PairingState
from worldmock.footprints.models import EventDraft
from worldmock.life import discovery, presentation, prompt_capacity
from worldmock.life.candidates import CandidateEntry, CandidateInputs, LocationView, recall, tiebreak_digest
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, SupplyQuoteState, WorldState,
)
from worldmock.life.presentation import PoolEntry, PresentationContext
from worldmock.life.result_projection import public_supply_row
from worldmock.life.semantics import CandidateConstants
from worldmock.model_endpoint import DEEPSEEK_CONTEXT_LENGTH, DEEPSEEK_MODEL


#: 路线行只公开的道路记录字段，外加自由流时长与（可达时）分时系数；原始响应路径与摘要不外露。
_ROUTE_FIELDS = {"route_id", "origin_id", "destination_id", "mode", "status", "distance_meters",
                 "duration_seconds", "fare_fen", "reason", "source", "routing_instance", "routing_profile",
                 "free_flow_duration_seconds"}


@pytest.fixture
def street():
    """人物在家；到 store-a 的步行路线可达（自由流 200 秒，分时系数 2），到 store-u 未知，到 store-b 不可达。"""
    supply = _supply("supply-a", "store-a")
    product = SimpleNamespace(product_id="meal", name="Meal", energy_kcal_per_unit=500, use_duration_seconds=600)
    scenario = SimpleNamespace(
        locations=(_location("home"), _location("store-a")), supplies=(supply,), products=(product,),
        timezone="Asia/Shanghai",
        routes=(_route("store-a", "reachable"), _route("store-u", "unknown"), _route("store-b", "unreachable")))
    agent = AgentState.model_construct(agent_id=1, location_id="home", qualifications=set())
    state = WorldState.model_construct(version=0, current_time=NOW, agents={1: agent},
                                       supply_quotes={"supply-a": None}, supply_stock={"supply-a": 5},
                                       opening_exceptions={})
    semantics = SimpleNamespace(
        travel_planned_seconds=lambda mode, base, now: base * 2,
        travel_time_factor=lambda mode, now: 2.0,
        purchase_pairing=lambda product: SimpleNamespace(state=PairingState.DEFINED, coefficient=1),
        purchase_amount=lambda product, price, quantity: price * quantity)
    return discovery.DiscoveryContext(state, scenario, semantics, agent)


def test_moment_view_carries_the_local_hour_of_the_scenario_timezone(engine_factory):
    """观察面（此刻能不能做）带人物所在场景时区自午夜起的小时数；目录浏览面不判定营业时段，本地时刻为空。"""
    engine = engine_factory()
    zone = ZoneInfo(engine.context.scenario.timezone)
    for wall, expected in (((7, 30, 36), 7.51), ((0, 0, 0), 0.0), ((23, 45, 0), 23.75)):
        epoch = int(datetime(2026, 1, 6, *wall, tzinfo=zone).timestamp())
        state = engine.context.state.model_copy(update={"current_time": epoch})
        context = PresentationContext(state, engine.context.scenario, engine.context.semantics, state.agents[1])
        assert presentation.local_hour(context) == pytest.approx(expected)
        moment = presentation.candidate_inputs(context, {}, moment=True)
        assert moment.local_hour == pytest.approx(expected)
        assert moment.require_location_instance is True
        catalog = presentation.candidate_inputs(context, {}, moment=False)
        assert catalog.local_hour is None and catalog.require_location_instance is False


def _four_supplies() -> tuple[tuple[PoolEntry, ...], CandidateInputs]:
    """四条供给、每页两条的一次取候选输入（换新名额比例 0.5，即每次一个）。"""
    identities = ("supply-a", "supply-b", "supply-c", "supply-d")
    entries = tuple(PoolEntry(object_id=identity, kind="product", bucket=identity, row={"supply_id": identity})
                    for identity in identities)
    place = LocationView("home", frozenset({"home"}), opening_status="open")
    constants = CandidateConstants(presentation_limit=2, pool_limit=10, rotation_quota_ratio=0.5,
                                   rotation_stale_seconds=3600, retrieval_index_threshold=100)
    inputs = CandidateInputs(location=place, locations={"home": place}, sources=presentation.entry_sources(entries),
                             current_time=NOW, window_end_time=NOW + 1000, presentation_limit=2, local_hour=None,
                             rotation_constants=constants, scenario_id="scene", agent_id=1, local_date="2026-01-06")
    return entries, inputs


def test_continuation_pages_follow_the_cursor_without_a_rotation_offset():
    """续页只凭游标承载的完整排序键定位（游标不承载轮转偏移）：同一世界状态下两页不重不漏；轮转只看各对象的
    上次呈现时刻，续页照同一组时刻重算出同一序列。"""
    entries, inputs = _four_supplies()
    first = presentation.present(entries, inputs, "product", None)
    assert set(json.loads(first.next_cursor)) == {"key", "identity"}
    assert len(presentation.cursor_key(first.next_cursor)) == 4
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
    entries, inputs = _four_supplies()
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


def test_route_rows_use_admitted_roads_and_time_of_day_duration(street):
    """路线行只取已准入的道路记录：可达路线的预计时长按分时系数换算并保留自由流时长；未知与不可达照实呈现；
    当前位置直接给零时长行，不为它虚构（也不查找）一段路线。"""
    reachable = discovery.route_row(street, "store-a", "walking")
    assert set(reachable) == _ROUTE_FIELDS | {"travel_time_factor"}
    assert (reachable["route_id"], reachable["status"], reachable["mode"]) == (
        "home-store-a-walking", "reachable", "walking")
    assert reachable["free_flow_duration_seconds"] == 200
    assert reachable["duration_seconds"] == 400 and reachable["travel_time_factor"] == 2.0
    assert reachable["distance_meters"] == 300 and reachable["fare_fen"] == 0
    assert reachable["source"]["kind"] == "scenario" and reachable["routing_profile"] == "foot"
    for destination, status in (("store-u", "unknown"), ("store-b", "unreachable")):
        row = discovery.route_row(street, destination, "walking")
        assert set(row) == _ROUTE_FIELDS
        assert (row["status"], row["reason"]) == (status, status)
        assert row["duration_seconds"] is None and row["free_flow_duration_seconds"] is None
    here = discovery.route_row(street, "home", "driving")
    assert here == {"route_id": None, "origin_id": "home", "destination_id": "home", "mode": "driving",
                    "status": "reachable", "distance_meters": 0, "duration_seconds": 0, "fare_fen": 0,
                    "reason": "already_at_destination", "source": None}


def test_range_status_compares_planned_duration_and_never_reads_unknown_as_outside(street):
    """道路范围只比较分时预计时长（不是自由流时长）；无上限为 unrestricted；资料未知或不可达照实返回，不算超范围。"""
    reachable = discovery.route_row(street, "store-a", "walking")
    assert discovery.range_status(reachable, None) == "unrestricted"
    assert discovery.range_status(reachable, 400) == "inside"
    assert discovery.range_status(reachable, 399) == "outside"
    assert discovery.range_status(reachable, 300) == "outside"
    for destination, status in (("store-u", "unknown"), ("store-b", "unreachable")):
        row = discovery.route_row(street, destination, "walking")
        assert discovery.range_status(row, 10_000) == status
        assert discovery.range_status(row, None) == status
    assert discovery.range_status(discovery.route_row(street, "home", "walking"), 0) == "inside"


def test_retained_rows_carry_fixed_point_rank_features_in_order_key_order():
    """保留项返回行带完整排序键原料（定点口味权重与注意力先验、相关度与哈希决胜键）；零权重对象照常保留，排在非零权重
    之后。"""
    place = LocationView("home", frozenset({"home"}), opening_status="open")
    entries = tuple(CandidateEntry(name, "lifestyle", location_kinds=frozenset({"anywhere"}))
                    for name in ("walk", "tea", "yoga"))
    inputs = CandidateInputs(location=place, locations={"home": place}, sources={"activated": entries},
                             current_time=NOW, window_end_time=NOW + 600, local_hour=None,
                             require_location_instance=False, relevance={"walk": 2},
                             taste_weights={"yoga": 0.5, "tea": 0.25}, attention_priors={"yoga": 0.6, "tea": 0.8})
    page = recall(inputs)
    assert page.excluded == ()
    assert [item.as_row() for item in page.retained] == [
        {"object_id": "yoga", "kind": "lifestyle", "taste_weight": 5000, "attention_prior": 6000, "relevance": 0,
         "tiebreak": tiebreak_digest("", 0, "", "yoga")},
        {"object_id": "tea", "kind": "lifestyle", "taste_weight": 2500, "attention_prior": 8000, "relevance": 0,
         "tiebreak": tiebreak_digest("", 0, "", "tea")},
        {"object_id": "walk", "kind": "lifestyle", "taste_weight": 0, "attention_prior": 0, "relevance": 2,
         "tiebreak": tiebreak_digest("", 0, "", "walk")},
    ]


def test_public_supply_row_keeps_an_unknown_quote_explicit(street):
    """报价未知的供给行公开时报价与报价来源都显式为空，比较与购买字段照留；来源只给可回溯引用，完整声明留在原始证据。"""
    supply = street.scenario.supplies[0]
    raw = {**discovery.supply_row(street, supply), "category": "meal"}
    public = public_supply_row(raw)
    assert raw["quote"] is None and raw["source"]["license"] == "test-license"
    assert public["quote"] is None and public["quote_source"] is None
    assert public["source"] == {"kind": "scenario", "reference": "https://example.test/scenario",
                                "fact_cutoff": "2026-01-01"}
    assert "category" not in public
    assert {key: public[key] for key in ("supply_id", "price_fen", "purchase_price_fen", "quote_status",
                                         "purchase_allowed", "current_stock")} == {
        "supply_id": "supply-a", "price_fen": None, "purchase_price_fen": None, "quote_status": "unknown",
        "purchase_allowed": False, "current_stock": 5}
    street.state.supply_quotes["supply-a"] = SupplyQuoteState(
        price_fen=120, price_version="v1", effective_from=NOW - 1, effective_to=None,
        source=_source().model_dump(mode="json"))
    known = public_supply_row(discovery.supply_row(street, supply))
    assert known["quote"] == {"effective_from": NOW - 1, "effective_to": None}
    assert known["quote_source"] == public["source"]
    assert known["price_fen"] == 120 and known["quote_status"] == "known"


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


def test_game_turn_stays_due_while_feedback_activities_realign_at_the_boundary(engine_factory):
    """边界判定段让被唤醒人物的反馈类活动对齐决策边界，但游戏回合不重排：交互边界到期的那一秒人物就绪，
    游戏仍停在该回合（game_feedback），同一秒到期的等待活动顺延一个反馈间隔。"""
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    now = state.current_time
    turn = now + 1
    state.agents[1] = state.agents[1].model_copy(update={"next_decision_at": now + 3600, "last_decision_time": now})
    # 活动进度草稿以活动的来源请求事件为直接原因，因此两项活动引用随状态一起提交的真实事件。
    origin = EventDraft(event_type="environment_event", occurred_at=now, visibility="system",
                        data={"kind": "test_state_staged"})
    shared = {"agent_id": 1, "location_id": state.agents[1].location_id, "status": ActivityStatus.ACTIVE,
              "started_at": now, "planned_end_at": now + 7200, "last_settled_at": now, "elapsed_seconds": 0,
              "planned_duration_seconds": 7200, "next_interaction_at": turn, "origin_action_id": "act",
              "origin_decision_id": "dec", "origin_request_event_id": origin.event_id}
    registry = default_affordances()
    for identity, kind in (("game", ActivityKind.PLAY_GAME), ("wait", ActivityKind.WAIT)):
        state.activities[identity] = ActivityState(activity_id=identity, kind=kind,
                                                   resource_claims=set(registry.resource_claims(kind.value)),
                                                   **shared)
    state.version += 1
    engine.context.store.commit(state.model_dump(mode="json"), [origin])
    engine.context.state = state
    advance_next(engine)
    assert engine.context.state.current_time == turn
    game, wait = engine.context.state.activities["game"], engine.context.state.activities["wait"]
    assert game.status == ActivityStatus.ACTIVE
    assert (game.next_interaction_at, game.boundary_type) == (turn, "game_feedback")
    assert wait.next_interaction_at == turn + engine.context.scenario.activity_feedback_seconds
    assert wait.boundary_type is None
    assert engine.context.state.agents[1].next_decision_at == turn
    assert engine.scheduling_state().ready_agent_ids == (1,)
