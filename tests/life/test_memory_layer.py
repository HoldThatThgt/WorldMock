"""记忆层：由已提交事件与世界状态派生（docs/dev/SPEC-persona-prompt.md 第一章「记忆层」、第三章「进度与记忆的更新」）。

session 结束（出发、到达、途中结束出行、实验截止）后，它的结果与世界事件写成 settled 句、pause 与 conclude 写下的 summary
与记忆进入 self_notes，今天结束的 session 各一条；更早日期只留仍未了结的订单与直接消息，加上归属该日的就寝反思；零点按
本地日期分区；8000 字符窗口按规定顺序删减；恢复前后逐字节相同。真实 LifeEngine 与 SQLite 路径。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.agent.base import ModelCallEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, stage_day_plan, start_activity, start_request, wake_by_mail,
                                 pause_request, report_request)
from tests.life.test_day_planning import _late_engine
from tests.life.test_dayrun_restaurants import _keep_waiting, _stage
from tests.life.test_progress_events import _model_pair
from tests.life.test_scheduling import _custom_engine
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, memory_layer
from worldmock.life.memory_layer import MEMORY_WINDOW_CHARS, _Entry, _fit, _Line, memory_view, reflection_date
from worldmock.life.interaction_menu import WAYS
from worldmock.life.models import (ActivityKind, ActivityState, ActivityStatus, PlanGoalInput, RecallTaskSummaryRequest,
                                   ReviseDayPlanRequest, SendMessageActionRequest)
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.readable import duration_phrase, iso_time, local_date
from worldmock.life.settled_text import activity_sentence, purchase_sentence
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

_ZONE = "Asia/Shanghai"
#: 观察中由环境构建的五层（人设层由宿主构建）。
_FIVE_LAYERS = ("tool_layer", "memory_layer", "todo_layer", "environment_layer", "progress_layer")
#: 一次行动请求的 wire 形状（没有 purpose、原生 tools 含 start_activity、思考开启），供记下模型证据。
_ACTION_REQUEST = {"model": "deepseek-flash", "messages": [], "extra_body": {"thinking": {"type": "enabled"}},
                   "tools": [{"type": "function", "function": {"name": "start_activity"}}]}


def _act(engine, round_id: str, tool: str, request, agent_id: int = 1):
    """单个就绪人物按真实顺序观察、登记并结算一轮，返回（本轮观察，结果）。"""
    decision = context(engine, round_id, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return observed, engine.commit_decision_round(round_id)[0]


def _replied(engine, round_id: str, tool: str, request, body: str):
    """像真实宿主那样先记下本决策的模型请求与带回复正文的响应，再登记世界请求并结算一轮，返回结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    call_id = f"{decision.decision_id}:model:0001:attempt:1"
    response = {"choices": [{"message": {"content": body, "tool_calls": [
        {"id": "call", "type": "function", "function": {"name": tool, "arguments": "{}"}}]}}]}
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "request", 1, _ACTION_REQUEST))
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "response", 1, _ACTION_REQUEST,
                                                   response=response))
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return engine.commit_decision_round(round_id)[0]


def _observe(engine, round_id: str, agent_id: int = 1) -> dict:
    """读取一次当前观察（只追加观察事件）。"""
    return engine.observe(context(engine, round_id, agent_id))


def _memory(engine, round_id: str, agent_id: int = 1) -> dict:
    """当前观察的记忆层（顺带校验 Schema）。"""
    layer = _observe(engine, round_id, agent_id)["memory_layer"]
    validate_layer(layer)
    return layer


def _memo(kind: str, text: str) -> dict:
    """结束活动时写下的一条普通记忆（没有对象字段）。"""
    return {"type": kind, "text": text, "importance": 0.5, "keywords": [text[:2]]}


def _view(kind: str, text: str, about: str) -> dict:
    """结束活动时写下的一条看法或偏好（relationship 或 preference，about 写对象）。"""
    return {"type": kind, "about": about, "text": text, "importance": 0.5, "keywords": [text[:2]]}


def _walk(engine, round_id: str, destination: str = "office"):
    """步行去某地（出发请求），返回结果。"""
    observed = _observe(engine, f"{round_id}-preview")
    target = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == destination)
    return _act(engine, round_id, "start_activity", start_activity("步行", target))[1]


def _clock(engine, epoch: int) -> str:
    """与观察同一天的 HH:MM。"""
    return iso_time(epoch, _ZONE)[11:16]


def _mail_line(engine, epoch: int) -> str:
    """wake_by_mail 注入的邮件在 settled 中的句子。"""
    return f"{_clock(engine, epoch)} 收到邮件「{engine.scenario.messages[0].subject}」"


def _next_morning(moment: int) -> int:
    """moment 所在本地日期的次日 09:00。"""
    local = datetime.fromtimestamp(moment, ZoneInfo(_ZONE))
    return int((local.replace(hour=9, minute=0, second=0) + timedelta(days=1)).timestamp())


def _short(name: str) -> str:
    """活动名去掉开始时刻后缀「（M月D日 HH:MM 起）」。"""
    return re.sub(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）", "", name)


def _ended_sessions(engine, agent_id: int = 1) -> list:
    """本人已结束的 session，按开始顺序。"""
    return sorted((item for item in engine.state.sessions.values()
                   if item.agent_id == agent_id and item.status == "ended"),
                  key=lambda item: (item.started_at, item.session_id))


def _concluding_wait(engine, round_id: str, summary: str, memories: list, views: tuple = ()) -> str:
    """开始等待、被邮件唤醒后带记忆与看法提前结束它，返回活动名。"""
    _, waited = _act(engine, f"{round_id}-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    name = activity_name(engine, waited.activity_id)
    _act(engine, f"{round_id}-conclude", "conclude_activity",
         conclude_request(name, summary=summary, memories=[memories], views=[list(views)]))
    return name


def test_departure_and_arrival_attribute_same_second_calls_to_the_right_session(engine_factory) -> None:
    """出发：同一秒先收尾再出发，收尾与出发都归旧 session；旧 session 的消息、邮件与结束的等待写成 settled，收尾的 summary
    与记忆写进 self_notes（relationship 记忆不进记忆层，标注去掉开始时刻后缀）。到达：路上收到的邮件与在到达提交里结束的
    出行（「到达」句）归路程 session；到达那一秒的收尾调用归新停留 session（它结束前不在记忆层），结束后只以 summary 出现在
    它的条目里（活动句按结束提交归属，不随收尾）。recorded_at 与 WorldState.sessions 的结束时刻逐一对应。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    start = engine.state.current_time
    _act(engine, "a-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="晚上见"))
    wait = _concluding_wait(engine, "a", "歇好了", [_memo("routine", "每天歇一会儿")],
                            views=(_view("relationship", "沈昊靠谱", friend),))
    departed = engine.state.current_time
    walk = _walk(engine, "a-walk")
    home = _memory(engine, "a-road")["earlier_today"]
    waited = engine.state.activities[next(item.activity_id for item in engine.state.activities.values()
                                          if item.name == wait)]
    assert home == [{
        "recorded_at": iso_time(departed, _ZONE),
        "settled": [f"{_clock(engine, start)} 给{friend}发消息：晚上见", _mail_line(engine, departed),
                    f"{wait}：{_clock(engine, waited.started_at)}–{_clock(engine, waited.ended_at)}，提前结束"],
        "self_notes": [{"activity": "等待", "text": "歇好了"}, {"activity": "等待", "text": "每天歇一会儿"}]}]
    mail_at = wake_by_mail(engine, offset=120)
    advance_next(engine)
    walk_name = activity_name(engine, walk.activity_id)
    _act(engine, "a-report", "report_status", report_request(walk_name, summary="快到了"))
    assert advance_until_ready(engine)
    arrived = engine.state.current_time
    _act(engine, "a-arrive", "conclude_activity", conclude_request(walk_name, summary="到了"))
    after = _memory(engine, "a-arrived")["earlier_today"]
    trip = engine.state.activities[walk.activity_id]
    arrival = f"{walk_name}：{_clock(engine, trip.started_at)}–{_clock(engine, arrived)}，到达"
    assert after == [*home, {"recorded_at": iso_time(arrived, _ZONE), "settled": [_mail_line(engine, mail_at), arrival],
                             "self_notes": []}]
    _walk(engine, "a-back", "home")
    office = _memory(engine, "a-office")["earlier_today"][-1]
    assert office == {"recorded_at": iso_time(engine.state.current_time, _ZONE), "settled": [],
                      "self_notes": [{"activity": _short(walk_name), "text": "到了"}]}
    ended = _ended_sessions(engine)
    assert [row["recorded_at"] for row in _memory(engine, "a-final")["earlier_today"]] == [
        iso_time(item.ended_at, _ZONE) for item in ended]


def test_departure_request_belongs_wholly_to_the_old_session(engine_factory) -> None:
    """出发请求整体归旧 session：同一请求里远程下单与步行出发，下单句进旧停留的条目，路程 session 的进度层没有这次调用。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    observed = _observe(engine, "f-preview")
    supply, label = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                         if row["kind"] == "supply" for action in row["actions"]
                         if action["label"] == "远程下单" and action["note"] is None)
    office = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == "office")
    request = start_request(targets=[supply, office], actions=[WAYS[label][0], WAYS["步行"][0]],
                            ways=[label, "步行"])
    _, started = _act(engine, "f-go", "start_activity", request)
    (order,) = started.data["orders"]
    road = _observe(engine, "f-road")
    assert progress_calls(road) == []
    context_ = engine._layer_context(engine.state.agents[1], (), [])
    assert road["memory_layer"]["earlier_today"] == [{"recorded_at": iso_time(order["created_at"], _ZONE),
                                                      "settled": [purchase_sentence(context_, order, None)],
                                                      "self_notes": []}]


