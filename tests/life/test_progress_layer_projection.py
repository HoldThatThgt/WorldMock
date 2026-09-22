"""进度层（Track 2）投影合同测试。

覆盖 docs/dev/SPEC-agent-interaction.md「进度层」与「上下文分层」：
- facts.sessions 结构正确性（含字段存在、note_history 排除）
- scene.current_sessions 只渲染 active 会话（0/1/2 条）
- 单会话进度层 4000 字符预算裁剪
- SESSION_PROGRESS_LAYER_BUDGET 常量存在
- sessions 键不在 FORBIDDEN_OBSERVATION_KEYS 内
- current_activities 集合形式（0-2 项）
"""

from __future__ import annotations

import pytest

from worldmock.life.models import SessionState, SessionStatus, SessionTaskKind
from worldmock.life.narration import (
    FORBIDDEN_OBSERVATION_KEYS,
    SESSION_PROGRESS_LAYER_BUDGET,
    render_scene,
)
from worldmock.life.projection import (
    _active_session_scene_rows,
    _session_fact_row,
    _session_fact_rows,
)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _make_session(
    session_id: str = "session-0001",
    agent_id: int = 1,
    status: SessionStatus = SessionStatus.ACTIVE,
    targets: list[str] | None = None,
    invested_seconds: int = 3600,
    progress_note: str = "进展顺利",
    task_kind: SessionTaskKind = SessionTaskKind.WORK_TASK,
    task_ref: str = "backend-hourly-2026-01-05",
) -> SessionState:
    """构造测试用 SessionState。"""
    ended_at = 1000 if status in (SessionStatus.CONCLUDED, SessionStatus.ABORTED) else None
    return SessionState(
        session_id=session_id,
        agent_id=agent_id,
        task_kind=task_kind,
        task_ref=task_ref,
        status=status,
        started_at=100,
        ended_at=ended_at,
        targets=targets if targets is not None else ["backend-hourly-2026-01-05"],
        activity_ids=["activity-0001"],
        segments=[[100, 700]],
        invested_seconds=invested_seconds,
        effective_seconds=600.0,
        remaining_contract_seconds=14400,
        preconditions_unmet=[],
        progress_note=progress_note,
        note_updated_at=700,
        note_history=[{"note": "历史自述", "updated_at": 500}],
    )


class _FakeWorldState:
    """最小世界状态 duck-type，只携带 sessions 字典。"""

    def __init__(self, sessions: dict[str, SessionState]) -> None:
        """@brief 初始化假世界状态。"""
        self.sessions = sessions


# ---------------------------------------------------------------------------
# note_history 不出现在 facts 行
# ---------------------------------------------------------------------------

def test_session_fact_row_excludes_note_history() -> None:
    """facts 行不含 note_history（进度层合同：只存于内部存储）。"""
    session = _make_session()
    row = _session_fact_row(session)
    assert "note_history" not in row


def test_session_fact_row_includes_required_fields() -> None:
    """facts 行包含进度层合同要求的全部客观字段与自述字段。"""
    session = _make_session()
    row = _session_fact_row(session)
    required = {
        "session_id", "task_kind", "task_ref", "status",
        "started_at", "ended_at", "targets", "activity_ids",
        "segments", "invested_seconds", "effective_seconds",
        "remaining_contract_seconds", "preconditions_unmet",
        "progress_note", "note_updated_at",
    }
    assert required <= set(row)


def test_session_fact_row_values_match_state() -> None:
    """facts 行字段值与 SessionState 字段一一对应。"""
    session = _make_session(session_id="session-0099", invested_seconds=7200)
    row = _session_fact_row(session)
    assert row["session_id"] == "session-0099"
    assert row["invested_seconds"] == 7200
    assert row["status"] == SessionStatus.ACTIVE


def test_session_fact_row_preconditions_is_list() -> None:
    """preconditions_unmet 在 facts 行中保持列表形式。"""
    session = _make_session()
    row = _session_fact_row(session)
    assert isinstance(row["preconditions_unmet"], list)


# ---------------------------------------------------------------------------
# _session_fact_rows 过滤与 agent_id 匹配
# ---------------------------------------------------------------------------

def test_session_fact_rows_filters_by_agent_id() -> None:
    """只返回 agent_id 匹配的会话行，其他 agent 的会话不出现。"""
    s1 = _make_session("s1", agent_id=1)
    s2 = _make_session("s2", agent_id=2)
    state = _FakeWorldState({"s1": s1, "s2": s2})
    rows = _session_fact_rows(state, agent_id=1)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"


