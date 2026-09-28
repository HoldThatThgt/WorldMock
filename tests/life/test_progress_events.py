"""进度层的有序记录（docs/dev/SPEC-persona-prompt.md「进度层」）。

session 下界：运行开始与到达取 session 开始时刻，出发与途中结束出行取引起切换的那条结果的事件序号；结果在整个 session
内保留；直接消息与交谈邀请带全文（不再截成 40 字），邮件只有主题；送达、截止预警与到期、目标提醒各只出现一次；同一秒内
推导记录在前、事件记录按来源序号；记忆查询只写条数；conclude_activity 的参数略去关系与偏好记忆；回复正文作为 self_note。
私有配套：progress_event_ids、presented_message_ids、progress_meta 的受保护标记与 settled 句。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, stage_day_plan, start_activity, submit_round, wake_by_mail)
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life import progress_layer
from worldmock.life.models import (ConcludeActivityRequest, PlanGoalInput, ReportStatusRequest,
                                   ReviseDayPlanRequest, SendMessageActionRequest, SessionStatus)
from worldmock.life.readable import clock_phrase, iso_time, money_phrase
from worldmock.life.settled_text import record_lines
from worldmock.life.schedule import DayPlan, PlanGoal, day_end_seconds
from worldmock.scheduling.events import ScheduledKind

_LONG_TEXT = "".join(f"第{index:03d}句话。" for index in range(50))[:300]


def _act(engine, round_id: str, tool: str, request, agent_id: int = 1):
    """单个就绪人物按真实顺序观察、登记并结算一轮，返回（本轮观察，结果）。"""
    decision = context(engine, round_id, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return observed, engine.commit_decision_round(round_id)[0]


def _observe(engine, round_id: str, agent_id: int = 1) -> dict:
    """读取一次当前观察（只追加观察事件，不开轮次）。"""
    return engine.observe(context(engine, round_id, agent_id))


def _events(observed: dict, kind: str | None = None) -> list[dict]:
    """进度层记录（可按 type 筛选）。"""
    return [row for row in observed["progress_layer"]["events"] if kind is None or row["type"] == kind]


def _revise(engine, round_id: str):
    """一次成功的瞬时世界请求：修订当日目标。"""
    return _act(engine, round_id, "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None),)))


def _walk_to_office(engine, round_id: str):
    """步行去办公室（出发请求）。"""
    observed = _observe(engine, f"{round_id}-preview")
    target = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == "office")
    return _act(engine, round_id, "start_activity", start_activity("步行", target))


def _lines(observed: dict, record: dict) -> list[str]:
    """一条进度层记录在 progress_meta 中的 settled 句。"""
    return observed["progress_meta"][observed["progress_layer"]["events"].index(record)]["lines"]


def _clock(record: dict) -> str:
    """记录发生时刻的本地 HH:MM（与观察同一天）。"""
    return datetime.fromisoformat(record["occurred_at"]).strftime("%H:%M")


def _append(engine, drafts: list[EventDraft]) -> list:
    """在不改变世界状态的前提下追加手工构造的事件（模拟宿主写入的模型证据与请求证据）。"""
    return engine.store.commit(engine.state.model_dump(mode="json"), drafts)


def test_run_start_session_is_time_bound_and_keeps_every_result(engine_factory) -> None:
    """运行开始的 session 事件序号下界为 0；本 session 的每个世界请求连同结果都保留，时间推进后仍在。"""
    engine = engine_factory()
    stage_day_plan(engine)
    start = engine.state.current_time
    assert progress_layer.session_bound(engine.store, engine.state, 1) == (start, 0)
    first = _observe(engine, "start-first")
    assert first["progress_layer"] == {"layer": "进度层", "session_started_at": iso_time(start, "Asia/Shanghai"),
                                       "events": []}
    _revise(engine, "start-revise")
    _act(engine, "start-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    later = _observe(engine, "start-later")
    assert [row["tool"] for row in progress_calls(later)] == ["revise_day_plan", "start_activity"]
    assert [row["type"] for row in _events(later)] == ["tool_call", "tool_call", "message_arrival"]
    assert later["progress_layer"]["session_started_at"] == iso_time(start, "Asia/Shanghai")


def test_started_activity_record_shows_the_latest_result_of_the_request(engine_factory) -> None:
    """开始活动的调用记录带该请求的最新结果：活动进行中时是受理时的 in_progress 与计划时长，结束后是终态结果；即时结果
    与终态结果都是记录的来源（浏览得到的商品名、到达等只在终态结果里，docs/dev/SPEC-persona-prompt.md「唯一出现原则」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, waited = _act(engine, "latest-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    (during,) = progress_calls(_observe(engine, "latest-during"), "start_activity")
    assert during["result"]["status"] == "in_progress"
    assert [row["name"] for row in during["result"]["details"]["planned"]] == [name]
    assert advance_until_ready(engine)
    results = [event for event in engine.store.read_agent_events("action_result", 1)
               if event.action_id == "action-latest-wait"]
    assert [event.data["status"] for event in results] == ["in_progress", "completed"]
    after = _observe(engine, "latest-after")
    (record,) = progress_calls(after, "start_activity")
    assert (record["result"]["status"], record["occurred_at"]) == ("completed", during["occurred_at"])
    assert "planned" not in record["result"]["details"]
    assert {event.event_id for event in results} <= set(after["progress_event_ids"])


def test_same_second_departure_opens_a_session_bounded_by_its_result(engine_factory) -> None:
    """同一秒内先修订目标再出发：出发引起的路上 session 以出发结果的事件序号为下界，修订与出发本身都留在前一个 session。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _revise(engine, "depart-revise")
    _, departure = _walk_to_office(engine, "depart-walk")
    session = next(item for item in engine.state.sessions.values() if item.status == "active")
    assert session.kind == "transit" and session.opened_by_action_id == "action-depart-walk"
    result_seq = next(event.event_seq for event in engine.store.read_agent_events("action_result", 1)
                      if event.action_id == "action-depart-walk")
    assert progress_layer.session_bound(engine.store, engine.state, 1) == (engine.state.current_time, result_seq)
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    on_road = _observe(engine, "depart-road")
    assert progress_calls(on_road) == []
    assert [row["type"] for row in _events(on_road)] == ["message_arrival"]
    assert departure.status == "in_progress"


def test_arrival_session_is_time_bound_and_excludes_the_departure(engine_factory) -> None:
    """到达（时钟推进）开始的停留 session 以到达时刻为下界：出发请求不在其中，到达那一秒的收尾调用在其中。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, departure = _walk_to_office(engine, "arrive-walk")
    assert advance_until_ready(engine)
    arrived = engine.state.current_time
    session = next(item for item in engine.state.sessions.values() if item.status == "active")
    assert (session.kind, session.opened_by_action_id, session.started_at) == ("stay", None, arrived)
    name = activity_name(engine, departure.activity_id)
    _act(engine, "arrive-conclude", "conclude_activity", conclude_request(name))
    observed = _observe(engine, "arrive-after")
    assert [row["tool"] for row in progress_calls(observed)] == ["conclude_activity"]
    assert progress_calls(observed)[0]["arguments"]["task"] == [name]


def test_en_route_conclusion_opens_a_session_bounded_by_the_conclusion_result(engine_factory) -> None:
    """途中结束出行：回到出发地的停留 session 记下这次结束的动作身份，进度层从该结果之后开始。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, departure = _walk_to_office(engine, "stop-walk")
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "stop-conclude", "conclude_activity", conclude_request(activity_name(engine, departure.activity_id)))
    session = next(item for item in engine.state.sessions.values() if item.status == "active")
    assert (session.kind, session.opened_by_action_id) == ("stay", "action-stop-conclude")
    observed = _observe(engine, "stop-after")
    assert progress_calls(observed) == [] and _events(observed) == []


def test_session_bound_fails_closed_without_an_active_session_or_the_opening_result(engine_factory) -> None:
    """session 下界只读已提交的世界与足迹：人物没有进行中的 session，或引起切换的动作在足迹中没有已提交结果时，以
    IntegrityError 失败关闭，不退回别的下界。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _walk_to_office(engine, "bound-walk")
    session = next(item for item in engine.state.sessions.values() if item.status == "active")
    assert session.opened_by_action_id == "action-bound-walk"
    closed = {key: item.model_copy(update={"status": SessionStatus.ENDED, "ended_at": engine.state.current_time})
              if key == session.session_id else item for key, item in engine.state.sessions.items()}
    with pytest.raises(IntegrityError, match="Agent has no active session"):
        progress_layer.session_bound(engine.store, engine.state.model_copy(update={"sessions": closed}), 1)
    orphan = {**engine.state.sessions,
              session.session_id: session.model_copy(update={"opened_by_action_id": "action-never-committed"})}
    with pytest.raises(IntegrityError, match="Session opening action has no committed result"):
        progress_layer.session_bound(engine.store, engine.state.model_copy(update={"sessions": orphan}), 1)


def test_direct_message_is_presented_in_full_and_marked_presented(engine_factory) -> None:
    """直接消息以全文进入收件人的进度层（300 字，不截成 40 字），带发件人姓名；消息身份进入 presented_message_ids，
    来源事件进入 progress_event_ids（docs/dev/SPEC-persona-prompt.md 第六章「直接消息截断」回归）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    sender, recipient = engine.state.agents[2], engine.state.agents[1]
    assert len(_LONG_TEXT) == 300
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("send_message", SendMessageActionRequest(recipient_name=f"人物：{recipient.name}",
                                                                       text=_LONG_TEXT))}, "message-send")
    observed = _observe(engine, "message-read", 1)
    (arrival,) = _events(observed, "message_arrival")
    assert (arrival["sender"], arrival["kind"], arrival["text"]) == (sender.name, "direct", _LONG_TEXT)
    message_id = next(iter(engine.state.agents[1].inbox))
    assert observed["presented_message_ids"] == [message_id]
    source = next(event for event in engine.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "message_arrival")
    assert source.event_id in observed["progress_event_ids"]
    rendered = json.dumps({key: observed[key] for key in ("tool_layer", "todo_layer", "environment_layer",
                                                          "memory_layer", "progress_layer")}, ensure_ascii=False)
    assert rendered.count(_LONG_TEXT) == 1
    assert _lines(observed, arrival) == [f"{_clock(arrival)} 收到{sender.name}的消息：{_LONG_TEXT}"]


def test_mail_arrival_names_only_the_subject(engine_factory) -> None:
    """邮件到达只写主题，发件人为 null；正文在阅读结果中，不在到达记录里。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _act(engine, "mail-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    observed = _observe(engine, "mail-seen")
    (mail,) = _events(observed, "message_arrival")
    message = engine.scenario.messages[0]
    assert mail == {"occurred_at": mail["occurred_at"], "type": "message_arrival", "sender": None, "kind": "mail",
                    "subject": message.subject}
    assert _lines(observed, mail) == [f"{_clock(mail)} 收到邮件「{message.subject}」"]


def test_delivery_appears_once_with_order_label_and_message(engine_factory) -> None:
    """订单送达时进度层写一条 delivery：订单名（商品、份数、下单时刻与方式）、状态 delivered 与「已送到<地点>」，只出现一次。"""
    from tests.life.test_dayrun_restaurants import _keep_waiting, _stage

    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    observed = _observe(engine, "order-preview")
    target, label = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                         if row["kind"] == "supply" for action in row["actions"]
                         if action["label"] == "远程下单" and action["note"] is None)
    _, bought = _act(engine, "order-buy", "start_activity", start_activity(label, target))
    order = bought.data["orders"][0]
    for step in range(64):
        if engine.state.deliveries[order["order_id"]].status != "pending":
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"order-wait-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    assert advance_until_ready(engine)
    delivered = _observe(engine, "order-delivered")
    (delivery,) = _events(delivered, "delivery")
    product = engine.rules.items[order["product_id"]].name
    place = next(item.name for item in engine.scenario.locations if item.location_id == order["destination_id"])
    assert delivery["order"].startswith(f"{product} 1 份（") and delivery["order"].endswith("远程下单）")
    assert (delivery["status"], delivery["message"]) == ("delivered", f"已送到{place}")
    assert len(_events(_observe(engine, "order-again"), "delivery")) == 1
    zone = engine.scenario.timezone
    (purchase,) = [row for row in progress_calls(delivered, "start_activity")
                   if row["result"]["details"].get("purchases")]
    assert _lines(delivered, purchase) == [
        f"{clock_phrase(order['created_at'], engine.state.current_time, zone)} 远程下单{product} 1 份，"
        f"付 {money_phrase(order['total_price_fen'])}，"
        f"预计 {clock_phrase(order['expected_at'], engine.state.current_time, zone)} 送到{place}"]
    assert _lines(delivered, delivery) == [f"{_clock(delivery)} {delivery['order']}：已送到{place}"]


def _stage_task_near_due(engine, before_due: int) -> str:
    """首个任务可见、人物在办公室，时刻为截止前 before_due 秒；返回任务目标名。"""
    task = engine.scenario.tasks[0]
    now = int(task.due_at.timestamp()) - before_due
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={
        "location_id": task.required_location_id, "visible_task_ids": {*agent.visible_task_ids, task.task_id},
        "next_decision_at": now, "body_settled_at": now}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_due_staged"})])
    engine.state = staged
    return f"工作：{task.title}"


def test_task_deadline_records_due_soon_and_expired_once_each(engine_factory) -> None:
    """截止前 1800 秒写一条 due_soon，截止时刻写一条 expired，都由截止时刻推导（不新增事件），各只出现一次。"""
    engine = engine_factory()
    name = _stage_task_near_due(engine, 1800)
    soon = _events(_observe(engine, "due-soon"), "task_deadline")
    assert soon == [{"occurred_at": soon[0]["occurred_at"], "type": "task_deadline", "task": name,
                     "stage": "due_soon"}]
    _stage_task_near_due(engine, 0)
    records = _events(_observe(engine, "due-now"), "task_deadline")
    assert [(row["task"], row["stage"]) for row in records] == [(name, "due_soon"), (name, "expired")]


def _reschedule_delivery(engine, order_id: str, due_at: int) -> None:
    """把一个订单的预排送达移到 due_at（到时仍由真实时钟结算），保持存储版本连续。"""
    staged = engine.state.model_copy(deep=True)
    event = next(item for item in staged.pending_events if item.kind == ScheduledKind.DELIVERY_ARRIVAL
                 and order_id in json.dumps(item.data))
    sequence = max(item.sequence for item in staged.pending_events) + 1
    staged.pending_events = sorted(
        [item for item in staged.pending_events if item.scheduled_id != event.scheduled_id]
        + [event.model_copy(update={"due_at": due_at, "sequence": sequence})],
        key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_delivery_rescheduled"})])
    engine.state = staged


def _wait_until(engine, moment: int, label: str) -> None:
    """只经真实时间边界推进到 moment：途中的决策机会先给刚结束的等待收尾，再接着等待。"""
    from tests.life.test_dayrun_restaurants import _keep_waiting

    for step in range(64):
        if engine.state.current_time >= moment:
            return
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"{label}-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    pytest.fail("The clock did not reach the requested moment")


def _evening_at_the_office(engine) -> tuple[str, int]:
    """18:00 人在办公室、首个任务（20:00 截止）可见；人物写下 19:30 提醒的今日目标，远程下单一份送到这里，并把它的
    送达（预计更早）推迟到 19:30、再安排一封 19:30 的邮件。返回任务目标名与截止预警时刻（截止前 1800 秒）。"""
    name = _stage_task_near_due(engine, 7200)
    stage_day_plan(engine)
    soon = engine.state.current_time + 5400
    remind = datetime.fromtimestamp(soon, ZoneInfo(engine.scenario.timezone)).strftime("%H:%M")
    _act(engine, "evening-plan", "revise_day_plan", ReviseDayPlanRequest(
        goals=(PlanGoalInput(intent_kind="休息", goal="交稿前喝口水", remind_time=remind),)))
    order = _order_to_the_office(engine)
    assert order["expected_at"] < soon
    _reschedule_delivery(engine, order["order_id"], soon)
    wake_by_mail(engine, offset=5400)
    return name, soon


def _order_to_the_office(engine) -> dict:
    """按名称（「<门店>出售的<商品>」）远程下单一份送到办公室：依次试能送到这里的供给，返回第一笔受理的订单。"""
    from worldmock.life.named_purchase import supply_label

    context_ = engine._layer_context(engine.state.agents[1], (), [])
    labels = [supply_label(context_, supply) for supply in engine.scenario.supplies
              if "delivery" in supply.fulfillment_modes and "office" in supply.delivery_destination_ids]
    for index, label in enumerate(labels):
        _, bought = _act(engine, f"evening-order-{index}", "start_activity", start_activity("远程下单", label))
        if bought.status == "completed":
            return bought.data["orders"][0]
    pytest.fail("No supply delivers to the office at this hour")


def test_same_second_deadline_reminder_arrivals_and_call_keep_a_stable_order(engine_factory) -> None:
    """同一秒（19:30）的五种记录顺序稳定：真实时钟推进到截止前 1800 秒时推导出 task_deadline（due_soon）与 plan_reminder，
    推导记录在前（截止在提醒前），同秒结算的邮件到达与送达按来源事件序号，人物当秒的调用最后；再观察一次顺序不变。"""
    from tests.life.test_dayrun_restaurants import _keep_waiting

    engine = engine_factory()
    name, soon = _evening_at_the_office(engine)
    _wait_until(engine, soon, "evening-wait")
    assert engine.state.current_time == soon
    woken = _observe(engine, "evening-woken")
    assert {"task_due_soon", "plan_reminder_due"} <= set(woken["wake_reasons"])
    assert engine.state.agents[1].day_plan.goals[0].reminded_at == soon
    _act(engine, "evening-call", *_keep_waiting(engine))
    moment = iso_time(soon, engine.scenario.timezone)
    arrivals = sorted((event for event in engine.store.read_agent_events("environment_event", 1)
                       if event.occurred_at == soon and event.data.get("kind") in {"mail_arrival",
                                                                                   "delivery_arrival"}),
                      key=lambda event: event.event_seq)
    kinds = {"mail_arrival": "message_arrival", "delivery_arrival": "delivery"}
    expected = ["task_deadline", "plan_reminder", *(kinds[event.data["kind"]] for event in arrivals), "tool_call"]
    first, again = (_observe(engine, label) for label in ("evening-seen", "evening-again"))
    same = [row for row in _events(first) if row["occurred_at"] == moment]
    assert [row["type"] for row in same] == expected and len(arrivals) == 2
    assert (same[0]["task"], same[0]["stage"], same[1]["goal"]) == (name, "due_soon", "交稿前喝口水")
    assert [row for row in _events(again) if row["occurred_at"] == moment] == same


def test_the_advancing_clock_derives_due_soon_reminder_and_expiry_once_each(engine_factory) -> None:
    """截止预警、目标提醒与到期都由真实时钟推进得到（不手置时刻）：19:15 的边界上进度层还没有它们；时钟推进到 20:00 截止时
    19:30 的 due_soon 与提醒各出现一次，截止时刻出现 expired。"""
    engine = engine_factory()
    name, soon = _evening_at_the_office(engine)
    _wait_until(engine, soon - 900, "clock-before")
    assert engine.state.current_time == soon - 900
    before = _observe(engine, "clock-before-seen")
    assert not [row for row in _events(before) if row["type"] in {"task_deadline", "plan_reminder"}]
    _wait_until(engine, soon, "clock-soon")
    _wait_until(engine, soon + 1800, "clock-due")
    assert engine.state.current_time == soon + 1800
    derived = [(row["type"], row.get("stage") or row.get("goal"), row["occurred_at"])
               for row in _events(_observe(engine, "clock-due-seen")) if row["type"] in {"task_deadline",
                                                                                         "plan_reminder"}]
    zone = engine.scenario.timezone
    assert derived == [("task_deadline", "due_soon", iso_time(soon, zone)),
                       ("plan_reminder", "交稿前喝口水", iso_time(soon, zone)),
                       ("task_deadline", "expired", iso_time(soon + 1800, zone))]
    assert all(row["task"] == name for row in _events(_observe(engine, "clock-again"), "task_deadline"))


def _stage_plan(engine, reminded_at: int | None) -> None:
    """预置当日计划：一个已设提醒的目标，reminded_at 为提醒触发时刻（None 为尚未触发）。"""
    now = engine.state.current_time
    goal = PlanGoal(intent_kind="休息", goal="午后歇一会", registered_at=now - 60, remind_at=now - 30,
                    reminded_at=reminded_at)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=day_end_seconds(now, engine.scenario.timezone), goals=(goal,))}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_plan_staged"})])
    engine.state = staged


