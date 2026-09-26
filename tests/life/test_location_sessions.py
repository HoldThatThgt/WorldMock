"""地点会话与活动生命周期（docs/dev/SPEC-time-and-energy.md 第七章、第一章受限轮中依赖名额与待收尾的三支）。

会话是一段地点停留或一段路，由系统随出发与到达切换；每人两个活动名额，进行中、暂停与待收尾的活动各占一个；
活动一结束就成为待收尾，同一秒 conclude_activity，做别的以 ACTIVITY_CONCLUSION_REQUIRED 拒绝，收尾前时钟不推进。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan, wake_by_mail
from tests.life.test_export_integration import _stage_final_interval
from worldmock.errors import ProtocolError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.engine import LifeEngine
from worldmock.life.narration import render_scene
from worldmock.life.models import (
    ActivityStatus, ConcludeActivityRequest, PauseActivityRequest, RecallTaskSummaryRequest, ReportStatusRequest,
    SameMomentStage,
    SessionKind, SessionStatus, StartActivityRequest, active_activities,
)

_FORMAL = Path("scenarios/white-collar-12-weeks.json")


def _round(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 单人物按真实顺序观察、登记并结算一轮，返回（本轮观察, 结果）。"""
    decision = context(engine, name, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _pick(observed: dict, canonical: str, *, mode: str | None = None, kind: str | None = None,
          place: str | None = None) -> tuple[str, str]:
    """@brief 从本轮冻结观察取一个对象名与动作短语。"""
    return next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                if (kind is None or row["kind"] == kind) and (place is None or row["object_id"] == place)
                for action in row["actions"] if action["canonical_action"] == canonical
                and (mode is None or action.get("travel_mode") == mode))


def _preview(engine: LifeEngine, name: str) -> dict:
    """@brief 不开轮次地读取一次当前观察（只追加观察事件）。"""
    return engine.observe(context(engine, f"preview-{name}"))


def _start(engine: LifeEngine, name: str, *choices: tuple[str, str]):
    """@brief 以可读对象与动作短语开始一到两项活动。"""
    request = StartActivityRequest(targets=[item[0] for item in choices], actions=[item[1] for item in choices])
    return _round(engine, name, "start_activity", request)[1]


def _wait(engine: LifeEngine, name: str):
    """@brief 以「自己」开始一段等待。"""
    return _start(engine, name, ("自己", "等待"))


def _depart(engine: LifeEngine, name: str, *companions: tuple[str, str], destination: str = "office"):
    """@brief 步行前往目的地，可在同一请求中一并开始路上的其他活动。"""
    route = _pick(_preview(engine, name), "travel_to", mode="walking", place=destination)
    return _start(engine, name, route, *companions)


def _name(engine: LifeEngine, activity_id: str) -> str:
    """@brief 活动的可读名称（本文件的用例里同一轮没有重名）。"""
    return engine.state.activities[activity_id].name


def _conclude(engine: LifeEngine, name: str, *activity_ids: str, memories: list[list[dict]] | None = None):
    """@brief 一次结束点名的活动，每项写一句总结，记忆默认空。"""
    request = ConcludeActivityRequest(task=[_name(engine, item) for item in activity_ids],
                                      summary=["收个尾。"] * len(activity_ids),
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
        if engine.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.state, 1)
            _round(engine, f"{name}-report-{step}", "report_status", ReportStatusRequest(
                task=[item.name for item in running], summary=["继续。"] * len(running)))
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def _stage(engine: LifeEngine, mutate) -> None:
    """@brief 改造世界状态并经存储落盘（保持版本连续），用于构造边界情形。"""
    staged = engine.state.model_copy(deep=True)
    mutate(staged)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_location_sessions_staged"})])
    engine.state = staged


