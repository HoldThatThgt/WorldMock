"""地点会话与活动生命周期字段的合同测试（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章「会话与活动的字段」）。

涵盖：会话是一段地点停留（stay）或一段路（transit），状态闭集 active/ended、种类与地点字段配对、
删除的会话字段按未知字段拒绝；活动新增的 concluded_at（只属于 completed/failed/cancelled 且不早于 ended_at）、
report_anchor_at、session_ids、name 与从会话移来的任务关联和自述字段；WorldState 拒绝同一人物的两个进行中会话、
指向未知会话的活动与指向未知活动的会话；以及 schema_version 升版后旧检查点被拒绝的验收要求。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.life.conftest import stage_day_plan, start_activity, submit_round
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.activities import change_activity
from worldmock.life.models import (
    WORLD_SCHEMA_VERSION,
    ActivityState,
    ActivityStatus,
    AgentState,
    ChangeActivityRequest,
    SessionKind,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    WorldState,
)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _active_session(**overrides) -> dict:
    """构造最小合法的进行中停留会话数据字典。"""
    base = {
        "session_id": "session-1-0001",
        "agent_id": 1,
        "kind": "stay",
        "location_id": "home",
        "status": "active",
        "started_at": 1000,
        "name": "家（1月5日 00:00 起）",
    }
    base.update(overrides)
    return base


def _ended_transit(**overrides) -> dict:
    """构造最小合法的已结束路上会话数据字典。"""
    base = {
        "session_id": "session-1-0002",
        "agent_id": 1,
        "kind": "transit",
        "origin_id": "home",
        "destination_id": "office",
        "status": "ended",
        "started_at": 1000,
        "ended_at": 2000,
        "name": "去张江办公室的路上（1月5日 14:17 起）",
    }
    base.update(overrides)
    return base


def _activity(**overrides) -> dict:
    """构造最小合法的进行中等待活动数据字典。"""
    base = {
        "activity_id": "activity-1",
        "agent_id": 1,
        "location_id": "home",
        "kind": "wait",
        "status": "active",
        "started_at": 1000,
        "planned_end_at": 1900,
        "last_settled_at": 1000,
        "elapsed_seconds": 0,
        "planned_duration_seconds": 900,
        "origin_action_id": "action-1",
        "origin_decision_id": "decision-1",
        "origin_request_event_id": "event-1",
    }
    base.update(overrides)
    return base


def _ended_activity(status: str, **overrides) -> dict:
    """构造在 1900 秒结束的终态活动数据字典。"""
    return _activity(status=status, ended_at=1900, last_settled_at=1900, elapsed_seconds=900, **overrides)


# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------


def test_world_schema_version_is_8():
    """WORLD_SCHEMA_VERSION 当前为 9（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 ：新增个人事务的人物状态，世界结构由 8 升为 9，
    只升一次）。用例名沿用版本 8 时的登记（时间与能量台账登记了这个节点），断言随结构版本更新。"""
    assert WORLD_SCHEMA_VERSION == 9


def test_session_opened_by_action_id_is_optional_and_nonblank():
    """SessionState.opened_by_action_id 缺省为 None（运行开始与到达）；由决策引起的切换记下非空的动作身份。"""
    stay = {"session_id": "session-1-0001", "agent_id": 1, "kind": "stay", "location_id": "home",
            "status": "active", "started_at": 1000, "name": "家（1月5日 00:00 起）"}
    assert SessionState.model_validate(stay).opened_by_action_id is None
    assert SessionState.model_validate({**stay, "opened_by_action_id": "action-9"}).opened_by_action_id == "action-9"
    with pytest.raises(ValidationError, match="opened_by_action_id"):
        SessionState.model_validate({**stay, "opened_by_action_id": ""})


def test_activity_paused_at_is_written_on_pause_and_cleared_on_resume():
    """ActivityState.paused_at 缺省为 None；暂停写入当前时刻，恢复清空（待办层按它排序）。"""
    active = ActivityState.model_validate(_activity())
    assert active.paused_at is None
    agent = AgentState.model_construct(agent_id=1)
    decision = active.model_copy(update={"kind": "wait", "next_interaction_at": 1300})
    _, paused = change_activity(agent, decision, ChangeActivityRequest(
        activity_id=active.activity_id, operation="pause"), 1300, 900)
    assert (paused.status, paused.paused_at) == ("paused", 1300)
    _, resumed = change_activity(agent, paused, ChangeActivityRequest(
        activity_id=active.activity_id, operation="resume"), 1600, 900)
    assert (resumed.status, resumed.paused_at) == ("active", None)


# ---------------------------------------------------------------------------
# 枚举闭集
# ---------------------------------------------------------------------------


def test_session_status_closed_set():
    """会话状态闭集只有 active 与 ended；paused、concluded、aborted 随地点会话删除。"""
    assert {s.value for s in SessionStatus} == {"active", "ended"}


def test_session_kind_closed_set():
    """会话种类闭集：一段地点停留 stay 或两地之间的一段路 transit。"""
    assert {k.value for k in SessionKind} == {"stay", "transit"}


def test_session_task_kind_closed_set():
    """任务关联类型闭集（已移到活动上）恰为三个合法成员。"""
    assert {k.value for k in SessionTaskKind} == {"goal", "work_task", "none"}


@pytest.mark.parametrize("bad_status", ["running", "done", "pending", "", "paused", "concluded", "aborted"])
def test_invalid_status_is_rejected(bad_status):
    """非法状态字符串被拒绝；旧会话状态 paused、concluded、aborted 同样不再合法。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(status=bad_status))


