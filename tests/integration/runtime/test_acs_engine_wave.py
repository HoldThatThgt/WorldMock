"""@brief 活动恢复、播报机会与邮件正文在真实引擎和宿主请求中的边界。"""

from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from pydantic import ValidationError
from tests.system.lifecycle.test_external_environment_lifecycle import _identity
from tests.life.conftest import (activity_name, advance_next, conclude_request, pause_request, report_request,
                                 start_activity, submit_round, wake_by_mail)
from worldmock import run_setup, runtime
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.models import canonical_json, sha256_json
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention

import json
import shutil
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.catalog.conftest import edit_row
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.life.conftest import (advance_next, conclude_request, context, pause_request, report_request,
                                 stage_day_plan, submit_round, wake_by_mail)
from tests.life.scenario_fixtures import run_identity
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from tests.life.work_runtime import at_workplace, trip_to
from worldmock.life.activity_control import is_pending_conclusion
from worldmock.life.models import SetAlarmRequest, StartActivityRequest
from worldmock.life.sessions import current_session


@pytest.fixture
def wave_engine(tmp_path):
    """@brief 人工目录副本只缩短已分配工作，并延长真正占名额的内容。"""
    from worldmock.life.engine import LifeEngine

    root = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    edit_row(root / "catalog", "contents.json", "topic-sales", {"duration_seconds": 120})
    for identity in ("c-rest", "c-call", "c-course"):
        edit_row(root / "catalog", "contents.json", identity, {"duration_seconds": 86400})
    path = root / "scenarios/single-person.json"
    document = json.loads(path.read_bytes())
    document["max_same_moment_decisions"] = 32
    for shift in document["agents"][0]["work_schedule"]["weekly"].values():
        if shift is not None:
            shift.update(starts=540, ends=542)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    engine = LifeEngine.create(path, tmp_path / "run", run_identity(document, "engine-wave"))
    stage_day_plan(engine, submitted=True)
    yield engine
    engine.close()


def companions(engine, label):
    """@brief 两个真实动作的占用不相冲突，同时填满人物两个名额。"""
    alarm = perform(engine, f"{label}-alarm", alarm_at(engine, 10), "set_alarm")
    assert alarm.error_code is None, alarm.data
    value = StartActivityRequest(items=[request("学课程", "哔哩哔哩").items[0],
        request("语音通话", "微信", to="联系人：王磊").items[0]], answering=None)
    result = perform(engine, label, value)
    assert result.error_code is None, result.data
    return tuple(item for item in engine.context.state.activities.values()
                 if item.origin_action_id == result.action_id)


def paused_work(engine):
    """@brief 到公司后实际开始并暂停已分配内容，不直接构造活动状态。"""
    at_workplace(engine)
    alarm = perform(engine, "pause-boundary-alarm", alarm_at(engine, 10), "set_alarm")
    assert alarm.error_code is None, alarm.data
    started = perform(engine, "work-first", request("办公", None))
    assert started.error_code is None, started.data
    activity = engine.context.state.activities[started.activity_id]
    paused = perform(engine, "work-pause", pause_request(activity.name, summary="先核对了季度总额。"), "pause_activity")
    assert paused.error_code is None, paused.data
    return activity.activity_id


def resume_result(engine, identity, label, summary="继续。"):
    """@brief 真实恢复请求的拒绝与人物待办层可用性必须一致。"""
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    name = engine.context.state.activities[identity].name
    observed = engine.observe(context(engine, f"{label}-preview"))
    row = next(item for item in observed["todo_layer"]["paused_activities"] if item["name"] == name)
    offered = next(item for item in row["tools"] if item["tool"] == "report_status")
    result = perform(engine, label, report_request(name, summary=summary), "report_status")
    assert offered["available"] == (result.error_code is None)
    if result.error_code is not None:
        assert offered["reason"] == result.data["reason"]
    return result


def visible_name(engine, identity):
    """@brief 本轮可读名称在同秒同动作重名时含真实去重后缀。"""
    observed = engine.observe(context(engine, f"name-{identity}"))
    return next(item["name"] for item in observed["activity_bindings"] if item["activity_id"] == identity)


