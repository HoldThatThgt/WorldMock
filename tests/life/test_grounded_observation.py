"""真实观察的环境侧七层数据与私有证据（docs/dev/SPEC-persona-prompt.md 第一章）。"""

from __future__ import annotations

import json
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
    ContentProgress,
)
from worldmock.life.readable import duration_phrase, iso_time
from worldmock.life.schedule import DayPlan, PlanGoal, day_end_seconds
from worldmock.life.todo_layer import todo_layer
from worldmock.life.tool_layer import LayerContext

#: 发给人物模型的环境侧层。
_LAYERS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


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


def _rendered(observation: dict) -> str:
    """环境侧各层的紧凑 JSON（检查不进提示的字段）。"""
    return json.dumps([observation[key] for key in _LAYERS], ensure_ascii=False)


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
    """真实观察列出可购买的供给：人在办公室时到店标不可用（需要在店里），远程下单可用；结算最后一件后下一轮再没有可用的
    取得方式（docs/dev/SPEC-persona-prompt.md「工具层」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    supply_id = _stage_open_single_stock(engine)
    product_id = next(item.product_id for item in engine.scenario.supplies if item.supply_id == supply_id)
    first_context = context(engine, "purchase-grounded")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    supply = next(item for item in observation["supplies"] if item["supply_id"] == supply_id)
    binding = next(item for item in observation["interaction_bindings"]
                   if item["kind"] == "supply" and item["object_id"] == supply_id)
    ways = target_actions(observation, binding["object"])
    assert [(way, row["available"]) for way, row in ways.items()] == [("到店", False), ("远程下单", True)]
    store = next(item.name for item in engine.scenario.locations if item.location_id == supply["location_id"])
    assert ways["到店"]["reason"] == f"到店{binding['object']} requires presence at {store}."
    assert [(item["label"], item["note"]) for item in binding["actions"]] == [
        (way, row["reason"]) for way, row in ways.items()]
    assert supply["current_stock"] == 1 and "initial_stock" not in supply
    assert observation["available_inventory"][product_id] == 0
    request = start_request(targets=[binding["object"]], actions=["obtain"], ways=["远程下单"])
    engine.submit_action(DirectToolCall(first_context, "action-purchase-grounded", "start_activity", request))
    result = engine.commit_decision_round("purchase-grounded")[0]
    assert result.status == "completed"
    assert engine.state.supply_stock[supply_id] == 0
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "purchase-then-wait")
    wake_by_mail(engine, agent_id=1, offset=900)
    advance_next(engine)
    refreshed = engine.observe(context(engine, "purchase-sold-out"))
    sold_out = next(item for item in refreshed["supplies"] if item["supply_id"] == supply_id)
    assert sold_out["current_stock"] == 0 and "initial_stock" not in sold_out
    assert not [row for row in refreshed["tool_layer"]["targets"] if row["name"] == binding["object"]
                and any(action["available"] for action in row["actions"])]


def test_observation_keeps_current_time_without_plan_conversion_anchors(engine_factory) -> None:
    """环境层写带时区的观察时刻；七层不带原始秒数与只用于时段换算的锚点，私有证据保留当前 epoch。"""
    engine = engine_factory()
    first_context = context(engine, "epoch-disclosure")
    engine.begin_decision_round([first_context])
    observation = engine.observe(first_context)
    now = observation["current_time"]
    assert observation["environment_layer"]["observed_at"] == iso_time(now, engine.scenario.timezone)
    rendered = _rendered(observation)
    assert str(now) not in rendered
    assert all(anchor not in rendered for anchor in ("day_start_epoch", "day_end_epoch", "bedtime_epoch",
                                                     "current_epoch"))


def test_observation_keeps_full_goal_text_in_the_todo_layer(engine_factory) -> None:
    """八个最长的目标文字完整进入待办层，按计划顺序，各带是否已推进与提醒时刻（没有提醒为 null）。"""
    from worldmock.life.schedule import INTENT_KINDS

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
    assert observation["todo_layer"]["day_plan"]["goals"] == [
        {"intent_kind": kind, "goal": "目" * 300, "progressed": False, "remind_time": None, "around_time": None}
        for kind in INTENT_KINDS[:8]]
    assert _rendered(observation).count("目" * 300) == 8


def _todo_context(agent, now: int, zone: str) -> LayerContext:
    """只有人物与计划的待办层上下文。"""
    state = SimpleNamespace(agents={1: agent}, activities={}, current_time=now)
    return LayerContext(state=state, scenario=SimpleNamespace(timezone=zone, locations=()), rules=None,
                        semantics=None, agent_id=1, names={"sessions": {}, "activities": {}}, bindings=[],
                        menu=None)


def test_goal_projection_survives_bedtime_and_expires_at_local_midnight(engine_factory) -> None:
    """今日计划在就寝后仍在待办层（目标带场景本地「HH:MM」提醒时刻，不设提醒的为 null），恰到本地次日零点才为 null。"""
    engine = engine_factory()
    now = engine.state.current_time
    zone = engine.scenario.timezone
    day_end = day_end_seconds(now, zone)
    agent = engine.state.agents[1]
    agent.day_plan = DayPlan(day_end=day_end, goals=())
    assert todo_layer(_todo_context(agent, day_end - 1, zone), engine.store)["day_plan"] == {
        "date": datetime.fromtimestamp(day_end - 1, ZoneInfo(zone)).date().isoformat(), "goals": []}
    assert todo_layer(_todo_context(agent, day_end, zone), engine.store)["day_plan"] is None
    agent.day_plan = DayPlan(day_end=day_end, goals=(
        PlanGoal(intent_kind="睡眠", goal="十一点前睡下", registered_at=now, remind_at=day_end - 3600),
        PlanGoal(intent_kind="休息", goal="睡前歇一会儿", registered_at=now)))
    goals = todo_layer(_todo_context(agent, day_end - 1, zone), engine.store)["day_plan"]["goals"]
    assert [(row["intent_kind"], row["remind_time"]) for row in goals] == [("睡眠", "23:00"), ("休息", None)]
    agent.day_plan = None
    assert todo_layer(_todo_context(agent, day_end - 1, zone), engine.store)["day_plan"] is None


def _stage_progress_limits(engine, tail: str = "contract") -> None:
    """形成工作合同剩余五十九秒和全部内容媒体剩余九秒的真实状态；tail 为 deadline 时另把时刻放在截止前五十九秒。"""
    task = engine.scenario.tasks[0]
    now = int(task.available_at.timestamp()) if tail == "contract" else int(task.due_at.timestamp()) - 59
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
        location_id=task.required_location_id,
        kind="work",
        status="completed",
        started_at=now - worked,
        ended_at=now,
        planned_end_at=now,
        last_settled_at=now,
        concluded_at=now,
        elapsed_seconds=worked,
        planned_duration_seconds=worked,
        effective_work_seconds=task.required_effective_seconds,
        target_id=task.task_id,
        resource_claims={"work", "idle"},
        origin_action_id="work-limit-action",
        origin_decision_id="work-limit-decision",
        origin_request_event_id="work-limit-request",
    )
    state.activities[activity.activity_id] = activity
    _commit_test_state(engine, state, "test_progress_limits")


@pytest.mark.parametrize(("tail", "note"), [
    ("contract", "Less than one minute remains in the contract; work cannot start."),
    ("deadline", "Less than one minute remains before the deadline; work cannot start."),
], ids=["contract", "deadline"])
def test_real_observation_lists_tail_media_as_restart_and_tail_work_with_note(
        engine_factory, tail: str, note: str) -> None:
    """不按人物状态剪裁（第五章、第八章「工作」「呈现」）：剩余不足 60 秒的内容列为「从头再看」并写全长；
    距截止或剩余合同不足 60 秒的任务照常列出工作动作并标不可用，选了以 ACTIVITY_DURATION_INVALID 返回同一原因与
    下一步；已达标且没在做的工作提交可用，合同写「有效工时已达标」。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_progress_limits(engine, tail)
    observed_context = context(engine, "progress-limits")
    engine.begin_decision_round([observed_context])
    observation = engine.observe(observed_context)
    task = observation["tasks"][0]
    assert task["remaining_contract_seconds"] == 59
    assert task["worked_seconds"] == engine.scenario.tasks[0].maximum_duration_seconds - 59
    assert task["effective_seconds"] == engine.scenario.tasks[0].required_effective_seconds
    assert task["material"]["end_char"] == task["material"]["total_chars"]
    assert {row["remaining_media_seconds"] for row in observation["content_progress"].values()} == {9}
    work = next(row for row in observation["interaction_bindings"]
                if row["kind"] == "work_task" and row["object_id"] == task["task_id"])
    assert target_actions(observation, work["object"])["工作"]["reason"] == note
    assert target_tools(observation, work["object"])["submit_work"]["available"] is True
    assert tool_targets(observation)[work["object"]]["contract"]["remaining"].startswith("有效工时已达标")
    contents = {item.item_id: item for item in engine.scenario.contents}
    listed = [row for row in observation["interaction_bindings"] if row["kind"] == "content"]
    assert listed
    for row in listed:
        item = contents[row["object_id"]]
        way = "从头再听" if item.kind == "podcast_episode" else "从头再看"
        (action,) = tool_targets(observation)[row["object"]]["actions"]
        assert (action["way"], action["duration"]) == (way, duration_phrase(min(item.duration_seconds, 86_400)))
        assert tool_targets(observation)[row["object"]]["state"].endswith("完了")
    engine.submit_action(DirectToolCall(observed_context, "action-progress-limits", "start_activity",
                                        start_activity("工作", work["object"])))
    result = engine.commit_decision_round("progress-limits")[0]
    assert result.status == "rejected" and result.error_code == "ACTIVITY_DURATION_INVALID"
    assert (result.data["reason"], result.data["next_step"]) == (note, "Submit the completed work.")
    assert not any(item.kind == "work" and item.status == "active" for item in engine.state.activities.values())


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
    assert progress["news-feed"]["title"] == "中文新闻流（1000 条标题+摘要）"
    assert set(progress["news-feed"]) == {
        "item_id", "position_seconds", "exposure_count", "remaining_media_seconds", "title"}


