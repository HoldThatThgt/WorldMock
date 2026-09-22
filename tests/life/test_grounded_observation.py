"""真实生活观察、动态原生工具 Schema 与领域结算的闭环测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_next, context, wake_by_mail, stage_day_plan
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (
    ActivityState,
    ContentProgress,
    PurchaseOrderRequest,
    StartActivityRequest,
)
from worldmock.life.narration import render_facts, render_scene
from worldmock.life.schedule import DayPlan, PlanGoal, day_end_seconds


def _commit_test_state(engine, state, kind: str) -> None:
    """提交一个明确测试场景状态并同步当前引擎。"""
    state.version = engine.state.version + 1
    event = EventDraft(
        event_type="environment_event",
        occurred_at=state.current_time,
        visibility="system",
        source_kind="scenario",
        data={"kind": kind},
    )
    engine.store.commit(state.model_dump(mode="json"), [event])
    engine.state = state


def _prepare_agent(observation: dict, observed_at: datetime) -> LifeAgent:
    """按正式入口用一个真实环境观察准备 LifeAgent。"""
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": observation}]
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, observed_at, wrapped))
    return agent


def _function_names(agent: LifeAgent) -> set[str]:
    """返回本轮实际发送给模型的原生函数名。"""
    return {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}


def _stage_open_single_stock(engine) -> str:
    """让一个公开商品处于营业时段且只剩一件。"""
    supply = engine.scenario.supplies[0]
    product_id = supply.product_id
    local = datetime.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    now = int(local.replace(hour=12, minute=0, second=0).timestamp())
    state = engine.state.model_copy(deep=True)
    state.current_time = now
    state.pending_events = [item for item in state.pending_events if item.due_at > now]
    state.supply_stock[supply.supply_id] = 1
    agent = state.agents[1]
    inventory = {"office": {product_id: 0.0}, "home": {product_id: 1.0}}
    state.agents[1] = agent.model_copy(
        update={"visible_supply_ids": {supply.supply_id}, "supply_browsed_at": {supply.supply_id: now},
                "supply_browsed_location": {supply.supply_id: "office"}, "location_id": "office",
                "inventory": inventory, "next_decision_at": now, "body_settled_at": now}, deep=True)
    _commit_test_state(engine, state, "test_single_stock")
    return supply.supply_id


def test_real_observation_schema_purchase_and_sold_out_refresh(engine_factory) -> None:
    """真实观察形成可购买闭集，结算最后库存后下一轮撤下购买工具。"""
    engine = engine_factory()
    stage_day_plan(engine)
    supply_id = _stage_open_single_stock(engine)
    product_id = next(item.product_id for item in engine.scenario.supplies if item.supply_id == supply_id)
    first_context = context(engine, "purchase-grounded")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    agent = _prepare_agent(observation, first_context.observed_at)
    supply = next(item for item in observation["supplies"] if item["supply_id"] == supply_id)
    payload = {
        "items": [{"supply_id": supply_id, "quantity": 1,
                   "price_version": supply["price_version"]}],
        "fulfillment": "delivery",
        "destination_id": "home",
    }
    decision = ReactDecision("", "purchase", payload, "")

    assert supply["current_stock"] == 1 and "initial_stock" not in supply
    assert observation["available_inventory"][product_id] == 0
    assert "consume" not in _function_names(agent)
    assert agent.validate_react_decisions([decision], False) == ""

    request = PurchaseOrderRequest.model_validate(payload)
    engine.submit_action(DirectToolCall(
        first_context, "action-purchase-grounded", "purchase", request))
    result = engine.commit_decision_round("purchase-grounded")[0]
    assert result.status == "completed"
    assert engine.state.supply_stock[supply_id] == 0

    # 下一决策钉在浏览可见期内（+900 < 1800 秒，docs/dev/SPEC-life-runtime.md「观察投影行为规则」）：售罄商品仍在
    # 可见目录中报告 current_stock=0，购买工具按库存闭集撤下。
    wake_by_mail(engine, agent_id=1, offset=900)
    advance_next(engine)
    next_context = context(engine, "purchase-sold-out")
    engine.begin_decision_round([next_context])
    refreshed = engine.observe(next_context)
    next_agent = _prepare_agent(refreshed, next_context.observed_at)
    sold_out = next(item for item in refreshed["supplies"] if item["supply_id"] == supply_id)

    assert sold_out["current_stock"] == 0 and "initial_stock" not in sold_out
    assert "purchase" not in _function_names(next_agent)


def test_observation_keeps_current_epoch_without_plan_conversion_anchors(engine_factory) -> None:
    """真实观察保留当前时间，删除只用于时段换算的所有锚点。"""
    engine = engine_factory()
    first_context = context(engine, "epoch-disclosure")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    now = observation["current_time"]
    local = datetime.fromtimestamp(now, ZoneInfo(engine.scenario.timezone))
    removed = {"day_start_epoch", "day_end_epoch", "bedtime_epoch"}
    for layer in ("scene", "facts"):
        assert observation[layer]["current_time"] == local.isoformat()
        assert observation[layer]["current_epoch"] == now
        assert removed.isdisjoint(observation[layer])
    assert f"Unix epoch：当前 {now}）" in render_scene(observation["scene"])
    assert f'"current_epoch":{now}' in render_facts(observation["facts"])


def test_observation_keeps_full_goal_text_without_exceeding_scene_budget(engine_factory) -> None:
    """最大目标文字完整进入事实，场景只显示有序意图和推进状态。"""
    from worldmock.life.schedule import INTENT_KINDS
    from worldmock.life.narration import count_hanzi, SCENE_HANZI_BUDGET

    engine = engine_factory()
    now = engine.state.current_time
    state = engine.state.model_copy(deep=True)
    goals = tuple(PlanGoal(intent_kind=kind, goal="目" * 300, registered_at=now)
                  for kind in INTENT_KINDS[:8])
    state.agents[1].day_plan = DayPlan(day_end=day_end_seconds(now, engine.scenario.timezone), goals=goals)
    _commit_test_state(engine, state, "test_full_goals")
    ctx = context(engine, "full-goals")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    assert observation["facts"]["day_plan"] == state.agents[1].day_plan.model_dump(mode="json")
    assert all(item["goal"] == "目" * 300 for item in observation["facts"]["day_plan"]["goals"])
    assert observation["scene"]["day_plan"] == {"goals": [
        {"intent_kind": kind, "progressed": False} for kind in INTENT_KINDS[:8]]}
    assert count_hanzi(render_scene(observation["scene"])) <= SCENE_HANZI_BUDGET
    _prepare_agent(observation, ctx.observed_at)


def test_goal_projection_survives_bedtime_and_expires_at_local_midnight(engine_factory) -> None:
    """清单在就寝后仍显示，恰到本地次日零点才失效。"""
    from worldmock.life.projection import _plan_fact, _plan_row

    engine = engine_factory()
    now = engine.state.current_time
    day_end = day_end_seconds(now, engine.scenario.timezone)
    agent = engine.state.agents[1]
    agent.day_plan = DayPlan(day_end=day_end, goals=())
    assert _plan_fact(agent, day_end - 1) == {"day_end": day_end, "goals": []}
    assert _plan_row(agent, day_end - 1) == {"goals": []}
    assert _plan_fact(agent, day_end) is None
    assert _plan_row(agent, day_end) is None


def _stage_progress_limits(engine) -> None:
    """形成工作合同剩余五十九秒和全部内容媒体剩余九秒的真实状态。"""
    task = engine.scenario.tasks[0]
    now = int(task.available_at.timestamp())
    worked = task.maximum_duration_seconds - 59
    state = engine.state.model_copy(deep=True)
    state.current_time = now
    state.pending_events = [item for item in state.pending_events if item.due_at > now]
    agent = state.agents[1]
    drained = {
        content.item_id: ContentProgress(
            item_id=content.item_id,
            position_seconds=content.duration_seconds - 9,
            exposure_count=1,
        )
        for content in engine.scenario.contents
    }
    visible = set(agent.visible_task_ids)
    visible.add(task.task_id)
    state.agents[1] = agent.model_copy(
        update={"location_id": task.required_location_id, "visible_task_ids": visible,
                "content_progress": drained, "next_decision_at": now, "body_settled_at": now},
        deep=True,
    )
    activity = ActivityState(
        activity_id="completed-work-limit",
        agent_id=1,
        kind="work",
        status="completed",
        started_at=now - worked,
        ended_at=now,
        planned_end_at=now,
        last_settled_at=now,
        elapsed_seconds=worked,
        planned_duration_seconds=worked,
        effective_work_seconds=task.required_effective_seconds,
        target_id=task.task_id,
        resource_claims={"position", "attention", "device", "hands"},
        origin_action_id="work-limit-action",
        origin_decision_id="work-limit-decision",
        origin_request_event_id="work-limit-request",
    )
    state.activities[activity.activity_id] = activity
    _commit_test_state(engine, state, "test_progress_limits")


def test_real_observation_hides_impossible_media_and_work_tail_starts(engine_factory) -> None:
    """环境公开同一结算进度，Schema 撤下尾段启动但保留已完成工作提交。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_progress_limits(engine)
    observed_context = context(engine, "progress-limits")
    engine.begin_decision_round([observed_context])
    observation = engine.observe(observed_context)
    agent = _prepare_agent(observation, observed_context.observed_at)
    task = observation["tasks"][0]
    content_id = engine.scenario.contents[0].item_id
    progress = observation["content_progress"][content_id]
    schemas = {
        item["function"]["name"]: item["function"]
        for item in agent.build_react_tool_schemas(readonly=False)
    }
    kinds = schemas["start_activity"]["parameters"]["properties"]["activity_type"]["enum"]

    assert task["remaining_contract_seconds"] == 59
    assert task["worked_seconds"] == engine.scenario.tasks[0].maximum_duration_seconds - 59
    assert task["effective_seconds"] == engine.scenario.tasks[0].required_effective_seconds
    assert task["material"]["end_char"] == task["material"]["total_chars"]
    assert progress["remaining_media_seconds"] == 9
    assert "work" not in kinds and "watch" not in kinds
    assert "submit_work" in schemas
    assert set(schemas["submit_work"]["parameters"]["properties"]) == {"task_id", "deliverable"}


