"""@brief 组合活动经正式决策动作进入真实世界并各自结算的验收回归。"""

from __future__ import annotations

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, stage_day_plan, wake_by_mail
from tests.life.test_scheduling import _custom_engine
from worldmock.life.models import (
    ChangePlaybackSpeedRequest, ConsumeItem, ConsumeRequest, ContinueActivityRequest,
    SetActivityStateRequest, StartBasicActivityRequest, StartParallelActivitiesRequest,
    StartSocialActivityRequest, StartTargetedActivityRequest, StartTravelRequest,
)

SCENARIO = "white-collar-12-weeks.json"
FOOD = "food-6923644266066"
ITEM = "item-umbrella"
CONTENT = "news-feed"


def _engine(tmp_path, *, short_day: bool = False):
    """@brief 从正式场景建立有真实持有物、适合白天活动的隔离运行。"""
    def mutate(document: dict) -> None:
        """@brief 只设置测试所需的日期、持有物与截止边界。"""
        document["start_at"] = "2026-01-05T08:00:00+08:00"
        if short_day:
            document["end_at"] = "2026-01-05T08:02:00+08:00"
            for section in ("tasks", "messages", "payrolls", "bills", "price_changes",
                            "product_releases"):
                document[section] = []
        document["agents"][0]["initial_inventory"] = {"home": {FOOD: 2, ITEM: 1}}

    engine = _custom_engine(SCENARIO, mutate, tmp_path, run_id="parallel-acceptance")
    stage_day_plan(engine)
    return engine