def test_plan_reminder_appears_once_and_precedes_same_second_events(engine_factory) -> None:
    """目标提醒由提醒写入时刻推导，只写目标文字、只出现一次；同一秒内推导记录排在事件记录之前。"""
    engine = engine_factory()
    now = engine.state.current_time + 120
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": now, "body_settled_at": now},
                                                   deep=True)
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_moment"})])
    engine.state = staged
    _stage_plan(engine, reminded_at=None)
    assert _events(_observe(engine, "reminder-none"), "plan_reminder") == []
    _stage_plan(engine, reminded_at=now)
    _act(engine, "reminder-wait", "start_activity", start_activity("等待", "自己"))
    observed = _observe(engine, "reminder-seen")
    assert [row["type"] for row in _events(observed)] == ["plan_reminder", "tool_call"]
    assert _events(observed, "plan_reminder")[0]["goal"] == "午后歇一会"
    assert len(_events(_observe(engine, "reminder-again"), "plan_reminder")) == 1


def _model_pair(engine, decision_id: str, call: dict, tool_content: str) -> list:
    """追加一次记忆查询的模型证据：响应带工具调用，同一决策的下一请求带对应 tool_call_id 的工具消息。"""
    now = engine.state.current_time
    first = _append(engine, [EventDraft(
        event_type="model_request", occurred_at=now, agent_id=1, decision_id=decision_id, visibility="system",
        source_kind="model", data={"phase": "request", "call_id": f"{decision_id}-1", "attempt": 1,
                                   "request": {"messages": []}, "response": None, "error": None})])[0]
    response = _append(engine, [EventDraft(
        event_type="model_response", occurred_at=now, agent_id=1, decision_id=decision_id, visibility="system",
        source_kind="model", caused_by=[first.event_id],
        data={"phase": "response", "call_id": f"{decision_id}-1", "attempt": 1, "request": None, "error": None,
              "response": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}})])
    following = _append(engine, [EventDraft(
        event_type="model_request", occurred_at=now, agent_id=1, decision_id=decision_id, visibility="system",
        source_kind="model", data={"phase": "request", "call_id": f"{decision_id}-2", "attempt": 1,
                                   "request": {"messages": [{"role": "tool", "tool_call_id": call["id"],
                                                             "content": tool_content}]},
                                   "response": None, "error": None})])
    return [response[0], following[0]]