@pytest.mark.parametrize("bad_kind", ["task", "daily", "plan", ""])
def test_invalid_task_kind_is_rejected(bad_kind):
    """活动上的非法 task_kind 字符串被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(task_kind=bad_kind))


# ---------------------------------------------------------------------------
# 种类与地点字段配对
# ---------------------------------------------------------------------------


def test_stay_session_carries_only_its_location():
    """停留会话带 location_id，不带出发地与目的地。"""
    session = SessionState.model_validate(_active_session())
    assert session.kind == SessionKind.STAY and session.location_id == "home"
    assert session.origin_id is None and session.destination_id is None


def test_transit_session_carries_origin_and_destination():
    """路上会话带 origin_id 与 destination_id，不带 location_id。"""
    session = SessionState.model_validate(_ended_transit())
    assert session.kind == SessionKind.TRANSIT
    assert (session.origin_id, session.destination_id, session.location_id) == ("home", "office", None)


@pytest.mark.parametrize("overrides", [
    {"location_id": None},
    {"destination_id": "office"},
    {"origin_id": "home"},
    {"kind": "transit"},
])
def test_stay_place_fields_disagreeing_with_kind_are_rejected(overrides):
    """停留会话缺地点或混入路段字段、或标成路上却只带地点，都被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(**overrides))


@pytest.mark.parametrize("overrides", [
    {"destination_id": None},
    {"origin_id": None},
    {"location_id": "office"},
    {"kind": "stay"},
])
def test_transit_place_fields_disagreeing_with_kind_are_rejected(overrides):
    """路上会话缺出发地或目的地、混入 location_id、或标成停留，都被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_ended_transit(**overrides))


# ---------------------------------------------------------------------------
# task_kind 与 task_ref 配对约束（从原会话移到活动）
# ---------------------------------------------------------------------------


def test_task_kind_none_with_null_ref_is_valid():
    """活动缺省 task_kind=none 且 task_ref=None，通过验证。"""
    activity = ActivityState.model_validate(_activity())
    assert activity.task_kind == SessionTaskKind.NONE
    assert activity.task_ref is None and activity.plan_goal_intent_kind is None


def test_task_kind_none_with_non_null_ref_is_rejected():
    """task_kind=none 且 task_ref 有值时被拒绝。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(task_kind="none", task_ref="backend-hourly-2026-01-05"))


