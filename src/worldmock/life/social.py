"""@brief 社交最小可行版的纯函数域：在场可见、消息、交谈、通话与联系人回复。

`docs/dev/SPEC-life-runtime.md`「社交最小可行版」的域校验、可见性投影与
记录结构。本模块只产出结构与判定，不写足迹库、
不调用模型、不结算资源；登记投递与独立模型调用由引擎层接线完成。
可见性闭集（「社交最小可行版」）：他人观察只暴露姓名、活动家族与可交谈状态，
不暴露身体数值、余额、任务或记忆。
"""

from __future__ import annotations

from collections.abc import Collection, Container
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 回复策略闭集（「社交最小可行版」）。
ReplyPolicy = Literal["none", "template", "model"]
#: 收件箱消息种类：直发消息与交谈邀请（「社交最小可行版」）。
MessageKind = Literal["direct", "chat_invite"]

#: 消息文本上限（「社交最小可行版」：不超过 300 汉字；按字符数计，超出即拒绝）。
MESSAGE_TEXT_MAX_CHARS = 300
#: model 策略回复文本上限（「社交最小可行版」：不超过 200 汉字）。
MODEL_REPLY_MAX_CHARS = 200
#: 通话时长闭区间端点（分钟，含端点，「社交最小可行版」）。
CALL_MIN_MINUTES = 10
CALL_MAX_MINUTES = 60
#: experiment_assumption：交谈被接受时双方社交需要缓解速率（约定值，非文献常数）。
CHAT_RELIEF_ACCEPTED_PER_HOUR = 0.5
#: experiment_assumption：交谈被拒绝时发起方社交需要缓解速率（约定值，非文献常数）。
CHAT_RELIEF_DECLINED_PER_HOUR = 0.2
#: experiment_assumption：通话社交需要缓解速率（约定值，非文献常数）。
CALL_RELIEF_PER_HOUR = 0.4
#: 联系人回复来源标记（「社交最小可行版」）。足迹 EventDraft.source_kind 冻结枚举不含
#: model_npc，落库时的映射由引擎层负责，本模块只产出记录结构。
REPLY_SOURCE_KIND = "model_npc"
#: template 策略的占位回复文本；正式模板渲染由引擎与语料层完成。
TEMPLATE_REPLY_TEXT = "（模板自动回复）"
#: experiment_assumption：联系人回复生成后到送达收件人的延迟（秒，实验常量；docs/dev/SPEC-time-and-energy.md
#: 第一章「人物之间的消息」，裁决 #21）。回复登记为预排送达事件，到期写入收件箱并以 message_arrival 唤醒。
CONTACT_REPLY_DELAY_SECONDS = 300


