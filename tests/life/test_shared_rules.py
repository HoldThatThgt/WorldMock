"""@brief 共享活动规则跨原有呈现、受理、预估与结算入口的一致性。"""

from dataclasses import replace
from tests.data_paths import ENVIRONMENT_ROOT
from types import SimpleNamespace

import pytest

from tests.life.test_motivation import _context
from tests.life.test_work_topics import _work_world, _start, _advance
from worldmock.life import activities
from worldmock.life.models import ActivityState
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import activity_factors
from worldmock.life.activity_control import _report_refusal
from worldmock.life.admission import Context
from worldmock.life.models import ActivityKind, ActivityStatus, ContentProgress
from worldmock.life.motivation import _continue_candidate, _entertainment_delta
from worldmock.life.named_catalog import _lifestyle_rows
from worldmock.life.readable import duration_phrase
from worldmock.life.relevance import RelevanceFacts, entry_relevance
from worldmock.life.schedule import DayPlan, PlanGoal, activity_family, progress_day_plan
from worldmock.life.tool_layer import _facts_seconds, _route, _work_check
from worldmock.life.work import work_start_refusal




def test_lifestyle_duration_matches_tool_layer_named_catalog_and_admission(engine_factory, monkeypatch):
    """生活活动的按名清单同样说明真实受理上限，而非未封顶的目录典型时长。"""
    engine = engine_factory()
    item = replace(engine.context.lifestyle_items[0], typical_duration_minutes=2000)
    agent = engine.context.state.agents[1]
    context = replace(engine.observations._layer_context(agent, (), []), lifestyle_items=(item,))
    assert _facts_seconds(context, {"object_id": item.activity_id}, {"canonical_action": "do"}) == 86_400
    assert _lifestyle_rows(context, (item,))[0]["note"] == duration_phrase(86_400)
    monkeypatch.setattr(engine.context, "lifestyle_items", (item,))
    monkeypatch.setattr(engine.actions, "_start", lambda state, pending, params: params)
    accepted = engine.actions._start_lifestyle_action(engine.context.state, SimpleNamespace(agent_id=1), item.activity_id)
    assert accepted.planned_duration_seconds == 86_400




def test_declared_sleep_channel_does_not_control_body_sleep_settlement(engine_factory, monkeypatch):
    """注册表睡眠通道改为清醒声明后，真实睡眠仍恢复不足账和睡眠压力，且不产生娱乐分录。"""
    from worldmock.catalog import affordances
    from worldmock.life import activities, body
    from worldmock.life.body import ActivityFactors, ENTERTAINMENT_RESOURCE, last_flows, settle_body, settle_shared_body
    from worldmock.life.models import BodyState

    engine = engine_factory()
    registry = affordances.engine_affordances()
    changed_registry = replace(registry, kinds={**registry.kinds,
                                                "sleep": replace(registry.kinds["sleep"], sleep_channel="awake")})
    monkeypatch.setattr(affordances, "engine_affordances", lambda: changed_registry)
    monkeypatch.setattr(body, "engine_affordances", lambda: changed_registry)
    monkeypatch.setattr(activities, "_AFFORDANCES", changed_registry)
    config = engine.context.scenario.body
    before = BodyState(energy_kcal=1600, sleep_pressure=0.6, sleep_deficit_hours=4, entertainment_need=0.3)
    factors = ActivityFactors(config.activity_met["sleep"])
    for settle in (lambda: settle_body(before, ActivityKind.SLEEP, 3600, config, factors),
                   lambda: settle_shared_body(before, ((ActivityKind.SLEEP, factors),), 3600, config)):
        last_flows()
        result = settle()
        assert result.after.sleep_deficit_hours == pytest.approx(3)
        assert result.after.sleep_pressure < before.sleep_pressure
        assert result.after.entertainment_need == before.entertainment_need
        assert result.after.energy_kcal < before.energy_kcal
        assert not [flow for flow in last_flows() if flow.resource == ENTERTAINMENT_RESOURCE]


