"""个人事务的完成依据与结算经真实请求、引擎与 SQLite（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 、2.7）。

正式场景（单人白领与两人的 multi-person）建立的 LifeEngine 上，人物以收尾请求的 errands 记下事务，随后经真实的购买、配送、
给予、制作与进食产生结算证据。事务只按证据累计：动作结果顶层已送达的订单（到店成交）、配送送达事件的新入库行、当面给予
（都按收货人归属），进食区间的实际食用量与制作完整结束时的产出；下单受理、未送达的订单、开始进食、本人买给别人或联系人的、
制作未完成、推进了当日目标的活动、声称办成的自述、记下前已持有的与记下前开始的进度区间都不算。累计达到数量减 1e-9 即办成并移出待办层，不足时保持未完成并记下累计量；
缺货、闭店或买不起被拒时事务与累计量不变；办成之后名称与商品可以再记一条新事务；同一轮里先给予、后取消时证据不落到
已取消的事务。errand_progressed（系统可见）与 errand_completed（人物可见）以证据事件为唯一原因、与证据同一提交，
人物为事务所有人，决策、动作与活动身份为空。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.life.conftest import (advance_next, conclude_request, errand_add, errand_cancel, report_request,
                                 stage_day_plan, stage_errands, submit_round, wake_by_mail)
from tests.life.routing_fixtures import (COLLEAGUE, COLLEAGUE_TARGET, HOME, MARKET, OFFICE, PERSON_TARGET, act, observe,
                                         open_world, place, routed, run_until, stage, with_colleague)
from tests.life.test_errand_todo_layer import _errand
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.errand_state import ERRAND_EVENT_KINDS
from worldmock.life.vocabulary import GIVE_WAY
from worldmock.life.models import ActivityStatus, active_activities
from worldmock.life.readable import iso_time, local_date
from worldmock.life.schedule import PlanGoal
from worldmock.life.storefront_state import current_quote

#: 正式目录里的纯牛奶：商品名唯一（三家店有售），集市可到店买、可远程下单送到家；整份喝完要 900 秒。
MILK, MILK_ID = "纯牛奶", "food-6923644266066"
#: 食材包与它制作的产物（制作 3000 秒，完整结束时一次扣一包、得一份）。
KIT, KIT_NAME, MEAL, MEAL_NAME = "ingredient-vegetable-kit", "素炒什锦食材包", "meal-0030", "素炒什锦"
#: 两人场景里人物 2（李凡）眼中的人物 1（陈曦）。
CHEN = "人物：陈曦"
WAIT = ("自己", "interact", "等待", None, None)
DRINK = (f"持有物：{MILK}", "consume", "食用", None, None)
#: 两种结算事件。
SETTLEMENT_KINDS = ("errand_progressed", "errand_completed")


def market(engine) -> str:
    """集市的地点名（指名购买写在 from 里）。"""
    return next(item.name for item in engine.context.scenario.locations if item.location_id == MARKET)


def buy(engine, label: str, way: str = "到店", to: str | None = None, agent_id: int = 1, also: tuple = ()):
    """指名在集市买一份纯牛奶（到店或远程下单，to 为收货人），also 是同一请求里的其他项；返回该人物的结果。"""
    return act(engine, label, routed((MILK, "obtain", way, market(engine), to), *also), agent_id)


def note(engine, label: str, changes: list[dict], agent_id: int = 1):
    """人物开始一段等待，一分钟后（一封邮件给的决策机会）结束它并在收尾请求里记下事务变更；同一秒仍可再决策。"""
    started = act(engine, f"{label}-wait", routed(WAIT), agent_id)
    assert started.status == "in_progress", (started.error_code, started.message)
    wake_by_mail(engine, agent_id, offset=60)
    run_until(engine, lambda current: agent_id in current.scheduling_state().ready_agent_ids)
    name = engine.context.state.activities[started.activity_id].name
    noted = act(engine, label, conclude_request(name, errands=changes), agent_id, tool="conclude_activity")
    assert noted.status == "completed", (noted.error_code, noted.message)
    assert agent_id in engine.scheduling_state().ready_agent_ids
    return noted


def home(engine_factory, holdings: dict[str, float]):
    """单人白领场景：1 月 5 日 12:30 人在家、当日计划已登记，家里只有给定的持有物（经存储提交，版本连续）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = int(datetime.fromisoformat("2026-01-05T12:30:00+08:00").timestamp())

    def change(state):
        """推到 12:30 并放好持有物。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        person = state.agents[1]
        person.location_id, person.body_settled_at, person.next_decision_at = HOME, now, now
        person.inventory = {HOME: dict(holdings)}
    stage(engine, change)
    assert engine.context.rules.items[MILK_ID].name == MILK and engine.context.rules.items[MEAL].name == MEAL_NAME
    return engine


def ready(engine, agent_id: int) -> None:
    """让人物此刻就有决策机会（经存储提交）。"""
    stage(engine, lambda state: setattr(state.agents[agent_id], "next_decision_at", state.current_time))


def first(engine, agent_id: int) -> None:
    """让同一秒就绪的人物里 agent_id 排在最前结算：就绪顺序是随机状态、时刻与世界版本确定的洗牌，这里只追加不改世界
    事实的提交（版本加一）直到它排在前面。"""
    while engine.scheduling_state().ready_agent_ids[0] != agent_id:
        stage(engine, lambda state: None)


def give(engine, recipient: str):
    """把手边的一份纯牛奶给 recipient 的请求。"""
    return routed((f"持有物：{MILK}", "consume", GIVE_WAY, None, recipient))


def errand_events(engine, agent_id: int = 1, kinds=ERRAND_EVENT_KINDS) -> list:
    """人物的事务事件（按事件顺序），可按 kind 筛选。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", agent_id)
            if event.data.get("kind") in kinds]


