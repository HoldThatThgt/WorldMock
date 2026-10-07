"""地点会话与活动生命周期（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章、第一章受限轮中依赖名额与待收尾的三支）。

会话是一段地点停留或一段路，由系统随出发与到达切换；每人两个活动名额，进行中与待收尾的活动各占一个，暂停的不占；
活动一结束就成为待收尾，同一秒 conclude_activity，做别的以 ACTIVITY_CONCLUSION_REQUIRED 拒绝，收尾前时钟不推进。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worldmock.life.engine import LifeEngine

from tests.data_paths import ENVIRONMENT_ROOT
from dataclasses import replace

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.life.conftest import (advance_next, advance_until_ready, context, progress_calls, stage_day_plan,
                                 target_actions, target_tools, tool_targets, wake_by_mail, start_request,
                                 conclude_request, pause_request, report_request)
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import EventDraft
from tests.life.test_same_moment_decisions import _contacts
from worldmock.life import activity_control, sessions
from worldmock.life.readable import duration_phrase, iso_time
from worldmock.life.models import (ActivityRequestItem, ActivityStatus, RecallTaskSummaryRequest, SessionKind,
                                   SessionStatus, StartActivityRequest, active_activities)

_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"




def _round(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 单人物按真实顺序观察、登记并结算一轮，返回（本轮观察, 结果）。"""
    from agentsociety2.society import DirectToolCall
    decision = context(engine, name, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _pick(observed: dict, canonical: str, *, mode: str | None = None, kind: str | None = None,
          place: str | None = None) -> tuple[str, str, str]:
    """@brief 从本轮冻结观察取一个对象名、动词与方式。"""
    return next((row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
                if (kind is None or row["kind"] == kind) and (place is None or row["object_id"] == place)
                for action in row["actions"] if action["canonical_action"] == canonical
                and (mode is None or action.get("travel_mode") == mode))


def _preview(engine: LifeEngine, name: str) -> dict:
    """@brief 不开轮次地读取一次当前观察（只追加观察事件）。"""
    return engine.observe(context(engine, f"preview-{name}"))


def _start(engine: LifeEngine, name: str, *choices: tuple[str, str, str]):
    """@brief 以可读对象、动词与方式开始一到两项活动。"""
    request = start_request(targets=[item[0] for item in choices], actions=[item[1] for item in choices],
                                   ways=[item[2] for item in choices])
    return _round(engine, name, "start_activity", request)[1]


def _wait(engine: LifeEngine, name: str):
    """@brief 以「自己」开始一段等待。"""
    return _start(engine, name, ("自己", "interact", "等待"))


def _depart(engine: LifeEngine, name: str, *companions: tuple[str, str, str], destination: str = "office"):
    """@brief 步行前往目的地，可在同一请求中一并开始路上的其他活动。"""
    route = _pick(_preview(engine, name), "travel_to", mode="walking", place=destination)
    return _start(engine, name, route, *companions)


def _name(engine: LifeEngine, activity_id: str) -> str:
    """@brief 活动的可读名称（本文件的用例里同一轮没有重名）。"""
    return engine.context.state.activities[activity_id].name


def _conclude(engine: LifeEngine, name: str, *activity_ids: str, memories: list[list[dict]] | None = None):
    """@brief 一次结束点名的活动，每项写一句总结，记忆默认空。"""
    request = conclude_request(
        *(_name(engine, item) for item in activity_ids), summary="收个尾。",
        memories=memories or [[] for _ in activity_ids])
    return _round(engine, name, "conclude_activity", request)[1]


def _started_ids(result) -> list[str]:
    """@brief 开始结果里的活动身份（单项或两项）。"""
    if result.activity_id:
        return [result.activity_id]
    return [row["activity_id"] for row in result.data["activities"]]


def _until_ended(engine: LifeEngine, activity_id: str, name: str) -> None:
    """@brief 推进到活动结束；中途的决策机会以播报继续所有进行中活动。"""
    for step in range(96):
        if engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.context.state, 1)
            _round(engine, f"{name}-report-{step}", "report_status", report_request(
                *(item.name for item in running), summary="继续。"))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def _stage(engine: LifeEngine, mutate) -> None:
    """@brief 改造世界状态并经存储落盘（保持版本连续），用于构造边界情形。"""
    staged = engine.context.state.model_copy(deep=True)
    mutate(staged)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_location_sessions_staged"})])
    engine.context.state = staged


def _environment_kinds(engine: LifeEngine, kind: str) -> list:
    """@brief 本人按 data.kind 过滤的环境事件。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") == kind]


def test_run_start_opens_one_stay_session_per_agent(engine_factory) -> None:
    """运行开始时按初始地点开始首个停留会话；名称为「<地点>（<M月D日> HH:MM 起）」，会话不写事件。"""
    engine = engine_factory("multi-person.json")
    for agent_id, agent in engine.context.state.agents.items():
        session = sessions.current_session(engine.context.state, agent_id)
        assert session.kind == SessionKind.STAY and session.location_id == agent.location_id
        place = next(item.name for item in engine.context.scenario.locations if item.location_id == agent.location_id)
        assert session.name == f"{place}（{sessions.moment_label(engine.context.state.current_time, 'Asia/Shanghai')} 起）"
    assert not [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                if "session" in str(event.data.get("kind", ""))]


def test_departure_and_arrival_switch_sessions_and_end_road_activities(engine_factory) -> None:
    """出发切到路上会话，到达当秒出行完成、路上开始的活动以 route_ended 结束并成为待收尾，开始目的地的停留会话。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    home = sessions.current_session(engine.context.state, 1)
    content = _pick(_preview(engine, "app"), "use_app", kind="app")
    started = _depart(engine, "depart", content, destination="park")
    travel_id, watch_id = _started_ids(started)
    road = sessions.current_session(engine.context.state, 1)
    assert engine.context.state.sessions[home.session_id].status == SessionStatus.ENDED
    assert road.kind == SessionKind.TRANSIT and road.origin_id == "home" and road.destination_id == "park"
    assert road.name.startswith("去") and "的路上（" in road.name
    assert engine.context.state.activities[watch_id].session_ids == [road.session_id]
    _until_ended(engine, travel_id, "walk")
    arrived = engine.context.state.current_time
    travel, watch = engine.context.state.activities[travel_id], engine.context.state.activities[watch_id]
    assert travel.status == ActivityStatus.COMPLETED and travel.concluded_at is None
    assert (watch.status, watch.boundary_type, watch.interruption_reason) == (
        ActivityStatus.CANCELLED, "route_ended", "route_ended")
    assert watch.ended_at == arrived and watch.concluded_at is None
    office = sessions.current_session(engine.context.state, 1)
    assert office.kind == SessionKind.STAY and office.location_id == "park" and office.started_at == arrived
    assert engine.context.state.sessions[road.session_id].ended_at == arrived
    assert {"planned_end", "no_active_activity"} <= set(engine.context.state.agents[1].pending_wake_reasons)
    observed = _preview(engine, "arrived")
    ended = [row for row in observed["tool_layer"]["targets"] if row["kind"] == "activity"]
    assert [(row["name"], row["phase"]) for row in ended] == [(travel.name, "刚结束"), (watch.name, "刚结束")]
    assert ended[0]["state"].startswith("到达，") and ended[1]["state"].startswith("提前结束，")


def test_just_ended_requires_conclusion_in_the_same_second(engine_factory) -> None:
    """待收尾时做别的以 ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名；收尾前时钟不推进；收尾后释放名额再开始。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "first-wait")
    _until_ended(engine, waited.activity_id, "first-wait")
    now = engine.context.state.current_time
    assert engine.scheduling_state().ready_agent_ids == (1,)
    rejected = _wait(engine, "second-wait")
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert _name(engine, waited.activity_id) in rejected.message
    assert engine.context.state.current_time == now and engine.context.state.agents[1].next_decision_at == now
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    concluded = _conclude(engine, "conclude-wait", waited.activity_id)
    assert concluded.status == "completed"
    assert engine.context.state.activities[waited.activity_id].concluded_at == now
    assert engine.context.state.activities[waited.activity_id].status == ActivityStatus.COMPLETED
    assert _wait(engine, "third-wait").status == "in_progress"
    assert engine.context.state.current_time == now
    advance_next(engine)


def test_waking_with_the_sleep_awaiting_conclusion_offers_its_conclusion(engine_factory) -> None:
    """当日没有计划时一觉醒来：睡眠是工具层刚结束的活动目标，待办层的今日计划为 null（当日计划由零点后台任务生成，
    第四章）；结束它可用，别的动作都标「先给刚结束的<睡眠>收尾」，conclude_activity 点名这段睡眠并被受理
    （第七章「活动一结束就要结束它」）。"""
    from agentsociety2.society import DirectToolCall
    engine = engine_factory(mutate=_contacts)
    slept = _start(engine, "no-plan-sleep", ("自己", "sleep", "睡觉"))
    assert advance_until_ready(engine, limit=200)
    sleep = engine.context.state.activities[slept.activity_id]
    assert sleep.status == ActivityStatus.COMPLETED and sleep.concluded_at is None
    assert engine.context.state.agents[1].day_plan is None
    assert "wake_from_sleep" in engine.context.state.agents[1].pending_wake_reasons
    decision = context(engine, "wake-no-plan")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert observed["todo_layer"]["day_plan"] is None
    assert tool_targets(observed)[sleep.name]["phase"] == "刚结束"
    assert target_tools(observed, sleep.name)["conclude_activity"]["available"] is True
    assert target_actions(observed, "自己")["等待"]["reason"] == f"Conclude the finished activities {sleep.name} first."
    engine.submit_action(DirectToolCall(decision, "action-wake-no-plan", "conclude_activity", conclude_request(
        sleep.name, summary="睡醒了。", memories=[[]])))
    assert engine.commit_decision_round("wake-no-plan")[0].status == "completed"
    assert engine.context.state.activities[slept.activity_id].concluded_at == engine.context.state.current_time


def test_other_activity_keeps_running_while_the_ended_one_is_concluded(engine_factory) -> None:
    """边走边看时内容先结束：当秒只收尾内容，出行不受影响；收尾后时钟照常推进。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    content = _pick(_preview(engine, "short-content"), "use_app", kind="app")
    travel_id, watch_id = _started_ids(_depart(engine, "long-depart", content))

    def shorten(state) -> None:
        """@brief 让内容比路程先放完。"""
        state.activities[watch_id] = state.activities[watch_id].model_copy(update={
            "planned_end_at": state.current_time + 300, "planned_duration_seconds": 300}, deep=True)

    _stage(engine, shorten)
    while engine.context.state.activities[watch_id].status == ActivityStatus.ACTIVE:
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    assert engine.context.state.activities[watch_id].status == ActivityStatus.COMPLETED
    assert engine.context.state.activities[travel_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _conclude(engine, "conclude-content", watch_id).status == "completed"
    assert engine.context.state.activities[travel_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)


def _wake(engine: LifeEngine, offset: int = 60) -> int:
    """@brief 以一封到达邮件把下一次决策机会钉在 offset 秒后，推进到该机会并返回当时的时刻。"""
    wake_by_mail(engine, offset=offset)
    assert advance_until_ready(engine)
    return engine.context.state.current_time


def _two_parallel(engine: LifeEngine, name: str) -> list[str]:
    """@brief 同一请求开始等待与观看（占用互不冲突），两个名额都被进行中活动占着。"""
    content = _pick(_preview(engine, name), "use_app", kind="app")
    started = _start(engine, name, ("自己", "interact", "等待"), content)
    assert started.status == "in_progress", started.error_code
    return _started_ids(started)


def _lifecycle_events(engine: LifeEngine) -> list:
    """@brief 本人的活动生命周期记录（结束、暂停、恢复）。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") in {"activity_concluded", "activity_paused", "activity_resumed"}]


def test_departure_with_running_activity_is_rejected_naming_it(engine_factory) -> None:
    """名额未满而有进行中活动时选择出行：出行照常列出并带说明；选了以 ACTIVITY_PRECONDITION_UNMET 返回原因「需要先
    结束或暂停手上的事：<活动名>」与下一步；暂停后出发被受理，暂停的活动留在原地。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    watched = _start(engine, "home-watch", _pick(_preview(engine, "home-watch"), "use_app", kind="app"))
    _wake(engine)
    name = _name(engine, watched.activity_id)
    observed = _preview(engine, "listed-depart")
    office = _pick(observed, "travel_to", mode="walking", place="office")[0]
    walk = target_actions(observed, office)["步行"]
    assert (walk["available"], walk["reason"]) == (
        False, f"Conclude or pause the current activities {name} before departure.")
    rejected = _depart(engine, "blocked-depart")
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert rejected.data["unmet"] == ["activities_before_departure"]
    assert rejected.data["suggested"]["conclude_or_pause"] == [name]
    assert rejected.data["items"][0]["path"] == ["tool_calls", 0, "function", "arguments", "items", 0, "target"]
    assert rejected.data["items"][0]["written"] == office
    assert rejected.data["items"][0]["why"] == rejected.data["reason"]
    assert rejected.data["reason"] == f"Conclude or pause the current activities {name} before departure."
    assert rejected.data["next_step"] == f"Conclude or pause {name} first."
    assert name in rejected.message
    assert sessions.current_session(engine.context.state, 1).kind == SessionKind.STAY
    paused = _round(engine, "pause-before-leaving", "pause_activity", pause_request(name, summary="出门前先停下。"))[1]
    assert paused.status == "completed"
    assert _depart(engine, "depart-after-pause").status == "in_progress"
    activity = engine.context.state.activities[watched.activity_id]
    assert activity.status == ActivityStatus.PAUSED
    assert engine.context.state.sessions[activity.session_ids[-1]].kind == SessionKind.STAY
    assert sessions.current_session(engine.context.state, 1).kind == SessionKind.TRANSIT


def test_paused_activity_resumes_only_at_its_start_location(engine_factory) -> None:
    """在别处恢复以 ACTIVITY_PRECONDITION_UNMET 拒绝并点名开始地点；回到开始地点后归入那里的新停留会话并可恢复。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "wait-home")
    _wake(engine)
    name = _name(engine, waited.activity_id)
    _round(engine, "pause-home", "pause_activity", pause_request(name, summary="先停一下。"))
    travel = _depart(engine, "to-office")
    _until_ended(engine, travel.activity_id, "to-office")
    assert _conclude(engine, "arrive-office", travel.activity_id).status == "completed"
    elsewhere = _round(engine, "resume-elsewhere", "report_status", report_request(name, summary="想接着等。"))[1]
    home = next(item.name for item in engine.context.scenario.locations if item.location_id == "home")
    assert elsewhere.status == "rejected" and elsewhere.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert elsewhere.data["suggested"] == {"travel_to": home}
    assert (elsewhere.data["reason"], elsewhere.data["next_step"]) == (
        f"Resume {name} at its start location {home}; it can be concluded elsewhere.", f"Travel to {home}.")
    assert home in elsewhere.message and name in elsewhere.message
    assert engine.context.state.activities[waited.activity_id].status == ActivityStatus.PAUSED
    back = _depart(engine, "back-home", destination="home")
    _until_ended(engine, back.activity_id, "back-home")
    current = sessions.current_session(engine.context.state, 1)
    assert current.location_id == "home" and waited.activity_id in current.activity_ids
    assert engine.context.state.activities[waited.activity_id].session_ids[-1] == current.session_id
    assert _conclude(engine, "arrive-home", back.activity_id).status == "completed"
    resumed = _round(engine, "resume-home", "report_status", report_request(name, summary="回家接着等。"))[1]
    assert resumed.status == "completed"
    activity = engine.context.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.ACTIVE
    assert activity.report_anchor_at == engine.context.state.current_time
    assert [event.data["kind"] for event in _lifecycle_events(engine)
            if event.activity_id == waited.activity_id] == ["activity_paused", "activity_resumed"]


def test_paused_activity_can_be_concluded_elsewhere(engine_factory) -> None:
    """人在别处可以直接结束暂停的活动：取消并收尾。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "wait-then-leave")
    _wake(engine)
    _round(engine, "pause-then-leave", "pause_activity", pause_request(
        _name(engine, waited.activity_id), summary="先停。"))
    travel = _depart(engine, "leave")
    _until_ended(engine, travel.activity_id, "leave")
    assert _conclude(engine, "conclude-both", travel.activity_id, waited.activity_id).status == "completed"
    activity = engine.context.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.CANCELLED and activity.interruption_reason == "agent_cancelled"
    assert activity.concluded_at == engine.context.state.current_time
    assert _wait(engine, "slot-free").status == "in_progress"


def test_pausing_an_activity_on_the_road_is_unavailable(engine_factory) -> None:
    """路上的活动（出行本身与路上开始的活动）不能暂停：ACTIVITY_PAUSE_UNAVAILABLE 带原因与下一步（可以直接结束），
    活动照常进行。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    content = _pick(_preview(engine, "road-content"), "use_app", kind="app")
    travel_id, watch_id = _started_ids(_depart(engine, "road", content))
    _wake(engine)
    for index, activity_id in enumerate((travel_id, watch_id)):
        rejected = _round(engine, f"road-pause-{index}", "pause_activity", pause_request(
            _name(engine, activity_id), summary="停一下。"))[1]
        assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
        name = _name(engine, activity_id)
        assert name in rejected.message
        assert (rejected.data["reason"], rejected.data["next_step"]) == (
            f"The activity {name} cannot be paused while travelling.", f"Conclude {name}.")
        assert engine.context.state.activities[activity_id].status == ActivityStatus.ACTIVE


def test_ending_travel_midway_ends_road_activities_and_stays_at_origin(engine_factory) -> None:
    """途中结束出行：路上会话结束，路上开始的其他活动以 route_ended 成为待收尾，人仍在出发地并开始该地的停留会话。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    content = _pick(_preview(engine, "midway-content"), "use_app", kind="app")
    travel_id, watch_id = _started_ids(_depart(engine, "midway", content))
    road = sessions.current_session(engine.context.state, 1)
    now = _wake(engine)
    assert _conclude(engine, "stop-walking", travel_id).status == "completed"
    travel, watch = engine.context.state.activities[travel_id], engine.context.state.activities[watch_id]
    assert travel.status == ActivityStatus.CANCELLED and travel.concluded_at == now
    assert (watch.status, watch.boundary_type, watch.interruption_reason, watch.concluded_at) == (
        ActivityStatus.CANCELLED, "route_ended", "route_ended", None)
    assert watch.media_position_seconds > 0
    stay = sessions.current_session(engine.context.state, 1)
    assert engine.context.state.sessions[road.session_id].ended_at == now
    assert stay.kind == SessionKind.STAY and stay.location_id == "home" == engine.context.state.agents[1].location_id
    assert engine.scheduling_state().ready_agent_ids == (1,)
    snapshots = {event.activity_id: event.data["activity"] for event in _lifecycle_events(engine)}
    assert snapshots[travel_id]["concluded_at"] == now and snapshots[watch_id]["boundary_type"] == "route_ended"
    assert _conclude(engine, "conclude-content", watch_id).status == "completed"


def test_two_running_activities_block_a_third_until_one_is_paused(engine_factory) -> None:
    """两项进行中的活动占满两个名额时不能开始第三项：SESSION_LIMIT_EXCEEDED 点名两项，下一步写结束或暂停其中一项；
    暂停其中一项就让出名额（暂停的活动不占名额），第三项被受理，暂停的那项仍暂停（第七章「两个名额」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "full")
    _wake(engine)
    blocked = _wait(engine, "third")
    listed = f"{_name(engine, waited)}、{_name(engine, watched)}"
    assert blocked.status == "rejected" and blocked.error_code == "SESSION_LIMIT_EXCEEDED"
    assert blocked.message == blocked.data["reason"] == f"Both activity slots are occupied by {listed}."
    assert blocked.data["next_step"] == f"Conclude or pause one of {listed} first."
    _round(engine, "pause-wait", "pause_activity", pause_request(_name(engine, waited), summary="停。"))
    _wake(engine)
    assert _wait(engine, "third-after").status == "in_progress"
    assert engine.context.state.activities[waited].status == ActivityStatus.PAUSED
    assert len(active_activities(engine.context.state, 1)) == 2
    assert activity_control.free_slots(engine.context.state, 1) == 0


def test_two_item_start_with_one_free_slot_says_only_one_slot_is_left(engine_factory) -> None:
    """只剩一个名额（另一个被进行中的等待占着）却要同时开始两项：名额判定先于整组准入，以 SESSION_LIMIT_EXCEEDED 拒绝，
    原因说只剩一个名额并点名占着名额的活动，下一步是先结束或暂停它；暂停等待之后再开始两项（等待与观看）被受理，暂停的
    活动不占名额（第五章「并行准入」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "one-slot-wait")
    _wake(engine)
    name = _name(engine, waited.activity_id)
    observed = _preview(engine, "one-slot")
    contents = [(row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
                if row["kind"] == "app" for action in row["actions"] if action["canonical_action"] == "use_app"]
    assert len({item[0] for item in contents[:2]}) == 2
    blocked = _start(engine, "one-slot-two", *contents[:2])
    assert blocked.status == "rejected" and blocked.error_code == "SESSION_LIMIT_EXCEEDED"
    assert blocked.message == blocked.data["reason"] == (
        f"Only one activity slot remains because {name} occupies a slot.")
    assert blocked.data["next_step"] == f"Conclude or pause one of {name} first."
    _round(engine, "one-slot-pause", "pause_activity", pause_request(name, summary="先停。"))
    both = _start(engine, "one-slot-two-again", ("自己", "interact", "等待"), contents[0])
    assert both.status == "in_progress" and len(_started_ids(both)) == 2
    assert engine.context.state.activities[waited.activity_id].status == ActivityStatus.PAUSED


def test_travel_with_full_slots_is_rejected_for_slots_before_the_departure_check(engine_factory) -> None:
    """两项都在进行、名额已满时选择出行：名额判定在出发前判定之前，以 SESSION_LIMIT_EXCEEDED 拒绝，不带出发前的未满足
    条件（第五章判定顺序）。

    暂停等待之后它让出名额，观看仍在进行：出行照常列出并带出发前的说明（第五章「动作照常列出，做错了再说明」），经真实
    决策轮以 ACTIVITY_PRECONDITION_UNMET 拒绝，只点名进行中的观看，人仍停留在原地。
    """
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "running-pair")
    with pytest.raises(BusinessRuleError, match="^SESSION_LIMIT_EXCEEDED$") as raised:
        activity_control.require_start_admission(engine.context.state, 1, (["travel_to"], ["步行"]), {})
    assert raised.value.unmet == []
    _wake(engine)
    _round(engine, "pause-the-wait", "pause_activity", pause_request(_name(engine, waited), summary="先停。"))
    _wake(engine)
    observed = _preview(engine, "listed-depart-beside-paused")
    office = _pick(observed, "travel_to", mode="walking", place="office")[0]
    walk = target_actions(observed, office)["步行"]
    watching = _name(engine, watched)
    assert (walk["available"], walk["reason"]) == (
        False, f"Conclude or pause the current activities {watching} before departure.")
    rejected = _depart(engine, "depart-beside-paused")
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert rejected.data["reason"] == walk["reason"] and _name(engine, waited) not in rejected.message
    assert rejected.data["suggested"]["conclude_or_pause"] == [watching]
    assert sessions.current_session(engine.context.state, 1).kind == SessionKind.STAY
    assert engine.context.state.activities[watched].status == ActivityStatus.ACTIVE


