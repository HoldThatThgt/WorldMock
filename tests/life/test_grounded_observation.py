"""真实生活观察、动态原生工具 Schema 与领域结算的闭环测试。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_next, context
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActivityState, ContentProgress, PurchaseRequest


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
    product = engine.scenario.products[0]
    merchant = next(
        item for item in engine.scenario.locations
        if item.location_id == product.merchant_location_id
    )
    local = datetime.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    now = int(local.replace(hour=merchant.open_hour, minute=0, second=0).timestamp())
    state = engine.state.model_copy(deep=True)
    state.current_time = now
    state.pending_events = [item for item in state.pending_events if item.due_at > now]
    state.merchant_stock[product.product_id] = 1
    agent = state.agents[1]
    visible = set(agent.visible_product_ids)
    visible.add(product.product_id)
    inventory = {"office": {product.product_id: 0.0}, "home": {product.product_id: 1.0}}
    state.agents[1] = agent.model_copy(
        update={"visible_product_ids": visible, "location_id": "office",
                "inventory": inventory, "next_decision_at": now},
        deep=True,
    )
    _commit_test_state(engine, state, "test_single_stock")
    return product.product_id


def test_real_observation_schema_purchase_and_sold_out_refresh(engine_factory) -> None:
    """真实观察形成可购买闭集，结算最后库存后下一轮撤下购买工具。"""
    engine = engine_factory()
    product_id = _stage_open_single_stock(engine)
    first_context = context(engine, "purchase-grounded")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    agent = _prepare_agent(observation, first_context.observed_at)
    product = next(item for item in observation["products"] if item["product_id"] == product_id)
    payload = {
        "product_id": product_id,
        "quantity": 1,
        "fulfillment": "delivery",
        "destination_id": "home",
        "price_version": product["price_version"],
    }
    decision = ReactDecision("", "purchase", payload, "")

    assert product["current_stock"] == 1 and "initial_stock" not in product
    assert observation["available_inventory"][product_id] == 0
    assert "consume" not in _function_names(agent)
    assert agent.validate_react_decisions([decision], False) == ""

    request = PurchaseRequest.model_validate(payload)
    engine.submit_action(DirectToolCall(
        first_context, "action-purchase-grounded", "purchase", request))
    result = engine.commit_decision_round("purchase-grounded")[0]
    assert result.status == "completed"
    assert engine.state.merchant_stock[product_id] == 0

    advance_next(engine)
    next_context = context(engine, "purchase-sold-out")
    engine.begin_decision_round([next_context])
    refreshed = engine.observe(next_context)
    next_agent = _prepare_agent(refreshed, next_context.observed_at)
    sold_out = next(item for item in refreshed["products"] if item["product_id"] == product_id)

    assert sold_out["current_stock"] == 0 and "initial_stock" not in sold_out
    assert "purchase" not in _function_names(next_agent)


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
                "content_progress": drained, "next_decision_at": now},
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
        origin_action_id="work-limit-action",
        origin_decision_id="work-limit-decision",
        origin_request_event_id="work-limit-request",
    )
    state.activities[activity.activity_id] = activity
    _commit_test_state(engine, state, "test_progress_limits")


def test_real_observation_hides_impossible_media_and_work_tail_starts(engine_factory) -> None:
    """环境公开同一结算进度，Schema 撤下尾段启动但保留已完成工作提交。"""
    engine = engine_factory()
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
    kinds = schemas["start_targeted_activity"]["parameters"]["properties"]["activity_type"]["enum"]

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
