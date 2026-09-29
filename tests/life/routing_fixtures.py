"""来源与去向、给予与代点关联测试共用的真实运行夹具（docs/dev/SPEC-persona-prompt.md 第五章）。

multi-person 场景：陈曦（人物 1）与李凡（人物 2）是同世界人物，开场都在住处；陈曦的通讯录里与李凡同名的同事联系人
（contact-chenxi-colleague）没有收货地点（同世界人物经人物身份收货）。夹具在场景副本里给陈曦加一位不是同世界人物的
同事联系人王磊（收货地点是办公室），经存储提交改写后的世界（版本连续），只替换测试要控制的事实。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import (advance_next, conclude_request, context, pending_names, stage_day_plan, start_request,
                                 submit_round, report_request)
from worldmock.footprints.models import EventDraft
from worldmock.life.activity_control import PHASE_ACTIVE, pending_conclusions
from worldmock.life.engine import LifeEngine

#: 夹具加给陈曦的同事联系人王磊：不是同世界人物，收货地点是办公室。
COLLEAGUE = "contact-chenxi-wanglei"
COLLEAGUE_TARGET = "联系人：王磊"
#: 陈曦通讯录里与同世界人物 2 同名的同事联系人：没有收货地点。
DUPLICATE_CONTACT, DUPLICATE_TARGET = "contact-chenxi-colleague", "联系人：李凡"
PERSON_TARGET = "人物：李凡"
OFFICE, HOME, MARKET = "office", "home", "market"


def with_colleague(document: dict) -> None:
    """@brief 场景副本里给陈曦加同事联系人王磊（收货地点是办公室）。"""
    agent = next(item for item in document["agents"] if item["agent_id"] == 1)
    agent["contacts"].append({"contact_id": COLLEAGUE, "salutation": "王磊", "relation": "同事", "closeness": 16,
                              "channels": ["workplace-IM"], "reply_policy": "none", "receive_location_id": OFFICE})


def open_world(engine_factory, mutate=with_colleague) -> LifeEngine:
    """@brief 在 mutate 改写的 multi-person 场景副本上，两人都登记空计划，时间移到 1 月 5 日 10:00（门店营业），人物 1
    刚看过集市的三件食品（它们每轮都在工具层里，不受轮转影响）。"""
    engine = engine_factory("multi-person.json", mutate=mutate)
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    zone = ZoneInfo(engine.scenario.timezone)
    now = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())

    def change(state):
        """@brief 推到营业时段并让人物 1 看过全部门店供给。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        visible = {item.supply_id for item in engine.scenario.supplies if item.location_id == MARKET
                   and "eat" in engine.rules.items[item.product_id].use_modes}
        visible = set(sorted(visible)[:3])
        for agent in state.agents.values():
            agent.body_settled_at = now
            agent.next_decision_at = now
        first = state.agents[1]
        first.visible_supply_ids = visible
        first.supply_browsed_at = {key: now for key in visible}
        first.supply_browsed_location = {key: first.location_id for key in visible}
    stage(engine, change)
    return engine


def stage(engine: LifeEngine, change) -> None:
    """@brief 在世界副本上执行 change(state) 后经存储提交（版本加一）。"""
    staged = engine.state.model_copy(deep=True)
    change(staged)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        source_kind="scenario", data={"kind": "test_routing_staged"})])
    engine.state = staged


def place(engine: LifeEngine, agent_id: int, location: str, holdings: dict[str, float] | None = None) -> None:
    """@brief 把人物放到某地；holdings 给出时替换为该地点手边的持有物。"""
    def change(state):
        """@brief 改人物位置与手边持有物。"""
        agent = state.agents[agent_id]
        agent.location_id = location
        if holdings is not None:
            agent.inventory = {location: dict(holdings)}
    stage(engine, change)


def act(engine: LifeEngine, name: str, request, agent_id: int = 1, tool: str = "start_activity"):
    """@brief 等到该人物可以决策且没有待收尾活动，真实观察、登记并结算一轮，返回该人物的结果；同一秒就绪的其他人物
    照常过日子（_keep_busy）。"""
    run_until(engine, lambda current: agent_id in current.scheduling_state().ready_agent_ids
              and not pending_conclusions(current.state, agent_id))
    ready = engine.scheduling_state().ready_agent_ids
    others = {other: _keep_busy(engine, other, f"{name}-{other}") for other in ready if other != agent_id}
    _, results = submit_round(engine, {agent_id: (tool, request), **others}, name)
    return next(item for item in results if item.agent_id == agent_id)


