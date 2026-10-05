"""进度层的有序记录（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」）。

session 下界：运行开始与到达取 session 开始时刻，出发与途中结束出行取引起切换的那条结果的事件序号；结果在整个 session
内保留；直接消息与交谈邀请带全文（不再截成 40 字），邮件只有主题；送达、截止预警与到期、目标提醒各只出现一次；同一秒内
推导记录在前、事件记录按来源序号；只读查询只留模型证据；conclude_activity 的参数略去关系与偏好记忆；回复正文作为 self_note。
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
                                 errand_add, errand_cancel, progress_calls, stage_day_plan, start_activity, submit_round,
                                 wake_by_mail, report_request, stage_legacy_control_request)
from tests.life.routing_fixtures import HOME, MARKET, act, observe, open_world, place, routed, run_until
from tests.life.test_errand_completion import (CHEN, DRINK, KIT_NAME, MILK, MILK_ID, WAIT, buy, first, give, home, note,
                                               ready, result_event, settlement)
from tests.life.test_errand_reminder import moment as local_moment, until
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life import progress_layer, prompt_capacity
from worldmock.life.guidance import rejection_guidance
from worldmock.life.models import (ActivityStatus, PlanGoalInput, RecallTaskSummaryRequest, ReviseDayPlanRequest,
                                   SendMessageActionRequest, SessionStatus)
from worldmock.life.prompt_capacity import ProgressBudget
from worldmock.life.prompt_schemas import validate_layer
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
        goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None),]))


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
    return engine.context.store.commit(engine.context.state.model_dump(mode="json"), drafts)


def test_run_start_session_is_time_bound_and_keeps_every_result(engine_factory) -> None:
    """运行开始的 session 事件序号下界为 0；本 session 的每个世界请求连同结果都保留，时间推进后仍在。"""
    engine = engine_factory()
    stage_day_plan(engine)
    start = engine.context.state.current_time
    assert progress_layer.session_bound(engine.context.store, engine.context.state, 1) == (start, 0)
    first = _observe(engine, "start-first")
    assert first["progress_layer"] == {"layer": "进度层", "session_started_at": iso_time(start, "Asia/Shanghai"),
                                       "events": [], "rejected_today": []}
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
    与终态结果都是记录的来源（浏览得到的商品名、到达等只在终态结果里，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「唯一出现原则」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, waited = _act(engine, "latest-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    (during,) = progress_calls(_observe(engine, "latest-during"), "start_activity")
    assert during["result"]["status"] == "in_progress"
    assert [row["name"] for row in during["result"]["details"]["planned"]] == [name]
    assert advance_until_ready(engine)
    results = [event for event in engine.context.store.read_agent_events("action_result", 1)
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
    session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
    assert session.kind == "transit" and session.opened_by_action_id == "action-depart-walk"
    result_seq = next(event.event_seq for event in engine.context.store.read_agent_events("action_result", 1)
                      if event.action_id == "action-depart-walk")
    assert progress_layer.session_bound(engine.context.store, engine.context.state, 1) == (engine.context.state.current_time, result_seq)
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
    arrived = engine.context.state.current_time
    session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
    assert (session.kind, session.opened_by_action_id, session.started_at) == ("stay", None, arrived)
    name = activity_name(engine, departure.activity_id)
    _act(engine, "arrive-conclude", "conclude_activity", conclude_request(name))
    observed = _observe(engine, "arrive-after")
    assert [row["tool"] for row in progress_calls(observed)] == ["conclude_activity"]
    assert progress_calls(observed)[0]["arguments"] == {
        "items": [{"activity_name": name, "summary": "收尾。", "memories": []}]}


def test_en_route_conclusion_opens_a_session_bounded_by_the_conclusion_result(engine_factory) -> None:
    """途中结束出行：回到出发地的停留 session 记下这次结束的动作身份，进度层从该结果之后开始。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, departure = _walk_to_office(engine, "stop-walk")
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "stop-conclude", "conclude_activity", conclude_request(activity_name(engine, departure.activity_id)))
    session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
    assert (session.kind, session.opened_by_action_id) == ("stay", "action-stop-conclude")
    observed = _observe(engine, "stop-after")
    assert progress_calls(observed) == [] and _events(observed) == []