def test_task_kind_goal_requires_task_ref():
    """task_kind=goal 时 task_ref=None 被拒绝。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(task_kind="goal", task_ref=None))


def test_task_kind_goal_with_task_ref_is_valid():
    """task_kind=goal 且 task_ref 与目标意图一致时通过验证。"""
    activity = ActivityState.model_validate(
        _activity(task_kind="goal", task_ref="工作", plan_goal_intent_kind="工作"))
    assert activity.task_kind == SessionTaskKind.GOAL
    assert activity.task_ref == "工作"


def test_task_kind_goal_with_disagreeing_intent_is_rejected():
    """目标活动的意图必须等于 task_ref。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(task_kind="goal", task_ref="工作", plan_goal_intent_kind="休息"))


def test_task_kind_work_task_requires_task_ref():
    """task_kind=work_task 时 task_ref=None 被拒绝。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(task_kind="work_task", task_ref=None))


def test_task_kind_work_task_with_task_ref_is_valid():
    """task_kind=work_task 且 task_ref 有值通过验证。"""
    activity = ActivityState.model_validate(
        _activity(task_kind="work_task", task_ref="backend-hourly-2026-01-05"))
    assert activity.task_kind == SessionTaskKind.WORK_TASK
    assert activity.task_ref == "backend-hourly-2026-01-05"


# ---------------------------------------------------------------------------
# 会话生命周期：ended 必须有 ended_at，active 不得有
# ---------------------------------------------------------------------------


def test_active_without_ended_at_is_valid():
    """active 状态不含 ended_at 通过验证。"""
    session = SessionState.model_validate(_active_session())
    assert session.status == SessionStatus.ACTIVE
    assert session.ended_at is None


def test_ended_with_ended_at_is_valid():
    """ended 状态含 ended_at 通过验证。"""
    session = SessionState.model_validate(_ended_transit())
    assert session.status == SessionStatus.ENDED
    assert session.ended_at == 2000


def test_active_with_ended_at_is_rejected():
    """active 状态含 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(ended_at=2000))


def test_ended_without_ended_at_is_rejected():
    """ended 状态缺 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_ended_transit(ended_at=None))


def test_ended_at_before_started_at_is_rejected():
    """ended_at 早于 started_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_ended_transit(started_at=5000, ended_at=1000))


def test_active_to_ended_transition():
    """系统结束会话：active → ended 时同时写 ended_at，名称不改。"""
    session = SessionState.model_validate(_active_session())
    ended = session.model_copy(update={"status": SessionStatus.ENDED, "ended_at": 9999})
    assert ended.status == SessionStatus.ENDED and ended.ended_at == 9999
    assert ended.name == session.name


# ---------------------------------------------------------------------------
# 删除的会话字段按未知字段拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("removed", [
    ("task_name", "等待"), ("session_name", "等待（上午十点，家中）"), ("task_kind", "none"),
    ("task_ref", None), ("plan_goal_intent_kind", "工作"), ("targets", []), ("segments", []),
    ("invested_seconds", 0), ("effective_seconds", 0.0), ("remaining_contract_seconds", None),
    ("preconditions_unmet", []), ("progress_note", ""), ("note_updated_at", None), ("note_history", []),
])
def test_removed_session_fields_are_rejected(removed):
    """第七章字段表删除的会话字段不再被接受（extra=forbid），自述与任务关联改在活动上。"""
    key, value = removed
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SessionState.model_validate(_active_session(**{key: value}))


