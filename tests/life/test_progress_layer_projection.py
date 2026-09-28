"""活动控制与公开结果合同：活动目标的人读客观进度、播报暂停与结束的控制、回看、公开结果投影与内部身份隔离。

docs/dev/SPEC-persona-prompt.md「工具层」「待办层」「进度层」：进行中与刚结束的活动是工具层的活动目标（名称、阶段、
客观进度与三个控制工具），暂停的活动只在待办层；播报、暂停与结束逐项点名活动；回看可按任务、当日目标或活动名，
结果不带身份并进入进度层该调用的结果。第八章「活动」：客观进度从活动累计读取，写成人读短语并随结算增长，工作不写「还差」。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import (
    activity_name, advance_next, advance_until_ready, conclude_request, context, progress_calls, stage_day_plan,
    start_activity, submit_round, wake_by_mail,
)
from worldmock.errors import BusinessRuleError
from worldmock.life.activity_control import activity_phase, objective_progress, visible_names
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
    SameMomentStage,
    SessionKind,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    WorldState,
    active_activities,
)
from worldmock.life.result_projection import public_result
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin
from worldmock.life.sessions import moment_label
from worldmock.life.tool_layer import LayerContext, tool_layer

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
    agent = AgentState.model_construct(agent_id=1, day_plan=plan, next_decision_at=now,
                                       same_moment_stage=SameMomentStage.OPEN, location_id="private-office")
    return WorldState.model_construct(
        current_time=now, timezone=_ZONE, agents={1: agent},
        activities={item.activity_id: item for item in activities},
        sessions={item.session_id: item for item in sessions})


def _layer_context(state: WorldState) -> LayerContext:
    """只有活动目标的工具层上下文：没有绑定对象，名称由 activity_control.visible_names 分配。"""
    return LayerContext(state=state, scenario=_PLACES, rules=SimpleNamespace(tasks={}), semantics=SimpleNamespace(),
                        agent_id=1, names=visible_names(state, 1, []), bindings=[],
                        menu=SimpleNamespace(allowed_actions=()))


#: 控制动作与工具层只需要的场景切片：地点名。
_PLACES = SimpleNamespace(timezone=_ZONE, locations=(SimpleNamespace(location_id="private-office", name="办公室"),))


def _goal_plan() -> DayPlan:
    """当日只有一个未推进的「工作」目标。"""
    return DayPlan(day_end=_START + 86400,
                   goals=(PlanGoal(intent_kind="工作", goal="完成纪要", registered_at=_START),))


class _Controller(SessionActionMixin):
    """用本轮冻结观察的活动名绑定测试控制动作，不加载场景或语料（地点名取固定切片，没有工作任务）。"""

    scenario = _PLACES
    rules = SimpleNamespace(tasks={})

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


def test_recall_reads_task_and_activity_without_identities() -> None:
    """回看工作任务取最近一条相关活动自述；回看活动给地点（最近所属会话的地点，未归入会话的取开始地点）、阶段、
    人读进度（工作不写「还差」）与自述；截止是带时区的 ISO 时刻，自述照原文、不逐条标注未经核验；会话名不在七层中出现，
    回看会话名以 TASK_NOT_FOUND 拒绝。结果不带内部身份并原样进入进度层该调用结果的 details.recall（第七章，
    docs/dev/SPEC-persona-prompt.md「通用格式」「进度层」）。自述仍在本轮记忆层 self_notes 中时（按活动的全名或去掉开始
    时刻的短名标注），任务与活动的回看都只写那项活动在记忆层中的名称与「已在记忆层」（「唯一出现原则」）。"""
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
    unassigned = _activity("unassigned", "等待", started_at=_START + 60, sessions=())
    state = _world([first, second, unassigned], [morning, afternoon], now=_START + 10200)
    memory = {"layer": "记忆层", "earlier_days": [], "earlier_today": []}

    class RecallController(SessionActionMixin):
        """只提供回看需要的冻结绑定、记忆层、任务目录与地点名。"""

        rules = SimpleNamespace(task=lambda _identity: task, tasks={task.task_id: task})
        scenario = SimpleNamespace(timezone=_ZONE, locations=(
            SimpleNamespace(location_id="private-office", name="办公室"),))

        def _frozen_observation(self, _pending: PendingAction) -> dict:
            names = visible_names(state, 1, ["工作：1月5日项目会议纪要"])
            return {"task_bindings": [{"task_name": "工作：1月5日项目会议纪要", "task_id": task.task_id}],
                    "goal_bindings": [], "memory_layer": memory,
                    "activity_bindings": [{"name": name, "activity_id": key,
                                           "phase": activity_phase(state.activities[key]), "note_labels": []}
                                          for key, name in names["activities"].items()]}

    controller = RecallController()

    def recall(name: str) -> dict:
        unchanged, result = controller._recall_task_summary(state, _pending(), RecallTaskSummaryRequest(
            task_name=name))
        assert unchanged is state
        return result.data

    by_task, by_activity = recall("工作：1月5日项目会议纪要"), recall(first.name)
    assert by_task["recall"] == {"kind": "task", "name": "工作：1月5日项目会议纪要",
                                 "due_at": "2026-01-05T18:00:00+08:00", "progress_note": "已经核对正文"}
    assert by_activity["recall"] == {
        "kind": "activity", "name": first.name, "place": "办公室", "phase": "已收尾",
        "objective_progress": "已做约 1 小时，有效约 45 分钟", "progress_note": "先整理提纲"}
    assert recall(unassigned.name)["recall"]["place"] == "办公室"
    for missing in ("不存在的名称", afternoon.name):
        with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
            recall(missing)
    assert "internal-" not in str([by_task, by_activity])
    event = {"action_id": "internal-action-9", "occurred_at": _START + 10200, "status": "completed",
             "error_code": None, "message": "Task summary retrieved", "data": by_activity}
    public = public_result(event, _scenario())
    assert public["details"] == {"recall": by_activity["recall"]}
    memory["earlier_today"] = [{"recorded_at": "2026-01-05T01:00:00+08:00", "settled": [], "self_notes": [
        {"activity": "工作1月5日项目会议纪要", "text": "先整理提纲"}]}]
    assert recall(first.name)["recall"] == {"kind": "activity", "activity": "工作1月5日项目会议纪要",
                                            "progress_note": "已在记忆层"}
    assert recall("工作：1月5日项目会议纪要")["recall"]["progress_note"] == "已经核对正文"
    memory["earlier_today"][0]["self_notes"].append({"activity": second.name, "text": "已经核对正文"})
    assert recall("工作：1月5日项目会议纪要")["recall"] == {
        "kind": "task", "activity": second.name, "progress_note": "已在记忆层"}


def test_activity_targets_carry_only_readable_objective_progress() -> None:
    """工具层的活动目标只有名称、阶段、人读客观进度与三个控制工具：工作写已做、有效与还剩，不写「还差」（剩余只在任务
    目标的 contract）；刚结束的活动先写终局再写已做；暂停的活动不在工具层；自述与内部身份不进入
    （docs/dev/SPEC-persona-prompt.md「工具层」「唯一出现原则」）。"""
    work = _activity("work", "工作1月5日项目会议纪要", kind=ActivityKind.WORK, elapsed=7200, planned=10800,
                     target_id="internal-task", effective_work_seconds=5400.0, progress_note="锁粒度那块卡住了")
    assert objective_progress(work) == "已做约 2 小时，有效约 1.5 小时，还剩约 1 小时"
    waiting = _activity("waiting", "等待", planned=900, started_at=_START + 1)
    ended = _activity("ended", "等人", status=ActivityStatus.COMPLETED, elapsed=900, planned=900,
                      started_at=_START + 2, boundary_type="planned_end")
    paused = _activity("paused", "阅读邮件", kind=ActivityKind.READ_MAIL, status=ActivityStatus.PAUSED,
                       started_at=_START + 3, elapsed=300, planned=900, paused_at=_START + 300)
    state = _world([work, waiting, ended, paused], now=_START + 7200)
    targets = {row["name"]: row for row in tool_layer(_layer_context(state))["targets"]}
    assert set(targets) == {work.name, waiting.name, ended.name}
    assert targets[work.name] == {"name": work.name, "kind": "activity", "phase": "进行中",
                                  "state": "已做约 2 小时，有效约 1.5 小时，还剩约 1 小时", "actions": [],
                                  "tools": targets[work.name]["tools"]}
    assert [row["tool"] for row in targets[work.name]["tools"]] == [
        "report_status", "pause_activity", "conclude_activity"]
    assert targets[waiting.name]["state"] == "已做不到 1 分钟，还剩约 15 分钟"
    assert (targets[ended.name]["phase"], targets[ended.name]["state"]) == ("刚结束", "按计划结束，已做约 15 分钟")
    rendered = json.dumps(targets, ensure_ascii=False)
    assert "internal-" not in rendered and "锁粒度那块卡住了" not in rendered


def _act(engine, round_id: str, tool: str, request):
    """单人物按真实顺序观察、登记并结算一轮。"""
    _, results = submit_round(engine, {1: (tool, request)}, round_id)
    return results[0]


def _observe(engine, round_id: str) -> dict:
    """读取一次当前观察（只追加观察事件，不开轮次）。"""
    return engine.observe(context(engine, round_id))


def _activity_target(observed: dict, name: str) -> dict | None:
    """工具层中按活动名找到的活动目标（不在工具层时为 None）。"""
    return next((row for row in observed["tool_layer"]["targets"]
                 if row["kind"] == "activity" and row["name"] == name), None)


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
    """真实运行中活动依次出现在：工具层（进行中）、待办层（暂停，带暂停时刻与恢复地点）、工具层（进行中）、工具层（刚结束）、
    两层都不在（已收尾）；写下的 summary 只作为进度层该调用的参数出现，不进工具层与待办层（第七章「进度层」，
    docs/dev/SPEC-persona-prompt.md「唯一出现原则」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _act(engine, "phase-wait", "start_activity", start_activity("等待", "自己"))
    activity_id = started.activity_id
    name = activity_name(engine, activity_id)
    opened = _observe(engine, "phase-active")
    assert _activity_target(opened, name)["phase"] == "进行中"
    assert _activity_target(opened, name)["state"] == "已做不到 1 分钟，还剩约 15 分钟"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert _act(engine, "phase-pause", "pause_activity", PauseActivityRequest(
        task=[name], summary=["先处理邮件"])).status == "completed"
    paused = _observe(engine, "phase-paused")
    assert _activity_target(paused, name) is None
    (item,) = paused["todo_layer"]["paused_activities"]
    assert (item["name"], item["progress"]) == (name, "已做约 1 分钟，还剩约 14 分钟")
    assert "先处理邮件" not in json.dumps([paused["tool_layer"], paused["todo_layer"]], ensure_ascii=False)
    assert progress_calls(paused, "pause_activity")[0]["arguments"]["summary"] == ["先处理邮件"]
    assert _act(engine, "phase-resume", "report_status", ReportStatusRequest(
        task=[name], summary=["回到等待"])).status == "completed"
    assert engine.state.activities[activity_id].note_history[-1]["note"] == "先处理邮件"
    resumed = _observe(engine, "phase-resumed")
    assert _activity_target(resumed, name)["phase"] == "进行中" and not resumed["todo_layer"]["paused_activities"]
    assert "回到等待" not in json.dumps(resumed["tool_layer"], ensure_ascii=False)
    _advance_until(engine, lambda: engine.state.activities[activity_id].status != ActivityStatus.ACTIVE, "phase")
    ended = _observe(engine, "phase-ended")
    assert _activity_target(ended, name)["phase"] == "刚结束"
    assert _activity_target(ended, name)["state"] == "按计划结束，已做约 15 分钟"
    assert _act(engine, "phase-conclude", "conclude_activity", conclude_request(
        name, summary="等完了")).status == "completed"
    concluded = _observe(engine, "phase-concluded")
    assert _activity_target(concluded, name) is None and not concluded["todo_layer"]["paused_activities"]


