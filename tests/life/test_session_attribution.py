"""session 归属、settled 写法的排除项与 answering 可关联的消息（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第三章「session 与记忆条目」
「settled 的写法」、第四章、第五章「代点请求与订单关联」）。

一个 session 的记录区间是（前一边界提交的末事件序号, 本边界提交的末事件序号]，同一提交按 state_version_after 聚合：出发所在
的决策轮提交整体归旧 session，到达所在的时钟推进提交整体归路程 session，进度层下界与记忆层区间同一口径。活动句按活动结束的
提交归属。交谈邀请不写 settled 句；零点计划输入的结束句带暂停区间；截止预警与唤醒同提前量；预算浓缩不折叠在途订单的
记录；answering 只关联进度层的与记忆层未回复的直接消息。
真实 LifeEngine 与 SQLite 路径。
"""

from __future__ import annotations

import json

from agentsociety2.agent.base import ModelCallEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, stage_day_plan, start_activity, submit_round, wake_by_mail,
                                 pause_request, report_request)
from tests.life.test_dayrun_restaurants import _stage
from tests.life.test_memory_layer import (_act, _clock, _contact_message, _mail_line, _observe, _remote_order,
                                          _walk)
from tests.life.test_same_moment_decisions import _contacts
from worldmock.life import prompt_capacity, progress_layer, schedule
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.life.models import ActivityStatus, PlanGoalInput, ReviseDayPlanRequest, SendMessageActionRequest
from worldmock.life.prompt_capacity import ProgressBudget
from worldmock.life.readable import iso_time

_ZONE = "Asia/Shanghai"
_REVISE = ReviseDayPlanRequest(goals=[PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None)])


def _first_result_seq(engine, agent_id: int, action_id: str) -> int:
    """某动作首个 action_result 的事件序号。"""
    return next(event.event_seq for event in engine.context.store.read_agent_events("action_result", agent_id)
                if event.action_id == action_id)


def _meta_lines(observed: dict, record: dict) -> list[str]:
    """一条进度层记录在 progress_meta 中的 settled 句。"""
    return observed["progress_meta"][observed["progress_layer"]["events"].index(record)]["lines"]