@pytest.mark.parametrize("body", [
    {"targets": ["自己"], "actions": ["interact", "sleep"], "ways": ["等待", "睡觉"]},
    {"items": [{"target": "自己", "way": way, "from": None, "to": None} for way in ("等待", "睡觉")]},
    {"items": [{"target": target, "way": "等待", "from": None, "to": None}
               for target in ("自己", "商品目录", "内容：Sintel")]},
], ids=["removed-parallel-arrays", "duplicate-targets", "three-items"])
def test_start_request_shape_errors_are_action_request_invalid(engine_factory, body) -> None:
    """旧数组、逐项目标重复或超过两项直接违反当前模式；绕过宿主构造的坏模型仍由环境原子拒绝。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate({"answering": None, "prefer": None, **body})
    constructed = dict(body)
    if "items" in constructed:
        constructed["items"] = [ActivityRequestItem.model_validate(item) for item in constructed["items"]]
    request = StartActivityRequest.model_construct(answering=None, prefer=None, **constructed)
    before_accounts = dict(engine.context.state.accounts)
    rejected = _round(engine, "shape", "start_activity", request)[1]
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert all(set(item) == {"path", "written", "error_code", "allowed_values", "expected", "why", "candidates"}
               and item["error_code"] == "ACTION_REQUEST_INVALID" and item["expected"] and item["why"]
               and item["candidates"] == [] for item in rejected.data["items"])
    if "items" not in body:
        [item] = rejected.data["items"]
        assert item["path"] == ["tool_calls", 0, "function", "arguments", "items"]
        assert item["written"] is None and item["allowed_values"] == []
        assert item["why"] == "This required field was omitted."
        assert "This field is required" in item["expected"]
    else:
        [item] = rejected.data["items"]
        assert item["path"] == ["tool_calls", 0, "function", "arguments", "items"]
        assert item["written"] == request.model_dump(mode="json", by_alias=True)["items"]
        assert item["why"] == ("a target may repeat only with a different recipient" if len(body["items"]) == 2
                               else "items requires one or two activity objects; acceptance is atomic")
    assert rejected.data["fix"] == "Correct every reported parameter."
    assert engine.context.state.activities == {}
    assert engine.context.state.accounts == before_accounts
    assert _wait(engine, "after-shape").status == "in_progress"


def test_both_paused_redecide_same_second_until_one_resumes(engine_factory) -> None:
    """两项都暂停时同一秒再决策；结束其一后仍同秒再决策；恢复其一后时钟可推进。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, second = _two_parallel(engine, "both")
    now = _wake(engine)
    paused = _round(engine, "pause-both", "pause_activity", pause_request(
        _name(engine, first), _name(engine, second), summary=["都停一下。", "也停。"]))[1]
    assert paused.status == "completed"
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at == now and agent.pending_wake_reasons == ["no_active_activity"]
    assert _conclude(engine, "end-one", first).status == "completed"
    assert engine.context.state.agents[1].next_decision_at == now
    resumed = _round(engine, "resume-other", "report_status", report_request(_name(engine, second), summary="接着看。"))[1]
    assert resumed.status == "completed" and engine.context.state.agents[1].next_decision_at > now
    advance_next(engine)
    assert engine.context.state.current_time > now