class _SocialModel(BaseModel):
    """@brief 社交域记录基类：字段封闭、不可变。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Contact(_SocialModel):
    """@brief 场景联系人：按人物声明（「社交最小可行版」与 `docs/dev/SPEC-life-realism.md`「世界素材品类要求」）。

    亲密度沿用 Humanoid Agents 的 closeness 0-30 口径；channels 为可用
    联系渠道（如电话、短信），由场景声明，引擎据此约束可达性。
    """

    contact_id: str
    salutation: str
    relation: str
    closeness: int = Field(ge=0, le=30)
    channels: tuple[str, ...]
    reply_policy: ReplyPolicy


class AgentPresence(_SocialModel):
    """@brief 同世界其他人物的在场事实（引擎观察侧输入）。

    awake 与 actively_gaming 由引擎按活动状态推导；本模块只消费判定。
    """

    agent_id: str
    name: str
    location: str
    activity_family: str
    awake: bool
    actively_gaming: bool


class PersonView(_SocialModel):
    """@brief 他人观察的可见投影：不透明身份 + 姓名、活动家族、可交谈。

    隐私闭集（「社交最小可行版」）限定他人只暴露姓名、活动家族与可交谈状态，不暴露
    身体数值、余额、任务或记忆；person_id 是动作目标身份锚点（message/chat/call
    动作的身份枚举用本轮观察，见
    `docs/dev/SPEC-life-runtime.md`「观察投影与提示合同」），不是隐私
    泄漏——scene 渲染仍只用姓名（含活动家族短语），身份只进 facts 层。
    """

    person_id: str
    name: str
    activity_family: str
    can_talk: bool


class Message(_SocialModel):
    """@brief 收件箱消息：直发消息或交谈邀请。"""

    message_id: str
    kind: MessageKind
    sender_id: str
    recipient_id: str
    text: str
    created_at: int


class MessageArrivalEvent(_SocialModel):
    """@brief message_arrival 送达记录结构（引擎落库为 environment_event）。

    本模块只产出结构不写库；inbox 在收件方下一次观察时可见（「社交最小可行版」）。
    """

    event_type: Literal["environment_event"] = "environment_event"
    kind: Literal["message_arrival"] = "message_arrival"
    message_id: str
    recipient_id: str
    occurred_at: int


class OutgoingMessage(_SocialModel):
    """@brief message 动作 / chat 邀请的动作请求：消息身份与时间由引擎赋予。"""

    message_id: str
    sender_id: str
    recipient_id: str
    text: str
    created_at: int


class SentMessage(_SocialModel):
    """@brief 已登记的瞬时消息与其送达记录。"""

    message: Message
    arrival: MessageArrivalEvent


class ChatStart(_SocialModel):
    """@brief 交谈发起结果：按发起方的前台活动结算（引擎活动通道执行）。

    对方对发起方选择交谈即接受（docs/dev/SPEC-time-and-energy.md 第五章「交谈」）；缓解按双方交谈同时进行的秒数
    由 social_actions 结算。
    """

    activity_kind: Literal["chat"] = "chat"
    partner_id: str
    duration_minutes: int
    invite_message: Message
    arrival: MessageArrivalEvent


class CallStart(_SocialModel):
    """@brief 通话发起结果：占用注意力的前台活动（面向场景联系人，「社交最小可行版」）。"""

    activity_kind: Literal["call"] = "call"
    contact_id: str
    duration_minutes: int
    attention_occupied: Literal[True] = True
    relief_per_hour: float


class IncomingMessage(_SocialModel):
    """@brief 联系人收到的人物消息；message_id 即回复幂等键（「社交最小可行版」）。"""

    message_id: str
    sender_id: str
    contact_id: str
    text: str
    received_at: int


class ModelReplyRequest(_SocialModel):
    """@brief model 策略回复请求：引擎据此发起独立 DeepSeek 调用并存档。"""

    contact_id: str
    reply_to_message_id: str
    recipient_id: str
    incoming_text: str
    thinking: Literal["disabled"] = "disabled"
    max_chars: int = MODEL_REPLY_MAX_CHARS


class ContactReply(_SocialModel):
    """@brief 联系人回复记录：只进入收件箱，不改任何资源或任务（「社交最小可行版」）。"""

    message: Message
    source_kind: Literal["model_npc"] = REPLY_SOURCE_KIND


def can_talk(presence: AgentPresence) -> bool:
    """@brief 可交谈判定：清醒且非主动游戏（「社交最小可行版」）。"""
    return presence.awake and not presence.actively_gaming


def people_here(agent_location: str,
                other_agents: list[AgentPresence]) -> list[PersonView]:
    """@brief 同一地点他人的可见投影（隐私闭集 + 身份锚点，「社交最小可行版」）。

    只暴露不透明身份、姓名、当前活动家族与可交谈状态；不暴露身体数值、
    余额、任务或记忆。身份（agent:<id> 不透明形式）供工具身份枚举与配对
    校验使用（见 `docs/dev/SPEC-life-runtime.md`「观察投影与提示合同」），scene 渲染仍只用姓名。列表保持输入顺序。
    """
    return [
        PersonView(
            person_id=item.agent_id,
            name=item.name,
            activity_family=item.activity_family,
            can_talk=can_talk(item),
        )
        for item in other_agents
        if item.location == agent_location
    ]


def _ensure_text_limit(text: str, limit: int) -> None:
    """@brief 校验文本不超过上限字符数，超出即拒绝（fail-closed）。"""
    if len(text) > limit:
        raise ValueError(f"text exceeds {limit} characters: got {len(text)}")


def send_message(outgoing: OutgoingMessage, world_agent_ids: Collection[str],
                 contacts: Collection[Contact]) -> SentMessage:
    """@brief 瞬时消息域校验并产出登记记录与 message_arrival 送达结构。

    收件人必须存在（同世界人物或场景联系人）；文本不超过 300 汉字。
    瞬时动作语义：本函数只登记，收件方在下一次观察的 inbox 看到送达
    （「社交最小可行版」）；写库由引擎层完成。
    """
    known = set(world_agent_ids) | {item.contact_id for item in contacts}
    if outgoing.recipient_id not in known:
        raise ValueError(f"recipient not found: {outgoing.recipient_id}")
    _ensure_text_limit(outgoing.text, MESSAGE_TEXT_MAX_CHARS)
    message = Message(kind="direct", **outgoing.model_dump())
    arrival = MessageArrivalEvent(
        message_id=outgoing.message_id,
        recipient_id=outgoing.recipient_id,
        occurred_at=outgoing.created_at,
    )
    return SentMessage(message=message, arrival=arrival)


def start_chat(initiator_location: str, partner: AgentPresence,
               duration_minutes: int, invite: OutgoingMessage) -> ChatStart:
    """@brief 交谈域校验：共同在场且对方可交谈；产出前台活动与邀请。

    邀请以 chat_invite 消息送达对方（「社交最小可行版」）；对方对发起方选择交谈即接受，缓解按双方交谈同时进行的
    秒数结算（docs/dev/SPEC-time-and-energy.md 第五章「交谈」），活动本体按发起方前台活动由引擎结算。
    """
    if partner.location != initiator_location:
        raise ValueError(
            f"chat requires co-presence: initiator at {initiator_location}, "
            f"partner {partner.agent_id} at {partner.location}")
    if not can_talk(partner):
        raise ValueError(f"chat partner cannot talk: {partner.agent_id}")
    if duration_minutes <= 0:
        raise ValueError(f"chat duration must be positive: {duration_minutes}")
    if invite.recipient_id != partner.agent_id:
        raise ValueError(
            f"chat invite recipient must be the partner: {invite.recipient_id}")
    _ensure_text_limit(invite.text, MESSAGE_TEXT_MAX_CHARS)
    invite_message = Message(kind="chat_invite", **invite.model_dump())
    arrival = MessageArrivalEvent(
        message_id=invite.message_id,
        recipient_id=invite.recipient_id,
        occurred_at=invite.created_at,
    )
    return ChatStart(
        partner_id=partner.agent_id,
        duration_minutes=duration_minutes,
        invite_message=invite_message,
        arrival=arrival,
    )


def start_call(contact: Contact, duration_minutes: int) -> CallStart:
    """@brief 通话域校验：面向场景联系人，10-60 分钟，占用注意力，0.4/小时。

    联系人类型即场景联系人的类型约束；时长闭区间含端点（「社交最小可行版」）。
    """
    if not CALL_MIN_MINUTES <= duration_minutes <= CALL_MAX_MINUTES:
        raise ValueError(
            f"call duration must be {CALL_MIN_MINUTES}-{CALL_MAX_MINUTES} "
            f"minutes: got {duration_minutes}")
    return CallStart(
        contact_id=contact.contact_id,
        duration_minutes=duration_minutes,
        relief_per_hour=CALL_RELIEF_PER_HOUR,
    )


def needs_reply(policy: ReplyPolicy, message_id: str,
                replied_message_ids: Container[str]) -> bool:
    """@brief 联系人回复判定：策略开启且该消息尚未回复过（最多一次幂等）。

    幂等键为收件消息的 message_id。「社交最小可行版」只显式要求 model 策略最多一次；
    template 策略沿用同一道门，避免同一消息的占位回复重复进入收件箱。
    """
    if policy == "none":
        return False
    return message_id not in replied_message_ids


def model_reply_request(incoming: IncomingMessage) -> ModelReplyRequest:
    """@brief 产出 model 策略的独立模型调用请求（实际调用由引擎层负责）。"""
    return ModelReplyRequest(
        contact_id=incoming.contact_id,
        reply_to_message_id=incoming.message_id,
        recipient_id=incoming.sender_id,
        incoming_text=incoming.text,
    )


def finalize_model_reply(request: ModelReplyRequest, text: str,
                         reply_message_id: str, created_at: int) -> ContactReply:
    """@brief 以模型返回文本落成回复记录：校验 ≤200 汉字并标记 model_npc。"""
    _ensure_text_limit(text, MODEL_REPLY_MAX_CHARS)
    message = Message(
        message_id=reply_message_id,
        kind="direct",
        sender_id=request.contact_id,
        recipient_id=request.recipient_id,
        text=text,
        created_at=created_at,
    )
    return ContactReply(message=message)


def template_reply(incoming: IncomingMessage, reply_message_id: str,
                   created_at: int) -> ContactReply:
    """@brief template 策略的占位回复记录：只进入收件箱，不触发模型调用。"""
    message = Message(
        message_id=reply_message_id,
        kind="direct",
        sender_id=incoming.contact_id,
        recipient_id=incoming.sender_id,
        text=TEMPLATE_REPLY_TEXT,
        created_at=created_at,
    )
    return ContactReply(message=message)