def settlement(engine, agent_id: int = 1) -> list:
    """人物的结算事件 errand_progressed 与 errand_completed（按事件顺序）。"""
    return errand_events(engine, agent_id, SETTLEMENT_KINDS)


def held(engine, agent_id: int = 1):
    """人物唯一的未完成事务（没有为 None）。"""
    errands = list(engine.context.state.agents[agent_id].errands.values())
    assert len(errands) <= 1
    return errands[0] if errands else None


def result_event(engine, result, agent_id: int = 1):
    """动作的第一条 action_result 事件（受理时的即时结果）。"""
    return next(event for event in engine.context.store.read_agent_events("action_result", agent_id)
                if event.action_id == result.action_id)


def arrival_of(engine, order_id: str):
    """送达该订单的配送事件（入库行里有它的那一次）。"""
    return next(event for agent_id in engine.context.state.agents
                for event in engine.context.store.read_agent_events("environment_event", agent_id)
                if event.data.get("kind") == "delivery_arrival"
                and order_id in [row["order_id"] for row in event.data["inventory_changes"]])


def progress_events(engine, activity_id: str) -> list:
    """一项活动的进度事件（按事件顺序）。"""
    return [event for event in engine.context.store.read_agent_events("activity_progress", 1) if event.activity_id == activity_id]


def assert_settlement(event, evidence, kind: str, added: float) -> None:
    """一条结算事件：所需 kind，environment_event、来源为环境，办成人物可见、进展系统可见；决策、动作与活动身份为空，唯一
    原因是证据事件，时刻与所在提交都与证据相同；载荷只有 kind、事务快照与本条证据的数量。"""
    assert (event.data["kind"], event.data["quantity_added"]) == (kind, added)
    assert (event.event_type, event.source_kind) == ("environment_event", "environment")
    assert event.visibility == ("agent" if kind == "errand_completed" else "system")
    assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
    assert event.caused_by == [evidence.event_id]
    assert (event.occurred_at, event.state_version_after) == (evidence.occurred_at, evidence.state_version_after)
    assert set(event.data) == {"kind", "errand", "quantity_added"}


def todo_rows(observed: dict) -> list[tuple[str, str, str]]:
    """观察待办层的事务行（名称，要办成什么，已办到哪）。"""
    return [(row["name"], row["done_when"], row["progress"]) for row in observed["todo_layer"]["errands"]]