def test_real_resume_checks_location_then_completed_assignment_before_slots_and_claims(wave_engine):
    engine = wave_engine
    identity = paused_work(engine)
    other = perform(engine, "work-second", request("办公", None))
    assert other.error_code is None, other.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[other.activity_id]))
    ended = perform(engine, "work-complete", conclude_request(visible_name(engine, other.activity_id)),
                    "conclude_activity")
    assert ended.error_code is None, ended.data
    assert engine.context.state.agents[1].content_progress["topic-sales"].completed_rounds == 1
    trip_to(engine, "home", "home-trip")
    occupied = companions(engine, "home-companions")
    assert resume_result(engine, identity, "wrong-location").error_code == "ACTIVITY_PRECONDITION_UNMET"
    ended = perform(engine, "end-home-companions", conclude_request(*(item.name for item in occupied)),
                    "conclude_activity")
    assert ended.error_code is None, ended.data
    trip_to(engine, "office", "office-trip")
    companions(engine, "office-companions")
    assert resume_result(engine, identity, "completed-content").error_code == "CONTENT_EXHAUSTED"
    assert engine.context.state.activities[identity].status == "paused"
    assert engine.context.state.agents[1].content_progress["topic-sales"].completed_rounds == 1


def test_real_resume_checks_slots_before_claims_when_location_and_assignment_are_eligible(wave_engine):
    engine = wave_engine
    identity = paused_work(engine)
    occupied = companions(engine, "full-slots")
    assert resume_result(engine, identity, "resume-full").error_code == "SESSION_LIMIT_EXCEEDED"
    call = next(item for item in occupied if item.data["category"] == "social")
    ended = perform(engine, "end-call", conclude_request(call.name), "conclude_activity")
    assert ended.error_code is None, ended.data
    assert resume_result(engine, identity, "resume-conflicting").error_code == "RESOURCE_CONFLICT"
    rest = next(item for item in occupied if item.data["category"] == "work_study")
    paused = perform(engine, "pause-rest", pause_request(rest.name), "pause_activity")
    assert paused.error_code is None, paused.data
    before = engine.context.state.activities[identity].model_copy(deep=True)
    assert resume_result(engine, identity, "resume-clear").error_code is None
    current = engine.context.state.activities[identity]
    assert current.status == "active"
    assert current.data == {**before.data, "start_position_seconds": before.media_position_seconds}
    assert current.media_position_seconds == before.media_position_seconds


def test_real_paused_work_keeps_its_last_report_across_departure_return_and_resume(wave_engine):
    engine = wave_engine
    identity = paused_work(engine)
    paused = engine.context.state.activities[identity].model_copy(deep=True)
    trip_to(engine, "home", "reported-work-home")
    trip_to(engine, "office", "reported-work-office")
    assert engine.context.state.activities[identity].progress_note == paused.progress_note
    assert resume_result(engine, identity, "reported-work-resume", paused.progress_note).error_code is None
    current = engine.context.state.activities[identity]
    assert current.progress_note == paused.progress_note == "先核对了季度总额。"
    assert current.media_position_seconds == paused.media_position_seconds
    assert current.data["content_id"] == paused.data["content_id"]
    assert len(current.session_ids) == 2 and current.session_ids[0] != current.session_ids[1]
    observed = engine.observe(context(engine, "reported-work-view"))
    target = next(item for item in observed["tool_layer"]["targets"]
                  if item["kind"] == "activity" and item["name"] == visible_name(engine, identity))
    assert target["last_report"] == paused.progress_note


def test_unknown_action_name_has_the_same_explicit_field_error_in_host_and_actual_environment(wave_engine):
    from agentsociety2.agent.base import ReactDecision
    from tests.integration.life.test_tool_layer import _host
    from worldmock.agents.life_feedback import feedback_report

    engine = wave_engine
    observed = engine.observe(context(engine, "unknown-action-preview"))
    person = _host(engine, context(engine, "unknown-action-host"), observed)
    arguments = request("不存在的动作", None).model_dump(mode="json")
    error = person.validate_react_decisions([ReactDecision("", "start_activity", arguments, "")], False)
    report = feedback_report(error).payload()
    result = perform(engine, "unknown-action-environment", StartActivityRequest.model_validate(arguments))
    assert result.error_code is not None
    host = report["items"][0]
    actual = result.data["items"][0]
    assert host["error_code"] == actual["error_code"] == "NAME_NOT_FOUND"
    assert host["written"] == actual["written"] == "不存在的动作"
    assert host["why"] == actual["why"]