def _stage_titled_progress(engine) -> None:
    """形成电影、字幕体验与新闻流三条已开始内容的真实进度状态。"""
    state = engine.state.model_copy(deep=True)
    agent = state.agents[1]
    staged = {
        "movie-0001": ContentProgress(item_id="movie-0001", position_seconds=600, exposure_count=1),
        "sintel-en-subtitles": ContentProgress(
            item_id="sintel-en-subtitles", position_seconds=300, exposure_count=1),
        "news-feed": ContentProgress(item_id="news-feed", position_seconds=120, exposure_count=2),
    }
    state.agents[1] = agent.model_copy(update={"content_progress": staged}, deep=True)
    _commit_test_state(engine, state, "test_content_progress_titles")


def test_real_observation_content_progress_titles(engine_factory) -> None:
    """已开始内容条目携带语料展示标题；feed 条目不写 title 键。"""
    engine = engine_factory()
    _stage_titled_progress(engine)
    observed_context = context(engine, "content-progress-titles")
    engine.begin_decision_round([observed_context])
    observation = engine.observe(observed_context)
    progress = observation["content_progress"]
    movies = json.loads(Path("corpora/movies/items.json").read_text(encoding="utf-8"))
    movie_title = next(row["title"] for row in movies if row["item_id"] == "movie-0001")
    identity = json.loads(Path("corpora/sintel/media-identity.json").read_text(encoding="utf-8"))

    assert progress["movie-0001"]["title"] == movie_title
    assert progress["sintel-en-subtitles"]["title"] == identity["title"]
    assert set(progress["movie-0001"]) == {
        "item_id", "position_seconds", "exposure_count",
        "remaining_media_seconds", "title"}
    assert "title" not in progress["news-feed"]
    assert set(progress["news-feed"]) == {
        "item_id", "position_seconds", "exposure_count", "remaining_media_seconds"}


