"""真实观察的环境侧七层数据与私有证据（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章）。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    advance_next, advance_until_ready, context, start_activity, stage_day_plan, submit_round, target_actions,
    target_tools, tool_targets, wake_by_mail, start_request)
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (
    ActivityState,
    ActivityStatus,
)
from worldmock.life.readable import duration_phrase, iso_time
from worldmock.life.schedule import DayPlan, PlanGoal, day_end_seconds
from worldmock.life.todo_layer import todo_layer
from worldmock.life.tool_layer import LayerContext

#: 发给人物模型的环境侧层。
_LAYERS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


def _commit_test_state(engine, state, kind: str) -> None:
    """提交一个明确测试场景状态并同步当前引擎。"""
    state.version = engine.context.state.version + 1
    event = EventDraft(
        event_type="environment_event",
        occurred_at=state.current_time,
        visibility="system",
        source_kind="scenario",
        data={"kind": kind},
    )
    engine.context.store.commit(state.model_dump(mode="json"), [event])
    engine.context.state = state


def _rendered(observation: dict) -> str:
    """环境侧各层的紧凑 JSON（检查不进提示的字段）。"""
    return json.dumps([observation[key] for key in _LAYERS], ensure_ascii=False)






def test_observation_keeps_current_time_without_plan_conversion_anchors(engine_factory) -> None:
    """环境层写带时区的观察时刻；七层不带原始秒数与只用于时段换算的锚点，私有证据保留当前 epoch。"""
    engine = engine_factory()
    first_context = context(engine, "epoch-disclosure")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    now = observation["current_time"]
    assert observation["environment_layer"]["observed_at"] == iso_time(now, engine.context.scenario.timezone)
    rendered = _rendered(observation)
    assert str(now) not in rendered
    assert all(anchor not in rendered for anchor in ("day_start_epoch", "day_end_epoch", "bedtime_epoch",
                                                     "current_epoch"))


def test_observation_keeps_full_goal_text_in_the_todo_layer(engine_factory) -> None:
    """八个最长的目标文字完整进入待办层，按计划顺序，各带是否已推进与提醒时刻（没有提醒为 null）。"""
    from worldmock.life.schedule import INTENT_KINDS

    engine = engine_factory()
    now = engine.context.state.current_time
    state = engine.context.state.model_copy(deep=True)
    goals = tuple(PlanGoal(intent_kind=kind, goal="目" * 300, registered_at=now)
                  for kind in INTENT_KINDS[:8])
    state.agents[1].day_plan = DayPlan(day_end=day_end_seconds(now, engine.context.scenario.timezone), goals=goals)
    _commit_test_state(engine, state, "test_full_goals")
    ctx = context(engine, "full-goals")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    assert observation["todo_layer"]["day_plan"]["goals"] == [
        {"intent_kind": kind, "goal": "目" * 300, "progressed": False, "remind_time": None, "around_time": None}
        for kind in INTENT_KINDS[:8]]
    assert _rendered(observation).count("目" * 300) == 8


def _todo_context(engine, agent, now: int) -> LayerContext:
    """@brief 用实际引擎的已登记目录与人物构造边界时刻的只读待办视图。"""
    state = engine.context.state.model_copy(deep=True)
    state.current_time = now
    return replace(engine.observations._layer_context(agent, (), []), state=state)


def test_goal_projection_survives_bedtime_and_expires_at_local_midnight(engine_factory) -> None:
    """今日计划在就寝后仍在待办层（目标带场景本地「HH:MM」提醒时刻，不设提醒的为 null），恰到本地次日零点才为 null。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    zone = engine.context.scenario.timezone
    day_end = day_end_seconds(now, zone)
    agent = engine.context.state.agents[1]
    agent.day_plan = DayPlan(day_end=day_end, goals=())
    assert todo_layer(_todo_context(engine, agent, day_end - 1), engine.context.store)["day_plan"] == {
        "date": datetime.fromtimestamp(day_end - 1, ZoneInfo(zone)).date().isoformat(), "goals": []}
    assert todo_layer(_todo_context(engine, agent, day_end), engine.context.store)["day_plan"] is None
    agent.day_plan = DayPlan(day_end=day_end, goals=(
        PlanGoal(intent_kind="睡眠", goal="十一点前睡下", registered_at=now, remind_at=day_end - 3600),
        PlanGoal(intent_kind="休息", goal="睡前歇一会儿", registered_at=now)))
    goals = todo_layer(_todo_context(engine, agent, day_end - 1), engine.context.store)["day_plan"]["goals"]
    assert [(row["intent_kind"], row["remind_time"]) for row in goals] == [("睡眠", "23:00"), ("休息", None)]
    agent.day_plan = None
    assert todo_layer(_todo_context(engine, agent, day_end - 1), engine.context.store)["day_plan"] is None