def test_a_remote_order_completes_only_in_the_commit_that_delivers_it(engine_factory) -> None:
    """远程下单受理时订单在途（pending）：受理所在的提交不推进事务，待办层仍写「已取得 0 份」；配送送达的那次时钟推进
    提交里，送达事件的新入库行（收货人是本人）让事务办成——errand_completed 以送达事件为原因、与它同一提交，事务移出
    状态，待办层不再列出它，进度层有一条只写名称的办成记录。"""
    engine = open_world(engine_factory)
    note(engine, "note", [errand_add("买牛奶", MILK)])
    ordered = buy(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    assert (ordered.status, order["status"], order["recipient_id"]) == ("completed", "pending", "agent:1")
    assert held(engine).progress == 0.0 and settlement(engine) == []
    assert todo_rows(observe(engine, "ordered")) == [("买牛奶", "取得纯牛奶 1 份", "已取得 0 份")]
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    arrival = arrival_of(engine, order["order_id"])
    [completed] = settlement(engine)
    assert_settlement(completed, arrival, "errand_completed", 1.0)
    assert completed.agent_id == 1 and completed.data["errand"]["progress"] == 1.0
    assert engine.context.state.agents[1].errands == {}
    after = observe(engine, "delivered")
    assert after["todo_layer"]["errands"] == []
    record = {"occurred_at": iso_time(arrival.occurred_at, engine.context.scenario.timezone), "type": "errand_completed",
              "errand": "买牛奶"}
    assert record in after["progress_layer"]["events"] and completed.event_id in after["progress_event_ids"]


def test_noting_then_buying_at_the_store_in_the_same_second_completes_while_the_item_stays_there(
        engine_factory) -> None:
    """人物在集市：收尾时记下「取得纯牛奶 1 份」，同一秒再到店买下一份——决策不耗模拟时间，到店成交的动作结果顶层订单
    已送达，买下的那次提交里事务办成；物品留在门店（库存在集市、家里没有），完成依据不含地点条件。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    noted = note(engine, "note", [errand_add("买牛奶", MILK)])
    bought = buy(engine, "buy")
    [order] = bought.data["orders"]
    assert (bought.status, order["status"], order["fulfillment"]) == ("completed", "delivered", "pickup")
    assert bought.occurred_at == noted.occurred_at == engine.context.state.current_time
    [completed] = settlement(engine)
    assert_settlement(completed, result_event(engine, bought), "errand_completed", 1.0)
    assert engine.context.state.agents[1].errands == {}
    inventory = engine.context.state.agents[1].inventory
    assert inventory[MARKET][MILK_ID] == 1.0 and MILK_ID not in inventory.get(HOME, {})


def test_quantity_two_with_one_obtained_stays_open_and_shows_the_progress(engine_factory) -> None:
    """「取得纯牛奶 2 份」只到店买到一份：同一提交写一条 errand_progressed（只读结果事件，同一次购买的受理事件不另算），
    事务仍未完成、累计量 1 份存进状态，下一次观察的待办层写「已取得 1 份」，进度层没有办成记录。之后把买到的这一份
    喝掉，取得的累计量不回退，也没有新的结算事件。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("囤牛奶", MILK, quantity=2)])
    bought = buy(engine, "buy")
    [progressed] = settlement(engine)
    assert_settlement(progressed, result_event(engine, bought), "errand_progressed", 1.0)
    errand = held(engine)
    assert (errand.progress, errand.quantity) == (1.0, 2)
    assert progressed.data["errand"] == errand.model_dump(mode="json")
    observed = observe(engine, "after")
    assert todo_rows(observed) == [("囤牛奶", "取得纯牛奶 2 份", "已取得 1 份")]
    assert not [record for record in observed["progress_layer"]["events"] if record["type"] == "errand_completed"]
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    run_until(engine, lambda current: current.context.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    assert engine.context.state.agents[1].inventory[MARKET][MILK_ID] == 0.0
    assert settlement(engine) == [progressed] and held(engine).progress == 1.0


def test_what_was_held_before_noting_does_not_count(engine_factory) -> None:
    """记下时家里已有两份纯牛奶：记下不算取得，事务保持「已取得 0 份」，没有结算事件；之后远程下单送到的那一份才算，
    办成事件只加 1 份（家里共有 3 份）。"""
    engine = open_world(engine_factory)
    place(engine, 1, HOME, {MILK_ID: 2.0})
    note(engine, "note", [errand_add("买牛奶", MILK)])
    assert held(engine).progress == 0.0 and settlement(engine) == []
    assert todo_rows(observe(engine, "noted")) == [("买牛奶", "取得纯牛奶 1 份", "已取得 0 份")]
    ordered = buy(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    [completed] = settlement(engine)
    assert_settlement(completed, arrival_of(engine, order["order_id"]), "errand_completed", 1.0)
    assert completed.data["errand"]["progress"] == 1.0
    assert engine.context.state.agents[1].inventory[HOME][MILK_ID] == 3.0


def test_a_progress_interval_begun_before_noting_does_not_count(engine_factory) -> None:
    """活动进度类证据要求结算区间的起点不早于记下的时刻。请求造不出这种情形（每个边界都把进行中的活动结算到此刻，记下之后
    的区间都从记下时刻或更晚开始），这里经真实时钟推进、以预置状态造出：人物从 t0 起喝一份纯牛奶，同一秒预置一条「吃掉
    纯牛奶 1 份」的事务，记下时刻在 t0 之后、下一边界之前；该边界的进度区间起点 t0 早于记下时刻，喝下的三分之一份不算；
    其后从边界起算的区间照算，喝完时累计三分之二份，事务仍未完成。"""
    engine = home(engine_factory, {MILK_ID: 2.0})
    start = engine.context.state.current_time
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    stage_errands(engine, 1, [_errand("staged:errand:0", "喝牛奶", MILK_ID, start + 100, outcome="eat")])
    wake_by_mail(engine, 1, offset=300)
    advance_next(engine)
    [before] = progress_events(engine, drinking.activity_id)
    [item] = before.data["consumption"]["items"]
    assert before.data["interval_start"] == start and round(item["interval_quantity"], 9) == round(1 / 3, 9)
    assert settlement(engine) == [] and held(engine).progress == 0.0
    run_until(engine, lambda current: current.context.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    counted = progress_events(engine, drinking.activity_id)[1:]
    assert counted and all(event.data["interval_start"] >= start + 300 for event in counted)
    events = settlement(engine)
    assert [event.caused_by for event in events] == [[event.event_id] for event in counted]
    assert {event.data["kind"] for event in events} == {"errand_progressed"}
    assert held(engine).progress == round(2 / 3, 9)


def test_bought_for_me_and_handed_to_me_count_but_bought_by_me_for_another_does_not(engine_factory) -> None:
    """两人都在家，陈曦记下「取得纯牛奶 3 份」：李凡当面给她一份（动作结果顶层 given，收货人是她）、远程下单送她一份（送达
    事件的人物字段是付款人李凡，入库行经订单取收货人）都算她取得；她远程下单送给李凡的那份送到李凡手上，不算她取得。
    两条 errand_progressed 都归陈曦、各以给予结果与送她那单的送达事件为原因，累计 2 份仍未完成；李凡没有任何结算事件。"""
    engine = open_world(engine_factory)
    place(engine, 2, HOME, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("囤牛奶", MILK, quantity=3)])
    for_li = buy(engine, "for-li", way="远程下单", to=PERSON_TARGET)
    ready(engine, 2)
    handed = act(engine, "hand", give(engine, CHEN), agent_id=2)
    assert handed.status == "completed" and handed.data["given"][0]["recipient_id"] == "agent:1"
    ready(engine, 2)
    for_chen = buy(engine, "for-chen", way="远程下单", to=CHEN, agent_id=2)
    orders = [for_li.data["orders"][0], for_chen.data["orders"][0]]
    assert [(order["agent_id"], order["recipient_id"]) for order in orders] == [(1, "agent:2"), (2, "agent:1")]
    run_until(engine, lambda current: all(current.context.state.deliveries[order["order_id"]].status == "delivered"
                                          for order in orders))
    gift = result_event(engine, handed, agent_id=2)
    events = settlement(engine)
    assert [event.caused_by for event in events] == [[gift.event_id],
                                                      [arrival_of(engine, orders[1]["order_id"]).event_id]]
    assert [(event.agent_id, event.data["kind"]) for event in events] == [(1, "errand_progressed")] * 2
    assert (held(engine).progress, held(engine).quantity) == (2.0, 3)
    assert engine.context.state.agents[2].inventory[HOME][MILK_ID] == 1.0 and settlement(engine, agent_id=2) == []


def test_an_order_and_a_gift_for_a_contact_outside_the_world_count_for_nobody(engine_factory) -> None:
    """陈曦记下「取得纯牛奶 1 份」后，远程下单送一份给同事联系人王磊（不是同世界人物，收货地点是办公室），又在办公室把
    手边的一份当面给他：送达的订单与给予的收货人都是联系人，不算任何人物取得——事务仍是「已取得 0 份」，两人都没有结算
    事件，牛奶记在王磊的持有上。"""
    engine = open_world(engine_factory)
    note(engine, "note", [errand_add("买牛奶", MILK)])
    ordered = buy(engine, "for-wang", way="远程下单", to=COLLEAGUE_TARGET)
    [order] = ordered.data["orders"]
    assert (ordered.status, order["recipient_id"]) == ("completed", COLLEAGUE)
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids
              and not active_activities(current.context.state, 1) and not pending_conclusions(current.context.state, 1))
    place(engine, 1, OFFICE, {MILK_ID: 1.0})
    handed = act(engine, "hand", give(engine, COLLEAGUE_TARGET))
    assert handed.status == "completed", (handed.error_code, handed.message)
    assert handed.data["given"][0]["recipient_id"] == COLLEAGUE
    assert engine.context.state.contact_holdings[COLLEAGUE] == {MILK_ID: 2.0}
    assert held(engine).progress == 0.0 and settlement(engine) == [] and settlement(engine, agent_id=2) == []
    assert todo_rows(observe(engine, "after")) == [("买牛奶", "取得纯牛奶 1 份", "已取得 0 份")]


def test_unfinished_making_does_not_count_and_the_finished_output_does(engine_factory) -> None:
    """人物开始用食材包制作素炒什锦（3000 秒），一分钟后播报时记下「取得素炒什锦 1 份」：之后每个未完成制作的进度区间都
    没有库存转换，不算取得；制作完整结束的那次进度结算带库存转换的产出，事务以它为原因办成，家里有了一份素炒什锦。"""
    engine = home(engine_factory, {KIT: 1.0})
    making = act(engine, "make", routed((f"持有物：{KIT_NAME}", "consume", "制作", None, None)))
    assert making.status == "in_progress", (making.error_code, making.message)
    wake_by_mail(engine, 1, offset=60)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
    name = engine.context.state.activities[making.activity_id].name
    noted = act(engine, "note", report_request(name, errands=[errand_add("做一份菜", MEAL_NAME)]), tool="report_status")
    assert noted.status == "completed"
    run_until(engine, lambda current: current.context.state.activities[making.activity_id].status != ActivityStatus.ACTIVE)
    after = [event for event in progress_events(engine, making.activity_id) if event.occurred_at > noted.occurred_at]
    unfinished = [event for event in after if "inventory_transform" not in event.data["consumption"]]
    finished = [event for event in after if "inventory_transform" in event.data["consumption"]]
    assert len(unfinished) >= 2 and len(finished) == 1 and finished[0] == after[-1]
    assert finished[0].data["activity"]["status"] == "completed"
    [completed] = settlement(engine)
    assert_settlement(completed, finished[0], "errand_completed", 1.0)
    assert engine.context.state.agents[1].inventory[HOME][MEAL] == 1.0 and engine.context.state.agents[1].errands == {}


def test_eating_counts_the_actual_interval_quantity(engine_factory) -> None:
    """「吃掉纯牛奶 1 份」：开始喝的那次受理（活动开始）不算；喝到一半（450/900 秒）的边界写 errand_progressed 加 0.5 份，
    喝完的边界再加 0.5 份办成——按进度结算的本区间实际食用量累计，不是按份数一次算。累计量达到数量减 1e-9 即办成：再记下
    「吃掉纯牛奶 2 份」并把累计量预置为 0.999999999，又喝下两个 0.5 份后累计 1.999999999，仍算办成，办成事件的快照写
    最终累计量。"""
    engine = home(engine_factory, {MILK_ID: 2.0})
    note(engine, "note", [errand_add("喝牛奶", MILK, outcome="吃掉")])
    first_drink = _drink_with_a_break(engine, "first")
    whole = settlement(engine)
    assert _assert_eaten(engine, first_drink, whole, 0.0) == 1.0
    assert len(whole) >= 2 and whole[0].data["quantity_added"] == 0.5 and engine.context.state.agents[1].errands == {}
    note(engine, "again", [errand_add("喝牛奶", MILK, outcome="吃掉", quantity=2)])
    stage_errands(engine, 1, [held(engine).model_copy(update={"progress": 0.999999999})])
    second_drink = _drink_with_a_break(engine, "second")
    near = settlement(engine)[len(whole):]
    assert _assert_eaten(engine, second_drink, near, 0.999999999) == 1.999999999
    assert near[-1].data["errand"]["quantity"] == 2 and engine.context.state.agents[1].errands == {}


def _drink_with_a_break(engine, label: str) -> str:
    """同一秒开始喝一份纯牛奶，以一封邮件在喝到一半（450 秒）时给一次决策机会，之后经真实边界喝完；返回活动身份。"""
    drinking = act(engine, f"{label}-drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    wake_by_mail(engine, 1, offset=450)
    run_until(engine, lambda current: current.context.state.activities[drinking.activity_id].status != ActivityStatus.ACTIVE)
    return drinking.activity_id


def _assert_eaten(engine, activity_id: str, events: list, progress: float) -> float:
    """一份纯牛奶的各进度区间与结算事件一一对应：每条以该区间的进度事件为原因、加本区间实际食用量（九位小数），快照的累计
    量逐条累加；最后一条办成，其余是进展。返回最终累计量。"""
    eaten = [event for event in progress_events(engine, activity_id)
             if event.data["consumption"]["items"][0]["interval_quantity"] > 0]
    assert len(events) == len(eaten) and eaten[0].data["consumption"]["items"][0]["interval_quantity"] == 0.5
    for index, (event, evidence) in enumerate(zip(events, eaten)):
        added = round(evidence.data["consumption"]["items"][0]["interval_quantity"], 9)
        progress = round(progress + added, 9)
        assert_settlement(event, evidence, SETTLEMENT_KINDS[index == len(events) - 1], added)
        assert event.data["errand"]["progress"] == progress
    return progress


def test_a_progressed_goal_and_a_claimed_completion_leave_the_errand_open(engine_factory) -> None:
    """目标 progressed 与任何自述都不是完成依据（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「不算」）：人物收尾时记下「取得纯牛奶
    1 份」，随后登记一条休息目标并开始等待。等待在下一边界推进了这条目标（推进它的活动就是这次等待），她当秒播报等待，
    自述「买牛奶这件事办成了」；等待又结算了一个区间之后，她收尾时再这样自述一遍。事务始终未完成、累计 0 份，没有任何结算
    事件，待办层照旧列着它，进度层没有办成记录。"""
    engine = home(engine_factory, {})
    note(engine, "note", [errand_add("买牛奶", MILK)])
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="歇一歇", registered_at=engine.context.state.current_time),))
    waited = act(engine, "wait", routed(WAIT))
    assert waited.status == "in_progress", (waited.error_code, waited.message)
    name, claim = engine.context.state.activities[waited.activity_id].name, "买牛奶这件事办成了，纯牛奶已经买到手。"
    _mail_chance(engine)
    [goal] = engine.context.state.agents[1].day_plan.goals
    assert goal.progressed_at == engine.context.state.current_time and goal.progress_activity_id == waited.activity_id
    reported = act(engine, "claim", report_request(name, summary=claim), tool="report_status")
    assert reported.status == "completed", (reported.error_code, reported.message)
    _mail_chance(engine)
    assert [event for event in progress_events(engine, waited.activity_id) if event.occurred_at > goal.progressed_at]
    concluded = act(engine, "done", conclude_request(name, summary=claim), tool="conclude_activity")
    assert concluded.status == "completed", (concluded.error_code, concluded.message)
    assert engine.context.state.activities[waited.activity_id].progress_note == claim
    assert settlement(engine) == [] and held(engine).progress == 0.0
    observed = observe(engine, "after")
    assert todo_rows(observed) == [("买牛奶", "取得纯牛奶 1 份", "已取得 0 份")]
    assert not [record for record in observed["progress_layer"]["events"] if record["type"] == "errand_completed"]


