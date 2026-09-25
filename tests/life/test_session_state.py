"""会话层（三级结构 Track 1）的字段、枚举与生命周期合同测试。

涵盖：SessionState 全字段组合、状态转移、枚举闭集、进展自述长度上限、
task_kind/task_ref 约束、WorldState.sessions 默认工厂、以及 schema_version
升版后旧检查点被拒绝的验收要求。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    WORLD_SCHEMA_VERSION,
    SessionState,
    SessionStatus,
    SessionTaskKind,
)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _active_session(**overrides) -> dict:
    """构造最小合法的 active 会话数据字典。"""
    base = {
        "session_id": "session-0001",
        "task_name": "等待",
        "session_name": "等待（上午十点，家中）",
        "agent_id": 1,
        "task_kind": "none",
        "task_ref": None,
        "status": "active",
        "started_at": 1000,
    }
    base.update(overrides)
    return base


def _concluded_session(**overrides) -> dict:
    """构造最小合法的 concluded 会话数据字典。"""
    base = {
        "session_id": "session-0002",
        "task_name": "等待",
        "session_name": "等待（上午十点，家中）",
        "agent_id": 1,
        "task_kind": "none",
        "task_ref": None,
        "status": "concluded",
        "started_at": 1000,
        "ended_at": 2000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------


def test_world_schema_version_is_7():
    """WORLD_SCHEMA_VERSION 必须为 7（时间与能量规格第九章「仍须修复」：世界结构版本 7）。"""
    assert WORLD_SCHEMA_VERSION == 7


# ---------------------------------------------------------------------------
# 枚举闭集
# ---------------------------------------------------------------------------


def test_session_status_closed_set():
    """SessionStatus 闭集恰为四个合法成员。"""
    expected = {"active", "paused", "concluded", "aborted"}
    assert {s.value for s in SessionStatus} == expected


def test_session_task_kind_closed_set():
    """SessionTaskKind 闭集恰为三个合法成员。"""
    expected = {"goal", "work_task", "none"}
    assert {k.value for k in SessionTaskKind} == expected


@pytest.mark.parametrize("bad_status", ["running", "done", "pending", ""])
def test_invalid_status_is_rejected(bad_status):
    """非法状态字符串被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(status=bad_status))


@pytest.mark.parametrize("bad_kind", ["task", "daily", "plan", ""])
def test_invalid_task_kind_is_rejected(bad_kind):
    """非法 task_kind 字符串被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(task_kind=bad_kind))


# ---------------------------------------------------------------------------
# task_kind 与 task_ref 配对约束
# ---------------------------------------------------------------------------


def test_task_kind_none_with_null_ref_is_valid():
    """task_kind=none 且 task_ref=None 通过验证。"""
    s = SessionState.model_validate(_active_session(task_kind="none", task_ref=None))
    assert s.task_kind == SessionTaskKind.NONE
    assert s.task_ref is None


def test_task_kind_none_with_non_null_ref_is_rejected():
    """task_kind=none 且 task_ref 有值时被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _active_session(task_kind="none", task_ref="backend-hourly-2026-01-05")
        )


def test_task_kind_goal_requires_task_ref():
    """task_kind=goal 时 task_ref=None 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _active_session(task_kind="goal", task_ref=None)
        )


def test_task_kind_goal_with_task_ref_is_valid():
    """task_kind=goal 且 task_ref 有值通过验证。"""
    s = SessionState.model_validate(
        _active_session(task_kind="goal", task_ref="工作", plan_goal_intent_kind="工作")
    )
    assert s.task_kind == SessionTaskKind.GOAL
    assert s.task_ref == "工作"


def test_task_kind_work_task_requires_task_ref():
    """task_kind=work_task 时 task_ref=None 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _active_session(task_kind="work_task", task_ref=None)
        )


def test_task_kind_work_task_with_task_ref_is_valid():
    """task_kind=work_task 且 task_ref 有值通过验证。"""
    s = SessionState.model_validate(
        _active_session(task_kind="work_task", task_ref="backend-hourly-2026-01-05")
    )
    assert s.task_kind == SessionTaskKind.WORK_TASK
    assert s.task_ref == "backend-hourly-2026-01-05"


# ---------------------------------------------------------------------------
# 生命周期：终态必须有 ended_at，非终态不得有
# ---------------------------------------------------------------------------


def test_active_without_ended_at_is_valid():
    """active 状态不含 ended_at 通过验证。"""
    s = SessionState.model_validate(_active_session())
    assert s.status == SessionStatus.ACTIVE
    assert s.ended_at is None


def test_paused_without_ended_at_is_valid():
    """paused 状态不含 ended_at 通过验证。"""
    s = SessionState.model_validate(_active_session(status="paused"))
    assert s.status == SessionStatus.PAUSED
    assert s.ended_at is None