@pytest.mark.parametrize("kind", ["work", "drive", "app"])
def test_real_observation_during_active_activity_keeps_decision_open(engine_factory, kind: str) -> None:
    """工作、驾车、浏览进行中被唤醒时观察照常：进行中的活动是工具层的活动目标（三个控制工具），「自己」的等待照常列出，
    每个列出的目标都有名称，决策可以继续（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。"""
    engine = engine_factory()
    activity_id = _start_active_activity(engine, kind)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert engine.context.state.activities[activity_id].status == ActivityStatus.ACTIVE
    round_id = f"open-during-{kind}"
    observed_context = context(engine, round_id)
    engine.begin_decision_round([observed_context])
    observation = engine.observe(observed_context)
    active = [row for row in observation["tool_layer"]["targets"] if row["kind"] == "activity"]
    assert [row["phase"] for row in active] == ["进行中"]
    assert [row["tool"] for row in active[0]["tools"]] == ["report_status", "pause_activity", "conclude_activity"]
    assert "等待" in target_actions(observation, "自己")
    assert all(row["name"] for row in observation["tool_layer"]["targets"])
    engine.abort_decision_round(round_id, "test teardown")


def test_motivation_context_wires_enabled_set_and_recent_meals(engine_factory) -> None:
    """动机上下文三接线：启用集直通声明、近 24h 进食计数、口味权重空映射不抬升。"""
    from tests.life.test_motivation import CONTRACT_PROFILE
    engine = engine_factory(mutate=lambda document: document["agents"][0].update({"motivation": CONTRACT_PROFILE}))
    agent = engine.context.state.agents[1]
    config = next(item for item in engine.context.scenario.agents if item.agent_id == 1)
    profile = config.motivation
    assert profile is not None
    now = engine.context.state.current_time

    def consumed_activity(key: str, started_at: int, mode: str, consumed: dict) -> ActivityState:
        """构造已结算的食用活动，不把模型请求当作已吃下的事实。"""
        return ActivityState(
            activity_id=key, agent_id=1, location_id=engine.context.state.agents[1].location_id,
            kind="consume", status="completed",
            started_at=started_at, ended_at=started_at + 600, planned_end_at=started_at + 600,
            last_settled_at=started_at + 600, elapsed_seconds=600, planned_duration_seconds=600,
            origin_action_id=key, origin_decision_id=key, origin_request_event_id=key,
            data={"request": {"use_mode": mode}, "consumed": consumed})

    engine.context.state.activities = {
        "fresh": consumed_activity("fresh", now - 600, "eat", {"food-a": 2, "food-b": 1}),
        "other": consumed_activity("other", now - 600, "use", {"food-c": 1}),
    }

    wired = engine.observations._motivation_context(agent, profile)
    assert wired.recent_meal_counts == {"food-a": 2, "food-b": 1}
    assert wired.lifestyle_enabled == config.lifestyle_activities
    assert not hasattr(wired, "taste_weights")
    # 边界内的实际结算计入；窗口外和其他人物的活动不计。
    from worldmock.life.observe import _recent_meal_counts
    stale = consumed_activity("stale", now - 86_401, "eat", {"food-stale": 3})
    boundary = consumed_activity("boundary", now - 86_400, "eat", {"food-edge": 1})
    assert _recent_meal_counts([stale, boundary], 1, now) == {"food-edge": 1}


def test_observation_people_facts_and_contacts_identity_layers(engine_factory) -> None:
    """在场的人与联系人都是工具层的 person 目标：同场人物名「人物：<姓名>」，状态写在做什么；联系人「联系人：<称谓>」；
    身份映射只在私有绑定，他人的身体、余额与记忆不进任何一层。"""
    engine = engine_factory("multi-person.json")
    data = engine.observations._observation_data(engine.context.state.agents[1])
    other = engine.context.state.agents[2]
    person = tool_targets(data)[f"人物：{other.name}"]
    assert (person["kind"], person["state"]) == ("person", "没在做什么")
    contacts = [row for row in data["tool_layer"]["targets"] if row["name"].startswith("联系人：")]
    config = next(item for item in engine.context.scenario.agents if item.agent_id == 1)
    assert [row["name"] for row in contacts] == [f"联系人：{item.salutation}" for item in config.contacts]
    assert all(row["kind"] == "contact" for row in contacts)
    assert {row["object_id"] for row in data["interaction_bindings"] if row["kind"] in {"person", "contact"}}
    text = _rendered(data)
    assert '"person_id"' not in text and '"contact_id"' not in text and "agent:" not in text
    for forbidden in ("energy_kcal", "balance_fen", "memory", "sleep_pressure", "entertainment_preferences"):
        assert forbidden not in text