def test_session_bound_fails_closed_without_an_active_session_or_the_opening_result(engine_factory) -> None:
    """session 下界只读已提交的世界与足迹：人物没有进行中的 session，或引起切换的动作在足迹中没有已提交结果时，以
    IntegrityError 失败关闭，不退回别的下界。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _walk_to_office(engine, "bound-walk")
    session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
    assert session.opened_by_action_id == "action-bound-walk"
    closed = {key: item.model_copy(update={"status": SessionStatus.ENDED, "ended_at": engine.context.state.current_time})
              if key == session.session_id else item for key, item in engine.context.state.sessions.items()}
    with pytest.raises(IntegrityError, match="Agent has no active session"):
        progress_layer.session_bound(engine.context.store, engine.context.state.model_copy(update={"sessions": closed}), 1)
    orphan = {**engine.context.state.sessions,
              session.session_id: session.model_copy(update={"opened_by_action_id": "action-never-committed"})}
    with pytest.raises(IntegrityError, match="Session opening action has no committed result"):
        progress_layer.session_bound(engine.context.store, engine.context.state.model_copy(update={"sessions": orphan}), 1)


def test_direct_message_is_presented_in_full_and_marked_presented(engine_factory) -> None:
    """直接消息以全文进入收件人的进度层（300 字，不截成 40 字），带发件人姓名；消息身份进入 presented_message_ids，
    来源事件进入 progress_event_ids（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」回归）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    sender, recipient = engine.context.state.agents[2], engine.context.state.agents[1]
    assert len(_LONG_TEXT) == 300
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("send_message", SendMessageActionRequest(recipient_name=f"人物：{recipient.name}",
                                                                       text=_LONG_TEXT))}, "message-send")
    observed = _observe(engine, "message-read", 1)
    (arrival,) = _events(observed, "message_arrival")
    assert (arrival["sender"], arrival["kind"], arrival["text"]) == (sender.name, "direct", _LONG_TEXT)
    message_id = next(iter(engine.context.state.agents[1].inbox))
    assert observed["presented_message_ids"] == [message_id]
    source = next(event for event in engine.context.store.read_agent_events("environment_event", 1)
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
    message = engine.context.scenario.messages[0]
    task = next(item for item in engine.context.scenario.tasks if item.task_id == message.related_task_id)
    assert mail == {"occurred_at": mail["occurred_at"], "type": "message_arrival", "sender": None, "kind": "mail",
                    "subject": message.subject, "task": f"工作：{task.title}"}
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
        if engine.context.state.deliveries[order["order_id"]].status != "pending":
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"order-wait-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    assert advance_until_ready(engine)
    delivered = _observe(engine, "order-delivered")
    (delivery,) = _events(delivered, "delivery")
    product = engine.context.rules.items[order["product_id"]].name
    place = next(item.name for item in engine.context.scenario.locations if item.location_id == order["destination_id"])
    assert delivery["order"].startswith(f"{product} 1 份（") and delivery["order"].endswith("远程下单）")
    assert (delivery["status"], delivery["message"]) == ("delivered", f"已送到{place}")
    assert len(_events(_observe(engine, "order-again"), "delivery")) == 1
    zone = engine.context.scenario.timezone
    (purchase,) = [row for row in progress_calls(delivered, "start_activity")
                   if row["result"]["details"].get("purchases")]
    assert _lines(delivered, purchase) == [
        f"{clock_phrase(order['created_at'], engine.context.state.current_time, zone)} 远程下单{product} 1 份，"
        f"付 {money_phrase(order['total_price_fen'])}，"
        f"预计 {clock_phrase(order['expected_at'], engine.context.state.current_time, zone)} 送到{place}"]
    assert _lines(delivered, delivery) == [f"{_clock(delivery)} {delivery['order']}：已送到{place}"]


def _stage_task_near_due(engine, before_due: int) -> str:
    """首个任务可见、人物在办公室，时刻为截止前 before_due 秒；返回任务目标名。"""
    task = engine.context.scenario.tasks[0]
    now = int(task.due_at.timestamp()) - before_due
    staged = engine.context.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={
        "location_id": task.required_location_id, "visible_task_ids": {*agent.visible_task_ids, task.task_id},
        "next_decision_at": now, "body_settled_at": now}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_due_staged"})])
    engine.context.state = staged
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
    staged = engine.context.state.model_copy(deep=True)
    event = next(item for item in staged.pending_events if item.kind == ScheduledKind.DELIVERY_ARRIVAL
                 and order_id in json.dumps(item.data))
    sequence = max(item.sequence for item in staged.pending_events) + 1
    staged.pending_events = sorted(
        [item for item in staged.pending_events if item.scheduled_id != event.scheduled_id]
        + [event.model_copy(update={"due_at": due_at, "sequence": sequence})],
        key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_delivery_rescheduled"})])
    engine.context.state = staged


def _wait_until(engine, moment: int, label: str) -> None:
    """只经真实时间边界推进到 moment：途中的决策机会先给刚结束的等待收尾，再接着等待。"""
    from tests.life.test_dayrun_restaurants import _keep_waiting

    for step in range(64):
        if engine.context.state.current_time >= moment:
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
    soon = engine.context.state.current_time + 5400
    remind = datetime.fromtimestamp(soon, ZoneInfo(engine.context.scenario.timezone)).strftime("%H:%M")
    _act(engine, "evening-plan", "revise_day_plan", ReviseDayPlanRequest(
        goals=[PlanGoalInput(intent_kind="休息", goal="交稿前喝口水", remind_time=remind, around_time=None),]))
    order = _order_to_the_office(engine)
    assert order["expected_at"] < soon
    _reschedule_delivery(engine, order["order_id"], soon)
    wake_by_mail(engine, offset=5400)
    return name, soon


def _order_to_the_office(engine) -> dict:
    """按名称（「<门店>出售的<商品>」）远程下单一份送到办公室：依次试能送到这里的供给，返回第一笔受理的订单。"""
    from worldmock.life.named_purchase import supply_label

    context_ = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    labels = [supply_label(context_, supply) for supply in engine.context.scenario.supplies
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
    assert engine.context.state.current_time == soon
    woken = _observe(engine, "evening-woken")
    assert {"task_due_soon", "plan_reminder_due"} <= set(woken["wake_reasons"])
    assert engine.context.state.agents[1].day_plan.goals[0].reminded_at == soon
    _act(engine, "evening-call", *_keep_waiting(engine))
    moment = iso_time(soon, engine.context.scenario.timezone)
    arrivals = sorted((event for event in engine.context.store.read_agent_events("environment_event", 1)
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
    assert engine.context.state.current_time == soon - 900
    before = _observe(engine, "clock-before-seen")
    assert not [row for row in _events(before) if row["type"] in {"task_deadline", "plan_reminder"}]
    _wait_until(engine, soon, "clock-soon")
    _wait_until(engine, soon + 1800, "clock-due")
    assert engine.context.state.current_time == soon + 1800
    derived = [(row["type"], row.get("stage") or row.get("goal"), row["occurred_at"])
               for row in _events(_observe(engine, "clock-due-seen")) if row["type"] in {"task_deadline",
                                                                                         "plan_reminder"}]
    zone = engine.context.scenario.timezone
    assert derived == [("task_deadline", "due_soon", iso_time(soon, zone)),
                       ("plan_reminder", "交稿前喝口水", iso_time(soon, zone)),
                       ("task_deadline", "expired", iso_time(soon + 1800, zone))]
    assert all(row["task"] == name for row in _events(_observe(engine, "clock-again"), "task_deadline"))


def _stage_plan(engine, reminded_at: int | None) -> None:
    """预置当日计划：一个已设提醒的目标，reminded_at 为提醒触发时刻（None 为尚未触发）。"""
    now = engine.context.state.current_time
    goal = PlanGoal(intent_kind="休息", goal="午后歇一会", registered_at=now - 60, remind_at=now - 30,
                    reminded_at=reminded_at)
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
        day_end=day_end_seconds(now, engine.context.scenario.timezone), goals=(goal,))}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_plan_staged"})])
    engine.context.state = staged


def test_plan_reminder_appears_once_and_precedes_same_second_events(engine_factory) -> None:
    """目标提醒由提醒写入时刻推导，只写目标文字、只出现一次；同一秒内推导记录排在事件记录之前。"""
    engine = engine_factory()
    now = engine.context.state.current_time + 120
    staged = engine.context.state.model_copy(deep=True)
    staged.current_time = now
    staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": now, "body_settled_at": now},
                                                   deep=True)
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_moment"})])
    engine.context.state = staged
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
    now = engine.context.state.current_time
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


def test_memory_queries_keep_evidence_without_progress_events(engine_factory) -> None:
    """成功与失败的记忆查询只保留模型证据，不派生进度记录。"""
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
    assert progress_calls(observed, "memory_search") == []
    assert not set(event.event_id for event in sources).intersection(observed["progress_event_ids"])
    assert engine.context.store.read_agent_events("model_response", 1)[0].data["response"]["choices"][0]["message"][
        "tool_calls"][0] == call
    assert "喝了咖啡" not in json.dumps(observed["progress_layer"], ensure_ascii=False)



def test_conclude_arguments_omit_relationship_and_preference_memories(engine_factory) -> None:
    """conclude_activity 的当前逐项参数进入进度层时略去 views；看法与偏好只在人设层出现，旧数组形状直接拒绝。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, waited = _act(engine, "memo-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    routine = {"type": "routine", "text": "每天等一会儿", "importance": 0.4, "keywords": ["等待"]}
    views = [{"type": "relationship", "about": "沈昊", "text": "和同事处得不错", "importance": 0.5, "keywords": ["同事"]},
             {"type": "preference", "about": "环境", "text": "喜欢安静", "importance": 0.5, "keywords": ["安静"]}]
    _act(engine, "memo-conclude", "conclude_activity", conclude_request(
        name, summary="等完了", memories=[[routine]], views=[views]))
    seen = _observe(engine, "memo-seen")
    (record,) = progress_calls(seen, "conclude_activity")
    assert record["arguments"] == {"items": [{"activity_name": name, "summary": "等完了", "memories": [routine]}]}
    legacy = {"task": [name], "summary": ["等完了"], "memories": [[
        *({"type": view["type"], "text": view["text"], "importance": view["importance"],
           "keywords": view["keywords"], "subject": view["about"]} for view in views), {**routine, "subject": None}]]}
    with pytest.raises(IntegrityError, match="conclude_activity request shape is not recognised"):
        progress_layer._arguments("conclude_activity", legacy)
    ended, zone, now = engine.context.state.activities[waited.activity_id], engine.context.scenario.timezone, engine.context.state.current_time
    span = f"{clock_phrase(ended.started_at, now, zone)}–{clock_phrase(ended.ended_at, now, zone)}"
    assert _lines(seen, record) == [f"{name}：{span}，提前结束"]


def test_a_rejection_without_a_next_step_writes_null(engine_factory) -> None:
    """即读查询的拒绝保留 null 下一步而不新增进度；真实世界动作的拒绝照常保留可读下一步。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = _observe(engine, "null-step")
    before = engine.context.state.model_dump(), engine.context.store.last_event_seq
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND") as caught:
        engine.read_task_summary(1, RecallTaskSummaryRequest(task_name="不存在的名称"), observed)
    guidance = rejection_guidance(caught.value)
    assert "next_step" in guidance and guidance["next_step"] is None
    assert guidance["fix"] == "Use an exact known task, goal or activity name."
    assert (engine.context.state.model_dump(), engine.context.store.last_event_seq) == before
    assert not progress_calls(_observe(engine, "null-step-seen"), "recall_task_summary")
    _, invalid = _act(engine, "null-step-invalid", "start_activity", start_activity("观看", "不存在的对象"))
    assert invalid.data["next_step"] == "Choose a known object and an offered way."


def _hand_request(engine, action_id: str, self_note: str | None) -> None:
    """追加一对手工请求证据（宿主写入 action_requested.data.self_note）与其结果。"""
    now = engine.context.state.current_time
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
        event_type="model_request", occurred_at=engine.context.state.current_time, agent_id=1, decision_id=decision_id,
        visibility="system", source_kind="model",
        data={"phase": "request", "call_id": f"{decision_id}-1", "attempt": 1, "request": request,
              "response": None, "error": None})])