def _mail_chance(engine) -> None:
    """两分钟后一封邮件给人物 1 一次决策机会，推进到那个边界（期间进行中的活动照常结算一个区间）。"""
    at = wake_by_mail(engine, 1, offset=120)
    run_until(engine, lambda current: current.context.state.current_time == at
              and 1 in current.scheduling_state().ready_agent_ids)


def test_an_out_of_stock_rejection_leaves_the_errand_and_its_progress_unchanged(engine_factory) -> None:
    """「取得纯牛奶 2 份」到店买到一份后集市卖完了：再到店买以 OUT_OF_STOCK 业务拒绝，事务与累计量原样，没有新的事务事件；
    待办层照旧写「已取得 1 份」，行里只有六个键（不重算可用性，缺货原因只在该次购买的拒绝结果里）。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("囤牛奶", MILK, quantity=2)])
    buy(engine, "buy")
    before, events = held(engine), errand_events(engine)
    assert before.progress == 1.0
    stage(engine, lambda state: state.supply_stock.__setitem__(f"supply-market-{MILK_ID}", 0))
    refused = buy(engine, "sold-out")
    assert (refused.status, refused.error_code) == ("rejected", "OUT_OF_STOCK")
    assert held(engine) == before and errand_events(engine) == events
    [row] = observe(engine, "after")["todo_layer"]["errands"]
    assert list(row) == ["name", "done_when", "progress", "memo", "remind_time", "noted_on"]
    assert (row["name"], row["progress"]) == ("囤牛奶", "已取得 1 份")


def _little_money(document: dict) -> None:
    """场景副本：在 with_colleague 之上把陈曦的初始余额改为 1 元，买不起一份纯牛奶。"""
    with_colleague(document)
    next(item for item in document["agents"] if item["agent_id"] == 1).update(initial_balance_fen=100)


def test_a_closed_store_or_an_unaffordable_purchase_leaves_the_errand_open(engine_factory) -> None:
    """余额只有 1 元的人物在集市记下「取得纯牛奶 1 份」：集市当天闭店（世界里的营业例外）时到店买以 MERCHANT_CLOSED
    业务拒绝；撤掉例外重新开门后再买，价钱高于余额，以 INSUFFICIENT_FUNDS 业务拒绝。两次拒绝后事务原样未完成、没有新的
    事务事件，待办层照旧列着它，行里只有六个键（闭店与买不起的原因只在该次购买的拒绝结果里，待办层不重算可用性）。"""
    engine = open_world(engine_factory, mutate=_little_money)
    supply = f"supply-market-{MILK_ID}"
    price = engine.context.semantics.purchase_amount(MILK_ID, current_quote(engine.context.state, supply).price_fen, 1)
    assert price > engine.context.state.accounts["agent:1"] == 100
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("买牛奶", MILK)])
    before, events = held(engine), errand_events(engine)
    today = local_date(engine.context.state.current_time, engine.context.scenario.timezone)
    stage(engine, lambda state: state.opening_exceptions[MARKET].__setitem__(today, []))
    closed = buy(engine, "closed")
    assert (closed.status, closed.error_code) == ("rejected", "MERCHANT_CLOSED")
    assert held(engine) == before and errand_events(engine) == events
    stage(engine, lambda state: state.opening_exceptions[MARKET].pop(today))
    unaffordable = buy(engine, "unaffordable")
    assert (unaffordable.status, unaffordable.error_code) == ("rejected", "INSUFFICIENT_FUNDS")
    assert held(engine) == before and errand_events(engine) == events
    assert [event.data["kind"] for event in events] == ["errand_registered"]
    [row] = observe(engine, "after")["todo_layer"]["errands"]
    assert list(row) == ["name", "done_when", "progress", "memo", "remind_time", "noted_on"]
    assert (row["name"], row["done_when"], row["progress"]) == ("买牛奶", "取得纯牛奶 1 份", "已取得 0 份")


def test_a_completed_errand_frees_its_name_and_its_item_for_a_new_errand(engine_factory) -> None:
    """事务记下后不可改名，终结后名称可以再用：在集市记下「买牛奶」并当场买下办成后事务移出状态；之后以同一名称、同一结果
    与商品新增「取得纯牛奶 2 份」被受理，回执写这个名称，新事务从 0 份累计、来源是第二次收尾，事件依次是记下、办成、记下。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    note(engine, "note", [errand_add("买牛奶", MILK)])
    buy(engine, "buy")
    assert engine.context.state.agents[1].errands == {}
    again = note(engine, "again", [errand_add("买牛奶", MILK, quantity=2)])
    assert again.data["errands"] == [{"errand": "买牛奶", "change": "新增"}]
    errand = held(engine)
    assert (errand.name, errand.product_id, errand.quantity, errand.progress) == ("买牛奶", MILK_ID, 2, 0.0)
    assert errand.origin_action_id == again.action_id
    kinds = [event.data["kind"] for event in errand_events(engine)]
    assert kinds == ["errand_registered", "errand_completed", "errand_registered"]
    assert todo_rows(observe(engine, "after")) == [("买牛奶", "取得纯牛奶 2 份", "已取得 0 份")]