def alarm_at(engine, seconds):
    """@brief 使用真实时区的未来闹钟参数，不修改世界时间或活动锚点。"""
    now = engine.context.state.current_time
    return SetAlarmRequest(at=datetime.fromtimestamp(now + seconds, engine.context.scenario.start_at.tzinfo).isoformat())


def test_real_report_opportunity_consumes_every_due_anchor_when_the_person_sets_an_alarm(wave_engine):
    engine = wave_engine
    value = StartActivityRequest(items=[request("学课程", "哔哩哔哩").items[0],
        request("语音通话", "微信", to="联系人：王磊").items[0]], answering=None)
    started = perform(engine, "parallel-course-call", value)
    assert started.error_code is None, started.data
    identities = tuple(item.activity_id for item in engine.context.state.activities.values()
                       if item.origin_action_id == started.action_id)
    assert len(identities) == 2
    anchors = {identity: engine.context.state.activities[identity].report_anchor_at for identity in identities}
    for step in range(80):
        ready = engine.scheduling_state().ready_agent_ids
        if ready and "progress_report_due" in engine.context.state.agents[1].pending_wake_reasons:
            break
        if ready:
            _, results = submit_round(engine, {1: ("set_alarm", alarm_at(engine, 4000))}, f"before-due-{step}")
            assert results[0].error_code is None, results[0].data
        else:
            advance_next(engine)
    else:
        raise AssertionError("Actual progress report opportunity was not reached")
    now = engine.context.state.current_time
    assert all(now - anchor >= 1800 for anchor in anchors.values())
    assert all(engine.context.state.activities[identity].report_anchor_at == anchors[identity]
               for identity in identities)
    _, results = submit_round(engine, {1: ("set_alarm", alarm_at(engine, 4000))}, "choose-alarm-at-report")
    assert results[0].error_code is None, results[0].data
    assert all(engine.context.state.activities[identity].report_anchor_at == now for identity in identities)
    assert "progress_report_due" not in engine.context.state.agents[1].pending_wake_reasons


