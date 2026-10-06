"""@brief app 足迹只来自当前事件合同，自动接续、跨 app 完成和会话过期保留真实因果。"""

from __future__ import annotations

import pytest

from worldmock.footprints.app_usage import app_usage_quality, project_app_usage
from worldmock.footprints.models import Event
from worldmock.errors import IntegrityError
from worldmock.life.models import ActivityState

START = 1767600000


def _event(sequence, kind, data, *, at=START, version=None, activity_id=None, agent_id=1):
    """@brief 真实事件模型提供完整的事件身份与提交组，不添加推送转换事件。"""
    return Event(event_id=f"event-{sequence}", event_seq=sequence, run_id="run", event_type=kind,
                 occurred_at=at, recorded_at="2026-01-05T08:00:00+00:00", wall_elapsed_ms=sequence,
                 state_version_before=0, state_version_after=sequence if version is None else version,
                 agent_id=agent_id, activity_id=activity_id,
                 visibility="system" if kind == "run_ended" else "agent", data=data).model_dump(mode="json")


def _app(identity="phone", app_id="bilibili", *, session="stay", content="long-video", pin=None,
         status="active", ended_at=None):
    """@brief 当前 use_app 模型的活动快照，目标是 app，内容和推送只在运行数据内。"""
    return ActivityState(activity_id=identity, agent_id=1, kind="use_app", target_id=app_id,
                         status=status, location_id="home", started_at=START, last_settled_at=START, elapsed_seconds=0,
                         planned_end_at=START + 1800, planned_duration_seconds=1800, ended_at=ended_at,
                         origin_action_id="open", origin_decision_id="decision", origin_request_event_id="request",
                         session_ids=[session], data={"app_id":app_id, "content_id":content,
                                                     "pinned_by_push_id":pin, "draw_index":0, "played":[]}
                         ).model_dump(mode="json")


def _push(sequence, identity="push", app="bilibili", session="stay", content="long-video", **fields):
    """@brief 推送使用现有 environment_event 的完整实际载荷。"""
    return _event(sequence, "environment_event", {"kind":"app_push", "push_id":identity,
                  "app_id":app, "app":"哔哩哔哩", "content_id":content, "title":"城市纪录片",
                  "content_kind":"long_video", "summary":"城市的一天。", "session_id":session,
                  "issued_at":START, **fields})


def _travel(*, session="transit", completed=False):
    """@brief 出行是独立活动，使用真实出发地、目的地和路上会话身份。"""
    return ActivityState(activity_id="commute", agent_id=1, kind="travel", location_id="home",
                         destination_id="office", travel_mode="walking", started_at=START,
                         status="completed" if completed else "active", last_settled_at=START,
                         elapsed_seconds=0, ended_at=START + 300 if completed else None,
                         planned_end_at=START + 300, planned_duration_seconds=300,
                         origin_action_id="go", origin_decision_id="decision", origin_request_event_id="request",
                         session_ids=[session], data={"path":["home", "office"]}).model_dump(mode="json")


def _progress(sequence, activity, *, before=0, after=300, completed=False, rounds=0, content="long-video",
              version=None):
    """@brief 单个真实结算区间的内容证据与结算后的活动快照，二者允许已经指向不同内容。"""
    return _event(sequence, "activity_progress", {"activity":activity, "interval_start":START,
                  "interval_end":START + 300, "content":{"content_id":content, "app_id":activity["target_id"],
                  "kind":"long_video", "position_before_seconds":before, "position_after_seconds":after,
                  "completed":completed, "completed_rounds_after":rounds}},
                  at=START + 300, version=version, activity_id=activity["activity_id"])


