"""个人事务结算的幂等与先后经真实请求、引擎与 SQLite（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「先后」「只读一处」「结算函数」）。

结算证据只在一处读取、每次提交恰好结算一次：重放的动作结果（带 reused）不算；重复到期的配送不再产生入库行；并行开始的
合并结果只读顶层 orders，不再读逐项结果 activity_results 里的同一订单；决策轮提交与时钟推进提交各结算一次（数量为 3 的
事务两次各只加 1 份）；同一提交内只有排在事务新增事件之后的证据才算——两人同一轮里给予排在记下之前不算，记下排在给予
之前才算。
"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import conclude_request, context, errand_add, submit_round, wake_by_mail
from tests.life.routing_fixtures import (
    FIRST_PERSON_TARGET,
    FOOD_STORE,
    HOME,
    SNACK_ID,
    SNACK_NAME,
    WAIT_ITEM,
    action_result_event,
    act,
    buy_food,
    current_errand,
    delivery_arrival,
    errand_note,
    food_store_name,
    give_food,
    mark_ready,
    open_world,
    place,
    prefer_first,
    routed,
    run_until,
    settlement_events,
    single_person_home,
    stage,
)
from tests.life.test_errand_completion import assert_settlement
from worldmock.life.vocabulary import GIVE_WAY
from worldmock.life.models import active_activities


def test_a_replayed_action_adds_nothing(engine_factory) -> None:
    """「取得薯片 2 份」到店买到一份后，同一动作身份与同一参数的重放只返回存档结果：重放的 action_result 带 reused 与同一张
    已送达订单，但不再累计——仍只有一条 errand_progressed，累计量仍是 1 份，库存也只多一份。"""
    engine = single_person_home(engine_factory, {})
    place(engine, 1, FOOD_STORE)
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=2)])
    request = routed((SNACK_NAME, "obtain", "到店", food_store_name(engine), None))
    bought = act(engine, "buy", request)
    [progressed] = settlement_events(engine)
    replay = context(engine, "replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(replay, bought.action_id, "start_activity", request))
    assert engine.commit_decision_round("replay") == [bought]
    [replayed] = [event for event in engine.context.store.read_agent_events("action_result", 1)
                  if event.action_id == bought.action_id and event.data.get("reused")]
    assert replayed.data["data"]["orders"] == bought.data["orders"]
    assert bought.data["orders"][0]["status"] == "delivered"
    assert settlement_events(engine) == [progressed] and current_errand(engine).progress == 1.0
    assert engine.context.state.agents[1].inventory[FOOD_STORE][SNACK_ID] == 1.0


def test_a_repeated_due_delivery_adds_nothing(engine_factory) -> None:
    """「取得薯片 2 份」远程下单的那份送达后，同一订单的配送事件再到期一次：送达事件照写（订单已送达），但没有新的入库
    行，事务不再累计，家里也不多一份。"""
    engine = single_person_home(engine_factory, {})
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=2)])
    ordered = buy_food(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    scheduled = next(item for item in engine.context.state.pending_events
                     if item.kind.value == "delivery_arrival" and order["order_id"] in item.data["order_ids"])
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    [progressed] = settlement_events(engine)
    assert_settlement(progressed, delivery_arrival(engine, order["order_id"]), "errand_progressed", 1.0)
    again = engine.context.state.current_time + 120

    def repeat(state):
        """同一配送事件换一个预排身份，两分钟后再到期一次。"""
        sequence = max((item.sequence for item in state.pending_events), default=0) + 1
        state.pending_events.append(scheduled.model_copy(update={
            "scheduled_id": f"{scheduled.scheduled_id}:again", "due_at": again, "sequence": sequence}))
        state.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    stage(engine, repeat)
    run_until(engine, lambda current: current.context.state.current_time >= again)
    arrivals = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                if event.data.get("kind") == "delivery_arrival"]
    assert [[row["status"] for row in event.data["orders"]] for event in arrivals] == [["delivered"], ["delivered"]]
    assert [len(event.data["inventory_changes"]) for event in arrivals] == [1, 0]
    assert settlement_events(engine) == [progressed] and current_errand(engine).progress == 1.0
    assert engine.context.state.agents[1].inventory[HOME][SNACK_ID] == 1.0


def test_a_parallel_start_counts_each_order_once(engine_factory) -> None:
    """同一请求到店买一份薯片并开始等待（逐项派发）：合并结果的顶层 orders 与逐项结果 activity_results 里是同一张订单，
    结算只读顶层，「取得薯片 2 份」只加 1 份，一条 errand_progressed 以该请求的结果事件为原因。"""
    engine = single_person_home(engine_factory, {})
    place(engine, 1, FOOD_STORE)
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=2)])
    started = buy_food(engine, "both", also=(WAIT_ITEM,))
    assert started.status == "in_progress"
    [order] = started.data["orders"]
    assert [row["orders"] for row in started.data["activity_results"] if "orders" in row] == [[order]]
    [progressed] = settlement_events(engine)
    assert_settlement(progressed, action_result_event(engine, started), "errand_progressed", 1.0)
    assert current_errand(engine).progress == 1.0


def test_each_commit_settles_once(engine_factory) -> None:
    """「取得薯片 3 份」：先远程下单一份（在途），同一秒到店买下一份——这次决策轮提交里恰好一条 errand_progressed，累计
    1 份；送达那次时钟推进提交里也恰好一条 errand_progressed，累计 2 份，事务仍未完成（数量为 3：同一提交若结算两次，
    这一步就会多加到办成）。"""
    engine = single_person_home(engine_factory, {})
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=3)])
    ordered = buy_food(engine, "order", way="远程下单")
    [order] = ordered.data["orders"]
    place(engine, 1, FOOD_STORE)
    bought = buy_food(engine, "buy")
    evidence = action_result_event(engine, bought)
    in_round = [event for event in settlement_events(engine) if event.state_version_after == evidence.state_version_after]
    assert [event.data["kind"] for event in in_round] == ["errand_progressed"] and current_errand(engine).progress == 1.0
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    arrival = delivery_arrival(engine, order["order_id"])
    in_advance = [event for event in settlement_events(engine) if event.state_version_after == arrival.state_version_after]
    assert [event.data["kind"] for event in in_advance] == ["errand_progressed"]
    assert_settlement(in_advance[0], arrival, "errand_progressed", 1.0)
    assert (current_errand(engine).progress, current_errand(engine).quantity) == (2.0, 3)
    assert len(settlement_events(engine)) == 2


def test_evidence_ordered_before_the_noting_event_in_the_same_commit_does_not_count(engine_factory) -> None:
    """两人同一轮：李凡当面给陈曦一份薯片，陈曦收尾时记下「取得薯片 1 份」。李凡排在前面时，给予的结果事件在本次提交
    里排在 errand_registered 之前，不算——事务保持未完成、累计为零、没有结算事件（薯片照样到她手上）；陈曦排在前面时，
    给予排在新增事件之后，同一提交里事务办成，以给予的结果事件为原因。先后按事件顺序，不按时刻（两者同一秒）。"""
    outcomes = {}
    for giver in (2, 1):
        engine = open_world(engine_factory)
        place(engine, 2, HOME, {SNACK_ID: 1.0})
        waited = act(engine, f"wait-{giver}", routed(WAIT_ITEM))
        wake_by_mail(engine, 1, offset=60)
        run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
        mark_ready(engine, 2)
        prefer_first(engine, giver)
        name = engine.context.state.activities[waited.activity_id].name
        noting = conclude_request(name, errands=[errand_add("买薯片", SNACK_NAME)])
        _, results = submit_round(engine, {1: ("conclude_activity", noting), 2: ("start_activity", give_food(engine, FIRST_PERSON_TARGET))},
                                  f"together-{giver}")
        assert all(result.status == "completed" for result in results)
        handed = next(result for result in results if result.agent_id == 2)
        gift = action_result_event(engine, handed, agent_id=2)
        [registered] = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                        if event.data.get("kind") == "errand_registered"]
        assert gift.occurred_at == registered.occurred_at
        assert (gift.event_seq < registered.event_seq) == (giver == 2)
        assert engine.context.state.agents[1].inventory[HOME][SNACK_ID] == 1.0
        outcomes[giver] = (current_errand(engine), settlement_events(engine), gift)
    errand, events, _ = outcomes[2]
    assert (errand.name, errand.progress) == ("买薯片", 0.0) and events == []
    errand, [completed], gift = outcomes[1]
    assert errand is None
    assert_settlement(completed, gift, "errand_completed", 1.0)


def test_a_parallel_start_counts_each_gift_once(engine_factory) -> None:
    """同一请求把一份薯片当面给陈曦并开始等待（逐项派发）：合并结果的顶层 given 与逐项结果 activity_results 里是同一条
    给予记录，结算只读顶层，陈曦的「取得薯片 2 份」只加 1 份——一条 errand_progressed 以李凡这次请求的结果事件为原因，
    事务仍未完成，薯片到她手上的也只有一份。"""
    engine = open_world(engine_factory)
    place(engine, 2, HOME, {SNACK_ID: 1.0})
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=2)])
    mark_ready(engine, 2)
    [waiting] = active_activities(engine.context.state, 2)
    ended = act(engine, "end-wait", conclude_request(waiting.name), agent_id=2, tool="conclude_activity")
    assert ended.status == "completed", (ended.error_code, ended.message)
    handed = act(engine, "hand", routed((f"持有物：{SNACK_NAME}", "consume", GIVE_WAY, None, FIRST_PERSON_TARGET), WAIT_ITEM), agent_id=2)
    assert handed.status == "in_progress", (handed.error_code, handed.message)
    [gift] = handed.data["given"]
    assert (gift["recipient_id"], gift["food_id"], gift["quantity"]) == ("agent:1", SNACK_ID, 1.0)
    assert [row["given"] for row in handed.data["activity_results"] if "given" in row] == [[gift]]
    [progressed] = settlement_events(engine)
    assert_settlement(progressed, action_result_event(engine, handed, agent_id=2), "errand_progressed", 1.0)
    assert progressed.agent_id == 1 and (current_errand(engine).progress, current_errand(engine).quantity) == (1.0, 2)
    assert engine.context.state.agents[1].inventory[HOME][SNACK_ID] == 1.0 and settlement_events(engine, agent_id=2) == []


def test_two_evidence_events_in_one_commit_are_credited_in_event_order(engine_factory) -> None:
    """结算按草稿先后扫描、命中即累加（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算 「结算函数」）。两人都在集市，陈曦记下「取得
    薯片 2 份」；同一轮里她到店买下一份、李凡当面给她一份，两条动作结果都是她这条事务的证据、落在同一次提交。排在前面
    的那条结果让事务进展到 1 份，排在后面的那条让它办成：errand_progressed 以较早的结果事件为原因，errand_completed 以较晚
    的为原因，两条结算事件也按这个先后落库，各与自己的证据同一提交。"""
    engine = open_world(engine_factory)
    place(engine, 1, FOOD_STORE)
    place(engine, 2, FOOD_STORE, {SNACK_ID: 1.0})
    errand_note(engine, "note", [errand_add("囤薯片", SNACK_NAME, quantity=2)])
    mark_ready(engine, 2)
    assert set(engine.scheduling_state().ready_agent_ids) == {1, 2}
    purchase = routed((SNACK_NAME, "obtain", "到店", food_store_name(engine), None))
    _, results = submit_round(engine, {1: ("start_activity", purchase), 2: ("start_activity", give_food(engine, FIRST_PERSON_TARGET))},
                              "buy-and-gift")
    assert [result.status for result in results] == ["completed", "completed"], [
        (result.error_code, result.message) for result in results]
    earlier, later = sorted((action_result_event(engine, result, agent_id=result.agent_id) for result in results),
                            key=lambda event: event.event_seq)
    progressed, completed = settlement_events(engine)
    assert_settlement(progressed, earlier, "errand_progressed", 1.0)
    assert_settlement(completed, later, "errand_completed", 1.0)
    assert [event.data["errand"]["progress"] for event in (progressed, completed)] == [1.0, 2.0]
    assert later.event_seq < progressed.event_seq < completed.event_seq
    assert engine.context.state.agents[1].errands == {} and engine.context.state.agents[1].inventory[FOOD_STORE][SNACK_ID] == 2.0