def test_concluded_with_ended_at_is_valid():
    """concluded 终态含 ended_at 通过验证。"""
    s = SessionState.model_validate(_concluded_session())
    assert s.status == SessionStatus.CONCLUDED
    assert s.ended_at == 2000


def test_aborted_with_ended_at_is_valid():
    """aborted 终态含 ended_at 通过验证。"""
    s = SessionState.model_validate(_concluded_session(status="aborted"))
    assert s.status == SessionStatus.ABORTED
    assert s.ended_at is not None


def test_active_with_ended_at_is_rejected():
    """active 状态含 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(ended_at=2000))


def test_paused_with_ended_at_is_rejected():
    """paused 状态含 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(status="paused", ended_at=2000))


def test_concluded_without_ended_at_is_rejected():
    """concluded 状态缺 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _concluded_session(ended_at=None)
        )


def test_aborted_without_ended_at_is_rejected():
    """aborted 状态缺 ended_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _concluded_session(status="aborted", ended_at=None)
        )


def test_ended_at_before_started_at_is_rejected():
    """ended_at 早于 started_at 被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(
            _concluded_session(started_at=5000, ended_at=1000)
        )


# ---------------------------------------------------------------------------
# 状态转移（通过 model_copy 模拟系统修改）
# ---------------------------------------------------------------------------


def test_active_to_paused_transition():
    """active → paused 转移后，ended_at 仍为 None。"""
    s = SessionState.model_validate(_active_session())
    paused = s.model_copy(update={"status": SessionStatus.PAUSED})
    assert paused.status == SessionStatus.PAUSED
    assert paused.ended_at is None


def test_paused_to_active_transition():
    """paused → active 转移通过。"""
    s = SessionState.model_validate(_active_session(status="paused"))
    resumed = s.model_copy(update={"status": SessionStatus.ACTIVE})
    assert resumed.status == SessionStatus.ACTIVE


def test_active_to_concluded_transition():
    """active → concluded 转移时需同时填写 ended_at。"""
    s = SessionState.model_validate(_active_session())
    concluded = s.model_copy(update={"status": SessionStatus.CONCLUDED, "ended_at": 9999})
    assert concluded.status == SessionStatus.CONCLUDED
    assert concluded.ended_at == 9999


def test_active_to_aborted_transition():
    """active → aborted（前置条件失效）转移需同时填写 ended_at。"""
    s = SessionState.model_validate(_active_session())
    aborted = s.model_copy(update={"status": SessionStatus.ABORTED, "ended_at": 8888})
    assert aborted.status == SessionStatus.ABORTED
    assert aborted.ended_at == 8888


# ---------------------------------------------------------------------------
# 进展自述字段
# ---------------------------------------------------------------------------


def test_progress_note_default_is_empty():
    """progress_note 默认为空字符串。"""
    s = SessionState.model_validate(_active_session())
    assert s.progress_note == ""


def test_progress_note_at_500_chars_is_valid():
    """进展自述恰好 500 字符通过验证。"""
    note = "字" * 500
    s = SessionState.model_validate(_active_session(progress_note=note))
    assert len(s.progress_note) == 500


def test_progress_note_at_501_chars_is_rejected():
    """进展自述 501 字符被拒绝。"""
    note = "字" * 501
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(progress_note=note))


def test_note_updated_at_and_history_defaults():
    """note_updated_at 默认 None，note_history 默认空列表。"""
    s = SessionState.model_validate(_active_session())
    assert s.note_updated_at is None
    assert s.note_history == []


# ---------------------------------------------------------------------------
# 系统数值字段默认值
# ---------------------------------------------------------------------------


def test_numeric_system_field_defaults():
    """invested_seconds、effective_seconds 等系统数值字段的默认值符合规格。"""
    s = SessionState.model_validate(_active_session())
    assert s.invested_seconds == 0
    assert s.effective_seconds == 0.0
    assert s.remaining_contract_seconds is None
    assert s.targets == []
    assert s.activity_ids == []
    assert s.segments == []
    assert s.preconditions_unmet == []


def test_negative_invested_seconds_is_rejected():
    """invested_seconds 为负数被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(invested_seconds=-1))