def test_five_app_facts_use_real_sources_and_the_consumed_content_before_automatic_selection():
    opened = _app(pin="push")
    following = _app(content="next-article")
    following["data"]["content_kind"] = "article"
    events = [_push(1), _event(2, "action_accepted", {"activity":opened}, activity_id="phone"),
              _progress(3, following, completed=True, rounds=1),
              _event(4, "environment_event", {"kind":"activity_paused", "activity":{
                  **following, "status":"paused"}}, at=START + 300, activity_id="phone")]
    facts = project_app_usage(events)
    assert {row["fact_kind"] for row in facts} == {
        "app_use", "content_consumed", "push_delivered", "push_opened", "push_released"}
    assert facts[0] == {"fact_kind":"app_use", "agent_id":1, "activity_id":"phone", "app_id":"bilibili",
                        "started_at":START, "ended_at":None, "status":"paused", "source_event_id":"event-4"}
    consumed = next(row for row in facts if row["fact_kind"] == "content_consumed")
    assert consumed == {"fact_kind":"content_consumed", "agent_id":1, "activity_id":"phone",
                        "app_id":"bilibili", "content_id":"long-video", "kind":"long_video",
                        "position_before":0, "position_after":300, "completed":True, "push_id":"push",
                        "source_event_id":"event-3"}
    assert [row["source_event_id"] for row in facts if row["fact_kind"].startswith("push_")] == [
        "event-1", "event-2", "event-3"]
    assert all(set(row) == {"fact_kind", "agent_id", "push_id", "app_id", "content_id", "session_id",
                            "occurred_at", "source_event_id"}
               for row in facts if row["fact_kind"].startswith("push_"))
    assert project_app_usage(events) == facts
    assert following["data"]["content_id"] == "next-article"


def test_parallel_native_result_snapshots_preserve_the_initial_push_pin():
    initial = _app(pin="push")
    events = [_push(1), _event(2, "action_accepted", {"status":"in_progress", "data":{"activities":[initial]}}),
              _progress(3, _app(content="following"), completed=True, rounds=1)]
    facts = project_app_usage(events)
    assert next(row for row in facts if row["fact_kind"] == "content_consumed")["push_id"] == "push"
    assert next(row for row in facts if row["fact_kind"] == "push_opened")["source_event_id"] == "event-2"


def test_cross_app_completion_releases_every_current_matching_push_without_inventing_an_open():
    events = [_push(1, "bili-push"), _push(2, "douyin-push", app="douyin"),
              _event(3, "action_accepted", {"activity":_app(pin="bili-push")}, activity_id="phone"),
              _progress(4, _app(content="following"), completed=True, rounds=1)]
    facts = project_app_usage(events)
    assert {row["push_id"] for row in facts if row["fact_kind"] == "push_released"} == {"bili-push", "douyin-push"}
    assert [row["push_id"] for row in facts if row["fact_kind"] == "push_opened"] == ["bili-push"]
    assert app_usage_quality(events, "UTC")["app_pushes"] == {
        "pushed":2, "opened_in_session":1, "consumed_pinned":1, "released_by_completion":2,
        "expired_with_session":0, "outside_window_violations":0}


def test_push_funnel_counts_each_push_once_across_partial_consumption_and_repeated_open_receipts():
    initial = _app(pin="push")
    events = [_push(1), _event(2, "action_accepted", {"activity":initial}, activity_id="phone"),
              _progress(3, initial, before=0, after=60),
              _event(4, "action_accepted", {"activity":initial}, at=START + 300, activity_id="phone"),
              _progress(5, initial, before=60, after=120)]
    result = app_usage_quality(events, "UTC")
    assert result["app_pushes"]["consumed_pinned"] == result["app_pushes"]["opened_in_session"] == 1
    assert result["content_contact"] == {"by_app":{"bilibili":2}, "by_kind":{"long_video":2}, "repeat_watch":0}


def test_repeat_watch_counts_real_rounds_at_each_interval_start_including_resumed_intervals():
    events = [_event(1, "action_accepted", {"activity":_app()}, activity_id="phone"),
              _progress(2, _app(), completed=True, rounds=1),
              _progress(3, _app(), before=0, after=30, rounds=1),
              _progress(4, _app(identity="phone-douyin", app_id="douyin"), before=30, after=90, rounds=1)]
    result = app_usage_quality(events, "UTC")["content_contact"]
    assert result == {"by_app":{"bilibili":2, "douyin":1}, "by_kind":{"long_video":3}, "repeat_watch":2}


def test_no_eligible_app_and_an_unended_cutoff_do_not_invent_push_delivery_or_expiry():
    empty = _event(2, "environment_event", {"kind":"app_push", "pushed":False, "session_id":"stay"})
    empty["visibility"] = "system"
    events = [_push(1), empty]
    assert len(project_app_usage(events)) == 1
    quality = app_usage_quality(events, "UTC")["app_pushes"]
    assert quality["pushed"] == 1 and quality["expired_with_session"] == 0