def _action_request_round(engine, round_id: str, tool: str, request) -> None:
    """真实宿主路径的一次决策：开轮、观察，记录一次行动请求（无 purpose、thinking 开启、原生 tools 含 start_activity），
    再登记世界请求并结算。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    prompt = {"model": "deepseek-flash", "messages": [{"role": "system", "content": "系统层"}],
              "tools": [{"type": "function", "function": {"name": "start_activity", "parameters": {}}}],
              "extra_body": {"thinking": {"type": "enabled"}}}
    engine.record_model_exchange(ModelCallEvidence(ctx, f"{round_id}-call", "request", 1, prompt))
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool, request))
    engine.commit_decision_round(round_id)


def test_arrival_commit_belongs_wholly_to_the_transit_session(engine_factory) -> None:
    """到达所在的时钟推进提交整体归路程 session：与到达同一提交送达的邮件和在这次提交里结束的出行（「到达」句）写进路程
    条目（同一提交内按事件序号：先结算活动，再送达邮件），不在新停留的进度层；新停留的下界是这次提交的末事件序号，到达之后
    同一秒的收尾调用在新停留的进度层，它的 settled 句不重复更早 session 里结束的出行。"""
    engine = engine_factory()
    stage_day_plan(engine)
    walk = _walk(engine, "arrive-walk")
    trip = engine.context.state.activities[walk.activity_id]
    wake_by_mail(engine, offset=trip.planned_end_at - engine.context.state.current_time)
    while engine.context.state.activities[walk.activity_id].status == ActivityStatus.ACTIVE:
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"arrival-continue-{engine.context.state.version}", "report_status",
                 report_request(activity_name(engine, walk.activity_id), summary="继续走到公司。"))
        advance_next(engine)
    arrived = engine.context.state.current_time
    assert arrived == trip.planned_end_at
    assert engine.context.state.activities[walk.activity_id].status == ActivityStatus.COMPLETED
    (mail,) = [event for event in engine.context.store.read_agent_events("environment_event", 1)
               if event.data.get("kind") == "mail_arrival"]
    assert mail.occurred_at == arrived
    lower = engine.context.store.commit_last_seq(mail.event_seq)
    assert lower >= mail.event_seq
    assert progress_layer.session_bound(engine.context.store, engine.context.state, 1) == (arrived, lower)
    name = activity_name(engine, walk.activity_id)
    _act(engine, "arrive-conclude", "conclude_activity", conclude_request(name))
    observed = _observe(engine, "arrive-seen")
    (conclusion,) = progress_calls(observed)
    assert conclusion["tool"] == "conclude_activity" and _meta_lines(observed, conclusion) == []
    assert mail.event_id not in observed["progress_event_ids"]
    assert [row for row in observed["progress_layer"]["events"] if row["type"] == "message_arrival"] == []
    ended = engine.context.state.activities[walk.activity_id]
    arrival = f"{name}：{_clock(engine, ended.started_at)}–{_clock(engine, arrived)}，到达"
    assert observed["memory_layer"]["earlier_today"] == [{
        "recorded_at": iso_time(arrived, _ZONE), "self_notes": [], "settled": [arrival, _mail_line(engine, arrived)]}]


def test_departure_commit_belongs_wholly_to_the_old_session(engine_factory) -> None:
    """出发所在的决策轮提交整体归旧 session：同一轮排在出发者之后结算的他人消息，送达事件在出发结果之后、仍在同一提交，
    写进出发前那段停留的条目，不在路程 session 的进度层；路程 session 的下界是这次提交的末事件序号。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    leaver, sender = engine.scheduling_state().ready_agent_ids
    preview = _observe(engine, "leave-preview", leaver)
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    message = SendMessageActionRequest(recipient_name=f"人物：{engine.context.state.agents[leaver].name}", text="我也出门了")
    submit_round(engine, {leaver: ("start_activity", start_activity("步行", office)),
                          sender: ("send_message", message)}, "leave")
    departure = _first_result_seq(engine, leaver, f"action-leave-{leaver}")
    (arrival,) = [event for event in engine.context.store.read_agent_events("environment_event", leaver)
                  if event.data.get("kind") == "message_arrival"]
    lower = engine.context.store.commit_last_seq(departure)
    assert departure < arrival.event_seq <= lower
    assert progress_layer.session_bound(engine.context.store, engine.context.state, leaver) == (engine.context.state.current_time, lower)
    road = _observe(engine, "leave-road", leaver)
    assert arrival.event_id not in road["progress_event_ids"] and road["progress_layer"]["events"] == []
    (home,) = road["memory_layer"]["earlier_today"]
    assert home["settled"] == [f"{_clock(engine, arrival.occurred_at)} 收到{engine.context.state.agents[sender].name}"
                               f"的消息：我也出门了"]


def test_answering_binds_progress_messages_and_only_unreplied_memory_messages(engine_factory) -> None:
    """answering 只关联可见的直接消息：进度层 message_arrival 的消息回复过也能关联；那段停留结束后，记忆层里已回复的消息全文
    仍在（记为已呈现），但不能再关联，只有未回复的可以。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    contacts = {item.salutation: item for item in engine.context.scenario.agents[0].contacts}
    replied = _contact_message(engine, contacts["母亲"].contact_id, "到公司了吗")
    open_ = _contact_message(engine, contacts["好友"].contact_id, "周末打球")
    _act(engine, "bind-reply", "send_message", SendMessageActionRequest(recipient_name="联系人：母亲", text="到了"))
    home = _observe(engine, "bind-home")
    assert [row["message_id"] for row in home["message_bindings"]] == [replied, open_]
    _walk(engine, "bind-walk")
    road = _observe(engine, "bind-road")
    assert road["presented_message_ids"] == [replied, open_]
    assert road["message_bindings"] == [{"name": f"好友 {_clock(engine, engine.context.state.current_time)} 的消息",
                                         "message_id": open_, "sender_id": contacts["好友"].contact_id}]


def test_chat_invitation_writes_no_settled_line_for_condensation(engine_factory) -> None:
    """交谈邀请在收件人的进度层带全文，但不是直接消息：progress_meta 里它的 settled 句为空，预算浓缩不会把它写成「收到…的消息」。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    submit_round(engine, {1: ("revise_day_plan", _REVISE),
                          2: ("start_activity", start_activity("交谈", f"人物：{engine.context.state.agents[1].name}"))},
                 "invite")
    observed = _observe(engine, "invite-seen")
    (invite,) = [row for row in observed["progress_layer"]["events"] if row["type"] == "message_arrival"]
    assert invite["kind"] == "chat_invite" and _meta_lines(observed, invite) == []


