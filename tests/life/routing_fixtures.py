"""来源、去向与个人事务测试共用的现行目录运行夹具。

夹具只使用登记 Catalog 的食品、店铺、应用和工作主题。测试改写世界时仍经真实存储提交，并在改变人物地点时同步重建
地点会话，避免位置与会话互相矛盾。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    activity_name,
    advance_next,
    conclude_request,
    context,
    pending_names,
    report_request,
    stage_day_plan,
    start_request,
    submit_round,
    wake_by_mail,
)
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.activity_control import PHASE_ACTIVE, pending_conclusions
from worldmock.life.engine import LifeEngine
from worldmock.life.errand_state import ERRAND_EVENT_KINDS
from worldmock.life.models import ActivityStatus, WorldState, active_activities
from worldmock.life.vocabulary import GIVE_WAY

#: 测试明确选择的当前地点、食品与应用身份。
HOME = "home"
OFFICE = "office"
FOOD_STORE = "noodle-1"
SNACK_ID = "chips"
SNACK_NAME = "薯片"
MEAL_ID = "beef-noodles"
MEAL_NAME = "牛肉面"
DRINK_ID = "lemon-tea"
DRINK_NAME = "柠檬茶"
CONTENT_APP_ID = "douyin"
CONTENT_APP_NAME = "抖音"

#: 两人场景里人物 2 眼中的人物 1，以及人物 1 眼中的人物 2。
FIRST_PERSON_TARGET = "人物：Test Person"
PERSON_TARGET = "人物：Second Test Person"
#: 夹具加给陈曦的同事联系人王磊：不是同世界人物，收货地点是办公室。
COLLEAGUE = "contact-chenxi-wanglei"
COLLEAGUE_TARGET = "联系人：王磊"
#: 陈曦通讯录里与同世界人物 2 同名的同事联系人：没有收货地点。
DUPLICATE_CONTACT = "contact-chenxi-colleague"
DUPLICATE_TARGET = "联系人：李凡"

WAIT_ITEM = ("自己", "interact", "等待", None, None)
EAT_SNACK_ITEM = (f"持有物：{SNACK_NAME}", "consume", "食用", None, None)
EAT_MEAL_ITEM = (f"持有物：{MEAL_NAME}", "consume", "食用", None, None)
SETTLEMENT_KINDS = ("errand_progressed", "errand_completed")


def with_colleague(document: dict) -> None:
    """@brief 场景副本里给陈曦加同事联系人王磊。"""
    agent = next(item for item in document["agents"] if item["agent_id"] == 1)
    agent.setdefault("contacts", []).append({
        "contact_id": COLLEAGUE,
        "salutation": "王磊",
        "relation": "同事",
        "closeness": 16,
        "channels": ["workplace-IM"],
        "reply_policy": "none",
        "receive_location_id": OFFICE,
    })


def open_world(engine_factory, mutate=with_colleague) -> LifeEngine:
    """@brief 建立当前双人场景，登记空计划并推进到店铺可用的十点。"""
    engine = engine_factory("multi-person.json", mutate=mutate)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    zone = ZoneInfo(engine.context.scenario.timezone)
    now = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())

    def change(state: WorldState) -> None:
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = now
            agent.next_decision_at = now
        state.agents[1].inventory = {}

    stage(engine, change)
    return engine


def stage(engine: LifeEngine, change) -> None:
    """@brief 改写世界副本并经严格模型和存储提交。"""
    staged = engine.context.state.model_copy(deep=True)
    replacement = change(staged)
    if replacement is not None:
        staged = replacement
    staged.version += 1
    staged = WorldState.model_validate(staged.model_dump())
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event",
        occurred_at=staged.current_time,
        visibility="system",
        source_kind="scenario",
        data={"kind": "test_routing_staged"},
    )])
    engine.context.state = staged


def place(engine: LifeEngine, agent_id: int, location: str,
          holdings: dict[str, float] | None = None) -> None:
    """@brief 改变人物位置与可选持有物，并为所有人物重建一致的地点会话。"""
    catalog = engine.context.semantics.catalog
    place_names = {identity: item.display_name for identity, item in catalog.locations.items()}

    def change(state: WorldState) -> WorldState:
        changed = sessions.end_all(state)
        agent = changed.agents[agent_id]
        agent.location_id = location
        if holdings is not None:
            agent.inventory = {location: dict(holdings)}
        for identity in sorted(changed.agents):
            changed = sessions.open_stay(changed, identity, place_names)
        return changed

    stage(engine, change)


def stage_person(engine: LifeEngine, location: str, energy: float,
                 inventory: dict | None = None) -> None:
    """@brief 把人物置于当前日十点半并提交实际能量、持有物、位置和会话。"""
    now = int(engine.context.scenario.start_at.timestamp()) + 10 * 3600 + 1800

    def change(state: WorldState) -> None:
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = now
            agent.next_decision_at = now
        state.agents[1].body.energy_kcal = energy

    stage(engine, change)
    place(engine, 1, location, inventory or {})


def keep_waiting(engine: LifeEngine) -> tuple[str, object]:
    """@brief 给刚结束的活动收尾；有活动就播报，否则开始等待。"""
    waiting = pending_conclusions(engine.context.state, 1)
    if waiting:
        names = [activity_name(engine, item.activity_id) for item in waiting]
        return "conclude_activity", conclude_request(*names)
    running = active_activities(engine.context.state, 1)
    if running:
        return "report_status", report_request(activity_name(engine, running[0].activity_id), summary="接着等。")
    return "start_activity", start_request(targets=["自己"], actions=["interact"], ways=["等待"])


def act(engine: LifeEngine, name: str, request, agent_id: int = 1,
        tool: str = "start_activity"):
    """@brief 等到人物可决策，照常处理同秒其他人物并提交一个真实动作。"""
    run_until(engine, lambda current: agent_id in current.scheduling_state().ready_agent_ids
              and not pending_conclusions(current.context.state, agent_id))
    ready = engine.scheduling_state().ready_agent_ids
    others = {other: _keep_busy(engine, other, f"{name}-{other}")
              for other in ready if other != agent_id}
    _, results = submit_round(engine, {agent_id: (tool, request), **others}, name)
    return next(item for item in results if item.agent_id == agent_id)


def run_until(engine: LifeEngine, done, limit: int = 96) -> None:
    """@brief 只经真实决策轮和时间边界推进世界，直到条件成立。"""
    for index in range(limit):
        if done(engine):
            return
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            advance_next(engine)
            continue
        label = f"{engine.context.state.version}-{index}"
        submit_round(engine, {agent_id: _keep_busy(engine, agent_id, label)
                              for agent_id in ready}, f"keep-busy-{label}")
    raise AssertionError("condition was not reached")


def later(engine: LifeEngine) -> None:
    """@brief 推进到严格未来的下一个结算边界。"""
    start = engine.context.state.current_time
    run_until(engine, lambda current: current.context.state.current_time > start)


def _keep_busy(engine: LifeEngine, agent_id: int, label: str) -> tuple:
    """@brief 收尾刚结束活动、播报进行中活动，否则开始等待。"""
    observed = engine.observe(context(engine, f"peek-{label}-{agent_id}", agent_id))
    names = pending_names(observed)
    if names:
        return "conclude_activity", conclude_request(*names)
    running = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_ACTIVE]
    if running:
        return "report_status", report_request(*running[:1], summary="接着做。")
    return "start_activity", start_request(targets=["自己"], actions=["interact"], ways=["等待"])


def contact_message(engine: LifeEngine, text: str, offset: int = 60,
                    contact_id: str = COLLEAGUE) -> str:
    """@brief 经现行预排消息路径向人物 1 送达一条联系人消息。"""
    from worldmock.life.social import Message
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

    message_id = f"reply-test-{engine.context.state.version}"

    def change(state: WorldState) -> None:
        due = state.current_time + offset
        message = Message(
            message_id=message_id,
            kind="direct",
            sender_id=contact_id,
            recipient_id="agent:1",
            text=text,
            created_at=due,
        )
        sequence = max((item.sequence for item in state.pending_events), default=0) + 1
        state.pending_events.append(ScheduledEvent(
            scheduled_id=f"contact-reply:{message_id}",
            kind=ScheduledKind.MESSAGE_ARRIVAL,
            due_at=due,
            sequence=sequence,
            agent_id=1,
            data={"message": message.model_dump(mode="json")},
        ))
        state.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))

    stage(engine, change)
    run_until(engine, lambda current: message_id in current.context.state.agents[1].inbox
              and 1 in current.scheduling_state().ready_agent_ids)
    return message_id


def routed(*items: tuple, answering: str | None = None):
    """@brief 逐项构造当前 start_activity 请求。"""
    return start_request(
        targets=[item[0] for item in items],
        actions=[item[1] for item in items],
        ways=[item[2] for item in items],
        from_=[item[3] for item in items],
        to=[item[4] for item in items],
        answering=answering,
    )


def observe(engine: LifeEngine, name: str, agent_id: int = 1) -> dict:
    """@brief 冻结一次本人观察，不登记动作。"""
    return engine.observe(context(engine, name, agent_id))


def supply_target(observed: dict, food_id: str = SNACK_ID,
                  store_id: str = FOOD_STORE) -> tuple[str, str]:
    """@brief 返回指定当前店铺食品的可读目标名和食品身份。"""
    object_id = f"{store_id}:{food_id}"
    row = next(row for row in observed["interaction_bindings"]
               if row["kind"] == "supply" and row["object_id"] == object_id)
    return row["object"], row["food_id"]


def holding_name(engine: LifeEngine, food_id: str) -> str:
    """@brief 当前目录食品的手边持有物目标名。"""
    return f"持有物：{engine.context.semantics.catalog.foods[food_id].name}"


def selected_food(engine: LifeEngine, food_id: str = MEAL_ID):
    """@brief 从当前登记目录返回测试明确选择的食品。"""
    return engine.context.semantics.catalog.foods[food_id]


def food_store_name(engine: LifeEngine, store_id: str = FOOD_STORE) -> str:
    """@brief 当前登记目录里的店铺显示名。"""
    return engine.context.semantics.catalog.locations[store_id].display_name


def buy_food(engine: LifeEngine, label: str, way: str = "到店", to: str | None = None,
             agent_id: int = 1, also: tuple = (), food_id: str = SNACK_ID,
             store_id: str = FOOD_STORE):
    """@brief 从指定登记店铺取得固定食品。"""
    target, _ = supply_target(observe(engine, f"{label}-purchase-preview", agent_id), food_id, store_id)
    item = (target, "obtain", way, food_store_name(engine, store_id), to)
    return act(engine, label, routed(item, *also), agent_id)


def errand_note(engine: LifeEngine, label: str, changes: list[dict], agent_id: int = 1):
    """@brief 结束一段真实等待并随收尾请求登记个人事务变更。"""
    started = act(engine, f"{label}-wait", routed(WAIT_ITEM), agent_id)
    assert started.status == "in_progress", (started.error_code, started.message)
    wake_by_mail(engine, agent_id, offset=60)
    run_until(engine, lambda current: agent_id in current.scheduling_state().ready_agent_ids)
    name = engine.context.state.activities[started.activity_id].name
    noted = act(engine, label, conclude_request(name, errands=changes), agent_id, tool="conclude_activity")
    assert noted.status == "completed", (noted.error_code, noted.message)
    return noted


def single_person_home(engine_factory, holdings: dict[str, float]):
    """@brief 当前单人场景在十二点半的真实家中世界。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = int(datetime.fromisoformat("2026-01-05T12:30:00+08:00").timestamp())

    def change(state: WorldState) -> None:
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        person = state.agents[1]
        person.body_settled_at = now
        person.next_decision_at = now

    stage(engine, change)
    place(engine, 1, HOME, holdings)
    return engine