def _environment_kinds(engine: LifeEngine, kind: str) -> list:
    """@brief 本人按 data.kind 过滤的环境事件。"""
    return [event for event in engine.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") == kind]


def test_run_start_opens_one_stay_session_per_agent(engine_factory) -> None:
    """运行开始时按初始地点开始首个停留会话；名称为「<地点>（<M月D日> HH:MM 起）」，会话不写事件。"""
    engine = engine_factory("multi-person.json")
    for agent_id, agent in engine.state.agents.items():
        session = sessions.current_session(engine.state, agent_id)
        assert session.kind == SessionKind.STAY and session.location_id == agent.location_id
        place = next(item.name for item in engine.scenario.locations if item.location_id == agent.location_id)
        assert session.name == f"{place}（{sessions.moment_label(engine.state.current_time, 'Asia/Shanghai')} 起）"
    assert not [event for event in engine.store.read_events(engine.store.last_event_seq)
                if "session" in str(event.data.get("kind", ""))]


def test_departure_and_arrival_switch_sessions_and_end_road_activities(engine_factory) -> None:
    """出发切到路上会话，到达当秒出行完成、路上开始的活动以 route_ended 结束并成为待收尾，开始目的地的停留会话。"""
    engine = engine_factory()
    stage_day_plan(engine)
    home = sessions.current_session(engine.state, 1)
    content = _pick(_preview(engine, "content"), "watch", kind="content")
    started = _depart(engine, "depart", content)
    travel_id, watch_id = _started_ids(started)
    road = sessions.current_session(engine.state, 1)
    assert engine.state.sessions[home.session_id].status == SessionStatus.ENDED
    assert road.kind == SessionKind.TRANSIT and road.origin_id == "home" and road.destination_id == "office"
    assert road.name.startswith("去") and "的路上（" in road.name
    assert engine.state.activities[watch_id].session_ids == [road.session_id]
    _until_ended(engine, travel_id, "walk")
    arrived = engine.state.current_time
    travel, watch = engine.state.activities[travel_id], engine.state.activities[watch_id]
    assert travel.status == ActivityStatus.COMPLETED and travel.concluded_at is None
    assert (watch.status, watch.boundary_type, watch.interruption_reason) == (
        ActivityStatus.CANCELLED, "route_ended", "route_ended")
    assert watch.ended_at == arrived and watch.concluded_at is None
    office = sessions.current_session(engine.state, 1)
    assert office.kind == SessionKind.STAY and office.location_id == "office" and office.started_at == arrived
    assert engine.state.sessions[road.session_id].ended_at == arrived
    assert {"planned_end", "no_active_activity"} <= set(engine.state.agents[1].pending_wake_reasons)
    observed = _preview(engine, "arrived")
    assert observed["scene"]["just_ended"] == [travel.name, watch.name]
    assert f"刚结束：{travel.name}、{watch.name}。" in render_scene(observed["scene"])


def test_just_ended_requires_conclusion_in_the_same_second(engine_factory) -> None:
    """待收尾时做别的以 ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名；收尾前时钟不推进；收尾后释放名额再开始。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "first-wait")
    _until_ended(engine, waited.activity_id, "first-wait")
    now = engine.state.current_time
    assert engine.scheduling_state().ready_agent_ids == (1,)
    rejected = _wait(engine, "second-wait")
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert _name(engine, waited.activity_id) in rejected.message
    assert engine.state.current_time == now and engine.state.agents[1].next_decision_at == now
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    concluded = _conclude(engine, "conclude-wait", waited.activity_id)
    assert concluded.status == "completed"
    assert engine.state.activities[waited.activity_id].concluded_at == now
    assert engine.state.activities[waited.activity_id].status == ActivityStatus.COMPLETED
    assert _wait(engine, "third-wait").status == "in_progress"
    assert engine.state.current_time == now
    advance_next(engine)


def test_other_activity_keeps_running_while_the_ended_one_is_concluded(engine_factory) -> None:
    """边走边看时内容先结束：当秒只收尾内容，出行不受影响；收尾后时钟照常推进。"""
    engine = engine_factory()
    stage_day_plan(engine)
    content = _pick(_preview(engine, "short-content"), "watch", kind="content")
    travel_id, watch_id = _started_ids(_depart(engine, "long-depart", content))

    def shorten(state) -> None:
        """@brief 让内容比路程先放完。"""
        state.activities[watch_id] = state.activities[watch_id].model_copy(update={
            "planned_end_at": state.current_time + 300, "planned_duration_seconds": 300}, deep=True)

    _stage(engine, shorten)
    while engine.state.activities[watch_id].status == ActivityStatus.ACTIVE:
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    assert engine.state.activities[watch_id].status == ActivityStatus.COMPLETED
    assert engine.state.activities[travel_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _conclude(engine, "conclude-content", watch_id).status == "completed"
    assert engine.state.activities[travel_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)


def _wake(engine: LifeEngine, offset: int = 60) -> int:
    """@brief 以一封到达邮件把下一次决策机会钉在 offset 秒后，推进到该机会并返回当时的时刻。"""
    wake_by_mail(engine, offset=offset)
    assert advance_until_ready(engine)
    return engine.state.current_time


def _two_parallel(engine: LifeEngine, name: str) -> list[str]:
    """@brief 同一请求开始等待与观看（占用互不冲突），两个名额都被进行中活动占着。"""
    content = _pick(_preview(engine, name), "watch", kind="content")
    started = _start(engine, name, ("自己", "等待"), content)
    assert started.status == "in_progress", started.error_code
    return _started_ids(started)


def _lifecycle_events(engine: LifeEngine) -> list:
    """@brief 本人的活动生命周期记录（结束、暂停、恢复）。"""
    return [event for event in engine.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") in {"activity_concluded", "activity_paused", "activity_resumed"}]


def test_departure_with_running_activity_is_rejected_naming_it(engine_factory) -> None:
    """名额未满而有进行中活动时选择出行：ACTIVITY_PRECONDITION_UNMET 点名要先处理的活动；暂停后出发被受理，暂停的活动留在原地。"""
    engine = engine_factory()
    stage_day_plan(engine)
    watched = _start(engine, "home-watch", _pick(_preview(engine, "home-watch"), "watch", kind="content"))
    _wake(engine)
    rejected = _depart(engine, "blocked-depart")
    name = _name(engine, watched.activity_id)
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert rejected.data["unmet"] == ["activities_before_departure"]
    assert rejected.data["suggested"] == {"conclude_or_pause": [name]}
    assert name in rejected.message
    assert sessions.current_session(engine.state, 1).kind == SessionKind.STAY
    paused = _round(engine, "pause-before-leaving", "pause_activity", PauseActivityRequest(
        task=[name], summary=["出门前先停下。"]))[1]
    assert paused.status == "completed"
    assert _depart(engine, "depart-after-pause").status == "in_progress"
    activity = engine.state.activities[watched.activity_id]
    assert activity.status == ActivityStatus.PAUSED
    assert engine.state.sessions[activity.session_ids[-1]].kind == SessionKind.STAY
    assert sessions.current_session(engine.state, 1).kind == SessionKind.TRANSIT


def test_paused_activity_resumes_only_at_its_start_location(engine_factory) -> None:
    """在别处恢复以 ACTIVITY_PRECONDITION_UNMET 拒绝并点名开始地点；回到开始地点后归入那里的新停留会话并可恢复。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "wait-home")
    _wake(engine)
    name = _name(engine, waited.activity_id)
    _round(engine, "pause-home", "pause_activity", PauseActivityRequest(task=[name], summary=["先停一下。"]))
    travel = _depart(engine, "to-office")
    _until_ended(engine, travel.activity_id, "to-office")
    assert _conclude(engine, "arrive-office", travel.activity_id).status == "completed"
    elsewhere = _round(engine, "resume-elsewhere", "report_status", ReportStatusRequest(
        task=[name], summary=["想接着等。"]))[1]
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    assert elsewhere.status == "rejected" and elsewhere.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert elsewhere.data["suggested"] == {"travel_to": home}
    assert home in elsewhere.message and name in elsewhere.message
    assert engine.state.activities[waited.activity_id].status == ActivityStatus.PAUSED
    back = _depart(engine, "back-home", destination="home")
    _until_ended(engine, back.activity_id, "back-home")
    current = sessions.current_session(engine.state, 1)
    assert current.location_id == "home" and waited.activity_id in current.activity_ids
    assert engine.state.activities[waited.activity_id].session_ids[-1] == current.session_id
    assert _conclude(engine, "arrive-home", back.activity_id).status == "completed"
    resumed = _round(engine, "resume-home", "report_status", ReportStatusRequest(
        task=[name], summary=["回家接着等。"]))[1]
    assert resumed.status == "completed"
    activity = engine.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.ACTIVE
    assert activity.report_anchor_at == engine.state.current_time
    assert [event.data["kind"] for event in _lifecycle_events(engine)
            if event.activity_id == waited.activity_id] == ["activity_paused", "activity_resumed"]


