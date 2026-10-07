"""零点计划 plan_day 与计划提醒（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「零点计划与计划提醒」）。

零点计划是运行时的独立后台任务：确定性客户端桩经后台任务的 client 参数注入（同联系人回复），不调用模型；
它不是决策、不调用人物，只经引擎的系统登记入口写入计划。提醒到点只触发一次，可结束睡眠。
"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.footprints.checkpoint import restore_branch

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.documentation_refs import section_text
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import life_tool_schema_sha256
from worldmock.agents.life_prompt import life_prompt_sha256

import worldmock.runtime as runtime_module
from tests.life.conftest import (StubPlanDayClient, advance_next, advance_until_ready, conclude_request, context, drive,
                                 errand_add, pending_names, plan_day_response, settle_day_plans, stage_day_plan,
                                 start_activity, submit_round, report_request, stage_legacy_control_request)
from tests.life.routing_fixtures import (
    COLLEAGUE_TARGET,
    HOME,
    MEAL_ID,
    MEAL_NAME,
    SNACK_ID,
    SNACK_NAME,
    act,
    buy_goods,
    errand_events,
    errand_note,
    place,
    routed,
    run_until,
    with_colleague,
)
from tests.life.scenario_fixtures import create_engine, read_scenario, run_identity, write_scenario
from tests.life.test_errand_reminder import evening
from tests.life.work_schedule_fixtures import OFFICE, declare
from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.life import day_planning, reminders
from worldmock.life.activity_control import PHASE_ACTIVE
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (ActivityStatus, PlanDayRequest, PlanGoalInput, ReviseDayPlanRequest,
                                   SendMessageActionRequest, deepseek_strict_schema)
from worldmock.life.prompt_schemas import TODO_LAYER_SCHEMA
from worldmock.life.schedule import ROUTINE_INTENT_KINDS, NextDecisionQuery, PlanGoal, clock_epoch, next_decision_time
from worldmock.life.settled_text import activity_endings
from worldmock.runtime import settle_pending_day_plans

SCENARIO = "single-person.json"
ZONE = ZoneInfo("Asia/Shanghai")
#: 睡前在收尾里写下的一条记忆（人物自述）。
_MEMORY = {"type": "commitment", "text": "明早七点半起床", "importance": 0.6, "keywords": ["起床"]}


def _at(day: int, hour: int, minute: int = 0) -> int:
    """场景时区 2026 年 1 月 day 日 hour:minute 的绝对时刻。"""
    return int(datetime(2026, 1, day, hour, minute, tzinfo=ZONE).timestamp())


def _goal(intent: str, goal: str, remind: str | None = None, around: str | None = None) -> dict:
    """零点计划输出中的一条目标（提醒时刻与大致时刻都是场景本地 HH:MM 或 null）。"""
    return {"intent_kind": intent, "goal": goal, "remind_time": remind, "around_time": around}


def _late_engine(tmp_path, start: str, run_id: str, base: str = SCENARIO, **params) -> LifeEngine:
    """场景在本地日中途启动，保留起点后的邮件与工资。"""
    return create_engine(base, _late_start(start, **params), tmp_path, run_id)


def _late_start(start: str, **params):
    """返回 mutate(document)：起点改为 start，丢弃起点之前的邮件与工资。"""
    def mutate(document: dict) -> None:
        begin = datetime.fromisoformat(start)
        document["start_at"] = start
        if len(document["agents"]) == 1:
            document["end_at"] = (
                datetime.fromisoformat(document["end_at"]) + timedelta(days=1)
            ).isoformat()
        document["messages"] = []
        for agent in document["agents"]:
            if agent["work_schedule"] is not None:
                agent["work_schedule"]["exceptions"][begin.date().isoformat()] = None
        document["payrolls"] = [item for item in document["payrolls"]
                                if datetime.fromisoformat(item["due_at"]) >= begin]
        document.update(params)

    return mutate


def _beirut_engine(tmp_path: Path) -> LifeEngine:
    """场景时区换成 Asia/Beirut，在夏令时切换前启动。"""
    def mutate(document: dict) -> None:
        document.update(
            timezone="Asia/Beirut",
            start_at="2026-03-28T23:45:00+02:00",
            end_at="2026-03-29T19:00:00+03:00",
            messages=[],
            payrolls=[],
        )

    return create_engine(SCENARIO, mutate, tmp_path, "beirut")


def _sha256(path: Path) -> str:
    """文件字节的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _events(engine: LifeEngine, after: int = 0) -> list:
    """已提交事件（事件序大于 after）。"""
    return [event for event in engine.context.store.read_events(engine.context.store.last_event_seq) if event.event_seq > after]


def _plan_events(engine: LifeEngine) -> list:
    """零点计划的模型证据与登记事件。"""
    return [event for event in _events(engine)
            if event.data.get("purpose") == "plan_day" or event.data.get("kind") == "day_plan_generated"]


def _start(engine: LifeEngine, action: str, name: str) -> str:
    """人物 1 以「自己」开始一项等待或睡眠，返回活动身份。"""
    _, results = submit_round(engine, {1: ("start_activity", start_activity(action, "自己"))}, name)
    assert results[0].status == "in_progress", results[0].error_code
    return results[0].activity_id


def _decide(engine: LifeEngine, name: str, call: tuple | None = None):
    """人物 1 就绪的一轮：提交 call；缺省有待收尾先收尾，有进行中活动时播报，都没有时开始等待。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    active = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_ACTIVE]
    if call is None and pending_names(observed):
        call = ("conclude_activity", conclude_request(*pending_names(observed)))
    elif call is None and active:
        call = ("report_status", report_request(*active[:1], summary="还在歇着。"))
    elif call is None:
        call = ("start_activity", start_activity("等待", "自己"))
    engine.submit_action(DirectToolCall(decision, f"action-{name}", *call))
    return observed, engine.commit_decision_round(name)[0]


def _run_until(engine: LifeEngine, moment: int) -> None:
    """推进并按缺省方式应对途中的决策，直到时钟到达 moment 且人物就绪。"""
    while True:
        assert advance_until_ready(engine)
        if engine.context.state.current_time >= moment:
            return
        while engine.scheduling_state().ready_agent_ids:
            _decide(engine, f"round-{engine.context.state.current_time}-{engine.context.state.version}")


def _revise(*goals: tuple) -> tuple:
    """修订当日目标的请求（意图、文字、提醒，可选第四项大致时刻，缺省为 null）。"""
    return ("revise_day_plan", ReviseDayPlanRequest(goals=[
        PlanGoalInput(intent_kind=intent, goal=goal, remind_time=remind, around_time=(around or [None])[0])
        for intent, goal, remind, *around in goals]))


def _forbid_client(monkeypatch) -> None:
    """断言后台任务不构造零点计划客户端。"""
    def forbidden(model_identity):
        raise AssertionError("plan day client must not be built")

    monkeypatch.setattr(runtime_module, "PlanDayClient", forbidden)


def test_start_path_generates_the_day_plan_once_and_leaves_the_person_untouched(engine_factory, monkeypatch):
    """起点恰为零点：只按启动路径生成一次；不改重估、体感与在场参照，不产生人物事件，首个决策仍带 run_start。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    fields = ("last_decision_time", "felt_bands_at_last_decision", "people_at_last_decision",
              "pending_wake_reasons", "next_decision_at")
    before = {name: getattr(engine.context.state.agents[1], name) for name in fields}
    seq = engine.context.store.last_event_seq
    client = StubPlanDayClient(goals=[_goal("工作", "上午把周报写完", "09:30", "10:00"),
                                      _goal("用餐", "中午推迟到十二点一刻去楼下吃面", None, "12:15")])

    assert drive(settle_pending_day_plans(engine, client=client)) == [
        {"agent_id": 1, "date": "2026-01-05", "goals": 2}]

    agent = engine.context.state.agents[1]
    assert agent.plan_submitted_dates == {"2026-01-05"} and agent.day_plan.day_end == _at(6, 0)
    assert [(goal.intent_kind, goal.registered_at, goal.remind_at, goal.reminded_at, goal.around_at)
            for goal in agent.day_plan.goals] == [("工作", start, _at(5, 9, 30), None, _at(5, 10)),
                                                  ("用餐", start, None, None, _at(5, 12, 15))]
    assert {name: getattr(agent, name) for name in fields} == before
    request, response, registered = _events(engine, seq)
    assert (request.event_type, response.event_type, registered.event_type) == (
        "model_request", "model_response", "environment_event")
    assert {request.data["purpose"], response.data["purpose"]} == {"plan_day"}
    assert (request.visibility, request.decision_id, response.caused_by) == ("system", None, [request.event_id])
    assert (response.data["phase"], response.data["error"], response.data["usage"]["total_tokens"]) == (
        "response", None, 39)
    assert (registered.data["kind"], registered.visibility, registered.caused_by) == (
        "day_plan_generated", "system", [response.event_id])
    assert registered.data["day_plan"] == agent.day_plan.model_dump(mode="json")
    sent = client.calls[0]
    assert (sent["max_tokens"], sent["tool_choice"], sent["max_retries"]) == (
        day_planning.PLAN_DAY_MAX_TOKENS, "required", 0)
    assert sent["messages"][0]["content"] == day_planning.PLAN_DAY_PROMPT
    assert [tool["function"]["name"] for tool in sent["tools"]] == ["submit_day_plan"]
    _forbid_client(monkeypatch)
    assert drive(settle_pending_day_plans(engine)) == []
    decision = context(engine, "first")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert "run_start" in observed["wake_reasons"]
    goals = observed["todo_layer"]["day_plan"]["goals"]
    assert [(row["intent_kind"], row["progressed"], row["remind_time"], row["around_time"]) for row in goals] == [
        ("工作", False, "09:30", "10:00"), ("用餐", False, None, "12:15")]
    assert not [event for event in _events(engine, seq)
                if event.event_type in {"decision_started", "memory_updated", "action_requested"}]