def test_experiment_end_ends_every_session_without_conclusion(engine_factory) -> None:
    """实验截止时所有会话结束；进行中的活动按 truncated 处理，不要求收尾。"""
    from tests.life.test_export_integration import _stage_final_interval
    engine = engine_factory(mutate=_contacts)
    _stage_final_interval(engine, 600)
    stage_day_plan(engine)
    waited = _wait(engine, "wait-to-end")
    _until_ended(engine, waited.activity_id, "wait-to-end")
    assert engine.context.state.current_time == engine.context.state.end_time
    assert all(item.status == SessionStatus.ENDED and item.ended_at == engine.context.state.end_time
               for item in engine.context.state.sessions.values())
    activity = engine.context.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.TRUNCATED and activity.concluded_at is None
    assert engine.scheduling_state().ready_agent_ids == ()


def _final_export(engine: LifeEngine, directory: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """@brief 实验截止后在最终检查点导出，返回（导出的活动行, 最终检查点的活动快照）。"""
    from worldmock.footprints.export import export_run
    agent_ref = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "location_sessions_final_export"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, directory)
    assert manifest["experiment_completed"] is True
    lines = (directory / "activities.jsonl").read_text(encoding="utf-8").splitlines()
    return {row["activity_id"]: row for row in map(json.loads, lines)}, checkpoint.state["activities"]


