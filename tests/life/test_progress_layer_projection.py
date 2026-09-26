"""进度层公开投影合同：按会话分组的活动行、人读客观进度、活动控制、结果顺序与内部身份隔离。

docs/dev/SPEC-time-and-energy.md 第七章「会话与活动的字段」：进度层 facts.progress.sessions[] 每行是会话名加
activities[]（活动名、阶段、客观进度与标注「你自己记下的，未经核验」的自述），按开始时刻排序；播报、暂停与结束逐项
点名活动；回看可按任务、当日目标、活动或会话名，结果不带身份。第八章「活动」：客观进度从活动与任务累计读取，写成
人读时长并随结算增长；自述不进场景。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import (
    activity_name, advance_next, advance_until_ready, conclude_request, context, stage_day_plan, start_activity,
    submit_round, wake_by_mail,
)
from worldmock.errors import BusinessRuleError
from worldmock.life import projection
from worldmock.life.activity_control import SELF_REPORT_MARK, activity_phase, visible_names
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    ConcludeActivityRequest,
    PauseActivityRequest,
    PendingAction,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SessionKind,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    WorldState,
    active_activities,
)
from worldmock.life.narration import render_scene
from worldmock.life.projection import _activity_fact_row, _progress_projection, _progress_result, _session_fact_rows
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin
from worldmock.life.sessions import moment_label

_ZONE = "Asia/Shanghai"
#: 2026-01-05 00:00（场景时区）。
_START = 1767542400


def _activity(key: str, label: str, *, kind: ActivityKind = ActivityKind.WAIT,
              status: ActivityStatus = ActivityStatus.ACTIVE, agent_id: int = 1, started_at: int = _START,
              elapsed: int = 0, planned: int = 3600, sessions: tuple[str, ...] = ("morning",),
              **fields) -> ActivityState:
    """用不透明内部身份构造真实活动模型，名称为「做什么＋对象（M月D日 HH:MM 起）」；终态在已结算处结束。"""
    ended = None if status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED} else started_at + elapsed
    return ActivityState(
        activity_id=f"internal-activity-{key}", agent_id=agent_id, location_id="private-office", kind=kind,
        status=status, started_at=started_at, ended_at=ended, planned_end_at=started_at + planned,
        last_settled_at=started_at + elapsed, elapsed_seconds=elapsed, planned_duration_seconds=planned,
        origin_action_id=f"internal-action-{key}", origin_decision_id="internal-decision",
        origin_request_event_id="internal-request", session_ids=[f"internal-session-{item}" for item in sessions],
        name=f"{label}（{moment_label(started_at, _ZONE)} 起）", **fields)


def _session(key: str, activities: tuple[ActivityState, ...] = (), *, agent_id: int = 1,
             started_at: int = _START, ended_at: int | None = None) -> SessionState:
    """办公室的一段停留：内部身份不透明，名称为地点加开始时刻。"""
    return SessionState(
        session_id=f"internal-session-{key}", agent_id=agent_id, kind=SessionKind.STAY,
        location_id="private-office", status=SessionStatus.ENDED if ended_at else SessionStatus.ACTIVE,
        started_at=started_at, ended_at=ended_at, activity_ids=[item.activity_id for item in activities],
        name=f"办公室（{moment_label(started_at, _ZONE)} 起）")


def _world(activities, sessions=(), *, now: int = _START + 600, plan: DayPlan | None = None) -> WorldState:
    """只含投影与控制所需字段的权威状态（内部身份保持不透明）。"""
    agent = AgentState.model_construct(agent_id=1, day_plan=plan)
    return WorldState.model_construct(
        current_time=now, timezone=_ZONE, agents={1: agent},
        activities={item.activity_id: item for item in activities},
        sessions={item.session_id: item for item in sessions})


def _inputs(state: WorldState, tasks: tuple = ()) -> SimpleNamespace:
    """投影输入束：本轮可见名称由 activity_control.visible_names 分配（重名追加序号）。"""
    return SimpleNamespace(state=state, agent=SimpleNamespace(agent_id=1, day_plan=None),
                           scenario=SimpleNamespace(tasks=tasks),
                           extras={"visible_names": visible_names(state, 1, [])})


def _goal_plan() -> DayPlan:
    """当日只有一个未推进的「工作」目标。"""
    return DayPlan(day_end=_START + 86400,
                   goals=(PlanGoal(intent_kind="工作", goal="完成纪要", registered_at=_START),))


class _Controller(SessionActionMixin):
    """用本轮冻结观察的活动名绑定测试控制动作，不加载场景或语料。"""

    def __init__(self, state: WorldState, rows: list[dict] | None = None) -> None:
        names = visible_names(state, 1, [])["activities"]
        self.rows = rows if rows is not None else [
            {"name": name, "activity_id": key, "phase": activity_phase(state.activities[key])}
            for key, name in names.items()]

    def _frozen_observation(self, pending: PendingAction) -> dict:
        return {"activity_bindings": self.rows, "verified_experiences": []}


def _pending() -> PendingAction:
    """控制动作只需人物与动作身份；冻结观察由专属夹具提供。"""
    return PendingAction.model_construct(action_id="private-action", agent_id=1)


@pytest.mark.parametrize("request_type", [ReportStatusRequest, PauseActivityRequest, ConcludeActivityRequest])
def test_activity_control_uses_parallel_task_and_summary_lists(request_type) -> None:
    """每项点名的活动各有一条自述，结束另带等长的记忆数组；模型接口不接受内部会话或活动定位字段。"""
    concluding = request_type is ConcludeActivityRequest
    memories = {"memories": [[], []]} if concluding else {}
    assert set(request_type.model_fields) == {"task", "summary", *memories}
    names = ["观看Space Habitat（1月5日 09:00 起）", "等待（1月5日 09:00 起）"]
    request = request_type(task=names, summary=["看到一半", "还在等"], **memories)
    assert len(request.task) == len(request.summary) == 2
    with pytest.raises(ValidationError):
        request_type(task=names, summary=["只写了一项"], **memories)
    with pytest.raises(ValidationError):
        request_type(task=[], summary=[], **({"memories": []} if concluding else {}))
    for leaked in ("session_id", "activity_id"):
        with pytest.raises(ValidationError):
            request_type(task=names, summary=["看到一半", "还在等"], **memories, **{leaked: "internal-7"})
    if concluding:
        with pytest.raises(ValidationError):
            request_type(task=names, summary=["看到一半", "还在等"], memories=[[]])


def test_report_status_maps_each_activity_to_its_own_note_and_goal_note() -> None:
    """自述数组按顺序写到各活动并把播报锚点移到当前时刻；关联目标只吸收对应活动的原文，不算推进。"""
    work = _activity("work", "工作周会纪要", kind=ActivityKind.WORK, task_kind=SessionTaskKind.GOAL,
                     task_ref="工作", plan_goal_intent_kind="工作", report_anchor_at=_START)
    leisure = _activity("leisure", "观看Space Habitat", kind=ActivityKind.WATCH, report_anchor_at=_START)
    state = _world([work, leisure], plan=_goal_plan())
    changed, result = _Controller(state)._report_status(state, _pending(), ReportStatusRequest(
        task=[work.name, leisure.name], summary=["已完成提纲", "刚看到开头"]))
    assert changed.activities[work.activity_id].progress_note == "已完成提纲"
    assert changed.activities[leisure.activity_id].progress_note == "刚看到开头"
    assert {changed.activities[item.activity_id].report_anchor_at for item in (work, leisure)} == {
        state.current_time}
    assert changed.agents[1].day_plan.goals[0].progress_note == "已完成提纲"
    assert changed.agents[1].day_plan.goals[0].progressed_at is None
    assert state.agents[1].day_plan.goals[0].progress_note == ""
    assert state.activities[work.activity_id].progress_note == ""
    assert result.data == {"task": [work.name, leisure.name],
                           "activity_ids": [work.activity_id, leisure.activity_id]}


def test_control_names_resolve_to_one_own_activity_of_this_round() -> None:
    """名称按本轮可见绑定唯一解析到本人的活动：同名的后一项带「（第二次）」；已收尾的活动不能播报；
    未知名称或指向他人的活动以 ACTION_REQUEST_INVALID 拒绝，状态不变。"""
    earlier = _activity("earlier", "等待", status=ActivityStatus.COMPLETED, elapsed=20, planned=20,
                        concluded_at=_START + 20)
    later = _activity("later", "等待", started_at=_START + 30, planned=900)
    outsider = _activity("outsider", "观看Sintel", agent_id=2)
    state = _world([earlier, later, outsider])
    controller = _Controller(state)
    names = {row["activity_id"]: row["name"] for row in controller.rows}
    assert earlier.name == later.name
    assert names == {earlier.activity_id: earlier.name, later.activity_id: f"{later.name}（第二次）"}
    changed, _ = controller._report_status(state, _pending(), ReportStatusRequest(
        task=[names[later.activity_id]], summary=["第二段也在等"]))
    assert changed.activities[later.activity_id].progress_note == "第二段也在等"
    assert changed.activities[earlier.activity_id] == earlier
    forged = controller.rows + [{"name": outsider.name, "activity_id": outsider.activity_id, "phase": "进行中"}]
    for task, rows in (([earlier.name], controller.rows), (["等待"], controller.rows), ([outsider.name], forged)):
        with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
            _Controller(state, rows)._report_status(state, _pending(), ReportStatusRequest(
                task=task, summary=["继续"]))
    assert state.activities[later.activity_id].progress_note == ""


def test_conclude_marks_named_just_ended_activities_and_keeps_their_status() -> None:
    """收尾刚结束的活动只写总结与 concluded_at、不改终态；只触及点名的活动；有待收尾时只结束别的活动以
    ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名待收尾项；已收尾的活动不能再结束（第七章「conclude_activity 的效果」）。"""
    work = _activity("work", "工作周会纪要", kind=ActivityKind.WORK, status=ActivityStatus.COMPLETED,
                     elapsed=600, planned=600, task_kind=SessionTaskKind.GOAL, task_ref="工作",
                     plan_goal_intent_kind="工作")
    road = _activity("road", "观看Space Habitat", kind=ActivityKind.WATCH, status=ActivityStatus.CANCELLED,
                     elapsed=600, boundary_type="route_ended", interruption_reason="route_ended")
    paused = _activity("paused", "阅读邮件", kind=ActivityKind.READ_MAIL, status=ActivityStatus.PAUSED,
                       started_at=_START - 600, elapsed=300, planned=900)
    state = _world([work, road, paused], plan=_goal_plan())
    controller = _Controller(state)
    changed, result = controller._conclude_activity(state, _pending(), conclude_request(work.name,
                                                                                     summary="纪要已收尾"))
    concluded = changed.activities[work.activity_id]
    assert (concluded.status, concluded.concluded_at) == (ActivityStatus.COMPLETED, state.current_time)
    assert concluded.progress_note == "纪要已收尾"
    assert changed.activities[road.activity_id] == road and changed.activities[paused.activity_id] == paused
    assert changed.agents[1].day_plan.goals[0].progress_note == "纪要已收尾"
    assert result.data == {"task": [work.name], "activity_ids": [work.activity_id], "memory_sources": [
        {"task": work.name, "activity_id": work.activity_id, "session_id": work.session_ids[-1],
         "verified_event_ids": []}]}
    both, _ = controller._conclude_activity(state, _pending(), ConcludeActivityRequest(
        task=[work.name, road.name], summary=["纪要已收尾", "路上没看完"], memories=[[], []]))
    assert all(both.activities[item.activity_id].concluded_at == state.current_time for item in (work, road))
    assert both.activities[road.activity_id].status == ActivityStatus.CANCELLED
    assert both.activities[road.activity_id].interruption_reason == "route_ended"
    with pytest.raises(BusinessRuleError, match="ACTIVITY_CONCLUSION_REQUIRED") as caught:
        controller._conclude_activity(state, _pending(), conclude_request(paused.name))
    assert work.name in str(caught.value.__cause__) and road.name in str(caught.value.__cause__)
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        _Controller(both)._conclude_activity(both, _pending(), conclude_request(work.name))


def test_recall_reads_task_activity_and_session_without_identities() -> None:
    """回看工作任务取最近一条相关活动自述；回看活动给地点、阶段、人读进度与自述；回看会话给地点、起止与活动名；
    自述标「你自己记下的，未经核验」，结果不带内部身份并原样进入下一次观察的 details.recall（第七章）。"""
    task = SimpleNamespace(task_id="internal-task", required_effective_seconds=14400.0,
                           due_at=datetime.fromtimestamp(_START + 64800, timezone.utc))
    work = {"kind": ActivityKind.WORK, "target_id": task.task_id, "task_kind": SessionTaskKind.WORK_TASK,
            "task_ref": task.task_id}
    first = _activity("first", "工作1月5日项目会议纪要", status=ActivityStatus.COMPLETED, elapsed=3600,
                      planned=3600, effective_work_seconds=2700.0, progress_note="先整理提纲",
                      note_updated_at=_START + 3600, concluded_at=_START + 3600, **work)
    second = _activity("second", "工作1月5日项目会议纪要", started_at=_START + 7200, elapsed=3000, planned=7200,
                       effective_work_seconds=2400.0, progress_note="已经核对正文", note_updated_at=_START + 10200,
                       sessions=("afternoon",), **work)
    morning = _session("morning", (first,), ended_at=_START + 3600)
    afternoon = _session("afternoon", (second,), started_at=_START + 7200)
    state = _world([first, second], [morning, afternoon], now=_START + 10200)

    class RecallController(SessionActionMixin):
        """只提供回看需要的冻结绑定、任务目录与地点名。"""

        rules = SimpleNamespace(task=lambda _identity: task, tasks={task.task_id: task})
        scenario = SimpleNamespace(timezone=_ZONE, locations=(
            SimpleNamespace(location_id="private-office", name="办公室"),))

        def _frozen_observation(self, _pending: PendingAction) -> dict:
            names = visible_names(state, 1, ["1月5日项目会议纪要"])
            return {"task_bindings": [{"task_name": "1月5日项目会议纪要", "task_id": task.task_id,
                                       "submission_ready": False}],
                    "goal_bindings": [],
                    "activity_bindings": [{"name": name, "activity_id": key,
                                           "phase": activity_phase(state.activities[key])}
                                          for key, name in names["activities"].items()],
                    "session_bindings": [{"name": name, "session_id": key}
                                         for key, name in names["sessions"].items()]}

    controller = RecallController()

    def recall(name: str) -> dict:
        unchanged, result = controller._recall_task_summary(state, _pending(), RecallTaskSummaryRequest(
            task_name=name))
        assert unchanged is state
        return result.data

    by_task, by_activity, by_session = recall("1月5日项目会议纪要"), recall(first.name), recall(afternoon.name)
    assert by_task["recall"] == {"kind": "task", "name": "1月5日项目会议纪要", "due_at": "1月5日 18:00",
                                 "progress_note": "已经核对正文", "subjective_status": SELF_REPORT_MARK}
    assert by_activity["recall"] == {
        "kind": "activity", "name": first.name, "place": "办公室", "phase": "已收尾",
        "objective_progress": "已做约1小时，有效约45分钟，还差约2.5小时",
        "progress_note": "先整理提纲", "subjective_status": SELF_REPORT_MARK}
    assert by_session["recall"] == {"kind": "session", "place": "办公室", "started_at": "1月5日 02:00",
                                    "ended_at": None, "activity_names": [second.name]}
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        recall("不存在的名称")
    assert "internal-" not in str([by_task, by_activity, by_session])
    row = {"action_id": "internal-action-9", "occurred_at": _START + 10200, "status": "completed",
           "error_code": None, "message": "Task summary retrieved", "result": {"data": by_activity}}
    public = _progress_result(SimpleNamespace(scenario=SimpleNamespace(products=(), locations=())), row)
    assert public["details"] == {"recall": by_activity["recall"]}


def test_activity_row_contains_only_readable_progress_fields() -> None:
    """活动行只有名称、阶段、人读客观进度与注明来源的自述：工作写已做、有效、还差与还剩（第八章「活动客观进度」）；
    没有自述的活动不带自述键；刚结束的活动不写还剩；内部身份不进入。"""
    task = SimpleNamespace(task_id="internal-task", required_effective_seconds=12600.0)
    work = _activity("work", "工作1月5日项目会议纪要", kind=ActivityKind.WORK, elapsed=7200, planned=10800,
                     target_id=task.task_id, effective_work_seconds=5400.0, progress_note="锁粒度那块卡住了")
    row = _activity_fact_row(_inputs(_world([work], now=_START + 7200), (task,)), work, work.name)
    assert row == {"name": work.name, "phase": "进行中",
                   "objective_progress": "已做约2小时，有效约1.5小时，还差约2小时，还剩约1小时",
                   "subjective_progress": "锁粒度那块卡住了", "subjective_status": "你自己记下的，未经核验"}
    assert "internal-" not in str(row)
    waiting = _activity("waiting", "等待", planned=900)
    assert _activity_fact_row(_inputs(_world([waiting])), waiting, waiting.name) == {
        "name": waiting.name, "phase": "进行中", "objective_progress": "已做不到1分钟，还剩约15分钟"}
    ended = _activity("ended", "等待", status=ActivityStatus.COMPLETED, elapsed=900, planned=900)
    assert _activity_fact_row(_inputs(_world([ended], now=_START + 900)), ended, ended.name) == {
        "name": ended.name, "phase": "刚结束", "objective_progress": "已做约15分钟"}


def test_sessions_are_ordered_by_start_and_group_activities_under_their_latest_session() -> None:
    """进度层按会话开始时刻排序，活动列在其最近所属的会话下（回到开始地点的暂停活动归入新会话）；
    已收尾的历史不按条数裁断；没有活动的一段路照样列出；他人的会话与活动不进入。"""
    history = [_activity(f"done-{index}", f"观看短片{index}", status=ActivityStatus.COMPLETED,
                         started_at=_START + index * 60, elapsed=30, planned=30,
                         concluded_at=_START + index * 60 + 30) for index in range(26)]
    paused = _activity("paused", "阅读邮件", kind=ActivityKind.READ_MAIL, status=ActivityStatus.PAUSED,
                       started_at=_START + 1800, elapsed=300, planned=900, sessions=("morning", "evening"))
    active = _activity("active", "等待", started_at=_START + 7300, planned=900, sessions=("evening",))
    morning = _session("morning", (*history, paused), ended_at=_START + 3600)
    road = SessionState(session_id="internal-session-road", agent_id=1, kind=SessionKind.TRANSIT,
                        origin_id="private-office", destination_id="private-home", status=SessionStatus.ENDED,
                        started_at=_START + 3600, ended_at=_START + 4200,
                        name=f"去家的路上（{moment_label(_START + 3600, _ZONE)} 起）")
    evening = _session("evening", (paused, active), started_at=_START + 7200)
    outsider_activity = _activity("outsider", "观看Sintel", agent_id=2, sessions=("outsider",))
    outsider = _session("outsider", (outsider_activity,), agent_id=2, started_at=_START - 60)
    state = _world([*history, paused, active, outsider_activity], [evening, outsider, road, morning],
                   now=_START + 7300)
    rows = _session_fact_rows(_inputs(state))
    assert [row["name"] for row in rows] == [morning.name, road.name, evening.name]
    assert len(rows[0]["activities"]) == 26
    assert [item["name"] for item in rows[0]["activities"]] == [item.name for item in history]
    assert {item["phase"] for item in rows[0]["activities"]} == {"已收尾"}
    assert rows[1]["activities"] == []
    assert [(item["name"], item["phase"]) for item in rows[2]["activities"]] == [
        (paused.name, "已暂停"), (active.name, "进行中")]
    assert "internal-" not in str(rows) and outsider_activity.name not in str(rows)


def _act(engine, round_id: str, tool: str, request):
    """单人物按真实顺序观察、登记并结算一轮。"""
    _, results = submit_round(engine, {1: (tool, request)}, round_id)
    return results[0]


def _observe(engine, round_id: str) -> dict:
    """读取一次当前观察（只追加观察事件，不开轮次）。"""
    return engine.observe(context(engine, round_id))


def _progress_row(observed: dict, name: str) -> dict:
    """进度层中按活动名找到的一行。"""
    return next(item for row in observed["facts"]["progress"]["sessions"] for item in row["activities"]
                if item["name"] == name)


def _advance_until(engine, done, round_id: str) -> None:
    """推进到条件成立；途中的决策机会以播报继续全部进行中活动。"""
    for step in range(96):
        if done():
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.state, 1)
            _act(engine, f"{round_id}-{step}", "report_status", ReportStatusRequest(
                task=[item.name for item in running], summary=["继续。"] * len(running)))
        else:
            advance_next(engine)
    pytest.fail("Condition was not reached at the declared boundaries")


def test_progress_rows_follow_pause_resume_end_and_conclusion(engine_factory) -> None:
    """真实运行中活动行的阶段依次为进行中、已暂停、进行中、刚结束、已收尾；自述只以「你自己记下的，未经核验」出现在
    进度层，从不进场景；场景不再有 current_sessions（第七章「进度层」，第八章「自述不当事实」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _act(engine, "phase-wait", "start_activity", start_activity("等待", "自己"))
    activity_id = started.activity_id
    name = activity_name(engine, activity_id)
    session = engine.state.sessions[engine.state.activities[activity_id].session_ids[-1]]
    opened = _observe(engine, "phase-active")
    assert [row["name"] for row in opened["facts"]["progress"]["sessions"]] == [session.name]
    assert _progress_row(opened, name) == {"name": name, "phase": "进行中",
                                           "objective_progress": "已做不到1分钟，还剩约15分钟"}
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert _act(engine, "phase-pause", "pause_activity", PauseActivityRequest(
        task=[name], summary=["先处理邮件"])).status == "completed"
    assert _progress_row(_observe(engine, "phase-paused"), name) == {
        "name": name, "phase": "已暂停", "objective_progress": "已做约1分钟，还剩约14分钟",
        "subjective_progress": "先处理邮件", "subjective_status": SELF_REPORT_MARK}
    assert _act(engine, "phase-resume", "report_status", ReportStatusRequest(
        task=[name], summary=["回到等待"])).status == "completed"
    assert engine.state.activities[activity_id].note_history[-1]["note"] == "先处理邮件"
    resumed = _observe(engine, "phase-resumed")
    assert _progress_row(resumed, name)["phase"] == "进行中"
    assert "current_sessions" not in resumed["scene"]
    assert "回到等待" not in str(resumed["scene"]) and "回到等待" not in render_scene(resumed["scene"])
    _advance_until(engine, lambda: engine.state.activities[activity_id].status != ActivityStatus.ACTIVE, "phase")
    ended = _observe(engine, "phase-ended")
    assert _progress_row(ended, name)["phase"] == "刚结束"
    assert _progress_row(ended, name)["objective_progress"] == "已做约15分钟"
    assert "回到等待" not in render_scene(ended["scene"]) and "先处理邮件" not in render_scene(ended["scene"])
    assert _act(engine, "phase-conclude", "conclude_activity", conclude_request(
        name, summary="等完了")).status == "completed"
    concluded = _progress_row(_observe(engine, "phase-concluded"), name)
    assert concluded["phase"] == "已收尾" and concluded["subjective_progress"] == "等完了"
    assert concluded["subjective_status"] == SELF_REPORT_MARK


