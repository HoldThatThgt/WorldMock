"""@brief 固定目录上的真实动机评分，保留前景、习惯、成本与实际工作目标的独立行为合同。"""

from __future__ import annotations

from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import ENVIRONMENT, registered_world
from worldmock.life.content import load_lifestyle_items
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus
from worldmock.life.motivation import (FAMILIES, MAX_CANDIDATES, MotivationContext, _Candidate,
    _continue_candidate, _cost_component, _emotion_state, _softmax, _work_candidates, beta_discount,
    classify_action_event, evaluate_motivation, habit_shares, meal_repeat_weight, prospect_value)
from worldmock.life.retrieval import item_kinds
from worldmock.life.scenario import MotivationProfile

CONTRACT_PROFILE = {'schema_version': 1, 'big_five': {'openness': 4, 'conscientiousness': 6, 'extraversion': 3, 'agreeableness': 4, 'neuroticism': 5}, 'weights': {'money_fen': 0.45, 'energy_kcal': 0.15, 'sleep_debt_hours': 0.15, 'entertainment_need': 0.2, 'goal_task': 0.05}, 'tau': 1.0, 'beta_present_bias': 0.8, 'loss_aversion': {'money_fen': 2.25, 'energy_kcal': 1.5}, 'comfort': {'balance_fen': 20000, 'energy_kcal': 1200}, 'aspiration': {'daily_work_seconds': 14400}, 'emotion_sensitivity': 0.3}


def _profile(**overrides):
    """@brief 当前闭集动机档案不接受过时注意力、口味或有效工时字段。"""
    return MotivationProfile.model_validate({**CONTRACT_PROFILE, **overrides})


@pytest.fixture(scope="module")
def transforms():
    """@brief 生产环境装配器提供实际通用动机参数。"""
    return registered_world()[0].motivation_transforms


def _context(location="office", **overrides):
    """@brief 所有世界输入都由当前实际环境登记、加载和菜单解析产生。"""
    semantics, state = registered_world(location=location)
    topics = tuple(semantics.catalog.work_topics.values())
    context = MotivationContext(agent=state.agents[1], balance_fen=state.accounts["agent:1"],
        body=semantics.body, home_location_id="home", catalog=semantics.catalog,
        location_ids=frozenset(item.location_id for item in semantics.scenario.locations),
        menu=resolve_interaction_menu(semantics.scenario, 1, location),
        lifestyle_items=load_lifestyle_items(ENVIRONMENT / "corpora", semantics.body.activity_met),
        activity_location_kinds=item_kinds(ENVIRONMENT / "corpora"),
        work_topics=topics, topic_totals={topic.topic_id: 0 for topic in topics}, workplace_location_id="office",
        active_activities=(), habit_events=(), consecutive_rejections=0, max_same_moment_decisions=3,
        total_work_seconds=0, current_time=state.current_time, end_time=state.end_time, timezone=state.timezone)
    return replace(context, **overrides)


def _start(*ways):
    """@brief 实际稳定工具逐项方式，没有已经删除的动词参数。"""
    return ("start_activity", {"items": [{"target": "对象", "way": way, "from": None, "to": None}
                                         for way in ways], "prefer": None})

class TestProspectValue:
    def test_zero_and_monotone_grid(self, transforms):
        for lam in (1.0, 2.25):
            previous = None
            for steps in range(-12, 13):
                z = steps * 0.25
                value = prospect_value(z, transforms, lam)
                if z == 0:
                    assert value == 0.0
                if previous is not None:
                    assert value > previous, f"value must increase in z at {z}"
                previous = value

    def test_gain_concavity(self, transforms):
        for x in (0.25, 0.5, 1.0, 2.0, 4.0):
            assert prospect_value(2 * x, transforms, 1.0) < 2 * prospect_value(x, transforms, 1.0)

    def test_loss_aversion_slope(self, transforms):
        lam = 2.25
        for x in (0.25, 0.5, 1.0, 2.0):
            assert abs(prospect_value(-x, transforms, lam)) == pytest.approx(
                lam * prospect_value(x, transforms, 1.0))

    def test_diminishing_relief_away_from_reference_grid(self, transforms):
        """@brief TK 敏感度递减：同量恢复在深亏缺段的效用增益更小（格点递减）。"""
        gain, scale, lam = 300.0, transforms.energy_scale_kcal, 1.5
        previous = None
        for deficit in range(300, 1400, 100):
            relief = lam * ((deficit / scale) ** transforms.satiation_exponent
                            - ((deficit - gain) / scale) ** transforms.satiation_exponent)
            if previous is not None:
                assert relief < previous, "relief must shrink as deficit deepens"
            previous = relief


def test_current_way_habit_classification_and_retired_way_absence():
    expected = {"睡觉": "sleep", "等待": "continue", "到店": "eat", "远程下单": "eat", "食用": "eat",
                "步行": "travel", "驾车": "travel", "打开": "entertainment", "工作": "work"}
    for way, family in expected.items():
        assert classify_action_event(*_start(way)) == (family,)
    assert classify_action_event("report_status", {}) == ("continue",)
    for way in ("观看", "接着看", "游玩", "浏览", "步行前往"):
        assert classify_action_event(*_start(way)) == ()
    assert classify_action_event("submit_work", {}) == ()
    assert classify_action_event(*_start("步行", "打开")) == ("travel", "entertainment")