def test_work_objective_progress_grows_with_settlement(engine_factory) -> None:
    """工作活动目标的客观进度读活动已结算的时长与有效工时，写成人读时长，随结算增长；不写「还差」，达标情况只在任务目标的
    contract（第八章「活动客观进度」，docs/dev/SPEC-persona-prompt.md 第六章）。"""
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
    observed = _observe(engine, "work-first")
    first = _activity_target(observed, name)["state"]
    assert first == "已做不到 1 分钟，有效不到 1 分钟，还剩约 8 小时"
    contract = next(row for row in observed["tool_layer"]["targets"] if row["name"] == target)["contract"]
    assert contract["remaining"] == "有效工时还差约 4 小时（约 0/4 小时）"
    moment = engine.state.current_time + 1800
    wake_by_mail(engine, offset=1800)
    _advance_until(engine, lambda: engine.state.current_time >= moment, "work")
    activity = engine.state.activities[started.activity_id]
    assert activity.elapsed_seconds == 1800 and 0 < activity.effective_work_seconds <= 1800
    later = _activity_target(_observe(engine, "work-later"), name)["state"]
    assert later.startswith(f"已做约 30 分钟，有效约 {round(activity.effective_work_seconds / 60)} 分钟")
    assert later.endswith("还剩约 7.5 小时") and "还差" not in later and later != first