def test_a_gift_followed_by_a_cancel_in_one_round_credits_nothing(engine_factory) -> None:
    """陈曦先记下「取得纯牛奶 1 份」；之后同一轮里李凡排在前面当面给她一份，她排在后面收尾时取消这条事务：给予的动作结果
    在本次提交里排在取消之前，但被取消的事务不再接受该提交的任何证据——没有结算事件，事务只以 errand_cancelled 终结，
    牛奶照样到了她手上。"""
    engine = open_world(engine_factory)
    place(engine, 2, HOME, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("买牛奶", MILK)])
    waited = act(engine, "wait", routed(WAIT))
    wake_by_mail(engine, 1, offset=60)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
    ready(engine, 2)
    first(engine, 2)
    name = engine.context.state.activities[waited.activity_id].name
    cancel = conclude_request(name, errands=[errand_cancel("买牛奶", "李凡给了一盒")])
    _, results = submit_round(engine, {1: ("conclude_activity", cancel), 2: ("start_activity", give(engine, CHEN))},
                              "gift-and-cancel")
    handed, cancelled = sorted(results, key=lambda result: -result.agent_id)
    assert (handed.status, cancelled.status) == ("completed", "completed")
    assert cancelled.data["errands"] == [{"errand": "买牛奶", "change": "取消"}]
    events = errand_events(engine)
    assert [event.data["kind"] for event in events] == ["errand_registered", "errand_cancelled"]
    assert result_event(engine, handed, agent_id=2).event_seq < events[1].event_seq
    assert engine.context.state.agents[1].errands == {} and engine.context.state.agents[1].inventory[HOME][MILK_ID] == 1.0