def test_mid_day_start_attaches_the_error_and_retries_invalid_output(tmp_path):
    """本地日中途启动时以起点为生成时刻；输出不合法（JSON、提醒早于生成时刻、格式）附回英文错误重试。"""
    engine = _late_engine(tmp_path, "2026-01-05T08:00:00+08:00", "mid-day")
    start = engine.context.state.current_time
    invalid = [plan_day_response(arguments="not json"),
               plan_day_response([_goal("工作", "上午把周报写完", "07:30")]),
               plan_day_response([_goal("工作", "上午把周报写完", "25:00")])]
    client = StubPlanDayClient(goals=[_goal("工作", "上午把周报写完", "09:30")], responses=invalid)

    assert settle_day_plans(engine, client)[0]["goals"] == 1

    goal = engine.context.state.agents[1].day_plan.goals[0]
    assert (goal.registered_at, goal.remind_at) == (start, _at(5, 9, 30))
    evidence = [event for event in _plan_events(engine) if event.event_type != "environment_event"]
    assert [(event.data["phase"], event.data["attempt"]) for event in evidence] == [
        (phase, attempt) for attempt in (1, 2, 3, 4) for phase in ("request", "response")]
    errors = [event.data["error"] for event in evidence if event.data["phase"] == "response"]
    assert all(errors[:3]) and errors[3] is None
    assert "later than the generation time 08:00" in errors[1] and "remind_time" in errors[2]
    assert evidence[2].caused_by == [evidence[1].event_id]
    retry = client.calls[1]["messages"]
    assert retry[:2] == client.calls[0]["messages"]
    assert json.loads(retry[2]["content"])["VALIDATION_ERROR"] == errors[0]
    payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert payload["回顾时段"] == "1月5日 00:00 至 1月5日 08:00"
    assert payload["生成时刻"].startswith("1月5日 08:00") and payload["计划日期"] == "2026-01-05"


def test_invalid_output_after_three_retries_is_a_run_fault_with_evidence(engine_factory):
    """输出一直不合法：首次之外再附回重试 3 次，之后作运行故障并保留全部请求、响应与错误证据，不用空计划兜底。"""
    engine = engine_factory()
    client = StubPlanDayClient(responses=[plan_day_response(arguments="{}") for _ in range(4)])

    with pytest.raises(ProtocolError, match="remained invalid after three retries"):
        drive(settle_pending_day_plans(engine, client=client))

    evidence = _plan_events(engine)
    assert len(client.calls) == 4
    assert [event.event_type for event in evidence] == ["model_request", "model_response"] * 4
    assert all("goals" in event.data["error"] for event in evidence[1::2])
    assert engine.context.state.agents[1].day_plan is None and not engine.context.state.agents[1].plan_submitted_dates


def test_system_entry_refuses_a_second_registration_and_an_invalid_plan(engine_factory):
    """系统登记入口不进人物工具面：目标不合法与同日重复登记都是协议错误（调用方先按纯函数与 plan_submitted_dates
    拦下）；没有待执行的零点判定段时零点节律入口拒绝；只有设了提醒的目标才有触发时刻。"""
    engine = engine_factory()
    early = PlanDayRequest(goals=[PlanGoalInput(intent_kind="工作", goal="写周报", remind_time="00:00",
                                                around_time=None),])

    with pytest.raises(ProtocolError, match="Generated day plan is invalid"):
        engine.register_day_plan(1, early, [], [])
    settle_day_plans(engine)
    with pytest.raises(ProtocolError, match="already generated"):
        engine.register_day_plan(1, PlanDayRequest(goals=[]), [], [])
    with pytest.raises(ProtocolError, match="No midnight rhythm is pending"):
        engine.decide_midnight_rhythm()
    with pytest.raises(ValueError, match="reminder trigger requires an earlier reminder"):
        PlanGoal(intent_kind="工作", goal="写周报", registered_at=0, reminded_at=10)
    assert "action_requested" not in {event.event_type for event in _events(engine)}


def test_transport_failure_is_a_run_fault_after_the_evidence_is_kept(engine_factory):
    """传输失败：先提交请求与失败证据再抛出，按运行故障处理。"""
    engine = engine_factory()

    with pytest.raises(ConnectionError, match="network down"):
        drive(settle_pending_day_plans(engine, client=StubPlanDayClient(raises=ConnectionError("network down"))))

    request, failure = _plan_events(engine)
    assert (request.data["phase"], failure.data["phase"], failure.caused_by) == (
        "request", "failure", [request.event_id])
    assert "network down" in failure.data["error"] and engine.context.state.agents[1].day_plan is None


@pytest.mark.parametrize(("goals", "reasons"), [
    ([_goal("工作", "上午把周报写完", "09:30")], ["reconsideration_interval"]),
    ([], ["reconsideration_interval"]),
])
def test_midnight_settles_the_old_day_then_plans_then_judges_the_second(tmp_path, goals, reasons):
    """零点边界：先结算旧日（末段推进计入旧计划）并使旧计划失效，再生成新计划，然后才判定该秒的唤醒与候选。

    人物只在等待：新计划有未推进目标时在手承诺按新计划计，用较短重估间隔在零点获得决策；新计划为空时不获得决策。
    """
    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", f"midnight-{len(goals)}",
                          activity_feedback_seconds=7200, progress_report_interval_seconds=86400,
                          reconsideration_interval_seconds=900, idle_reconsideration_interval_seconds=1200)
    settle_day_plans(engine, StubPlanDayClient(goals=[_goal("休息", "睡前歇一会儿")]))
    waiting = _start(engine, "等待", "evening-wait")
    seq = engine.context.store.last_event_seq
    boundary = engine.scheduling_state().next_event_at
    assert int(boundary.timestamp()) == _at(6, 0)

    engine.advance(900, boundary)

    assert engine.midnight_rhythm_pending and engine.context.state.agents[1].day_plan is None
    with pytest.raises(ProtocolError, match="Midnight rhythm"):
        engine.scheduling_state()
    committed = _events(engine, seq)
    progress = next(event for event in committed if event.event_type == "activity_progress")
    assert progress.data["day_plan"]["day_end"] == _at(6, 0)
    assert progress.data["day_plan"]["goals"][0]["progressed_at"] == _at(6, 0)
    assert not [event for event in committed if event.data.get("kind") == "body_settlement"]
    client = StubPlanDayClient(goals=goals)
    settle_day_plans(engine, client)
    payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert payload["已核验经历"] == ["1月5日 23:45–00:00 等待了约 15 分钟"]
    assert payload["上一天的目标"] == [{"意图": "休息", "目标": "睡前歇一会儿", "推进": "已推进"}]
    after = _events(engine, seq)
    kinds = [event.data.get("kind") or event.event_type for event in after]
    assert kinds.index("activity_progress") < kinds.index("day_plan_generated") < kinds.index("body_settlement")
    settlement = next(event for event in after if event.data.get("kind") == "body_settlement")
    agent = engine.context.state.agents[1]
    assert settlement.data["wake_reasons"] == reasons == agent.pending_wake_reasons
    assert settlement.data["decision_offered"] is bool(reasons)
    assert agent.next_decision_at == (_at(6, 0) if reasons else _at(6, 0, 5))
    assert engine.context.state.activities[waiting].status == "active"
    if reasons:
        # 零点计划与决策同秒：计划之后照常决策，无运行故障。
        assert _decide(engine, "midnight-report")[1].status == "completed"