@pytest.mark.asyncio
async def test_real_arrived_mail_body_occurs_once_in_the_actual_host_model_request(wave_engine, monkeypatch):
    from agentsociety2.agent.base import ReactLoopRequest
    from tests.integration.runtime.test_seven_layer_request import _GroundedCorrectionDispatcher, _ModelResponse
    from tests.integration.life.test_tool_layer import _host
    from worldmock.life import prompt_capacity

    engine = wave_engine
    started = perform(engine, "wait-mail", request("自己", None, "等待"))
    assert started.error_code is None, started.data
    at = wake_by_mail(engine, offset=60)
    pump(engine, lambda: engine.context.state.current_time >= at)
    assert "test-mail" in engine.context.state.agents[1].visible_message_ids
    decision = context(engine, "mail-request")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    dispatcher = _GroundedCorrectionDispatcher(responses=[_ModelResponse("alarm", "set_alarm",
        json.dumps(alarm_at(engine, 300).model_dump(mode="json")))])
    agent = _host(engine, decision, observed)
    agent._dispatcher, agent._model_name, agent._enable_memory = dispatcher, "deepseek-flash", False
    agent.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)
    await agent.run_react_loop(ReactLoopRequest(0, decision.observed_at,
        [{"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    results = engine.commit_decision_round(decision.round_id)
    assert results[0].error_code is None, results[0].data
    assert len(dispatcher.requests) == 1
    message = engine.context.scenario.messages[0]
    wire = json.dumps(dispatcher.requests[0]["messages"], ensure_ascii=False)
    assert wire.count(message.body) == 1
    assert wire.count(message.subject) == 1
    requests = engine.context.store.read_agent_events("model_request", 1)
    assert len([event for event in requests if event.decision_id == decision.decision_id]) == 1


def morning_window(document):
    """@brief 真正跨过首个预排推送时刻，再完成一段带导航的实际出行。"""
    document.update(start_at="2026-01-05T07:50:00+08:00", end_at="2026-01-05T08:30:00+08:00",
                    messages=[], payrolls=[], bills=[])


@pytest.mark.parametrize("travel_first", [True, False])
def test_real_navigation_and_action_batch_selects_once_in_new_road_session_and_exports(
        engine_factory, tmp_path, monkeypatch, travel_first):
    from tests.integration.life.test_acs_amended_runtime import checkpoint
    from worldmock.footprints.export import export_run
    from worldmock.footprints.validation import validate_export
    from worldmock.life import action_run

    engine = engine_factory(mutate=morning_window)
    started = perform(engine, "wait-first-push", request("自己", None, "等待"))
    assert started.error_code is None, started.data
    pump(engine, lambda: bool(current_session(engine.context.state, 1).app_pushes))
    ended = perform(engine, "end-before-road", conclude_request(visible_name(engine, started.activity_id)),
                    "conclude_activity")
    assert ended.error_code is None, ended.data
    old_session = current_session(engine.context.state, 1)
    app_id, push = next(iter(old_session.app_pushes.items()))
    catalog = engine.context.semantics.catalog
    action_id = catalog.contents[push.content_id].action_id
    destination = catalog.locations["office"].display_name
    travel = request(f"地点：{destination}", "高德地图", "步行").items[0]
    action = request(catalog.actions[action_id].name, catalog.apps[app_id].name).items[0]
    items = [travel, action] if travel_first else [action, travel]
    calls, select = [], action_run.select_content

    def selected(catalog, scenario, state, query, stream):
        calls.append((query.activity_id, current_session(state, query.agent_id).session_id))
        return select(catalog, scenario, state, query, stream)

    monkeypatch.setattr(action_run, "select_content", selected)
    result = perform(engine, "road-and-content", StartActivityRequest(items=items, answering=None))
    assert result.error_code is None, result.data
    current = current_session(engine.context.state, 1)
    activity = next(item for item in engine.context.state.activities.values()
                    if item.origin_action_id == result.action_id and item.kind == "action")
    assert current.kind == "transit" and current.session_id != old_session.session_id
    assert calls == [(activity.activity_id, current.session_id)]
    assert activity.session_ids == [current.session_id]
    assert activity.data["pinned_by_push_id"] is None and not current.app_pushes
    assert engine.context.state.sessions[old_session.session_id].app_pushes[app_id].status == "pending"
    monkeypatch.setattr(action_run, "select_content", select)
    pump(engine, lambda: engine.context.state.current_time == engine.context.state.end_time)
    point = checkpoint(engine)
    directory = tmp_path / "road-export"
    manifest = export_run(engine.context.store, point.last_event_seq, directory)
    assert validate_export(directory)[0] == manifest


@pytest.mark.parametrize("asleep", [False, True])
def test_real_face_chat_invites_awake_partner_immediately_and_never_wakes_sleeping_partner(engine_factory, asleep):
    """@brief 共同在场的真实请求只邀请清醒对方；睡眠拒绝不制造送达或唤醒。"""
    engine = engine_factory("multi-person.json")
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    now = engine.context.state.current_time
    second = request("自己", None, "睡觉" if asleep else "等待")
    submit_round(engine, {1: ("set_alarm", alarm_at(engine, 10)),
        2: ("start_activity", second)}, "establish-partner-state")
    first = perform(engine, "invite-real", request("聊天", None, to="Second Test Person"))
    messages = engine.context.store.read_agent_events("environment_event", 2)
    arrivals = [event for event in messages if event.data.get("kind") == "message_arrival"]
    if asleep:
        assert first.status == "rejected" and first.error_code == "CHAT_PARTNER_NOT_AVAILABLE"
        assert arrivals == [] and 2 not in engine.scheduling_state().ready_agent_ids
        sleeping = next(row for row in engine.context.state.activities.values() if row.agent_id == 2)
        assert sleeping.kind == "sleep" and sleeping.status == "active" and sleeping.elapsed_seconds == 0
    else:
        assert first.error_code is None
        assert len(arrivals) == 1 and arrivals[0].occurred_at == now
        assert 2 in engine.scheduling_state().ready_agent_ids
        assert "message_arrival" in engine.context.state.agents[2].pending_wake_reasons
    assert engine.context.state.current_time == now


def test_real_chat_partner_ending_requires_both_conclusions_before_time_advances(engine_factory):
    """@brief 实际接受邀请、同秒结束双方并逐项收尾，内容不凭零秒开始而增加。"""
    engine = engine_factory("multi-person.json")
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    now = engine.context.state.current_time
    submit_round(engine, {1: ("set_alarm", alarm_at(engine, 10)),
        2: ("start_activity", request("自己", None, "等待"))}, "chat-initial")
    first = perform(engine, "chat-start", request("聊天", None, to="Second Test Person"))
    mine = first.activity_id
    accepted = perform(engine, "chat-accept", request("聊天", None, to="Test Person"), identity=2)
    assert accepted.error_code is None and engine.context.state.current_time == now
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    boundary = engine.context.state.current_time
    assert boundary == now + 10
    other = engine.context.state.activities[accepted.activity_id]
    ended = perform(engine, "chat-end", conclude_request(engine.context.state.activities[mine].name),
                    "conclude_activity")
    assert ended.error_code is None and engine.context.state.current_time == boundary
    partner = engine.context.state.activities[other.activity_id]
    assert partner.boundary_type == "partner_ended" and is_pending_conclusion(partner)
    assert 2 in engine.scheduling_state().ready_agent_ids
    before = {identity: person.content_progress for identity, person in engine.context.state.agents.items()}
    done = perform(engine, "chat-partner-conclude", conclude_request(partner.name), "conclude_activity", identity=2)
    assert done.error_code is None and engine.context.state.current_time == boundary
    assert engine.context.state.activities[partner.activity_id].concluded_at == boundary
    assert {identity: person.content_progress for identity, person in engine.context.state.agents.items()} == before
    assert all(person.content_progress["c-chat"].position_seconds == 10
               for person in engine.context.state.agents.values())


ROOT = Path(__file__).resolve().parents[3]


def _new_engine(tmp_path, shared_content: bool = False):
    """@brief 真实新建隔离环境，身份指纹取当前生产规则而非手写摘要。"""
    root = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    if shared_content:
        contents_path = root / "catalog/contents.json"
        document = json.loads(contents_path.read_text(encoding="utf-8"))
        document["contents"] = [item for item in document["contents"]
                                if item["action_id"] != "browse-feed" or item["content_id"] == "c-long"]
        contents_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        refresh_manifest_identity(root / "catalog")
    path = root / "scenarios/single-person.json"
    engine = LifeEngine.create(path, tmp_path / "parent", _identity(path), run_setup.stage_model_identity())
    return path, engine


def _checkpoint(engine, tmp_path):
    """@brief 用符合真实人物恢复合同的工作区生成共同检查点与内容寻址素材。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENT.json").write_bytes(canonical_json({
        "schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1,
        "profile": {"same_second_continuation": True}, "initialized_at": None,
    }))
    (workspace / "config.json").write_bytes(canonical_json({
        "enable_memory": True, "max_react_turns": run_setup.MAX_REACT_TURNS,
    }))
    return engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})


def _resume_request(path, engine, checkpoint, destination):
    """@brief 直接构造真实恢复入口的输入，父库始终保持独立。"""
    return runtime.ResumeRequest(ROOT, path, engine.context.store.run_dir, checkpoint.checkpoint_id, destination)


def _replace_checkpoint_state(store, checkpoint, state):
    """@brief 反例保持数据库记录摘要和事件身份有效，只改变待校验的世界结构。"""
    row = store.connection.execute("SELECT body FROM checkpoints WHERE checkpoint_id=?",
                                   (checkpoint.checkpoint_id,)).fetchone()
    payload = json.loads(row[0])
    payload["state"] = state
    with store.connection:
        store.connection.execute("DROP TRIGGER checkpoints_no_update")
        store.connection.execute("UPDATE checkpoints SET body=?,record_sha256=? WHERE checkpoint_id=?",
                                 (canonical_json(payload).decode(), sha256_json(payload), checkpoint.checkpoint_id))
    assert store.get_checkpoint(checkpoint.checkpoint_id).state == state


def test_real_create_checkpoint_and_resume_preserve_world_and_parent(tmp_path):
    """@brief 当前世界能实际新建、封存和恢复，子分支保留状态并独立写入。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        parent_before = engine.context.state.model_dump(mode="json")
        destination = tmp_path / "child"
        prepared = runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination))
        child = LifeEngine.open(path, prepared.run_dir)
        try:
            assert child.context.state.schema_version == 11
            assert child.context.state.current_time == engine.context.state.current_time
            assert child.context.state.agents == engine.context.state.agents
            assert child.context.state.run_id != engine.context.state.run_id
            assert engine.context.state.model_dump(mode="json") == parent_before
        finally:
            child.close()
    finally:
        engine.close()


def test_real_open_rejects_world_structure_ten(tmp_path):
    """@brief 打开入口从真实 SQLite 世界读取旧结构，并由严格模型拒绝。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    state = engine.context.state.model_dump(mode="json")
    state["schema_version"] = 10
    with engine.context.store.connection:
        engine.context.store.connection.execute("UPDATE world_state SET body=? WHERE singleton=1",
                                                (canonical_json(state).decode(),))
    engine.close()
    with pytest.raises(ValidationError, match="schema_version"):
        LifeEngine.open(path, run_dir)


@pytest.mark.parametrize("replay", [False, True])
def test_real_restore_and_replay_preparation_reject_world_ten_before_publication(tmp_path, replay):
    """@brief 恢复和重放共用的真实入口在发布分支前拒绝记录校验已通过的旧世界。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        state = checkpoint.state.copy()
        state["schema_version"] = 10
        _replace_checkpoint_state(engine.context.store, checkpoint, state)
        destination = tmp_path / "child"
        intervention = CounterfactualIntervention(
            kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="核对余额影响",
            source_event_ids=(engine.context.store.read_events(checkpoint.last_event_seq)[0].event_id,),
        ) if replay else None
        with pytest.raises(ValidationError, match="schema_version"):
            runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination), intervention)
        assert not destination.exists()
        assert not list(tmp_path.glob(".worldmock-resume-*"))
    finally:
        engine.close()