def test_work_objective_progress_grows_with_settlement(engine_factory) -> None:
    """工作的客观进度读活动已结算的时长与任务有效工时累计，写成人读时长，随结算增长（第八章「活动客观进度」）。"""
    from tests.life.test_work_review import _at_task_start

    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = engine.scenario.tasks[0]
    preview = _observe(engine, "work-preview")
    target, label = next((row["object"], action["label"]) for row in preview["interaction_bindings"]
                         if row["object_id"] == task.task_id for action in row["actions"]
                         if action["canonical_action"] == "work")
    started = _act(engine, "work-start", "start_activity", start_activity(label, target))
    assert started.status == "in_progress"
    name = activity_name(engine, started.activity_id)
    first = _progress_row(_observe(engine, "work-first"), name)["objective_progress"]
    assert first == "已做不到1分钟，有效不到1分钟，还差约4小时，还剩约8小时"
    moment = engine.state.current_time + 1800
    wake_by_mail(engine, offset=1800)
    _advance_until(engine, lambda: engine.state.current_time >= moment, "work")
    activity = engine.state.activities[started.activity_id]
    assert activity.elapsed_seconds == 1800 and 0 < activity.effective_work_seconds <= 1800
    later = _progress_row(_observe(engine, "work-later"), name)["objective_progress"]
    assert later.startswith(f"已做约30分钟，有效约{round(activity.effective_work_seconds / 60)}分钟，还差约")
    assert later.endswith("还剩约7.5小时") and later != first


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