def test_negative_effective_seconds_is_rejected():
    """effective_seconds 为负数被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(effective_seconds=-0.1))


# ---------------------------------------------------------------------------
# 全字段构造（示例来自规格 JSON）
# ---------------------------------------------------------------------------


def test_full_session_state_from_spec():
    """按规格示例 JSON 构造完整会话状态，验证所有字段可正确解析。"""
    data = {
        "session_id": "session-0007",
        "task_name": "项目工作",
        "session_name": "项目工作（下午两点，办公室）",
        "agent_id": 1,
        "task_kind": "work_task",
        "task_ref": "backend-hourly-2026-01-05",
        "status": "active",
        "started_at": 1767585601,
        "ended_at": None,
        "targets": ["backend-hourly-2026-01-05"],
        "activity_ids": ["activity-...-action:0001"],
        "segments": [[1767585601, 1767590401], [1767591301, 1767594001]],
        "invested_seconds": 7500,
        "effective_seconds": 6000,
        "remaining_contract_seconds": 14400,
        "preconditions_unmet": [],
        "progress_note": "锁粒度那块卡住了，先把接口对完再回头啃。",
        "note_updated_at": 1767594001,
        "note_history": [],
    }
    s = SessionState.model_validate(data)
    assert s.session_id == "session-0007"
    assert s.task_kind == SessionTaskKind.WORK_TASK
    assert s.invested_seconds == 7500
    assert s.remaining_contract_seconds == 14400
    assert len(s.segments) == 2


# ---------------------------------------------------------------------------
# 未知字段被拒绝（extra="forbid"）
# ---------------------------------------------------------------------------


def test_extra_fields_are_rejected():
    """StrictModel 语义：未知字段被拒绝。"""
    with pytest.raises(ValidationError):
        SessionState.model_validate(_active_session(unknown_field="surprise"))


# ---------------------------------------------------------------------------
# WorldState.sessions 字段与 schema_version=7
# ---------------------------------------------------------------------------


def _minimal_world_state_dict(**overrides) -> dict:
    """构造最小合法 WorldState 字典（无代理、无库存）。"""
    base: dict = {
        "schema_version": 7,
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


def test_world_state_schema_version_7_is_valid():
    """schema_version=7 的 WorldState 通过验证。"""
    from worldmock.life.models import WorldState

    ws = WorldState.model_validate(_minimal_world_state_dict())
    assert ws.schema_version == 7


#: 时间与能量规格之前的世界结构版本；其检查点必须以版本不符拒绝。
_VERSION_6_WORLD_SCHEMA = 6


def _assert_only_schema_version_mismatch(error: ValidationError) -> None:
    """断言唯一的校验错误是 schema_version 版本不符，而不是缺字段或其他结构问题。"""
    assert [(item["loc"], item["type"]) for item in error.errors()] == [
        (("schema_version",), "literal_error")]


def test_world_state_schema_version_6_is_rejected():
    """版本 6 的旧世界状态以版本不符直接拒绝，不迁移不降级。"""
    from worldmock.life.models import WorldState

    with pytest.raises(ValidationError) as caught:
        WorldState.model_validate(_minimal_world_state_dict(schema_version=_VERSION_6_WORLD_SCHEMA))
    _assert_only_schema_version_mismatch(caught.value)


def test_run_with_version_6_world_state_is_rejected_on_open(engine_factory):
    """已落盘的版本 6 世界状态在重新打开运行时以版本不符拒绝，不迁移（时间与能量规格第九章）。"""
    engine = engine_factory()
    payload = engine.state.model_dump(mode="json")
    payload["schema_version"] = _VERSION_6_WORLD_SCHEMA
    payload["version"] += 1
    engine.store.commit(payload, [EventDraft(
        event_type="environment_event", occurred_at=payload["current_time"],
        visibility="system", data={"kind": "test_version_6_world_state"})])
    run_dir = engine.store.run_dir
    engine.close()
    with pytest.raises(ValidationError, match="schema_version") as caught:
        LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    _assert_only_schema_version_mismatch(caught.value)


def test_world_state_schema_version_missing_is_rejected():
    """schema_version 缺失时被拒绝（Literal 无默认值语义）。"""
    from worldmock.life.models import WorldState

    data = _minimal_world_state_dict()
    del data["schema_version"]
    with pytest.raises(ValidationError):
        WorldState.model_validate(data)


def test_world_state_sessions_defaults_to_empty_dict():
    """WorldState.sessions 有 default_factory，未填时默认为空字典。"""
    from worldmock.life.models import WorldState

    ws = WorldState.model_validate(_minimal_world_state_dict())
    assert ws.sessions == {}


def test_world_state_sessions_accepts_session_entries():
    """WorldState.sessions 能正确存储并取回 SessionState 条目。"""
    from worldmock.life.models import WorldState

    session_data = _active_session(session_id="session-0001")
    data = _minimal_world_state_dict(sessions={"session-0001": session_data})
    ws = WorldState.model_validate(data)
    assert "session-0001" in ws.sessions
    assert ws.sessions["session-0001"].session_id == "session-0001"


def test_world_state_sessions_rejects_invalid_session():
    """WorldState.sessions 中的非法会话条目使整体验证失败。"""
    from worldmock.life.models import WorldState

    bad_session = _active_session(status="concluded")  # 终态缺 ended_at
    data = _minimal_world_state_dict(sessions={"session-bad": bad_session})
    with pytest.raises(ValidationError):
        WorldState.model_validate(data)