@pytest.mark.parametrize("end", ["departure", "arrival", "run_end"])
def test_push_expiry_requires_a_recorded_real_session_end(end):
    if end == "departure":
        ending = _event(2, "action_accepted", {"activity":_travel()}, activity_id="commute")
    elif end == "arrival":
        ending = _event(2, "activity_progress", {"activity":_travel(session="stay", completed=True)},
                        at=START + 300, activity_id="commute")
    else:
        ending = _event(2, "run_ended", {}, at=START + 300)
    assert app_usage_quality([_push(1), ending], "UTC")["app_pushes"]["expired_with_session"] == 1


def test_same_commit_arrival_does_not_expire_a_push_before_the_other_app_finishes():
    events = [_push(1, session="transit"),
              _event(2, "action_accepted", {"activity":_app(pin="push", session="transit")}, activity_id="phone"),
              _event(3, "activity_progress", {"activity":_travel(completed=True)},
                     at=START + 300, version=3, activity_id="commute"),
              _progress(4, _app(content="following", session="transit"), completed=True, rounds=1, version=3)]
    result = app_usage_quality(events, "UTC")["app_pushes"]
    assert result["released_by_completion"] == 1 and result["expired_with_session"] == 0
    assert next(row for row in project_app_usage(events) if row["fact_kind"] == "push_released")[
        "source_event_id"] == "event-4"


def test_completion_in_a_new_session_never_releases_the_old_matching_push():
    events = [_push(1, "historical"), _push(2, "current", app="douyin", session="next-stay"),
              _event(3, "action_accepted", {"activity":_app(app_id="douyin", pin="current", session="next-stay")},
                     activity_id="phone"),
              _progress(4, _app(app_id="douyin", content="following", session="next-stay"), completed=True, rounds=1)]
    assert [row["push_id"] for row in project_app_usage(events) if row["fact_kind"] == "push_released"] == ["current"]
    assert app_usage_quality(events, "UTC")["app_pushes"]["expired_with_session"] == 1


@pytest.mark.parametrize("timezone_name, issued_at, expected", [
    ("UTC", START, 0), ("Asia/Shanghai", START, 0), ("America/New_York", START, 1),
    ("UTC", START + 14 * 3600, 1),
])
def test_push_window_violations_use_the_recorded_local_window(timezone_name, issued_at, expected):
    assert app_usage_quality([_push(1, issued_at=issued_at)], timezone_name)["app_pushes"][
        "outside_window_violations"] == expected


@pytest.mark.parametrize("source_kind,visibility", [
    (source, visibility) for source in ("environment", "model", "scenario", "external")
    for visibility in ("agent", "evaluation", "system") if (source, visibility) != ("environment", "agent")
])
@pytest.mark.parametrize("fact", ["push", "progress"])
def test_app_facts_reject_non_environment_or_private_sources(source_kind, visibility, fact, caplog):
    """@brief 完整且可通过 Event 模型的伪造来源必须报错，不能生成推送、消费或质量计数。"""
    row = _push(1) if fact == "push" else _progress(1, _app())
    row.update(source_kind=source_kind, visibility=visibility)
    row = Event.model_validate(row).model_dump(mode="json")
    for project in (project_app_usage, lambda events: app_usage_quality(events, "UTC")):
        with pytest.raises(IntegrityError, match="agent-visible environment event"):
            project([row])
    assert "agent-visible environment event" in caplog.text


@pytest.mark.parametrize("scheduled", [False, True])
def test_no_candidate_push_is_an_authenticated_private_environment_settlement(scheduled):
    """@brief 真正没有候选的系统结算保留现有预排身份，但不制造手机事实。"""
    data = {"kind":"app_push", "pushed":False, "session_id":"stay"}
    if scheduled:
        data["scheduled_id"] = "push-grid-8"
    row = _event(1, "environment_event", data)
    row["visibility"] = "system"
    assert project_app_usage([row]) == []
    assert app_usage_quality([row], "UTC")["app_pushes"] == {
        "pushed":0, "opened_in_session":0, "consumed_pinned":0, "released_by_completion":0,
        "expired_with_session":0, "outside_window_violations":0}


