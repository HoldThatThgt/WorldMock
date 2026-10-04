"""@brief 共享活动规则跨原有呈现、受理、预估与结算入口的一致性。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.life.test_motivation import _agent, _body, _context, _running
from worldmock.errors import BusinessRuleError
from worldmock.life.activities import activity_factors
from worldmock.life.activity_control import _report_refusal
from worldmock.life.admission import Context
from worldmock.life.models import ActivityKind, ActivityStatus, ContentProgress
from worldmock.life.motivation import _continue_candidate, _entertainment_delta
from worldmock.life.named_catalog import _content_rows, _lifestyle_rows
from worldmock.life.readable import duration_phrase
from worldmock.life.relevance import RelevanceFacts, entry_relevance
from worldmock.life.schedule import DayPlan, PlanGoal, activity_family, progress_day_plan
from worldmock.life.tool_layer import _facts_seconds, _route, _work_check
from worldmock.life.work import work_start_refusal


def test_content_duration_matches_tool_layer_named_catalog_and_admission(engine_factory, monkeypatch):
    """同一进度的长内容、续看与不足一分钟重启在三条原入口中采用相同封顶秒数。"""
    engine = engine_factory()
    original = engine.scenario.contents[0]
    agent = engine.state.agents[1]
    for duration, position, expected in ((200_000, 0, 86_400), (200_000, 199_970, 86_400),
                                         (1000, 200, 800), (1000, 970, 1000)):
        item = original.model_copy(update={"duration_seconds": duration})
        agent.content_progress[item.item_id] = ContentProgress(
            item_id=item.item_id, position_seconds=position, exposure_count=1)
        monkeypatch.setitem(engine.rules.contents, item.item_id, item)
        scenario = engine.scenario.model_copy(update={"contents": (item,)})
        context = replace(engine._layer_context(agent, (), []), scenario=scenario)
        binding, action = {"object_id": item.item_id}, {"canonical_action": "watch"}
        listed = _facts_seconds(context, binding, action)
        accepted = engine._targeted_duration(engine.state, 1, ActivityKind.WATCH, item.item_id)
        assert listed == accepted == expected
        assert _content_rows(context)[0]["note"] == duration_phrase(accepted)


def test_lifestyle_duration_matches_tool_layer_named_catalog_and_admission(engine_factory, monkeypatch):
    """生活活动的按名清单同样说明真实受理上限，而非未封顶的目录典型时长。"""
    engine = engine_factory()
    item = replace(engine.lifestyle_items[0], typical_duration_minutes=2000)
    agent = engine.state.agents[1]
    context = replace(engine._layer_context(agent, (), []), lifestyle_items=(item,))
    assert _facts_seconds(context, {"object_id": item.activity_id}, {"canonical_action": "do"}) == 86_400
    assert _lifestyle_rows(context, (item,))[0]["note"] == duration_phrase(86_400)
    monkeypatch.setattr(engine, "lifestyle_items", (item,))
    monkeypatch.setattr(engine, "_start", lambda state, pending, params: params)
    accepted = engine._start_lifestyle_action(engine.state, SimpleNamespace(agent_id=1), item.activity_id)
    assert accepted.planned_duration_seconds == 86_400


@pytest.mark.parametrize("seconds", [14_400, 14_401])
def test_consume_duration_limit_matches_listing_and_real_admission(engine_factory, monkeypatch, seconds):
    """食用四小时在真实受理中开始，超过一秒则由工具预查与实际派发共同拒绝。"""
    from worldmock.life.tool_layer import _consume_check

    engine = engine_factory()
    original = next(item for item in engine.scenario.products if "eat" in item.use_modes)
    product = original.model_copy(update={"use_duration_seconds": seconds, "required_location_id": None})
    monkeypatch.setitem(engine.rules.items, product.product_id, product)
    state = engine.state.model_copy(deep=True)
    agent = state.agents[1]
    agent.inventory = {agent.location_id: {product.product_id: 1.0}}
    context = replace(engine._layer_context(agent, (), []), state=state)
    binding = {"object_id": product.product_id}
    action = {"use_mode": "eat", "label": "食用"}
    pending = SimpleNamespace(agent_id=1, action_id="shared-consume", decision_id="shared-decision",
                              requested_event_id="shared-request")
    listed = _consume_check(context, binding, action)
    if seconds == 14_401:
        assert listed is not None
        assert listed.code == "ACTIVITY_DURATION_INVALID"
        with pytest.raises(BusinessRuleError, match="ACTIVITY_DURATION_INVALID"):
            engine._start_consume_action(state, pending, [product.product_id], [{"selected_action": action}])
        assert not state.activities
        assert agent.inventory[agent.location_id][product.product_id] == 1.0
        return
    assert listed is None
    changed, result = engine._start_consume_action(state, pending, [product.product_id], [{"selected_action": action}])
    activity = changed.activities[result.activity_id]
    assert result.status == "in_progress"
    assert activity.kind == ActivityKind.CONSUME
    assert activity.planned_duration_seconds == 14_400
    assert activity.data["request"]["duration_seconds"] == 14_400
    assert activity.data["opened"] == {product.product_id: 1.0}


def test_declared_sleep_channel_does_not_control_body_sleep_settlement(engine_factory, monkeypatch):
    """注册表睡眠通道改为清醒声明后，真实睡眠仍恢复不足账和睡眠压力，且不产生娱乐分录。"""
    from worldmock.catalog import affordances
    from worldmock.life import activities, body
    from worldmock.life.body import ActivityFactors, ENTERTAINMENT_RESOURCE, last_flows, settle_body, settle_shared_body
    from worldmock.life.models import BodyState

    engine = engine_factory()
    registry = affordances.default_affordances()
    changed_registry = replace(registry, kinds={**registry.kinds,
                                                "sleep": replace(registry.kinds["sleep"], sleep_channel="awake")})
    monkeypatch.setattr(affordances, "default_affordances", lambda: changed_registry)
    monkeypatch.setattr(body, "default_affordances", lambda: changed_registry)
    monkeypatch.setattr(activities, "_AFFORDANCES", changed_registry)
    config = engine.scenario.body
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


def test_travel_plan_matches_tool_layer_admission_and_engine_validation(engine_factory, monkeypatch):
    """冻结路线选择的变化必须一起进入报价、逐项受理与引擎复查，错误时长仍明确拒绝。"""
    from worldmock.life.travel import route_for

    engine = engine_factory()
    agent = engine.state.agents[1]
    monkeypatch.setattr("worldmock.life.travel.route_for", lambda *args: route_for(*args).model_copy(
        update={"duration_seconds": 600}))
    state = engine.state.model_copy(update={"current_time": engine.state.current_time + 8 * 3600})
    context = replace(engine._layer_context(agent, (), []), state=state)
    listed_route, listed_seconds = _route(context, "office", "driving")
    assert listed_seconds == engine.semantics.travel_planned_seconds("driving", 600, state.current_time) > 600
    monkeypatch.setattr(engine, "_start", lambda state, pending, params: params)
    accepted = engine._start_travel_action(state, SimpleNamespace(agent_id=1), "office",
                                           {"selected_action": {"travel_mode": "driving"}})
    assert accepted.planned_duration_seconds == listed_seconds
    assert engine._travel_start_context(state, agent, accepted)[0] == listed_route
    wrong = accepted.model_copy(update={"planned_duration_seconds": listed_seconds + 1})
    with pytest.raises(BusinessRuleError, match="ROUTE_DURATION_MISMATCH"):
        engine._travel_start_context(state, agent, wrong)


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.5, "1"])
def test_invalid_exposure_evidence_fails_both_preview_and_settlement(count, caplog):
    """缺失、布尔、非整数和非正次数均拒绝，预估不能默认一次或改算清醒累积。"""
    evidence = {"entertainment_relief_per_hour": 0.3}
    if count is not None:
        evidence["exposure_count"] = count
    activity = _running(ActivityKind.WATCH, "movie-0001", 1800, evidence)
    agent, body = _agent(), _body()
    context = _context(agent=agent, body=body, taste_weights=agent.taste_weights)
    for operation in (lambda: activity_factors(activity, agent, body),
                      lambda: _entertainment_delta(context, activity, 0.5)):
        with pytest.raises(ValueError, match="watch activity entertainment evidence is incomplete"):
            operation()
    assert sum("Watch activity entertainment evidence" in record.message for record in caplog.records) == 2


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), -0.1, True])
def test_invalid_content_rate_fails_both_preview_and_settlement(rate):
    """非有限、负值与布尔费率不能成为实际缓解或预估缓解。"""
    activity = _running(ActivityKind.WATCH, "movie-0001", 1800,
                        {"entertainment_relief_per_hour": rate, "exposure_count": 1})
    agent, body = _agent(), _body()
    context = _context(agent=agent, body=body, taste_weights=agent.taste_weights)
    with pytest.raises(ValueError):
        activity_factors(activity, agent, body)
    with pytest.raises(ValueError):
        _entertainment_delta(context, activity, 0.5)


@pytest.mark.parametrize("count", [1, 2, 9])
def test_valid_content_rate_matches_preview_and_settlement(count):
    """接触次数、偏好下限与同一费率在原预估和结算入口中同值。"""
    activity = _running(ActivityKind.WATCH, "movie-0001", 1800,
                        {"entertainment_relief_per_hour": 0.3, "exposure_count": count})
    agent, body = _agent(preferences={"movie-0001": 0}), _body()
    context = _context(agent=agent, body=body, taste_weights=agent.taste_weights)
    factors = activity_factors(activity, agent, body)
    assert factors.entertainment_relief_per_hour == pytest.approx(0.15 / count)
    assert _entertainment_delta(context, activity, 0.5) == pytest.approx(-0.075 / count)


def test_work_refusal_matches_listing_and_admission(engine_factory):
    """截止前不足一分钟的开工拒绝由唯一规则供原工具层与受理层消费。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    state = engine.state.model_copy(update={"current_time": int(task.due_at.timestamp()) - 59})
    context = replace(engine._layer_context(state.agents[1], (), []), state=state)
    expected = work_start_refusal(task, 0, state.current_time)
    assert _work_check(context, {"object_id": task.task_id}, {}) == expected
    with pytest.raises(BusinessRuleError) as caught:
        engine._targeted_duration(state, 1, ActivityKind.WORK, task.task_id)
    assert caught.value.public_reason == expected.reason
    assert caught.value.suggested["next_step"] == expected.next_step
    activity = _running(ActivityKind.WORK, task.task_id, 3600, {}).model_copy(
        update={"status": ActivityStatus.PAUSED, "location_id": state.agents[1].location_id})
    resume = Context(state, 1, {}, tasks={task.task_id: task})
    assert _report_refusal(resume, activity) == expected