def test_habit_shares_preserve_recency_and_parallel_family_mass():
    events = [_start("到店"), _start("打开"), _start("打开")]
    shares = habit_shares(events, 0.5)
    assert set(shares) == set(FAMILIES)
    assert shares["eat"] == pytest.approx(0.25 / 1.75)
    assert shares["entertainment"] == pytest.approx(1.5 / 1.75)
    assert sum(shares.values()) == pytest.approx(1)
    both = habit_shares([_start("步行", "打开")], 0.5)
    assert both["travel"] == both["entertainment"] == pytest.approx(0.5)


def test_actual_work_candidates_have_no_topic_payment_or_efficiency_discount():
    context = _context()
    travel, candidates = _work_candidates(context, {"max_tasks": 3})
    assert travel is None and len(candidates) == 1
    candidate = candidates[0]
    assert candidate.goal_work_seconds == candidate.duration_seconds
    assert candidate.money_deferred_fen == candidate.money_now_fen == 0
    assert candidate.work_type is True
    finished = replace(context, topic_totals={"topic-sales": 28800})
    assert _work_candidates(finished, {"max_tasks": 3}) == (None, [])
    empty = replace(context, work_topics=(), topic_totals={})
    assert _work_candidates(empty, {"max_tasks": 3}) == (None, [])


def test_away_from_workplace_generates_only_actual_graph_trip():
    context = _context(location="home")
    travel, candidates = _work_candidates(context, {"max_tasks": 3})
    assert candidates == [] and travel.family == "travel"
    assert travel.duration_seconds > 0 and travel.money_now_fen <= 0
    assert travel.goal_work_seconds == 0


def test_work_goal_deficit_reads_actual_seconds_only():
    profile = _profile()
    empty = _context(total_work_seconds=0)
    half = replace(empty, total_work_seconds=7200)
    full = replace(empty, total_work_seconds=14400)
    assert _emotion_state(empty, profile) == (0, 1)
    assert _emotion_state(half, profile) == (0, 0.5)
    assert _emotion_state(full, profile) == (0, 0)
    assert _emotion_state(replace(full, consecutive_rejections=9), profile) == (1, 0)


def test_continue_work_uses_remaining_elapsed_seconds_without_skill_fields():
    context = _context()
    now = context.current_time
    activity = ActivityState(activity_id="work", agent_id=1, location_id="office", kind="work", status="active",
        started_at=now, planned_end_at=now + 1800, last_settled_at=now, elapsed_seconds=0,
        planned_duration_seconds=1800, target_id="topic-sales", origin_action_id="work",
        origin_decision_id="decision", origin_request_event_id="request", data={"topic_id":"topic-sales",
        "worked_before_seconds": 0})
    candidate = _continue_candidate(context, activity)
    assert candidate.goal_work_seconds == candidate.duration_seconds == 1800
    assert candidate.money_deferred_fen == 0


def test_score_shape_probabilities_read_only_and_four_components(transforms):
    context = _context()
    before = context.agent.model_dump_json()
    scored = evaluate_motivation(context, _profile(), transforms, top_k=MAX_CANDIDATES)
    assert scored and context.agent.model_dump_json() == before
    assert sum(item.softmax_probability for item in scored) == pytest.approx(1)
    assert [item.rank for item in scored] == list(range(1, len(scored) + 1))
    assert all(set(item.components) == {"homeostatic", "habit", "emotion", "cost"} for item in scored)
    assert all(item.score == pytest.approx(item.components["homeostatic"] + item.components["habit"]
        + item.components["emotion"] - item.components["cost"]) for item in scored)


def test_softmax_stable_monotone_grid_and_equal_values():
    assert _softmax([], 1) == []
    assert _softmax([5, 5, 5], 1) == pytest.approx([1 / 3] * 3)
    values = _softmax([1000, 1001, 1002], 0.3)
    assert values[0] < values[1] < values[2] and sum(values) == pytest.approx(1)
    assert _softmax([0, 1], 0.1)[1] > _softmax([0, 1], 10)[1]


def test_price_cost_is_counted_once_and_duration_normalized(transforms):
    profile = _profile()
    one = _Candidate("eat", "one", "same meal", duration_seconds=3600, money_now_fen=-1000)
    two = replace(one, money_now_fen=-2000)
    difference = _cost_component(two, profile, transforms) - _cost_component(one, profile, transforms)
    assert difference == pytest.approx(1000 / transforms.money_scale_fen)
    assert beta_discount(profile, 0) == 1
    assert beta_discount(profile, 3600) == profile.beta_present_bias


def test_meal_repeat_weight_preserves_diminishing_probability():
    assert [meal_repeat_weight(index) for index in range(4)] == pytest.approx([1, 0.5, 1 / 3, 0.25])


@pytest.mark.parametrize("mutation", [
    {"weights": {"money_fen": 0.9, "energy_kcal": 0.05, "sleep_debt_hours": 0.05,
                 "entertainment_need": 0.05, "goal_task": 0.05}},
    {"weights": {"money_fen": 0.45}}, {"loss_aversion": {}}, {"loss_aversion": {"money_fen": 0.5}},
    {"comfort": {"balance_fen": 100}}, {"aspiration": {"daily_work_seconds": -1}},
    {"aspiration": {"daily_effective_work_seconds": 14400}}, {"schema_version": 2}, {"extra_field": 1},
    {"attention_prior": {"article": 1}},
])
def test_current_motivation_contract_rejects_invalid_and_retired_fields(mutation):
    with pytest.raises(ValidationError):
        _profile(**mutation)