def test_plan_inputs_keep_resumed_pause_intervals_in_ended_activity_sentences(engine_factory) -> None:
    """零点计划输入的「动作结果」与记忆层同一写法：在回顾时段内结束的活动一句，起止里写已恢复的暂停区间。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, first = _act(engine, "plan-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, first.activity_id)
    paused_at = wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "plan-pause", "pause_activity", pause_request(name, summary="先停一下"))
    _, second = _act(engine, "plan-other", "start_activity", start_activity("等待", "自己"))
    resumed_at = wake_by_mail(engine, offset=180)
    advance_next(engine)
    _act(engine, "plan-other-end", "conclude_activity", conclude_request(activity_name(engine, second.activity_id)))
    _act(engine, "plan-resume", "report_status", report_request(name, summary="接着等"))
    closed = wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "plan-conclude", "conclude_activity", conclude_request(name))
    started = engine.context.state.activities[first.activity_id].started_at
    expected = (f"{name}：{_clock(engine, started)}–{_clock(engine, closed)}"
                f"（暂停 {_clock(engine, paused_at)}–{_clock(engine, resumed_at)}），提前结束")
    assert expected in plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["动作结果"]




def test_open_order_record_stays_uncondensed_while_older_records_fold(engine_factory, monkeypatch) -> None:
    """预算浓缩（第四章）：上次行动请求之前的记录中，仍在途订单的下单记录受保护、不被折叠；它之前的一段未受保护记录先折成
    condensed，它之后的记录不与之前的连成一段。"""
    engine = engine_factory(mutate=_contacts)
    stage_day_plan(engine)
    _stage(engine, "home", 1500)
    _act(engine, "fold-revise", "revise_day_plan", _REVISE)
    order = _remote_order(engine, "fold-order")
    friend = engine.context.scenario.agents[0].contacts[0].salutation
    _act(engine, "fold-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="在路上"))
    _action_request_round(engine, "fold-last", "revise_day_plan", _REVISE)
    observed = _observe(engine, "fold-seen")
    records = observed["progress_layer"]["events"]
    assert [row["tool"] for row in records] == ["revise_day_plan", "start_activity", "send_message", "revise_day_plan"]
    assert engine.context.state.deliveries[order["order_id"]].status == "pending"
    assert [row["protected"] for row in observed["progress_meta"]] == [False, True, False, True]
    layers = (observed["tool_layer"], observed["progress_layer"])
    request = {"model": prompt_capacity.MODEL_ALIAS, "max_tokens": 100, "tools": [],
               "messages": [{"role": "system", "content": "系统层"}, {"role": "user", "content": "[]"}]}
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 100 if "condensed" in json.dumps(
        candidate["messages"][1]["content"], ensure_ascii=False) else 900)
    fitted = prompt_capacity.fit_request(request, ProgressBudget(layers, tuple(observed["progress_meta"])))
    events = json.loads(fitted["messages"][1]["content"])[1]["events"]
    assert [row["type"] for row in events] == ["condensed", "tool_call", "tool_call", "tool_call"]
    assert events[0]["lines"] == [] and events[1:] == records[1:]
