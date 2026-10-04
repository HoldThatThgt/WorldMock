"""社交最小可行版纯函数域的独立测试（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」），以及人物目标在真实观察
工具层中的列法与环境按私有绑定解析名称（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。"""

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
              gaming: bool = False, agent_id: str = "a2") -> AgentPresence:
    """构造同世界他人在场事实。"""
    return AgentPresence(agent_id=agent_id, name=name, location=location,
                         activity_family=family, awake=awake,
                         actively_gaming=gaming)


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
    # 身份锚点闭集（docs/CONTRACTS.md#contracts-worldmock-接口合同「人物工具与管理接口」工具身份枚举）+ 隐私闭集（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。
    assert set(PersonView.model_fields) == {"person_id", "name", "activity_family", "can_talk"}
    dumped = views[0].model_dump()
    assert dumped["person_id"] == "a2"
    assert set(dumped) == {"person_id", "name", "activity_family", "can_talk"}
    for forbidden in ("balance", "tasks", "energy_kcal", "memory", "sleep_pressure"):
        assert forbidden not in dumped


def test_can_talk_requires_awake_and_not_active_gaming() -> None:
    """睡眠或主动游戏中的人不可交谈，且在场投影同步反映。"""
    assert can_talk(_presence())
    assert not can_talk(_presence(awake=False))
    assert not can_talk(_presence(gaming=True))
    views = people_here("办公室", [_presence(awake=False), _presence(gaming=True)])
    assert [view.can_talk for view in views] == [False, False]


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
    """交谈要求共同在场且对方可交谈（睡眠/主动游戏均拒绝）。"""
    with pytest.raises(ValueError, match="co-presence"):
        start_chat("办公室", _presence(location="家"), 30, _outgoing())
    with pytest.raises(ValueError, match="cannot talk"):
        start_chat("办公室", _presence(awake=False), 30, _outgoing())
    with pytest.raises(ValueError, match="cannot talk"):
        start_chat("办公室", _presence(gaming=True), 30, _outgoing())


def test_start_chat_registers_foreground_activity_with_invite() -> None:
    """有效交谈发起产出前台活动与 chat_invite 邀请送达记录。"""
    chat = start_chat("办公室", _presence(), 45, _outgoing())
    assert chat.activity_kind == "chat"
    assert chat.partner_id == "a2"
    assert chat.duration_minutes == 45
    assert chat.invite_message.kind == "chat_invite"
    assert chat.invite_message.recipient_id == "a2"
    assert chat.arrival.kind == "message_arrival"


def test_call_duration_boundaries_10_and_60_minutes() -> None:
    """通话时长闭区间：10 与 60 分钟受理，9 与 61 拒绝。"""
    for minutes in (10, 60):
        call = start_call(_contact(), minutes)
        assert call.activity_kind == "call"
        assert call.duration_minutes == minutes
        assert call.attention_occupied is True
        assert call.relief_per_hour == pytest.approx(0.4)
    for minutes in (9, 61):
        with pytest.raises(ValueError, match="duration"):
            start_call(_contact(), minutes)


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


def _people_seen(engine_factory):
    """双人场景运行开始时人物 1 的真实观察：同处一地的同伴与通讯录联系人都是工具层 person 目标。"""
    from tests.life.conftest import context, stage_day_plan

    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    observed = engine.observe(context(engine, "people-seen"))
    people = [row for row in observed["tool_layer"]["targets"] if row["kind"] == "person"]
    return engine, observed, people


def test_message_recipients_come_from_current_observation_identities(engine_factory) -> None:
    """send_message 的对象是本轮工具层的人物目标：同处的同伴（「人物：」）与通讯录联系人（「联系人：」）各带可用的
    send_message；环境按本轮私有绑定解析名称，列出的名称送达，未列出的以 RECIPIENT_NOT_FOUND 业务拒绝。同伴在第一轮
    开始睡觉，此后只有人物 1 在同一秒继续决策。"""
    from tests.life.conftest import start_activity, submit_round
    from worldmock.life.models import SendMessageActionRequest

    engine, observed, people = _people_seen(engine_factory)
    friend = next(item.name for item in engine.context.state.agents.values() if item.agent_id != 1)
    salutations = [item.salutation for item in engine.context.scenario.agents[0].contacts]
    assert [row["name"] for row in people] == [f"人物：{friend}", *(f"联系人：{name}" for name in salutations)]
    assert all(row["tools"] == [{"tool": "send_message", "available": True, "reason": None}] for row in people)
    kinds = {row["object"]: row["kind"] for row in observed["interaction_bindings"]}
    assert [kinds[row["name"]] for row in people] == ["person", *("contact" for _ in salutations)]
    for index, name in enumerate([people[0]["name"], people[-1]["name"], "人物：陌生人"]):
        requests = {1: ("send_message", SendMessageActionRequest(recipient_name=name, text="周末见面吗"))}
        if index == 0:
            requests[2] = ("start_activity", start_activity("睡觉", "自己"))
        _, results = submit_round(engine, requests, f"recipient-{index}")
        result = next(item for item in results if item.agent_id == 1)
        expected = ("rejected", "RECIPIENT_NOT_FOUND") if name == "人物：陌生人" else ("completed", None)
        assert (result.status, result.error_code) == expected, name


def test_social_partners_pair_kind_with_identity_source(engine_factory) -> None:
    """交谈与通话由对象本轮动作闭集配对：同伴只列 interact:交谈，联系人只列 interact:通话；环境按私有绑定解析选择，
    交换方式与未列出的对象解析不出，内部动词由合法方式决定。"""
    from tests.life.conftest import select_start, start_arguments

    _, observed, people = _people_seen(engine_factory)
    person, contact = people[0]["name"], people[-1]["name"]
    offered = {row["name"]: [(item["action"], item["way"]) for item in row["actions"]] for row in people}
    assert offered[person] == [("interact", "交谈")] and offered[contact] == [("interact", "通话")]
    bindings = observed["interaction_bindings"]
    for target, way, canonical in ((person, "交谈", "chat"), (contact, "通话", "call")):
        (row,) = select_start(bindings, start_arguments([target], ["interact"], [way]))
        assert row["selected_action"]["canonical_action"] == canonical
    for target, verb, way in ((contact, "interact", "交谈"), (person, "interact", "通话"),
                              ("人物：陌生人", "interact", "交谈")):
        assert select_start(bindings, start_arguments([target], [verb], [way])) is None