def test_completion_and_progress_events_cite_their_evidence_and_carry_no_decision_identity(engine_factory) -> None:
    """「取得纯牛奶 2 份」：远程下单的那份送达时写 errand_progressed（系统可见），以送达事件为原因、与它同一次时钟推进提交；
    之后到店买下的那份写 errand_completed（人物可见），以到店购买的结果事件为原因、与它同一次决策轮提交。两条都归事务所有人，
    决策、动作与活动身份为空，载荷是 {kind, errand, quantity_added}：进展的快照就是累计后的状态，办成的快照是同一条事务、
    progress 为最终累计量。结算不改世界事实：两份牛奶各在家与集市，余额只因两次付款减少。"""
    engine = open_world(engine_factory)
    note(engine, "note", [errand_add("囤牛奶", MILK, quantity=2, memo="一份送到家，一份顺路买")])
    balance = engine.context.state.accounts["agent:1"]
    ordered = buy(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    [progressed] = settlement(engine)
    assert_settlement(progressed, arrival_of(engine, order["order_id"]), "errand_progressed", 1.0)
    assert progressed.agent_id == 1 and progressed.data["errand"] == held(engine).model_dump(mode="json")
    place(engine, 1, MARKET)
    bought = buy(engine, "buy")
    [_, completed] = settlement(engine)
    assert_settlement(completed, result_event(engine, bought), "errand_completed", 1.0)
    assert completed.agent_id == 1 and completed.data["errand"] == {**progressed.data["errand"], "progress": 2.0}
    inventory = engine.context.state.agents[1].inventory
    assert (inventory[HOME][MILK_ID], inventory[MARKET][MILK_ID]) == (1.0, 1.0)
    paid = order["total_price_fen"] + bought.data["orders"][0]["total_price_fen"]
    assert engine.context.state.accounts["agent:1"] == balance - paid


def test_progress_short_of_the_tolerance_stays_open(engine_factory) -> None:
    """容差只有 1e-9（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「累计与容差」），这里是不足的一侧：「吃掉纯牛奶 2 份」的累计量
    预置为 0.9999995，再经真实边界把一整份喝完（两个 0.5 份），累计 1.9999995，比数量减 1e-9 还少，事务保持未完成——每个
    食用区间各写一条 errand_progressed、没有办成事件，状态与最后一条快照都记下这个累计量，待办层照旧列着它。"""
    engine = home(engine_factory, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("喝牛奶", MILK, outcome="吃掉", quantity=2)])
    stage_errands(engine, 1, [held(engine).model_copy(update={"progress": 0.9999995})])
    drinking = _drink_with_a_break(engine, "drink")
    eaten = [event for event in progress_events(engine, drinking)
             if event.data["consumption"]["items"][0]["interval_quantity"] > 0]
    events = settlement(engine)
    assert len(eaten) >= 2 and [event.caused_by for event in events] == [[event.event_id] for event in eaten]
    assert [event.data["kind"] for event in events] == ["errand_progressed"] * len(eaten)
    assert round(sum(event.data["quantity_added"] for event in events), 9) == 1.0
    errand = held(engine)
    assert errand is not None and (errand.progress, errand.quantity) == (1.9999995, 2)
    assert events[-1].data["errand"] == errand.model_dump(mode="json")
    assert errand_events(engine, kinds=("errand_completed",)) == []
    [row] = observe(engine, "after")["todo_layer"]["errands"]
    assert (row["name"], row["done_when"]) == ("喝牛奶", "吃掉纯牛奶 2 份")