def test_extra_fields_are_rejected():
    """StrictModel 语义：未知字段被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(unknown_field="surprise"))


def test_full_session_state_from_spec():
    """按规格示例构造一段已结束的路上会话，全部字段正确解析。"""
    session = SessionState.model_validate(_ended_transit(
        session_id="session-1-0002", started_at=1767593820, ended_at=1767594840,
        activity_ids=["activity-travel", "activity-podcast"]))
    assert session.session_id == "session-1-0002"
    assert session.name == "去张江办公室的路上（1月5日 14:17 起）"
    assert session.activity_ids == ["activity-travel", "activity-podcast"]
    assert session.ended_at - session.started_at == 1020


# ---------------------------------------------------------------------------
# 活动的生命周期字段与移来的自述字段
# ---------------------------------------------------------------------------


def test_activity_lifecycle_field_defaults():
    """活动新增字段的缺省：未收尾、无播报锚点、未归入会话、无名称、无自述。"""
    activity = ActivityState.model_validate(_activity())
    assert activity.concluded_at is None and activity.report_anchor_at is None
    assert activity.session_ids == [] and activity.name == ""
    assert activity.progress_note == "" and activity.note_updated_at is None and activity.note_history == []


def test_activity_carries_name_sessions_anchor_and_notes():
    """活动记录可读名称、先后所属的会话、播报锚点与自述历史。"""
    activity = ActivityState.model_validate(_activity(
        name="等待（1月5日 00:00 起）", session_ids=["session-1-0001", "session-1-0003"], report_anchor_at=1500,
        progress_note="还在等消息。", note_updated_at=1500,
        note_history=[{"note": "先歇一下。", "updated_at": 1200}]))
    assert activity.name == "等待（1月5日 00:00 起）"
    assert activity.session_ids == ["session-1-0001", "session-1-0003"]
    assert activity.report_anchor_at == 1500
    assert activity.note_history[-1]["note"] == "先歇一下。"


def test_progress_note_at_500_chars_is_valid():
    """活动自述恰好 500 字符通过验证。"""
    activity = ActivityState.model_validate(_activity(progress_note="字" * 500))
    assert len(activity.progress_note) == 500


def test_progress_note_at_501_chars_is_rejected():
    """活动自述 501 字符被拒绝。"""
    with pytest.raises(ValidationError):
        ActivityState.model_validate(_activity(progress_note="字" * 501))


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_concluded_at_on_concludable_terminal_status_is_valid(status):
    """completed、failed、cancelled 的活动可以收尾，concluded_at 不早于 ended_at。"""
    pending = ActivityState.model_validate(_ended_activity(status))
    assert pending.concluded_at is None
    concluded = ActivityState.model_validate(_ended_activity(status, concluded_at=1900))
    assert concluded.concluded_at == concluded.ended_at == 1900
    assert ActivityState.model_validate(_ended_activity(status, concluded_at=1960)).concluded_at == 1960


@pytest.mark.parametrize("status", ["active", "paused"])
def test_concluded_at_on_running_activity_is_rejected(status):
    """进行中或暂停的活动没有收尾时刻（主动结束先转为 cancelled 再收尾）。"""
    with pytest.raises(ValidationError, match="concluded_at"):
        ActivityState.model_validate(_activity(status=status, concluded_at=1000))


def test_concluded_at_on_truncated_activity_is_rejected():
    """实验截止截断的活动不要求收尾，也不允许带收尾时刻。"""
    assert ActivityState.model_validate(_ended_activity("truncated")).concluded_at is None
    with pytest.raises(ValidationError, match="concluded_at"):
        ActivityState.model_validate(_ended_activity("truncated", concluded_at=1900))


def test_concluded_at_before_ended_at_is_rejected():
    """收尾不能早于活动结束。"""
    with pytest.raises(ValidationError, match="concluded_at"):
        ActivityState.model_validate(_ended_activity("completed", concluded_at=1899))


# ---------------------------------------------------------------------------
# WorldState.sessions 字段与 schema_version=9
# ---------------------------------------------------------------------------


def _minimal_world_state_dict(**overrides) -> dict:
    """构造最小合法 WorldState 字典（无代理、无库存）。"""
    base: dict = {
        "schema_version": 9,
        "version": 0,
        "current_time": 1000,
        "end_time": 2000,
        "run_id": "test-run",
        "timezone": "Asia/Shanghai",
        "scenario_sha256": "a" * 64,
        "content_manifest_sha256": "b" * 64,
        "agents": {},
        "accounts": {},
        "supply_stock": {},
        "supply_quotes": {},
        "opening_exceptions": {},
        "random_seed": 42,
        "random_state": "state",
    }
    base.update(overrides)
    return base


def test_world_state_schema_version_9_is_valid():
    """schema_version=9 的 WorldState 通过验证。"""
    ws = WorldState.model_validate(_minimal_world_state_dict())
    assert ws.schema_version == 9


#: 人物提示七层之前的世界结构版本；其检查点必须以版本不符拒绝。
_VERSION_7_WORLD_SCHEMA = 7
#: 个人事务之前的世界结构版本（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 ）；其检查点与导出必须以版本不符拒绝，不迁移。
_VERSION_8_WORLD_SCHEMA = 8


def _assert_only_schema_version_mismatch(error: ValidationError) -> None:
    """断言唯一的校验错误是 schema_version 版本不符，而不是缺字段或其他结构问题。"""
    assert [(item["loc"], item["type"]) for item in error.errors()] == [
        (("schema_version",), "literal_error")]


def test_world_state_schema_version_7_is_rejected():
    """版本 7 的旧世界状态以版本不符直接拒绝，不迁移不降级。"""
    with pytest.raises(ValidationError) as caught:
        WorldState.model_validate(_minimal_world_state_dict(schema_version=_VERSION_7_WORLD_SCHEMA))
    _assert_only_schema_version_mismatch(caught.value)


def test_run_with_version_7_world_state_is_rejected_on_open(engine_factory):
    """已落盘的版本 7 世界状态在重新打开运行时以版本不符拒绝，不迁移（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「版本」）。"""
    engine = engine_factory()
    payload = engine.context.state.model_dump(mode="json")
    payload["schema_version"] = _VERSION_7_WORLD_SCHEMA
    payload["version"] += 1
    engine.context.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"],
        visibility="system", data={"kind": "test_version_7_world_state"})])
    run_dir = engine.context.store.run_dir
    engine.close()
    with pytest.raises(ValidationError, match="schema_version") as caught:
        LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    _assert_only_schema_version_mismatch(caught.value)


def test_world_state_schema_version_8_is_rejected():
    """版本 8 的旧世界状态（个人事务之前）以版本不符直接拒绝，不迁移不降级（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 ，
    裁决 E7）；同一份状态写成版本 9 即可通过，唯一的错误就是版本。"""
    with pytest.raises(ValidationError) as caught:
        WorldState.model_validate(_minimal_world_state_dict(schema_version=_VERSION_8_WORLD_SCHEMA))
    _assert_only_schema_version_mismatch(caught.value)
    assert WorldState.model_validate(_minimal_world_state_dict()).schema_version == WORLD_SCHEMA_VERSION


def test_run_with_version_8_world_state_is_rejected_on_open(engine_factory):
    """已落盘的版本 8 世界状态（人物状态里没有 errands 键，与版本 8 写出的检查点同形）在重新打开运行时以版本不符拒绝，
    不迁移、不补字段（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 ）。"""
    engine = engine_factory()
    payload = engine.context.state.model_dump(mode="json")
    payload["schema_version"] = _VERSION_8_WORLD_SCHEMA
    payload["version"] += 1
    for agent in payload["agents"].values():
        assert agent.pop("errands") == {}
    engine.context.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"],
        visibility="system", data={"kind": "test_version_8_world_state"})])
    run_dir = engine.context.store.run_dir
    engine.close()
    with pytest.raises(ValidationError, match="schema_version") as caught:
        LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    _assert_only_schema_version_mismatch(caught.value)


def test_world_state_schema_version_missing_is_rejected():
    """schema_version 缺失时被拒绝（Literal 无默认值语义）。"""
    data = _minimal_world_state_dict()
    del data["schema_version"]
    with pytest.raises(ValidationError):
        WorldState.model_validate(data)


def test_world_state_sessions_defaults_to_empty_dict():
    """WorldState.sessions 有 default_factory，未填时默认为空字典。"""
    ws = WorldState.model_validate(_minimal_world_state_dict())
    assert ws.sessions == {}


def test_world_state_sessions_rejects_invalid_session():
    """WorldState.sessions 中的非法会话条目使整体验证失败。"""
    bad_session = _active_session(status="ended")  # ended 缺 ended_at
    data = _minimal_world_state_dict(sessions={"session-1-0001": bad_session})
    with pytest.raises(ValidationError):
        WorldState.model_validate(data)


def _payload_with_wait(engine_factory) -> tuple[dict, str, str]:
    """真实运行开始一段等待后的世界状态载荷，返回（载荷, 活动身份, 当前会话身份）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "session-state")
    activity_id = results[0].activity_id
    payload = engine.context.state.model_dump(mode="json")
    session_id = payload["activities"][activity_id]["session_ids"][-1]
    return payload, activity_id, session_id


