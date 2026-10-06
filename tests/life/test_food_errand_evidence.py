"""@brief 实际食品结算输出经事务证据提取与结算，归真实收货人并只累计实际区间份量。"""

from worldmock.footprints.models import EventDraft
from worldmock.life import errands
from worldmock.life.activities import ActivityOrigin, start_activity
from worldmock.life.activity_evidence import meal_consumption_data
from worldmock.life.basket import apply_meal_progress, open_meal, purchase_order
from worldmock.life.giving import give
from worldmock.life.models import ActivityStartParams, ConsumeItem, ConsumeRequest, PurchaseItem, PurchaseOrderRequest
from worldmock.life.models import WorldState
from worldmock.scheduling.effects import apply_scheduled_effect
from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world


def food_world(location="noodle-1"):
    """@brief 生产多人物场景的严格世界，人物同处而持有为空。"""
    semantics, state = registered_world(location, scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    agents = {identity: agent.model_copy(update={"location_id": location, "inventory": {}}, deep=True)
              for identity, agent in state.agents.items()}
    return semantics, WorldState.model_validate(state.model_copy(update={"agents": agents}).model_dump())


def note(state, semantics, food_id="beef-noodles", outcome="取得", quantity=2):
    """@brief 所有人通过真实事务名称绑定与新增入口记下同一食品结果。"""
    changes = [{"change": "新增", "name": "食品事务", "item": semantics.catalog.foods[food_id].name,
                "outcome": outcome, "quantity": quantity, "memo": None, "remind_time": None, "reason": None}]
    origin = errands.ErrandOrigin("note", "note-event", state.current_time, state.timezone)
    agents = {}
    for identity, agent in state.agents.items():
        observed = errands.bindings(agent, semantics.catalog.foods)
        agents[identity], _ = errands.apply_changes(agent, changes, observed, origin)
    return WorldState.model_validate(state.model_copy(update={"agents": agents}).model_dump())


def buy_request(recipient, destination="noodle-1", fulfillment="pickup"):
    """@brief 食品身份来自真实固定菜单，到店请求没有应用。"""
    return PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", food_id="beef-noodles", quantity=1,
                                                    recipient_id=f"agent:{recipient}"),),
                                destination_id=destination, fulfillment=fulfillment,
                                app_id="meituan" if fulfillment == "delivery" else None)


def result_draft(state, payload, reused=False):
    """@brief 使用真实购买或给予输出形成动作结果草稿，数据不改食品键。"""
    return EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                      data={"data": payload, "reused": reused})


def arrival_draft(state, payload):
    """@brief 使用真实到期处理的订单和新入库行形成配送草稿。"""
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
                      data={"kind": "delivery_arrival", **payload})


def progress(state, identity):
    """@brief 读取仍未完成的真实事务累计量。"""
    return next(iter(state.agents[identity].errands.values())).progress


def test_actual_pickup_credits_the_recipient_and_reused_result_cannot_credit_twice():
    semantics, state = food_world()
    state = note(state, semantics)
    recipient = next(identity for identity in state.agents if identity != 1)
    bought = purchase_order(state, semantics, 1, buy_request(recipient))
    draft = result_draft(bought.state, {"orders": [row.model_dump(mode="json") for row in bought.orders]})
    assert errands.evidence_rows(draft.event_type, draft.agent_id, draft.data) == [
        errands.Evidence(recipient, "obtain", "beef-noodles", 1.0, None)]
    settled, credits = errands.settle(bought.state, [draft])
    assert progress(settled, recipient) == 1 and progress(settled, 1) == 0
    assert credits[0].agent_id == recipient and credits[0].caused_by == [draft.event_id]
    replay = result_draft(settled, draft.data["data"], reused=True)
    assert errands.evidence_rows(replay.event_type, replay.agent_id, replay.data) == []
    replayed, repeated = errands.settle(WorldState.model_validate_json(settled.model_dump_json()), [replay])
    assert repeated == [] and progress(replayed, recipient) == 1
    second = purchase_order(replayed, semantics, 1, buy_request(recipient))
    finished, credits = errands.settle(second.state, [result_draft(second.state,
        {"orders": [row.model_dump(mode="json") for row in second.orders]})])
    assert not finished.agents[recipient].errands and progress(finished, 1) == 0
    assert [credit.data["kind"] for credit in credits] == ["errand_completed"]
    assert credits[0].data["errand"]["product_id"] == "beef-noodles"


def test_actual_giving_credits_only_the_recipient_and_actual_fraction():
    semantics, state = food_world("home")
    state = note(state, semantics, "chips", quantity=1)
    recipient = next(identity for identity in state.agents if identity != 1)
    giver = state.agents[1].model_copy(update={"inventory": {"home": {"chips": 0.5}}}, deep=True)
    state = WorldState.model_validate(state.model_copy(update={"agents": {**state.agents, 1: giver}}).model_dump())
    given, row = give(state, semantics.scenario, 1, {"object_id": "chips", "recipient_id": f"agent:{recipient}"})
    draft = result_draft(given, {"given": [row]})
    assert errands.evidence_rows(draft.event_type, draft.agent_id, draft.data) == [
        errands.Evidence(recipient, "obtain", "chips", 0.5, None)]
    settled, credits = errands.settle(given, [draft])
    assert progress(settled, recipient) == 0.5 and progress(settled, 1) == 0
    assert settled.agents[recipient].inventory["home"]["chips"] == 0.5
    assert credits[0].data["quantity_added"] == 0.5 and credits[0].agent_id == recipient
    replayed, repeated = errands.settle(settled, [result_draft(settled, {"given": [row]}, reused=True)])
    assert repeated == [] and progress(replayed, recipient) == 0.5


