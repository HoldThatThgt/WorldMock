"""邮件正文只在实际阅读完成后进入人物知识。"""

from __future__ import annotations

import pytest

from tests.life.conftest import (advance_until_ready, context, stage_day_plan, start_activity, submit_round,
                                 wake_by_mail)
from worldmock.errors import BusinessRuleError
from worldmock.life.content import mail_headers, reveal_mail
from worldmock.life.models import ActivityKind, ActivityStartParams, ActivityState


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
    （docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出」「并行准入」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    wake_by_mail(engine, offset=60)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "mail-wait")
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "mail-see"))
    mail = next(row for row in observed["interaction_bindings"] if row["kind"] == "mail")
    public = next(row for row in observed["facts"]["interactive_objects"] if row["object"] == mail["object"])
    assert public["actions"] == [{"action": "interact：阅读", "note": None}]
    _, results = submit_round(engine, {1: ("start_activity", start_activity("阅读", mail["object"]))}, "mail-read")
    assert results[0].status == "in_progress"
    reading = engine.state.activities[results[0].activity_id]
    assert reading.kind == ActivityKind.READ_MAIL and reading.target_id == engine.scenario.messages[0].message_id