def _start_active_activity(engine, kind: str) -> str:
    """经真实 start_activity 入口开始工作、驾车或浏览，返回进行中活动身份。"""
    from tests.life.test_revision_contract import _act
    from tests.life.test_travel_time import _stage_peak, _start_driving
    from tests.life.test_work_review import _at_task_start

    if kind == "drive":
        _stage_peak(engine)
        stage_day_plan(engine)
        return _start_driving(engine, "active-drive")[1].activity_id
    if kind == "work":
        _at_task_start(engine)
        stage_day_plan(engine)
        task_id = engine.scenario.tasks[0].task_id
        observed = engine.observe(context(engine, "active-work-preview"))
        binding, action = next((row, action) for row in observed["interaction_bindings"]
                               for action in row["actions"]
                               if row["object_id"] == task_id and action["canonical_action"] == "work")
        request = start_request(targets=[binding["object"]], actions=[action["verb"]], ways=[action["label"]])
    else:
        stage_day_plan(engine)
        request = start_activity("浏览", "商品目录")
    return _act(engine, f"active-{kind}", "start_activity", request).activity_id


@pytest.mark.parametrize("kind", ["work", "drive", "browse"])
def test_real_observation_during_active_activity_keeps_decision_open(engine_factory, kind: str) -> None:
    """工作、驾车、浏览进行中被唤醒时观察照常：进行中的活动是工具层的活动目标（三个控制工具），「自己」的等待照常列出，
    每个列出的目标都有名称，决策可以继续（docs/dev/SPEC-persona-prompt.md「工具层」）。"""
    engine = engine_factory()
    activity_id = _start_active_activity(engine, kind)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert engine.state.activities[activity_id].status == ActivityStatus.ACTIVE
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
    engine = engine_factory()
    agent = engine.state.agents[1]
    config = next(item for item in engine.scenario.agents if item.agent_id == 1)
    profile = config.motivation
    assert profile is not None
    now = engine.state.current_time

    def consumed_activity(key: str, started_at: int, mode: str, consumed: dict) -> ActivityState:
        """构造已结算的食用活动，不把模型请求当作已吃下的事实。"""
        return ActivityState(
            activity_id=key, agent_id=1, location_id=engine.state.agents[1].location_id,
            kind="consume", status="completed",
            started_at=started_at, ended_at=started_at + 600, planned_end_at=started_at + 600,
            last_settled_at=started_at + 600, elapsed_seconds=600, planned_duration_seconds=600,
            origin_action_id=key, origin_decision_id=key, origin_request_event_id=key,
            data={"request": {"use_mode": mode}, "consumed": consumed})

    engine.state.activities = {
        "fresh": consumed_activity("fresh", now - 600, "eat", {"food-a": 2, "food-b": 1}),
        "other": consumed_activity("other", now - 600, "use", {"food-c": 1}),
    }

    wired = engine._motivation_context(agent, profile)
    assert wired.recent_meal_counts == {"food-a": 2, "food-b": 1}
    assert wired.lifestyle_enabled == config.lifestyle_activities
    assert wired.taste_weights == {}
    # 边界内的实际结算计入；窗口外和其他人物的活动不计。
    from worldmock.life.observe import _recent_meal_counts
    stale = consumed_activity("stale", now - 86_401, "eat", {"food-stale": 3})
    boundary = consumed_activity("boundary", now - 86_400, "eat", {"food-edge": 1})
    assert _recent_meal_counts([stale, boundary], 1, now) == {"food-edge": 1}


