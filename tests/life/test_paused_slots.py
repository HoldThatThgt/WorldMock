"""暂停的活动不占并行名额（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-甲名额）。

每人两个并行名额，占名额的是进行中与待收尾的活动，暂停的不占：一项暂停时可以同时开始两项；只有暂停的活动时可以出发，
它们留在原地，在别处也能直接结束；恢复暂停的活动要有空名额，依次检查开始地点、原分配内容是否已完成、空名额、占用冲突，没有
空名额以 SESSION_LIMIT_EXCEEDED 拒绝并点名进行中的活动，待办层的可用性与受理同文；名额超限的下一步按被点名活动能否
暂停写（裁决 R3、R4）；两项都暂停到同刻上限后进入开始或恢复。全部经真实请求、引擎与 SQLite 存储。
"""

from __future__ import annotations

from tests.life.conftest import (advance_next, pause_request, report_request, stage_day_plan, target_actions,
                                 target_tools)
from tests.life.test_location_sessions import (_conclude, _depart, _name, _pick, _preview, _round, _start,
                                               _started_ids, _two_parallel, _until_ended, _wait, _wake)
from tests.life.test_same_moment_decisions import _reach_limit
from tests.life.test_same_moment_decisions import _contacts
from worldmock.life import activity_control, sessions
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityStatus, SameMomentStage, SessionKind

_LIMIT = "SESSION_LIMIT_EXCEEDED"


def _occupying(engine: LifeEngine) -> str:
    """@brief 此刻占名额的活动（进行中与待收尾）的可读名称，按开始时刻与身份排序、以「、」连接，与拒绝原因同序。"""
    return "、".join(item.name for item in activity_control.slot_activities(engine.context.state, 1))


def _contents(engine: LifeEngine, name: str) -> list[dict]:
    """@brief 明确选择两个不同目录动作及实际应用，不按 app 对象推断动作。"""
    observed = _preview(engine, name)
    return [_pick(observed, "do", kind="action", place=identity)
            for identity in ("browse-feed", "study-course")]


def _paused_wait(engine: LifeEngine, name: str) -> str:
    """@brief 开始一段等待，一分钟后暂停它；暂停后没有进行中活动，人物同一秒再决策。返回等待的活动身份。"""
    waited = _wait(engine, f"{name}-wait")
    assert waited.status == "in_progress", waited.error_code
    _wake(engine)
    paused = _round(engine, f"{name}-pause", "pause_activity", pause_request(
        _name(engine, waited.activity_id), summary="先停一下。"))[1]
    assert paused.status == "completed", paused.error_code
    return waited.activity_id


def _resume(engine: LifeEngine, name: str, activity_id: str):
    """@brief 以播报点名暂停的活动（即恢复它），返回结果。"""
    return _round(engine, name, "report_status", report_request(_name(engine, activity_id), summary="接着来。"))[1]