def test_en_route_conclusion_keeps_the_conclusion_in_the_transit_session(engine_factory) -> None:
    """途中结束出行：回到出发地的停留 session 以这次收尾的结果为下界，所以收尾调用归路程 session；路程条目写路上的邮件与
    出行「提前结束」句，summary 进 self_notes；新停留 session 此刻没有记忆条目。"""
    engine = engine_factory()
    stage_day_plan(engine)
    walk = _walk(engine, "b-walk")
    mail_at = wake_by_mail(engine, offset=120)
    advance_next(engine)
    name = activity_name(engine, walk.activity_id)
    _act(engine, "b-stop", "conclude_activity", conclude_request(name, summary="不去了"))
    trip = engine.state.activities[walk.activity_id]
    (entry,) = _memory(engine, "b-seen")["earlier_today"]
    assert entry == {"recorded_at": iso_time(mail_at, _ZONE),
                     "settled": [_mail_line(engine, mail_at),
                                 f"{name}：{_clock(engine, trip.started_at)}–{_clock(engine, trip.ended_at)}，提前结束"],
                     "self_notes": [{"activity": _short(name), "text": "不去了"}]}
    assert _ended_sessions(engine)[-1].ended_at == mail_at


def test_denied_arrival_ends_the_trip_and_the_failed_trip_is_concluded_in_the_new_stay(engine_factory,
                                                                                    monkeypatch) -> None:
    """到达被拒也结束路程 session：出行在到达提交里失败，「失败」句归路程 session（到达后即出现在记忆层）；人已在目的地
    开始停留，失败的出行在新停留里收尾，那段停留结束后只写它的 summary。"""
    engine = engine_factory()
    stage_day_plan(engine)
    walk = _walk(engine, "g-walk")
    guarded = tuple(item.model_copy(update={"required_qualifications": ("门禁卡",)})
                    if item.location_id == "office" else item for item in engine.scenario.locations)
    monkeypatch.setattr(engine, "scenario", engine.scenario.model_copy(update={"locations": guarded}))
    assert advance_until_ready(engine)
    name = activity_name(engine, walk.activity_id)
    trip = engine.state.activities[walk.activity_id]
    assert trip.status == ActivityStatus.FAILED
    _act(engine, "g-conclude", "conclude_activity", conclude_request(name, summary="进不去"))
    road = {"recorded_at": iso_time(trip.ended_at, _ZONE), "self_notes": [],
            "settled": [f"{name}：{_clock(engine, trip.started_at)}–{_clock(engine, trip.ended_at)}，失败"]}
    assert _memory(engine, "g-denied")["earlier_today"] == [road]
    _walk(engine, "g-home", "home")
    (_, entry) = _memory(engine, "g-left")["earlier_today"]
    assert entry["settled"] == [] and entry["self_notes"] == [{"activity": _short(name), "text": "进不去"}]