def test_holding_targets_show_product_display_names(engine_factory) -> None:
    """手边持有物是工具层的「持有物：<目录展示名>」目标，不出现商品标识。"""
    engine = engine_factory()
    held = next(item for item in engine.context.scenario.foods if item.category.value == "meal")
    data = engine.observations._observation_data(engine.context.state.agents[1])
    assert f"持有物：{held.name}" not in tool_targets(data)
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"inventory": {staged.agents[1].location_id: {held.food_id: 1.0}}}, deep=True)
    engine.context.state = staged
    data = engine.observations._observation_data(engine.context.state.agents[1])
    assert f"持有物：{held.name}" in tool_targets(data)
    assert held.food_id not in json.dumps(data["tool_layer"], ensure_ascii=False)


def _start_active_activity(engine, kind: str) -> str:
    """经真实请求开始工作、出行或应用使用，不读旧材料、路线时段或浏览夹具。"""
    from tests.life.test_work_protocol import at_workplace
    stage_day_plan(engine)
    if kind == "work":
        at_workplace(engine)
        topic = engine.context.scenario.work_topics[0]
        request = start_activity("工作", f"工作：{topic.topic}")
    elif kind == "drive":
        place = next(item.name for item in engine.context.scenario.locations if item.location_id == "office")
        request = start_activity("驾车", f"地点：{place}")
    else:
        app = next(item.name for item in engine.context.scenario.apps if item.app_id == "douyin")
        request = start_activity("打开", f"应用：{app}")
    _, results = submit_round(engine, {1: ("start_activity", request)}, f"active-{kind}")
    assert results[0].status == "in_progress"
    return results[0].activity_id


def test_real_observation_fixed_menu_purchase_keeps_food_available(engine_factory) -> None:
    """真实固定菜单不因买过一份消失；到店地点资格和远程配送仍由同一受理入口判定。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed_context = context(engine, "fixed-menu-purchase")
    engine.begin_decision_round([observed_context])
    observation = engine.observe(observed_context)
    binding = next(row for row in observation["interaction_bindings"] if row["kind"] == "supply"
                   and row["store_id"] == "noodle-1")
    ways = target_actions(observation, binding["object"])
    assert not ways["到店"]["available"] and ways["远程下单"]["available"]
    engine.submit_action(DirectToolCall(observed_context, "action-fixed-menu", "start_activity",
                                      start_activity("远程下单", binding["object"])))
    result = engine.commit_decision_round(observed_context.round_id)[0]
    assert result.status == "completed"
    refreshed = engine.observe(context(engine, "fixed-menu-refresh"))
    assert target_actions(refreshed, binding["object"])["远程下单"]["available"]
    assert "current_stock" not in json.dumps(refreshed)


def test_real_observation_topic_tail_work_uses_shared_minute_refusal(engine_factory) -> None:
    """真实非失败活动累计后余下五十九秒，工作目标和受理返回同一原因与下一步。"""
    engine = engine_factory()
    from tests.life.test_work_protocol import at_workplace
    at_workplace(engine)
    topic = engine.context.scenario.work_topics[0]
    state = engine.context.state.model_copy(deep=True)
    now = state.current_time
    worked = topic.target_seconds - 59
    activity = ActivityState(activity_id="completed-topic-tail", agent_id=1, location_id="office", kind="work",
        status="completed", started_at=now-worked, ended_at=now, planned_end_at=now,
        last_settled_at=now, concluded_at=now, elapsed_seconds=worked, planned_duration_seconds=worked,
        target_id=topic.topic_id, resource_claims={"work"}, origin_action_id="tail-action",
        origin_decision_id="tail-decision", origin_request_event_id="tail-request",
        data={"topic_id": topic.topic_id, "worked_before_seconds": 0})
    state.activities[activity.activity_id] = activity
    _commit_test_state(engine, state, "test_work_topic_tail")
    decision = context(engine, "topic-tail")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    target = f"工作：{topic.topic}"
    reason = "Less than one minute of the topic remains; work cannot start."
    assert target_actions(observed, target)["工作"]["reason"] == reason
    engine.submit_action(DirectToolCall(decision, "tail-start", "start_activity", start_activity("工作", target)))
    result = engine.commit_decision_round(decision.round_id)[0]
    assert (result.error_code, result.data["reason"], result.data["next_step"]) == (
        "ACTIVITY_DURATION_INVALID", reason, "Choose another activity.")


def test_real_observation_uses_registered_app_and_content_names(engine_factory) -> None:
    """私有证据来自唯一登记目录，旧媒体位置、游戏目录和工作材料不再出现。"""
    engine = engine_factory()
    data = engine.observe(context(engine, "registered-names"))
    assert data["contents"] == [{"content_id": item.content_id, "title": item.title, "kind": item.kind.value}
                                for item in engine.context.semantics.catalog.contents.values()]
    assert {row["app_id"] for row in data["apps"]} == set(engine.context.semantics.catalog.apps)
    assert not {"content_progress", "game_catalog", "tasks", "discovery_catalog"} & data.keys()