def _scenario(products: tuple = (), locations: tuple = ()) -> SimpleNamespace:
    """公开结果投影所需的场景切片：商品名、地点名与时区。"""
    agents = (SimpleNamespace(agent_id=1, name="林悦",
                              contacts=(SimpleNamespace(contact_id="contact-wanglei", salutation="王磊"),)),)
    return SimpleNamespace(products=products, inventory_items=(), locations=locations, timezone=_ZONE, agents=agents)


def _result(data: dict | None = None) -> dict:
    """构造 action_result 事件正文，公开投影应主动删掉内部身份。"""
    return {"action_id": "internal-action-42", "agent_id": 1, "occurred_at": _START + 120, "status": "rejected",
            "error_code": "WORK_REQUIRES_REWORK", "message": "这份工作需要补充结论。", "data": data or {}}


def test_fresh_result_preserves_feedback_without_action_id() -> None:
    """公开结果写带时区的时刻、状态、错误码、原文说明与 details（没有新事实时为 {}），不暴露事件或动作定位字段。"""
    public = public_result(_result(), _scenario())
    assert public == {"occurred_at": "2026-01-05T00:02:00+08:00", "status": "rejected",
                      "error_code": "WORK_REQUIRES_REWORK", "message": "这份工作需要补充结论。", "details": {}}
    assert "internal-action-42" not in str(public)