@pytest.mark.parametrize("peer", ["running", "concluded"])
def test_experiment_end_export_matches_the_final_checkpoint_for_every_truncated_activity(
        engine_factory, tmp_path: Path, peer: str) -> None:
    """同一请求开始的两项中一项暂停，另一项仍在进行（或已由人物结束、另开了一段等待）时跑到实验截止：每项被截断的
    活动（含暂停的）写一条带截断快照、回链开始请求的 activity_truncated；同源两项的聚合终态不带快照，导出的
    activities.jsonl 仍逐行等于最终检查点（第八章「活动」）。"""
    from tests.life.test_export_integration import _stage_final_interval
    engine = engine_factory(mutate=_contacts)
    _stage_final_interval(engine, 600)
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "end-pair")
    _wake(engine)
    _round(engine, "end-pause", "pause_activity", pause_request(_name(engine, watched), summary="先停。"))
    running = waited
    if peer == "concluded":
        _wake(engine)
        assert _conclude(engine, "end-conclude", waited).status == "completed"
        running = _wait(engine, "end-wait-again").activity_id
    _until_ended(engine, running, "end-run")
    assert engine.context.state.current_time == engine.context.state.end_time
    truncated = sorted(key for key, item in engine.context.state.activities.items() if item.status == ActivityStatus.TRUNCATED)
    assert truncated == sorted({watched, running})
    records = _environment_kinds(engine, "activity_truncated")
    assert sorted(event.activity_id for event in records) == truncated
    for event in records:
        activity = engine.context.state.activities[event.activity_id]
        expected = activity.model_dump(mode="json")
        assert event.occurred_at == engine.context.state.end_time and event.data["activity_id"] == event.activity_id
        assert {key: event.data["activity"][key] for key in expected} == expected
        assert event.caused_by == [activity.origin_request_event_id]
    exported, final = _final_export(engine, tmp_path / "final-export")
    assert exported.keys() == final.keys()
    for activity_id, snapshot in final.items():
        row = {**exported[activity_id], "kind": exported[activity_id]["activity_type"]}
        assert {key: row[key] for key in snapshot} == snapshot
    assert (exported[watched]["status"], exported[running]["status"]) == ("truncated", "truncated")