def test_two_item_start_beside_a_paused_activity_is_accepted(engine_factory) -> None:
    """一项暂停时同时开始两项被受理：暂停的活动不占名额，两个空名额都给新开始的两项；暂停的那项仍暂停（旧行为是只剩一个
    名额、整组拒绝）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    paused = _paused_wait(engine, "lunch")
    assert activity_control.free_slots(engine.context.state, 1) == 2
    content = _contents(engine, "lunch-contents")[0]
    both = _start(engine, "lunch-two", {"target": "自己", "way": "等待", "app": None, "to": None, "pay_app": None}, content)
    assert both.status == "in_progress" and len(_started_ids(both)) == 2
    occupying = {item.activity_id for item in activity_control.slot_activities(engine.context.state, 1)}
    assert occupying == set(_started_ids(both)) and activity_control.free_slots(engine.context.state, 1) == 0
    assert engine.context.state.activities[paused].status == ActivityStatus.PAUSED


def test_resume_with_two_running_is_refused_naming_both_until_one_is_concluded(engine_factory) -> None:
    """两项进行时恢复暂停的第三项：SESSION_LIMIT_EXCEEDED，原因点名进行中的两项、下一步写结束或暂停其中一项，待办层的恢复
    标不可用且原因一字不差；结束其中一项后恢复被受理；之后再开始第三项又以名额占满拒绝，点名恢复的那项与仍在进行的那项。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    paused = _paused_wait(engine, "third")
    waited, watched = _two_parallel(engine, "third-pair")
    _wake(engine)
    listed = _occupying(engine)
    assert listed == f"{_name(engine, waited)}、{_name(engine, watched)}"
    refused = _resume(engine, "third-resume-full", paused)
    assert refused.status == "rejected" and refused.error_code == _LIMIT
    assert refused.message == refused.data["reason"] == f"Both activity slots are occupied by {listed}."
    assert refused.data["next_step"] == f"Conclude or pause one of {listed} first."
    assert engine.context.state.activities[paused].status == ActivityStatus.PAUSED
    marked = target_tools(_preview(engine, "third-todo"), _name(engine, paused))["report_status"]
    assert (marked["available"], marked["reason"]) == (False, refused.data["reason"])
    assert _conclude(engine, "third-end-wait", waited).status == "completed"
    _wake(engine)
    assert target_tools(_preview(engine, "third-todo-free"), _name(engine, paused))["report_status"]["available"]
    assert _resume(engine, "third-resume-free", paused).status == "completed"
    assert engine.context.state.activities[paused].status == ActivityStatus.ACTIVE
    _wake(engine)
    again = _wait(engine, "third-start")
    listed = _occupying(engine)
    assert listed == f"{_name(engine, paused)}、{_name(engine, watched)}"
    assert again.status == "rejected" and again.error_code == _LIMIT
    assert again.data["reason"] == f"Both activity slots are occupied by {listed}."


def test_departure_with_only_paused_activities_leaves_them_in_place(engine_factory) -> None:
    """只有暂停的活动时出发被受理：两项暂停的活动不占名额也不算进行中，留在出发地的停留会话里；到达后在别处可以直接
    结束其中一项（取消并收尾），另一项仍暂停，在别处恢复按开始地点拒绝（地点先于名额）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "leave-pair")
    _wake(engine)
    both = _round(engine, "leave-pause", "pause_activity", pause_request(
        _name(engine, waited), _name(engine, watched), summary=["先停。", "也停。"]))[1]
    assert both.status == "completed"
    travel = _depart(engine, "leave")
    assert travel.status == "in_progress", (travel.error_code, travel.message)
    assert sessions.current_session(engine.context.state, 1).kind == SessionKind.TRANSIT
    for activity_id in (waited, watched):
        activity = engine.context.state.activities[activity_id]
        assert activity.status == ActivityStatus.PAUSED and activity.location_id == "home"
        stay = engine.context.state.sessions[activity.session_ids[-1]]
        assert (stay.kind, stay.location_id) == (SessionKind.STAY, "home")
    _until_ended(engine, travel.activity_id, "leave")
    assert engine.context.state.agents[1].location_id == "office"
    assert _conclude(engine, "leave-arrive", travel.activity_id, watched).status == "completed"
    dropped = engine.context.state.activities[watched]
    assert dropped.status == ActivityStatus.CANCELLED and dropped.concluded_at == engine.context.state.current_time
    assert engine.context.state.activities[waited].status == ActivityStatus.PAUSED
    elsewhere = _resume(engine, "leave-resume-elsewhere", waited)
    assert elsewhere.status == "rejected" and elsewhere.error_code == "ACTIVITY_PRECONDITION_UNMET"




def test_resume_checks_the_free_slot_before_resource_conflicts(engine_factory) -> None:
    """恢复的检查次序，名额先于占用冲突：暂停的等待与进行中的另一段等待占用冲突，两项进行时恢复报名额占满（点名两项，
    不报冲突）；暂停观看腾出名额后再恢复，才报与那段等待的占用冲突。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    paused = _paused_wait(engine, "order")
    waited, watched = _two_parallel(engine, "order-pair")
    _wake(engine)
    full = _resume(engine, "order-resume-full", paused)
    assert full.status == "rejected" and full.error_code == _LIMIT
    assert full.data["reason"] == f"Both activity slots are occupied by {_name(engine, waited)}、{_name(engine, watched)}."
    _round(engine, "order-pause-watch", "pause_activity", pause_request(_name(engine, watched), summary="停一下。"))
    _wake(engine)
    assert activity_control.free_slots(engine.context.state, 1) == 1
    conflict = _resume(engine, "order-resume-conflict", paused)
    assert conflict.status == "rejected" and conflict.error_code == "RESOURCE_CONFLICT"
    assert conflict.data["reason"] == f"This activity conflicts with the running activities {_name(engine, waited)}."
    marked = target_tools(_preview(engine, "order-todo"), _name(engine, paused))["report_status"]
    assert (marked["available"], marked["reason"]) == (False, conflict.data["reason"])