def test_world_state_sessions_accepts_session_entries(engine_factory):
    """真实运行的地点会话随世界状态往返：运行开始的停留会话记下其中开始的活动。"""
    payload, activity_id, session_id = _payload_with_wait(engine_factory)
    ws = WorldState.model_validate(payload)
    session = ws.sessions[session_id]
    assert session.session_id == session_id and session.agent_id == 1
    assert session.kind == SessionKind.STAY and session.status == SessionStatus.ACTIVE
    assert session.activity_ids == [activity_id]
    assert ws.activities[activity_id].session_ids == [session_id]
    assert ws.activities[activity_id].status == ActivityStatus.ACTIVE


def test_world_state_rejects_two_active_sessions_for_one_agent(engine_factory):
    """每人至多一个进行中会话：再加一段同一人物的进行中停留会话即拒绝。"""
    payload, _, session_id = _payload_with_wait(engine_factory)
    second = {**payload["sessions"][session_id], "session_id": "session-1-9999", "activity_ids": []}
    payload["sessions"]["session-1-9999"] = second
    with pytest.raises(ValidationError, match="more than one active session"):
        WorldState.model_validate(payload)
    second["status"], second["ended_at"] = "ended", payload["current_time"]
    assert WorldState.model_validate(payload).sessions["session-1-9999"].status == SessionStatus.ENDED