def test_observation_people_facts_and_contacts_identity_layers(engine_factory) -> None:
    """在场的人与联系人都是工具层的 person 目标：同场人物名「人物：<姓名>」，状态写在做什么；联系人「联系人：<称谓>」；
    身份映射只在私有绑定，他人的身体、余额与记忆不进任何一层。"""
    engine = engine_factory("multi-person.json")
    data = engine._observation_data(engine.state.agents[1])
    other = engine.state.agents[2]
    person = tool_targets(data)[f"人物：{other.name}"]
    assert (person["kind"], person["state"]) == ("person", "没在做什么")
    contacts = [row for row in data["tool_layer"]["targets"] if row["name"].startswith("联系人：")]
    config = next(item for item in engine.scenario.agents if item.agent_id == 1)
    assert [row["name"] for row in contacts] == [f"联系人：{item.salutation}" for item in config.contacts]
    assert all(row["kind"] == "person" for row in contacts)
    assert {row["object_id"] for row in data["interaction_bindings"] if row["kind"] in {"person", "contact"}}
    text = _rendered(data)
    assert '"person_id"' not in text and '"contact_id"' not in text and "agent:" not in text
    for forbidden in ("energy_kcal", "balance_fen", "memory", "sleep_pressure", "entertainment_preferences"):
        assert forbidden not in text


def test_holding_targets_show_product_display_names(engine_factory) -> None:
    """手边持有物是工具层的「持有物：<目录展示名>」目标，不出现商品标识。"""
    engine = engine_factory()
    held = next(item for item in engine.scenario.products if item.name)
    data = engine._observation_data(engine.state.agents[1])
    assert f"持有物：{held.name}" not in tool_targets(data)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"inventory": {staged.agents[1].location_id: {held.product_id: 1.0}}}, deep=True)
    engine.state = staged
    data = engine._observation_data(engine.state.agents[1])
    assert f"持有物：{held.name}" in tool_targets(data)
    assert held.product_id not in json.dumps(data["tool_layer"], ensure_ascii=False)