def test_real_replay_entry_rejects_world_ten_before_model_runtime(tmp_path):
    """@brief 独立进程通过公开重放入口拒绝旧世界，锁定依赖和进程配置完整执行。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        state = checkpoint.state.copy()
        state["schema_version"] = 10
        _replace_checkpoint_state(engine.context.store, checkpoint, state)
        program = """
import asyncio, sys
from pathlib import Path
from pydantic import ValidationError
from worldmock import run_setup
run_setup._read_deepseek_key = lambda: 'offline-test-key'
run_setup._configure_process(Path(sys.argv[1]))
from worldmock import runtime
from worldmock.life.models import CounterfactualIntervention
request = runtime.CounterfactualRequest(
    runtime.ResumeRequest(*(Path(value) if index != 3 else value
                            for index, value in enumerate(sys.argv[1:]))),
    CounterfactualIntervention(kind='set_balance_fen', agent_id=1, balance_fen=0,
                              hypothesis='Check balance effects', source_event_ids=('test-source',)))
try:
    asyncio.run(runtime.replay_world(request))
except ValidationError as error:
    assert 'schema_version' in str(error)
    print('World 10 rejected before publication')
else:
    raise AssertionError('World 10 was accepted')
"""
        destination = tmp_path / "replay"
        result = subprocess.run(
            [sys.executable, "-c", program, str(ROOT), str(path), str(engine.context.store.run_dir),
             checkpoint.checkpoint_id, str(destination)], cwd=ROOT, env=os.environ.copy(),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "World 10 rejected before publication" in result.stdout
        assert not destination.exists()
    finally:
        engine.close()


def test_real_resume_rejects_same_version_changed_catalog_before_publication(tmp_path):
    """@brief 目录按原环境版本重新发布后仍因实际字节身份变化拒绝恢复。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        catalog = path.parent.parent / "catalog"
        goods = catalog / "goods.json"
        goods.write_bytes(goods.read_bytes() + b" ")
        refresh_manifest_identity(catalog)
        destination = tmp_path / "child"
        with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
            runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination))
        assert not destination.exists()
    finally:
        engine.close()