def test_paused_activity_can_be_concluded_elsewhere(engine_factory) -> None:
    """人在别处可以直接结束暂停的活动：取消并收尾，名额释放。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "wait-then-leave")
    _wake(engine)
    _round(engine, "pause-then-leave", "pause_activity", PauseActivityRequest(
        task=[_name(engine, waited.activity_id)], summary=["先停。"]))
    travel = _depart(engine, "leave")
    _until_ended(engine, travel.activity_id, "leave")
    assert _conclude(engine, "conclude-both", travel.activity_id, waited.activity_id).status == "completed"
    activity = engine.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.CANCELLED and activity.interruption_reason == "agent_cancelled"
    assert activity.concluded_at == engine.state.current_time
    assert _wait(engine, "slot-free").status == "in_progress"


def test_pausing_an_activity_on_the_road_is_unavailable(engine_factory) -> None:
    """路上的活动（出行本身与路上开始的活动）不能暂停：ACTIVITY_PAUSE_UNAVAILABLE，活动照常进行。"""
    engine = engine_factory()
    stage_day_plan(engine)
    content = _pick(_preview(engine, "road-content"), "watch", kind="content")
    travel_id, watch_id = _started_ids(_depart(engine, "road", content))
    _wake(engine)
    for index, activity_id in enumerate((travel_id, watch_id)):
        rejected = _round(engine, f"road-pause-{index}", "pause_activity", PauseActivityRequest(
            task=[_name(engine, activity_id)], summary=["停一下。"]))[1]
        assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
        assert _name(engine, activity_id) in rejected.message
        assert engine.state.activities[activity_id].status == ActivityStatus.ACTIVE


def test_ending_travel_midway_ends_road_activities_and_stays_at_origin(engine_factory) -> None:
    """途中结束出行：路上会话结束，路上开始的其他活动以 route_ended 成为待收尾，人仍在出发地并开始该地的停留会话。"""
    engine = engine_factory()
    stage_day_plan(engine)
    content = _pick(_preview(engine, "midway-content"), "watch", kind="content")
    travel_id, watch_id = _started_ids(_depart(engine, "midway", content))
    road = sessions.current_session(engine.state, 1)
    now = _wake(engine)
    assert _conclude(engine, "stop-walking", travel_id).status == "completed"
    travel, watch = engine.state.activities[travel_id], engine.state.activities[watch_id]
    assert travel.status == ActivityStatus.CANCELLED and travel.concluded_at == now
    assert (watch.status, watch.boundary_type, watch.interruption_reason, watch.concluded_at) == (
        ActivityStatus.CANCELLED, "route_ended", "route_ended", None)
    assert watch.media_position_seconds > 0
    stay = sessions.current_session(engine.state, 1)
    assert engine.state.sessions[road.session_id].ended_at == now
    assert stay.kind == SessionKind.STAY and stay.location_id == "home" == engine.state.agents[1].location_id
    assert engine.scheduling_state().ready_agent_ids == (1,)
    snapshots = {event.activity_id: event.data["activity"] for event in _lifecycle_events(engine)}
    assert snapshots[travel_id]["concluded_at"] == now and snapshots[watch_id]["boundary_type"] == "route_ended"
    assert _conclude(engine, "conclude-content", watch_id).status == "completed"


def test_two_slots_full_block_new_activities_until_conclusion(engine_factory) -> None:
    """两个名额被进行中或暂停中的活动占满时不能开始新活动（SESSION_LIMIT_EXCEEDED 点名两项），conclude_activity 后释放。

    达到同刻上限后名额被暂停活动占满，受限轮只开放恢复或结束；结束一项后只开放开始或恢复。
    """
    engine = engine_factory()
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "full")
    _wake(engine)
    _round(engine, "pause-wait", "pause_activity", PauseActivityRequest(
        task=[_name(engine, waited)], summary=["停。"]))
    _wake(engine)
    blocked = _wait(engine, "third")
    assert blocked.status == "rejected" and blocked.error_code == "SESSION_LIMIT_EXCEEDED"
    assert _name(engine, waited) in blocked.message and _name(engine, watched) in blocked.message
    _round(engine, "pause-watch", "pause_activity", PauseActivityRequest(
        task=[_name(engine, watched)], summary=["也停。"]))
    still = _wait(engine, "third-again")
    assert still.status == "rejected" and still.error_code == "SESSION_LIMIT_EXCEEDED"
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.RESUME_OR_CONCLUDE
    assert _conclude(engine, "free-one", waited).status == "completed"
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME
    assert _wait(engine, "third-after").status == "in_progress"


def test_both_paused_redecide_same_second_until_one_resumes(engine_factory) -> None:
    """两项都暂停时同一秒再决策；结束其一后仍同秒再决策；恢复其一后时钟可推进。"""
    engine = engine_factory()
    stage_day_plan(engine)
    first, second = _two_parallel(engine, "both")
    now = _wake(engine)
    paused = _round(engine, "pause-both", "pause_activity", PauseActivityRequest(
        task=[_name(engine, first), _name(engine, second)], summary=["都停一下。", "也停。"]))[1]
    assert paused.status == "completed"
    agent = engine.state.agents[1]
    assert agent.next_decision_at == now and agent.pending_wake_reasons == ["no_active_activity"]
    assert _conclude(engine, "end-one", first).status == "completed"
    assert engine.state.agents[1].next_decision_at == now
    resumed = _round(engine, "resume-other", "report_status", ReportStatusRequest(
        task=[_name(engine, second)], summary=["接着看。"]))[1]
    assert resumed.status == "completed" and engine.state.agents[1].next_decision_at > now
    advance_next(engine)
    assert engine.state.current_time > now


def test_arrival_denied_fails_travel_and_starts_stay_at_destination(engine_factory, monkeypatch) -> None:
    """到达被拒：出行记为 failed 并成为待收尾；人已在目的地，开始目的地的停留会话。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel = _depart(engine, "denied-depart")
    guarded = tuple(item.model_copy(update={"required_qualifications": ("门禁卡",)})
                    if item.location_id == "office" else item for item in engine.scenario.locations)
    monkeypatch.setattr(engine, "scenario", engine.scenario.model_copy(update={"locations": guarded}))
    _until_ended(engine, travel.activity_id, "denied-walk")
    activity = engine.state.activities[travel.activity_id]
    assert activity.status == ActivityStatus.FAILED and activity.interruption_reason == "LOCATION_ACCESS_DENIED"
    assert activity.concluded_at is None
    stay = sessions.current_session(engine.state, 1)
    assert stay.kind == SessionKind.STAY and stay.location_id == "office" == engine.state.agents[1].location_id
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _conclude(engine, "conclude-denied", travel.activity_id).status == "completed"