def mark_ready(engine: LifeEngine, agent_id: int) -> None:
    """@brief 经存储提交让人物在当前时刻可决策。"""
    stage(engine, lambda state: setattr(state.agents[agent_id], "next_decision_at", state.current_time))


def prefer_first(engine: LifeEngine, agent_id: int) -> None:
    """@brief 仅增加版本直到人物排在当前稳定就绪顺序首位。"""
    while engine.scheduling_state().ready_agent_ids[0] != agent_id:
        stage(engine, lambda state: None)


def give_food(engine: LifeEngine, recipient: str, food_id: str = SNACK_ID):
    """@brief 把手边一份指定食品给予收货人。"""
    return routed((holding_name(engine, food_id), "consume", GIVE_WAY, None, recipient))


def errand_events(engine: LifeEngine, agent_id: int = 1,
                  kinds=ERRAND_EVENT_KINDS) -> list:
    """@brief 返回人物按事件顺序排列的个人事务事件。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", agent_id)
            if event.data.get("kind") in kinds]


def settlement_events(engine: LifeEngine, agent_id: int = 1) -> list:
    """@brief 返回人物的事务进展与完成事件。"""
    return errand_events(engine, agent_id, SETTLEMENT_KINDS)


def current_errand(engine: LifeEngine, agent_id: int = 1):
    """@brief 返回人物唯一未完成事务；没有时返回 None。"""
    errands = list(engine.context.state.agents[agent_id].errands.values())
    assert len(errands) <= 1
    return errands[0] if errands else None


def action_result_event(engine: LifeEngine, result, agent_id: int = 1):
    """@brief 返回动作受理时的第一条公开结果事件。"""
    return next(event for event in engine.context.store.read_agent_events("action_result", agent_id)
                if event.action_id == result.action_id)


def delivery_arrival(engine: LifeEngine, order_id: str):
    """@brief 返回包含指定订单入库行的配送到达事件。"""
    return next(event for agent_id in engine.context.state.agents
                for event in engine.context.store.read_agent_events("environment_event", agent_id)
                if event.data.get("kind") == "delivery_arrival"
                and order_id in [row["order_id"] for row in event.data["inventory_changes"]])


def activity_progress_events(engine: LifeEngine, activity_id: str) -> list:
    """@brief 返回指定活动的全部真实进度事件。"""
    return [event for event in engine.context.store.read_agent_events("activity_progress", 1)
            if event.activity_id == activity_id]


def stage_food_holding(engine: LifeEngine, food_id: str, quantity: float,
                       agent_id: int = 1) -> None:
    """@brief 在人物当前位置放置登记食品并持久提交。"""
    def change(state: WorldState) -> None:
        agent = state.agents[agent_id]
        inventory = {key: dict(value) for key, value in agent.inventory.items()}
        inventory.setdefault(agent.location_id, {})[food_id] = quantity
        agent.inventory = inventory

    stage(engine, change)


def decision_round(engine: LifeEngine, name: str, tool: str, request):
    """@brief 单人物按真实顺序观察、登记并结算一轮。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def start_eating(engine: LifeEngine, food, label: str):
    """@brief 从真实持有物目标开始食用登记食品。"""
    observed = engine.observe(context(engine, f"{label}-preview"))
    target = holding_name(engine, food.food_id)
    action = next(row for row in observed["tool_layer"]["targets"] if row["name"] == target)
    result = decision_round(engine, label, "start_activity", routed(
        (target, "consume", "食用", None, None)))[1]
    assert result.status == "in_progress", result.error_code
    selected = next(row for row in action["actions"] if row["way"] == "食用")
    return selected, result


def run_activity_out(engine: LifeEngine, activity_id: str, label: str) -> None:
    """@brief 只经真实时间边界与播报推进活动到终局。"""
    for step in range(96):
        if engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.context.state, 1)
            decision_round(
                engine,
                f"{label}-report-{step}",
                "report_status",
                report_request(*(item.name for item in running), summary="继续。"),
            )
        else:
            advance_next(engine)
    raise AssertionError("activity did not end at its declared boundaries")
