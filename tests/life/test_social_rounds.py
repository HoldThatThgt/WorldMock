"""第五章「交谈」与联系人回复的多人验收（multi-person 场景，真实引擎）。

对发起人选择交谈即接受：不再发邀请、计划结束对齐发起方；同一对人物重复发起以 CHAT_ALREADY_ACTIVE 拒绝；一方结束时
对方同秒结束（partner_ended）；缓解按双方同时进行的秒数结算，与人物推进顺序无关；途中人物不算在场；联系人 none 策略
照常送达不回复、template 策略落一条占位回复；反馈间隔为 60、7200、86400 秒时交谈、通话、等待都无 run_failed。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 stage_day_plan, start_activity, submit_round, wake_by_mail)
from tests.life.test_scheduling import _custom_engine
from worldmock.life import social_actions
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (ActivityKind, ActivityStatus, ReportStatusRequest, SendMessageActionRequest,
                                   active_activities)
from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS, TEMPLATE_REPLY_TEXT


def _people(engine: LifeEngine) -> None:
    """@brief 两人都登记空计划。"""
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)


def _person(engine: LifeEngine, observer: int, target: int) -> str | None:
    """@brief observer 本轮交互对象层中 target 的可读名称；不在场时为 None。"""
    observed = engine.observe(context(engine, f"person-{observer}-{target}-{engine.state.version}", observer))
    return next((row["object"] for row in observed["interaction_bindings"]
                 if row["kind"] == "person" and row["object_id"] == f"agent:{target}"), None)


def _wait() -> tuple[str, object]:
    """@brief 以「自己」开始等待。"""
    return "start_activity", start_activity("等待", "自己")


def _chat(engine: LifeEngine, observer: int, target: int) -> tuple[str, object]:
    """@brief observer 对同场的 target 选择交谈。"""
    return "start_activity", start_activity("交谈", _person(engine, observer, target))


def _chats(engine: LifeEngine) -> dict[int, object]:
    """@brief 每个人物最近开始的一项交谈。"""
    rows = sorted((item for item in engine.state.activities.values() if item.kind == ActivityKind.CHAT),
                  key=lambda item: (item.started_at, item.activity_id))
    return {item.agent_id: item for item in rows}


def _invite_and_accept(engine: LifeEngine) -> dict[int, object]:
    """@brief 人物 1 发起交谈、人物 2 同秒因邀请获得机会并对 1 选择交谈（接受）；返回双方交谈。"""
    submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "invite")
    assert engine.scheduling_state().ready_agent_ids == (2,)
    submit_round(engine, {2: _chat(engine, 2, 1)}, "accept")
    return _chats(engine)


def test_choosing_chat_with_the_inviter_accepts_without_a_new_invite(engine_factory) -> None:
    """邀请写明「对<发起人>选择交谈即接受」；对发起人选择交谈即接受：不再给发起人发邀请，接受方的计划结束对齐发起方，
    双方交谈都记为已接受；同一秒内的邀请与接受都不产生 run_failed（第五章「交谈」）。"""
    engine = engine_factory("multi-person.json")
    _people(engine)
    submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "invite")
    inbox = engine.observe(context(engine, "invite-inbox", 2))["facts"]["inbox"]
    inviter = engine.state.agents[1].name
    assert inbox[-1]["kind"] == "chat_invite" and inbox[-1]["head"] == f"{inviter}邀请你交谈，对{inviter}选择交谈即接受"
    submit_round(engine, {2: _chat(engine, 2, 1)}, "accept")
    chats = _chats(engine)
    assert chats[2].planned_end_at == chats[1].planned_end_at and chats[2].started_at == chats[1].started_at
    assert chats[1].data["accepted"] is True and chats[2].data["accepted"] is True
    assert not any(message.kind == "chat_invite" for message in engine.state.agents[1].inbox.values())
    assert not engine.store.read_agent_events("run_failed", 1) and not engine.store.read_agent_events("run_failed", 2)


def test_repeated_chat_between_the_same_pair_is_chat_already_active(engine_factory) -> None:
    """同一对人物只能有一项进行中交谈：发起人再对同一人选择交谈，在占用判定之前以 CHAT_ALREADY_ACTIVE 拒绝，
    交互对象层的交谈动作带同一说明。"""
    engine = engine_factory("multi-person.json")
    _people(engine)
    submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "invite")
    waiting = activity_name(engine, active_activities(engine.state, 2)[0].activity_id)
    submit_round(engine, {2: ("report_status", ReportStatusRequest(task=[waiting], summary=["先等等。"]))}, "later")
    wake_by_mail(engine, agent_id=1, offset=60)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (1,)
    observed = engine.observe(context(engine, "again-preview"))
    chat = next(action for row in observed["interaction_bindings"] if row["object_id"] == "agent:2"
                for action in row["actions"] if action["label"] == "交谈")
    assert chat["note"] == "你们已经在交谈了"
    _, results = submit_round(engine, {1: _chat(engine, 1, 2)}, "again")
    assert results[0].status == "rejected" and results[0].error_code == "CHAT_ALREADY_ACTIVE"
    assert results[0].data["reason"] == "你们已经在交谈了"


def test_one_side_ending_ends_the_partner_chat_in_the_same_second(engine_factory) -> None:
    """一方结束交谈时，对方以其为伙伴的进行中交谈在同一秒结束：status=completed，boundary_type 与
    interruption_reason 都记 partner_ended，成为待收尾，对方同秒获得收尾机会。"""
    engine = engine_factory("multi-person.json")
    _people(engine)
    chats = _invite_and_accept(engine)
    wake_by_mail(engine, agent_id=2, offset=120)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (2,)
    now = engine.state.current_time
    submit_round(engine, {2: ("conclude_activity", conclude_request(activity_name(engine, chats[2].activity_id)))},
                 "end")
    ended = engine.state.activities[chats[1].activity_id]
    assert ended.status == ActivityStatus.COMPLETED and ended.ended_at == now
    assert ended.boundary_type == ended.interruption_reason == social_actions.PARTNER_ENDED
    assert ended.concluded_at is None and engine.state.agents[1].next_decision_at == now
    assert engine.state.agents[1].pending_wake_reasons == ["no_active_activity"]
    assert engine.scheduling_state().ready_agent_ids == (1,)


def test_accepted_chat_relief_uses_seconds_both_chats_run_regardless_of_order(engine_factory) -> None:
    """接受带来的缓解按区间内双方交谈同时进行的秒数结算，以推进前的状态判定：即使同一次推进里对方的交谈先结算完成，
    本人这一段仍按双方同时进行的秒数取交谈账本，不退回未接受的速率；接受之前的一段不算（第五章「交谈」）。"""
    from worldmock.footprints.models import EventDraft

    engine = engine_factory("multi-person.json")
    _people(engine)
    staged = engine.state.model_copy(deep=True)
    for agent in staged.agents.values():
        agent.body = agent.body.model_copy(update={"social_need": 0.8})
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_social_need_staged"})])
    engine.state = staged
    submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "invite")
    waiting = activity_name(engine, active_activities(engine.state, 2)[0].activity_id)
    submit_round(engine, {2: ("report_status", ReportStatusRequest(task=[waiting], summary=["等会儿。"]))}, "later")
    invite = _chats(engine)[1]
    assert social_actions.shared_chat_seconds(engine.state, invite, invite.planned_end_at) == 0
    wake_by_mail(engine, agent_id=2, offset=300)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (2,)
    submit_round(engine, {2: _chat(engine, 2, 1)}, "accept-late")
    chats = _chats(engine)
    end = chats[1].planned_end_at
    assert chats[2].planned_end_at == end and chats[2].planned_duration_seconds == end - engine.state.current_time
    assert [social_actions.shared_chat_seconds(engine.state, chats[key], end) for key in (1, 2)] == [600, 600]
    advanced = engine.state.model_copy(deep=True)
    advanced.activities[chats[1].activity_id] = chats[1].model_copy(update={
        "status": ActivityStatus.COMPLETED, "ended_at": end, "last_settled_at": end,
        "elapsed_seconds": chats[1].planned_duration_seconds}, deep=True)
    assert social_actions.shared_chat_seconds(advanced, chats[2], end) == 600
    before = {key: engine.state.agents[key].body.social_need for key in (1, 2)}
    while engine.state.activities[chats[1].activity_id].status == ActivityStatus.ACTIVE:
        _keep_going(engine)
    after = {key: engine.state.agents[key].body.social_need for key in (1, 2)}
    assert all(after[key] < before[key] for key in (1, 2))
    assert engine.state.activities[chats[2].activity_id].status == ActivityStatus.COMPLETED


def _keep_going(engine: LifeEngine) -> None:
    """@brief 有人就绪时播报其进行中活动继续，否则推进到下一边界。"""
    ready = engine.scheduling_state().ready_agent_ids
    if not ready:
        advance_next(engine)
        return
    requests = {}
    for agent_id in ready:
        names = [activity_name(engine, item.activity_id) for item in active_activities(engine.state, agent_id)][:2]
        requests[agent_id] = ("report_status", ReportStatusRequest(task=names, summary=["继续。" for _ in names]))
    submit_round(engine, requests, f"keep-{engine.state.current_time}-{engine.state.version}")


def test_people_on_the_road_are_not_present_and_cannot_be_invited(engine_factory) -> None:
    """途中不在任何地点：出行中的人物不出现在他人的交互对象与在场名单里，出行的人物自己身边也没有人。"""
    engine = engine_factory("multi-person.json")
    _people(engine)
    assert _person(engine, 1, 2) is not None
    observed = engine.observe(context(engine, "road-preview", 2))
    trip = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                if row["kind"] == "location" for action in row["actions"] if action["label"] == "步行")
    submit_round(engine, {1: _wait(), 2: ("start_activity", start_activity(trip[1], trip[0]))}, "leave")
    assert _person(engine, 1, 2) is None and _person(engine, 2, 1) is None
    watcher = engine.observe(context(engine, "road-left", 1))
    assert engine.state.agents[2].name not in [row["name"] for row in watcher["facts"]["people_here"]]


def test_awake_partner_gets_the_invite_in_the_same_second_and_a_sleeper_is_not_woken(engine_factory) -> None:
    """交谈邀请送达时，清醒的收件人在同一秒以 message_arrival 获得机会；人物消息送达睡着的收件人时不唤醒，睡着的人物
    照常列出、交谈带「对方现在不方便交谈」（第一章「人物之间的消息」、第五章「动作照常列出」）。"""
    engine = engine_factory("multi-person.json")
    _people(engine)
    submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "awake-invite")
    assert engine.scheduling_state().ready_agent_ids == (2,)
    assert engine.state.agents[2].pending_wake_reasons == ["message_arrival"]
    sleeper = engine_factory("multi-person.json")
    _people(sleeper)
    submit_round(sleeper, {1: _wait(), 2: ("start_activity", start_activity("睡觉", "自己"))}, "sleep")
    wake_by_mail(sleeper, agent_id=1, offset=60)
    assert advance_until_ready(sleeper) and sleeper.scheduling_state().ready_agent_ids == (1,)
    row = next(row for row in sleeper.observe(context(sleeper, "msg-preview"))["interaction_bindings"]
               if row["object_id"] == "agent:2")
    assert [(action["label"], action["note"]) for action in row["actions"]] == [("交谈", "对方现在不方便交谈")]
    message = SendMessageActionRequest(recipient_name=row["object"], text="睡了吗")
    submit_round(sleeper, {1: ("send_message", message)}, "msg")
    assert 2 not in sleeper.scheduling_state().ready_agent_ids
    assert any(item.kind == ActivityKind.SLEEP for item in active_activities(sleeper.state, 2))


def _template_contact(document: dict) -> None:
    """@brief 把人物 1 的最后一位 model 策略联系人改为 template 策略（none 策略联系人保留）。"""
    agent = next(item for item in document["agents"] if item["agent_id"] == 1)
    contact = [item for item in agent["contacts"] if item["reply_policy"] == "model"][-1]
    contact["reply_policy"] = "template"


def _contact_name(engine: LifeEngine, policy: str) -> str:
    """@brief 人物 1 的某一回复策略的联系人在交互对象层中的可读名称。"""
    config = next(item for item in engine.scenario.agents if item.agent_id == 1)
    contact = next(item for item in config.contacts if item.reply_policy == policy)
    observed = engine.observe(context(engine, f"contact-{policy}"))
    return next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == contact.contact_id)


def test_none_policy_delivers_without_reply_and_template_policy_posts_a_placeholder(tmp_path) -> None:
    """给 none 策略联系人发消息照常送达、不回复；template 策略落一条占位回复，在 CONTACT_REPLY_DELAY_SECONDS 后送达
    收件箱（不再以 CONTACT_REPLY_NOT_MODEL 拒绝，第五章「动作照常列出」）。"""
    engine = _custom_engine("multi-person.json", _template_contact, tmp_path, "template-reply")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    none_name, template_name = _contact_name(engine, "none"), _contact_name(engine, "template")
    _, results = submit_round(engine, {1: ("send_message", SendMessageActionRequest(
        recipient_name=none_name, text="在吗")), 2: _wait()}, "none-message")
    assert next(item for item in results if item.agent_id == 1).status == "completed"
    assert not [item for item in engine.state.pending_events if item.scheduled_id.startswith("contact-reply:")]
    assert not [event for event in engine.store.read_agent_events("environment_event", 1)
                if event.data.get("kind") == "model_npc_reply_pending"]
    _, results = submit_round(engine, {1: ("send_message", SendMessageActionRequest(
        recipient_name=template_name, text="在吗"))}, "template-message")
    sent_at = engine.state.current_time
    scheduled = [item for item in engine.state.pending_events if item.scheduled_id.startswith("contact-reply:")]
    assert len(scheduled) == 1 and scheduled[0].due_at == sent_at + CONTACT_REPLY_DELAY_SECONDS
    submit_round(engine, {1: _wait()}, "template-wait")
    while engine.state.current_time < sent_at + CONTACT_REPLY_DELAY_SECONDS:
        advance_next(engine)
    replies = [message for message in engine.state.agents[1].inbox.values() if message.text == TEMPLATE_REPLY_TEXT]
    assert len(replies) == 1 and replies[0].created_at == sent_at
    assert not engine.store.read_agent_events("run_failed", 1)


def _feedback(seconds: int):
    """@brief 场景结算间隔改为给定秒数的改写函数。"""
    def mutate(document: dict) -> None:
        document["activity_feedback_seconds"] = seconds
    return mutate


@pytest.mark.parametrize("seconds,chat,call", [(60, 60, 600), (7200, 7200, 3600), (86400, 7200, 3600)])
def test_chat_call_and_wait_run_without_failure_at_any_feedback_interval(tmp_path, seconds, chat, call) -> None:
    """反馈间隔为 60、7200、86400 秒时交谈、通话与等待都照常受理：交谈时长截取到 60–7200 秒、通话到 600–3600 秒、
    等待取间隔，不再触发 CALL_DURATION_OUT_OF_RANGE 或请求模型的校验异常，推进到下一个决策机会也没有 run_failed
    （第五章「交谈」）。"""
    engine = _custom_engine("multi-person.json", _feedback(seconds), tmp_path, f"feedback-{seconds}")
    _people(engine)
    _, invited = submit_round(engine, {1: _chat(engine, 1, 2), 2: _wait()}, "feedback-invite")
    assert all(result.status == "in_progress" for result in invited), [result.error_code for result in invited]
    contact = next(row["object"] for row in engine.observe(context(engine, "feedback-preview", 2))[
        "interaction_bindings"] if row["kind"] == "contact")
    _, called = submit_round(engine, {2: ("start_activity", start_activity("通话", contact))}, "feedback-call")
    assert called[0].status == "in_progress", called[0].error_code
    calls = [item for item in engine.state.activities.values() if item.kind == ActivityKind.CALL]
    waits = [item for item in engine.state.activities.values() if item.kind == ActivityKind.WAIT]
    assert _chats(engine)[1].planned_duration_seconds == chat and calls[0].planned_duration_seconds == call
    assert waits[0].planned_duration_seconds == seconds
    assert advance_until_ready(engine)
    assert not engine.store.read_agent_events("run_failed", 1) and not engine.store.read_agent_events("run_failed", 2)