def run_until(engine: LifeEngine, done, limit: int = 40) -> None:
    """@brief 推进世界直到 done(engine) 成立：就绪的人物照常过日子（_keep_busy），没人就绪就推进到下一边界。"""
    for index in range(limit):
        if done(engine):
            return
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            advance_next(engine)
            continue
        label = f"{engine.state.version}-{index}"
        submit_round(engine, {agent_id: _keep_busy(engine, agent_id, label) for agent_id in ready},
                     f"keep-busy-{label}")
    raise AssertionError("condition was not reached")


def later(engine: LifeEngine) -> None:
    """@brief 推进到下一个结算边界（同一秒的决策轮数随之归零），人物 1 在那时又看了一眼先前看过的供给。"""
    start = engine.state.current_time
    run_until(engine, lambda current: current.state.current_time > start)

    def change(state):
        """@brief 刷新人物 1 看过的供给的浏览时刻与地点。"""
        agent = state.agents[1]
        agent.supply_browsed_at = {key: state.current_time for key in agent.visible_supply_ids}
        agent.supply_browsed_location = {key: agent.location_id for key in agent.visible_supply_ids}
    stage(engine, change)


def _keep_busy(engine: LifeEngine, agent_id: int, label: str) -> tuple:
    """@brief 让一个就绪人物继续过日子：先给刚结束的活动收尾，有进行中的活动就播报它，否则开始等待。"""
    observed = engine.observe(context(engine, f"peek-{label}-{agent_id}", agent_id))
    names = pending_names(observed)
    if names:
        return "conclude_activity", conclude_request(*names)
    running = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_ACTIVE]
    if running:
        return "report_status", report_request(*running[:1], summary="接着做。")
    return "start_activity", start_request(targets=["自己"], actions=["interact"], ways=["等待"])


def contact_message(engine: LifeEngine, text: str, offset: int = 60, contact_id: str = COLLEAGUE) -> str:
    """@brief 联系人的一条直接消息在 offset 秒后送达人物 1（与联系人回复同一条预排送达路径）；推进到送达，返回消息身份。"""
    from worldmock.life.social import Message
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

    message_id = f"reply-test-{engine.state.version}"

    def change(state):
        """@brief 登记预排送达。"""
        due = state.current_time + offset
        message = Message(message_id=message_id, kind="direct", sender_id=contact_id, recipient_id="agent:1",
                          text=text, created_at=due)
        sequence = max((item.sequence for item in state.pending_events), default=0) + 1
        state.pending_events.append(ScheduledEvent(
            scheduled_id=f"contact-reply:{message_id}", kind=ScheduledKind.MESSAGE_ARRIVAL, due_at=due,
            sequence=sequence, agent_id=1, data={"message": message.model_dump(mode="json")}))
        state.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    stage(engine, change)
    run_until(engine, lambda current: message_id in current.state.agents[1].inbox
              and 1 in current.scheduling_state().ready_agent_ids)
    return message_id


def routed(*items: tuple, answering: str | None = None):
    """@brief 逐项（对象，动词，方式，来源，去向）构造 start_activity 请求。"""
    return start_request(targets=[item[0] for item in items], actions=[item[1] for item in items],
                         ways=[item[2] for item in items], from_=[item[3] for item in items],
                         to=[item[4] for item in items], answering=answering)


def observe(engine: LifeEngine, name: str, agent_id: int = 1) -> dict:
    """@brief 冻结一次本人观察（不登记动作）。"""
    return engine.observe(context(engine, name, agent_id))


def supply_target(observed: dict, product_id: str | None = None) -> tuple[str, str]:
    """@brief 本轮一个能远程下单的集市供给：（目标名，商品身份）。"""
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and any(action["label"] == "远程下单" for action in row["actions"])
               and (product_id is None or row["object_id"].endswith(product_id)))
    return row["object"], row["object_id"].removeprefix("supply-market-")


def holding_name(engine: LifeEngine, product_id: str) -> str:
    """@brief 手边持有物目标名「持有物：<商品>」。"""
    return f"持有物：{engine.rules.items[product_id].name}"


def food_product(engine: LifeEngine) -> str:
    """@brief 集市里一件可以吃的商品身份（夹具统一用它）。"""
    return next(item.product_id for item in engine.scenario.supplies if item.location_id == MARKET
                and "eat" in engine.rules.items[item.product_id].use_modes)