def test_experiment_end_ends_every_session_without_conclusion(engine_factory) -> None:
    """实验截止时所有会话结束；进行中的活动按 truncated 处理，不要求收尾。"""
    engine = engine_factory()
    _stage_final_interval(engine, 600)
    stage_day_plan(engine)
    waited = _wait(engine, "wait-to-end")
    _until_ended(engine, waited.activity_id, "wait-to-end")
    assert engine.state.current_time == engine.state.end_time
    assert all(item.status == SessionStatus.ENDED and item.ended_at == engine.state.end_time
               for item in engine.state.sessions.values())
    activity = engine.state.activities[waited.activity_id]
    assert activity.status == ActivityStatus.TRUNCATED and activity.concluded_at is None
    assert engine.scheduling_state().ready_agent_ids == ()


def test_same_named_activities_get_ordinals_instead_of_failing(engine_factory) -> None:
    """同一分钟内先后开始的同名活动同轮可见时追加「（第二次）」，不抛错，名称仍能唯一解析。"""
    engine = engine_factory()
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
    rows = [item["name"] for row in observed["facts"]["progress"]["sessions"] for item in row["activities"]]
    assert rows == [names[first.activity_id], names[second.activity_id]]
    _wake(engine)
    reported = _round(engine, "report-second", "report_status", ReportStatusRequest(
        task=[names[second.activity_id]], summary=["第二段也在等。"]))[1]
    assert reported.status == "completed"
    assert engine.state.activities[second.activity_id].progress_note == "第二段也在等。"