def test_midnight_is_the_local_date_change_when_dst_skips_00_00(tmp_path):
    """夏令时在零点拨快的日子没有 00:00（Asia/Beirut 2026-03-29 从 01:00 开始）：本地日期变更的那一秒就是零点边界，
    推进把该秒的节律判定留到零点计划之后，零点计划为新的一日生成一次；零点回拨后重复出现的 00:00 不是零点。
    """
    beirut = ZoneInfo("Asia/Beirut")
    engine = _beirut_engine(tmp_path)
    try:
        settle_day_plans(engine)
        slept = _start(engine, "睡觉", "bedtime")
        boundary = engine.scheduling_state().next_event_at
        assert boundary == datetime(2026, 3, 29, 1, 0, tzinfo=beirut)

        engine.advance(int(boundary.timestamp()) - engine.context.state.current_time, boundary)

        assert engine.midnight_rhythm_pending
        client = StubPlanDayClient(goals=[_goal("晨间例程", "起床洗漱", "07:00")])
        settle_day_plans(engine, client)
        agent = engine.context.state.agents[1]
        assert agent.plan_submitted_dates == {"2026-03-28", "2026-03-29"}
        assert agent.day_plan.day_end == int(datetime(2026, 3, 30, tzinfo=beirut).timestamp())
        assert agent.day_plan.goals[0].remind_at == int(datetime(2026, 3, 29, 7, 0, tzinfo=beirut).timestamp())
        payload = json.loads(client.calls[0]["messages"][1]["content"])
        assert payload["回顾时段"] == "3月28日 00:00 至 3月29日 01:00"
        assert engine.context.state.activities[slept].status == "active" and not engine.scheduling_state().ready_agent_ids
    finally:
        engine.close()
    fall_back = int(datetime(2026, 11, 1, tzinfo=ZoneInfo("America/Havana")).timestamp())
    assert reminders.is_local_midnight(fall_back, "America/Havana")
    assert not reminders.is_local_midnight(fall_back + 3600, "America/Havana")


def test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once(tmp_path):
    """跨零点的长睡眠中没有生活决策；新计划的提醒进入候选，到点结束睡眠并按实际时长结算，只触发一次。"""
    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "night")
    settle_day_plans(engine)
    slept = _start(engine, "睡觉", "bedtime")
    assert engine.context.state.activities[slept].planned_end_at > _at(6, 5)
    boundary = engine.scheduling_state().next_event_at
    engine.advance(int(boundary.timestamp()) - engine.context.state.current_time, boundary)
    settle_day_plans(engine, StubPlanDayClient(goals=[_goal("晨间例程", "起床洗漱", "05:00")]))
    assert not engine.scheduling_state().ready_agent_ids
    assert reminders.reminder_times(engine.context.state.agents[1], engine.context.state.current_time) == (_at(6, 5),)

    assert advance_until_ready(engine) and engine.context.state.current_time == _at(6, 5)

    sleep = engine.context.state.activities[slept]
    assert (sleep.status, sleep.ended_at, sleep.boundary_type, sleep.interruption_reason) == (
        "completed", _at(6, 5), "plan_reminder", "plan_reminder")
    assert sleep.elapsed_seconds == _at(6, 5) - sleep.started_at
    observed, _ = _decide(engine, "woken")
    assert {"planned_end", "wake_from_sleep", "no_active_activity", "plan_reminder_due"} <= set(
        observed["wake_reasons"])
    assert _reminders(observed) == ["起床洗漱"]
    assert engine.context.state.agents[1].day_plan.goals[0].reminded_at == _at(6, 5)
    observed, _ = _decide(engine, "after-conclusion")
    assert "wake_from_sleep" not in observed["wake_reasons"] and _reminders(observed) == ["起床洗漱"]
    assert advance_until_ready(engine) and engine.context.state.current_time > _at(6, 5)
    observed, _ = _decide(engine, "later")
    # 提醒记录由提醒写入时刻推导，在本段停留内保留且只出现一次；不再唤醒。
    assert _reminders(observed) == ["起床洗漱"] and "plan_reminder_due" not in observed["wake_reasons"]
    assert reminders.reminder_times(engine.context.state.agents[1], engine.context.state.current_time) == ()


def test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end(engine_factory):
    """计划结束与提醒同秒：睡眠记 boundary_type=planned_end，提醒记为已触发，此后不再触发；无运行故障。"""
    engine = engine_factory()
    settle_day_plans(engine)
    slept = _start(engine, "睡觉", "sleep")
    end = engine.context.state.activities[slept].planned_end_at
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="晨间例程", goal="起床", registered_at=engine.context.state.current_time,
                                           remind_at=end),))

    assert advance_until_ready(engine) and engine.context.state.current_time == end

    sleep = engine.context.state.activities[slept]
    assert (sleep.status, sleep.boundary_type, sleep.interruption_reason) == ("completed", "planned_end", None)
    agent = engine.context.state.agents[1]
    assert agent.day_plan.goals[0].reminded_at == end
    assert {"planned_end", "wake_from_sleep", "plan_reminder_due"} <= set(agent.pending_wake_reasons)
    assert reminders.reminder_times(agent, end) == () and not reminders.reminder_due(agent, end + 1)


def test_sleep_keeps_the_reminder_among_the_next_decision_candidates():
    """睡眠中重估间隔与截止预警被剔除，当日未触发的提醒不剔除：下一决策时刻是提醒，早于睡眠的计划结束。"""
    query = NextDecisionQuery(now=_at(5, 0), planned_end=_at(5, 8), last_decision_time=_at(5, 0),
                              next_task_due_at=_at(5, 3), plan_reminder_times=(_at(5, 7),), is_asleep=True)

    assert next_decision_time(query) == _at(5, 7)
    assert next_decision_time(query.model_copy(update={"plan_reminder_times": ()})) == _at(5, 8)


def test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep(engine_factory):
    """提醒进入推进边界候选不依赖人物的 next_decision_at（恢复与反事实分支按世界状态里的计划推进）：只置计划、
    不经节律重算时，下一边界仍是提醒时刻，推进到点结束睡眠（boundary_type=plan_reminder）。
    """
    engine = engine_factory()
    settle_day_plans(engine)
    slept = _start(engine, "睡觉", "sleep")
    remind = engine.context.state.activities[slept].started_at + 60
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="晨间例程", goal="起床", registered_at=engine.context.state.current_time,
                                           remind_at=remind),))
    assert engine.context.state.agents[1].next_decision_at > remind

    assert int(engine.scheduling_state().next_event_at.timestamp()) == remind
    advance_next(engine)

    sleep = engine.context.state.activities[slept]
    assert (engine.context.state.current_time, sleep.status, sleep.ended_at, sleep.boundary_type) == (
        remind, "completed", remind, "plan_reminder")
    agent = engine.context.state.agents[1]
    assert agent.day_plan.goals[0].reminded_at == remind and "plan_reminder_due" in agent.pending_wake_reasons