def test_meta_marks_records_after_the_last_action_request_as_protected_and_writes_lines(engine_factory) -> None:
    """progress_meta 与记录一一对应：上次行动请求之后的新记录受保护，之前的不受保护（与在途订单无关时）；成功的发消息写
    settled 句，修订目标与失败的请求不写。"""
    engine = engine_factory()
    stage_day_plan(engine)
    other = engine.context.scenario.agents[0].contacts[0].salutation
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
    boundary = engine.context.store.last_event_seq
    _act(engine, "after-second", "start_activity", start_activity("等待", "自己"))
    observed = _observe(engine, "after-seen")
    from worldmock.life.tool_layer import LayerContext
    context_ = LayerContext(state=engine.context.state, scenario=engine.context.scenario, rules=engine.context.rules,
                            semantics=engine.context.semantics, agent_id=1, names={"sessions": {}, "activities": {}},
                            bindings=observed["interaction_bindings"], menu=None,
                            lifestyle_items=engine.context.lifestyle_items)
    after = progress_layer.records_after(context_, engine.context.store, boundary)
    assert [record["tool"] for record, _ in after] == ["start_activity"]
    assert after[0][0] == progress_calls(observed)[-1]


def test_submission_lines_say_passed_or_rework_and_other_failures_write_nothing(engine_factory) -> None:
    """提交工作的 settled 句：通过写「HH:MM 提交「<任务>」，通过」，需要返工写「未通过」，其他被拒的提交不写；
    修订目标、回看这类调用即使成功也不写。"""
    engine = engine_factory()
    stage_day_plan(engine)
    from worldmock.life.tool_layer import LayerContext
    context_ = LayerContext(state=engine.context.state, scenario=engine.context.scenario, rules=engine.context.rules,
                            semantics=engine.context.semantics, agent_id=1, names={"sessions": {}, "activities": {}},
                            bindings=[], menu=None, lifestyle_items=engine.context.lifestyle_items)
    moment = iso_time(engine.context.state.current_time, engine.context.scenario.timezone)
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
    """轮次提交时「已读」即全文已呈现（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「直接消息截断」）：本 session 进度层呈现过全文
    的消息提交后记为已读；留在出发前 session 的消息不在路上 session 的进度层，出发前的 session 结束后它以全文进入记忆层，
    记忆层的全文同样算呈现，路上那一轮提交后记为已读。

    人物 1 先发第一条；人物 2 在同一秒看到它（呈现）并出发，同一轮人物 1 先结算、再发第二条，它的送达排在出发结果之前，
    留在出发前的 session；人物 2 出发前的观察看不到第二条，提交后仍未读。
    """
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    friend = f"人物：{engine.context.state.agents[2].name}"
    preview = _observe(engine, "read-preview", 2)
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    revise = ReviseDayPlanRequest(goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None,
                                                       around_time=None),])
    _, first = submit_round(engine, {1: ("send_message", SendMessageActionRequest(recipient_name=friend, text="第一条")),
                                     2: ("revise_day_plan", revise)}, "read-first")
    shown = next(item.data["message_id"] for item in first if item.agent_id == 1)
    _, second = submit_round(engine, {
        1: ("send_message", SendMessageActionRequest(recipient_name=friend, text="第二条")),
        2: ("start_activity", start_activity("步行", office))}, "read-second")
    hidden = next(item.data["message_id"] for item in second if item.agent_id == 1)
    presented = engine.context.store.read_agent_events("observation", 2)[-1].data["presented_message_ids"]
    assert presented == [shown] and shown in engine.context.state.agents[2].read_inbox_ids
    assert hidden in engine.context.state.agents[2].inbox and hidden not in engine.context.state.agents[2].read_inbox_ids
    walking = next(item.activity_id for item in second if item.agent_id == 2)
    report = report_request(activity_name(engine, walking), summary="路上看看消息。")
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")), 2: ("report_status", report)},
                 "read-road")
    road = engine.context.store.read_agent_events("observation", 2)[-1].data
    arrival = next(event.event_id for event in engine.context.store.read_agent_events("environment_event", 2)
                   if event.data.get("message_id") == hidden)
    assert arrival not in road["progress_event_ids"]
    (entry,) = road["memory_layer"]["earlier_today"]
    assert [line.split("：", 1)[1] for line in entry["settled"] if "的消息：" in line] == ["第一条", "第二条"]
    assert road["presented_message_ids"] == [shown, hidden] and hidden in engine.context.state.agents[2].read_inbox_ids