def test_cross_day_duplicate_session_and_activity_names_get_ordinals(engine_factory) -> None:
    """跨日重名的会话与活动不抛错：名称用绝对日期；仍然重名时（例如跨年同日）同轮追加序号，回看与播报都能唯一解析。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "today")
    stay = sessions.current_session(engine.state, 1)
    earlier = engine.state.current_time - 365 * 86400

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
    sessions_by_id = {row["session_id"]: row["name"] for row in observed["session_bindings"]}
    activities_by_id = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    assert sessions_by_id["session-last-year"] == stay.name
    assert sessions_by_id[stay.session_id] == f"{stay.name}（第二次）"
    assert activities_by_id["activity-last-year"] == _name(engine, waited.activity_id)
    assert activities_by_id[waited.activity_id] == f"{_name(engine, waited.activity_id)}（第二次）"
    _wake(engine)
    recalled = _round(engine, "recall-duplicate", "recall_task_summary",
                      RecallTaskSummaryRequest(task_name=sessions_by_id[stay.session_id]))[1]
    assert recalled.status == "completed" and recalled.data["recall"]["kind"] == "session"
    _wake(engine)
    reported = _round(engine, "report-duplicate", "report_status", ReportStatusRequest(
        task=[activities_by_id[waited.activity_id]], summary=["今天的这段还在等。"]))[1]
    assert reported.status == "completed"
    assert engine.state.activities[waited.activity_id].progress_note == "今天的这段还在等。"
    assert not [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.event_type == "run_failed"]


def test_self_report_stays_out_of_the_scene_and_is_marked_unverified(engine_factory) -> None:
    """自述不当事实：播报的自述只出现在进度层活动行，标「你自己记下的，未经核验」；场景没有 current_sessions，也不复述自述。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "self-report")
    _wake(engine)
    claim = "我已经吃过晚饭了。"
    assert _round(engine, "claim", "report_status", ReportStatusRequest(
        task=[_name(engine, waited.activity_id)], summary=[claim]))[1].status == "completed"
    observed = _preview(engine, "after-claim")
    assert "current_sessions" not in observed["scene"]
    assert claim not in json.dumps(observed["scene"], ensure_ascii=False)
    assert claim not in render_scene(observed["scene"])
    rows = [item for row in observed["facts"]["progress"]["sessions"] for item in row["activities"]]
    row = next(item for item in rows if item["name"] == _name(engine, waited.activity_id))
    assert row["subjective_progress"] == claim and row["subjective_status"] == "你自己记下的，未经核验"
    assert row["phase"] == "进行中" and row["objective_progress"].startswith("已做")