def test_successful_memory_query_is_recorded_with_its_count_and_failures_are_not(engine_factory) -> None:
    """此前决策里成功的记忆查询作为 tool_call 记录：参数原文，结果只写条数；失败（工具结果带 error）的查询不记录。"""
    engine = engine_factory()
    stage_day_plan(engine)
    call = {"id": "call-memory", "type": "function",
            "function": {"name": "memory_search", "arguments": json.dumps({"query": "咖啡"}, ensure_ascii=False)}}
    episodes = {"epistemic_status": "subjective", "episodes": [
        {"type": "reflection", "text": "喝了咖啡", "time": "1", "epistemic_status": "subjective"},
        {"type": "reflection", "text": "咖啡很苦", "time": "2", "epistemic_status": "subjective"}]}
    sources = _model_pair(engine, "decision-memory-ok", call, json.dumps(episodes, ensure_ascii=False))
    failed = {**call, "id": "call-memory-failed"}
    _model_pair(engine, "decision-memory-failed", failed, json.dumps({"error": "memory unavailable"}))
    observed = _observe(engine, "memory-seen")
    (record,) = progress_calls(observed, "memory_search")
    assert record["arguments"] == {"query": "咖啡"}
    assert record["result"] == {"occurred_at": record["occurred_at"], "status": "completed", "error_code": None,
                                "message": "Memory query returned 2 episodes", "details": {"episodes": 2}}
    assert [event.event_id for event in sources] == observed["progress_event_ids"][:2]
    assert "喝了咖啡" not in json.dumps(observed["progress_layer"], ensure_ascii=False)


