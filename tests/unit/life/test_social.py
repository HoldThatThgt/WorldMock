"""社交最小可行版纯函数域的独立测试（docs/design/04-simulation-engine.md「社交最小可行版」），以及人物目标在真实观察
工具层中的列法与环境按私有绑定解析名称（docs/design/06-agent.md「工具层」）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life.social import (
    TEMPLATE_REPLY_TEXT,
    AgentPresence,
    Contact,
    ContactReply,
    IncomingMessage,
    OutgoingMessage,
    PersonView,
    can_talk,
    finalize_model_reply,
    model_reply_request,
    ReceivedItem,
    ReplyFacts,
    ReplyHistoryEntry,
    needs_reply,
    people_here,
    send_message,
    start_call,
    start_chat,
    template_reply,
)


def _presence(name: str = "李雷", location: str = "办公室",
              family: str = "工作", awake: bool = True,
              agent_id: str = "a2") -> AgentPresence:
    """构造同世界他人在场事实。"""
    return AgentPresence(agent_id=agent_id, name=name, location=location,
                         activity_family=family, awake=awake)


def _contact(**overrides) -> Contact:
    """构造场景联系人（妈妈，template 策略）。"""
    fields = dict(contact_id="c-mother", salutation="妈妈", relation="家人",
                  closeness=12, channels=("电话", "短信"),
                  reply_policy="template")
    fields.update(overrides)
    return Contact(**fields)


def _outgoing(recipient_id: str = "a2", text: str = "晚上一起吃饭吗？",
              message_id: str = "m-1") -> OutgoingMessage:
    """构造人物发出的消息请求。"""
    return OutgoingMessage(message_id=message_id, sender_id="a1",
                           recipient_id=recipient_id, text=text,
                           created_at=1000)


def _incoming() -> IncomingMessage:
    """构造联系人收到的人物消息。"""
    return IncomingMessage(message_id="m-9", sender_id="a1",
                           contact_id="c-mother", text="周末回家吃饭吗",
                           received_at=1400)


def test_people_here_filters_location_and_exposes_closed_fields() -> None:
    """同地点者可见、异地者过滤；facts 层含不透明身份，隐私闭集不含身体/余额/任务/记忆。"""
    here = _presence(name="李雷", location="共享办公-茶水间")
    away = _presence(name="韩梅梅", location="家", agent_id="a3")
    views = people_here("共享办公-茶水间", [here, away])
    assert [view.name for view in views] == ["李雷"]
    # 身份锚点闭集（docs/design/09-io-formats.md「人物工具与管理接口」工具身份枚举）+ 隐私闭集（docs/design/04-simulation-engine.md「社交最小可行版」）。
    assert set(PersonView.model_fields) == {"person_id", "name", "activity_family", "can_talk"}
    dumped = views[0].model_dump()
    assert dumped["person_id"] == "a2"
    assert set(dumped) == {"person_id", "name", "activity_family", "can_talk"}
    for forbidden in ("balance", "tasks", "energy_kcal", "memory", "sleep_pressure"):
        assert forbidden not in dumped


def test_can_talk_requires_awake_and_allows_current_app_activity() -> None:
    """睡着的人不可交谈；应用使用不虚构已退役的游戏状态。"""
    assert can_talk(_presence())
    assert not can_talk(_presence(awake=False))
    assert can_talk(_presence(family="使用应用"))
    views = people_here("办公室", [_presence(awake=False), _presence(family="使用应用")])
    assert [view.can_talk for view in views] == [False, True]


def test_send_message_accepts_300_chars_and_rejects_301() -> None:
    """消息文本 300 汉字边界：恰好 300 受理，301 拒绝。"""
    sent = send_message(_outgoing(text="饭" * 300), ["a2"], [])
    assert len(sent.message.text) == 300
    with pytest.raises(ValueError, match="300"):
        send_message(_outgoing(text="饭" * 301), ["a2"], [])


def test_send_message_rejects_unknown_recipient() -> None:
    """收件人既非同世界人物也非场景联系人即拒绝。"""
    with pytest.raises(ValueError, match="recipient not found"):
        send_message(_outgoing(recipient_id="ghost"), ["a2"], [_contact()])


def test_send_message_delivers_to_world_agent_and_contact() -> None:
    """同世界人物与场景联系人均可收件；产出 message_arrival 送达结构。"""
    world = send_message(_outgoing(recipient_id="a2"), ["a2"], [])
    to_contact = send_message(_outgoing(recipient_id="c-mother"), [], [_contact()])
    for sent in (world, to_contact):
        assert sent.message.kind == "direct"
        assert sent.arrival.event_type == "environment_event"
        assert sent.arrival.kind == "message_arrival"
        assert sent.arrival.message_id == sent.message.message_id
        assert sent.arrival.recipient_id == sent.message.recipient_id
        assert sent.arrival.occurred_at == sent.message.created_at


def test_start_chat_rejects_absent_or_unavailable_partner() -> None:
    """交谈要求共同在场且对方醒着。"""
    with pytest.raises(ValueError, match="co-presence"):
        start_chat("办公室", _presence(location="家"), 30, _outgoing())
    with pytest.raises(ValueError, match="cannot talk"):
        start_chat("办公室", _presence(awake=False), 30, _outgoing())


def test_start_chat_registers_foreground_activity_with_invite() -> None:
    """有效交谈发起产出前台活动与 chat_invite 邀请送达记录。"""
    chat = start_chat("办公室", _presence(), 45, _outgoing())
    assert chat.activity_kind == "chat"
    assert chat.partner_id == "a2"
    assert chat.duration_seconds == 45
    assert chat.invite_message.kind == "chat_invite"
    assert chat.invite_message.recipient_id == "a2"
    assert chat.arrival.kind == "message_arrival"


def test_call_duration_is_actual_content_seconds_without_old_minute_window() -> None:
    """@brief 通话时长采用内容余量，一秒与长内容均受理，不再有十至六十分钟窗口。"""
    for seconds in (1, 59, 600, 3600, 10800):
        call = start_call(_contact(), seconds)
        assert call.activity_kind == "call"
        assert call.duration_seconds == seconds
        assert call.attention_occupied is True
        assert call.relief_per_hour == pytest.approx(0.4)
    for seconds in (0, -1):
        with pytest.raises(ValueError, match="duration"):
            start_call(_contact(), seconds)


def test_contact_closeness_bounds_and_policy_closed_set() -> None:
    """亲密度闭区间 0-30；reply_policy 只允许 none/template/model。"""
    assert _contact(closeness=0).closeness == 0
    assert _contact(closeness=30).closeness == 30
    for bad in (-1, 31):
        with pytest.raises(ValidationError):
            _contact(closeness=bad)
    with pytest.raises(ValidationError):
        _contact(reply_policy="always")


def test_reply_policy_branches_with_at_most_once_idempotency() -> None:
    """none 不回复；template/model 首条回复；同一消息幂等至多一次。"""
    incoming = _incoming()
    assert not needs_reply("none", incoming.message_id, set())
    assert needs_reply("template", incoming.message_id, set())
    assert needs_reply("model", incoming.message_id, set())
    replied = {incoming.message_id}
    assert not needs_reply("model", incoming.message_id, replied)
    assert not needs_reply("template", incoming.message_id, replied)


def test_template_reply_is_placeholder_with_model_npc_source() -> None:
    """template 策略返回占位记录，标记 model_npc，且结构不含资源/任务字段。"""
    reply = template_reply(_incoming(), "r-1", 1500)
    assert isinstance(reply, ContactReply)
    assert reply.message.text == TEMPLATE_REPLY_TEXT
    assert reply.source_kind == "model_npc"
    assert reply.message.kind == "direct"
    assert reply.message.sender_id == "c-mother"
    assert reply.message.recipient_id == "a1"
    assert set(reply.model_dump()) == {"message", "source_kind"}


def test_model_reply_request_and_finalize_validate_200_chars() -> None:
    """model 策略产出独立调用请求（带来信事实、双方往来与对方收到的物品，presence 固定 remote）；
    回复文本 200 汉字边界并标记 model_npc。"""
    history = (ReplyHistoryEntry(speaker="陈曦", time="10:00", text="在吗"),)
    received = (ReceivedItem(item="牛肉炒面", quantity="1 份", time="10:40"),)
    facts = ReplyFacts(sender_name="陈曦", now="2026-01-05T10:45:00+08:00", receive_location="公司")
    request = model_reply_request(_incoming(), history, received, facts)
    assert (request.history, request.received) == (history, received)
    assert (request.sender_name, request.now, request.receive_location) == (
        "陈曦", "2026-01-05T10:45:00+08:00", "公司")
    assert request.presence == "remote"
    assert model_reply_request(_incoming(), history, received,
                               ReplyFacts(sender_name="陈曦", now="2026-01-05T10:45:00+08:00")).receive_location is None
    with pytest.raises(ValidationError):
        ReplyFacts(sender_name="", now="2026-01-05T10:45:00+08:00")
    assert request.thinking == "disabled"
    assert request.max_chars == 200
    assert request.reply_to_message_id == "m-9"
    assert request.recipient_id == "a1"
    reply = finalize_model_reply(request, "好" * 200, "r-2", 1600)
    assert reply.source_kind == "model_npc"
    assert reply.message.sender_id == "c-mother"
    assert len(reply.message.text) == 200
    with pytest.raises(ValueError, match="200"):
        finalize_model_reply(request, "好" * 201, "r-3", 1600)


def test_message_recipients_come_from_current_observation_identities() -> None:
    """@brief 世界人物与本人的登记联系人提供独立真实身份，未知收件人被拒绝。"""
    from tests.life.action_worlds import action_world
    semantics, state = action_world(multiple=True)
    people = [f"agent:{identity}" for identity in state.agents if identity != 1]
    contacts = semantics.scenario.agents[0].contacts
    person = send_message(_outgoing(people[0]), people, contacts)
    contact = send_message(_outgoing(contacts[0].contact_id), people, contacts)
    assert person.message.recipient_id == "agent:2"
    assert contact.message.recipient_id == "contact-colleague"
    with pytest.raises(ValueError):
        send_message(_outgoing("unknown"), people, contacts)


def test_social_partners_pair_kind_with_identity_source() -> None:
    """@brief 共同在场社交只能引用真实人物；远程社交只能引用本人的登记联系人。"""
    from tests.life.action_worlds import action_world, begin
    from worldmock.life import social_actions
    from worldmock.errors import BusinessRuleError
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    _, local = begin(semantics, state, "small-talk", None, "agent:2")
    assert social_actions.start_chat(state, local, "invite").invite.message.recipient_id == "agent:2"
    contact = semantics.scenario.agents[0].contacts[0]
    _, remote = begin(semantics, state, "voice-call", "wechat", contact.contact_id)
    assert social_actions.start_remote_social(remote, contact, "voice").contact_id == contact.contact_id
    with pytest.raises(BusinessRuleError, match="SOCIAL_PARTNER_NOT_FOUND"):
        social_actions.partner_presence(state, contact.contact_id)
    remote.data["counterpart"] = "agent:2"
    with pytest.raises(BusinessRuleError, match="CONTACT_NOT_FOUND"):
        social_actions.start_remote_social(remote, contact, "voice")