def test_registry_met_key_is_shared_by_preview_and_settlement(monkeypatch):
    """注册表声明改用另一代谢键时，两条原入口必须一起使用新键，而非拼写旧键。"""
    from worldmock.life import activities, motivation

    original = activities._AFFORDANCES
    registry = SimpleNamespace(met_lookup_key=lambda *args, **kwargs: "declared:watch",
                               relief_channel=original.relief_channel)
    monkeypatch.setattr(activities, "_AFFORDANCES", registry)
    monkeypatch.setattr(motivation, "default_affordances", lambda: registry)
    activity = _running(ActivityKind.WATCH, "movie-0001", 1800,
                        {"entertainment_relief_per_hour": 0.3, "exposure_count": 1})
    agent = _agent()
    body = _body().model_copy(update={"activity_met": {"declared:watch": 1.75}})
    context = _context(agent=agent, body=body, taste_weights=agent.taste_weights)
    assert activity_factors(activity, agent, body).met == 1.75
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

    engine = engine_factory()
    agent = engine.state.agents[1]
    message = engine.scenario.messages[0]
    assert mail_headers(agent, engine.rules.messages) == engine._event_tool_support(agent, ())["messages"] == []
    delivered = agent.model_copy(update={"visible_message_ids": {message.message_id}}, deep=True)
    expected = [{"message_id": message.message_id, "thread_id": message.thread_id,
                 "subject": message.subject, "read": False}]
    assert mail_headers(delivered, engine.rules.messages) == expected
    assert engine._event_tool_support(delivered, ())["messages"] == expected
    read, revealed = reveal_mail(SimpleNamespace(target_id=message.message_id), delivered, engine.rules.messages)
    expected[0].update(read=True, body=message.body)
    assert revealed["body"] == message.body
    assert mail_headers(read, engine.rules.messages) == expected
    assert engine._event_tool_support(read, ())["messages"] == expected