def test_unfinished_browse_writes_no_purchase_sentence(engine_factory) -> None:
    """购买句只由订单生成：提前结束的浏览只留活动句（「提前结束」），summary 写了「挑好了、买了」也不推断出购买；
    settled 恰好是路上唤醒的邮件与这一句，没有任何下单、付款或买到的句子。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = _observe(engine, "h-preview")
    catalog = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "catalog")
    _, browsed = _act(engine, "h-browse", "start_activity", start_activity("浏览", catalog))
    assert browsed.status == "in_progress"
    mail_at = wake_by_mail(engine, offset=60)
    advance_next(engine)
    name = activity_name(engine, browsed.activity_id)
    _act(engine, "h-stop", "conclude_activity", conclude_request(name, summary="番茄和上海青挑好了，买了。"))
    _walk(engine, "h-walk")
    (entry,) = _memory(engine, "h-road")["earlier_today"]
    ended = engine.state.activities[browsed.activity_id]
    assert entry["settled"] == [_mail_line(engine, mail_at),
                                f"{name}：{_clock(engine, ended.started_at)}–{_clock(engine, ended.ended_at)}，提前结束"]
    assert entry["self_notes"] == [{"activity": _short(name), "text": "番茄和上海青挑好了，买了。"}]


def test_experiment_end_closes_the_last_session_into_the_memory_layer(tmp_path) -> None:
    """实验截止时所有 session 结束：最后一段停留以截止时刻为 recorded_at 进入记忆层；按计划结束的等待与被实验截止截断、
    没有收尾的睡眠都在这段停留里结束，各写一句（活动句按结束提交归属，不看是否收尾）。

    窗口取 22:10–22:40：人物在这段时间里困意一直是「困」（22:06:41 刚越过 0.93 档界，docs/dev/SPEC-dayrun-fidelity.md
    1.4），等待不会被体感跨档打断。"""
    def window(document: dict) -> None:
        document.update(start_at="2026-01-05T22:10:00+08:00", end_at="2026-01-05T22:40:00+08:00", tasks=[],
                        messages=[], bills=[], payrolls=[])

    engine = _custom_engine("white-collar-12-weeks.json", window, tmp_path, run_id="memory-end")
    try:
        stage_day_plan(engine, submitted=True)
        friend = engine.scenario.agents[0].contacts[0].salutation
        _act(engine, "c-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}",
                                                                       text="我先睡了"))
        _act(engine, "c-wait", "start_activity", start_activity("等待", "自己"))
        assert advance_until_ready(engine)
        _act(engine, "c-conclude", *_keep_waiting(engine))
        _, slept = _act(engine, "c-sleep", "start_activity", start_activity("睡觉", "自己"))
        assert not advance_until_ready(engine)
        assert engine.state.current_time == engine.state.end_time
        assert engine.state.activities[slept.activity_id].status == ActivityStatus.TRUNCATED
        view = memory_view(engine._layer_context(engine.state.agents[1], (), []), engine.store)
        assert view.layer["earlier_today"] == [{
            "recorded_at": "2026-01-05T22:40:00+08:00",
            "settled": [f"22:10 给{friend}发消息：我先睡了", "等待（1月5日 22:10 起）：22:10–22:25，按计划结束",
                        f"{activity_name(engine, slept.activity_id)}：22:25–22:40，提前结束"],
            "self_notes": [{"activity": "等待", "text": "收尾。"}]}]
        assert [item.ended_at for item in _ended_sessions(engine)] == [engine.state.end_time]
    finally:
        engine.close()


def test_failures_replays_recalls_queries_and_empty_sessions_leave_no_entry(engine_factory) -> None:
    """不写：被拒的请求（开始活动、发给不存在的人的消息、点错活动的收尾所写的 summary 与记忆）、重放的同一请求、回看、
    修订计划与记忆查询；只走了一段路的路程 session 只有到达那一句（出行在到达提交里结束）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    rejected = _act(engine, "d-reject", "start_activity", start_activity("观看", "不存在的对象"))[1]
    assert rejected.status == "rejected"
    refused = _act(engine, "d-refused", "send_message",
                   SendMessageActionRequest(recipient_name="联系人：不认识的人", text="这条发不出去"))[1]
    assert (refused.status, refused.error_code) == ("rejected", "RECIPIENT_NOT_FOUND")
    request = SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="只说一次")
    sent_at = engine.state.current_time
    _act(engine, "d-send", "send_message", request)
    replay = context(engine, "d-replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(replay, "action-d-send", "send_message", request))
    engine.commit_decision_round("d-replay")
    _, waited = _act(engine, "d-wait", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    wait = activity_name(engine, waited.activity_id)
    missed = _act(engine, "d-missed", "conclude_activity", conclude_request(
        "不存在的活动", summary="收错了", memories=[[_memo("routine", "这条记忆不该出现")]]))[1]
    assert missed.status == "rejected"
    _act(engine, "d-conclude", "conclude_activity", conclude_request(wait, summary="等完了"))
    assert _act(engine, "d-recall", "recall_task_summary", RecallTaskSummaryRequest(task_name=wait))[1].status \
        == "completed"
    _act(engine, "d-revise", "revise_day_plan", ReviseDayPlanRequest(goals=(
        PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None, around_time=None),)))
    call = {"id": "call-memory", "type": "function",
            "function": {"name": "memory_search", "arguments": json.dumps({"query": "x"})}}
    _model_pair(engine, "decision-memory", call, json.dumps({"epistemic_status": "subjective", "episodes": []}))
    walk = _walk(engine, "d-walk")
    (entry,) = _memory(engine, "d-road")["earlier_today"]
    ended = engine.state.activities[waited.activity_id]
    wait_line = (f"{wait}：{_clock(engine, ended.started_at)}–{_clock(engine, ended.ended_at)}，"
                 f"{activity_control.ending_phrase(ended)}")
    assert entry["settled"] == [f"{_clock(engine, sent_at)} 给{friend}发消息：只说一次", wait_line]
    assert entry["self_notes"] == [{"activity": "等待", "text": "等完了"}]
    assert advance_until_ready(engine)
    walk_name = activity_name(engine, walk.activity_id)
    _act(engine, "d-arrive", "conclude_activity", conclude_request(walk_name))
    today = _memory(engine, "d-arrived")["earlier_today"]
    trip = engine.state.activities[walk.activity_id]
    assert today == [entry, {"recorded_at": iso_time(trip.ended_at, _ZONE), "self_notes": [], "settled": [
        f"{walk_name}：{_clock(engine, trip.started_at)}–{_clock(engine, trip.ended_at)}，到达"]}]
    assert len(_ended_sessions(engine)) == 2


def _replied(engine, round_id: str, tool: str, request, reply: str):
    """一次带回复正文的决策：真实登记模型请求与响应（响应正文为 reply、带一个 tool 调用），再提交世界请求并结算，
    返回结果（宿主据此把正文写成 action_requested.data.self_note）。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    wire = {"model": "deepseek-flash", "messages": [], "tools": [{"type": "function", "function": {"name": tool}}],
            "extra_body": {"thinking": {"type": "enabled"}}}
    response = {"choices": [{"message": {"content": reply, "tool_calls": [
        {"id": f"call-{round_id}", "type": "function", "function": {"name": tool, "arguments": "{}"}}]}}]}
    call_id = f"{decision.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "request", 1, wire))
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "response", 1, wire, response=response))
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return engine.commit_decision_round(round_id)[0]


def test_reply_bodies_stay_in_their_session_and_never_reach_the_memory_layer(engine_factory) -> None:
    """回复正文只到本 session 结束：发消息与收尾两次决策的回复正文写成各自调用的 self_note，出现在本段停留的进度层；
    出发结束这段停留后，记忆层条目的 settled 只有消息、邮件与结束活动的句子，self_notes 只有收尾写下的 summary，
    两段回复正文都不出现。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    sent_at = engine.state.current_time
    message = SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="晚上见")
    assert _replied(engine, "e-send", "send_message", message, "先跟他打个招呼。").status == "completed"
    _, waited = _act(engine, "e-wait", "start_activity", start_activity("等待", "自己"))
    mail_at = wake_by_mail(engine, offset=300)
    advance_next(engine)
    wait = activity_name(engine, waited.activity_id)
    concluded = _replied(engine, "e-conclude", "conclude_activity", conclude_request(wait, summary="歇好了"),
                         "歇够了，该出门了。")
    assert concluded.status == "completed"
    calls = progress_calls(_observe(engine, "e-seen"))
    assert [(row["tool"], row.get("self_note")) for row in calls] == [
        ("send_message", "先跟他打个招呼。"), ("start_activity", None), ("conclude_activity", "歇够了，该出门了。")]
    departed = engine.state.current_time
    _walk(engine, "e-walk")
    layer = _memory(engine, "e-road")
    ended = engine.state.activities[waited.activity_id]
    assert layer["earlier_today"] == [{
        "recorded_at": iso_time(departed, _ZONE),
        "settled": [f"{_clock(engine, sent_at)} 给{friend}发消息：晚上见", _mail_line(engine, mail_at),
                    f"{wait}：{_clock(engine, ended.started_at)}–{_clock(engine, ended.ended_at)}，提前结束"],
        "self_notes": [{"activity": "等待", "text": "歇好了"}]}]


def _commit(engine, staged, kind: str) -> None:
    """持久提交一份改写过的世界（版本加一，带一条系统事件）。"""
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system", data={"kind": kind})])
    engine.state = staged


def _jump(engine, moment: int) -> None:
    """把世界直接置于 moment（人物都没有进行中的活动），丢弃此前到期的预排事件，人物在 moment 就绪。"""
    staged = engine.state.model_copy(deep=True)
    staged.current_time = moment
    staged.pending_events = [item for item in staged.pending_events if item.due_at > moment]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(update={"next_decision_at": moment, "body_settled_at": moment},
                                                   deep=True)
    _commit(engine, staged, "test_jump")


def _schedule(engine, event: ScheduledEvent, due_at: int | None) -> None:
    """把一条预排事件从世界中取下（due_at 为 None）或以新的到期时刻放回。"""
    staged = engine.state.model_copy(deep=True)
    staged.pending_events = [item for item in staged.pending_events if item.scheduled_id != event.scheduled_id]
    if due_at is not None:
        sequence = max((item.sequence for item in staged.pending_events), default=0) + 1
        staged.pending_events = sorted([*staged.pending_events, event.model_copy(update={
            "due_at": due_at, "sequence": sequence})], key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    _commit(engine, staged, "test_reschedule")


def _remote_order(engine, round_id: str):
    """在菜市场远程下单一份（送到这里），返回订单。"""
    observed = _observe(engine, f"{round_id}-preview")
    target, label = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                         if row["kind"] == "supply" for action in row["actions"]
                         if action["label"] == "远程下单" and action["note"] is None)
    return _act(engine, round_id, "start_activity", start_activity(label, target))[1].data["orders"][0]