def test_session_fact_rows_empty_when_no_match() -> None:
    """无匹配会话时返回空列表。"""
    state = _FakeWorldState({})
    rows = _session_fact_rows(state, agent_id=1)
    assert rows == []


def test_session_fact_rows_multiple_sessions() -> None:
    """同一 agent 多个会话全部返回。"""
    s1 = _make_session("s1", agent_id=1)
    s2 = _make_session("s2", agent_id=1, status=SessionStatus.PAUSED)
    state = _FakeWorldState({"s1": s1, "s2": s2})
    rows = _session_fact_rows(state, agent_id=1)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 单会话进度层 4000 字符预算裁剪
# ---------------------------------------------------------------------------

def test_session_progress_layer_budget_constant_exists() -> None:
    """SESSION_PROGRESS_LAYER_BUDGET 常量存在且等于 4000。"""
    assert SESSION_PROGRESS_LAYER_BUDGET == 4000


def test_session_fact_rows_trims_oversized_note() -> None:
    """progress_note 过长导致单行超 4000 字符时，note 被裁剪至预算内。"""
    long_note = "进" * 600  # 600 字符，超出 max_length=500 校验，用 model_construct 绕过
    # 用 model_construct 绕过 max_length 校验以测试裁剪逻辑
    session = SessionState.model_construct(
        session_id="s-big",
        agent_id=1,
        task_kind=SessionTaskKind.WORK_TASK,
        task_ref="task-x",
        status=SessionStatus.ACTIVE,
        started_at=100,
        ended_at=None,
        targets=["task-x"],
        activity_ids=["a-1"],
        segments=[[100, 700]],
        invested_seconds=3600,
        effective_seconds=3600.0,
        remaining_contract_seconds=None,
        preconditions_unmet=[],
        progress_note=long_note,
        note_updated_at=700,
        note_history=[],
    )
    state = _FakeWorldState({"s-big": session})
    import json
    rows = _session_fact_rows(state, agent_id=1)
    assert len(rows) == 1
    row_json = json.dumps(rows[0], ensure_ascii=False)
    assert len(row_json) <= SESSION_PROGRESS_LAYER_BUDGET


# ---------------------------------------------------------------------------
# sessions 键不在 FORBIDDEN_OBSERVATION_KEYS
# ---------------------------------------------------------------------------

def test_sessions_not_in_forbidden_observation_keys() -> None:
    """facts.sessions 键不属于禁入提示的字段集（进度层合同显式解禁）。"""
    assert "sessions" not in FORBIDDEN_OBSERVATION_KEYS


# ---------------------------------------------------------------------------
# _active_session_scene_rows：只返回 active 会话
# ---------------------------------------------------------------------------

def test_active_session_scene_rows_empty_on_no_sessions() -> None:
    """无会话时返回空列表。"""
    state = _FakeWorldState({})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert rows == []


def test_active_session_scene_rows_excludes_paused() -> None:
    """paused 会话不出现在 scene 行中。"""
    s = _make_session("sp", agent_id=1, status=SessionStatus.PAUSED)
    state = _FakeWorldState({"sp": s})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert rows == []


def test_active_session_scene_rows_excludes_concluded() -> None:
    """concluded 会话不出现在 scene 行中。"""
    s = _make_session("sc", agent_id=1, status=SessionStatus.CONCLUDED)
    state = _FakeWorldState({"sc": s})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert rows == []


def test_active_session_scene_rows_single_active() -> None:
    """单个 active 会话正确映射为 scene 行（含 label/invested_seconds/progress_note）。"""
    s = _make_session("s1", agent_id=1, targets=["后端开发"], invested_seconds=7200,
                      progress_note="锁粒度那块卡住了")
    state = _FakeWorldState({"s1": s})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert len(rows) == 1
    assert rows[0]["label"] == "后端开发"
    assert rows[0]["invested_seconds"] == 7200
    assert rows[0]["progress_note"] == "锁粒度那块卡住了"


def test_active_session_scene_rows_two_active() -> None:
    """两个 active 会话都出现在 scene 行中（并发上限 2）。"""
    s1 = _make_session("s1", agent_id=1, targets=["后端开发"])
    s2 = _make_session("s2", agent_id=1, targets=["文档撰写"])
    state = _FakeWorldState({"s1": s1, "s2": s2})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert len(rows) == 2


def test_active_session_scene_rows_caps_at_two() -> None:
    """即使有 3 个 active 会话，scene 行最多取 2 条（并发上限合同）。"""
    sessions = {
        f"s{i}": _make_session(f"s{i}", agent_id=1, targets=[f"任务{i}"])
        for i in range(3)
    }
    state = _FakeWorldState(sessions)
    rows = _active_session_scene_rows(state, agent_id=1)
    assert len(rows) <= 2