def _empty_names() -> dict:
    """本轮没有任何会话与活动时的可见名称。"""
    return {"visible_names": {"sessions": {}, "activities": {}}, "verified_experiences": []}


def test_all_fresh_results_remain_in_progress_event_order() -> None:
    """七条新结算全部出现，不能按旧五条限制截掉前面的反馈。"""
    events = [{**_result(), "action_id": f"private-{index}",
               "occurred_at": index, "message": f"第{index}条反馈"}
              for index in range(7)]
    inputs = SimpleNamespace(
        scenario=SimpleNamespace(products=(), locations=(), tasks=()),
        state=SimpleNamespace(sessions={}, activities={}, current_time=10),
        agent=SimpleNamespace(agent_id=1, day_plan=None),
        task_rows=[],
        recent_results=events,
        extras=_empty_names(),
    )
    progress = _progress_projection(inputs)
    assert [row["message"] for row in progress["results"]] == [
        f"第{index}条反馈" for index in range(7)]
    assert progress["sessions"] == [] and progress["alarms"] == []
    assert "private-0" not in str(progress)


def test_facts_exposes_one_progress_field_instead_of_two_old_result_fields(monkeypatch) -> None:
    """完整事实组装把新结算和按会话分组的活动放在同一个进度层字段；已核验经历另以句子列出。"""
    for name in ("_contact_rows", "_public_inbox", "_task_fact_rows", "_public_routes",
                 "_public_products", "_public_inventory"):
        monkeypatch.setattr(projection, name, lambda *args: [])
    monkeypatch.setattr(projection, "_plan_fact", lambda *args: {"goals": []})
    monkeypatch.setattr(projection, "drive_summary", lambda *args: "")
    inputs = SimpleNamespace(
        scenario=SimpleNamespace(products=(), tasks=(), timezone=_ZONE, locations=(
            SimpleNamespace(location_id="private-office", name="办公室"),)),
        state=SimpleNamespace(sessions={}, activities={}, current_time=10),
        agent=SimpleNamespace(agent_id=1, location_id="private-office", day_plan=None),
        task_rows=[], recent_results=[_result()], extras=_empty_names(),
    )
    facts = projection._facts_projection(
        inputs, datetime.fromtimestamp(10, timezone.utc), {},
        {"people": [], "inbox": [], "bindings": []})
    assert "progress" in facts
    assert "sessions" not in facts and "recent_action_results" not in facts
    assert facts["progress"]["results"][0]["message"] == _result()["message"]
    assert facts["progress"]["sessions"] == [] and facts["experiences"] == []
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