def test_full_slots_with_a_call_say_pause_only_the_pausable_one(engine_factory) -> None:
    """名额被一段等待与一通电话占满：通话不能暂停，下一步写「结束其中一项，或暂停等待」，暂停只点名等待；暂停通话确实被
    拒（与 pause_activity 的判定同源）。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    started = _start(engine, "call-pair", {"target": "自己", "way": "等待", "app": None, "to": None, "pay_app": None}, {"target": "语音通话", "way": "进行", "app": "微信", "to": "联系人：母亲", "pay_app": None})
    assert started.status == "in_progress", (started.error_code, started.message)
    waited, called = _started_ids(started)
    _wake(engine)
    listed = _occupying(engine)
    blocked = _start(engine, "call-third", _contents(engine, "call-contents")[0])
    assert blocked.status == "rejected" and blocked.error_code == _LIMIT
    assert blocked.data["reason"] == f"Both activity slots are occupied by {listed}."
    assert blocked.data["next_step"] == f"Conclude one of {listed}, or pause {_name(engine, waited)}, first."
    hang = _round(engine, "call-pause", "pause_activity", pause_request(_name(engine, called), summary="停一下。"))[1]
    assert hang.status == "rejected" and hang.error_code == "SOCIAL_ACTIVITY_CANNOT_PAUSE"


def test_full_slots_on_the_road_say_conclude_only(engine_factory) -> None:
    """名额被出行与路上开始的观看占满：路上的活动都不能暂停，下一步只写「结束其中一项」；工具层列出的等待带同一原因。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    content = _pick(_preview(engine, "road-content"), "do", kind="action")
    travel_id, watch_id = _started_ids(_depart(engine, "road", content))
    _wake(engine)
    listed = _occupying(engine)
    assert listed == f"{_name(engine, travel_id)}、{_name(engine, watch_id)}"
    listed_wait = target_actions(_preview(engine, "road-listing"), "自己")["等待"]
    blocked = _wait(engine, "road-third")
    assert blocked.status == "rejected" and blocked.error_code == _LIMIT
    assert blocked.data["reason"] == f"Both activity slots are occupied by {listed}."
    assert blocked.data["next_step"] == f"Conclude one of {listed} first."
    assert (listed_wait["available"], listed_wait["reason"]) == (False, blocked.data["reason"])


def test_both_paused_at_the_limit_accept_a_two_item_start(engine_factory) -> None:
    """两项都暂停到同刻上限：阶段为开始或恢复（不再进入恢复或结束），同时开始两项被受理，两项暂停的活动仍暂停，同一秒的
    轮数为上限加一，之后时钟推进。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    waited, watched = _two_parallel(engine, "limit-pair")
    _wake(engine)
    now = engine.context.state.current_time
    _round(engine, "limit-pause", "pause_activity", pause_request(
        _name(engine, waited), _name(engine, watched), summary=["先停。", "也停。"]))
    _reach_limit(engine)
    limit = engine.context.scenario.max_same_moment_decisions
    agent = engine.context.state.agents[1]
    assert (agent.same_moment_rounds, agent.same_moment_stage) == (limit, SameMomentStage.START_OR_RESUME)
    first, other = _contents(engine, "limit-contents")[:2]
    assert first["target"] != other["target"] and other["target"] not in _name(engine, watched)
    both = _start(engine, "limit-two", {"target": "自己", "way": "等待", "app": None, "to": None, "pay_app": None}, other)
    assert both.status == "in_progress", (both.error_code, both.message)
    assert len(_started_ids(both)) == 2
    assert {engine.context.state.activities[key].status for key in (waited, watched)} == {ActivityStatus.PAUSED}
    agent = engine.context.state.agents[1]
    assert agent.same_moment_rounds == limit + 1 and agent.next_decision_at > now
    advance_next(engine)
    assert engine.context.state.current_time > now