#: 「使用」方式的两种消费：用食材包制作素炒什锦（库存转换，产物入库），与使用一包纸巾（目录里只能使用的非食品，300 秒）。
_USED = [pytest.param(KIT, "制作", MEAL, id="making"), pytest.param("item-tissues", "使用", None, id="using")]


@pytest.mark.parametrize(("product_id", "way", "output"), _USED)
def test_making_or_using_an_item_is_not_eating_it(engine_factory, product_id: str, way: str, output) -> None:
    """「吃掉」的依据只是本人进食活动的食用量（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「依据」）：人物记下「吃掉<物品> 1 份」，
    随后把这件物品用掉——用食材包制作素炒什锦，或使用一包纸巾。活动完整结束的那次进度结算以「使用」方式写下该物品的
    本区间用量 1 份（制作另带库存转换），这不是进食：没有任何结算事件，事务仍未完成、累计 0 份，待办层照旧列着它；物品
    照样用掉，制作的产物照样入库。"""
    engine = home(engine_factory, {product_id: 1.0})
    name = engine.context.rules.items[product_id].name
    note(engine, "note", [errand_add("吃掉它", name, outcome="吃掉")])
    noted = held(engine)
    using = act(engine, "use", routed((f"持有物：{name}", "consume", way, None, None)))
    assert using.status == "in_progress", (using.error_code, using.message)
    run_until(engine, lambda current: current.context.state.activities[using.activity_id].status != ActivityStatus.ACTIVE,
              limit=80)
    finished = progress_events(engine, using.activity_id)[-1]
    consumption = finished.data["consumption"]
    assert consumption["use_mode"] == "use" and ("inventory_transform" in consumption) == (output is not None)
    assert [(item["product_id"], item["interval_quantity"]) for item in consumption["items"]] == [(product_id, 1.0)]
    assert finished.data["interval_start"] >= noted.registered_at
    inventory = engine.context.state.agents[1].inventory[HOME]
    assert inventory[product_id] == 0.0 and (output is None or inventory[output] == 1.0)
    assert settlement(engine) == [] and held(engine) == noted and noted.progress == 0.0
    assert todo_rows(observe(engine, "after")) == [("吃掉它", f"吃掉{name} 1 份", "已吃掉 0 份")]


