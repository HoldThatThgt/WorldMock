"""邮件正文只在实际阅读完成后进入人物知识。"""

from __future__ import annotations

import pytest

from tests.life.conftest import (activity_name, advance_until_ready, context, stage_day_plan, start_activity,
                                 submit_round, wake_by_mail)
from tests.life.test_scheduling import _custom_engine
from tests.life.test_social_rounds import _chat, _contact_name, _people, _person, _template_contact, _wait
from worldmock.errors import BusinessRuleError
from worldmock.life.content import mail_headers, reveal_mail
from worldmock.life.models import (ActivityKind, ActivityStartParams, ActivityState, ReportStatusRequest,
                                   SendMessageActionRequest, active_activities)
from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS, TEMPLATE_REPLY_TEXT


def test_mail_body_requires_arrival_and_read_completion(engine_factory) -> None:
    """@brief 到达先展示标题，正文由阅读结算揭示。"""
    engine = engine_factory()
    message = engine.scenario.messages[0]
    activity = ActivityState.model_construct(target_id=message.message_id)
    person = engine.state.agents[1]
    assert mail_headers(person, engine.rules.messages) == []
    with pytest.raises(BusinessRuleError, match="MESSAGE_NOT_VISIBLE"):
        reveal_mail(activity, person, engine.rules.messages)

    delivered = person.model_copy(update={"visible_message_ids": {message.message_id}}, deep=True)
    headers = mail_headers(delivered, engine.rules.messages)
    assert headers[0]["subject"] == message.subject
    assert "body" not in headers[0]

    read, result = reveal_mail(activity, delivered, engine.rules.messages)
    assert result["body"] == message.body
    assert message.message_id in read.read_message_ids
    assert mail_headers(read, engine.rules.messages)[0]["body"] == message.body


def test_mail_read_start_freezes_the_arrived_mail_and_rejects_unseen_mail(engine_factory) -> None:
    """阅读邮件的开始校验只接受已到达（可见）的邮件并冻结其身份，未到达的以 MESSAGE_NOT_VISIBLE 拒绝；
    校验按人物的现场地点进行（docs/dev/SPEC-time-and-energy.md 第五章「并行准入」）。"""
    engine = engine_factory()
    message = engine.scenario.messages[0]
    person = engine.state.agents[1]
    request = ActivityStartParams(activity_type=ActivityKind.READ_MAIL, planned_duration_seconds=300,
                                  target_id=message.message_id)
    now = engine.state.current_time
    with pytest.raises(BusinessRuleError, match="MESSAGE_NOT_VISIBLE"):
        engine.rules.validate_start(person, request, now, person.location_id)
    delivered = person.model_copy(update={"visible_message_ids": {message.message_id}}, deep=True)
    assert engine.rules.validate_start(delivered, request, now, person.location_id) == {
        "message_id": message.message_id}


def test_arrived_mail_is_read_through_its_object(engine_factory) -> None:
    """到达的场景邮件作为邮件对象列出，动作为「interact：阅读」；选择后开始阅读活动并冻结该邮件，可与等待并行
    （docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」「并行准入」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    wake_by_mail(engine, offset=60)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "mail-wait")
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "mail-see"))
    mail = next(row for row in observed["interaction_bindings"] if row["kind"] == "mail")
    public = next(row for row in observed["tool_layer"]["targets"] if row["name"] == mail["object"])
    assert (public["kind"], public["state"]) == ("message", "未读")
    assert [(row["action"], row["way"], row["available"]) for row in public["actions"]] == [("interact", "阅读", True)]
    _, results = submit_round(engine, {1: ("start_activity", start_activity("阅读", mail["object"]))}, "mail-read")
    assert results[0].status == "in_progress"
    reading = engine.state.activities[results[0].activity_id]
    assert reading.kind == ActivityKind.READ_MAIL and reading.target_id == engine.scenario.messages[0].message_id


def test_chat_invites_person_messages_and_contact_replies_stay_in_the_inbox_not_mail_objects(tmp_path) -> None:
    """交谈邀请、人物消息与联系人回复以全文进入进度层的 message_arrival，不作为阅读对象；只有场景邮件是可阅读的邮件对象
    （docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」）。multi-person 场景真实引擎：人物 2 邀请
    人物 1 交谈、再给人物 1 发消息；人物 1 给 template 策略联系人发消息，占位回复在 CONTACT_REPLY_DELAY_SECONDS 后以
    message_arrival 送达，同一秒一封场景邮件也到达。四条都在收件箱，交互对象里只有那封场景邮件可阅读。"""
    engine = _custom_engine("multi-person.json", _template_contact, tmp_path, "inbox-not-mail")
    _people(engine)
    submit_round(engine, {1: _wait(), 2: _chat(engine, 2, 1)}, "inbox-invite")
    assert engine.scheduling_state().ready_agent_ids == (1,)
    template = SendMessageActionRequest(recipient_name=_contact_name(engine, "template"), text="在吗")
    submit_round(engine, {1: ("send_message", template)}, "inbox-contact")
    replied_at = engine.state.current_time + CONTACT_REPLY_DELAY_SECONDS
    wake_by_mail(engine, agent_id=2, offset=60)
    assert advance_until_ready(engine) and engine.scheduling_state().ready_agent_ids == (2,)
    message = SendMessageActionRequest(recipient_name=_person(engine, 2, 1), text="晚上一起吃饭吗")
    submit_round(engine, {2: ("send_message", message)}, "inbox-person")
    assert engine.scheduling_state().ready_agent_ids == (1,)
    waiting = activity_name(engine, active_activities(engine.state, 1)[0].activity_id)
    submit_round(engine, {1: ("report_status", ReportStatusRequest(task=[waiting], summary=["接着等。"]))},
                 "inbox-keep")
    wake_by_mail(engine, agent_id=1, offset=replied_at - engine.state.current_time)
    assert advance_until_ready(engine) and engine.state.current_time == replied_at
    observed = engine.observe(context(engine, "inbox-final"))
    friend = engine.state.agents[2].name
    contacts = next(item.contacts for item in engine.scenario.agents if item.agent_id == 1)
    salutation = next(item.salutation for item in contacts if item.reply_policy == "template")
    invite = next(item.text for item in engine.state.agents[1].inbox.values() if item.kind == "chat_invite")
    social = {("chat_invite", friend, invite), ("direct", friend, message.text),
              ("direct", salutation, TEMPLATE_REPLY_TEXT)}
    mail = engine.scenario.messages[0]
    arrivals = {(row["kind"], row["sender"], row.get("text", row.get("subject")))
                for row in observed["progress_layer"]["events"] if row["type"] == "message_arrival"}
    assert social | {("mail", None, mail.subject)} <= arrivals
    mails = [row for row in observed["interaction_bindings"] if row["kind"] == "mail"]
    assert [row["object_id"] for row in mails] == [mail.message_id]
    objects = observed["tool_layer"]["targets"]
    readable = [row["name"] for row in objects if any(item["way"] == "阅读" for item in row["actions"])]
    assert readable == [mails[0]["object"]]
    assert not [row["name"] for row in objects for _, _, text in social if text in row["name"]]