def test_reminders_wake_once_merge_and_fire_after_the_goal_progressed(engine_factory):
    """同时到期的提醒合并为一次唤醒；被等待推进过的目标照样按时提醒；空提醒不唤醒；等待不被提醒停止。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    remind = start + 1200
    stage_day_plan(engine, submitted=True, goals=(
        PlanGoal(intent_kind="休息", goal="歇一会儿", registered_at=start, remind_at=remind),
        PlanGoal(intent_kind="晨间例程", goal="起床洗漱", registered_at=start, remind_at=remind),
        PlanGoal(intent_kind="娱乐", goal="晚上看会儿剧", registered_at=start)))
    _start(engine, "等待", "first-wait")

    _run_until(engine, remind)

    assert engine.context.state.current_time == remind
    agent = engine.context.state.agents[1]
    assert agent.day_plan.goals[0].progressed_at is not None
    assert agent.pending_wake_reasons.count("plan_reminder_due") == 1
    observed, result = _decide(engine, "reminded", _revise(("工作", "写周报", "00:00")))
    assert _reminders(observed) == ["歇一会儿", "起床洗漱"]
    assert result.error_code == "PLAN_REMIND_TIME_INVALID"
    assert [row["phase"] for row in observed["activity_bindings"]].count(PHASE_ACTIVE) == 1
    # 同一秒的后续同刻轮次：提醒记录仍各只出现一次，不重复写出。
    observed, result = _decide(engine, "after-rejection")
    assert _reminders(observed) == ["歇一会儿", "起床洗漱"] and result.status == "completed"
    assert [goal.reminded_at for goal in engine.context.state.agents[1].day_plan.goals] == [remind, remind, None]
    assert advance_until_ready(engine)
    assert "plan_reminder_due" not in engine.context.state.agents[1].pending_wake_reasons


def _reminders(observed: dict) -> list[str]:
    """进度层的目标提醒记录（只写目标文字，按计划顺序）。"""
    return [row["goal"] for row in observed["progress_layer"]["events"] if row["type"] == "plan_reminder"]


def test_revision_keeps_replaces_and_cancels_reminders(engine_factory):
    """修订：原样条目保留登记与提醒状态（已触发的原样重发不被拒绝），只改提醒的换新提醒，被删除的取消旧提醒。

    remind_time 格式错误以 ACTION_REQUEST_INVALID 拒绝；不晚于修订时刻以 PLAN_REMIND_TIME_INVALID 拒绝。
    """
    engine = engine_factory()
    start = engine.context.state.current_time
    stage_day_plan(engine, submitted=True, goals=(
        PlanGoal(intent_kind="工作", goal="写周报", registered_at=start, remind_at=start + 1200),
        PlanGoal(intent_kind="社交", goal="给老朋友打电话", registered_at=start, remind_at=start + 7200),
        PlanGoal(intent_kind="用餐", goal="吃夜宵", registered_at=start)))

    _, revised = _decide(engine, "revise", _revise(("工作", "写周报", "00:20"), ("用餐", "吃夜宵", "01:00"),
                                                   ("娱乐", "看会儿剧", "04:00")))
    assert revised.status == "completed"
    goals = {goal.intent_kind: goal for goal in engine.context.state.agents[1].day_plan.goals}
    assert set(goals) == {"工作", "用餐", "娱乐"}
    assert (goals["用餐"].registered_at, goals["用餐"].remind_at) == (start, start + 3600)
    assert start + 7200 not in reminders.boundary_candidates(engine.context.state, engine.context.scenario.timezone)
    bad = ReviseDayPlanRequest.model_construct(goals=[
        PlanGoalInput.model_construct(intent_kind="工作", goal="写周报", remind_time="8:15", around_time=None)])
    assert _decide(engine, "bad-format", ("revise_day_plan", bad))[1].error_code == "ACTION_REQUEST_INVALID"
    late = _revise(("工作", "写周报", "00:20"), ("用餐", "吃夜宵", "00:00"))
    assert _decide(engine, "too-early", late)[1].error_code == "PLAN_REMIND_TIME_INVALID"
    _decide(engine, "wait")
    _run_until(engine, start + 1200)
    assert engine.context.state.current_time == start + 1200
    assert engine.context.state.agents[1].day_plan.goals[0].reminded_at == start + 1200
    same = _revise(("工作", "写周报", "00:20"), ("用餐", "吃夜宵", "01:00"), ("娱乐", "看会儿剧", "04:00"))
    assert _decide(engine, "resend", same)[1].status == "completed"
    kept = engine.context.state.agents[1].day_plan.goals[0]
    assert (kept.registered_at, kept.remind_at, kept.reminded_at) == (start, start + 1200, start + 1200)


def test_revision_carries_around_time_and_changing_only_it_keeps_registration(engine_factory):
    """revise_day_plan 的条目带大致时刻（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进）：登记为当日绝对时刻 around_at，不进提醒候选、
    不触发决策；格式错误以 ACTION_REQUEST_INVALID 拒绝；四字段比较中只改大致时刻的修订保留登记时刻与已触发的提醒状态
    （早于修订时刻的大致时刻也受理，提醒不重校、不再触发），待办层写「约 HH:MM」。"""
    engine = engine_factory()
    start, zone = engine.context.state.current_time, engine.context.scenario.timezone
    stage_day_plan(engine, submitted=True, goals=(
        PlanGoal(intent_kind="用餐", goal="午饭推迟到一点", registered_at=start, remind_at=start + 1200),))
    _, revised = _decide(engine, "around", _revise(("用餐", "午饭推迟到一点", "00:20", "13:00")))
    assert revised.status == "completed"
    [goal] = engine.context.state.agents[1].day_plan.goals
    assert (goal.registered_at, goal.remind_at, goal.around_at) == (start, start + 1200, _at(5, 13))
    assert _at(5, 13) not in reminders.boundary_candidates(engine.context.state, zone)
    bad = ReviseDayPlanRequest.model_construct(goals=[PlanGoalInput.model_construct(
        intent_kind="用餐", goal="午饭推迟到一点", remind_time="00:20", around_time="13")])
    assert _decide(engine, "bad-around", ("revise_day_plan", bad))[1].error_code == "ACTION_REQUEST_INVALID"
    _decide(engine, "wait")
    _run_until(engine, start + 1200)
    assert engine.context.state.agents[1].day_plan.goals[0].reminded_at == start + 1200
    _, moved = _decide(engine, "earlier", _revise(("用餐", "午饭推迟到一点", "00:20", "00:05")))
    assert moved.status == "completed"
    [goal] = engine.context.state.agents[1].day_plan.goals
    assert (goal.registered_at, goal.remind_at, goal.reminded_at, goal.around_at) == (
        start, start + 1200, start + 1200, _at(5, 0, 5))
    assert reminders.reminder_times(engine.context.state.agents[1], engine.context.state.current_time) == ()
    row = engine.observe(context(engine, "around-shown"))["todo_layer"]["day_plan"]["goals"][0]
    assert (row["remind_time"], row["around_time"]) == ("00:20", "00:05")


def test_zero_hour_plan_items_carry_around_time_and_a_malformed_one_is_retried(tmp_path):
    """零点计划的条目带大致时刻（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进）：格式错误或缺了 around_time 的输出附回英文错误重试；
    早于生成时刻的大致时刻不是错误（不像提醒），登记为当日绝对时刻。"""
    engine = _late_engine(tmp_path, "2026-01-05T08:00:00+08:00", "around-plan")
    invalid = [plan_day_response([_goal("用餐", "午饭推迟到一点", None, "13")]),
               plan_day_response([{"intent_kind": "用餐", "goal": "午饭推迟到一点", "remind_time": None}])]
    client = StubPlanDayClient(goals=[_goal("用餐", "午饭推迟到一点", None, "13:00"),
                                      _goal("通勤", "今天晚半小时出门", None, "07:30")], responses=invalid)

    assert settle_day_plans(engine, client)[0]["goals"] == 2

    errors = [event.data["error"] for event in _plan_events(engine) if event.data.get("phase") == "response"]
    assert "around_time" in errors[0] and "around_time" in errors[1] and errors[2] is None
    assert [(goal.intent_kind, goal.around_at, goal.remind_at) for goal in engine.context.state.agents[1].day_plan.goals] == [
        ("用餐", _at(5, 13), None), ("通勤", _at(5, 7, 30), None)]


def _contract_plan_template() -> str:
    """接口合同「请求组成」逐字登记的零点计划中文模板。"""
    text = section_text((Path(__file__).resolve().parents[2] / "docs" / "CONTRACTS.md").read_text(encoding="utf-8"),
                        "contracts-model-request")
    block = re.search(r"```text\n(.*?)\n```", text.split("零点计划的中文模板（system）：", 1)[1], flags=re.DOTALL)
    assert block is not None
    return block.group(1)


def test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times(engine_factory):
    """零点计划模板（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事、8.2，裁决 S11、D12；docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-零点计划修订与只读问答；
    第三、五、六条按 docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-零点计划模板 改写）：真实零点计划请求的 system 就是
    接口合同逐字登记的模板，仍为八条。开头的输入说明末行写明还没办完的个人事务；第五条：人物档案与背景里天天如此的事（睡觉、
    起床、上下班、吃饭、休息、每天的消遣）不写成目标，只写今天与平常不同的安排（写清楚哪里不同）、要办的事与照第三条要提醒
    的事，清单可以很短；第六条：每条目标写大致时刻 around_time，它只表示大概在那前后、不是开始的钟点，goal 里只给和人约好的
    事写钟点；第七条：个人事务已在待办里，不必照抄成目标，今天打算去办的可写一条目标说明何时办；第三条：要按时起床的填起床
    的时刻，出门去上班、上课、接人、赴约这类要按时到的事填该出门的时刻，几点去睡也不设提醒（S2 撤回），收尾时写明的起床、
    出门时刻照旧定提醒，改写前「不需要提醒」时的兜底句不再出现。第八条（上班安排，docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6）
    接在第七条之后。user 输入带人物档案的作息窗口，输出 Schema 要求 around_time。"""
    engine = engine_factory()
    client = StubPlanDayClient()
    settle_day_plans(engine, client)
    [request] = client.calls
    system = request["messages"][0]["content"]
    assert system == day_planning.PLAN_DAY_PROMPT == _contract_plan_template()
    opening, *chunks = re.split(r"\n(?=[一二三四五六七八]、)", system)
    items = {chunk[0]: chunk.replace("\n", "") for chunk in chunks}
    assert list(items) == ["一", "二", "三", "四", "五", "六", "七", "八"]
    assert opening.split("\n")[2] == ("以及此刻他能看到的待办任务、还没办完的个人事务、收件箱、在途订单、联系人和上一天目标的"
                                      "推进情况。")
    assert items["五"] == ("五、人物档案与背景里天天如此的事（睡觉、起床、上下班、吃饭、休息、每天的消遣）不写成目标；"
                          "只写今天与平常不同的安排（写清楚哪里不同）、要办的事，以及照第三条要提醒的事。清单可以很短。")
    assert items["六"].startswith("六、每条目标都写大致时刻 around_time") and "时段的中点" in items["六"]
    assert "不是开始的钟点" in items["六"] and "goal 里只给和人约好的事写钟点" in items["六"]
    assert items["七"] == "七、个人事务已经记在待办里，不必照抄成目标；今天打算去办的，可以写一条目标说明什么时候办。"
    assert "写零到八条目标" in items["一"]
    assert "几点起床或几点出门的，就给对应的目标定这个时刻的提醒" in items["三"]
    assert "出门去上班、上课、接人、赴约这类要按时到的事填该出门的时刻" in items["三"]
    assert "几点去睡也不设提醒" in items["三"]
    assert "不需要提醒就填 null" not in system
    assert "就寝" not in system and "睡前" not in system
    payload = json.loads(request["messages"][1]["content"])
    assert payload["人物"] == {"名字": engine.context.state.agents[1].name}
    goal_schema = request["tools"][0]["function"]["parameters"]["properties"]["goals"]["items"]
    assert set(goal_schema["required"]) == {"intent_kind", "goal", "remind_time", "around_time"}
    assert engine.context.state.agents[1].day_plan.goals == ()



def test_plan_template_first_line_and_rule_eight_are_the_contract_text(engine_factory):
    """零点计划模板的阶段 3 改写（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6）：真实零点计划请求的 system 就是模块常量与接口合同
    逐字登记的模板；输入说明首行与规格 4.6 的定稿逐字相同，在「人物档案与背景原文、」之后写「当天的上班安排、」；第八条
    与定稿逐字相同，排在第七条之后、是模板的末行。阶段 2 改写的输入说明末行与第七条逐字保留，模板仍不提就寝、睡前。"""
    engine = engine_factory()
    client = StubPlanDayClient()
    settle_day_plans(engine, client)
    [request] = client.calls
    system = request["messages"][0]["content"]
    contract_lines = _contract_plan_template().splitlines()
    first_line, rule_eight = contract_lines[0], contract_lines[-1]

    assert system == day_planning.PLAN_DAY_PROMPT == _contract_plan_template()
    lines = system.split("\n")
    assert lines[0] == first_line == ("你要为一个人写出他这一天的当日目标清单。用户消息是一份 JSON，只含世界已经记下的事实："
                                      "人物档案与背景原文、当天的上班安排、")
    assert lines[2] == "以及此刻他能看到的待办任务、还没办完的个人事务、收件箱、在途订单、联系人和上一天目标的推进情况。"
    assert lines[-2:] == ["七、个人事务已经记在待办里，不必照抄成目标；今天打算去办的，可以写一条目标说明什么时候办。",
                          rule_eight]
    assert rule_eight == "八、输入里有上班安排的日子，目标不要安排在上班时间离开上班地点，写明的休息时段和工作要求去别处的外出除外。"
    assert [line for line in lines if "上班安排" in line] == [first_line, rule_eight]
    assert "就寝" not in system and "睡前" not in system


#: 零点计划写出的四条例行意图目标（意图、文字、大致时刻）：今天与平常不同的作息安排，都不设提醒。
_ROUTINE_PLANNED = (("睡眠", "今晚看完球赛再睡，比平常晚一些", "23:30"), ("晨间例程", "起床后先下楼跑两圈再洗漱", "07:00"),
                    ("通勤", "今天改坐公交去公司", "08:30"), ("用餐", "午饭和同事去新开的面馆", "12:15"))
#: 人物随后修订成的四条例行意图目标（意图、文字、提醒、大致时刻）：换了说法，仍不设提醒。
_ROUTINE_REVISED = (("睡眠", "球赛改到明天，今晚早点睡", None, "22:30"), ("晨间例程", "起床后不跑步了，直接洗漱", None, "07:00"),
                    ("通勤", "下雨，改坐地铁去公司", None, "08:15"), ("用餐", "晚饭回家自己煮面", None, "18:30"))


def test_routine_goals_without_reminders_are_accepted_by_zero_hour_plan_and_revision(engine_factory):
    """模板第五条只改模板文字、不进校验器（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-零点计划模板，裁决 D1）：
    例行意图（睡眠、晨间例程、通勤、用餐，恰为 ROUTINE_INTENT_KINDS）且 remind_time 为 null 的四条目标，经 parse_plan_day
    原样解析，经零点计划后台任务一次受理并登记（没有附回重试），提醒为空、大致时刻换算为当日绝对时刻；人物随后经
    revise_day_plan 把四条换成新的说法、仍不设提醒，照常受理。待办层逐条呈现意图、文字、是否已推进、提醒（null）与大致
    时刻（HH:MM）五键，与改写前同形。"""
    engine = engine_factory()
    start, zone = engine.context.state.current_time, engine.context.scenario.timezone
    goals = [_goal(intent, text, None, around) for intent, text, around in _ROUTINE_PLANNED]
    assert {goal["intent_kind"] for goal in goals} == ROUTINE_INTENT_KINDS
    parsed = day_planning.parse_plan_day(plan_day_response(goals), start, zone)
    assert [goal.model_dump() for goal in parsed.goals] == goals
    client = StubPlanDayClient(goals=goals)

    assert settle_day_plans(engine, client) == [{"agent_id": 1, "date": "2026-01-05", "goals": 4}]

    errors = [event.data["error"] for event in _plan_events(engine) if event.data.get("phase") == "response"]
    assert len(client.calls) == 1 and errors == [None]
    assert [(goal.intent_kind, goal.remind_at, goal.around_at)
            for goal in engine.context.state.agents[1].day_plan.goals] == [
        ("睡眠", None, _at(5, 23, 30)), ("晨间例程", None, _at(5, 7)), ("通勤", None, _at(5, 8, 30)),
        ("用餐", None, _at(5, 12, 15))]
    _, revised = _decide(engine, "revise-routine", _revise(*_ROUTINE_REVISED))
    assert revised.status == "completed"
    assert [(goal.intent_kind, goal.goal, goal.registered_at, goal.remind_at, goal.around_at)
            for goal in engine.context.state.agents[1].day_plan.goals] == [
        (intent, text, start, None, clock_epoch(around, start, zone)) for intent, text, _, around in _ROUTINE_REVISED]
    rows = engine.observe(context(engine, "routine-shown"))["todo_layer"]["day_plan"]["goals"]
    assert rows == [{"intent_kind": intent, "goal": text, "progressed": False, "remind_time": None,
                     "around_time": around} for intent, text, _, around in _ROUTINE_REVISED]


#: 提醒时刻与大致时刻在两个工具参数 Schema 里的共同形状：HH:MM 或 null。
_CLOCK_OR_NULL = {"anyOf": [{"pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", "type": "string"}, {"type": "null"}]}


def test_goal_time_schema_and_todo_rendering_stay_and_the_template_is_outside_both_fingerprints(monkeypatch):
    """模板改写不动字段（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-零点计划模板，裁决 D2）：submit_day_plan 与
    revise_day_plan 的目标条目 Schema 相同，四键必填，remind_time 与 around_time 都是 HH:MM 或 null；待办层目标仍是五键，
    两个时刻写 HH:MM 或 null。模板不在 prompt_sha256 与 tool_schema_sha256 之内：把模块常量换成别的文字，零点计划请求
    随之改变，两个指纹不变（模板只由 code_sha256 绑定）。"""
    plan_tool = day_planning.prepare_plan_day({}, "deepseek-flash")["tools"][0]["function"]
    plan_item = plan_tool["parameters"]["properties"]["goals"]["items"]
    assert plan_tool["strict"] is True
    assert plan_item == deepseek_strict_schema(ReviseDayPlanRequest)["properties"]["goals"]["items"]
    assert plan_item["required"] == ["intent_kind", "goal", "remind_time", "around_time"]
    assert plan_item["properties"]["remind_time"] == {**_CLOCK_OR_NULL, "title": "Remind Time"}
    assert plan_item["properties"]["around_time"] == {**_CLOCK_OR_NULL, "title": "Around Time"}
    todo_goal = TODO_LAYER_SCHEMA["properties"]["day_plan"]["oneOf"][1]["properties"]["goals"]["items"]
    shown = {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"}
    assert todo_goal["required"] == ["intent_kind", "goal", "progressed", "remind_time", "around_time"]
    assert (todo_goal["properties"]["remind_time"], todo_goal["properties"]["around_time"]) == (shown, shown)
    fingerprints = (life_prompt_sha256(), life_tool_schema_sha256())
    monkeypatch.setattr(day_planning, "PLAN_DAY_PROMPT", "另一份零点计划模板")
    assert day_planning.prepare_plan_day({}, "deepseek-flash")["messages"][0]["content"] == "另一份零点计划模板"
    assert (life_prompt_sha256(), life_tool_schema_sha256()) == fingerprints


#: 计划输入「上班安排」的休息日取值（与环境层 duty 的休息日同形、不含 status）。
_PLAN_REST_DAY = {"day": "休息日", "hours": None, "place": None, "breaks": []}


def _plan_workday(engine: LifeEngine) -> dict:
    """标准班次（09:00–18:00，午休 12:00–13:30）在办公室的工作日安排；地点写场景里的显示名。"""
    place_name = next(item.name for item in engine.context.scenario.locations if item.location_id == OFFICE)
    return {"day": "工作日", "hours": "09:00–18:00", "place": place_name, "breaks": ["12:00–13:30"]}


def _sent_payload(client: StubPlanDayClient) -> dict:
    """客户端桩记下的首个零点计划请求的 user 输入。"""
    return json.loads(client.calls[0]["messages"][1]["content"])


def test_plan_input_carries_the_plan_dates_duty_arrangement_without_status(engine_factory):
    """零点计划输入「上班安排」（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6）：白领场景在 2026-01-05（周一）零点经启动路径生成
    计划。周一当班的人物得到 {day, hours, place, breaks} 四键（班次与休息时段写 HH:MM–HH:MM，地点写显示名，不写内部
    身份，没有 status）；每周声明里周一为 null 的人物得到休息日四键；没有工作安排的人物为 null。plan_inputs 的载荷与
    客户端实际收到的请求逐值相同。"""
    cases = ((declare(), _plan_workday), (declare(weekly={"0": None}), lambda engine: _PLAN_REST_DAY))
    for mutate, expected in cases:
        engine = engine_factory(mutate=mutate)
        inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload
        client = StubPlanDayClient()
        settle_day_plans(engine, client)
        sent = _sent_payload(client)
        arrangement = expected(engine)

        assert (inputs["计划日期"], sent["计划日期"]) == ("2026-01-05", "2026-01-05")
        assert inputs["上班安排"] == sent["上班安排"] == arrangement
        if arrangement is not None:
            assert set(sent["上班安排"]) == {"day", "hours", "place", "breaks"}
            assert OFFICE not in json.dumps(sent["上班安排"], ensure_ascii=False)


def test_plan_input_duty_is_for_the_new_day_at_the_midnight_boundary(tmp_path):
    """零点边界上「上班安排」按新开始的一日求值（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6）：白领场景改在 2026-01-05（周一）
    23:45 启动，人物的工作安排把周一写成不当班的日期例外、周二照每周声明当班。启动路径的计划日期是 1 月 5 日，输入为
    休息日；人物等待到零点，零点计划的计划日期是 1 月 6 日，输入是周二的工作日安排，而不是刚结束的周一。"""
    late = _late_start("2026-01-05T23:45:00+08:00", activity_feedback_seconds=7200,
                       progress_report_interval_seconds=86400, reconsideration_interval_seconds=900,
                       idle_reconsideration_interval_seconds=1200)
    schedule = declare(exceptions={"2026-01-05": None})

    def mutate(document: dict) -> None:
        """中途启动与工作安排两处改写合在一份场景副本上。"""
        late(document)
        schedule(document)

    engine = create_engine(SCENARIO, mutate, tmp_path, "duty-midnight")
    try:
        started = StubPlanDayClient()
        settle_day_plans(engine, started)
        _start(engine, "等待", "evening-wait")
        boundary = engine.scheduling_state().next_event_at
        assert int(boundary.timestamp()) == _at(6, 0)
        engine.advance(int(boundary.timestamp()) - engine.context.state.current_time, boundary)
        assert engine.midnight_rhythm_pending
        crossed = StubPlanDayClient()
        settle_day_plans(engine, crossed)

        assert (_sent_payload(started)["计划日期"], _sent_payload(started)["上班安排"]) == ("2026-01-05", _PLAN_REST_DAY)
        assert (_sent_payload(crossed)["计划日期"], _sent_payload(crossed)["上班安排"]) == (
            "2026-01-06", _plan_workday(engine))
        assert _sent_payload(crossed)["回顾时段"] == "1月5日 00:00 至 1月6日 00:00"
    finally:
        engine.close()


@pytest.mark.parametrize("scheduled", [0, 1], ids=["first-person-scheduled", "second-person-scheduled"])
def test_plan_input_carries_each_persons_own_duty_arrangement(engine_factory, scheduled: int):
    """零点计划输入带的是被计划的那个人自己的上班安排（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6）：双人场景只给其中一人
    声明工作安排时，这个人的输入是自己的工作日安排，另一人没有工作安排、输入为 null；只给人物 1 声明与只给人物 2 声明
    各跑一次。plan_inputs 的载荷与客户端实际收到的两份零点计划请求（按「人物」的名字对上）逐值相同。取别人的班次时，
    没有工作安排的人会被套上同伴的上班安排。"""
    scheduled_only = declare(scheduled)

    def mutate(document: dict) -> None:
        for index, person in enumerate(document["agents"]):
            person["work_schedule"] = None
            person["work_content_ids"] = ["topic-sales"] if index == scheduled else []
        scheduled_only(document)

    engine = engine_factory("multi-person.json", mutate=mutate)
    people = [(item.agent_id, item.name) for item in engine.context.scenario.agents]
    assert [agent_id for agent_id, _ in people] == [1, 2]
    expected = {name: (_plan_workday(engine) if index == scheduled else None)
                for index, (_, name) in enumerate(people)}
    assert len(expected) == 2
    assert {name: plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, agent_id).payload["上班安排"] for agent_id, name in people} == expected
    client = StubPlanDayClient()
    settle_day_plans(engine, client)
    sent = [json.loads(call["messages"][1]["content"]) for call in client.calls]
    assert len(sent) == 2
    assert {payload["人物"]["名字"]: payload["上班安排"] for payload in sent} == expected


def test_plan_day_input_lists_open_errands_read_only(engine_factory):
    """零点计划输入的只读键「个人事务」（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-零点计划修订与只读问答）：1 月 5 日 23:41 收尾时记下「吃掉纯牛奶 2 份」
    （带备注与次日 08:00 的提醒）与「取得素炒什锦食材包 1 份」，随即喝掉手边的一份奶（累计 1 份）。零点边界的零点计划输入
    以名称、要办成什么、已办到哪、备注、提醒时刻五键列出这两条，与同一时刻待办层的事务行同源同序，不带事务或商品身份，
    也不登记事务事件为来源；「待办任务」仍是此刻可见的工作任务，没有事务。计划登记前后事务逐字段相同，零点计划没有写任何
    事务事件：它只读事务，不创建、不更改、不终结。"""
    engine = evening(engine_factory, "23:40")
    place(engine, 1, "noodle-1")
    errand_note(engine, "note", [errand_add("早饭前喝两份奶", SNACK_NAME, quantity=2, memo="睡前喝一份，明早再喝一份",
                                     remind_time="08:00"), errand_add("周末补食材包", MEAL_NAME)])
    received = buy_goods(engine, "drink")
    assert received.status == "completed", received.error_code
    midnight = _at(6, 0)

    def before_midnight(current) -> bool:
        """下一边界是零点且此刻没人要决策。"""
        state = current.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == midnight
    run_until(engine, before_midnight)
    engine.advance(midnight - engine.context.state.current_time, engine.scheduling_state().next_event_at)
    held, noted = dict(engine.context.state.agents[1].errands), len(errand_events(engine))
    client = StubPlanDayClient()

    settle_day_plans(engine, client)

    payload = json.loads(client.calls[0]["messages"][1]["content"])
    rows = engine.observe(context(engine, "planned"))["todo_layer"]["errands"]
    assert payload["个人事务"] == [
        {"名称": "早饭前喝两份奶", "要办成什么": "取得薯片 2 份", "已办到哪": "已取得 1 份", "备注": "睡前喝一份，明早再喝一份",
         "提醒时刻": "08:00"},
        {"名称": "周末补食材包", "要办成什么": "取得牛肉面 1 份", "已办到哪": "已取得 0 份", "备注": None,
         "提醒时刻": None}]
    assert payload["个人事务"] == [{"名称": row["name"], "要办成什么": row["done_when"], "已办到哪": row["progress"],
                                "备注": row["memo"], "提醒时刻": row["remind_time"]} for row in rows]
    assert payload["待办任务"] == []
    listed = json.dumps(payload["个人事务"], ensure_ascii=False)
    assert all(identity not in listed for identity in (*held, SNACK_ID, MEAL_ID))
    assert "早饭前喝两份奶" not in json.dumps(payload["待办任务"], ensure_ascii=False)
    assert engine.context.state.agents[1].errands == held and len(errand_events(engine)) == noted
    [registered] = [event for event in _plan_events(engine) if event.data.get("kind") == "day_plan_generated"
                    and event.occurred_at == midnight]
    assert not set(registered.data["source_event_ids"]) & {event.event_id for event in errand_events(engine)}


def test_plan_day_input_writes_no_line_for_an_errand_completed_before_midnight(engine_factory):
    """办成句只用于进度层折叠，零点计划输入不写（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现）：1 月 5 日 23:41 收尾时记下「吃掉
    纯牛奶 1 份」，随即喝掉手边那一份，零点前办成（errand_completed 落库，事务移出状态）。零点计划的输入没有「办成」句，
    事务名不出现在任何键里，「个人事务」为空列表；喝奶本身仍作为已核验经历写进输入。"""
    engine = evening(engine_factory, "23:40")
    place(engine, 1, "noodle-1")
    errand_note(engine, "note", [errand_add("睡前喝掉那盒奶", SNACK_NAME)])
    received = buy_goods(engine, "drink")
    assert received.status == "completed", received.error_code
    midnight = _at(6, 0)
    [completed] = [event for event in errand_events(engine) if event.data["kind"] == "errand_completed"]
    assert completed.occurred_at < midnight and engine.context.state.agents[1].errands == {}

    def before_midnight(current) -> bool:
        """下一边界是零点且此刻没人要决策。"""
        state = current.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == midnight
    run_until(engine, before_midnight)
    engine.advance(midnight - engine.context.state.current_time, engine.scheduling_state().next_event_at)
    client = StubPlanDayClient()

    settle_day_plans(engine, client)

    payload = json.loads(client.calls[0]["messages"][1]["content"])
    listed = json.dumps(payload, ensure_ascii=False)
    assert payload["个人事务"] == [] and "办成" not in listed and "睡前喝掉那盒奶" not in listed
    assert any(SNACK_NAME in sentence for sentence in payload["已核验经历"])


def test_midnight_plan_inputs_list_only_the_planned_persons_errands(engine_factory):
    """「个人事务」只列被计划的那个人此刻还没办完的事务（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-零点计划修订与只读问答、2.1；接口合同「请求组成」
    的零点计划输入）。两人场景经真实请求、引擎与 SQLite 跨过本地零点：只有陈曦在 22:51 的收尾里记下一条事务。零点前按人
    装配的输入里，她的「个人事务」列着这一条，李凡的为空；零点那一刻落库的两条 plan_day 模型请求同样如此——各人的输入
    写着各人的名字，事务只在陈曦的那一条里。"""
    def evening_people(document):
        document.update(start_at="2026-01-05T22:50:00+08:00", end_at="2026-01-07T00:00:00+08:00",
                        payrolls=[])
        for message in document["messages"]:
            message["sent_at"] = "2026-01-06T23:59:00+08:00"
        for person in document["agents"]:
            person["work_schedule"] = None
            person["work_content_ids"] = []
    engine = engine_factory("multi-person.json", mutate=evening_people)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    errand_note(engine, "note", [errand_add("补食材包", MEAL_NAME, memo="周末去超市")])
    assert [row["名称"] for row in plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["个人事务"]] == ["补食材包"]
    assert plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 2).payload["个人事务"] == []
    midnight = _at(6, 0)
    run_until(engine, lambda current: current.context.state.current_time > midnight, limit=120)
    sent = {}
    for agent_id in (1, 2):
        [request] = [event for event in engine.context.store.read_agent_events("model_request", agent_id)
                     if event.data.get("purpose") == "plan_day" and event.occurred_at == midnight]
        sent[agent_id] = json.loads(request.data["request"]["messages"][1]["content"])
    assert (sent[1]["人物"]["名字"], sent[2]["人物"]["名字"]) == ("Test Person", "Second Test Person")
    assert sent[1]["个人事务"] == [{"名称": "补食材包", "要办成什么": "取得牛肉面 1 份", "已办到哪": "已取得 0 份",
                                "备注": "周末去超市", "提醒时刻": None}]
    assert sent[2]["个人事务"] == []


def test_wake_time_noted_at_conclusion_still_becomes_a_reminder_that_ends_the_sleep(tmp_path):
    """模板用例（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事 验收；docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-零点计划模板）：
    零条目标仍然合法；天天如此的事不写成目标，但收尾时记下的起床时刻仍按第三条生成提醒——零点计划的输入带这句收尾记录，
    照模板写出的计划只有这一条与平常不同的起床目标（带提醒与大致时刻），登记后提醒到点结束跨零点的睡眠；计划里没有就寝
    目标，也没有就寝提醒。"""
    engine = _late_engine(tmp_path, "2026-01-05T23:30:00+08:00", "wake-reminder")
    try:
        settle_day_plans(engine)
        assert engine.context.state.agents[1].day_plan.goals == ()
        waited = _start(engine, "等待", "evening-wait")
        assert advance_until_ready(engine)
        memory = {**_MEMORY, "text": "明早六点半起床，比平常早"}
        _decide(engine, "note-wake", ("conclude_activity", conclude_request(
            engine.context.state.activities[waited].name, summary="歇好了，明早六点半起床。", memories=[[memory]])))
        slept = _start(engine, "睡觉", "bedtime")
        boundary = engine.scheduling_state().next_event_at
        engine.advance(int(boundary.timestamp()) - engine.context.state.current_time, boundary)
        client = StubPlanDayClient(goals=[_goal("晨间例程", "六点半起床，比平常早", "06:30", "06:30")])
        settle_day_plans(engine, client)
        payload = json.loads(client.calls[0]["messages"][1]["content"])
        assert [row["收尾说明"] for row in payload["收尾记录"]] == ["歇好了，明早六点半起床。"]
        [goal] = engine.context.state.agents[1].day_plan.goals
        assert (goal.intent_kind, goal.remind_at, goal.around_at) == ("晨间例程", _at(6, 6, 30), _at(6, 6, 30))
        assert engine.context.state.activities[slept].planned_end_at > _at(6, 6, 30)
        assert advance_until_ready(engine) and engine.context.state.current_time == _at(6, 6, 30)
        sleep = engine.context.state.activities[slept]
        assert (sleep.status, sleep.ended_at, sleep.interruption_reason) == ("completed", _at(6, 6, 30), "plan_reminder")
    finally:
        engine.close()


def test_plan_inputs_read_world_facts_and_self_reports_but_not_the_persons_mind(engine_factory, monkeypatch):
    """输入含人物档案与背景原文、当日已核验经历句子、公开动作结果（与记忆层同一 settled 写法：发消息、结束的活动）、收尾
    的 summary 与 memories（标未经核验），不含身体数值与内部身份；达到窗口 85% 时先裁掉较早的经历句子，登记的来源随之只含
    保留的经历与其余输入的来源：被裁掉的首段等待经历，其来源事件同时是那段等待的结束事件，只作为「动作结果」里结束句的
    来源留下。
    """
    engine = engine_factory(mutate=with_colleague)
    stage_day_plan(engine)
    _decide(engine, "message", ("send_message", SendMessageActionRequest(recipient_name=COLLEAGUE_TARGET,
                                                                          text="周五一起吃饭？")))
    first = _start(engine, "等待", "first-wait")
    assert advance_until_ready(engine)
    _decide(engine, "conclude-first")
    second = _start(engine, "等待", "second-wait")
    assert advance_until_ready(engine)
    name = engine.context.state.activities[second].name
    _decide(engine, "conclude-second", ("conclude_activity", conclude_request(
        name, summary="歇好了，明早七点半起床。", memories=[[_MEMORY]])))
    config = next(item for item in engine.context.scenario.agents if item.agent_id == 1)

    inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)

    payload = inputs.payload
    assert payload["人物"] == {"名字": config.name}
    assert [sentence for sentence, _ in inputs.experiences] == ["00:00–00:15 等待了约 15 分钟",
                                                               "00:15–00:16 等待了约 2 分钟"]
    assert payload["动作结果"][0] == "00:00 给王磊发消息：周五一起吃饭？"
    assert payload["动作结果"][1:] == [line for line in payload["动作结果"][1:] if "等待（" in line]
    assert payload["收尾记录"][-1] == {"时间": "00:16", "活动": name, "收尾说明": "歇好了，明早七点半起床。",
                                    "想记住的": ["明早七点半起床"], "说明": "你自己记下的，未经核验"}
    assert payload["联系人"] == [{"称呼": item.salutation, "关系": item.relation} for item in config.contacts]
    text = json.dumps(payload, ensure_ascii=False)
    for leaked in ("energy_kcal", "sleep_pressure", "agent:1", "contact-", first, second):
        assert leaked not in text
    monkeypatch.setattr(day_planning, "model_context_length", lambda model: 980)
    monkeypatch.setattr(day_planning, "request_token_count", lambda request: 800 + 20 * len(
        json.loads(request["messages"][1]["content"])["已核验经历"]))
    settle_day_plans(engine, StubPlanDayClient())
    registered = _plan_events(engine)[-1]
    kept = [source for _, source in inputs.experiences[1:]]
    dropped = inputs.experiences[0][1]
    assert registered.data["source_event_ids"] == list(dict.fromkeys([*kept, *inputs.sources]))
    assert dropped not in kept and dropped == activity_endings(engine.context.store.read_agent_events(
        "activity_progress", 1))[first].event_id and dropped in inputs.sources
    monkeypatch.setattr(day_planning, "request_token_count", lambda request: 10_000)
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        day_planning.fit_plan_day({"x": "y"}, ["a"], "deepseek-flash")


def test_plan_inputs_list_only_accepted_conclusions_once(engine_factory):
    """收尾记录只取已受理的 conclude_activity 请求：被拒请求里写下的话不进输入，重放的同一请求不重复列出。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _start(engine, "等待", "first-wait")
    assert advance_until_ready(engine)
    name = engine.context.state.activities[waited].name
    rejected = ("conclude_activity", conclude_request("根本没有这件事", summary="明早七点起。",
                                                      memories=[[{**_MEMORY, "text": "明早七点起床"}]]))
    accepted = ("conclude_activity", conclude_request(name, summary="歇好了，明早七点半起床。", memories=[[_MEMORY]]))
    assert _decide(engine, "bad-conclude", rejected)[1].status == "rejected"
    assert _decide(engine, "good-conclude", accepted)[1].status == "completed"
    replay = context(engine, "replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(replay, "action-good-conclude", *accepted))
    engine.commit_decision_round("replay")

    inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)

    assert [(row["活动"], row["收尾说明"], row["想记住的"]) for row in inputs.payload["收尾记录"]] == [
        (name, "歇好了，明早七点半起床。", ["明早七点半起床"])]
    requests = [event for event in _events(engine)
                if event.event_type == "action_requested" and event.data["tool_name"] == "conclude_activity"]
    assert [event.data["reused"] for event in requests] == [False, False, True]
    assert [event.event_id in inputs.sources for event in requests] == [False, True, False]