def test_a_making_interval_begun_before_noting_yields_no_output_evidence(engine_factory) -> None:
    """制作产出也是活动进度类证据，同样要求结算区间的起点不早于记下的时刻（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「先后」）。
    与进食一样请求造不出这种情形，这里经真实时钟推进、以预置状态造出：人物用食材包制作素炒什锦，推进到制作结束前的最后
    一个边界，预置一条「取得素炒什锦 1 份」的事务，记下时刻在这个边界之后一秒。制作完整结束的那个区间起点早于记下时刻，
    它的产出不算：素炒什锦照样入库，没有结算事件，事务仍未完成、累计 0 份。"""
    engine = home(engine_factory, {KIT: 1.0})
    making = act(engine, "make", routed((f"持有物：{KIT_NAME}", "consume", "制作", None, None)))
    assert making.status == "in_progress", (making.error_code, making.message)
    end = engine.context.state.activities[making.activity_id].planned_end_at

    def last_boundary(current) -> bool:
        """没人要决策，且下一边界就是制作的计划结束时刻。"""
        state = current.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == end
    run_until(engine, last_boundary, limit=80)
    last = engine.context.state.current_time
    assert engine.context.state.activities[making.activity_id].last_settled_at <= last < end
    staged = _errand("staged:errand:0", "做一份菜", MEAL, last + 1)
    stage_errands(engine, 1, [staged])
    advance_next(engine)
    assert engine.context.state.current_time == end
    finished = progress_events(engine, making.activity_id)[-1]
    assert "inventory_transform" in finished.data["consumption"] and finished.data["activity"]["status"] == "completed"
    assert finished.data["interval_start"] <= last < staged.registered_at
    assert engine.context.state.agents[1].inventory[HOME][MEAL] == 1.0
    assert settlement(engine) == [] and held(engine) == staged and staged.progress == 0.0