@pytest.mark.parametrize("alteration", ["model", "agent_visible", "hybrid_delivery", "extra_field"])
def test_no_candidate_push_rejects_wrong_source_visibility_or_delivery_shape(alteration):
    """@brief 无候选结算只允许真实环境的系统正文，不能夹带投递字段绕过认证。"""
    row = _event(1, "environment_event", {"kind":"app_push", "pushed":False, "session_id":"stay"})
    row["visibility"] = "system"
    if alteration == "model":
        row["source_kind"] = "model"
    elif alteration == "agent_visible":
        row["visibility"] = "agent"
    elif alteration == "hybrid_delivery":
        row["data"]["push_id"] = "invented"
    else:
        row["data"]["text"] = "invented"
    with pytest.raises(IntegrityError, match="system-visible environment event|settlement fields"):
        project_app_usage([row])


@pytest.mark.parametrize("event_type", ["action_accepted", "action_result", "environment_event"])
@pytest.mark.parametrize("kind", ["app", "travel"])
@pytest.mark.parametrize("source_kind,visibility", [("model", "agent"), ("environment", "system"),
                                                   ("environment", "evaluation")])
def test_snapshot_bearing_events_reject_private_or_model_sources(event_type, kind, source_kind, visibility):
    """@brief 完整活动快照不能让模型或私有事件制造打开、app 活动或会话失效。"""
    activity = _app(pin="push", session="transit") if kind == "app" else _travel(completed=True)
    row = _event(2, event_type, {"activity":activity}, activity_id=activity["activity_id"])
    row.update(source_kind=source_kind, visibility=visibility)
    row = Event.model_validate(row).model_dump(mode="json")
    with pytest.raises(IntegrityError, match="agent-visible environment events"):
        project_app_usage([_push(1, session="transit"), row])
    with pytest.raises(IntegrityError, match="agent-visible environment events"):
        app_usage_quality([_push(1, session="transit"), row], "UTC")


@pytest.mark.parametrize("event_type", ["model_response", "observation"])
@pytest.mark.parametrize("kind", ["app", "travel"])
def test_non_activity_events_cannot_introduce_app_or_travel_snapshots(event_type, kind):
    """@brief 即使伪造为环境来源，模型响应和观察也不是活动快照的实际生产事件。"""
    activity = _app(pin="push") if kind == "app" else _travel(completed=True)
    row = _event(2, event_type, {"activity":activity}, activity_id=activity["activity_id"])
    with pytest.raises(IntegrityError, match="agent-visible environment events"):
        project_app_usage([_push(1), row])


def test_wait_content_payload_cannot_create_consumption_or_repeat_counts():
    """@brief 私自给真实等待快照夹带内容字段，不能制造手机消费或重复观看次数。"""
    row = _progress(1, _app(), rounds=7)
    row["data"]["activity"] = ActivityState.model_validate({**_app(), "kind":"wait", "target_id":None,
                                                         "data":{}}).model_dump(mode="json")
    row = Event.model_validate(row).model_dump(mode="json")
    with pytest.raises(IntegrityError, match="Content consumption must belong to a use_app activity"):
        project_app_usage([row])
    with pytest.raises(IntegrityError, match="Content consumption must belong to a use_app activity"):
        app_usage_quality([row], "UTC")


@pytest.mark.parametrize("source_kind,visibility", [
    (source, visibility) for source in ("environment", "model", "scenario", "external")
    for visibility in ("agent", "evaluation", "system") if (source, visibility) != ("environment", "system")
])
def test_push_expiry_rejects_forged_run_end_provenance(source_kind, visibility, caplog):
    """@brief 私有或模型结束事件不能成为推送失效的真实会话边界。"""
    row = _event(2, "run_ended", {"end_time":START + 300}, at=START + 300)
    row.update(source_kind=source_kind, visibility=visibility)
    row = Event.model_validate(row).model_dump(mode="json")
    for project in (project_app_usage, lambda events: app_usage_quality(events, "UTC")):
        with pytest.raises(IntegrityError, match="Run end must be a system-visible environment event"):
            project([_push(1), row])
    assert "Run end must be a system-visible environment event" in caplog.text