def test_conclude_arguments_omit_relationship_and_preference_memories(engine_factory) -> None:
    """conclude_activity 的参数原样进入进度层，只略去 relationship 与 preference 记忆（它们只在人设层出现）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, waited = _act(engine, "memo-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    memories = [[{"type": "relationship", "text": "和同事处得不错", "importance": 0.5, "keywords": ["同事"],
                  "subject": "沈昊"},
                 {"type": "preference", "text": "喜欢安静", "importance": 0.5, "keywords": ["安静"], "subject": "环境"},
                 {"type": "routine", "text": "每天等一会儿", "importance": 0.4, "keywords": ["等待"], "subject": None}]]
    _act(engine, "memo-conclude", "conclude_activity", ConcludeActivityRequest(
        task=[name], summary=["等完了"], memories=memories))
    seen = _observe(engine, "memo-seen")
    (record,) = progress_calls(seen, "conclude_activity")
    assert record["arguments"]["summary"] == ["等完了"]
    assert [item["type"] for item in record["arguments"]["memories"][0]] == ["routine"]
    ended, zone, now = engine.state.activities[waited.activity_id], engine.scenario.timezone, engine.state.current_time
    span = f"{clock_phrase(ended.started_at, now, zone)}–{clock_phrase(ended.ended_at, now, zone)}"
    assert _lines(seen, record) == [f"{name}：{span}，提前结束"]


def _hand_request(engine, action_id: str, self_note: str | None) -> None:
    """追加一对手工请求证据（宿主写入 action_requested.data.self_note）与其结果。"""
    now = engine.state.current_time
    request = _append(engine, [EventDraft(
        event_type="action_requested", occurred_at=now, agent_id=1, decision_id=f"decision-{action_id}",
        action_id=action_id, source_kind="model",
        data={"tool_name": "revise_day_plan", "request": {"goals": []}, "request_sha256": "0" * 64,
              "reused": False, "self_note": self_note})])[0]
    _append(engine, [EventDraft(
        event_type="action_result", occurred_at=now, agent_id=1, decision_id=f"decision-{action_id}",
        action_id=action_id, caused_by=[request.event_id],
        data={"action_id": action_id, "agent_id": 1, "status": "completed", "occurred_at": now,
              "activity_id": None, "error_code": None, "message": "Day plan revised", "data": {}})])


def test_self_note_is_copied_from_the_request_and_omitted_when_empty(engine_factory) -> None:
    """回复正文作为该调用的 self_note 出现在进度层；没有正文（null 或空）时不写该键。"""
    engine = engine_factory()
    _hand_request(engine, "noted", "先把计划理一理。")
    _hand_request(engine, "silent", None)
    calls = progress_calls(_observe(engine, "note-seen"))
    assert [row.get("self_note") for row in calls] == ["先把计划理一理。", None]
    assert "self_note" not in calls[1]


def _action_request(engine, decision_id: str) -> None:
    """追加一次行动请求证据（无 purpose、thinking 开启、原生 tools 含 start_activity）。"""
    request = {"model": "deepseek-flash", "messages": [], "tools": [
        {"type": "function", "function": {"name": "start_activity", "parameters": {"type": "object"}}}],
        "extra_body": {"thinking": {"type": "enabled"}}}
    _append(engine, [EventDraft(
        event_type="model_request", occurred_at=engine.state.current_time, agent_id=1, decision_id=decision_id,
        visibility="system", source_kind="model",
        data={"phase": "request", "call_id": f"{decision_id}-1", "attempt": 1, "request": request,
              "response": None, "error": None})])


def test_meta_marks_records_after_the_last_action_request_as_protected_and_writes_lines(engine_factory) -> None:
    """progress_meta 与记录一一对应：上次行动请求之后的新记录受保护，之前的不受保护（与在途订单无关时）；成功的发消息写
    settled 句，修订目标与失败的请求不写。"""
    engine = engine_factory()
    stage_day_plan(engine)
    other = engine.scenario.agents[0].contacts[0].salutation
    first, rejected = _act(engine, "meta-reject", "start_activity", start_activity("观看", "不存在的对象"))
    assert rejected.status == "rejected"
    observed, sent = _act(engine, "meta-send", "send_message", SendMessageActionRequest(
        recipient_name=f"联系人：{other}", text="晚上一起吃饭吗"))
    assert sent.status == "completed"
    _action_request(engine, "decision-meta")
    _revise(engine, "meta-revise")
    seen = _observe(engine, "meta-seen")
    assert len(seen["progress_meta"]) == len(_events(seen)) == 3
    assert [row["protected"] for row in seen["progress_meta"]] == [False, False, True]
    assert seen["progress_meta"][0]["lines"] == []
    assert seen["progress_meta"][1]["lines"][0].endswith(f"给{other}发消息：晚上一起吃饭吗")
    assert seen["progress_meta"][2]["lines"] == []
    assert first["progress_meta"] == [] and all(row["protected"] for row in observed["progress_meta"])


def test_records_after_gives_the_progress_records_whose_sources_follow_the_bound(engine_factory) -> None:
    """records_after 只给来源序号都在界之后的事件记录，记录与进度层中的同一条 tool_call 逐字段相同（续接的 tool 消息
    与进度层同一投影）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _revise(engine, "after-first")
    boundary = engine.store.last_event_seq
    _act(engine, "after-second", "start_activity", start_activity("等待", "自己"))
    observed = _observe(engine, "after-seen")
    from worldmock.life.tool_layer import LayerContext
    context_ = LayerContext(state=engine.state, scenario=engine.scenario, rules=engine.rules,
                            semantics=engine.semantics, agent_id=1, names={"sessions": {}, "activities": {}},
                            bindings=observed["interaction_bindings"], menu=None,
                            lifestyle_items=engine.lifestyle_items)
    after = progress_layer.records_after(context_, engine.store, boundary)
    assert [record["tool"] for record, _ in after] == ["start_activity"]
    assert after[0][0] == progress_calls(observed)[-1]