def test_unresolved_order_crosses_midnight_into_earlier_days_and_disappears_when_delivered(engine_factory) -> None:
    """零点按本地日期分区：昨天结束的 session 只留仍未了结的订单，按下单日期归入 earlier_days，句子带「M月D日」；订单
    送达（在当前 session）后它从记忆层消失。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    order = _remote_order(engine, "e-order")
    delivery = next(item for item in engine.state.pending_events if item.kind == ScheduledKind.DELIVERY_ARRIVAL)
    _schedule(engine, delivery, None)
    walk = _walk(engine, "e-walk")
    placed = engine.state.current_time
    assert advance_until_ready(engine)
    _act(engine, "e-arrive", "conclude_activity", conclude_request(activity_name(engine, walk.activity_id)))
    context_ = engine._layer_context(engine.state.agents[1], (), [])
    line = purchase_sentence(context_, order, None)
    home, road = _memory(engine, "e-today")["earlier_today"]
    assert home["settled"] == [line] and road["settled"][0].endswith("，到达")
    next_day = _next_morning(placed)
    _jump(engine, next_day)
    stage_day_plan(engine, submitted=True)
    day = local_date(placed, _ZONE)
    later = _memory(engine, "e-next-day")
    carried = purchase_sentence(engine._layer_context(engine.state.agents[1], (), []), order, None)
    assert carried.startswith(f"{int(day[5:7])}月{int(day[8:])}日 ")
    assert later == {"layer": "记忆层", "earlier_days": [{"date": day, "settled": [carried], "self_notes": []}],
                     "earlier_today": []}
    _schedule(engine, delivery, engine.state.current_time + 60)
    _act(engine, "e-wait", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    assert engine.state.deliveries[order["order_id"]].status == "delivered"
    assert _memory(engine, "e-delivered") == {"layer": "记忆层", "earlier_days": [], "earlier_today": []}


def _contact_message(engine, contact_id: str, text: str) -> str:
    """场景联系人发来一条直接消息（写进收件箱并留 message_arrival 事件），返回消息身份。"""
    from worldmock.life.social import Message

    staged = engine.state.model_copy(deep=True)
    message = Message(message_id=f"message-{contact_id}-{staged.version}", kind="direct", sender_id=contact_id,
                      recipient_id="agent:1", text=text, created_at=staged.current_time)
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={"inbox": {**agent.inbox, message.message_id: message}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, agent_id=1, source_kind="environment",
        data={"kind": "message_arrival", "message_id": message.message_id, "recipient_id": "agent:1",
              "occurred_at": staged.current_time})])
    engine.state = staged
    return message.message_id


def test_new_work_is_a_world_event_sentence(engine_factory) -> None:
    """世界事件各一句：新工作到来写「HH:MM 收到新工作「<任务>」」，session 结束后进入记忆层。"""
    engine = engine_factory()
    stage_day_plan(engine)
    task = next(item for item in engine.scenario.tasks
                if item.task_id not in engine.state.agents[1].visible_task_ids)
    _act(engine, "w-wait", "start_activity", start_activity("等待", "自己"))
    staged = engine.state.model_copy(deep=True)
    due = staged.current_time + 240
    sequence = max((item.sequence for item in staged.pending_events), default=0) + 1
    staged.pending_events = sorted([*staged.pending_events, ScheduledEvent(
        scheduled_id="test-work-arrival", kind=ScheduledKind.WORK_AVAILABLE, due_at=due, sequence=sequence,
        agent_id=1, data={"task_id": task.task_id})], key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    _commit(engine, staged, "test_work_scheduled")
    advance_next(engine)
    assert engine.state.current_time == due and task.task_id in engine.state.agents[1].visible_task_ids
    waiting = [item for item in engine.state.activities.values() if item.agent_id == 1 and item.concluded_at is None]
    _act(engine, "w-conclude", "conclude_activity", conclude_request(*(activity_name(engine, item.activity_id)
                                                                      for item in waiting)))
    _walk(engine, "w-walk")
    (entry,) = _memory(engine, "w-road")["earlier_today"]
    assert entry["settled"][0] == f"{_clock(engine, due)} 收到新工作「{task.title}」"


def test_chat_invitation_is_not_carried_into_the_memory_layer(engine_factory) -> None:
    """交谈邀请只在收到它的那段停留的进度层出现；那段停留结束后不写成 settled 句（它不是直接消息，也没有全文可读）。"""
    from tests.life.conftest import submit_round

    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    friend = engine.state.agents[1].name
    revise = ReviseDayPlanRequest(goals=(PlanGoalInput(intent_kind="休息", goal="歇一歇", remind_time=None,
                                                       around_time=None),))
    submit_round(engine, {1: ("revise_day_plan", revise),
                          2: ("start_activity", start_activity("交谈", f"人物：{friend}"))}, "i-invite")
    home = _observe(engine, "i-home")
    assert [row["kind"] for row in home["progress_layer"]["events"] if row["type"] == "message_arrival"] == [
        "chat_invite"]
    _walk(engine, "i-walk")
    road = _observe(engine, "i-road")
    assert road["memory_layer"]["earlier_today"] == [] and road["presented_message_ids"] == []


def test_unanswered_message_crosses_midnight_counts_as_presented_and_a_reply_resolves_it(engine_factory) -> None:
    """昨天结束的 session 里没有回复的直接消息按收到日期留在 earlier_days（全文、带「M月D日」），全文在记忆层即算呈现；
    同一 session 里已了结的发消息句跨零点后消失；通话不算回复，给发件人 send_message 成功后它从记忆层消失。"""
    engine = engine_factory()
    stage_day_plan(engine)
    mother = next(item for item in engine.scenario.agents[0].contacts if item.salutation == "陈母")
    friend = next(item for item in engine.scenario.agents[0].contacts if item.salutation == "沈昊")
    received_at = engine.state.current_time
    message_id = _contact_message(engine, mother.contact_id, "周末回家吃饭吗")
    _act(engine, "m-send", "send_message", SendMessageActionRequest(recipient_name="联系人：沈昊", text="明早见"))
    walk = _walk(engine, "m-walk")
    assert advance_until_ready(engine)
    _act(engine, "m-arrive", "conclude_activity", conclude_request(activity_name(engine, walk.activity_id)))
    moment = engine.state.current_time
    _jump(engine, _next_morning(moment))
    stage_day_plan(engine, submitted=True)
    day = local_date(received_at, _ZONE)
    line = f"{int(day[5:7])}月{int(day[8:])}日 {_clock(engine, received_at)} 收到陈母的消息：周末回家吃饭吗"
    unresolved = {"layer": "记忆层", "earlier_days": [{"date": day, "settled": [line], "self_notes": []}],
                  "earlier_today": []}
    observed = _observe(engine, "m-next-day")
    assert observed["memory_layer"] == unresolved and friend.salutation not in json.dumps(unresolved)
    assert observed["presented_message_ids"] == [message_id]
    observed, called = _act(engine, "m-call", "start_activity", start_activity("通话", "联系人：陈母"))
    assert called.status == "in_progress" and _memory(engine, "m-calling") == unresolved
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    _act(engine, "m-hang-up", "conclude_activity", conclude_request(activity_name(engine, called.activity_id)))
    _act(engine, "m-reply", "send_message", SendMessageActionRequest(recipient_name="联系人：陈母", text="好的"))
    assert _memory(engine, "m-replied") == {"layer": "记忆层", "earlier_days": [], "earlier_today": []}


def test_sentences_write_portions_money_and_cross_day_clocks(engine_factory) -> None:
    """句式：份数至多两位小数、金额按元（至多两位小数）、跨日加「M月D日」；结束活动的起止写暂停区间，吃掉的份数写小数。"""
    engine = engine_factory()
    context_ = engine._layer_context(engine.state.agents[1], (), [])
    now = engine.state.current_time
    product = engine.scenario.products[0]
    place = next(item for item in engine.scenario.locations if item.location_id == "office")
    order = {"created_at": now - 86400 + 600, "fulfillment": "delivery", "product_id": product.product_id,
             "quantity": 1, "total_price_fen": 3250, "destination_id": "office", "expected_at": now - 86400 + 1500,
             "recipient_id": "agent:1", "request_message_id": None}
    day = local_date(now - 86400, _ZONE)
    prefix = f"{int(day[5:7])}月{int(day[8:])}日 "
    assert purchase_sentence(context_, order, None) == (
        f"{prefix}{_clock(engine, order['created_at'])} 远程下单{product.name} 1 份，付 32.5 元，"
        f"预计 {prefix}{_clock(engine, order['expected_at'])} 送到{place.name}")
    meal = ActivityState(activity_id="meal", agent_id=1, location_id="home", kind=ActivityKind.CONSUME,
                         status=ActivityStatus.CANCELLED, started_at=now, ended_at=now + 1800,
                         planned_end_at=now + 3600, last_settled_at=now + 1800, elapsed_seconds=1500,
                         planned_duration_seconds=3600, origin_action_id="a", origin_decision_id="d",
                         origin_request_event_id="e", boundary_type="cancelled", name="吃饭（1月5日 00:00 起）",
                         data={"request": {"use_mode": "eat"}, "consumed": {product.product_id: 1 / 3},
                               "opened": {product.product_id: 1.0}, "discarded": {product.product_id: 2 / 3}})
    assert activity_sentence(context_, meal, [(now + 600, now + 900)]) == (
        f"吃饭（1月5日 00:00 起）：{_clock(engine, now)}–{_clock(engine, now + 1800)}"
        f"（暂停 {_clock(engine, now + 600)}–{_clock(engine, now + 900)}），提前结束，吃了 0.33 份，丢弃 0.67 份")


def test_paused_interval_appears_in_the_concluded_activity_sentence(engine_factory) -> None:
    """暂停与恢复不写句子；结束的活动句在起止里写已恢复的暂停区间，进度层浓缩用的 lines 与记忆层同一写法；条目内两项活动
    去掉开始时刻后缀会重名时，self_notes 保留原名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, first = _act(engine, "p-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, first.activity_id)
    paused_at = wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "p-pause", "pause_activity", pause_request(name, summary="先停一下"))
    _, second = _act(engine, "p-other", "start_activity", start_activity("等待", "自己"))
    other = activity_name(engine, second.activity_id)
    resumed_at = wake_by_mail(engine, offset=180)
    advance_next(engine)
    _act(engine, "p-other-end", "conclude_activity", conclude_request(other))
    _act(engine, "p-resume", "report_status", report_request(name, summary="接着等"))
    closed = wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "p-conclude", "conclude_activity", conclude_request(name, summary="等完了"))
    seen = _observe(engine, "p-seen")
    record = progress_calls(seen, "conclude_activity")[-1]
    ended = engine.state.activities[first.activity_id]
    expected = (f"{name}：{_clock(engine, ended.started_at)}–{_clock(engine, closed)}"
                f"（暂停 {_clock(engine, paused_at)}–{_clock(engine, resumed_at)}），提前结束")
    assert seen["progress_meta"][seen["progress_layer"]["events"].index(record)]["lines"] == [expected]
    _walk(engine, "p-walk")
    (entry,) = _memory(engine, "p-road")["earlier_today"]
    assert expected in entry["settled"] and len([line for line in entry["settled"] if "暂停" in line]) == 1
    assert entry["self_notes"] == [{"activity": name, "text": "先停一下"}, {"activity": other, "text": "收尾。"},
                                   {"activity": name, "text": "等完了"}]