def _recall(engine: LifeEngine, name: str, task_name: str) -> dict:
    """@brief 回看一个可读名称，返回下一次观察 details.recall 中的公开结果（与私有结果同形、不带身份）。"""
    _wake(engine)
    result = _round(engine, name, "recall_task_summary", RecallTaskSummaryRequest(task_name=task_name))[1]
    assert result.status == "completed"
    shown = [row.get("details", {}).get("recall") for row in _preview(engine, f"{name}-shown")[
        "facts"]["progress"]["results"]]
    assert result.data["recall"] in shown
    return result.data["recall"]


def test_recall_by_task_activity_and_session_names_reaches_the_next_observation(engine_factory) -> None:
    """回看可按任务名、活动名或会话名（目标名见当日目标用例）；结果在下一次观察的 details.recall 中呈现，不带身份。"""
    engine = engine_factory()
    stage_day_plan(engine)
    task = next(iter(engine.scenario.tasks))
    _stage(engine, lambda state: state.agents[1].visible_task_ids.add(task.task_id))
    waited = _wait(engine, "recall-wait")
    name = _name(engine, waited.activity_id)
    stay = sessions.current_session(engine.state, 1)
    assert _recall(engine, "recall-task", task.title) == {
        "kind": "task", "name": task.title,
        "due_at": sessions.moment_label(int(task.due_at.timestamp()), engine.scenario.timezone)}
    before = _recall(engine, "recall-activity", name)
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    assert {key: before[key] for key in ("kind", "name", "place", "phase")} == {
        "kind": "activity", "name": name, "place": home, "phase": "进行中"}
    assert before["objective_progress"].startswith("已做") and "progress_note" not in before
    _wake(engine)
    _round(engine, "recall-note", "report_status", ReportStatusRequest(task=[name], summary=["等得有点久。"]))
    after = _recall(engine, "recall-activity-again", name)
    assert (after["progress_note"], after["subjective_status"]) == ("等得有点久。", "你自己记下的，未经核验")
    assert _recall(engine, "recall-session", stay.name) == {
        "kind": "session", "place": home, "started_at": sessions.moment_label(stay.started_at, "Asia/Shanghai"),
        "ended_at": None, "activity_names": [name]}
    for recall in (before, after):
        assert not {key for key in recall if key.endswith("_id")}