def test_contract_templates_match_scenario_loading_and_resource_mapping(engine_factory, monkeypatch):
    """合同模板身份统一变化时，真实加载准入、合同匹配与身体效率参数必须一起消费新对象。"""
    from worldmock.life import semantics
    from worldmock.life.category_contract import CONTRACT_OBJECT_IDS

    engine = engine_factory()
    objects = dict(engine.semantics.catalog.objects)
    for kind, object_id in tuple(CONTRACT_OBJECT_IDS.items()):
        original = objects.pop(object_id)
        shared_id = f"shared-{object_id}"
        objects[shared_id] = original.model_copy(update={
            "object_id": shared_id,
            "attributes": {**original.attributes, "work_notes_multiplier": 1.75}})
        monkeypatch.setitem(CONTRACT_OBJECT_IDS, kind, shared_id)
    catalog = replace(engine.semantics.catalog, objects=objects)
    monkeypatch.setattr(semantics, "load_catalog", lambda _path: catalog)
    root = Path(__file__).resolve().parents[2]
    loaded, _ = semantics.load_runtime_scenario(root / "scenarios/white-collar-12-weeks.json", root / "catalog")
    referenced = {item.object_id for item in loaded.refs.objects}
    assert {CONTRACT_OBJECT_IDS[task.contract_kind] for task in loaded.scenario.tasks} <= referenced
    assert not {"contract-hourly", "contract-piece", "contract-package"} & referenced
    assert loaded.body.work_notes_multiplier == 1.75
    for kind, object_id in CONTRACT_OBJECT_IDS.items():
        match = loaded.contract_match(SimpleNamespace(contract_kind=kind))
        assert match.category_id == objects[object_id].category_id