def test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line(engine_factory) -> None:
    """个人事务在本 session 内办成：进度层恰有一条只写名称的 errand_completed 记录，来源是办成事件（进入
    progress_event_ids），事务同时从待办层消失；浓缩用的 settled 句折成「HH:MM 办成「<事务名>」」，跨日时带日期。取消的
    事务只从待办层消失，不留记录。落在开启新 session 的那次提交里的办成归旧 session：同一轮里李凡先当面给陈曦一盒牛奶、
    陈曦随后出发回家，这次提交开启她的路上 session，办成事件在其中却不属于路上 session 的进度层，只能从待办层消失看出
    （docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 ）。"""
    engine = open_world(engine_factory)
    zone = engine.context.scenario.timezone
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("买牛奶", MILK), errand_add("做菜", KIT_NAME)])
    waiting = buy(engine, "buy", also=(WAIT,))
    [completed] = settlement(engine)
    observed = observe(engine, "bought")
    records = [row for row in _events(observed) if row["type"].startswith("errand")]
    record = {"occurred_at": iso_time(completed.occurred_at, zone), "type": "errand_completed", "errand": "买牛奶"}
    assert records == [record] and completed.event_id in observed["progress_event_ids"]
    moment = datetime.fromtimestamp(completed.occurred_at, ZoneInfo(zone)).strftime("%H:%M")
    assert _lines(observed, record) == [f"{moment} 办成「买牛奶」"]
    assert [row["name"] for row in observed["todo_layer"]["errands"]] == ["做菜"]
    later = SimpleNamespace(state=SimpleNamespace(current_time=completed.occurred_at + 86400),
                            scenario=SimpleNamespace(timezone=zone))
    assert record_lines(later, record, [completed]) == [f"1月5日 {moment} 办成「买牛奶」"]
    wake_by_mail(engine, offset=60)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
    wait_name = activity_name(engine, next(row["activity_id"] for row in waiting.data["activities"]))
    changes = [errand_cancel("做菜", "今晚在外面吃"), errand_add("再买一盒", MILK)]
    act(engine, "cancel", conclude_request(wait_name, errands=changes), tool="conclude_activity")
    after = observe(engine, "cancelled")
    assert [row for row in _events(after) if row["type"].startswith("errand")] == [record]
    assert [row["name"] for row in after["todo_layer"]["errands"]] == ["再买一盒"]
    home_target = next(row["object"] for row in after["interaction_bindings"] if row["object_id"] == HOME)
    place(engine, 2, MARKET, {MILK_ID: 1.0})
    ready(engine, 2)
    first(engine, 2)
    walk = routed((home_target, "travel", "步行", None, None))
    _, results = submit_round(engine, {1: ("start_activity", walk), 2: ("start_activity", give(engine, CHEN))}, "leave")
    leaving, handed = sorted(results, key=lambda result: result.agent_id)
    assert (leaving.status, handed.status) == ("in_progress", "completed"), (leaving.error_code, handed.error_code)
    departed = settlement(engine)[-1]
    assert (departed.data["kind"], departed.data["errand"]["name"]) == ("errand_completed", "再买一盒")
    assert departed.caused_by == [result_event(engine, handed, agent_id=2).event_id]
    assert departed.state_version_after == result_event(engine, leaving).state_version_after
    road = observe(engine, "road")
    assert road["progress_layer"]["session_started_at"] == iso_time(departed.occurred_at, zone)
    assert [row for row in _events(road) if row["type"].startswith("errand")] == []
    assert departed.event_id not in road["progress_event_ids"] and road["todo_layer"]["errands"] == []