def test_sessions_and_pending_conclusion_survive_reopen(engine_factory) -> None:
    """地点会话与待收尾随世界状态保存；重开后仍须在同一秒收尾。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel = _depart(engine, "reopen-depart")
    _until_ended(engine, travel.activity_id, "reopen-walk")
    saved = dict(engine.state.sessions)
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(_FORMAL, run_dir)
    try:
        assert restored.state.sessions == saved
        rejected = _wait(restored, "reopen-other")
        assert rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
        assert _conclude(restored, "reopen-conclude", travel.activity_id).status == "completed"
    finally:
        restored.close()


def test_lifecycle_records_and_export_match_the_final_checkpoint(engine_factory, tmp_path: Path) -> None:
    """暂停、恢复与结束逐活动各写一条带快照、含本次请求事件的 environment_event；导出的活动与最终检查点逐项一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    first, second = _two_parallel(engine, "records")
    _wake(engine)
    _round(engine, "records-pause", "pause_activity", PauseActivityRequest(
        task=[_name(engine, first)], summary=["停。"]))
    _wake(engine)
    _round(engine, "records-resume", "report_status", ReportStatusRequest(
        task=[_name(engine, first)], summary=["接着等。"]))
    _wake(engine)
    _conclude(engine, "records-end", first, second)
    events = _lifecycle_events(engine)
    assert sorted((event.activity_id, event.data["kind"]) for event in events) == sorted([
        (first, "activity_paused"), (first, "activity_resumed"), (first, "activity_concluded"),
        (second, "activity_concluded")])
    requested = {event.action_id: event.event_id for event in engine.store.read_agent_events("action_requested", 1)}
    for event in events:
        assert requested[event.action_id] in event.caused_by
        assert event.data["activity"]["activity_id"] == event.activity_id == event.data["activity_id"]
    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "location_sessions_export"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_run(engine.store, checkpoint.last_event_seq, tmp_path / "export")
    lines = (tmp_path / "export" / "activities.jsonl").read_text(encoding="utf-8").splitlines()
    exported = {row["activity_id"]: row for row in map(json.loads, lines)}
    assert exported.keys() == engine.state.activities.keys()
    for activity_id, activity in engine.state.activities.items():
        expected = activity.model_dump(mode="json")
        row = {**exported[activity_id], "kind": exported[activity_id]["activity_type"]}
        assert {key: row[key] for key in expected} == expected