def test_real_open_rejects_negative_driving_days(tmp_path):
    """@brief 真实恢复状态的驾车可用日余额为负时在构造期拒绝，不进入人物决策。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    state = engine.context.state.model_dump(mode="json")
    state["agents"]["1"]["driving_days"] = -1
    with engine.context.store.connection:
        engine.context.store.connection.execute("UPDATE world_state SET body=? WHERE singleton=1",
                                                (canonical_json(state).decode(),))
    engine.close()
    with pytest.raises(ValidationError, match="driving_days"):
        LifeEngine.open(path, run_dir)


def _progress_until(engine, target):
    """@brief 只经实际请求和排程边界推进，等待目标时刻不会改写世界时钟。"""
    from worldmock.life.activity_control import pending_conclusions
    from worldmock.life.models import active_activities

    for step in range(2000):
        if engine.context.state.current_time >= target:
            return
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            advance_next(engine)
            continue
        requests = {}
        for agent_id in ready:
            pending = pending_conclusions(engine.context.state, agent_id)
            active = active_activities(engine.context.state, agent_id)
            if pending:
                requests[agent_id] = ("conclude_activity", conclude_request(*(item.name for item in pending)))
            elif active:
                requests[agent_id] = ("report_status", report_request(*(item.name for item in active)))
            else:
                requests[agent_id] = ("start_activity", start_activity("等待", "自己"))
        _, results = submit_round(engine, requests, f"wave-{target}-{step}")
        assert all(result.status in {"completed", "in_progress"} for result in results)
    raise AssertionError("Engine did not reach the requested event boundary")


def test_real_app_interleaving_uses_shared_content_cursor_and_reopens_it(tmp_path):
    """@brief 两个应用交错消费同一长视频，暂停与真实重开都从全局最新游标继续。"""
    path, engine = _new_engine(tmp_path, shared_content=True)
    run_dir = engine.context.store.run_dir
    try:
        _, results = submit_round(engine, {1: ("start_activity", start_activity(
            "进行", "刷推荐", app=["抖音"]))}, "first-app")
        first = results[0].activity_id
        assert results[0].status == "in_progress" and engine.context.state.activities[first].data["content_id"] == "c-long"
        target = wake_by_mail(engine, offset=120)
        _progress_until(engine, target)
        _, results = submit_round(engine, {1: ("pause_activity", pause_request(activity_name(engine, first)))}, "pause-first")
        assert results[0].status == "completed"
        _, results = submit_round(engine, {1: ("start_activity", start_activity(
            "进行", "刷推荐", app=["哔哩哔哩"]))}, "second-app")
        second = results[0].activity_id
        assert engine.context.state.activities[second].media_position_seconds == 120
        target = wake_by_mail(engine, offset=60)
        _progress_until(engine, target)
        submit_round(engine, {1: ("pause_activity", pause_request(activity_name(engine, second)))}, "pause-second")
        _, results = submit_round(engine, {1: ("report_status", report_request(activity_name(engine, first)))}, "resume-first")
        assert results[0].status == "completed"
        assert engine.context.state.activities[first].media_position_seconds == 180
        checkpoint = _checkpoint(engine, tmp_path)
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == checkpoint.state
        assert engine.context.state.activities[first].media_position_seconds == 180
        assert engine.context.state.agents[1].content_progress["c-long"].position_seconds == 180
    finally:
        engine.close()


def test_real_app_push_is_persisted_without_reissue_after_open(tmp_path):
    """@brief 真实时钟触发会话推送，保存重开后事件与未消费推送均不重复。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    try:
        target = min(event.due_at for event in engine.context.state.pending_events if event.kind == "app_push")
        _progress_until(engine, target)
        pushed = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                  if event.data.get("kind") == "app_push" and "push_id" in event.data]
        assert pushed and len({event.data["push_id"] for event in pushed}) == len(pushed)
        checkpoint = _checkpoint(engine, tmp_path)
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == checkpoint.state
        _progress_until(engine, target + 1)
        restored = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                    if event.data.get("kind") == "app_push" and "push_id" in event.data]
        assert [event.event_id for event in restored] == [event.event_id for event in pushed]
    finally:
        engine.close()