def _reminded_then_drunk(engine_factory):
    """单人场景 12:30 在家、手边一份纯牛奶：12:31 的收尾里记下「吃掉纯牛奶 1 份」（提醒 12:40）与「取得食材包 1 份」，12:40
    被提醒后结束等待、把奶喝完，第一条事务在同一段停留里办成。返回引擎与刚喝完、还没收尾的那项进食活动。"""
    engine = home(engine_factory, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("喝牛奶", MILK, outcome="吃掉", remind_time="12:40"),
                          errand_add("补食材包", KIT_NAME, memo="周末去超市")])
    waited = act(engine, "wait", routed(WAIT))
    until(engine, local_moment(engine, "2026-01-05 12:40"))
    ended = act(engine, "end", conclude_request(activity_name(engine, waited.activity_id), summary="不等了。"),
                tool="conclude_activity")
    assert ended.status == "completed", (ended.error_code, ended.message)
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    run_until(engine, lambda current: current.context.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    assert [item.name for item in engine.context.state.agents[1].errands.values()] == ["补食材包"]
    return engine, drinking


def _errand_positions(observed: dict) -> dict[str, int]:
    """进度层里两种个人事务记录各自唯一一条的下标（记录类型 → 下标）。"""
    rows = _events(observed)
    found = {row["type"]: index for index, row in enumerate(rows) if row["type"].startswith("errand")}
    assert sorted(found) == ["errand_completed", "errand_reminder"]
    assert len([row for row in rows if row["type"].startswith("errand")]) == 2
    return found


def test_real_errand_records_satisfy_a_progress_schema_that_admits_only_the_errand_name(engine_factory) -> None:
    """进度层 Schema 登记了 errand_reminder 与 errand_completed 两种记录，内容字段恰为 errand（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 、6.4；接口合同「七层 Schema」）。真实请求、引擎与 SQLite 上，被提醒后把奶喝完的那次观察里两条记录各由落库的
    errand_reminded、errand_completed 事件派生、只写事务名，整层通过校验；给任一条多写备注、提醒钟点或事务身份，或去掉
    事务名，校验都以 ValueError 指明进度层与该记录的位置。"""
    engine, _ = _reminded_then_drunk(engine_factory)
    zone = engine.context.scenario.timezone
    observed = observe(engine, "seen")
    found, progress = _errand_positions(observed), observed["progress_layer"]
    sources = {event.data["kind"]: event for event in engine.context.store.read_agent_events("environment_event", 1)
               if event.data.get("kind") in {"errand_reminded", "errand_completed"}}
    records = progress["events"]
    for kind, source in (("errand_reminder", "errand_reminded"), ("errand_completed", "errand_completed")):
        assert records[found[kind]] == {"occurred_at": iso_time(sources[source].occurred_at, zone), "type": kind,
                                        "errand": "喝牛奶"}
        assert sources[source].event_id in observed["progress_event_ids"]
    validate_layer(progress)
    extras = ({"memo": "睡前喝一份"}, {"remind_time": "12:40"}, {"errand_id": sources["errand_completed"].data[
        "errand"]["errand_id"]})
    for kind, position in found.items():
        nameless = {key: value for key, value in records[position].items() if key != "errand"}
        for changed in (*({**records[position], **extra} for extra in extras), nameless):
            broken = [*records[:position], changed, *records[position + 1:]]
            with pytest.raises(ValueError, match=rf"Prompt layer 进度层 violates its schema at \['events', {position}\]"):
                validate_layer({**progress, "events": broken})


def test_errand_records_seen_by_a_later_action_request_fold_under_capacity(engine_factory, monkeypatch) -> None:
    """个人事务的两种记录与其他事件记录同一保护口径（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 ；接口合同「可见期与提示预算」）：
    只在比上次行动请求新时受保护。真实请求、引擎与 SQLite 上，被提醒后把奶喝完，下一次观察里提醒与办成两条记录都受
    保护；再经一次记下行动请求的真实决策（给喝完的活动收尾）之后，两条都不再受保护，settled 句分别为空与「HH:MM 办成
    「喝牛奶」」。此时按预算浓缩：较旧的一段折成一条 condensed，进度层不再有任何 errand 记录，办成折成的那一句在 lines 里
    恰好一次，提醒不留句；只有上次行动请求之后的收尾调用原样保留。"""
    from tests.life.test_session_attribution import _action_request_round

    engine, drinking = _reminded_then_drunk(engine_factory)
    fresh = observe(engine, "fresh")
    assert all(fresh["progress_meta"][index]["protected"] for index in _errand_positions(fresh).values())
    _action_request_round(engine, "close", "conclude_activity", conclude_request(
        activity_name(engine, drinking.activity_id), summary="喝完了。"))
    later = observe(engine, "later")
    found, meta = _errand_positions(later), later["progress_meta"]
    assert [meta[found[kind]]["protected"] for kind in ("errand_reminder", "errand_completed")] == [False, False]
    [completed] = [event for event in settlement(engine) if event.data["kind"] == "errand_completed"]
    clock = datetime.fromtimestamp(completed.occurred_at, ZoneInfo(engine.context.scenario.timezone)).strftime("%H:%M")
    assert (meta[found["errand_reminder"]]["lines"], meta[found["errand_completed"]]["lines"]) == (
        [], [f"{clock} 办成「喝牛奶」"])
    request = {"model": prompt_capacity.MODEL_ALIAS, "max_tokens": 100, "tools": [],
               "messages": [{"role": "system", "content": "系统层"}, {"role": "user", "content": "[]"}]}
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 100 if "condensed" in json.dumps(
        candidate["messages"][1]["content"], ensure_ascii=False) else 900)
    budget = ProgressBudget((later["tool_layer"], later["progress_layer"]), tuple(meta))
    events = json.loads(prompt_capacity.fit_request(request, budget)["messages"][1]["content"])[1]["events"]
    assert [row["type"] for row in events] == ["condensed", "tool_call"]
    assert events[1] == later["progress_layer"]["events"][-1] and events[1]["tool"] == "conclude_activity"
    assert [line for line in events[0]["lines"] if "喝牛奶" in line and "办成" in line] == [f"{clock} 办成「喝牛奶」"]
    assert not [line for line in events[0]["lines"] if "提醒" in line]