def test_reflection_date_is_the_local_date_six_hours_before_sleep_start() -> None:
    """反思归属日期：23:00 入睡归当日，次日 01:00 入睡归前一日，14:00 小睡归当日（不按醒来日期减一天）。"""
    def moment(text: str) -> int:
        """ISO 时刻的 epoch 秒。"""
        return int(datetime.fromisoformat(text).timestamp())

    assert reflection_date(moment("2026-01-05T23:00:00+08:00"), _ZONE) == "2026-01-05"
    assert reflection_date(moment("2026-01-06T01:00:00+08:00"), _ZONE) == "2026-01-05"
    assert reflection_date(moment("2026-01-05T14:00:00+08:00"), _ZONE) == "2026-01-05"
    assert reflection_date(moment("2026-01-06T05:59:00+08:00"), _ZONE) == "2026-01-05"
    assert reflection_date(moment("2026-01-06T06:00:00+08:00"), _ZONE) == "2026-01-06"


def _reflect(engine, round_id: str, text: str):
    """醒来那一轮：观察带 wake_from_sleep，登记一条来源为 reflection 的主观快照，再给睡眠收尾。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert "wake_from_sleep" in observed["wake_reasons"]
    engine.record_memory_update(SimpleNamespace(
        context=decision, operation="append_episodes", created_episode_ids=(f"{round_id}-r",), merged_episode_ids=(),
        episodes=({"id": f"{round_id}-r", "type": "reflection", "text": text, "epistemic_status": "subjective",
                   "source": "reflection"},),
        memory_before_sha256="a" * 64, memory_sha256="b" * 64, episodes_sha256="c" * 64, state_sha256="d" * 64,
        consolidated=True, observation_event_id=observed["observation_event_id"],
        verified_event_ids=tuple(row["source_event_id"] for row in observed["verified_experiences"])))
    sleep = next(row["name"] for row in observed["activity_bindings"] if row["phase"] == "刚结束")
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "conclude_activity", conclude_request(sleep)))
    engine.commit_decision_round(round_id)


def _sleep_until_awake(engine, round_id: str) -> None:
    """开始睡觉并推进到睡够醒来（零点计划照常生成）。"""
    _act(engine, round_id, "start_activity", start_activity("睡觉", "自己"))
    assert advance_until_ready(engine, limit=256)


@pytest.mark.parametrize("bedtime", ["2026-01-05T23:00:00+08:00", "2026-01-06T01:00:00+08:00"])
def test_bedtime_reflection_lands_on_the_date_of_the_sleep_it_follows(tmp_path, bedtime) -> None:
    """就寝反思由醒来那一轮登记（快照带来源），归属日期由刚结束的睡眠开始时刻推出：23:00 与次日 01:00 入睡、1月6日
    醒来时反思都进 earlier_days 的 1月5日；它只进 self_notes（activity 为 null），与当日 session 条目分开。"""
    engine = _late_engine(tmp_path, bedtime, "memory-reflect-late")
    try:
        stage_day_plan(engine, submitted=True)
        _sleep_until_awake(engine, "r-sleep")
        _reflect(engine, "r-wake", "昨晚睡得太晚了")
        assert local_date(engine.state.current_time, _ZONE) == "2026-01-06"
        layer = _memory(engine, "r-seen")
        assert layer["earlier_days"] == [{"date": "2026-01-05", "settled": [],
                                          "self_notes": [{"activity": None, "text": "昨晚睡得太晚了"}]}]
        (event,) = engine.store.read_agent_events("memory_updated", 1)
        assert event.data["trigger"] == ["reflection"] and event.data["episodes"][0]["source"] == "reflection"
    finally:
        engine.close()


def test_nap_reflection_stays_out_until_the_next_day(tmp_path) -> None:
    """14:00 的小睡：反思归当日，当天不在 earlier_days（只列更早日期），跨零点后出现在该日。"""
    engine = _late_engine(tmp_path, "2026-01-05T14:00:00+08:00", "memory-reflect-nap")
    try:
        stage_day_plan(engine, submitted=True)
        _sleep_until_awake(engine, "n-sleep")
        assert local_date(engine.state.current_time, _ZONE) == "2026-01-05"
        _reflect(engine, "n-wake", "午觉睡久了")
        assert _memory(engine, "n-today")["earlier_days"] == []
        moment = engine.state.current_time
        _jump(engine, _next_morning(moment))
        assert _memory(engine, "n-next-day")["earlier_days"] == [
            {"date": "2026-01-05", "settled": [], "self_notes": [{"activity": None, "text": "午觉睡久了"}]}]
    finally:
        engine.close()


def _entry(head: dict, lines: list[tuple[str, bool]], notes: int, exempt: bool = False) -> _Entry:
    """窗口测试的一条条目：lines 为（文字，是否未了结），notes 为自述条数（每条 900 字）。"""
    return _Entry(head=head, lines=[_Line(text, (index, index), open=flag) for index, (text, flag) in
                                    enumerate(lines)],
                  notes=[{"activity": None, "text": f"{index}" + "想" * 900} for index in range(notes)], exempt=exempt)


def test_window_deletes_oldest_reflections_then_shrinks_oldest_today_entries_and_keeps_exemptions(caplog) -> None:
    """8000 字符窗口：先删最旧日期的反思，再删次旧日期的，再把最旧的今日条目缩成只剩未了结句；未了结句与最近一个 session
    的条目永不删去；仍超出时写英文日志。"""
    big = "事" * 1500
    days = [_entry({"date": "2026-01-03"}, [(f"未了结{big}", True)], 2),
            _entry({"date": "2026-01-04"}, [], 2)]
    today = [_entry({"recorded_at": "2026-01-05T09:00:00+08:00"}, [(big, False), (f"未了结订单{big}", True)], 1),
             _entry({"recorded_at": "2026-01-05T10:00:00+08:00"}, [(big, False)], 1, exempt=True)]
    layer = _fit(days, today)
    assert layer["earlier_days"] == [{"date": "2026-01-03", "settled": [f"未了结{big}"], "self_notes": []}]
    assert layer["earlier_today"][0]["settled"] == [f"未了结订单{big}"] and layer["earlier_today"][0]["self_notes"] == []
    assert layer["earlier_today"][1]["settled"] == [big] and len(layer["earlier_today"][1]["self_notes"]) == 1
    assert len(json.dumps(layer, ensure_ascii=False, separators=(",", ":"))) <= MEMORY_WINDOW_CHARS
    small = [_entry({"date": "2026-01-03"}, [], 1), _entry({"date": "2026-01-04"}, [], 1)]
    kept = _fit(small, [_entry({"recorded_at": "2026-01-05T09:00:00+08:00"}, [(big, False)], 1)])
    assert [len(entry["self_notes"]) for entry in kept["earlier_days"]] == [1, 1]
    stubborn = [_entry({"recorded_at": "2026-01-05T09:00:00+08:00"}, [("欠" * 9000, True)], 0),
                _entry({"recorded_at": "2026-01-05T10:00:00+08:00"}, [], 0, exempt=True)]
    with caplog.at_level(logging.WARNING):
        over = _fit([], stubborn)
    assert over["earlier_today"][0]["settled"] == ["欠" * 9000]
    assert "exceeds its 8000-character window" in caplog.text


def test_memory_layer_is_byte_identical_after_restore_and_the_cache_only_accelerates(engine_factory, tmp_path) -> None:
    """恢复前后逐字节相同：父运行（缓存已热）与从共同检查点恢复的分支（新存储，首次全量读取）在同一时刻派生同一份记忆
    层与看法偏好（含按增量读取的活动结束事件）；清掉缓存重算也相同；条目与 WorldState.sessions 中的已结束 session
    逐一对应（路程有到达句）。"""
    from pathlib import Path

    from worldmock.life.engine import LifeEngine

    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    _act(engine, "r-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="出门了"))
    _concluding_wait(engine, "r", "歇好了", [], views=(_view("preference", "喜欢安静", "环境"),))
    walk = _walk(engine, "r-walk")
    assert advance_until_ready(engine)
    _act(engine, "r-arrive", "conclude_activity", conclude_request(activity_name(engine, walk.activity_id)))
    before = _observe(engine, "r-before")
    rendered = json.dumps([before["memory_layer"], before["persona_views"]], ensure_ascii=False)
    snapshots = {f"agent:{agent_id}": engine.store.assets.put_json({"agent_id": agent_id}, {"kind": "memory_test"})
                 for agent_id in engine.store.manifest["agent_ids"]}
    checkpoint = engine.checkpoint(snapshots)
    engine.store.restore(checkpoint.checkpoint_id, tmp_path / "branch").close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), tmp_path / "branch")
    try:
        assert restored.store not in memory_layer._CACHE
        after = restored.observe(context(restored, "r-after"))
        assert json.dumps([after["memory_layer"], after["persona_views"]], ensure_ascii=False) == rendered
        assert memory_layer._CACHE[restored.store][1].last_seq > 0
    finally:
        restored.close()
    memory_layer._CACHE.pop(engine.store)
    cold = _observe(engine, "r-cold")
    assert json.dumps([cold["memory_layer"], cold["persona_views"]], ensure_ascii=False) == rendered
    home, trip = _ended_sessions(engine)
    assert [row["recorded_at"] for row in cold["memory_layer"]["earlier_today"]] == [
        iso_time(home.ended_at, _ZONE), iso_time(trip.ended_at, _ZONE)]
    assert trip.kind == "transit" and trip.ended_at == engine.state.current_time


def test_reply_body_stays_in_its_session_progress_and_never_enters_the_memory_layer(engine_factory) -> None:
    """回复正文只是本 session 进度层中该调用的 self_note（docs/dev/SPEC-persona-prompt.md 第二章「回复正文」）：发消息那次
    回复的正文（去首尾空白）随调用出现在本段停留的进度层；出发结束这段停留后，记忆层写这次发消息的 settled 句，回复正文
    却不在记忆层，也不在路上的任何一层。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    body = "先跟朋友说一声再出门。"
    start = engine.state.current_time
    sent = _replied(engine, "n-send", "send_message",
                    SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="晚上见"), f"  {body} ")
    assert sent.status == "completed"
    (call,) = progress_calls(_observe(engine, "n-stay"), "send_message")
    assert call["self_note"] == body
    _walk(engine, "n-walk")
    road = _observe(engine, "n-road")
    assert road["memory_layer"]["earlier_today"] == [{
        "recorded_at": iso_time(start, _ZONE), "settled": [f"{_clock(engine, start)} 给{friend}发消息：晚上见"],
        "self_notes": []}]
    assert body not in json.dumps([road[key] for key in _FIVE_LAYERS], ensure_ascii=False)