def test_plan_inputs_refuse_an_accepted_conclusion_recorded_without_errands(engine_factory):
    """零点计划输入经 control_items 读已受理的收尾请求（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-控制请求记录、2.3）：一次真实受理的收尾
    照常列为一条收尾记录；经 FootprintStore 在回顾时段里补登一条带 items 而缺请求级 errands 的旧形状收尾及其已完成的结果
    后，装配输入以完整性故障拒绝，不把它当作没有事务变更的收尾列出来。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _start(engine, "等待", "first-wait")
    assert advance_until_ready(engine)
    name = engine.context.state.activities[waited].name
    accepted = ("conclude_activity", conclude_request(name, summary="歇好了。", memories=[[_MEMORY]]))
    assert _decide(engine, "conclude", accepted)[1].status == "completed"
    assert [(row["活动"], row["收尾说明"]) for row in plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["收尾记录"]] == [(name, "歇好了。")]

    stage_legacy_control_request(engine, "conclude_activity")

    with pytest.raises(IntegrityError, match="conclude_activity request lacks errands"):
        plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)


def test_review_window_is_empty_at_a_midnight_start_and_the_old_day_at_a_midnight_boundary(engine_factory, tmp_path):
    """回顾时段：起点恰为零点的启动路径只回顾生成时刻之前的当日部分，即空时段，不回顾运行没有经历的前一日；
    零点边界上才回顾刚结束的前一日（前一日零点至本次零点）。
    """
    engine = engine_factory()
    client = StubPlanDayClient()
    settle_day_plans(engine, client)
    assert json.loads(client.calls[0]["messages"][1]["content"])["回顾时段"] == "1月5日 00:00 至 1月5日 00:00"

    late = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "window")
    try:
        settle_day_plans(late)
        _start(late, "睡觉", "bedtime")
        boundary = late.scheduling_state().next_event_at
        assert int(boundary.timestamp()) == _at(6, 0)
        late.advance(int(boundary.timestamp()) - late.context.state.current_time, boundary)

        assert late.midnight_rhythm_pending
        assert plan_inputs(late.context, late.observations, late.midnight_rhythm_pending, 1).payload["回顾时段"] == "1月5日 00:00 至 1月6日 00:00"
    finally:
        late.close()


def test_run_ending_at_midnight_runs_no_plan_day_and_nothing_follows_run_ended(tmp_path, monkeypatch):
    """终点恰为零点：该边界只结算旧日并结束运行，不执行零点计划，run_ended 之后没有事件。"""
    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "ending", base="multi-person.json")
    start = engine.context.state.current_time
    settle_day_plans(engine)
    submit_round(engine, {agent_id: ("start_activity", start_activity("等待", "自己"))
                          for agent_id in engine.context.state.agents}, "last-waits")
    _forbid_client(monkeypatch)

    advance_next(engine)

    assert engine.context.state.current_time == engine.context.state.end_time == _at(6, 0)
    assert not engine.midnight_rhythm_pending
    assert drive(settle_pending_day_plans(engine)) == []
    assert _events(engine)[-1].event_type == "run_ended"
    assert {event.occurred_at for event in _plan_events(engine)} == {start}


def test_resume_and_counterfactual_branches_follow_plan_submitted_dates(engine_factory, tmp_path, monkeypatch):
    """恢复与反事实分支按既有 plan_submitted_dates 判定：已生成的不重复；从尚未生成的检查点恢复时生成一次。"""
    engine = engine_factory()
    workspace = tmp_path / "agent"
    workspace.mkdir()
    (workspace / "AGENT.json").write_text("{}", encoding="utf-8")
    snapshots = {"agent:1": snapshot_directory(workspace, engine.context.store.assets)}
    initial = engine.checkpoint(snapshots)
    settle_day_plans(engine, StubPlanDayClient(goals=[_goal("休息", "歇一会儿")]))
    planned = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    scenario_path = (ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios") / SCENARIO
    restore_branch(engine.context.store, initial.checkpoint_id, tmp_path / 'resume', None).close()
    restore_branch(engine.context.store, planned.checkpoint_id, tmp_path / 'branch', {'branch_kind': 'counterfactual', 'intervention_sha256': 'a' * 64}).close()
    resumed = LifeEngine.open(scenario_path, tmp_path / "resume")
    branch = LifeEngine.open(scenario_path, tmp_path / "branch")
    try:
        assert day_planning.agents_missing_plan(resumed.context.state, resumed.context.scenario.timezone) == [1]
        assert settle_day_plans(resumed)[0]["agent_id"] == 1
        _forbid_client(monkeypatch)
        assert drive(settle_pending_day_plans(resumed)) == []
        assert drive(settle_pending_day_plans(branch)) == []
        assert branch.context.state.agents[1].day_plan.goals[0].goal == "歇一会儿"
    finally:
        resumed.close()
        branch.close()