def test_a_completion_record_follows_the_calls_of_its_second_by_event_sequence(engine_factory) -> None:
    """errand_reminder 与 errand_completed 是事件记录，同一时刻排在由截止与提醒推导的记录之后、按事件序号排列
    （接口合同「七层字段」进度层；docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 ）。真实请求、引擎与 SQLite 上，人物在集市收尾时
    记下「取得纯牛奶 1 份」，同一秒到店买下：这一秒的记录依次是给出决策机会的邮件、记下事务的收尾、买下的请求、办成，
    办成排在引起它的购买之后；进度来源以办成事件收尾，它的事件序号是全部来源里最大的。"""
    engine = home(engine_factory, {})
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("买牛奶", MILK)])
    assert buy(engine, "buy").status == "completed" and engine.context.state.agents[1].errands == {}
    observed = observe(engine, "bought")
    rows = _events(observed)
    [completed] = [row for row in rows if row["type"] == "errand_completed"]
    same_second = [(row["type"], row.get("tool")) for row in rows if row["occurred_at"] == completed["occurred_at"]]
    assert same_second == [("message_arrival", None), ("tool_call", "conclude_activity"),
                           ("tool_call", "start_activity"), ("errand_completed", None)]
    sequences = {event.event_id: event.event_seq for event in engine.context.store.read_events(engine.context.store.last_event_seq)}
    [event] = settlement(engine)
    assert event.data["kind"] == "errand_completed" and observed["progress_event_ids"][-1] == event.event_id
    assert sequences[event.event_id] == max(sequences[identity] for identity in observed["progress_event_ids"])


def test_the_progress_layer_refuses_a_stored_control_request_without_errands(engine_factory) -> None:
    """进度层经 control_items 读控制请求（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-交付边界 、2.3）：真实播报之后的观察照现行形状写出
    它的参数（每项活动名与自述，不带 errands）；经 FootprintStore 在同一 session 里补登一条带 items 而缺请求级 errands 的
    旧形状播报及其结果后，下一次观察以完整性故障拒绝，不当作没有事务变更的请求放行。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, waited = _act(engine, "legacy-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    _, reported = _act(engine, "legacy-report", "report_status", report_request(name, summary="还在等。"))
    assert reported.status == "completed"
    (record,) = progress_calls(_observe(engine, "legacy-before"), "report_status")
    assert record["arguments"] == {"items": [{"activity_name": name, "summary": "还在等。"}]}
    stage_legacy_control_request(engine, "report_status")
    with pytest.raises(IntegrityError, match="report_status request lacks errands"):
        _observe(engine, "legacy-after")
