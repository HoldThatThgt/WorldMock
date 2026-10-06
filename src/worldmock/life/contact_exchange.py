"""@brief 与场景联系人的往来（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「联系人往来」）。

发给场景联系人的消息记入 AgentState.outbox。联系人回复请求增加 history（双方此前的消息按时间排序，至多 20 条：人物发出的
取 outbox，联系人发来的取收件箱）与 received（联系人实际收到的物品：收货人是该联系人的已送达订单，以及当面给予的记录）；
另带这次来信的事实：发件人姓名、场景本地时刻与联系人的收货地名（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-联系人回复不虚构到场 ）。
回复策略与延迟送达不变。
"""

from __future__ import annotations

from worldmock.footprints.store import FootprintStore
from worldmock.life.models import WorldState
from worldmock.life.readable import clock_phrase, iso_time, portion_phrase
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.social import (HISTORY_LIMIT, Contact, IncomingMessage, Message, ModelReplyRequest, ReceivedItem,
                                   ReplyFacts, ReplyHistoryEntry, model_reply_request)


def record_outgoing(state: WorldState, agent_id: int, message: Message) -> WorldState:
    """@brief 把人物发给场景联系人的消息记入其 outbox。"""
    agent = state.agents[agent_id]
    changed = agent.model_copy(update={"outbox": {**agent.outbox, message.message_id: message}}, deep=True)
    return state.model_copy(update={"agents": {**state.agents, agent_id: changed}}, deep=True)


def reply_request(state: WorldState, store: FootprintStore, semantics: ScenarioSemantics, message: Message,
                  contact: Contact) -> ModelReplyRequest:
    """@brief model 策略联系人对 message 的回复请求：来信及其事实、双方此前的往来与联系人实际收到的物品。"""
    scenario = semantics.scenario
    incoming = IncomingMessage(message_id=message.message_id, sender_id=message.sender_id,
                               contact_id=contact.contact_id, text=message.text, received_at=message.created_at)
    agent_id = int(message.sender_id.split(":", 1)[1])
    return model_reply_request(incoming, _history(state, scenario, (agent_id, contact), message.message_id),
                               _received(state, store, semantics, contact.contact_id),
                               _facts(state, scenario, agent_id, contact))


def _facts(state: WorldState, scenario: RuntimeScenario, agent_id: int, contact: Contact) -> ReplyFacts:
    """@brief 这次来信的事实：写信人物的姓名、此刻（带时区的 ISO 8601）与联系人收货地点的名称（没有时为空）。"""
    place = contact.receive_location_id
    names = {item.location_id: item.name for item in scenario.locations}
    return ReplyFacts(sender_name=state.agents[agent_id].name, now=iso_time(state.current_time, scenario.timezone),
                      receive_location=names[place] if place is not None else None)


def _history(state: WorldState, scenario: RuntimeScenario, pair: tuple[int, Contact],
             current_id: str) -> tuple[ReplyHistoryEntry, ...]:
    """@brief 双方此前的消息（不含本次来信），按时刻与消息身份排序，只留最近 HISTORY_LIMIT 条。"""
    agent_id, contact = pair
    agent = state.agents[agent_id]
    sent = [(item, agent.name) for item in agent.outbox.values()
            if item.recipient_id == contact.contact_id and item.message_id != current_id]
    received = [(item, contact.salutation) for item in agent.inbox.values() if item.sender_id == contact.contact_id]
    rows = sorted(sent + received, key=lambda pair_: (pair_[0].created_at, pair_[0].message_id))[-HISTORY_LIMIT:]
    return tuple(ReplyHistoryEntry(speaker=speaker, text=item.text,
                                   time=clock_phrase(item.created_at, state.current_time, scenario.timezone))
                 for item, speaker in rows)


def _received(state: WorldState, store: FootprintStore, semantics: ScenarioSemantics,
              contact_id: str) -> tuple[ReceivedItem, ...]:
    """@brief 联系人实际收到的物品：收货人是它的已送达订单（送达时刻）与当面给予（给予时刻），按时刻排序。"""
    scenario = semantics.scenario
    names = {food.food_id: food.name for food in semantics.catalog.foods.values()}
    rows = [(order.expected_at, order.food_id, float(order.quantity)) for order in state.deliveries.values()
            if order.recipient_id == contact_id and order.status == "delivered"]
    for agent_id in state.agents:
        for event in store.read_agent_events("action_result", agent_id):
            given = [] if event.data.get("reused") else (event.data.get("data") or {}).get("given", [])
            rows.extend((event.occurred_at, item["food_id"], item["quantity"])
                        for item in given if item["recipient_id"] == contact_id)
    return tuple(ReceivedItem(item=names[product], quantity=portion_phrase(quantity),
                              time=clock_phrase(moment, state.current_time, scenario.timezone))
                 for moment, product, quantity in sorted(rows))