def test_purchase_result_replaces_product_and_supply_ids_with_names() -> None:
    """多商品结果各自写商品名、份数、付款、送达地点、预计时刻（带时区 ISO，docs/dev/SPEC-persona-prompt.md「通用格式」）、
    收货人与所回应的消息（没有关联时为 null），不复述请求的方式，删除报价版本、订单、供给、收货人与消息身份。"""
    products = (SimpleNamespace(product_id="p1", name="牛肉面"),
                SimpleNamespace(product_id="p2", name="豆浆"))
    places = (SimpleNamespace(location_id="private-office", name="办公室"),)
    orders = [
        {"order_id": "order-1", "product_id": "p1", "supply_id": "s1", "quantity": 1, "fulfillment": "delivery",
         "destination_id": "private-office", "expected_at": _START + 900, "price_version": "private-v1",
         "total_price_fen": 2800, "recipient_id": "contact-wanglei", "request_message_id": "reply-private"},
        {"order_id": "order-2", "product_id": "p2", "supply_id": "s2", "quantity": 1, "fulfillment": "pickup",
         "destination_id": "private-office", "expected_at": _START + 86_400, "price_version": "private-v2",
         "total_price_fen": 550, "recipient_id": "agent:1", "request_message_id": None},
    ]
    payload = {"orders": orders, "answering": "王磊 00:00 的消息"}
    public = public_result({**_result(payload), "status": "completed", "error_code": None},
                           _scenario(products, places))
    assert public["details"]["purchases"] == [
        {"product": "牛肉面", "quantity": "1 份", "paid": "28 元", "destination": "办公室",
         "expected_at": "2026-01-05T00:15:00+08:00", "recipient": "王磊", "request": "王磊 00:00 的消息"},
        {"product": "豆浆", "quantity": "1 份", "paid": "5.5 元", "destination": "办公室",
         "expected_at": "2026-01-06T00:00:00+08:00", "recipient": "林悦", "request": None},
    ]
    assert all(key not in str(public) for key in ("order-1", "supply_id", "private-v1", "s1", "contact-wanglei",
                                                   "reply-private", "agent:1"))