def test_same_named_activities_get_ordinals_instead_of_failing(engine_factory) -> None:
    """同一分钟内先后开始的同名活动同轮可见时追加「（第二次）」，不抛错，名称仍能唯一解析。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first = _wait(engine, "same-1")
    _wake(engine, offset=30)
    _conclude(engine, "same-end", first.activity_id)
    second = _wait(engine, "same-2")
    assert _name(engine, first.activity_id) == _name(engine, second.activity_id)
    observed = _preview(engine, "same-names")
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    assert names[first.activity_id] == _name(engine, first.activity_id)
    assert names[second.activity_id] == f"{_name(engine, second.activity_id)}（第二次）"
    rows = [row["name"] for row in observed["tool_layer"]["targets"] if row["kind"] == "activity"]
    assert rows == [names[second.activity_id]]
    _wake(engine)
    reported = _round(engine, "report-second", "report_status", report_request(
        names[second.activity_id], summary="第二段也在等。"))[1]
    assert reported.status == "completed"
    assert engine.context.state.activities[second.activity_id].progress_note == "第二段也在等。"


def test_cross_day_duplicate_activity_names_get_ordinals(engine_factory) -> None:
    """跨日重名的活动不抛错：名称用绝对日期；仍然重名时（例如跨年同日）同轮追加序号，回看与播报都能唯一解析。会话名不在
    七层中出现，观察不绑定会话名（回看的名称只取七层中出现的名称）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "today")
    stay = sessions.current_session(engine.context.state, 1)
    earlier = engine.context.state.current_time - 365 * 86400

    def last_year(state) -> None:
        """@brief 补一段去年同日同刻、同名的已结束停留会话，其中一项同名等待已收尾。"""
        activity = state.activities[waited.activity_id].model_copy(update={
            "activity_id": "activity-last-year", "status": ActivityStatus.COMPLETED, "started_at": earlier,
            "last_settled_at": earlier + 60, "ended_at": earlier + 60, "planned_end_at": earlier + 60,
            "concluded_at": earlier + 60, "session_ids": ["session-last-year"]}, deep=True)
        state.activities[activity.activity_id] = activity
        state.sessions["session-last-year"] = stay.model_copy(update={
            "session_id": "session-last-year", "started_at": earlier, "ended_at": earlier + 60,
            "status": SessionStatus.ENDED, "activity_ids": [activity.activity_id]}, deep=True)

    _stage(engine, last_year)
    observed = _preview(engine, "duplicates")
    activities_by_id = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    assert "session_bindings" not in observed
    assert activities_by_id["activity-last-year"] == _name(engine, waited.activity_id)
    assert activities_by_id[waited.activity_id] == f"{_name(engine, waited.activity_id)}（第二次）"
    _wake(engine)
    recalled = _recall(engine, "recall-duplicate", activities_by_id[waited.activity_id])
    assert (recalled["kind"], recalled["phase"]) == ("activity", "进行中")
    _wake(engine)
    reported = _round(engine, "report-duplicate", "report_status", report_request(
        activities_by_id[waited.activity_id], summary="今天的这段还在等。"))[1]
    assert reported.status == "completed"
    assert engine.context.state.activities[waited.activity_id].progress_note == "今天的这段还在等。"
    assert not [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                if event.event_type == "run_failed"]