def test_actual_pending_delivery_does_not_credit_and_restored_arrival_credits_once():
    semantics, state = food_world("park")
    state = note(state, semantics)
    recipient = next(identity for identity in state.agents if identity != 1)
    bought = purchase_order(state, semantics, 1, buy_request(recipient, "park", "delivery"))
    ordered = result_draft(bought.state, {"orders": [row.model_dump(mode="json") for row in bought.orders]})
    assert errands.evidence_rows(ordered.event_type, ordered.agent_id, ordered.data) == []
    pending, credits = errands.settle(bought.state, [ordered])
    assert credits == [] and progress(pending, recipient) == progress(pending, 1) == 0
    restored = WorldState.model_validate_json(pending.model_dump_json())
    due = at_time(restored, bought.orders[0].expected_at)
    first = apply_scheduled_effect(due, bought.scheduled[0], semantics)
    arrived = arrival_draft(first.state, first.data)
    assert errands.evidence_rows(arrived.event_type, arrived.agent_id, arrived.data) == [
        errands.Evidence(recipient, "obtain", "beef-noodles", 1.0, None)]
    settled, credits = errands.settle(first.state, [arrived])
    assert progress(settled, recipient) == 1 and progress(settled, 1) == 0
    assert credits[0].agent_id == recipient and credits[0].caused_by == [arrived.event_id]
    repeated = apply_scheduled_effect(WorldState.model_validate_json(settled.model_dump_json()),
                                      bought.scheduled[0], semantics)
    duplicate = arrival_draft(repeated.state, repeated.data)
    assert repeated.data["inventory_changes"] == []
    assert errands.evidence_rows(duplicate.event_type, duplicate.agent_id, duplicate.data) == []
    after, credits = errands.settle(repeated.state, [duplicate])
    assert credits == [] and progress(after, recipient) == 1
    assert after.agents[recipient].inventory["park"]["beef-noodles"] == 1
    assert after.accounts == bought.state.accounts


def start_food(state, semantics):
    """@brief 实际开餐与活动初始化冻结消费请求和 opened 份额。"""
    request = ConsumeRequest(items=(ConsumeItem(product_id="beef-noodles", quantity=1),),
                             use_mode="eat", duration_seconds=1200)
    eater, opened = open_meal(state.agents[1], request)
    _, activity = start_activity(eater, ActivityStartParams(activity_type="consume", planned_duration_seconds=1200),
                                 state.current_time, ActivityOrigin("eat", "decision", "request"), semantics.body)
    activity.data = {"request": request.model_dump(mode="json"), "opened": opened, "consumed": {}}
    return eater, activity


def test_actual_food_progress_counts_interval_portions_and_zero_replay_does_not_credit():
    semantics, state = food_world()
    bought = purchase_order(state, semantics, 1, buy_request(1))
    state = note(bought.state, semantics, outcome="吃掉", quantity=1)
    eater, activity = start_food(state, semantics)
    consumed = {}
    for elapsed, expected_portion, expected_progress in ((600, 0.5, 0.5), (900, 0.25, 0.75), (1200, 0.25, 1.0)):
        interval_start = activity.last_settled_at
        activity.elapsed_seconds = elapsed
        eater = apply_meal_progress(eater, semantics.catalog.foods, activity, semantics.body)
        consumption = meal_consumption_data(activity, eater, consumed, semantics.catalog.foods)
        activity.last_settled_at = activity.started_at + elapsed
        agents = {**state.agents, 1: eater}
        state = at_time(state.model_copy(update={"agents": agents}), activity.last_settled_at)
        draft = EventDraft(event_type="activity_progress", occurred_at=state.current_time, agent_id=1,
            activity_id=activity.activity_id, data={"interval_start": interval_start, "consumption": consumption})
        assert errands.evidence_rows(draft.event_type, draft.agent_id, draft.data) == [
            errands.Evidence(1, "eat", "beef-noodles", expected_portion, interval_start)]
        state, credits = errands.settle(state, [draft])
        assert credits[0].data["quantity_added"] == expected_portion
        assert credits[0].data["errand"]["progress"] == expected_progress
        consumed = dict(activity.data["consumed"])
        eater = state.agents[1]
        replayed_eater = apply_meal_progress(eater, semantics.catalog.foods, activity, semantics.body)
        replayed = meal_consumption_data(activity, replayed_eater, consumed, semantics.catalog.foods)
        replay_draft = EventDraft(event_type="activity_progress", occurred_at=state.current_time, agent_id=1,
            data={"interval_start": state.current_time, "consumption": replayed})
        assert errands.evidence_rows(replay_draft.event_type, replay_draft.agent_id, replay_draft.data) == []
        assert errands.settle(state, [replay_draft]) == (state, [])
    assert not state.agents[1].errands
    recipient = next(identity for identity in state.agents if identity != 1)
    assert progress(state, recipient) == 0
