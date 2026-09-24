"""进度层公开投影合同：结果、会话、顺序和内部身份隔离。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life import projection
from worldmock.life.models import (
    AgentState,
    ConcludeActivityRequest,
    PauseActivityRequest,
    PendingAction,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    WorldState,
)
from worldmock.life.projection import (
    _active_session_scene_rows,
    _progress_projection,
    _progress_result,
    _session_fact_row,
    _session_fact_rows,
)
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin


def _session(
    key: str,
    status: SessionStatus = SessionStatus.ACTIVE,
    agent_id: int = 1,
    started_at: int = 100,
    task_name: str = "周会纪要",
) -> SessionState:
    """用不透明内部身份和可读名称构造真实会话模型。"""
    ended_at = started_at + 60 if status in {SessionStatus.CONCLUDED, SessionStatus.ABORTED} else None
    start_time = datetime.fromtimestamp(started_at, timezone.utc).strftime("%H:%M:%S")
    return SessionState(
        session_id=key,
        agent_id=agent_id,
        task_kind=SessionTaskKind.WORK_TASK,
        task_ref=f"internal-task-{key}",
        task_name=task_name,
        session_name=f"{task_name}：{start_time} 在办公室开始的工作",
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        targets=[f"internal-task-{key}"],
        activity_ids=[f"internal-activity-{key}"],
        segments=[[started_at, started_at + 30]],
        invested_seconds=3600,
        effective_seconds=2700,
        remaining_contract_seconds=900,
        preconditions_unmet=[],
        progress_note="先把接口对齐，下一步检查边界。",
        note_updated_at=started_at + 30,
        note_history=[{"note": "旧记录", "updated_at": started_at}],
    )


def _state(*sessions: SessionState) -> SimpleNamespace:
    """只提供投影函数需要的世界状态字段。"""
    return SimpleNamespace(sessions={session.session_id: session for session in sessions})


@pytest.mark.parametrize("request_type", [ReportStatusRequest, PauseActivityRequest,
                                          ConcludeActivityRequest])
def test_selected_session_control_uses_parallel_task_and_summary_lists(request_type) -> None:
    """每项当前任务各有一条自述；模型接口不接受内部会话定位字段。"""
    assert set(request_type.model_fields) == {"task", "summary"}
    request = request_type(task=["周会纪要", "预约体检"], summary=["写完提纲", "约到明早"])
    assert len(request.task) == len(request.summary) == 2
    with pytest.raises(ValidationError):
        request_type(task=["周会纪要", "预约体检"], summary=["只写了一项"])
    with pytest.raises(ValidationError):
        request_type(task=[], summary=[])


class _SessionController(SessionActionMixin):
    """用冻结会话名称测试控制动作，不加载场景或语料。"""

    def __init__(self, sessions: list[SessionState]) -> None:
        self.bindings = [{"task_name": session.task_name, "session_name": session.session_name,
                          "session_id": session.session_id, "status": session.status} for session in sessions]

    def _frozen_observation(self, pending: PendingAction) -> dict:
        return {"session_bindings": self.bindings}


def _control_state(*sessions: SessionState) -> WorldState:
    """构造会话控制需要的最小权威状态与独立目标自述。"""
    plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind="工作", goal="完成纪要", registered_at=1),))
    agent = AgentState.model_construct(agent_id=1, day_plan=plan)
    return WorldState.model_construct(current_time=200, agents={1: agent}, activities={},
                                      sessions={session.session_id: session for session in sessions})


def _control_pending() -> PendingAction:
    """控制动作只需人物与动作身份；冻结观察由专属夹具提供。"""
    return PendingAction.model_construct(action_id="private-action", agent_id=1)


def test_report_status_maps_each_task_to_its_own_note_and_goal_note() -> None:
    """摘要数组按顺序写各会话；关联目标只吸收对应会话原文。"""
    work = _session("work", task_name="完成纪要").model_copy(update={
        "task_kind": SessionTaskKind.GOAL, "task_ref": "private-goal",
        "plan_goal_intent_kind": "工作"})
    leisure = _session("leisure", task_name="看电影")
    state = _control_state(work, leisure)
    controller = _SessionController([work, leisure])
    request = ReportStatusRequest(task=[work.task_name, leisure.task_name],
                                  summary=["已完成提纲", "刚看到开头"])
    changed, _ = controller._report_status(state, _control_pending(), request)
    assert changed.sessions[work.session_id].progress_note == "已完成提纲"
    assert changed.sessions[leisure.session_id].progress_note == "刚看到开头"
    assert changed.agents[1].day_plan.goals[0].progress_note == "已完成提纲"
    assert changed.agents[1].day_plan.goals[0].progressed_at is None
    assert state.agents[1].day_plan.goals[0].progress_note == ""


def test_pause_then_report_resumes_by_readable_session_name() -> None:
    """暂停会话可通过 report_status 恢复，主观进展保留历史。"""
    session = _session("paused-work", task_name="完成纪要")
    controller = _SessionController([session])
    pending = _control_pending()
    paused, _ = controller._pause_activity(
        _control_state(session), pending,
        PauseActivityRequest(task=[session.session_name], summary=["先处理邮件"]))
    assert paused.sessions[session.session_id].status == SessionStatus.PAUSED
    resumed, _ = controller._report_status(
        paused, pending, ReportStatusRequest(task=[session.session_name], summary=["回到纪要"]))
    assert resumed.sessions[session.session_id].status == SessionStatus.ACTIVE
    assert resumed.sessions[session.session_id].progress_note == "回到纪要"
    assert resumed.sessions[session.session_id].note_history[-1]["note"] == "先处理邮件"


def test_current_task_name_must_identify_one_session() -> None:
    """同一任务有两个当前会话时，使用自然语言会话名消歧。"""
    first = _session("first", task_name="完成纪要", started_at=100)
    second = _session("second", task_name="完成纪要", started_at=120)
    state = _control_state(first, second)
    controller = _SessionController([first, second])
    pending = _control_pending()
    with pytest.raises(BusinessRuleError):
        controller._report_status(
            state, pending, ReportStatusRequest(task=["完成纪要"], summary=["已完成提纲"]))
    assert state.sessions[first.session_id].progress_note == first.progress_note
    changed, _ = controller._report_status(
        state, pending, ReportStatusRequest(task=[first.session_name], summary=["已完成提纲"]))
    assert changed.sessions[first.session_id].progress_note == "已完成提纲"
    assert changed.sessions[second.session_id].progress_note == second.progress_note


def test_recall_work_task_uses_latest_session_note_without_removed_agent_field() -> None:
    """回看工作任务读取会话自述，回看具体会话读取该会话状态。"""
    first = _session("first", started_at=100).model_copy(update={
        "progress_note": "先整理提纲", "note_updated_at": 130})
    second = _session("second", started_at=200).model_copy(update={
        "task_ref": first.task_ref, "progress_note": "已经核对正文", "note_updated_at": 230})
    state = _control_state(first, second)

    class RecallController(SessionActionMixin):
        """只提供回看动作需要的冻结绑定与任务目录。"""

        rules = SimpleNamespace(task=lambda _identity: SimpleNamespace(
            task_id=first.task_ref, required_skill="writing",
            due_at=datetime.fromtimestamp(1000, timezone.utc)))

        def _frozen_observation(self, _pending: PendingAction) -> dict:
            return {"session_bindings": [
                {"session_name": session.session_name, "session_id": session.session_id}
                for session in (first, second)],
                "task_bindings": [{"task_name": first.task_name, "task_id": first.task_ref,
                                   "submission_ready": False}],
                "goal_bindings": []}

    controller = RecallController()
    _, task_result = controller._recall_task_summary(
        state, _control_pending(), RecallTaskSummaryRequest(task_name=first.task_name))
    assert task_result.data["task"]["progress_note"] == "已经核对正文"

    _, session_result = controller._recall_task_summary(
        state, _control_pending(), RecallTaskSummaryRequest(task_name=first.session_name))
    assert session_result.data["session"]["subjective_progress"] == "先整理提纲"


def test_conclude_only_ends_selected_active_sessions_and_updates_matching_notes() -> None:
    """结束工具逐项写入对应摘要，未选中及暂停会话保持原状。"""
    work = _session("work", task_name="完成纪要").model_copy(update={
        "task_kind": SessionTaskKind.GOAL, "task_ref": "private-goal",
        "plan_goal_intent_kind": "工作"})
    leisure = _session("leisure", task_name="看电影")
    paused = _session("paused", SessionStatus.PAUSED, task_name="暂停阅读")
    state = _control_state(work, leisure, paused)
    controller = _SessionController([work, leisure, paused])
    changed, _ = controller._conclude_activity(state, _control_pending(),
                                               ConcludeActivityRequest(task=[work.task_name],
                                                                      summary=["纪要已收尾"]))
    assert changed.sessions[work.session_id].status == SessionStatus.CONCLUDED
    assert changed.sessions[leisure.session_id].status == SessionStatus.ACTIVE
    assert changed.sessions[paused.session_id].status == SessionStatus.PAUSED
    assert changed.sessions[work.session_id].progress_note == "纪要已收尾"
    assert changed.sessions[leisure.session_id].progress_note == leisure.progress_note
    assert changed.agents[1].day_plan.goals[0].progress_note == "纪要已收尾"
    both, _ = controller._conclude_activity(state, _control_pending(),
                                            ConcludeActivityRequest(task=[work.task_name, leisure.task_name],
                                                                   summary=["纪要已收尾", "影片已看完"]))
    assert both.sessions[work.session_id].progress_note == "纪要已收尾"
    assert both.sessions[leisure.session_id].progress_note == "影片已看完"
    with pytest.raises(BusinessRuleError):
        controller._conclude_activity(_control_state(paused), _control_pending(),
                                      ConcludeActivityRequest(task=[paused.task_name],
                                                              summary=["不能结束暂停项"]))


def test_session_row_contains_only_readable_progress_fields() -> None:
    """内部身份、活动时段和历史原文不能进入人物进度层。"""
    row = _session_fact_row(_session("session-opaque-7"))
    assert set(row) == {
        "task_name", "session_name", "phase", "objective_progress",
        "subjective_progress", "subjective_status",
    }
    assert row["task_name"] == "周会纪要"
    assert row["phase"] == "进行中"
    assert row["subjective_status"] == "未经核验"
    assert "3600" in row["objective_progress"]
    assert "session-opaque-7" not in str(row)
    assert "internal-task" not in str(row)
    assert "internal-activity" not in str(row)


def test_sessions_are_ordered_and_not_capped_by_count() -> None:
    """进行中、暂停中、终态依次排列；终态最近优先，历史条数不裁断。"""
    terminal = [_session(f"done-{index}", SessionStatus.CONCLUDED, started_at=200 + index)
                for index in range(26)]
    active = _session("active", SessionStatus.ACTIVE, started_at=50)
    paused = _session("paused", SessionStatus.PAUSED, started_at=70)
    outsider = _session("outsider", agent_id=2)
    rows = _session_fact_rows(_state(*terminal, paused, outsider, active), agent_id=1)
    assert len(rows) == 28
    assert [row["phase"] for row in rows[:2]] == ["进行中", "已暂停"]
    assert rows[2]["session_name"] == terminal[-1].session_name
    assert rows[-1]["session_name"] == terminal[0].session_name
    assert all("outsider" not in str(row) for row in rows)


def test_scene_uses_readable_current_task_without_session_identity() -> None:
    """场景短句只取当前会话，不能在目标缺失时退回内部会话编号。"""
    active = _session("secret-active")
    paused = _session("secret-paused", SessionStatus.PAUSED)
    rows = _active_session_scene_rows(_state(active, paused), agent_id=1)
    assert len(rows) == 1
    assert rows[0]["label"] == active.task_name
    assert "secret-active" not in str(rows)


def _result(data: dict | None = None) -> dict:
    """构造事件侧完整结果，公开投影应主动删掉内部身份。"""
    return {
        "action_id": "internal-action-42",
        "occurred_at": 120,
        "status": "rejected",
        "error_code": "WORK_REQUIRES_REWORK",
        "message": "这份工作需要补充结论。",
        "result": {"data": data or {}},
    }


def test_fresh_result_preserves_feedback_without_action_id() -> None:
    """进度层保留完整新反馈，不暴露事件或动作定位字段。"""
    inputs = SimpleNamespace(scenario=SimpleNamespace(products=(), locations=()))
    public = _progress_result(inputs, _result())
    assert public == {
        "occurred_at": 120,
        "status": "rejected",
        "error_code": "WORK_REQUIRES_REWORK",
        "message": "这份工作需要补充结论。",
    }
    assert "internal-action-42" not in str(public)


def test_all_fresh_results_remain_in_progress_event_order() -> None:
    """七条新结算全部出现，不能按旧五条限制截掉前面的反馈。"""
    events = [{**_result(), "action_id": f"private-{index}",
               "occurred_at": index, "message": f"第{index}条反馈"}
              for index in range(7)]
    inputs = SimpleNamespace(
        scenario=SimpleNamespace(products=(), locations=()),
        state=SimpleNamespace(sessions={}, current_time=10),
        agent=SimpleNamespace(agent_id=1),
        task_rows=[],
        recent_results=events,
    )
    progress = _progress_projection(inputs)
    assert [row["message"] for row in progress["results"]] == [
        f"第{index}条反馈" for index in range(7)]
    assert progress["sessions"] == [] and progress["alarms"] == []
    assert "private-0" not in str(progress)


def test_facts_exposes_one_progress_field_instead_of_two_old_result_fields(monkeypatch) -> None:
    """完整事实组装把新结算和会话放在同一个进度层字段。"""
    for name in ("_contact_rows", "_public_inbox", "_task_fact_rows", "_public_routes",
                 "_public_products", "_public_inventory"):
        monkeypatch.setattr(projection, name, lambda *args: [])
    monkeypatch.setattr(projection, "_plan_fact", lambda *args: {"goals": []})
    monkeypatch.setattr(projection, "drive_summary", lambda *args: "")
    inputs = SimpleNamespace(
        scenario=SimpleNamespace(products=(), locations=(
            SimpleNamespace(location_id="private-office", name="办公室"),)),
        state=SimpleNamespace(sessions={}, current_time=10),
        agent=SimpleNamespace(agent_id=1, location_id="private-office"),
        task_rows=[], recent_results=[_result()],
    )
    facts = projection._facts_projection(
        inputs, datetime.fromtimestamp(10, timezone.utc), {},
        {"people": [], "inbox": [], "bindings": []})
    assert "progress" in facts
    assert "sessions" not in facts and "recent_action_results" not in facts
    assert facts["progress"]["results"][0]["message"] == _result()["message"]
    assert "private-office" not in str(facts)


def test_purchase_result_replaces_product_and_supply_ids_with_names() -> None:
    """多商品结果各自保留份数与金额，删除报价版本及内部供给身份。"""
    products = (SimpleNamespace(product_id="p1", name="牛肉面"),
                SimpleNamespace(product_id="p2", name="豆浆"))
    inputs = SimpleNamespace(scenario=SimpleNamespace(products=products, locations=()))
    orders = [
        {"order_id": "order-1", "product_id": "p1", "supply_id": "s1", "quantity": 1,
         "price_version": "private-v1", "total_price_fen": 2800},
        {"order_id": "order-2", "product_id": "p2", "supply_id": "s2", "quantity": 1,
         "price_version": "private-v2", "total_price_fen": 500},
    ]
    public = _progress_result(inputs, _result({"orders": orders}))
    assert public["details"]["purchases"] == [
        {"product": "牛肉面", "quantity": 1, "charged_fen": 2800},
        {"product": "豆浆", "quantity": 1, "charged_fen": 500},
    ]
    assert all(key not in str(public) for key in ("order-1", "supply_id", "private-v1"))