def test_submission_lines_say_passed_or_rework_and_other_failures_write_nothing(engine_factory) -> None:
    """提交工作的 settled 句：通过写「HH:MM 提交「<任务>」，通过」，需要返工写「未通过」，其他被拒的提交不写；
    修订目标、回看这类调用即使成功也不写。"""
    engine = engine_factory()
    stage_day_plan(engine)
    from worldmock.life.tool_layer import LayerContext
    context_ = LayerContext(state=engine.state, scenario=engine.scenario, rules=engine.rules,
                            semantics=engine.semantics, agent_id=1, names={"sessions": {}, "activities": {}},
                            bindings=[], menu=None, lifestyle_items=engine.lifestyle_items)
    moment = iso_time(engine.state.current_time, engine.scenario.timezone)
    clock = moment[11:16]

    def lines(tool: str, arguments: dict, status: str, error_code: str | None = None) -> list[str]:
        """一条手工 tool_call 记录与其即时结果的 settled 句。"""
        record = {"occurred_at": moment, "type": "tool_call", "tool": tool, "arguments": arguments}
        result = SimpleNamespace(event_type="action_result",
                                 data={"status": status, "error_code": error_code, "data": {}})
        return record_lines(context_, record, [SimpleNamespace(event_type="action_requested", data={}), result])

    submission = {"task_name": "工作：会议纪要"}
    assert lines("submit_work", submission, "completed") == [f"{clock} 提交「会议纪要」，通过"]
    assert lines("submit_work", submission, "rejected", "WORK_REQUIRES_REWORK") == [f"{clock} 提交「会议纪要」，未通过"]
    assert lines("submit_work", submission, "rejected", "WORK_SUBMISSION_LATE") == []
    assert lines("revise_day_plan", {"goals": []}, "completed") == []
    assert lines("recall_task_summary", {"task_name": "工作：会议纪要"}, "completed") == []


