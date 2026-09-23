"""邮件正文只在实际阅读完成后进入人物知识。"""

from __future__ import annotations

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.content import mail_headers, reveal_mail
from worldmock.life.models import ActivityState


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