def test_active_session_scene_rows_label_falls_back_to_session_id() -> None:
    """无 targets 时 label 退回 session_id。"""
    s = _make_session("s-no-target", agent_id=1, targets=[])
    state = _FakeWorldState({"s-no-target": s})
    rows = _active_session_scene_rows(state, agent_id=1)
    assert rows[0]["label"] == "s-no-target"


# ---------------------------------------------------------------------------
# scene 渲染：current_sessions 中文短句
# ---------------------------------------------------------------------------

def _minimal_scene_obs(extra: dict | None = None) -> dict:
    """最小合法 scene observation 字典（不含黑名单键）。"""
    base: dict = {
        "current_time": "2026-09-22T09:00:00+08:00",
        "current_epoch": 1758556800,
        "location": "公司工位",
        "people_here": [],
        "felt": {},
        "current_activities": [],
        "paused_activities": [],
        "day_plan": {"goals": []},
        "inbox_head": [],
        "inbox_has_older_unread": False,
        "reachable_places": [],
        "inventory": [],
        "local_food_summary": "",
        "recent_action_results": [],
    }
    if extra:
        base.update(extra)
    return base


def test_render_scene_zero_current_sessions() -> None:
    """无 current_sessions 或空列表时 scene 正常渲染，不含「正在做」。"""
    obs = _minimal_scene_obs({"current_sessions": []})
    scene = render_scene(obs)
    assert "正在做" not in scene


def test_render_scene_one_active_session_with_note() -> None:
    """单个 active 会话含进展自述时，scene 包含「正在做」和进展短语。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "后端开发", "invested_seconds": 7200, "progress_note": "卡在锁粒度"}
        ]
    })
    scene = render_scene(obs)
    assert "正在做" in scene
    assert "后端开发" in scene
    assert "卡在锁粒度" in scene


def test_render_scene_session_without_note() -> None:
    """无进展自述时 scene 仍包含时间短语，不含空括号。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "阅读", "invested_seconds": 1800, "progress_note": ""}
        ]
    })
    scene = render_scene(obs)
    assert "正在做" in scene
    assert "阅读" in scene
    assert "（，）" not in scene


def test_render_scene_two_active_sessions() -> None:
    """两个 active 会话分别在 scene 中各出现一行。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "后端开发", "invested_seconds": 3600, "progress_note": ""},
            {"label": "文档撰写", "invested_seconds": 900, "progress_note": ""},
        ]
    })
    scene = render_scene(obs)
    assert "后端开发" in scene
    assert "文档撰写" in scene


def test_render_scene_hours_formatted() -> None:
    """投入超过一小时时，短句包含「小时」字样。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "工作", "invested_seconds": 5400, "progress_note": ""}
        ]
    })
    scene = render_scene(obs)
    assert "小时" in scene


def test_render_scene_minutes_formatted_under_one_hour() -> None:
    """投入不足一小时时，短句包含「分钟」字样。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "工作", "invested_seconds": 1500, "progress_note": ""}
        ]
    })
    scene = render_scene(obs)
    assert "分钟" in scene


def test_render_scene_just_started_label() -> None:
    """刚开始（0 秒）时，短句包含「刚开始」。"""
    obs = _minimal_scene_obs({
        "current_sessions": [
            {"label": "工作", "invested_seconds": 0, "progress_note": ""}
        ]
    })
    scene = render_scene(obs)
    assert "刚开始" in scene


# ---------------------------------------------------------------------------
# current_activities 集合形式（0-2 项）
# ---------------------------------------------------------------------------

def test_render_scene_zero_current_activities() -> None:
    """无当前活动时 scene 正常渲染（不抛异常）。"""
    obs = _minimal_scene_obs({"current_activities": []})
    scene = render_scene(obs)
    assert isinstance(scene, str)


def test_render_scene_one_current_activity() -> None:
    """单个当前活动在 scene 中渲染进度句。"""
    obs = _minimal_scene_obs({
        "current_activities": [{"title": "写周报", "progress": "已完成一半", "verb": "进行"}]
    })
    scene = render_scene(obs)
    assert "写周报" in scene


def test_render_scene_two_current_activities() -> None:
    """两个当前活动（并发上限）均在 scene 中渲染。"""
    obs = _minimal_scene_obs({
        "current_activities": [
            {"title": "写周报", "progress": "一半", "verb": "进行"},
            {"title": "听播客", "progress": "刚开始", "verb": "收听"},
        ]
    })
    scene = render_scene(obs)
    assert "写周报" in scene
    assert "听播客" in scene