def test_self_report_stays_out_of_the_tool_layer_and_is_only_a_call_argument(engine_factory) -> None:
    """自述不当事实：播报的 summary 只作为进度层该调用的参数出现，工具层的活动目标只写客观进度，待办层与环境层也不复述
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「唯一出现原则」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "self-report")
    _wake(engine)
    claim = "我已经吃过晚饭了。"
    assert _round(engine, "claim", "report_status", report_request(
        _name(engine, waited.activity_id), summary=claim))[1].status == "completed"
    observed = _preview(engine, "after-claim")
    layers = [observed[key] for key in ("tool_layer", "todo_layer", "environment_layer", "memory_layer")]
    assert claim not in json.dumps(layers, ensure_ascii=False)
    assert progress_calls(observed, "report_status")[-1]["arguments"] == {
        "items": [{"activity_name": _name(engine, waited.activity_id), "summary": claim}]}
    row = tool_targets(observed)[_name(engine, waited.activity_id)]
    assert row["phase"] == "进行中" and row["state"].startswith("已做")


def _recall(engine: LifeEngine, name: str, task_name: str) -> dict:
    """@brief 即时回看冻结观察的公开结果，不登记动作、不推进时间、不产生进度事件。"""
    observed = _preview(engine, name)
    state, sequence = engine.context.state.model_dump(), engine.context.store.last_event_seq
    result = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=task_name), observed)
    assert (engine.context.state.model_dump(), engine.context.store.last_event_seq) == (state, sequence)
    assert not progress_calls(observed, "recall_task_summary")
    return result["recall"]


def test_recall_by_task_and_activity_names_reaches_the_next_observation(engine_factory) -> None:
    """回看可按任务名或活动名（目标名见当日目标用例）；结果在下一次观察进度层该调用的 details.recall 中呈现，不带身份；
    工作主题按实际累计工时回看，自述照原文写、不逐条标注未经核验（系统层规则统一说明）。会话名不在七层中出现，回看会话名以
    TASK_NOT_FOUND 业务拒绝（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「通用格式」、第八章「回看的名称只取七层中出现的名称」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    topic = engine.context.scenario.work_topics[0]
    waited = _wait(engine, "recall-wait")
    name = _name(engine, waited.activity_id)
    stay = sessions.current_session(engine.context.state, 1)
    assert _recall(engine, "recall-task", f"工作：{topic.topic}") == {
        "kind": "task", "name": f"工作：{topic.topic}", "note_history": [],
        "contract": {"topic": topic.topic, "target": "约 8 小时", "done": "约 0 小时", "remaining": "约 8 小时"}}
    before = _recall(engine, "recall-activity", name)
    home = next(item.name for item in engine.context.scenario.locations if item.location_id == "home")
    assert {key: before[key] for key in ("kind", "name", "place", "phase")} == {
        "kind": "activity", "name": name, "place": home, "phase": "进行中"}
    assert before["objective_progress"].startswith("已做") and "progress_note" not in before
    _wake(engine)
    _round(engine, "recall-note", "report_status", report_request(name, summary="等得有点久。"))
    after = _recall(engine, "recall-activity-again", name)
    assert after["progress_note"] == "等得有点久。" and "subjective_status" not in after
    _wake(engine)
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        _recall(engine, "recall-session", stay.name)
    for recall in (before, after):
        assert not {key for key in recall if key.endswith("_id")}