def test_real_published_route_and_store_purchase_emits_receipt_without_holding(tmp_path):
    """@brief 人物沿发布图到店购买，真实活动保存路径并只产生收货事实。"""
    from tests.life.work_runtime import trip_to

    path, engine = _new_engine(tmp_path)
    try:
        trip_to(engine, "noodle-2", "visit-store")
        trip = next(item for item in engine.context.state.activities.values() if item.kind == "travel")
        catalog = engine.context.semantics.catalog
        assert trip.data["path"] == list(catalog.graph.path("home", "noodle-2"))
        assert trip.data["distance_meters"] == catalog.graph.distance_meters("home", "noodle-2")
        _, results = submit_round(engine, {1: ("start_activity", start_activity("购买", "牛肉面"))}, "buy-food")
        assert results[0].status == "completed", results[0].error_code
        receipts = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                    if event.data.get("kind") == "goods_received" and event.data.get("goods_id") == "beef-noodles"]
        assert len(receipts) == 1 and receipts[0].agent_id == 1
        assert receipts[0].data["category"] == "meal" and receipts[0].data["provided"] == 600
        assert 0 < receipts[0].data["applied"] <= receipts[0].data["provided"]
        state = engine.context.state.model_dump(mode="json")
        assert "inventory" not in state["agents"]["1"] and "contact_holdings" not in state
    finally:
        engine.close()