def test_recalled_summary_still_in_the_memory_layer_is_not_repeated(engine_factory) -> None:
    """回看的 summary 仍在记忆层时结果只写「已在记忆层」与活动名（docs/dev/SPEC-persona-prompt.md 第一章「唯一出现原则」）：
    同一段停留里回看刚结束的等待，summary 还不在记忆层，结果照常写地点、阶段、进度与正文（正文不逐条标注未经核验，
    「通用格式」）；出发后这段停留进入记忆层，在路上再回看，details.recall 只有它在记忆层中的活动名与「已在记忆层」，不写
    其他回看事实，summary 在五层中只出现一次。"""
    engine = engine_factory()
    stage_day_plan(engine)
    summary = "歇好了，精神回来了。"
    name = _concluding_wait(engine, "s", summary, [])
    _act(engine, "s-home", "recall_task_summary", RecallTaskSummaryRequest(task_name=name))
    (home,) = progress_calls(_observe(engine, "s-home-seen"), "recall_task_summary")
    at_home = home["result"]["details"]["recall"]
    assert (at_home["kind"], at_home["name"], at_home["progress_note"]) == ("activity", name, summary)
    assert {"place", "phase", "objective_progress"} <= set(at_home) and "subjective_status" not in at_home
    _walk(engine, "s-walk")
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "s-road", "recall_task_summary", RecallTaskSummaryRequest(task_name=name))
    road = _observe(engine, "s-road-seen")
    (call,) = progress_calls(road, "recall_task_summary")
    recall = call["result"]["details"]["recall"]
    assert recall == {"kind": "activity", "activity": _short(name), "progress_note": "已在记忆层"}
    assert {"activity": _short(name), "text": summary} in road["memory_layer"]["earlier_today"][0]["self_notes"]
    assert json.dumps([road[key] for key in _FIVE_LAYERS], ensure_ascii=False).count(summary) == 1