@pytest.mark.parametrize("candidate_kind,activity_kind", [("product", "consume"), ("lifestyle", "lifestyle"),
                                                        ("content", "watch"), ("game", "play_game"), ("task", "work")])
def test_candidate_kind_mapping_drives_real_generation_and_resource_filter(candidate_kind, activity_kind, monkeypatch):
    """真实三段候选流程按唯一种类映射读取活动占用，只有占用相应资源的候选被排除。"""
    from worldmock.life import candidates

    kinds = ("product", "lifestyle", "content", "game", "task")
    entries = tuple(candidates.CandidateEntry(f"object-{kind}", kind) for kind in kinds)
    registry = SimpleNamespace(resource_claims=lambda kind, **_kwargs: frozenset({f"activity:{kind}"}))
    monkeypatch.setattr(candidates, "default_affordances", lambda: registry)
    inputs = candidates.CandidateInputs(
        location=None, locations={}, sources={"on_site": entries}, require_location_instance=False,
        local_hour=None, current_time=100, window_end_time=1000,
        occupied_resources=frozenset({f"activity:{activity_kind}"}))
    page = candidates.recall(inputs)
    assert {row.entry.kind for row in page.generated} == set(kinds)
    assert page.source_counts["on_site"] == 5
    assert [(row.kind, row.reason) for row in page.excluded] == [(candidate_kind, "ELIGIBILITY_RESOURCE_CONFLICT")]
    assert {row.kind for row in page.retained} == set(kinds) - {candidate_kind}