def _events_of(engine, event_type: str) -> list:
    """读取足迹库中某类型的全部事件。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == event_type]


def _decision_statement_params() -> dict:
    """决策自述阶段的模型请求参数形状（无 tools、温度 1.0、max_tokens 8000、reasoning 上限）。"""
    return {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "此刻感受"}],
            "stream": False, "temperature": 1.0, "max_tokens": 8000, "max_retries": 0,
            "extra_body": {"thinking": {"type": "disabled"},
                           "reasoning": {"max_tokens": 1500}}}


def test_decision_started_written_once_complete_with_decision_statement(engine_factory) -> None:
    """决策自述可得时才写 decision_started：一次写完整（含 decision_statement）且不可再更新。"""
    from agentsociety2.agent.base import ModelCallEvidence
    from worldmock.footprints.models import DECISION_STATEMENT_FIELD

    engine = engine_factory()
    ctx = context(engine, "decision_statement-round")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    params = _decision_statement_params()
    engine.record_model_exchange(ModelCallEvidence(ctx, "call-1", "request", 1, params))
    assert _events_of(engine, "decision_started") == []

    engine.record_model_exchange(ModelCallEvidence(
        ctx, "call-1", "response", 1, params,
        response={"choices": [{"message": {"content": "有点饿，先吃点东西再继续工作。"}}]}))
    starts = _events_of(engine, "decision_started")
    assert len(starts) == 1
    data = starts[0].data
    assert data[DECISION_STATEMENT_FIELD] == "有点饿，先吃点东西再继续工作。"
    assert data["round_id"] == "decision_statement-round" and data["round_order"] == 0
    assert data["world_state_version"] == engine.state.version
    assert data["wake_reasons"] == ["run_start"]
    assert starts[0].agent_id == 1 and starts[0].decision_id == ctx.decision_id
    stored = engine.store.connection.execute(
        "SELECT body FROM events WHERE event_id=?", (starts[0].event_id,),
    ).fetchone()[0]
    assert json.loads(stored)["data"]["decision_statement"] == data[DECISION_STATEMENT_FIELD]
    with pytest.raises(sqlite3.IntegrityError, match="Events are immutable"):
        engine.store.connection.execute(
            "UPDATE events SET body=? WHERE event_id=?", ("{}", starts[0].event_id),
        )

    # 自述已经持久化，登记动作不重复创建或修改证据。
    engine.submit_action(DirectToolCall(
        ctx, "action-decision_statement", "start_activity",
        StartActivityRequest(activity_type="wait", planned_duration_seconds=900)))
    engine.commit_decision_round("decision_statement-round")
    assert len(_events_of(engine, "decision_started")) == 1
    assert engine.store.connection.execute(
        "SELECT body FROM events WHERE event_id=?", (starts[0].event_id,),
    ).fetchone()[0] == stored


def test_invalid_decision_statement_is_absent_from_direct_engine_evidence(engine_factory) -> None:
    """空文本带错误证据不写自述；独立的引擎直驱登记只写无自述事件。"""
    from agentsociety2.agent.base import ModelCallEvidence
    from worldmock.footprints.models import DECISION_STATEMENT_FIELD

    engine = engine_factory()
    ctx = context(engine, "decision_statement-invalid")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    params = _decision_statement_params()
    engine.record_model_exchange(ModelCallEvidence(ctx, "call-1", "request", 1, params))
    engine.record_model_exchange(ModelCallEvidence(
        ctx, "call-1", "response", 1, params,
        response={"choices": [{"message": {"content": ""}}]},
        error="decision_statement is empty"))
    # 行动阶段形状（带 tools）的交换也不是决策自述，不触发落库。
    engine.record_model_exchange(ModelCallEvidence(
        ctx, "call-2", "request", 1, {**params, "tools": [], "tool_choice": "auto"}))
    engine.record_model_exchange(ModelCallEvidence(
        ctx, "call-2", "response", 1, {**params, "tools": [], "tool_choice": "auto"},
        response={"choices": [{"message": {"content": "工具调用"}}]}))
    assert _events_of(engine, "decision_started") == []

    engine.submit_action(DirectToolCall(
        ctx, "action-decision_statement-invalid", "start_activity",
        StartActivityRequest(activity_type="wait", planned_duration_seconds=900)))
    engine.commit_decision_round("decision_statement-invalid")
    starts = _events_of(engine, "decision_started")
    assert len(starts) == 1
    assert DECISION_STATEMENT_FIELD not in starts[0].data
    assert starts[0].data["round_id"] == "decision_statement-invalid"


def test_motivation_context_wires_enabled_set_and_recent_meals(engine_factory) -> None:
    """动机上下文三接线：启用集直通声明、近 24h 进食计数、口味权重空映射不抬升。"""
    engine = engine_factory()
    agent = engine.state.agents[1]
    config = next(item for item in engine.scenario.agents if item.agent_id == 1)
    profile = config.motivation
    assert profile is not None
    now = engine.state.current_time
    fresh = EventDraft(
        event_type="action_requested", occurred_at=now, agent_id=1,
        decision_id="decision-meal", action_id="action-meal", source_kind="model",
        data={"tool_name": "consume", "request": {
            "items": [{"product_id": "food-a", "quantity": 2},
                      {"product_id": "food-b", "quantity": 1}],
            "use_mode": "eat", "duration_seconds": 600},
            "request_sha256": "0" * 64, "reused": False})
    non_meal = EventDraft(
        event_type="action_requested", occurred_at=now, agent_id=1,
        decision_id="decision-use", action_id="action-use", source_kind="model",
        data={"tool_name": "consume", "request": {
            "items": [{"product_id": "food-c", "quantity": 1}],
            "use_mode": "use", "duration_seconds": 600},
            "request_sha256": "0" * 64, "reused": False})
    engine.store.commit(engine.state.model_dump(mode="json"), [fresh, non_meal])

    wired = engine._motivation_context(agent, profile)
    assert wired.recent_meal_counts == {"food-a": 2, "food-b": 1}
    assert wired.lifestyle_enabled == config.lifestyle_activities
    assert wired.taste_weights == {}
    # 反例（窗口外不计）：直接以 >24h 的合成事件核对聚合窗口边界。
    from types import SimpleNamespace

    from worldmock.life.observe import _recent_meal_counts

    stale = SimpleNamespace(occurred_at=now - 86_401, data={
        "tool_name": "consume", "request": {
            "items": [{"product_id": "food-stale", "quantity": 3}],
            "use_mode": "eat", "duration_seconds": 600}})
    boundary = SimpleNamespace(occurred_at=now - 86_400, data={
        "tool_name": "consume", "request": {
            "items": [{"product_id": "food-edge", "quantity": 1}],
            "use_mode": "eat", "duration_seconds": 600}})
    assert _recent_meal_counts([stale, boundary], now) == {"food-edge": 1}


def test_observation_people_facts_and_contacts_identity_layers(engine_factory) -> None:
    """facts 层携带不透明身份与联系人锚点；scene 层只有姓名，无身体/余额/任务。"""
    from worldmock.life.narration import render_facts

    engine = engine_factory("multi-person.json")
    data = engine._observation_data(engine.state.agents[1])
    people = data["facts"]["people_here"]
    assert people, "co-located agents must be visible"
    for row in people:
        assert set(row) == {"person_id", "name", "activity_family", "can_talk"}
        assert row["person_id"] == "agent:2"
    assert data["scene"]["people_here"] == [row["name"] for row in people]
    contacts = data["facts"]["contacts"]
    assert contacts and all(set(row) == {"contact_id", "salutation", "relation"}
                            for row in contacts)
    text = render_facts(data["facts"])
    assert '"person_id":"agent:2"' in text and '"contact_id"' in text
    # 他人隐私闭集（docs/dev/SPEC-life-runtime.md「社交最小可行版」）：身体数值/余额/记忆不进 facts 渲染文本
    # （tasks 是本人可见任务行，属 facts 合同字段，不在禁项之列）。
    for forbidden in ("energy_kcal", "balance_fen", "memory", "sleep_pressure",
                      "entertainment_preferences"):
        assert forbidden not in text


def test_scene_inventory_shows_product_display_names(engine_factory) -> None:
    """scene 手边持有物用目录展示名，不用商品标识（scene 合同是给人读的）。"""
    from worldmock.life.narration import render_scene

    engine = engine_factory()
    held = next(item for item in engine.scenario.products if item.name)
    data = engine._observation_data(engine.state.agents[1])
    # 未持有时先确认标识不出现在 scene，再持有后确认展示名出现。
    assert held.product_id not in data["scene"]["inventory"]
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"inventory": {staged.agents[1].location_id: {held.product_id: 1.0}}}, deep=True)
    engine.state = staged
    data = engine._observation_data(engine.state.agents[1])
    assert held.name in data["scene"]["inventory"]
    assert held.product_id not in data["scene"]["inventory"]
    scene = render_scene(dict(data["scene"]))
    assert held.name in scene and held.product_id not in scene