def _chars(layer: dict) -> int:
    """记忆层紧凑 JSON 的字符数（窗口的计量口径）。"""
    return len(json.dumps(layer, ensure_ascii=False, separators=(",", ":")))


def _arrive(engine, round_id: str, travel_id: str) -> None:
    """推进到出行结束（途中的决策机会播报出行），到达那一秒给出行收尾。"""
    for step in range(64):
        if engine.state.activities[travel_id].status != ActivityStatus.ACTIVE:
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"{round_id}-{step}", "report_status", report_request(
                activity_name(engine, travel_id), summary="还在路上。"))
        else:
            advance_next(engine)
    _act(engine, round_id, "conclude_activity", conclude_request(activity_name(engine, travel_id), summary="到了"))


def test_a_real_memory_layer_over_the_window_drops_reflections_then_shrinks_the_oldest_entry(tmp_path,
                                                                                            monkeypatch) -> None:
    """真实派生的记忆层超过 8000 字符：1月5日 23:00 入睡、1月6日醒来登记的反思归 1月5日；当天在家收到陈母的消息没有回复，
    收尾时写下八条长记忆，出门、到公司再写八条后回家。窗口先只删最旧日期的反思（窗口恰为去掉反思后的大小时其余不动），
    8000 字符时再把最旧的今日条目缩成只剩未了结的消息句；路程条目与最近一个 session 的条目（最大的一条）原样保留，
    结果不超过窗口，观察中的记忆层就是这份截取结果，未回复的消息仍记为已呈现（第三章「窗口」）。"""
    engine = _late_engine(tmp_path, "2026-01-05T23:00:00+08:00", "memory-window-real")
    try:
        stage_day_plan(engine, submitted=True)
        _sleep_until_awake(engine, "v-sleep")
        reflection = "昨晚" + "想" * 998
        _reflect(engine, "v-wake", reflection)
        mother = next(item for item in engine.scenario.agents[0].contacts if item.salutation == "陈母")
        received_at = engine.state.current_time
        message_id = _contact_message(engine, mother.contact_id, "周末回家吃饭吗")
        _concluding_wait(engine, "v-home", "歇好了", [_memo("routine", f"{index}" + "家" * 599) for index in range(8)])
        _arrive(engine, "v-arrive", _walk(engine, "v-walk").activity_id)
        _concluding_wait(engine, "v-office", "忙完了", [_memo("routine", f"{index}" + "班" * 349) for index in range(8)])
        _walk(engine, "v-back", "home")
        context_ = engine._layer_context(engine.state.agents[1], (), [])
        monkeypatch.setattr(memory_layer, "MEMORY_WINDOW_CHARS", 10 ** 6)
        full = memory_view(context_, engine.store).layer
        assert full["earlier_days"] == [{"date": "2026-01-05", "settled": [],
                                         "self_notes": [{"activity": None, "text": reflection}]}]
        home, trip, office = full["earlier_today"]
        line = f"{_clock(engine, received_at)} 收到陈母的消息：周末回家吃饭吗"
        assert line in home["settled"] and len(home["self_notes"]) == len(office["self_notes"]) == 10
        unreflected = {**full, "earlier_days": []}
        assert _chars(full) > _chars(unreflected) > MEMORY_WINDOW_CHARS
        monkeypatch.setattr(memory_layer, "MEMORY_WINDOW_CHARS", _chars(unreflected))
        assert memory_view(context_, engine.store).layer == unreflected
        monkeypatch.setattr(memory_layer, "MEMORY_WINDOW_CHARS", MEMORY_WINDOW_CHARS)
        view = memory_view(context_, engine.store)
        shrunk = {"recorded_at": home["recorded_at"], "settled": [line], "self_notes": []}
        assert view.layer == {"layer": "记忆层", "earlier_days": [], "earlier_today": [shrunk, trip, office]}
        assert _chars(view.layer) <= MEMORY_WINDOW_CHARS
        assert view.presented_message_ids == view.open_message_ids == [message_id]
        observed = _observe(engine, "v-road")
        assert observed["memory_layer"] == view.layer and observed["presented_message_ids"] == [message_id]
    finally:
        engine.close()


def _finish_waiting(engine, round_id: str, activity_id: str) -> None:
    """推进到等待按计划结束（途中的决策机会播报它），再给它收尾。"""
    for step in range(16):
        if engine.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"{round_id}-{step}", "report_status", report_request(
                activity_name(engine, activity_id), summary="接着等。"))
        else:
            advance_next(engine)
    _act(engine, round_id, "conclude_activity", conclude_request(activity_name(engine, activity_id)))


def test_a_stay_spanning_midnight_is_one_entry_of_the_day_it_ended_with_dated_clocks(tmp_path) -> None:
    """零点不切断 session：1月5日 23:50 发消息并开始等待，等待跨过零点在 00:05 结束，收尾后出门；这段停留是一条记忆，
    记在它结束的 1月6日的 earlier_today（不进 earlier_days），1月5日发生的事句中带「1月5日」（第三章「零点、反思与未了结」）。"""
    engine = _late_engine(tmp_path, "2026-01-05T23:50:00+08:00", "memory-midnight")
    try:
        stage_day_plan(engine, submitted=True)
        friend = engine.scenario.agents[0].contacts[0].salutation
        _act(engine, "z-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}",
                                                                       text="快十二点了"))
        _, waited = _act(engine, "z-wait", "start_activity", start_activity("等待", "自己"))
        _finish_waiting(engine, "z-conclude", waited.activity_id)
        ended = engine.state.activities[waited.activity_id]
        assert local_date(ended.ended_at, _ZONE) == "2026-01-06"
        _walk(engine, "z-walk")
        name = activity_name(engine, waited.activity_id)
        assert _memory(engine, "z-road") == {"layer": "记忆层", "earlier_days": [], "earlier_today": [{
            "recorded_at": iso_time(engine.state.current_time, _ZONE),
            "settled": [f"1月5日 23:50 给{friend}发消息：快十二点了",
                        f"{name}：1月5日 23:50–{_clock(engine, ended.ended_at)}，按计划结束"],
            "self_notes": [{"activity": "等待", "text": "收尾。"}]}]}
        (stay,) = _ended_sessions(engine)
        assert (local_date(stay.started_at, _ZONE), local_date(stay.ended_at, _ZONE)) == ("2026-01-05", "2026-01-06")
    finally:
        engine.close()