def test_travel_plan_matches_tool_layer_admission_and_engine_validation(engine_factory):
    """@brief 工具报价、真实受理和引擎复核都消费同一完整登记图。"""
    engine = engine_factory()
    context = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    trip, seconds = _route(context, 'office', 'driving')
    pending = SimpleNamespace(agent_id=1, action_id='trip', decision_id='decision', requested_event_id='request')
    changed, result = engine.actions._start_travel_action(engine.context.state, pending, 'office',
                                                        {'selected_action': {'travel_mode': 'driving'}})
    accepted = changed.activities[result.activity_id]
    assert accepted.planned_duration_seconds == seconds == trip.duration_seconds
    from worldmock.life.travel import require_travel_duration
    from worldmock.life.models import ActivityStartParams
    wrong = ActivityStartParams(activity_type='travel', planned_duration_seconds=seconds+1,
                                destination_id='office', travel_mode='driving')
    with pytest.raises(BusinessRuleError, match='ROUTE_DURATION_MISMATCH'):
        require_travel_duration(engine.context.scenario, engine.context.semantics, 'home', wrong,
                                engine.context.state.current_time)


def test_work_refusal_matches_listing_and_admission(engine_factory):
    """@brief 目标仅剩五十九秒时，开工和恢复使用同一拒绝事实。"""
    semantics, state, topic, _ = _work_world()
    state, activity_id = _start(semantics, state, topic)
    state, _ = _advance(semantics, state, activity_id, topic.target_seconds-59)
    agent, paused = activities.change_activity(state.agents[1], state.activities[activity_id],
        __import__('worldmock.life.models', fromlist=['ChangeActivityRequest']).ChangeActivityRequest(
            activity_id=activity_id, operation='pause'), state.current_time, semantics.body)
    state.agents[1], state.activities[activity_id] = agent, paused
    expected = work_start_refusal(topic, topic.target_seconds-59)
    engine = engine_factory()
    context = replace(engine.observations._layer_context(agent, (), []), state=state)
    assert _work_check(context, {'object_id': topic.topic_id}, {}) == expected
    with pytest.raises(BusinessRuleError) as caught:
        engine.actions._targeted_duration(state, 1, ActivityKind.WORK, topic.topic_id)
    assert caught.value.public_reason == expected.reason
    resume = Context(state, 1, {}, semantics.catalog.graph, work_topics={topic.topic_id:topic})
    assert _report_refusal(resume, paused) == expected


def test_registry_met_key_is_shared_by_preview_and_settlement(monkeypatch):
    """@brief 固定应用活动在预估与结算中读取同一个代谢键。"""
    from worldmock.life import activities, motivation
    context = _context(location='home')
    now = context.current_time
    activity = ActivityState(activity_id='app', agent_id=1, location_id='home', kind='use_app', status='active',
        started_at=now, planned_end_at=now+1800, last_settled_at=now, elapsed_seconds=0,
        planned_duration_seconds=1800, target_id='bilibili', origin_action_id='app',
        origin_decision_id='decision', origin_request_event_id='request', data={'content_kind':'long_video'})
    registry = SimpleNamespace(met_lookup_key=lambda *args, **kwargs: 'declared:app')
    monkeypatch.setattr(activities, '_AFFORDANCES', registry)
    monkeypatch.setattr(motivation, 'engine_affordances', lambda:registry)
    body = context.body.model_copy(update={'activity_met':{'declared:app':1.75}})
    context = replace(context, body=body)
    assert activity_factors(activity, context.agent, body).met == 1.75
    assert _continue_candidate(context, activity) is not None


@pytest.mark.parametrize("category", [None, "", "unknown"])
def test_unknown_lifestyle_family_does_not_advance_or_score(category):
    """缺失与未知品类不能猜成目标证据或相关度加分。"""
    plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind="运动", goal="运动一下", registered_at=0),))
    activity = {"activity_id": "a", "kind": "lifestyle", "category": category, "status": "active"}
    assert activity_family(activity) is None
    assert progress_day_plan(plan, activity, 100, 200) == plan
    entry = SimpleNamespace(kind="lifestyle", category=category, row={}, object_id="a")
    assert entry_relevance(entry, RelevanceFacts(hour=10, goal_families=frozenset({"lifestyle:sport"}))) == 0