def test_world_state_rejects_activity_referring_to_unknown_session(engine_factory):
    """活动只能指向已有会话：session_ids 含未知会话即拒绝。"""
    payload, activity_id, _ = _payload_with_wait(engine_factory)
    payload["activities"][activity_id]["session_ids"].append("session-1-ghost")
    with pytest.raises(ValidationError, match="unknown session"):
        WorldState.model_validate(payload)


@pytest.mark.parametrize("corruption", ["unknown_activity", "unknown_owner", "future_start", "key_mismatch"])
def test_world_state_rejects_inconsistent_session_entries(engine_factory, corruption):
    """会话的身份、归属、活动与时序必须与世界一致：未知活动、未知人物、未来开始或键不符都拒绝。"""
    payload, _, session_id = _payload_with_wait(engine_factory)
    session = payload["sessions"][session_id]
    if corruption == "unknown_activity":
        session["activity_ids"].append("activity-ghost")
    elif corruption == "unknown_owner":
        session["agent_id"] = 99
    elif corruption == "future_start":
        session["started_at"] = payload["current_time"] + 1
    else:
        payload["sessions"]["session-renamed"] = payload["sessions"].pop(session_id)
    with pytest.raises(ValidationError, match="session identity, owner or activities are invalid"):
        WorldState.model_validate(payload)
