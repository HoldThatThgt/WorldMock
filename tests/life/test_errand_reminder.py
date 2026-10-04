"""个人事务的提醒时刻经真实请求、引擎与 SQLite（docs/dev/SPEC-errands-workday.md 2.5、2.6、2.7）。

正式场景建立的 LifeEngine 上，人物以收尾请求的 errands 记下带 remind_time 的事务，随后经真实的时钟推进与决策轮。提醒只
支持时刻：钟点换算为绝对时刻（今日的该钟点严格晚于受理时刻取今天，否则取下一本地日）。清醒人物的未提醒时刻同时是下一
边界与下一次决策机会的候选，到点的那个边界恰在该时刻，以唤醒原因 errand_reminder_due（追加在 plan_reminder_due 之后，
不在睡眠允许集合）提供一次决策机会，并写一条人物可见、不带决策与动作身份的 errand_reminded；事务状态除提醒时刻外不变，
只提醒一次，更新时写新钟点重新计一次，完成或取消后不再提醒，同刻到期的多条合并为一次机会、各写一条事件。睡眠中的人物
不计入候选、不形成静默边界、不交付也不消耗，错过的提醒在醒来的那个边界与 wake_from_sleep 一并交付；零点边界由零点节律
入口交付。未完成事务不是在手承诺：重估间隔与闭店预警不因它改变。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, errand_add,
                                 errand_cancel, errand_update, report_request, settle_day_plans, stage_day_plan,
                                 stage_errands, start_activity, wake_by_mail)
from tests.life.routing_fixtures import HOME, act, observe, routed, run_until, stage
from tests.life.test_errand_completion import (DRINK, KIT_NAME, MILK, MILK_ID, SETTLEMENT_KINDS, WAIT, buy,
                                               errand_events, home, note)
from tests.life.test_errand_requests import _act
from tests.life.test_errand_todo_layer import _errand
from tests.life.test_same_moment_decisions import _short_intervals
from worldmock.life import errands, reminders
from worldmock.life.models import ActivityStatus
from worldmock.life.readable import iso_time
from worldmock.life.rhythm import apply_decision_rhythm, engine_rhythm_inputs
from worldmock.life.schedule import PlanGoal, WakeReason

#: 正式目录里的第三种商品（名称照写即唯一解析）。
TEA = "康师傅 冰红茶"
#: 提醒事件的 kind。
REMINDED = ("errand_reminded",)


def moment(engine, when: str) -> int:
    """场景本地时刻「YYYY-MM-DD HH:MM」的纪元秒。"""
    return int(datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(engine.scenario.timezone)).timestamp())


def evening(engine_factory, clock: str):
    """单人白领场景：1 月 5 日 clock 人在家、当日空计划已登记、此刻可以决策（经存储提交，版本连续）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = moment(engine, f"2026-01-05 {clock}")

    def change(state):
        """推到 clock，人在家。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        person = state.agents[1]
        person.location_id, person.body_settled_at, person.next_decision_at = HOME, now, now
    stage(engine, change)
    return engine


def reminded(engine) -> list:
    """人物 1 的提醒事件（按事件顺序）。"""
    return errand_events(engine, kinds=REMINDED)


def errand(engine, name: str):
    """人物 1 名为 name 的未完成事务（没有为 None）。"""
    return next((item for item in engine.state.agents[1].errands.values() if item.name == name), None)


def settlement(engine, at: int):
    """人物 1 在 at 结束的唯一已提交共同身体结算事件。"""
    [event] = [event for event in engine.store.read_agent_events("environment_event", 1)
               if event.data.get("kind") == "body_settlement" and event.data["interval_end"] == at]
    return event


def until(engine, at: int) -> None:
    """推进到 at（就绪的人物照常过日子），并断言 at 恰是一个边界。"""
    run_until(engine, lambda current: current.state.current_time >= at)
    assert engine.state.current_time == at


def started_reasons(engine, at: int) -> list[list[str]]:
    """人物 1 在 at 的各次 decision_started 的唤醒原因。"""
    return [event.data["wake_reasons"] for event in engine.store.read_agent_events("decision_started", 1)
            if event.occurred_at == at]


def assert_reminder(event, item, at: int) -> None:
    """一条提醒事件：environment_event、来源为环境、人物可见、人物为事务所有人；决策、动作与活动身份为空，没有原因事件，
    时刻是交付的边界；载荷只有 kind 与记下提醒时刻后的事务快照。"""
    assert (event.event_type, event.source_kind, event.visibility, event.agent_id) == (
        "environment_event", "environment", "agent", 1)
    assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
    assert event.caused_by == [] and event.occurred_at == at
    assert event.data == {"kind": "errand_reminded", "errand": item.model_dump(mode="json")}


def lines(observed: dict, record: dict) -> list[str]:
    """一条进度层记录在私有 progress_meta 中的 settled 句（浓缩时折成的句子）。"""
    return observed["progress_meta"][observed["progress_layer"]["events"].index(record)]["lines"]


def test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged(engine_factory) -> None:
    """清醒的人物 12:31 记下「取得纯牛奶 1 份」、提醒 12:40：此后这一时刻是下一边界与下一次决策机会的候选，引擎恰在 12:40
    有一个边界。该边界的共同身体结算以 errand_reminder_due 提供决策（带新唤醒原因的结算与 decision_started 照常落库），
    同一提交写一条人物可见、不带决策与动作身份的 errand_reminded；事务只多了提醒时刻，名称、完成依据、累计量与备注不变，
    仍未完成。接下来那次观察的进度层有一条只写名称的 errand_reminder 记录（来源计入观察，浓缩时不留句子），待办层该行的
    提醒钟点变为 null。此后不再提醒：没有第二条提醒事件，这一时刻不再是任何候选。"""
    engine = home(engine_factory, {})
    zone = engine.scenario.timezone
    note(engine, "note", [errand_add("买牛奶", MILK, memo="下班顺路买", remind_time="12:40")])
    before, remind = errand(engine, "买牛奶"), moment(engine, "2026-01-05 12:40")
    assert before.remind_at == remind and before.reminded_at is None
    waited = act(engine, "wait", routed(WAIT))
    assert waited.status == "in_progress"
    agent = engine.state.agents[1]
    assert errands.reminder_times(agent, engine.state.current_time) == (remind,)
    assert remind in reminders.boundary_candidates(engine.state, zone) and agent.next_decision_at == remind
    until(engine, remind)
    boundary = settlement(engine, remind)
    assert boundary.data["decision_offered"] is True and "errand_reminder_due" in boundary.data["wake_reasons"]
    after = errand(engine, "买牛奶")
    assert after == before.model_copy(update={"reminded_at": remind})
    [event] = reminded(engine)
    assert_reminder(event, after, remind)
    assert event.state_version_after == boundary.state_version_after
    assert 1 in engine.scheduling_state().ready_agent_ids
    observed, result = _act(engine, "reminded", "report_status",
                            report_request(activity_name(engine, waited.activity_id), summary="想起要买牛奶。"))
    assert result.status == "completed"
    assert started_reasons(engine, remind) == [boundary.data["wake_reasons"]]
    record = {"occurred_at": iso_time(remind, zone), "type": "errand_reminder", "errand": "买牛奶"}
    assert record in observed["progress_layer"]["events"] and event.event_id in observed["progress_event_ids"]
    assert lines(observed, record) == []
    [row] = observed["todo_layer"]["errands"]
    assert (row["name"], row["memo"], row["remind_time"]) == ("买牛奶", "下班顺路买", None)
    run_until(engine, lambda current: current.state.current_time > remind + 1800)
    assert reminded(engine) == [event] and errand(engine, "买牛奶") == after
    assert errands.reminder_times(engine.state.agents[1], engine.state.current_time) == ()
    later = [event.data["wake_reasons"] for event in engine.store.read_agent_events("decision_started", 1)
             if event.occurred_at > remind]
    assert later and all("errand_reminder_due" not in reasons for reasons in later)


def test_a_clock_equal_to_the_acceptance_time_means_the_next_local_day(engine_factory) -> None:
    """12:31:00 受理的收尾里三条事务的提醒钟点：等于受理时刻的 12:31 与早于它的 12:30 都取下一本地日的同一钟点（总在二十
    四小时之内），晚一分钟的 12:32 取今天。待办层照写三个钟点；今天的 12:32 恰是一个边界，只提醒那一条，另两条留到明天。"""
    engine = home(engine_factory, {})
    zone = engine.scenario.timezone
    noted = note(engine, "note", [errand_add("买牛奶", MILK, remind_time="12:31"),
                                  errand_add("补食材包", KIT_NAME, remind_time="12:32"),
                                  errand_add("囤冰红茶", TEA, remind_time="12:30")])
    accepted = moment(engine, "2026-01-05 12:31")
    assert noted.occurred_at == accepted
    expected = {"买牛奶": moment(engine, "2026-01-06 12:31"), "补食材包": moment(engine, "2026-01-05 12:32"),
                "囤冰红茶": moment(engine, "2026-01-06 12:30")}
    assert {name: errand(engine, name).remind_at for name in expected} == expected
    assert expected["买牛奶"] == accepted + 86400
    registered = errand_events(engine, kinds=("errand_registered",))
    assert [event.data["errand"]["remind_at"] for event in registered] == list(expected.values())
    rows = observe(engine, "noted")["todo_layer"]["errands"]
    assert [(row["name"], row["remind_time"]) for row in rows] == [
        ("买牛奶", "12:31"), ("补食材包", "12:32"), ("囤冰红茶", "12:30")]
    assert errands.reminder_times(engine.state.agents[1], accepted) == tuple(sorted(expected.values()))
    act(engine, "wait", routed(WAIT))
    until(engine, expected["补食材包"])
    [event] = reminded(engine)
    assert event.data["errand"]["name"] == "补食材包"
    assert (errand(engine, "买牛奶").reminded_at, errand(engine, "囤冰红茶").reminded_at) == (None, None)
    later = sorted([expected["买牛奶"], expected["囤冰红茶"]])
    assert errands.reminder_times(engine.state.agents[1], engine.state.current_time) == tuple(later)


def test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary(
        engine_factory) -> None:
    """22:51 记下提醒 23:30 的事务（醒着时这一时刻是下一边界的候选），随即入睡：睡眠中它不再是下一边界或下一次决策机会的
    候选，引擎在 23:30 没有边界；其后仍在睡的每个边界（含本地零点与零点节律）都是静默的——不提供决策、没有观察、不写提醒
    事件、不记提醒时刻。醒来的那个边界同时以 wake_from_sleep 与 errand_reminder_due 提供决策并写提醒事件，醒后第一次观察的
    待办层该行提醒钟点为 null、进度层有提醒记录。"""
    engine = evening(engine_factory, "22:50")
    zone = engine.scenario.timezone
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="23:30")])
    remind, midnight = moment(engine, "2026-01-05 23:30"), moment(engine, "2026-01-06 00:00")
    assert errand(engine, "买牛奶").remind_at == remind
    assert remind in reminders.boundary_candidates(engine.state, zone)
    slept = act(engine, "sleep", start_activity("睡觉", "自己"))
    assert slept.status == "in_progress"
    assert errands.boundary_times(engine.state) == [] and remind not in reminders.boundary_candidates(engine.state, zone)
    assert engine.state.agents[1].next_decision_at != remind
    observations = len(engine.store.read_agent_events("observation", 1))
    asleep = []
    while True:
        advance_next(engine)
        now = engine.state.current_time
        if engine.state.activities[slept.activity_id].status != ActivityStatus.ACTIVE:
            break
        asleep.append(now)
        assert (settlement(engine, now).data["decision_offered"], settlement(engine, now).data["wake_reasons"]) == (
            False, [])
        assert engine.scheduling_state().ready_agent_ids == ()
        assert reminded(engine) == [] and errand(engine, "买牛奶").reminded_at is None
        assert engine.state.agents[1].next_decision_at != remind and errands.boundary_times(engine.state) == []
    assert remind not in asleep and midnight in asleep
    assert len(engine.store.read_agent_events("observation", 1)) == observations
    woke = engine.state.current_time
    boundary = settlement(engine, woke)
    assert boundary.data["decision_offered"] is True
    assert {"wake_from_sleep", "errand_reminder_due"} <= set(boundary.data["wake_reasons"])
    item = errand(engine, "买牛奶")
    [event] = reminded(engine)
    assert item.reminded_at == woke and item.remind_at == remind
    assert_reminder(event, item, woke)
    assert event.state_version_after == boundary.state_version_after
    observed, result = _act(engine, "woke", "conclude_activity",
                            conclude_request(activity_name(engine, slept.activity_id), summary="睡醒了。"))
    assert result.status == "completed"
    assert {"wake_from_sleep", "errand_reminder_due"} <= set(started_reasons(engine, woke)[0])
    assert [row["remind_time"] for row in observed["todo_layer"]["errands"]] == [None]
    record = {"occurred_at": iso_time(woke, zone), "type": "errand_reminder", "errand": "买牛奶"}
    assert record in observed["progress_layer"]["events"]


def test_errands_due_together_are_one_opportunity_with_one_event_each(engine_factory) -> None:
    """两条事务都提醒 12:40、第三条提醒 12:55：12:40 的边界把两条一并交付——唤醒原因 errand_reminder_due 只列一次、只提供
    一次决策机会（播报之后这一秒没有第二次），两条各写一条提醒事件（待办层顺序，与该边界的结算同一提交）；第三条不在此时
    交付，到 12:55 才单独提醒。"""
    engine = home(engine_factory, {})
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="12:40"),
                          errand_add("补食材包", KIT_NAME, remind_time="12:40"),
                          errand_add("囤冰红茶", TEA, remind_time="12:55")])
    first, third = moment(engine, "2026-01-05 12:40"), moment(engine, "2026-01-05 12:55")
    waited = act(engine, "wait", routed(WAIT))
    until(engine, first)
    boundary = settlement(engine, first)
    assert boundary.data["wake_reasons"].count("errand_reminder_due") == 1
    events = reminded(engine)
    assert [event.data["errand"]["name"] for event in events] == ["买牛奶", "补食材包"]
    for event in events:
        assert_reminder(event, errand(engine, event.data["errand"]["name"]), first)
        assert event.state_version_after == boundary.state_version_after
    assert errand(engine, "囤冰红茶").reminded_at is None
    _, result = _act(engine, "together", "report_status",
                     report_request(activity_name(engine, waited.activity_id), summary="记着要去买。"))
    assert result.status == "completed"
    assert engine.scheduling_state().ready_agent_ids == () and len(started_reasons(engine, first)) == 1
    until(engine, third)
    assert [event.data["errand"]["name"] for event in reminded(engine)] == ["买牛奶", "补食材包", "囤冰红茶"]
    assert settlement(engine, third).data["wake_reasons"].count("errand_reminder_due") == 1


def test_errand_and_goal_reminders_due_together_are_listed_in_enum_order(engine_factory) -> None:
    """当日目标与个人事务都提醒 12:40：同一边界的唤醒原因里 plan_reminder_due 在前、errand_reminder_due 在后，各计一次，与
    其余原因一起按枚举声明顺序排列；目标与事务各自记下提醒时刻，决策开始的唤醒原因相同，进度层两种提醒记录并列。"""
    engine = home(engine_factory, {})
    zone = engine.scenario.timezone
    remind = moment(engine, "2026-01-05 12:40")
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="外出", goal="去集市买牛奶", registered_at=engine.state.current_time,
                                           remind_at=remind),))
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="12:40")])
    waited = act(engine, "wait", routed(WAIT))
    until(engine, remind)
    reasons = settlement(engine, remind).data["wake_reasons"]
    order = [reason.value for reason in WakeReason]
    assert reasons == sorted(reasons, key=order.index) and len(reasons) == len(set(reasons))
    assert reasons.index("plan_reminder_due") < reasons.index("errand_reminder_due")
    assert engine.state.agents[1].day_plan.goals[0].reminded_at == remind
    assert errand(engine, "买牛奶").reminded_at == remind and len(reminded(engine)) == 1
    observed, _ = _act(engine, "both", "report_status",
                       report_request(activity_name(engine, waited.activity_id), summary="该出门了。"))
    assert started_reasons(engine, remind) == [reasons]
    types = [row["type"] for row in observed["progress_layer"]["events"] if row["occurred_at"] == iso_time(remind, zone)]
    assert {"plan_reminder", "errand_reminder"} <= set(types)


def test_completed_or_cancelled_errands_are_not_reminded(engine_factory) -> None:
    """三条事务都提醒 13:00：「吃掉纯牛奶 1 份」在 12:46 喝完时办成，「取得食材包」在 12:33 的播报里取消，二者移出状态后
    不再提醒；13:00 的边界照常为仍未完成的那一条交付提醒，只有它一条提醒事件。"""
    engine = home(engine_factory, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("喝牛奶", MILK, outcome="吃掉", remind_time="13:00"),
                          errand_add("补食材包", KIT_NAME, remind_time="13:00"),
                          errand_add("囤冰红茶", TEA, remind_time="13:00")])
    remind = moment(engine, "2026-01-05 13:00")
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress"
    wake_by_mail(engine, 1, offset=120)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
    name = activity_name(engine, drinking.activity_id)
    cancelled = act(engine, "cancel", report_request(name, summary="还在喝。", errands=[
        errand_cancel("补食材包", "家里还有一包")]), tool="report_status")
    assert cancelled.status == "completed" and errand(engine, "补食材包") is None
    run_until(engine, lambda current: current.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    assert errand(engine, "喝牛奶") is None
    assert [event.data["kind"] for event in errand_events(engine, kinds=("errand_cancelled", "errand_completed"))] == [
        "errand_cancelled", "errand_completed"]
    assert errands.reminder_times(engine.state.agents[1], engine.state.current_time) == (remind,)
    until(engine, remind)
    [event] = reminded(engine)
    assert_reminder(event, errand(engine, "囤冰红茶"), remind)
    assert set(engine.state.agents[1].errands) == {event.data["errand"]["errand_id"]}


def test_updating_the_reminder_clock_arms_it_again(engine_factory) -> None:
    """提醒 12:40 的事务到点提醒一次；同一秒的播报更新它的钟点为 12:50 并改了备注：提醒时刻按受理时刻重新换算、已提醒时刻
    清空（errand_updated 的快照如此），待办层重新显示「12:50」；12:50 再提醒一次，共两条提醒事件。"""
    engine = home(engine_factory, {})
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="12:40")])
    first, second = moment(engine, "2026-01-05 12:40"), moment(engine, "2026-01-05 12:50")
    waited = act(engine, "wait", routed(WAIT))
    until(engine, first)
    assert len(reminded(engine)) == 1 and errand(engine, "买牛奶").reminded_at == first
    _, updated = _act(engine, "update", "report_status", report_request(
        activity_name(engine, waited.activity_id), summary="晚点再去。",
        errands=[errand_update("买牛奶", memo="十二点五十再出门", remind_time="12:50")]))
    assert updated.status == "completed"
    item = errand(engine, "买牛奶")
    assert (item.remind_at, item.reminded_at, item.memo) == (second, None, "十二点五十再出门")
    [event] = errand_events(engine, kinds=("errand_updated",))
    assert event.data["errand"] == item.model_dump(mode="json")
    [row] = observe(engine, "updated")["todo_layer"]["errands"]
    assert row["remind_time"] == "12:50"
    until(engine, second)
    events = reminded(engine)
    assert [event.occurred_at for event in events] == [first, second]
    assert errand(engine, "买牛奶").reminded_at == second
    assert settlement(engine, second).data["decision_offered"] is True


def test_a_reminder_due_at_local_midnight_is_delivered_by_the_midnight_rhythm(engine_factory) -> None:
    """23:41 记下提醒「00:00」的事务（换算为次日零点），人醒着等待：零点边界的推进本身不交付（判定段留到零点计划之后，此时
    事务未记提醒、没有提醒事件）；零点计划登记之后零点节律入口判定 errand_reminder_due、交付提醒，提醒事件与该秒的共同身体
    结算同一提交、排在零点计划事件之后。"""
    engine = evening(engine_factory, "23:40")
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="00:00")])
    midnight = moment(engine, "2026-01-06 00:00")
    assert errand(engine, "买牛奶").remind_at == midnight
    act(engine, "wait", routed(WAIT))

    def before_midnight(current) -> bool:
        """下一边界是零点且此刻没人要决策。"""
        state = current.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == midnight
    run_until(engine, before_midnight)
    target = engine.scheduling_state().next_event_at
    engine.advance(midnight - engine.state.current_time, target)
    assert engine.midnight_rhythm_pending and engine.state.current_time == midnight
    assert reminded(engine) == [] and errand(engine, "买牛奶").reminded_at is None
    settle_day_plans(engine)
    boundary = settlement(engine, midnight)
    assert boundary.data["decision_offered"] is True and "errand_reminder_due" in boundary.data["wake_reasons"]
    [event] = reminded(engine)
    item = errand(engine, "买牛奶")
    assert item.reminded_at == midnight
    assert_reminder(event, item, midnight)
    assert event.state_version_after == boundary.state_version_after
    [plan] = [row for row in engine.store.read_agent_events("environment_event", 1)
              if row.data.get("kind") == "day_plan_generated" and row.occurred_at == midnight]
    assert plan.event_seq < event.event_seq
    assert 1 in engine.scheduling_state().ready_agent_ids


def test_a_reminder_delivered_with_the_arrival_stays_out_of_the_new_stay_session(engine_factory) -> None:
    """记录按所在提交归属 session（docs/dev/SPEC-errands-workday.md 2.6）：人物步行去公司，途中一条未完成事务的提醒时刻
    恰是到达那一秒（请求只写 HH:MM，步行到达的秒数不落在整分，这里以预置的事务造出）。到达的那次时钟推进提交开启办公室
    的停留 session，提醒以 errand_reminder_due 与到达同一边界交付、errand_reminded 落在同一提交；它归旧的路上 session：
    到达后第一次观察的进度层没有 errand_reminder 记录，提醒事件不是这次观察的进度来源，只从待办层该行的提醒钟点变为
    null 看得出已提醒过。"""
    engine = engine_factory()
    stage_day_plan(engine)
    zone = engine.scenario.timezone
    office = next(row["object"] for row in observe(engine, "preview")["interaction_bindings"]
                  if row["object_id"] == "office")
    walk = act(engine, "walk", start_activity("步行", office))
    assert walk.status == "in_progress", (walk.error_code, walk.message)
    arrival = engine.state.activities[walk.activity_id].planned_end_at
    staged = _errand("staged:errand:0", "买牛奶", MILK_ID, engine.state.current_time, remind_at=arrival)
    stage_errands(engine, 1, [staged])
    run_until(engine, lambda current: current.state.activities[walk.activity_id].status != ActivityStatus.ACTIVE)
    assert engine.state.current_time == arrival
    boundary = settlement(engine, arrival)
    assert boundary.data["decision_offered"] is True and "errand_reminder_due" in boundary.data["wake_reasons"]
    [event] = reminded(engine)
    assert_reminder(event, staged.model_copy(update={"reminded_at": arrival}), arrival)
    assert event.state_version_after == boundary.state_version_after
    [session] = [item for item in engine.state.sessions.values() if item.agent_id == 1 and item.status == "active"]
    assert (session.kind, session.started_at, session.opened_by_action_id) == ("stay", arrival, None)
    observed = observe(engine, "arrived")
    assert observed["progress_layer"]["session_started_at"] == iso_time(arrival, zone)
    assert [record for record in observed["progress_layer"]["events"] if record["type"].startswith("errand")] == []
    assert event.event_id not in observed["progress_event_ids"]
    [row] = observed["todo_layer"]["errands"]
    assert (row["name"], row["remind_time"]) == ("买牛奶", None)


def test_open_errands_change_neither_the_reconsideration_interval_nor_the_closing_warning(engine_factory) -> None:
    """未完成事务不是在手承诺（裁决 E6）。重估间隔 600 秒、空闲重估 1200 秒的场景里，人物等待中以空闲间隔重估时播报并
    记下一条不带提醒的事务，之后仍只是等待：下一次决策照样按空闲间隔排在 1200 秒之后并以 reconsideration_interval 到来，
    距上次决策 600 秒的边界不判重估；不带提醒的事务不另增候选时刻（下一边界候选只有零点）。在一个十分钟后闭店的工作
    地点，只有未完成事务时没有闭店预警，看得见该地点的工作任务时才有。"""
    engine = engine_factory(mutate=_short_intervals)
    zone = engine.scenario.timezone
    stage_day_plan(engine)
    start = engine.state.current_time
    waited = act(engine, "wait", routed(WAIT))
    assert engine.state.agents[1].next_decision_at == start + 1200
    assert advance_until_ready(engine) and engine.state.current_time == start + 1200
    noted = engine.state.current_time
    _, result = _act(engine, "note", "report_status", report_request(
        activity_name(engine, waited.activity_id), summary="接着等。", errands=[errand_add("买牛奶", MILK)]))
    assert result.status == "completed"
    agent = engine.state.agents[1]
    assert agent.errands and agent.next_decision_at == noted + 1200
    assert errands.reminder_times(agent, noted) == ()
    assert reminders.boundary_candidates(engine.state, zone) == [moment(engine, "2026-01-06 00:00")]
    state = engine.state.model_copy(deep=True)
    state.agents[1].last_decision_time = noted - 600
    _, verdicts = apply_decision_rhythm(replace(engine_rhythm_inputs(engine, state), interval_seconds=0))
    assert "reconsideration_interval" not in verdicts[1].wake_reasons
    assert advance_until_ready(engine) and engine.state.current_time == noted + 1200
    assert engine.state.agents[1].pending_wake_reasons == ["reconsideration_interval"]

    task = engine.scenario.tasks[0]
    state = engine.state.model_copy(deep=True)
    person = state.agents[1]
    person.location_id, person.last_decision_time = task.required_location_id, state.current_time
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(zone))
    state.opening_exceptions[person.location_id][local.date().isoformat()] = [(0, local.hour * 60 + local.minute + 10)]
    _, verdicts = apply_decision_rhythm(replace(engine_rhythm_inputs(engine, state), interval_seconds=0))
    assert person.errands and not {"commitment_threatened", "location_hours_change"} & set(verdicts[1].wake_reasons)
    person.visible_task_ids = person.known_task_ids = {task.task_id}
    _, verdicts = apply_decision_rhythm(replace(engine_rhythm_inputs(engine, state), interval_seconds=0))
    assert {"commitment_threatened", "location_hours_change"} <= set(verdicts[1].wake_reasons)


def test_partial_progress_after_the_reminder_does_not_remind_again(engine_factory) -> None:
    """到点只提醒一次，只有写了新钟点的更新才重新计一次（docs/dev/SPEC-errands-workday.md 2.5、2.7）。「取得纯牛奶 2 份」
    提醒 12:35：远程下单的一份还在途，12:35 提醒一次；之后这一份送达，事务进展到 1 份——结算只改累计量，提醒时刻与已提醒
    时刻原样，送达的那个边界不以 errand_reminder_due 提供决策，此后半小时也没有第二条提醒事件；待办层该行写「已取得
    1 份」，提醒钟点仍为 null。"""
    engine = home(engine_factory, {})
    note(engine, "note", [errand_add("囤牛奶", MILK, quantity=2, remind_time="12:35")])
    remind = moment(engine, "2026-01-05 12:35")
    ordered = buy(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    due = next(item.due_at for item in engine.state.pending_events
               if item.kind.value == "delivery_arrival" and order["order_id"] in item.data["order_ids"])
    assert due > remind
    assert act(engine, "wait", routed(WAIT)).status == "in_progress"
    until(engine, remind)
    [event] = reminded(engine)
    assert errand(engine, "囤牛奶").reminded_at == remind
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.current_time == due
    [progressed] = errand_events(engine, kinds=SETTLEMENT_KINDS)
    item = errand(engine, "囤牛奶")
    assert (item.progress, item.remind_at, item.reminded_at) == (1.0, remind, remind)
    assert (progressed.data["kind"], progressed.data["errand"]) == ("errand_progressed", item.model_dump(mode="json"))
    assert "errand_reminder_due" not in settlement(engine, due).data["wake_reasons"]
    run_until(engine, lambda current: current.state.current_time >= due + 1800)
    assert reminded(engine) == [event] and errand(engine, "囤牛奶") == item
    [row] = observe(engine, "after")["todo_layer"]["errands"]
    assert (row["name"], row["progress"], row["remind_time"]) == ("囤牛奶", "已取得 1 份", None)


def test_a_reminder_time_equal_to_the_present_is_not_a_candidate(engine_factory) -> None:
    """候选只取严格晚于此刻的时刻（docs/dev/SPEC-errands-workday.md 2.5「调度口径」）。请求造不出这种情形（提醒钟点换算出的
    时刻总严格晚于受理时刻，每个边界又把清醒人物全部已到点的提醒一并交付），这里以预置状态造出：清醒的人物持有一条未提醒
    的事务，提醒时刻正是此刻这一秒。她以真实请求开始等待之后，引擎报告的下一边界严格在此刻之后，这一时刻不是下一边界
    也不是下一次决策机会的候选，时钟照常推进；已到点的提醒在推进到的那个边界交付。"""
    engine = home(engine_factory, {})
    now = engine.state.current_time
    stage_errands(engine, 1, [_errand("staged:errand:0", "买牛奶", MILK_ID, now - 600, remind_at=now)])
    assert act(engine, "wait", routed(WAIT)).status == "in_progress"
    assert engine.state.current_time == now
    assert int(engine.scheduling_state().next_event_at.timestamp()) > now
    agent = engine.state.agents[1]
    assert errands.boundary_times(engine.state) == [] and errands.reminder_times(agent, now) == ()
    assert now not in reminders.boundary_candidates(engine.state, engine.scenario.timezone)
    assert agent.next_decision_at > now and reminded(engine) == []
    advance_next(engine)
    reached = engine.state.current_time
    assert reached > now
    [event] = reminded(engine)
    assert_reminder(event, errand(engine, "买牛奶"), reached)
    assert errand(engine, "买牛奶").reminded_at == reached
    assert "errand_reminder_due" in settlement(engine, reached).data["wake_reasons"]


def test_an_errand_completed_at_its_reminder_boundary_is_not_reminded(engine_factory) -> None:
    """时钟推进的结算在构造节律边界之前（docs/dev/SPEC-errands-workday.md 2.4「结算函数」），办成的事务不再提醒（2.5）。
    12:31 记下「吃掉纯牛奶 1 份」、提醒 12:46 并当秒开始喝：一整份 900 秒，恰在 12:46:00 喝完。这个边界先结算——事务办成、
    移出状态，然后才判定节律：没有提醒事件，该边界的唤醒原因不含 errand_reminder_due，办成事件的快照没有已提醒时刻，
    事务事件只有记下、进展与办成。"""
    engine = home(engine_factory, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("喝牛奶", MILK, outcome="吃掉", remind_time="12:46")])
    remind = moment(engine, "2026-01-05 12:46")
    assert errand(engine, "喝牛奶").remind_at == remind
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    assert engine.state.activities[drinking.activity_id].planned_end_at == remind
    run_until(engine, lambda current: current.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    assert engine.state.current_time == remind and engine.state.agents[1].errands == {}
    kinds = [event.data["kind"] for event in errand_events(engine)]
    assert (kinds[0], kinds[-1]) == ("errand_registered", "errand_completed")
    assert set(kinds[1:-1]) <= {"errand_progressed"}
    assert reminded(engine) == []
    boundary = settlement(engine, remind)
    assert "errand_reminder_due" not in boundary.data["wake_reasons"]
    [completed] = errand_events(engine, kinds=("errand_completed",))
    assert completed.state_version_after == boundary.state_version_after
    assert (completed.data["errand"]["remind_at"], completed.data["errand"]["reminded_at"]) == (remind, None)
