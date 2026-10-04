"""场景声明的工作安排按日期与世界时钟求值（docs/dev/SPEC-errands-workday.md 4.3、4.4、4.5、4.7）。

纯函数用例：班次按本地日期取（日期例外优先于每周声明，不从星期推断），起止与休息时段按当地日历时间构造的半开区间，
上班安排与此刻状态只由声明与时钟决定。声明经真实的场景模型 WorkSchedule 校验后求值。

引擎用例（engine_factory 加 declare 改写场景副本，经真实观察、请求、结算与 SQLite）：环境层 duty 随时钟经过班前、
当班、休息时段与班后，休息日来自每周 null 与日期例外，没有工作安排的人物为 null；计划修订与工作提交都不改变它，检查点
恢复后环境层逐字节相同；班内离岗照常列出、照常受理且不多写事件；班次不进入任务的开放、开工、恢复与截止。时刻与地点由
测试套件既有的预置做法置好（_stage_office），断言都经真实入口。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (StubPlanDayClient, advance_next, conclude_pending, context, layer_start,
                                 settle_day_plans, stage_day_plan, target_actions, tool_targets, wake_by_mail)
from tests.life.test_work_contracts import (AcceptingReviewer, _clock, _completed_work_state, _decide, _decide_at,
                                            _drive, _pause, _resume, _stage_office, _start_work, _submission)
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, declare
from worldmock.catalog.affordances import default_affordances
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ReviseDayPlanRequest, SubmitWorkActionRequest
from worldmock.life.narration import enforce_field_blacklist
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.scenario import WorkSchedule, WorkShift
from worldmock.life.work_schedule import (DAY_REST, DAY_WORK, DUTY_DAYS, DUTY_STATUSES, STATUS_AFTER, STATUS_BEFORE,
                                          STATUS_BREAK, STATUS_ON_DUTY, break_spans, day_arrangement, duty,
                                          duty_status, shift_on, shift_span)

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_ZONE = "Asia/Shanghai"
_PLACES = {"office": "张江高科技园区办公室（冻结场景锚点）", "home": "张江居所（冻结场景锚点）"}


def _schedule(**changes) -> WorkSchedule:
    """十二周场景人物 1 的工作安排（与场景日历一致，可按 declare 的关键字覆盖），经场景模型校验。"""
    document = json.loads(_FORMAL.read_text(encoding="utf-8"))
    declare(**changes)(document)
    return WorkSchedule.model_validate(document["agents"][0]["work_schedule"])


def _at(text: str) -> int:
    """上海当地时刻（ISO 写法，不带偏移）的时间戳。"""
    return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(_ZONE)).timestamp())


def test_shift_on_prefers_exceptions_over_the_weekly_table() -> None:
    """某天的班次先看日期例外、再看每周声明（4.3）：例外为 null 的周一不当班，例外写了班次的周六当班；其余日子照每周
    声明，周末不当班；不从星期推断——把班次写在周六、周一写 null 时，周六当班而周一不当班。日历特殊日由例外给出。"""
    schedule = _schedule(exceptions={"2026-01-05": None, "2026-01-10": {**STANDARD_SHIFT, "breaks": []}})
    monday = schedule.weekly["0"]
    assert monday == WorkShift(starts=540, ends=1080, breaks=((720, 810),))
    assert shift_on(schedule, date(2026, 1, 12)) == monday
    assert shift_on(schedule, date(2026, 1, 5)) is None
    assert shift_on(schedule, date(2026, 1, 10)) == WorkShift(starts=540, ends=1080, breaks=())
    assert [shift_on(schedule, date(2026, 1, day)) for day in (17, 18)] == [None, None]
    assert shift_on(schedule, date(2026, 2, 14)) == monday
    assert shift_on(schedule, date(2026, 2, 16)) is None

    flipped = _schedule(weekly={"0": None, "5": STANDARD_SHIFT})
    assert shift_on(flipped, date(2026, 1, 12)) is None
    assert shift_on(flipped, date(2026, 1, 17)) == monday
    assert shift_on(flipped, date(2026, 1, 13)) == monday


def test_shift_and_break_spans_are_local_calendar_times() -> None:
    """班次与休息时段的起止是当地零点加分钟数的日历时间（4.3，与营业日历同一构造）：上海 09:00–18:00 与午休 12:00–13:30；
    1440 分钟落在次日零点；在夏令时切换日，零点到 24:00 的班次只有 23 个小时，不按固定秒数推算。"""
    shift = _schedule().weekly["0"]
    zone = ZoneInfo(_ZONE)
    day = date(2026, 1, 5)
    assert shift_span(shift, day, zone) == (_at("2026-01-05T09:00:00"), _at("2026-01-05T18:00:00"))
    assert break_spans(shift, day, zone) == ((_at("2026-01-05T12:00:00"), _at("2026-01-05T13:30:00")),)
    whole = WorkShift(starts=0, ends=1440, breaks=((0, 30), (1410, 1440)))
    assert shift_span(whole, day, zone) == (_at("2026-01-05T00:00:00"), _at("2026-01-06T00:00:00"))
    berlin = ZoneInfo("Europe/Berlin")
    start, end = shift_span(whole, date(2026, 3, 29), berlin)
    assert end - start == 23 * 3600
    assert break_spans(whole, date(2026, 3, 29), berlin)[1] == (end - 1800, end)


def test_duty_values_come_from_the_declaration_and_the_clock_alone() -> None:
    """上班安排与此刻状态只由声明与时钟决定（4.4）：没有工作安排为 None；休息日是固定的休息日对象、状态为 None；当班日写
    班次与休息时段（EN DASH，24:00 写法）和工作地点的显示名，不含内部身份；状态在班前、班内、休息时段、班后之间按半开
    区间切换。两组枚举按规格的顺序给出。"""
    schedule = _schedule()
    assert DUTY_DAYS == (DAY_WORK, DAY_REST) == ("工作日", "休息日")
    assert DUTY_STATUSES == (STATUS_BEFORE, STATUS_ON_DUTY, STATUS_BREAK, STATUS_AFTER) == (
        "未到上班时间", "当班中", "休息时段", "已下班")
    assert day_arrangement(None, date(2026, 1, 5), _PLACES) is None
    assert duty(None, _at("2026-01-05T10:00:00"), _ZONE, _PLACES) is None
    rest = {"day": "休息日", "hours": None, "place": None, "breaks": []}
    assert day_arrangement(schedule, date(2026, 1, 10), _PLACES) == rest
    assert duty(schedule, _at("2026-01-10T10:00:00"), _ZONE, _PLACES) == {**rest, "status": None}
    work = {"day": "工作日", "hours": "09:00–18:00", "place": _PLACES["office"], "breaks": ["12:00–13:30"]}
    assert day_arrangement(schedule, date(2026, 1, 5), _PLACES) == work
    statuses = {moment: duty(schedule, _at(f"2026-01-05T{moment}"), _ZONE, _PLACES)["status"]
                for moment in ("08:59:59", "09:00:00", "11:59:59", "12:00:00", "13:29:59", "13:30:00", "17:59:59",
                               "18:00:00")}
    assert statuses == {"08:59:59": "未到上班时间", "09:00:00": "当班中", "11:59:59": "当班中", "12:00:00": "休息时段",
                        "13:29:59": "休息时段", "13:30:00": "当班中", "17:59:59": "当班中", "18:00:00": "已下班"}
    assert duty_status(schedule.weekly["0"], date(2026, 1, 5), _at("2026-01-05T12:30:00"), ZoneInfo(_ZONE)) == "休息时段"
    late = _schedule(shift={"starts": 1200, "ends": 1440, "breaks": []})
    assert day_arrangement(late, date(2026, 1, 5), _PLACES)["hours"] == "20:00–24:00"
    assert "office" not in json.dumps(duty(schedule, _at("2026-01-05T10:00:00"), _ZONE, _PLACES), ensure_ascii=False)


# --- 环境层 duty、效力与工作合同边界：真实引擎（docs/dev/SPEC-errands-workday.md 4.4、4.5、4.7） ---

#: 工作地点 office 在三份场景里的显示名；工具层回家目标的名称；当班日的上班安排（不含 status）与休息日对象（含 status）。
_OFFICE_NAME = _PLACES["office"]
_HOME_TARGET = f"地点：{_PLACES['home']}"
_WORKDAY = {"day": "工作日", "hours": "09:00–18:00", "place": _OFFICE_NAME, "breaks": ["12:00–13:30"]}
_REST = {"day": "休息日", "hours": None, "place": None, "breaks": [], "status": None}
#: 环境构建的另外四层：班次、休息时段与「上班安排」一词都不在这里重述。
_OTHER_LAYERS = ("tool_layer", "todo_layer", "memory_layer", "progress_layer")


def _checked(engine, observed: dict, agent_id: int = 1) -> dict:
    """核对一次观察的环境层：通过 Schema 与字段黑名单、键序固定，与落库的观察事件逐值相同；返回观察。"""
    layer = observed["environment_layer"]
    validate_layer(layer)
    enforce_field_blacklist(layer)
    assert list(layer) == ["layer", "observed_at", "body", "location", "duty"]
    stored = engine.store.read_agent_events("observation", agent_id)[-1]
    assert stored.event_id == observed["observation_event_id"]
    assert stored.data["environment_layer"] == layer
    return observed


def _observe(engine, name: str, agent_id: int = 1) -> dict:
    """不开决策轮的一次真实观察，经 _checked 核对后返回。"""
    return _checked(engine, engine.observe(context(engine, name, agent_id)), agent_id)


def _duty_of(engine, name: str, agent_id: int = 1) -> dict | None:
    """一次真实观察的环境层 duty。"""
    return _observe(engine, name, agent_id)["environment_layer"]["duty"]


def _only_in_the_environment_layer(observed: dict) -> None:
    """duty 不含工作地点的内部身份；班次、休息时段与「上班安排」一词不出现在工具层、待办层、记忆层与进度层。"""
    assert "office" not in json.dumps(observed["environment_layer"]["duty"], ensure_ascii=False)
    others = json.dumps([observed[key] for key in _OTHER_LAYERS], ensure_ascii=False)
    assert all(text not in others for text in ("09:00–18:00", "12:00–13:30", "上班安排"))


def test_duty_follows_the_clock_through_a_working_day(engine_factory) -> None:
    """周一 09:00–18:00、午休 12:00–13:30 的人物（4.4）：运行开始的零点在家是「未到上班时间」；在办公室的 09:00:00 与
    09:30 是「当班中」，12:00:00 与 12:30 是「休息时段」，13:30:00 与 17:59:59 回到「当班中」，18:00:00 是「已下班」。
    duty 在 location 之后，五键依次为 day、hours、place、breaks、status，地点写显示名、不含内部身份，经 Schema 与字段
    黑名单；班次与「上班安排」只在环境层出现。改成休息时段显示为当班中时 12:00 与 12:30 不再是「休息时段」。"""
    engine = engine_factory(mutate=declare())
    start = _observe(engine, "midnight")
    assert start["environment_layer"]["duty"] == {**_WORKDAY, "status": "未到上班时间"}
    assert list(start["environment_layer"]["duty"]) == ["day", "hours", "place", "breaks", "status"]
    _only_in_the_environment_layer(start)
    statuses = {}
    for moment in ("09:00:00", "09:30:00", "12:00:00", "12:30:00", "13:30:00", "17:59:59", "18:00:00"):
        _stage_office(engine, _clock(moment))
        observed = _observe(engine, f"at-{moment}")
        duty = observed["environment_layer"]["duty"]
        assert {key: duty[key] for key in _WORKDAY} == _WORKDAY
        _only_in_the_environment_layer(observed)
        statuses[moment] = duty["status"]
    assert statuses == {"09:00:00": "当班中", "09:30:00": "当班中", "12:00:00": "休息时段", "12:30:00": "休息时段",
                        "13:30:00": "当班中", "17:59:59": "当班中", "18:00:00": "已下班"}


def test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly(
        engine_factory) -> None:
    """休息日只来自声明（4.3、4.4）：双人场景（时间窗内没有日历特殊日，加载检查不牵涉例外）的人物 1 在每周声明里把周一
    写 null 时，2026-01-05（周一）零点与 10:00 人在办公室都是固定的休息日对象（状态为 null，不看人在不在场）；每周照常
    当班而日期例外把这一天写成 null 时同样是休息日；每周把周一写 null、日期例外给这一天写上班次时是工作日。按星期推断
    休息日时第一种仍是工作日，忽略例外日期时后两种颠倒。"""
    def monday(**declared):
        """按 declare 的关键字给人物 1 声明工作安排的双人场景运行（停在运行开始的周一零点）。"""
        return engine_factory("multi-person.json", mutate=declare(**declared))

    weekly_off = monday(weekly={"0": None})
    assert _duty_of(weekly_off, "weekly-null") == _REST
    _stage_office(weekly_off, _clock("10:00:00"))
    assert _duty_of(weekly_off, "weekly-null-at-office") == _REST
    assert _duty_of(monday(exceptions={"2026-01-05": None}), "exception-null") == _REST
    excepted_on = monday(weekly={"0": None}, exceptions={"2026-01-05": STANDARD_SHIFT})
    assert _duty_of(excepted_on, "exception-shift") == {**_WORKDAY, "status": "未到上班时间"}


def test_people_without_a_schedule_have_no_duty_while_others_keep_theirs(engine_factory) -> None:
    """双人场景只给人物 1 声明工作安排（4.4）：人物 1 的 duty 是自己的工作日安排，人物 2 的 duty 为 null；仓库场景的
    人物都写 null，duty 为 null。给所有人物套用某人的班次时人物 2 也有班次。"""
    engine = engine_factory("multi-person.json", mutate=declare(0))
    assert [item.agent_id for item in engine.scenario.agents] == [1, 2]
    assert _duty_of(engine, "scheduled", 1) == {**_WORKDAY, "status": "未到上班时间"}
    assert _duty_of(engine, "unscheduled", 2) is None
    assert _duty_of(engine_factory(), "repository") is None


def _stage_finished_work(engine, worked: int) -> None:
    """把世界置于首个任务开放后 worked 秒：人在办公室停留，这项任务的工作已做够合同有效工时并已收尾（只置状态，同
    test_work_review 的预置做法），当秒可以决策。"""
    task = engine.scenario.tasks[0]
    staged = _completed_work_state(engine, task, worked, float(task.required_effective_seconds))
    done = next(item for item in staged.activities.values() if item.target_id == task.task_id)
    staged.activities[done.activity_id] = done.model_copy(
        update={"resource_claims": set(default_affordances().resource_claims("work"))})
    now = staged.current_time
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    staged = sessions.open_stay(sessions.end_all(staged), 1, engine._place_names)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", source_kind="scenario",
        data={"kind": "test_finished_work_staged"})])
    engine.state = staged


@pytest.mark.asyncio
async def test_duty_stays_on_duty_after_the_work_is_submitted(engine_factory) -> None:
    """手上的工作交了也还在班上（4.5）：14:00 在办公室、首个任务已做够有效工时，经真实 submit_work、独立验收准备与
    结算提交并付款；提交前后的 duty 都是「当班中」，逐值相同。"""
    engine = engine_factory(mutate=declare())
    task = engine.scenario.tasks[0]
    _stage_finished_work(engine, 6 * 3600)
    assert engine.state.current_time == _clock("14:00:00")
    engine._work_reviewer = AcceptingReviewer()
    decision = context(engine, "submit")
    engine.begin_decision_round([decision])
    observed = _checked(engine, engine.observe(decision))
    before = observed["environment_layer"]["duty"]
    [name] = [row["task_name"] for row in observed["task_bindings"] if row["task_id"] == task.task_id]
    engine.submit_action(DirectToolCall(decision, "action-submit", "submit_work", SubmitWorkActionRequest(
        task_name=name, deliverable=_submission(task).deliverable)))
    await engine.prepare_pending_work_review("action-submit")
    submitted = engine.commit_decision_round("submit")[0]
    assert submitted.status == "completed" and submitted.data["payment_fen"] == task.pay_fen
    assert engine.state.paid_task_ids == {task.task_id}
    assert before == _duty_of(engine, "after-submit") == {**_WORKDAY, "status": "当班中"}


def test_revising_the_day_plan_does_not_change_duty(engine_factory) -> None:
    """今日计划改不了上班安排（4.5）：10:00 在办公室、今日计划为空时 duty 是「当班中」；真实 revise_day_plan 写下
    「下午三点回家」被受理，同一秒的下一次观察里目标在待办层，duty 逐值不变且非空。计划有目标后 duty 消失时失败。"""
    engine = engine_factory(mutate=declare())
    _stage_office(engine, _clock("10:00:00"))
    stage_day_plan(engine)
    decision = context(engine, "revise")
    engine.begin_decision_round([decision])
    before = _checked(engine, engine.observe(decision))["environment_layer"]["duty"]
    request = ReviseDayPlanRequest.model_validate({"goals": [
        {"intent_kind": "通勤", "goal": "下午三点回家", "remind_time": None, "around_time": "15:00"}]})
    engine.submit_action(DirectToolCall(decision, "action-revise", "revise_day_plan", request))
    assert engine.commit_decision_round("revise")[0].status == "completed"
    after = _observe(engine, "revised")
    assert engine.state.current_time == _clock("10:00:00")
    assert [goal["goal"] for goal in after["todo_layer"]["day_plan"]["goals"]] == ["下午三点回家"]
    assert before is not None and after["environment_layer"]["duty"] == before
    assert after["environment_layer"]["duty"]["status"] == "当班中"


def test_environment_layer_is_byte_identical_after_checkpoint_restore(engine_factory, tmp_path) -> None:
    """检查点恢复后环境层逐字节相同（4.4）：12:30 在办公室（休息时段）提交共同检查点，原运行与从检查点恢复的分支对同一
    时刻各观察一次，两份环境层序列化后逐字节相同、duty 非空。分支用引擎创建时那份改写后的场景文件打开。"""
    engine = engine_factory(mutate=declare())
    _stage_office(engine, _clock("12:30:00"))
    snapshots = {f"agent:{agent_id}": engine.store.assets.put_json({"agent_id": agent_id}, {"kind": "continuation"})
                 for agent_id in engine.store.manifest["agent_ids"]}
    checkpoint = engine.checkpoint(snapshots)
    original = _observe(engine, "restored")["environment_layer"]
    engine.store.restore(checkpoint.checkpoint_id, tmp_path / "branch").close()
    branch = LifeEngine.open(tmp_path / "scenario-0" / "scenarios" / "white-collar-12-weeks.json", tmp_path / "branch")
    try:
        restored = _observe(branch, "restored")["environment_layer"]
        assert json.dumps(restored, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)
        assert restored["duty"] == {**_WORKDAY, "status": "休息时段"}
    finally:
        branch.close()


def _leave_for_home(engine) -> tuple[dict, dict, object, Counter]:
    """10:00 在办公室，经真实观察看到回家的步行并请求它、结算后让一封邮件在一分钟后到达并推进到那时，在路上再观察；
    返回（办公室的观察，路上的观察，动作结果，这一段产生的事件按 (event_type, data.kind) 计数）。"""
    _stage_office(engine, _clock("10:00:00"))
    since = engine.store.last_event_seq
    decision = context(engine, "leave")
    engine.begin_decision_round([decision])
    observed = _checked(engine, engine.observe(decision))
    engine.submit_action(DirectToolCall(decision, "action-leave", "start_activity",
                                        layer_start(observed, _HOME_TARGET, "步行")))
    result = engine.commit_decision_round("leave")[0]
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    road = _observe(engine, "on-the-road")
    events = engine.store.read_events(engine.store.last_event_seq)
    return observed, road, result, Counter((event.event_type, event.data.get("kind")) for event in events
                                           if event.event_seq > since)


def test_leaving_the_workplace_on_duty_is_listed_and_accepted_without_new_event_kinds(engine_factory) -> None:
    """环境不新增拒绝（4.5）：当班中在办公室，回家的步行照常列为可用（原因为 null），真实 start_activity 照常受理为
    进行中；一分钟后在路上，duty 照样按时钟求值为「当班中」；同样的动作在没有工作安排的同一场景里产生的事件，按
    (event_type, data.kind) 计数完全相同，不另写事件。"""
    scheduled, road, result, kinds = _leave_for_home(engine_factory(mutate=declare()))
    plain, plain_road, plain_result, plain_kinds = _leave_for_home(engine_factory())
    assert scheduled["environment_layer"]["duty"]["status"] == "当班中"
    assert plain["environment_layer"]["duty"] is None and plain_road["environment_layer"]["duty"] is None
    assert road["environment_layer"]["location"]["session_kind"] == "transit"
    assert road["environment_layer"]["duty"] == {**_WORKDAY, "status": "当班中"}
    walk = target_actions(scheduled, _HOME_TARGET)["步行"]
    assert walk == target_actions(plain, _HOME_TARGET)["步行"]
    assert (walk["available"], walk["reason"]) == (True, None)
    assert (result.status, result.error_code) == (plain_result.status, plain_result.error_code) == ("in_progress", None)
    assert kinds == plain_kinds and sum(kinds.values()) > 0


def test_work_can_start_before_the_shift_and_continue_after_it(engine_factory) -> None:
    """班次不进入开工与恢复的判定（4.7）：班次 09:00–18:00，任务由 08:00 的通知开放、20:00 截止。08:30 在办公室
    （未到上班时间）真实 start_activity 工作被受理为进行中；另一次运行 17:30 开工后照常播报做过 18:00，18:30（已下班）
    工作仍在进行，暂停后同一秒恢复被受理、工作回到进行中。班次并入任务开放判定时 08:30 的开工被拒绝。"""
    early = engine_factory(mutate=declare())
    task = early.scenario.tasks[0]
    assert (task.available_at.isoformat(), task.due_at.isoformat()) == ("2026-01-05T08:00:00+08:00",
                                                                        "2026-01-05T20:00:00+08:00")
    _stage_office(early, _clock("08:30:00"))
    assert _duty_of(early, "before-shift")["status"] == "未到上班时间"
    early_work = _start_work(early, "early-start")
    assert early.state.activities[early_work].status == "active"
    late = engine_factory(mutate=declare())
    _stage_office(late, _clock("17:30:00"))
    work_id = _start_work(late, "late-start")
    _decide_at(late, _clock("18:30:00"))
    assert late.state.activities[work_id].status == "active"
    assert _duty_of(late, "after-shift")["status"] == "已下班"
    assert _pause(late, "after-shift-pause", work_id).status == "completed"
    resumed = _resume(late, "after-shift-resume", work_id)
    assert (resumed.status, resumed.error_code) == ("completed", None)
    assert late.state.activities[work_id].status == "active" and late.state.current_time == _clock("18:30:00")


def _task_targets(engine, moments: tuple[str, ...]) -> list[dict]:
    """在给定钟点把人置于办公室并观察，返回每次观察里首个任务的工具层目标（合同、开工动作与提交工具）。"""
    name = f"工作：{engine.scenario.tasks[0].title}"
    rows = []
    for moment in moments:
        _stage_office(engine, _clock(moment))
        rows.append(tool_targets(_observe(engine, f"task-{moment}"))[name])
    return rows


def test_shifts_do_not_change_task_opening_or_deadline(engine_factory) -> None:
    """班次不参与任务开放时刻的编译、不延长或缩短截止（4.7）：有工作安排与没有工作安排的同一场景，全部任务的开放与
    截止时刻逐一相同；08:30（班前）与 18:30（班后）在办公室看到的首个任务目标（合同的开放、截止与剩余，开工动作）
    逐值相同，开放写 08:00、截止写 20:00。"""
    scheduled = engine_factory(mutate=declare())
    plain = engine_factory()
    assert [(item.task_id, item.available_at, item.due_at) for item in scheduled.scenario.tasks] == [
        (item.task_id, item.available_at, item.due_at) for item in plain.scenario.tasks]
    moments = ("08:30:00", "18:30:00")
    targets = _task_targets(scheduled, moments)
    assert targets == _task_targets(plain, moments)
    assert [(row["contract"]["opens"], row["contract"]["deadline"]) for row in targets] == [
        ("2026-01-05T08:00:00+08:00", "2026-01-05T20:00:00+08:00")] * 2
    assert all(row["actions"][0]["available"] for row in targets)


#: 08:30–18:00、两段休息（12:30–13:00 与 15:00–15:15）的班次：班次与第一段休息的起点都不在整点上。
_TWO_BREAKS = {"starts": 510, "ends": 1080, "breaks": [[750, 780], [900, 915]]}


def test_status_is_the_break_inside_every_declared_break(engine_factory) -> None:
    """休息时段是一个列表，状态在声明的每一段里都是「休息时段」（docs/dev/SPEC-errands-workday.md 4.3、4.4）：班次
    08:30–18:00、休息 12:30–13:00 与 15:00–15:15 的人物在办公室，经真实观察：12:45 在第一段里，13:00:00 回到
    「当班中」；14:59:59 仍是「当班中」，15:00:00 与 15:14:59 在第二段里，15:15:00 又是「当班中」（区间半开）。只认
    第一段休息时，第二段里显示为当班中。"""
    engine = engine_factory(mutate=declare(shift=_TWO_BREAKS))
    statuses = {}
    for moment in ("12:45:00", "13:00:00", "14:59:59", "15:00:00", "15:14:59", "15:15:00"):
        _stage_office(engine, _clock(moment))
        statuses[moment] = _duty_of(engine, f"at-{moment}")["status"]
    assert statuses == {"12:45:00": "休息时段", "13:00:00": "当班中", "14:59:59": "当班中", "15:00:00": "休息时段",
                        "15:14:59": "休息时段", "15:15:00": "当班中"}


def test_duty_and_the_plan_input_write_every_break_and_the_declared_minutes(engine_factory) -> None:
    """班次与休息时段照声明写出（docs/dev/SPEC-errands-workday.md 4.4、4.6；接口合同「上班安排」：HH:MM–HH:MM）：班次
    08:30 开始、休息 12:30–13:00 与 15:00–15:15 时，零点计划输入、客户端实际收到的零点计划请求与 10:00 在办公室真实
    观察到的环境层 duty 都写 hours「08:30–18:00」，breaks 按声明顺序列出两段，起点的分钟不被抹成整点；三处同源，除
    status 外逐值相同。"""
    engine = engine_factory(mutate=declare(shift=_TWO_BREAKS))
    arrangement = {"day": "工作日", "hours": "08:30–18:00", "place": _OFFICE_NAME,
                   "breaks": ["12:30–13:00", "15:00–15:15"]}
    assert plan_inputs(engine, 1).payload["上班安排"] == arrangement
    client = StubPlanDayClient()
    settle_day_plans(engine, client)
    [request] = client.calls
    assert json.loads(request["messages"][1]["content"])["上班安排"] == arrangement
    _stage_office(engine, _clock("10:00:00"))
    assert _duty_of(engine, "two-breaks") == {**arrangement, "status": "当班中"}


def test_status_is_on_duty_at_home_inside_the_shift(engine_factory) -> None:
    """状态只由时钟与班次决定，不含人物在不在场（docs/dev/SPEC-errands-workday.md 4.4、4.5）：周一 09:00–18:00 当班的
    人物 10:00 在办公室，经真实 start_activity 步行回家、到家并收尾那一程，这时还不到午休。下一次真实观察里人停留在
    家，duty 仍是工作日安排、状态「当班中」，工作地点仍写办公室；人不在工作地点不让状态变成「已下班」。"""
    engine = engine_factory(mutate=declare())
    _stage_office(engine, _clock("10:00:00"))
    left = _decide(engine, "walk-home", "start_activity", lambda observed: layer_start(observed, _HOME_TARGET, "步行"))
    assert left.status == "in_progress"
    _drive(engine, "to-home", lambda: engine.state.agents[1].location_id == "home")
    assert conclude_pending(engine, "arrived-home").status == "completed"
    assert engine.scenario.agents[0].home_location_id == "home" == engine.state.agents[1].location_id
    assert _clock("10:00:00") < engine.state.current_time < _clock("12:00:00")
    layer = _observe(engine, "at-home-on-duty")["environment_layer"]
    assert (layer["location"]["session_kind"], layer["location"]["name"]) == ("stay", _PLACES["home"])
    assert layer["duty"] == {**_WORKDAY, "status": "当班中"}