@pytest.mark.parametrize("kind", ["chat", "call"])
def test_social_family_advances_the_social_goal(kind):
    """交谈和通话保留真实家族令牌并一致推进社交目标。"""
    plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind="社交", goal="聊一下", registered_at=0),))
    activity = {"activity_id": "a", "kind": kind, "status": "active"}
    assert activity_family(activity) == kind
    assert progress_day_plan(plan, activity, 100, 200).goals[0].progress_activity_id == "a"


@pytest.mark.parametrize("interval", [(True, 200), (100, False), (100.0, 200), (100, 200.0),
                                     (float("nan"), 200), (100, float("inf")), (200, 200), (200, 100)])
def test_invalid_interval_does_not_advance_the_plan(interval):
    """目标推进与质量及指标读取同一严格整数区间，畸形端点不能变成实际进度。"""
    plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind="社交", goal="聊一下", registered_at=0),))
    activity = {"activity_id": "a", "kind": "chat", "status": "active"}
    assert progress_day_plan(plan, activity, *interval) == plan


def test_mail_headers_match_content_revelation_and_observation(engine_factory):
    """已到达邮件的字段和阅读后正文在内容与真实观察消费者中一致，未到达邮件不能出现。"""
    from worldmock.life.content import mail_headers, reveal_mail

    def mail(document):
        document['messages'] = [{'message_id':'shared-mail','thread_id':'shared-thread','recipient_agent_id':1,
            'sent_at':'2026-01-05T00:01:00+08:00','subject':'共享邮件','body':'测试正文'}]
    engine = engine_factory(mutate=mail)
    agent = engine.context.state.agents[1]
    message = engine.context.scenario.messages[0]
    assert mail_headers(agent, engine.context.rules.messages) == engine.observations._event_tool_support(agent, ())["messages"] == []
    delivered = agent.model_copy(update={"visible_message_ids": {message.message_id}}, deep=True)
    expected = [{"message_id": message.message_id, "thread_id": message.thread_id,
                 "subject": message.subject, "read": False}]
    assert mail_headers(delivered, engine.context.rules.messages) == expected
    assert engine.observations._event_tool_support(delivered, ())["messages"] == expected
    read, revealed = reveal_mail(SimpleNamespace(target_id=message.message_id), delivered, engine.context.rules.messages)
    expected[0].update(read=True, body=message.body)
    assert revealed["body"] == message.body
    assert mail_headers(read, engine.context.rules.messages) == expected
    assert engine.observations._event_tool_support(read, ())["messages"] == expected




@pytest.mark.parametrize("candidate_kind,activity_kind", [("product", "consume"), ("lifestyle", "lifestyle"),
                                                        ("task", "work")])
def test_candidate_kind_mapping_drives_real_generation_and_resource_filter(candidate_kind, activity_kind, monkeypatch):
    """真实三段候选流程按唯一种类映射读取活动占用，只有占用相应资源的候选被排除。"""
    from worldmock.life import candidates

    kinds = ("product", "lifestyle", "task")
    entries = tuple(candidates.CandidateEntry(f"object-{kind}", kind) for kind in kinds)
    registry = SimpleNamespace(resource_claims=lambda kind, **_kwargs: frozenset({f"activity:{kind}"}))
    monkeypatch.setattr(candidates, "engine_affordances", lambda: registry)
    inputs = candidates.CandidateInputs(
        location=None, locations={}, sources={"on_site": entries}, require_location_instance=False,
        current_time=100, window_end_time=1000,
        occupied_resources=frozenset({f"activity:{activity_kind}"}))
    page = candidates.recall(inputs)
    assert {row.entry.kind for row in page.generated} == set(kinds)
    assert page.source_counts["on_site"] == 3
    assert [(row.kind, row.reason) for row in page.excluded] == [(candidate_kind, "ELIGIBILITY_RESOURCE_CONFLICT")]
    assert {row.kind for row in page.retained} == set(kinds) - {candidate_kind}