def test_round_commit_reads_only_messages_presented_in_full(engine_factory) -> None:
    """轮次提交时「已读」即全文已呈现（docs/dev/SPEC-persona-prompt.md 第六章「直接消息截断」）：本 session 进度层呈现过全文
    的消息提交后记为已读；留在出发前 session 的消息不在路上 session 的进度层，出发前的 session 结束后它以全文进入记忆层，
    记忆层的全文同样算呈现，路上那一轮提交后记为已读。

    人物 1 先发第一条；人物 2 在同一秒看到它（呈现）并出发，同一轮人物 1 先结算、再发第二条，它的送达排在出发结果之前，
    留在出发前的 session；人物 2 出发前的观察看不到第二条，提交后仍未读。
    """
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    friend = f"人物：{engine.state.agents[2].name}"
    preview = _observe(engine, "read-preview", 2)
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    revise = ReviseDayPlanRequest(goals=(PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None),))
    _, first = submit_round(engine, {1: ("send_message", SendMessageActionRequest(recipient_name=friend, text="第一条")),
                                     2: ("revise_day_plan", revise)}, "read-first")
    shown = next(item.data["message_id"] for item in first if item.agent_id == 1)
    _, second = submit_round(engine, {
        1: ("send_message", SendMessageActionRequest(recipient_name=friend, text="第二条")),
        2: ("start_activity", start_activity("步行", office))}, "read-second")
    hidden = next(item.data["message_id"] for item in second if item.agent_id == 1)
    presented = engine.store.read_agent_events("observation", 2)[-1].data["presented_message_ids"]
    assert presented == [shown] and shown in engine.state.agents[2].read_inbox_ids
    assert hidden in engine.state.agents[2].inbox and hidden not in engine.state.agents[2].read_inbox_ids
    walking = next(item.activity_id for item in second if item.agent_id == 2)
    report = ReportStatusRequest(task=[activity_name(engine, walking)], summary=["路上看看消息。"])
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")), 2: ("report_status", report)},
                 "read-road")
    road = engine.store.read_agent_events("observation", 2)[-1].data
    arrival = next(event.event_id for event in engine.store.read_agent_events("environment_event", 2)
                   if event.data.get("message_id") == hidden)
    assert arrival not in road["progress_event_ids"]
    (entry,) = road["memory_layer"]["earlier_today"]
    assert [line.split("：", 1)[1] for line in entry["settled"] if "的消息：" in line] == ["第一条", "第二条"]
    assert road["presented_message_ids"] == [shown, hidden] and hidden in engine.state.agents[2].read_inbox_ids