def _decimal_order(engine, round_id: str) -> dict:
    """在菜市场远程下单一份标价不是整元的商品（送到这里），返回订单。"""
    from worldmock.life.storefront_state import current_quote

    observed = _observe(engine, f"{round_id}-preview")
    for row in observed["interaction_bindings"]:
        label = next((action["label"] for action in row["actions"] if action["label"] == "远程下单"
                      and action["note"] is None), None) if row["kind"] == "supply" else None
        supply = next((item for item in engine.scenario.supplies if item.supply_id == row["object_id"]), None)
        if label is None or supply is None:
            continue
        price = engine.semantics.purchase_amount(supply.product_id,
                                                 current_quote(engine.state, supply.supply_id).price_fen, 1)
        if price % 100:
            return _act(engine, round_id, "start_activity", start_activity(label, row["object"]))[1].data["orders"][0]
    pytest.fail("No listed supply has a price with fractional yuan")


def test_meal_and_decimal_price_order_sentences_come_from_real_settlements(engine_factory) -> None:
    """真实结算写成的句子：在菜市场远程下单一份标价带角分的商品，settled 写「付 X.YZ 元」（至多两位小数）；4 份里开吃
    1 份，吃到 300/1500 秒时结束，句子写「提前结束，吃了 0.2 份，丢弃 0.8 份」；出门后这段停留的条目带这两句。"""
    from tests.life.test_meal_outcome import _food

    engine = engine_factory()
    stage_day_plan(engine)
    food = _food(engine)
    _stage(engine, "market", 1500, inventory={"market": {food.product_id: 4.0}})
    ordered_at = engine.state.current_time
    order = _decimal_order(engine, "k-order")
    _, started = _act(engine, "k-eat", "start_activity", start_activity("食用", f"持有物：{food.name}"))
    assert started.status == "in_progress"
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    name = activity_name(engine, started.activity_id)
    _act(engine, "k-end", "conclude_activity", conclude_request(name, summary="吃完了"))
    _walk(engine, "k-walk")
    (entry,) = _memory(engine, "k-road")["earlier_today"]
    meal = engine.state.activities[started.activity_id]
    price = order["total_price_fen"]
    yuan = f"{price // 100}.{price % 100:02d}".rstrip("0")
    product = engine.rules.items[order["product_id"]].name
    market = next(item.name for item in engine.scenario.locations if item.location_id == "market")
    assert f"{_clock(engine, ordered_at)} 远程下单{product} 1 份，付 {yuan} 元，预计 " \
           f"{_clock(engine, order['expected_at'])} 送到{market}" in entry["settled"]
    assert (f"{name}：{_clock(engine, meal.started_at)}–{_clock(engine, meal.ended_at)}，提前结束，吃了 0.2 份，"
            f"丢弃 0.8 份") in entry["settled"]


@pytest.mark.asyncio
async def test_work_hours_and_submission_verdicts_are_settled_sentences(engine_factory) -> None:
    """真实工作与提交：做完首个任务要求的工时并收尾，句子写终局与有效工时；第一次提交被评审退回写「未通过」，改后再交
    通过写「通过」（评审桩只替换模型传输，提交与结算走真实路径）；出门后这段停留的条目按时间先后带这三句。"""
    from tests.life.test_work_review import StaticReviewer, _at_task_start, _complete_required_work, _submit_with_review

    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    work = next(item for item in engine.state.activities.values() if item.kind == ActivityKind.WORK)
    returned_at = engine.state.current_time
    returned = await _submit_with_review(engine, StaticReviewer(accepted=False), "u-return")
    assert (returned.status, returned.error_code) == ("rejected", "WORK_REQUIRES_REWORK")
    passed = await _submit_with_review(engine, StaticReviewer(accepted=True), "u-pass")
    assert passed.status == "completed"
    _walk(engine, "u-walk", "home")
    settled = _memory(engine, "u-road")["earlier_today"][-1]["settled"]
    title = engine.scenario.tasks[0].title
    work_line = (f"{activity_name(engine, work.activity_id)}：{_clock(engine, work.started_at)}–"
                 f"{_clock(engine, work.ended_at)}，{activity_control.ending_phrase(work)}，"
                 f"有效工时{duration_phrase(work.effective_work_seconds)}")
    verdicts = [f"{_clock(engine, returned_at)} 提交「{title}」，未通过", f"{_clock(engine, returned_at)} 提交「{title}」，通过"]
    assert [line for line in settled if line == work_line or line in verdicts] == [work_line, *verdicts]


def test_a_chat_ended_by_the_partner_is_settled_as_partner_ended(engine_factory) -> None:
    """真实交谈：陈曦发起、李凡接受，李凡先结束，陈曦的交谈同秒以「对方结束」结束；陈曦收尾后出门，这段停留的条目写
    「<交谈>：起–止，对方结束」。"""
    from tests.life.conftest import submit_round
    from tests.life.test_social_rounds import _invite_and_accept, _people

    engine = engine_factory("multi-person.json")
    _people(engine)
    chats = _invite_and_accept(engine)
    wake_by_mail(engine, agent_id=2, offset=120)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (2,)
    submit_round(engine, {2: ("conclude_activity", conclude_request(activity_name(engine, chats[2].activity_id)))},
                 "q-end")
    chat = engine.state.activities[chats[1].activity_id]
    assert chat.boundary_type == "partner_ended"
    name = activity_name(engine, chat.activity_id)
    submit_round(engine, {1: ("conclude_activity", conclude_request(name)),
                          2: ("start_activity", start_activity("等待", "自己"))}, "q-ack")
    office = next(row["object"] for row in _observe(engine, "q-preview")["interaction_bindings"]
                  if row["object_id"] == "office")
    submit_round(engine, {1: ("start_activity", start_activity("步行", office))}, "q-walk")
    entry = _memory(engine, "q-road")["earlier_today"][-1]
    assert f"{name}：{_clock(engine, chat.started_at)}–{_clock(engine, chat.ended_at)}，对方结束" in entry["settled"]


def test_messages_sent_and_deliveries_received_on_the_road_land_in_the_transit_entry(engine_factory) -> None:
    """路程中收到的邮件、发出的消息与送达都归路程 session：在菜市场远程下单、等一刻钟后步行去公司，路上发一条消息，订单
    按预计时刻在路上送到菜市场；到达后路程条目依次写邮件、发出的消息、送达与「到达」句，下单句与等待句留在菜市场那段
    停留的条目里（第三章「归属」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    friend = engine.scenario.agents[0].contacts[0].salutation
    order = _remote_order(engine, "t-order")
    _, waited = _act(engine, "t-wait", "start_activity", start_activity("等待", "自己"))
    _finish_waiting(engine, "t-wait-end", waited.activity_id)
    walk = _walk(engine, "t-walk")
    trip = engine.state.activities[walk.activity_id]
    mail_at = wake_by_mail(engine, offset=60)
    advance_next(engine)
    sent_at = engine.state.current_time
    _act(engine, "t-send", "send_message", SendMessageActionRequest(recipient_name=f"联系人：{friend}", text="在路上了"))
    arrives = order["expected_at"]
    assert sent_at < arrives < trip.planned_end_at
    _arrive(engine, "t-arrive", walk.activity_id)
    assert engine.state.deliveries[order["order_id"]].status == "delivered"
    home, road = _memory(engine, "t-arrived")["earlier_today"]
    trip = engine.state.activities[walk.activity_id]
    market = next(item.name for item in engine.scenario.locations if item.location_id == "market")
    context_ = engine._layer_context(engine.state.agents[1], (), [])
    assert home["settled"][0] == purchase_sentence(context_, order, None) and len(home["settled"]) == 2
    assert road["settled"][:2] == [_mail_line(engine, mail_at), f"{_clock(engine, sent_at)} 给{friend}发消息：在路上了"]
    assert road["settled"][2].startswith(f"{_clock(engine, arrives)} ") and road["settled"][2].endswith(
        f"：已送到{market}")
    assert road["settled"][3:] == [f"{activity_name(engine, walk.activity_id)}：{_clock(engine, trip.started_at)}–"
                                   f"{_clock(engine, trip.ended_at)}，到达"]