def _action(engine, name: str, tool: str, request):
    """@brief 通过观察、登记与环境结算执行一个真实人物动作。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return engine.commit_decision_round(name)[0]


def _watch(seconds: int = 600) -> StartTargetedActivityRequest:
    """@brief 选择正式内容目录中足够长的真实播放对象。"""
    return StartTargetedActivityRequest(activity_type="watch", target_id=CONTENT,
                                        planned_duration_seconds=seconds, playback_speed=1)


def _walk(engine) -> StartTravelRequest:
    """@brief 按冻结路线与当前时刻生成真实步行时长。"""
    route = next(item for item in engine.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office"
                 and item.mode.value == "walking")
    duration = engine.semantics.travel_planned_seconds("walking", route.duration_seconds,
                                                        engine.state.current_time)
    return StartTravelRequest(destination_id="office", travel_mode="walking",
                              planned_duration_seconds=duration)


def _parallel(engine, name: str, *items):
    """@brief 真实提交组合工具并读取其独立活动身份。"""
    result = _action(engine, name, "start_parallel_activities",
                     StartParallelActivitiesRequest(activities=items))
    ids = [row["activity_id"] for row in result.data.get("activities", [])]
    return result, ids


def _until(engine, predicate) -> None:
    """@brief 只沿环境边界推进；需要人物决策时继续一个仍在执行的子活动。"""
    for index in range(128):
        if predicate():
            return
        if engine.scheduling_state().ready_agent_ids:
            active = next(item for item in engine.state.activities.values() if item.status == "active")
            result = _action(engine, f"continue-{index}", "continue_activity",
                             ContinueActivityRequest(activity_id=active.activity_id))
            assert result.status == "completed", result.error_code
        else:
            advance_next(engine)
    states = {identity: (activity.kind.value, activity.status.value, activity.elapsed_seconds)
              for identity, activity in engine.state.activities.items()}
    raise AssertionError(f"Activity boundary was not reached at {engine.state.current_time}: {states}")


def _terminal_events(engine, name: str):
    """@brief 查询组合原动作唯一终态事件，忽略起始的 in_progress 回执。"""
    return [event for event in engine.store.read_agent_events("action_result", 1)
            if event.action_id == f"action-{name}" and event.data["status"] != "in_progress"]


@pytest.mark.parametrize("partner", ["walk", "eat", "use", "wait", "lifestyle"])
def test_each_allowed_watch_partner_starts_through_atomic_tool(tmp_path, partner: str) -> None:
    """@brief 五种资源相容组合均经动作入口启动，且各有独立 ACTIVE 身份。"""
    engine = _engine(tmp_path)
    try:
        partners = {
            "walk": lambda: _walk(engine),
            "eat": lambda: ConsumeRequest(items=(ConsumeItem(product_id=FOOD, quantity=1),),
                                           use_mode="eat", duration_seconds=900),
            "use": lambda: ConsumeRequest(items=(ConsumeItem(product_id=ITEM, quantity=1),),
                                           use_mode="use", duration_seconds=300),
            "wait": lambda: StartBasicActivityRequest(activity_type="wait",
                                                       planned_duration_seconds=600),
            "lifestyle": lambda: StartBasicActivityRequest(activity_type="lifestyle:yoga",
                                                            planned_duration_seconds=600),
        }
        result, ids = _parallel(engine, f"allowed-{partner}", _watch(), partners[partner]())
        assert result.status == "in_progress", result.error_code
        assert result.activity_id is None and len(ids) == len(set(ids)) == 2
        assert all(engine.state.activities[identity].status == "active" for identity in ids)
        assert {engine.state.activities[identity].kind.value for identity in ids} == {
            "watch", "travel" if partner == "walk" else "consume" if partner in {"eat", "use"}
            else partner}
        assert engine.state.processed_actions[f"action-allowed-{partner}"].result["status"] == "in_progress"
    finally:
        engine.close()


@pytest.mark.parametrize("watch_seconds,first_kind", [(600, "watch"), (3000, "travel")])
def test_walking_and_playback_finish_in_either_order(tmp_path, watch_seconds: int,
                                                     first_kind: str) -> None:
    """@brief 先结束的子项不覆盖另一个，聚合只在两者终结后完成。"""
    engine = _engine(tmp_path)
    name = f"order-{first_kind}"
    try:
        result, ids = _parallel(engine, name, _watch(watch_seconds), _walk(engine))
        assert result.status == "in_progress"
        by_kind = {engine.state.activities[identity].kind.value: identity for identity in ids}
        _until(engine, lambda: engine.state.activities[by_kind[first_kind]].status == "completed")
        other_kind = "travel" if first_kind == "watch" else "watch"
        assert engine.state.activities[by_kind[other_kind]].status == "active"
        assert engine.state.processed_actions[f"action-{name}"].result["status"] == "in_progress"
        assert _terminal_events(engine, name) == []
        _until(engine, lambda: engine.state.activities[by_kind[other_kind]].status == "completed")
        assert engine.state.processed_actions[f"action-{name}"].result["status"] == "completed"
        assert len(_terminal_events(engine, name)) == 1
        assert engine.state.agents[1].location_id == "office"
        assert engine.state.agents[1].content_progress[CONTENT].position_seconds == watch_seconds
    finally:
        engine.close()


def test_simultaneous_completion_has_one_body_and_one_aggregate_terminal(tmp_path) -> None:
    """@brief 同秒双完成各留进度，仅结一段身体和一次组合终态。"""
    engine = _engine(tmp_path)
    try:
        started, ids = _parallel(engine, "same-end", _watch(120),
                                 StartBasicActivityRequest(activity_type="wait",
                                                           planned_duration_seconds=120))
        assert started.status == "in_progress"
        _until(engine, lambda: all(engine.state.activities[identity].status == "completed" for identity in ids))
        body = [event for event in engine.store.read_agent_events("environment_event", 1)
                if event.data.get("kind") == "body_settlement"]
        progress = [event for event in engine.store.read_agent_events("activity_progress", 1)
                    if event.activity_id in ids]
        assert len(body) == 1 and len(progress) == 2
        assert {event.data["body_settlement_event_id"] for event in progress} == {body[0].event_id}
        assert len(_terminal_events(engine, "same-end")) == 1
        assert engine.state.processed_actions["action-same-end"].result["status"] == "completed"
    finally:
        engine.close()


def test_pause_resume_speed_and_cancel_affect_only_named_activity(tmp_path) -> None:
    """@brief 播放暂停与变速不停止等待，取消等待不重置媒体位置。"""
    engine = _engine(tmp_path)
    try:
        started, ids = _parallel(engine, "controls", _watch(600),
                                 StartBasicActivityRequest(activity_type="wait",
                                                           planned_duration_seconds=600))
        assert started.status == "in_progress"
        by_kind = {engine.state.activities[identity].kind.value: identity for identity in ids}
        watch_id, wait_id = by_kind["watch"], by_kind["wait"]
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 60)
        before_wait = engine.state.activities[wait_id].elapsed_seconds
        before_media = engine.state.activities[watch_id].media_position_seconds
        paused = _action(engine, "pause-watch", "set_activity_state",
                         SetActivityStateRequest(activity_id=watch_id, operation="pause"))
        assert paused.status == "completed"
        assert engine.state.activities[wait_id].status == "active"
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 120)
        assert engine.state.activities[watch_id].media_position_seconds == before_media
        assert engine.state.activities[wait_id].elapsed_seconds > before_wait
        resumed = _action(engine, "resume-watch", "set_activity_state",
                          SetActivityStateRequest(activity_id=watch_id, operation="resume"))
        assert resumed.status == "completed"
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 180)
        changed = _action(engine, "speed-watch", "change_playback_speed",
                          ChangePlaybackSpeedRequest(activity_id=watch_id, playback_speed=2))
        assert changed.status == "completed"
        assert engine.state.activities[wait_id].status == "active"
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 240)
        assert engine.state.activities[watch_id].media_position_seconds > before_media + 60
        cancelled = _action(engine, "cancel-wait", "set_activity_state",
                            SetActivityStateRequest(activity_id=wait_id, operation="cancel"))
        assert cancelled.status == "completed"
        assert engine.state.activities[watch_id].status == "active"
        assert engine.state.processed_actions["action-controls"].result["status"] == "in_progress"
        _until(engine, lambda: engine.state.activities[watch_id].status == "completed")
        assert engine.state.processed_actions["action-controls"].result["status"] == "cancelled"
        assert len(_terminal_events(engine, "controls")) == 1
    finally:
        engine.close()


def test_travel_pause_resume_and_watch_cancel_are_independent(tmp_path) -> None:
    """@brief 暂停步行不暂停听取，取消播放不取消已恢复的路线。"""
    engine = _engine(tmp_path)
    try:
        started, ids = _parallel(engine, "reverse-controls", _watch(3000), _walk(engine))
        assert started.status == "in_progress"
        by_kind = {engine.state.activities[identity].kind.value: identity for identity in ids}
        watch_id, travel_id = by_kind["watch"], by_kind["travel"]
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 60)
        travel_elapsed = engine.state.activities[travel_id].elapsed_seconds
        media_before = engine.state.activities[watch_id].media_position_seconds
        paused = _action(engine, "pause-travel", "set_activity_state",
                         SetActivityStateRequest(activity_id=travel_id, operation="pause"))
        assert paused.status == "completed"
        assert engine.state.activities[watch_id].status == "active"
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 120)
        assert engine.state.activities[travel_id].elapsed_seconds == travel_elapsed
        assert engine.state.activities[watch_id].media_position_seconds > media_before
        resumed = _action(engine, "resume-travel", "set_activity_state",
                          SetActivityStateRequest(activity_id=travel_id, operation="resume"))
        assert resumed.status == "completed"
        assert engine.state.activities[travel_id].status == "active"
        wake_by_mail(engine, offset=60)
        _until(engine, lambda: engine.state.current_time == started.occurred_at + 180)
        assert engine.state.activities[travel_id].elapsed_seconds > travel_elapsed
        cancelled = _action(engine, "cancel-watch", "set_activity_state",
                            SetActivityStateRequest(activity_id=watch_id, operation="cancel"))
        assert cancelled.status == "completed"
        assert engine.state.activities[travel_id].status == "active"
        assert engine.state.processed_actions["action-reverse-controls"].result["status"] == "in_progress"
        _until(engine, lambda: engine.state.activities[travel_id].status == "completed")
        assert engine.state.agents[1].location_id == "office"
        assert engine.state.processed_actions["action-reverse-controls"].result["status"] == "cancelled"
        assert len(_terminal_events(engine, "reverse-controls")) == 1
    finally:
        engine.close()


def test_experiment_end_truncates_both_and_fails_group(tmp_path) -> None:
    """@brief 实验截止逐项截断，不把任何一项冒称完成。"""
    engine = _engine(tmp_path, short_day=True)
    try:
        result, ids = _parallel(engine, "cutoff", _watch(600),
                                StartBasicActivityRequest(activity_type="wait",
                                                          planned_duration_seconds=600))
        assert result.status == "in_progress"
        _until(engine, lambda: engine.state.current_time == engine.state.end_time)
        assert {engine.state.activities[identity].status.value for identity in ids} == {"truncated"}
        assert engine.state.processed_actions["action-cutoff"].result["status"] == "failed"
        assert len(_terminal_events(engine, "cutoff")) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("invalid", ["missing-inventory", "game-conflict", "social-conflict"])
def test_rejected_group_has_no_activity_money_game_or_invitation_effects(tmp_path, invalid: str) -> None:
    """@brief 暂存组拒绝后，活动、库存、资金、游戏和社交通知均不留下半成品。"""
    engine = _engine(tmp_path)
    try:
        if invalid == "missing-inventory":
            request = ConsumeRequest(items=(ConsumeItem(product_id=FOOD, quantity=3),),
                                     use_mode="eat", duration_seconds=900)
        elif invalid == "game-conflict":
            request = StartTargetedActivityRequest(activity_type="play_game",
                                                   target_id=engine.scenario.games[0].game_id,
                                                   planned_duration_seconds=600, playback_speed=1)
        else:
            request = StartSocialActivityRequest(kind="call", partner_id="contact-mother",
                                                 duration_minutes=10)
        before = engine.state.model_dump(mode="json")
        agent_before = engine.state.agents[1].model_copy(deep=True)
        events_before = engine.store.last_event_seq
        result, ids = _parallel(engine, f"reject-{invalid}", _watch(), request)
        assert result.status == "rejected" and ids == []
        assert engine.state.activities == {}
        after = engine.state.model_dump(mode="json")
        for field in ("accounts", "supply_stock", "deliveries", "pending_events"):
            assert after[field] == before[field]
        assert engine.state.agents[1].inventory == agent_before.inventory
        assert engine.state.agents[1].game_sessions == agent_before.game_sessions
        assert engine.state.agents[1].inbox == agent_before.inbox
        assert not any(event.event_type == "activity_progress" for event in
                       engine.store.read_events(engine.store.last_event_seq)
                       if event.event_seq > events_before)
    finally:
        engine.close()


def test_every_parallel_activity_progresses_its_matching_day_goal(tmp_path) -> None:
    """同一真实结算区间的播放和等待均推进对应目标，不能只处理首项进度。"""
    from tests.life.test_day_plan import goal
    from tests.life.test_revision_contract import _commit_staged
    from worldmock.life.schedule import DayPlan, day_end_seconds

    engine = _engine(tmp_path)
    try:
        now = engine.state.current_time
        staged = engine.state.model_copy(deep=True)
        staged.agents[1].day_plan = DayPlan(
            day_end=day_end_seconds(now, engine.scenario.timezone), goals=(
                goal("休息", "真实等待", registered=now - 1),
                goal("娱乐", "真实播放", registered=now - 1)))
        _commit_staged(engine, staged)
        result, identities = _parallel(engine, "two-goals", _watch(120),
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120))
        assert result.status == "in_progress" and len(identities) == 2
        advance_next(engine)
        by_kind = {engine.state.activities[identity].kind.value: identity for identity in identities}
        goals = {item.intent_kind: item for item in engine.state.agents[1].day_plan.goals}
        assert goals["娱乐"].progress_activity_id == by_kind["watch"]
        assert goals["休息"].progress_activity_id == by_kind["wait"]
        assert {item.progressed_at for item in goals.values()} == {engine.state.current_time}
    finally:
        engine.close()