def test_result_details_keep_only_new_readable_facts() -> None:
    """details 的闭集键：开始活动写计划时长，到达写地点与能否进入，浏览只列商品名称，回看、原因、下一步、候选与终局
    原样；读完的邮件写主题与正文（正文只在阅读动作的结果中），恢复的活动写恢复后的计划时长；不复述参数、不含内部身份；
    工作结果另带只含公开字段的 work_review。"""
    products = (SimpleNamespace(product_id="p1", name="牛肉面"),)
    places = (SimpleNamespace(location_id="private-office", name="办公室"),)
    activity = {"activity_id": "internal-activity-1", "name": "步行前往办公室（1月5日 00:00 起）",
                "planned_duration_seconds": 750, "data": {"arrival": {"location_id": "private-office",
                                                                     "access_status": "denied"}}}
    started = {**_result({"activity": activity, "browse_result": {"items": [{"product_id": "p1", "supply_id": "s1",
                                                                              "price_fen": 1200}]},
                          "recall": {"name": "工作：会议纪要"}, "candidates": ["办公室出售的牛肉面"],
                          "outcome": [{"activity": "吃牛肉面", "outcome": "完成", "items": [], "note": None}],
                          "request": {"targets": ["地点：办公室"]}}),
               "status": "in_progress", "error_code": None}
    details = public_result(started, _scenario(products, places))["details"]
    assert details == {"planned": [{"name": activity["name"], "duration": "约 12 分钟"}],
                       "arrival": {"place": "办公室", "access": "没能进去"}, "browse_items": ["牛肉面"],
                       "recall": {"name": "工作：会议纪要"}, "candidates": ["办公室出售的牛肉面"],
                       "outcome": [{"activity": "吃牛肉面", "outcome": "完成", "items": [], "note": None}]}
    allowed = {**activity, "data": {"arrival": {"location_id": "private-office", "access_status": "allowed"}}}
    arrived = public_result({**_result({"activity": allowed}), "status": "completed", "error_code": None},
                            _scenario(products, places))["details"]
    assert arrived == {"arrival": {"place": "办公室", "access": "进入"}}
    rejected = public_result(_result({"reason": "钱不够", "next_step": "换一件便宜的", "unmet": ["funds"]}),
                             _scenario())["details"]
    assert rejected == {"reason": "钱不够", "next_step": "换一件便宜的"}
    reading = {"activity_id": "internal-activity-2", "kind": "read_mail", "name": "阅读邮件工作安排（1月5日 00:00 起）",
               "planned_duration_seconds": 300, "data": {"revealed": {
                   "message_id": "internal-mail", "thread_id": "internal-thread", "subject": "工作安排",
                   "body": "周一上午交会议纪要。", "related_task_id": "internal-task", "attachment_sha256": None}}}
    read = public_result({**_result({"activity": reading}), "status": "completed", "error_code": None},
                         _scenario())["details"]
    assert read == {"mail": {"subject": "工作安排", "body": "周一上午交会议纪要。"}}
    unread = {**reading, "data": {}}
    assert public_result({**_result({"activity": unread}), "status": "in_progress", "error_code": None},
                         _scenario())["details"] == {"planned": [{"name": reading["name"], "duration": "约 5 分钟"}]}
    resumed = {"task": ["工作会议纪要（1月5日 00:00 起）"], "activity_ids": ["internal-activity-3"],
               "resumed": [{"name": "工作会议纪要（1月5日 00:00 起）", "planned_duration_seconds": 22_409}]}
    assert public_result({**_result(resumed), "status": "completed", "error_code": None}, _scenario())["details"] == {
        "planned": [{"name": "工作会议纪要（1月5日 00:00 起）", "duration": "约 6 小时"}]}


def test_work_review_keeps_only_public_fields() -> None:
    """工作结果的 work_review 只保留公开汇总与两种逐项判断的公开字段，隐藏参考身份与额外字段都去掉。"""
    item = {"content_category": "decision", "covered": True, "source_supported": True, "contradiction": False,
            "source_excerpt": "25 euros", "rationale": "stated", "passed": True, "reference_id": "hidden-ref"}
    delivery = {"delivery_path": "decisions[0]", "source_supported": True, "contradiction": False,
                "source_excerpt": "25 euros", "rationale": "matches", "passed": True, "oracle": "hidden"}
    review = {"accepted": True, "required_fact_total": 1, "required_fact_passed": 1, "required_fact_missing": 0,
              "missing_content_categories": [], "required_fact_assessments": [item],
              "delivery_assessments": [delivery], "oracle_path": "hidden/path"}
    public = public_result({**_result({"work_review": review}), "status": "completed", "error_code": None},
                           _scenario())
    assert "hidden" not in json.dumps(public) and public["details"] == {}
    assert public["work_review"]["required_fact_assessments"] == [
        {key: value for key, value in item.items() if key != "reference_id"}]
    assert public["work_review"]["delivery_assessments"] == [
        {key: value for key, value in delivery.items() if key != "oracle"}]