def test_recall_of_content_started_with_a_walk_names_the_road(engine_factory) -> None:
    """边走边看：和步行同一请求开始的内容，回看的地点是它所属的那段路「去<目的地>的路上」，不是出发地；
    到达收尾后仍是那段路（第七章 recall_task_summary「活动给 {名称, 地点, …}」）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    content = _pick(_preview(engine, "road-recall"), "use_app", kind="app")
    travel_id, watch_id = _started_ids(_depart(engine, "road-recall", content))
    road = f"去{({item.location_id: item.name for item in engine.context.scenario.locations})['office']}的路上"
    assert _recall(engine, "recall-road-content", _name(engine, watch_id))["place"] == road
    assert _recall(engine, "recall-road-walk", _name(engine, travel_id))["place"] == road
    _until_ended(engine, travel_id, "road-recall-walk")
    assert _conclude(engine, "road-recall-arrive", travel_id, watch_id).status == "completed"
    after = _recall(engine, "recall-road-content-after", _name(engine, watch_id))
    assert (after["place"], after["phase"]) == (road, "已收尾")


def test_sessions_and_pending_conclusion_survive_reopen(engine_factory, tmp_path) -> None:
    """地点会话与待收尾随世界状态保存；重开后仍须在同一秒收尾。"""
    from worldmock.life.engine import LifeEngine
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    travel = _depart(engine, "reopen-depart")
    _until_ended(engine, travel.activity_id, "reopen-walk")
    saved = dict(engine.context.state.sessions)
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(tmp_path / "scenario-0" / "scenarios" / "single-person.json", run_dir)
    try:
        assert restored.context.state.sessions == saved
        rejected = _wait(restored, "reopen-other")
        assert rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
        assert _conclude(restored, "reopen-conclude", travel.activity_id).status == "completed"
    finally:
        restored.close()


def test_lifecycle_records_and_export_match_the_final_checkpoint(engine_factory, tmp_path: Path) -> None:
    """暂停、恢复与结束逐活动各写一条带快照、含本次请求事件的 environment_event；导出的活动与最终检查点逐项一致。"""
    from worldmock.footprints.export import export_run
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    first, second = _two_parallel(engine, "records")
    _wake(engine)
    _round(engine, "records-pause", "pause_activity", pause_request(_name(engine, first), summary="停。"))
    _wake(engine)
    _round(engine, "records-resume", "report_status", report_request(_name(engine, first), summary="接着等。"))
    _wake(engine)
    _conclude(engine, "records-end", first, second)
    events = _lifecycle_events(engine)
    assert sorted((event.activity_id, event.data["kind"]) for event in events) == sorted([
        (first, "activity_paused"), (first, "activity_resumed"), (first, "activity_concluded"),
        (second, "activity_concluded")])
    requested = {event.action_id: event.event_id for event in engine.context.store.read_agent_events("action_requested", 1)}
    for event in events:
        assert requested[event.action_id] in event.caused_by
        assert event.data["activity"]["activity_id"] == event.activity_id == event.data["activity_id"]
    agent_ref = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "location_sessions_export"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_run(engine.context.store, checkpoint.last_event_seq, tmp_path / "export")
    lines = (tmp_path / "export" / "activities.jsonl").read_text(encoding="utf-8").splitlines()
    exported = {row["activity_id"]: row for row in map(json.loads, lines)}
    assert exported.keys() == engine.context.state.activities.keys()
    for activity_id, activity in engine.context.state.activities.items():
        expected = activity.model_dump(mode="json")
        row = {**exported[activity_id], "kind": exported[activity_id]["activity_type"]}
        assert {key: row[key] for key in expected} == expected


def test_slot_counting_durations_and_session_helpers_are_pure(engine_factory) -> None:
    """名额计数（到店与远程下单不占名额，方式相同的两份食用合成一餐）、人读时长、会话辅助函数的边界。"""
    assert activity_control.new_activity_count(["purchase", "purchase"], ["到店", "到店"]) == 0
    assert activity_control.new_activity_count(["purchase", "purchase"], ["到店", "远程下单"]) == 0
    assert activity_control.new_activity_count(["consume", "consume"], ["食用", "食用"]) == 1
    assert activity_control.new_activity_count(["consume", "consume"], ["食用", "使用"]) == 2
    assert activity_control.new_activity_count(["travel_to", "purchase"], ["步行", "远程下单"]) == 1
    assert activity_control.new_activity_count(["travel_to", "use_app"], ["步行", "打开"]) == 2
    assert [duration_phrase(value) for value in (59, 600, 5400)] == ["不到 1 分钟", "约 10 分钟", "约 1.5 小时"]
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited = _wait(engine, "helpers")
    activity = engine.context.state.activities[waited.activity_id]
    work = activity.model_copy(update={"kind": "work", "elapsed_seconds": 7200,
                                       "planned_duration_seconds": 7200}, deep=True)
    assert activity_control.objective_progress(work) == "已做约 2 小时，还剩不到 1 分钟"
    stay = sessions.current_session(engine.context.state, 1)
    assert sessions.end_transit(engine.context.state, 1, {item.location_id: item.name for item in engine.context.scenario.locations}) == (engine.context.state, [])
    assert not sessions.on_road(engine.context.state, activity.model_copy(update={"session_ids": []}))
    assert sessions.place_label(stay, {item.location_id: item.name for item in engine.context.scenario.locations}) == {item.location_id: item.name for item in engine.context.scenario.locations}["home"]
    road = stay.model_copy(update={"kind": "transit", "location_id": None, "origin_id": "home",
                                   "destination_id": "office"})
    assert sessions.place_label(road, {item.location_id: item.name for item in engine.context.scenario.locations}) == f"去{({item.location_id: item.name for item in engine.context.scenario.locations})['office']}的路上"
    assert sessions.arriving_transit(engine.context.state, 1, engine.context.state.current_time) is None
    many = {f"a{index:02d}": activity.model_copy(update={"activity_id": f"a{index:02d}"}) for index in range(12)}
    crowded = engine.context.state.model_copy(update={"activities": many})
    names = activity_control.visible_names(crowded, 1, [])["activities"]
    assert names["a00"] == activity.name and names["a01"] == f"{activity.name}（第二次）"
    assert names["a09"] == f"{activity.name}（第十次）" and names["a11"] == f"{activity.name}（第十二次）"


def test_world_state_rejects_a_body_settled_ahead_of_the_clock(engine_factory) -> None:
    """恢复时世界状态的人物不变量仍按原样拒绝：身体结算时刻不能晚于世界时钟（与会话集合校验同在 validate_world）。"""
    engine = engine_factory(mutate=_contacts)
    document = engine.context.state.model_dump(mode="json")
    document["agents"]["1"]["body_settled_at"] = engine.context.state.current_time + 1
    with pytest.raises(ValueError, match="body settlement is ahead of world time"):
        type(engine.context.state).model_validate(document)