def test_slot_counting_durations_and_session_helpers_are_pure(engine_factory) -> None:
    """名额计数（到店购买与远程下单不占名额，同短语的两份食用合成一餐）、人读时长、会话辅助函数的边界。"""
    from worldmock.life import activity_control

    assert activity_control.new_activity_count(["purchase", "purchase"], ["到店购买", "到店购买"]) == 0
    assert activity_control.new_activity_count(["consume", "consume"], ["吃", "吃"]) == 1
    assert activity_control.new_activity_count(["travel_to", "purchase"], ["步行前往", "配送到当前位置"]) == 1
    assert activity_control.new_activity_count(["travel_to", "watch"], ["步行前往", "观看"]) == 2
    assert [activity_control.readable_duration(value) for value in (59, 600, 5400)] == [
        "不到1分钟", "约10分钟", "约1.5小时"]
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "helpers")
    activity = engine.state.activities[waited.activity_id]
    work = activity.model_copy(update={"kind": "work", "elapsed_seconds": 7200, "effective_work_seconds": 5400,
                                       "planned_duration_seconds": 7200}, deep=True)
    assert activity_control.objective_progress(work, 7200) == "已做约2小时，有效约1.5小时，还差约2小时，还剩不到1分钟"
    assert activity_control.objective_progress(work, 0) == "已做约2小时，有效约1.5小时，还剩不到1分钟"
    assert activity_control.remaining_required(engine.state, activity, {}) is None
    stay = sessions.current_session(engine.state, 1)
    assert sessions.end_transit(engine.state, 1, engine._place_names) == (engine.state, [])
    assert not sessions.on_road(engine.state, activity.model_copy(update={"session_ids": []}))
    assert sessions.place_label(stay, engine._place_names) == engine._place_names["home"]
    road = stay.model_copy(update={"kind": "transit", "location_id": None, "origin_id": "home",
                                   "destination_id": "office"})
    assert sessions.place_label(road, engine._place_names) == f"去{engine._place_names['office']}的路上"
    assert sessions.arriving_transit(engine.state, 1, engine.state.current_time) is None
    many = {f"a{index:02d}": activity.model_copy(update={"activity_id": f"a{index:02d}"}) for index in range(12)}
    crowded = engine.state.model_copy(update={"activities": many})
    names = activity_control.visible_names(crowded, 1, [])["activities"]
    assert names["a00"] == activity.name and names["a01"] == f"{activity.name}（第二次）"
    assert names["a09"] == f"{activity.name}（第十次）" and names["a11"] == f"{activity.name}（第十二次）"


def test_world_state_rejects_a_body_settled_ahead_of_the_clock(engine_factory) -> None:
    """恢复时世界状态的人物不变量仍按原样拒绝：身体结算时刻不能晚于世界时钟（与会话集合校验同在 validate_world）。"""
    engine = engine_factory()
    document = engine.state.model_dump(mode="json")
    document["agents"]["1"]["body_settled_at"] = engine.state.current_time + 1
    with pytest.raises(ValueError, match="body settlement is ahead of world time"):
        type(engine.state).model_validate(document)
