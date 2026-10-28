"""引擎要记的手机记录：app 的开和关、进出地点、订单通知，都是人物看不到、也不给决策机会的系统记录。"""

from datetime import timedelta

import pytest

from tests.life.conftest import (
    action_identity,
    advance_next,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    purchase,
    sleep,
    travel,
    use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from tests.life.phone_records import (
    foreground,
    label_where,
    moment,
    notices,
    paying_label,
    phone_environment,
    phone_events,
    phone_window,
    purchase_draws,
    returning_label,
    stays,
)
from worldmock.agents.prompt.life_prompt import life_user_layers, system_message, user_message
from worldmock.recording_engine.projections.activities.app_usage import project_app_usage
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.projections.commerce.purchases import project_purchase_facts
from worldmock.exchange_engine.commerce.shipping import shipping_delay_seconds
from worldmock.foundation.randomness import random_stream

#: 咖啡例子的各个时刻：09:05:00 开始购买，下单 185 秒、付款 15 秒，09:20:00 送达。
BUY, PAYING, PAID = moment(9, 5), moment(9, 8, 5), moment(9, 8, 20)
SHIPPED, DELIVERED = moment(9, 9, 30), moment(9, 20)
#: 导航例子的各个时刻：08:20:00 出发，08:31:00 开始用 app，08:40:00 用完，08:50:00 到达。
DEPART, BUSY, FREE, ARRIVE = moment(8, 20), moment(8, 31), moment(8, 40), moment(8, 50)
PHONE_KINDS = {"app_foreground", "order_notification", "payment_notification", "location_entered", "location_left"}


def coffee(engine_factory, tmp_path, method):
    """咖啡例子：人在家，09:05:00 一边开始「测试休息」一边买冰美式；购买抽到 200 秒，付款抽到给定方式和 15 秒。"""
    engine = engine_factory(phone_environment(tmp_path / "coffee"), mutate=phone_window((9, 0), 2))
    stage_moment(engine, BUY)
    label = label_where("coffee", lambda item: purchase_draws(engine, action_identity(item, 2), "luckin")
                        == (200, method, 15), limit=400000)
    _, (rest, bought) = act_group(engine, label, do_at("家", "测试休息"), purchase("冰美式"))
    assert (rest.status, bought.status) == ("in_progress", "in_progress"), bought.error_code
    (order,) = engine.context.state.deliveries.values()
    assert (order.app_id, order.payment_app_id, order.payment_duration_seconds) == ("luckin", method, 15)
    assert (order.fulfillment, order.distance_meters, order.expected_at, order.total_price_fen) == (
        "delivery", 1500, DELIVERED, 2100)
    return engine, bought, order.order_id


def conclude(engine, activity_id, label):
    """给一项刚结束的活动收尾。"""
    name = engine.context.state.activities[activity_id].name
    _, result = act(engine, label, conclude_request(name), "conclude_activity")
    assert result.status == "completed", result.error_code
    return result


def offered(engine, at):
    """某一秒的共同身体结算有没有给人物决策机会。"""
    return [row.data["decision_offered"] for row in events(engine, "environment_event")
            if row.data.get("kind") == "body_settlement" and row.occurred_at == at]


@pytest.mark.parametrize("method", ["alipay", None])
def test_coffee_purchase_records_the_ordering_and_payment_apps_and_all_four_notices(engine_factory, tmp_path, method):
    engine, bought, order_id = coffee(engine_factory, tmp_path, method)
    # 受理不写任何 app 记录或通知；下单段结束的那一秒写下单的内容 app，付款段走完的那一秒写支付 app。
    assert foreground(engine) == [] and notices(engine) == []
    advance_next(engine)
    assert engine.context.state.current_time == PAYING
    assert foreground(engine) == [("luckin", "ordering", BUY, PAYING)] and notices(engine) == []
    advance_next(engine)
    assert engine.context.state.current_time == PAID
    payer = "luckin" if method is None else method
    assert foreground(engine, bought.activity_id) == [("luckin", "ordering", BUY, PAYING),
                                                      (payer, "payment", PAYING, PAID)]
    assert notices(engine) == [(PAID, "订单已下单", "luckin", [order_id]), (PAID, "支付成功", payer, [order_id])]
    conclude(engine, bought.activity_id, "coffee-done")
    advance_next(engine)
    assert engine.context.state.current_time == SHIPPED == PAID + shipping_delay_seconds(DELIVERED - PAID)
    assert notices(engine)[2:] == [(SHIPPED, "订单已发货", "luckin", [order_id])]
    reach(engine, DELIVERED)
    assert notices(engine)[3:] == [(DELIVERED, "订单已送达", "luckin", [order_id])]
    texts = [row.data["summary"] for row in events(engine, "environment_event")
             if row.data.get("kind") in {"order_notification", "payment_notification"}]
    assert texts == ["已下单：冰美式", "已消费 21.00 元，购买冰美式", "冰美式已发货", "冰美式已送达"]
    records = [row for row in events(engine, "environment_event") if row.data.get("kind") in PHONE_KINDS]
    assert all((row.source_kind, row.visibility, row.resource_changes, row.agent_id) == ("environment", "system", {}, 1)
               for row in records)
    # 起因：前台区间是购买受理的结果；已下单、支付成功、已发货是这张订单的付款事件；已送达是这一趟的到达事件。
    accepted = next(row for row in events(engine, "action_result") if row.action_id == bought.action_id)
    (payment,) = phone_events(engine, "purchase_payment")
    (arrival,) = phone_events(engine, "delivery_arrival")
    assert [row.caused_by for row in phone_events(engine, "app_foreground")] == [[accepted.event_id]] * 2
    assert [row.activity_id for row in phone_events(engine, "app_foreground")] == [bought.activity_id] * 2
    placed, shipped, delivered = phone_events(engine, "order_notification")
    (paid_notice,) = phone_events(engine, "payment_notification")
    assert [row.caused_by for row in (placed, paid_notice, shipped)] == [[payment.event_id]] * 3
    assert delivered.caused_by == [arrival.event_id] and arrival.caused_by == [payment.event_id]
    assert all(set(row.data) == {"kind", "app_id", "order_ids", "title", "summary"}
               for row in (placed, shipped, delivered))
    # 外卖送达时先写人物可见的到达事件和收货，再写「订单已送达」。
    order = [row.data["kind"] for row in events(engine, "environment_event") if row.occurred_at == DELIVERED
             and row.data.get("kind") in {"delivery_arrival", "goods_received", "order_notification"}]
    assert order == ["delivery_arrival", "goods_received", "order_notification"]


@pytest.mark.parametrize("method", ["alipay", None])
def test_coffee_purchase_projects_the_app_use_rows_of_the_proposal_example(engine_factory, tmp_path, method):
    engine, bought, order_id = coffee(engine_factory, tmp_path, method)
    reach(engine, PAID)
    conclude(engine, bought.activity_id, "coffee-done")
    reach(engine, DELIVERED)
    rows = project_app_usage([row.model_dump(mode="json") for row in events(engine)])
    facts = [{key: row[key] for key in row if key not in {"agent_id", "activity_id", "source_event_id", "order_ids"}}
             for row in rows]
    paying = [{"fact_kind": "app_use", "app_id": "alipay", "status": "completed",
               "segments": [{"started_at": 1791767285, "ended_at": 1791767300}]}]
    ordering_end = 1791767285 if method == "alipay" else 1791767300
    assert facts == [
        {"fact_kind": "app_use", "app_id": "luckin", "status": "completed",
         "segments": [{"started_at": 1791767100, "ended_at": ordering_end}]},
        *(paying if method == "alipay" else []),
        {"fact_kind": "order_notification", "app_id": "luckin", "occurred_at": 1791767300, "title": "订单已下单",
         "summary": "已下单：冰美式"},
        {"fact_kind": "payment_notification", "app_id": method or "luckin", "occurred_at": 1791767300,
         "amount_fen": 2100, "title": "支付成功", "summary": "已消费 21.00 元，购买冰美式"},
        {"fact_kind": "order_notification", "app_id": "luckin", "occurred_at": 1791767370, "title": "订单已发货",
         "summary": "冰美式已发货"},
        {"fact_kind": "order_notification", "app_id": "luckin", "occurred_at": 1791768000, "title": "订单已送达",
         "summary": "冰美式已送达"}]
    uses = [row for row in rows if row["fact_kind"] == "app_use"]
    assert all(row["activity_id"] == bought.activity_id and row["agent_id"] == 1 for row in uses)
    assert all(row["activity_id"] is None and row["order_ids"] == [order_id] for row in rows[len(uses):])
    # 每一行的来源是真实事件：app 使用行指向最后一条贡献区间的前台事件，其余指向通知事件本身。
    last = {(row.data["app_id"]): row.event_id for row in phone_events(engine, "app_foreground")}
    assert [row["source_event_id"] for row in uses] == [last[row["app_id"]] for row in uses]


def test_shipping_notice_gives_no_decision_opportunity_and_no_new_wake_reason(engine_factory, tmp_path):
    engine, bought, _ = coffee(engine_factory, tmp_path, "alipay")
    reach(engine, PAID)
    conclude(engine, bought.activity_id, "coffee-done")
    person = engine.context.state.agents[1]
    # 发货通知排程不算进下一次决策时刻的候选：人物接下来在「测试休息」结束、外卖送达的 09:20:00 才决策。
    assert [row.kind for row in engine.context.state.pending_events if row.due_at == SHIPPED] == ["order_shipped"]
    assert person.next_decision_at == DELIVERED and person.pending_wake_reasons == []
    advance_next(engine)
    state = engine.context.state
    assert state.current_time == SHIPPED and engine.scheduling_state().ready_agent_ids == ()
    assert (state.agents[1].next_decision_at, state.agents[1].pending_wake_reasons) == (DELIVERED, [])
    assert offered(engine, SHIPPED) == [False]
    assert not [row for row in state.pending_events if row.kind == "order_shipped"]
    at_shipping = [row.data.get("kind") for row in events(engine, "environment_event") if row.occurred_at == SHIPPED]
    assert sorted(at_shipping) == ["body_settlement", "order_notification"]
    reach(engine, DELIVERED)
    started = [row for row in events(engine, "decision_started")]
    assert {reason for row in started for reason in row.data["wake_reasons"]} <= {
        "session_open", "planned_end", "delivery_arrival", "no_active_activity", "progress_report_due"}
    assert not [row for row in started if PAID < row.occurred_at < DELIVERED]


def test_shipping_and_delivery_notices_are_written_while_the_person_sleeps_without_waking(engine_factory, tmp_path):
    engine = engine_factory(phone_environment(tmp_path / "asleep"), mutate=phone_window((9, 0), 2))
    stage_moment(engine, BUY)
    _, bought = act(engine, "night-coffee", purchase("冰美式"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "night-coffee")
    (order,) = engine.context.state.deliveries.values()
    paid = order.paid_at
    _, nap = act(engine, "nap", sleep(30))
    assert nap.status == "in_progress" and engine.context.state.current_time == paid
    woke = paid + 1800
    assert paid < order.expected_at < woke
    reach(engine, woke)
    shipped = paid + shipping_delay_seconds(order.expected_at - paid)
    assert [(row[0], row[1]) for row in notices(engine)][-2:] == [(shipped, "订单已发货"),
                                                                  (order.expected_at, "订单已送达")]
    assert engine.context.state.deliveries[order.order_id].status == "delivered"
    assert offered(engine, shipped) == [False] and offered(engine, order.expected_at) == [False]
    assert not [row for row in events(engine, "decision_started") if paid < row.occurred_at < woke]
    assert engine.context.state.activities[nap.activity_id].ended_at == woke


def at_store(persona, config):
    persona["initial_location"] = "四季便利"
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


@pytest.mark.parametrize("method", ["alipay", None])
def test_buying_in_the_store_records_only_the_payment_app_and_nothing_when_paid_directly(engine_factory, method):
    engine = engine_factory(mutate=at_store)
    label = paying_label(engine, "tissue", {None: method})
    _, bought = act(engine, label, purchase("纸巾"))
    assert bought.status == "in_progress", bought.error_code
    activity = engine.context.state.activities[bought.activity_id]
    reach(engine, activity.planned_end_at)
    (order,) = engine.context.state.deliveries.values()
    assert (order.app_id, order.payment_app_id, order.status, order.fulfillment) == (None, method, "delivered", "pickup")
    assert phone_events(engine, "order_notification") == []
    if method is None:
        # 到店且直付：没有 app 可开，一条 app 记录、一条通知都没有。
        assert order.payment_duration_seconds == 0 and foreground(engine) == [] and notices(engine) == []
        return
    paying = activity.planned_end_at - order.payment_duration_seconds
    assert foreground(engine) == [("alipay", "payment", paying, activity.planned_end_at)]
    assert notices(engine) == [(activity.planned_end_at, "支付成功", "alipay", [order.order_id])]


def test_parcel_gets_placed_paid_and_shipped_notices_but_only_the_pickup_notice_on_arrival(engine_factory):
    def six_days(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(days=6)

    engine = engine_factory(mutate=six_days)
    label = paying_label(engine, "parcel", {"bilibili": None})
    _, bought = act(engine, label, purchase("平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, label)
    (order,) = engine.context.state.deliveries.values()
    assert (order.fulfillment, order.app_id, order.payment_app_id) == ("station_pickup", "bilibili", None)
    paid, arrives = order.paid_at, order.expected_at
    shipped = paid + shipping_delay_seconds(arrives - paid)
    assert 2 * 86400 - 86400 < arrives - paid and paid < shipped < arrives
    assert [(row.kind, row.scheduled_id, row.due_at) for row in engine.context.state.pending_events
            if row.data.get("order_ids") == [order.order_id]] == [
        ("order_shipped", f"shipped-{order.order_id}", shipped), ("delivery_arrival", f"delivery-{order.order_id}", arrives)]
    reach(engine, arrives)
    assert engine.context.state.deliveries[order.order_id].status == "ready_for_pickup"
    # 直付的快递：下单和付款都在哔哩哔哩里，「支付成功」也由它发；到站时没有「订单已送达」。
    assert notices(engine) == [(paid, "订单已下单", "bilibili", [order.order_id]),
                               (paid, "支付成功", "bilibili", [order.order_id]),
                               (shipped, "订单已发货", "bilibili", [order.order_id])]
    facts = project_purchase_facts([row.model_dump(mode="json") for row in events(engine)])
    (ready,) = [row for row in facts if row["fact_kind"] == "pickup_ready"]
    assert (ready["order_id"], ready["arrived_at"], ready["pickup_location_id"]) == (
        order.order_id, arrives, "parcel-station")


def commute(engine_factory, tmp_path, clip_seconds=540, hours=2):
    """导航例子的世界：晴天，家到公司驾车 30 分钟；每 11 分钟给一次播报机会；人物 08:20:00 在家。"""
    engine = engine_factory(phone_environment(tmp_path / "commute", clip_seconds),
                            mutate=phone_window((8, 0), hours, progress_report_interval_seconds=660))
    stage_moment(engine, DEPART)
    return engine


def navigation(engine):
    return engine.context.state.agents[1].navigation_foreground


@pytest.mark.parametrize("returns", [True, False])
def test_navigation_is_open_on_the_road_closes_for_a_side_activity_and_returns_by_a_draw(
        engine_factory, tmp_path, returns):
    engine = commute(engine_factory, tmp_path)
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    # 出发即开：只写人物状态，不写事件，出行活动的快照里没有导航 app。
    assert navigation(engine).model_dump() == {"activity_id": trip.activity_id, "app_id": "amap", "opened_at": DEPART}
    assert foreground(engine) == [] and "amap" not in str(trip.data)
    assert advance_until_ready(engine) and engine.context.state.current_time == BUSY
    label = returning_label(engine, "commute", "watch", returns)
    _, watching = act(engine, label, use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    assert navigation(engine) is None and foreground(engine) == [("amap", "navigation", DEPART, BUSY)]
    assert advance_until_ready(engine) and engine.context.state.current_time == FREE
    assert engine.context.state.activities[watching.activity_id].status == "completed"
    # 抽取按真实停止的那一秒算，不等人物收尾；收尾不是停止执行，导航不变。
    opened = {"activity_id": trip.activity_id, "app_id": "amap", "opened_at": FREE} if returns else None
    assert (navigation(engine) and navigation(engine).model_dump()) == opened
    conclude(engine, watching.activity_id, "watch-done")
    assert (navigation(engine) and navigation(engine).model_dump()) == opened
    reach(engine, ARRIVE)
    assert engine.context.state.activities[trip.activity_id].status == "completed" and navigation(engine) is None
    second = [("amap", "navigation", FREE, ARRIVE)] if returns else []
    assert foreground(engine) == [("amap", "navigation", DEPART, BUSY), *second]
    accepted = next(row for row in events(engine, "action_result") if row.action_id == trip.action_id)
    assert all((row.activity_id, row.caused_by, row.visibility) == (trip.activity_id, [accepted.event_id], "system")
               for row in phone_events(engine, "app_foreground"))
    assert [row.occurred_at for row in phone_events(engine, "app_foreground")] == [BUSY, ARRIVE][:1 + returns]


def test_departing_and_opening_an_app_in_the_same_round_has_one_interval_after_the_app_ends(engine_factory, tmp_path):
    """提案的另一个例子：同一轮里既出发又开始用哔哩哔哩，08:31:00 用完并抽中，只有 08:31:00–08:50:00 一条。"""
    engine = commute(engine_factory, tmp_path, clip_seconds=660)
    seed = engine.context.state.random_state
    label = label_where("both", lambda item: random_stream(
        seed, f"navigation-return:activity-{action_identity(item, 1)}:activity-{action_identity(item, 2)}"
    ).getrandbits(1) == 1)
    _, (trip, watching) = act_group(engine, label, travel("公司", "驾车"), use_app("哔哩哔哩"))
    assert (trip.status, watching.status) == ("in_progress", "in_progress")
    # 出发那一秒槽位 2 已有活动：导航一开始不在前台，不记零秒区间。
    assert navigation(engine) is None and foreground(engine) == []
    assert advance_until_ready(engine) and engine.context.state.current_time == BUSY
    assert engine.context.state.activities[watching.activity_id].status == "completed"
    assert navigation(engine).model_dump() == {"activity_id": trip.activity_id, "app_id": "amap", "opened_at": BUSY}
    assert foreground(engine) == []
    conclude(engine, watching.activity_id, "both-done")
    reach(engine, ARRIVE)
    assert foreground(engine) == [("amap", "navigation", BUSY, ARRIVE)] and navigation(engine) is None


def test_departing_while_an_app_is_already_in_use_keeps_navigation_closed_until_the_app_stops(engine_factory, tmp_path):
    engine = engine_factory(phone_environment(tmp_path / "running", 1860),
                            mutate=phone_window((8, 0), 2, progress_report_interval_seconds=660))
    label = returning_label(engine, "leave", "early-clip", False)
    _, watching = act(engine, label, use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    assert advance_until_ready(engine)
    left = engine.context.state.current_time
    assert left == moment(8, 11) and engine.context.state.activities[watching.activity_id].status == "active"
    _, trip = act(engine, "leave", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    assert navigation(engine) is None and foreground(engine) == []
    # 08:31:00 那条内容播完，这一次没抽中：导航留在后台，整趟出行没有任何导航记录。
    reach(engine, left + 1800)
    assert engine.context.state.activities[watching.activity_id].ended_at == moment(8, 31)
    assert engine.context.state.activities[trip.activity_id].status == "completed"
    assert navigation(engine) is None and foreground(engine) == []
    assert stays(engine) == [(moment(8), "location_entered", "home", "run_start"), (left, "location_left", "home", None),
                             (left + 1800, "location_entered", "office", "arrival")]


def test_ending_the_trip_early_closes_navigation_and_enters_the_origin_again(engine_factory, tmp_path):
    engine = commute(engine_factory, tmp_path)
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    assert advance_until_ready(engine) and engine.context.state.current_time == BUSY
    stopped = conclude(engine, trip.activity_id, "turn-back")
    assert engine.context.state.activities[trip.activity_id].status == "cancelled"
    assert navigation(engine) is None and foreground(engine) == [("amap", "navigation", DEPART, BUSY)]
    assert engine.context.state.agents[1].location_id == "home"
    assert stays(engine) == [(moment(8), "location_entered", "home", "run_start"), (DEPART, "location_left", "home", None),
                             (BUSY, "location_entered", "home", "travel_ended_early")]
    departed, entered = phone_events(engine, "location_left")[0], phone_events(engine, "location_entered")[1]
    results = {row.action_id: row.event_id for row in events(engine, "action_result")
               if row.occurred_at in {DEPART, BUSY} and row.data.get("status") in {"in_progress", "completed"}}
    assert departed.caused_by == [results[trip.action_id]] and entered.caused_by == [results[stopped.action_id]]
    sessions = engine.context.state.sessions
    assert [(sessions[row.data["session_id"]].kind, sessions[row.data["session_id"]].location_id)
            for row in (departed, entered)] == [("stay", "home"), ("stay", "home")]
    assert departed.data["session_id"] != entered.data["session_id"]


def test_run_cutoff_on_the_road_closes_navigation_once_and_writes_no_stay_event(engine_factory, tmp_path):
    engine = engine_factory(phone_environment(tmp_path / "cutoff"), mutate=phone_window((8, 0), 1))
    stage_moment(engine, moment(8, 40))
    _, trip = act(engine, "late", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    reach(engine, moment(9))
    assert engine.context.state.current_time == engine.context.state.end_time == moment(9)
    assert engine.context.state.activities[trip.activity_id].status == "truncated"
    assert navigation(engine) is None and foreground(engine) == [("amap", "navigation", moment(8, 40), moment(9))]
    # 截止时人物还在路上：那一秒没有进入，也没有离开。
    assert stays(engine) == [(moment(8), "location_entered", "home", "run_start"),
                             (moment(8, 40), "location_left", "home", None)]


def test_arrival_at_the_run_cutoff_still_enters_the_destination(engine_factory, tmp_path):
    engine = engine_factory(phone_environment(tmp_path / "on-time"), mutate=phone_window((8, 0), 1))
    stage_moment(engine, moment(8, 30))
    _, trip = act(engine, "on-time", travel("公司", "驾车"))
    reach(engine, moment(9))
    assert engine.context.state.activities[trip.activity_id].status == "completed"
    assert foreground(engine) == [("amap", "navigation", moment(8, 30), moment(9))]
    assert stays(engine)[-1] == (moment(9), "location_entered", "office", "arrival")
    (arrived,) = [row for row in phone_events(engine, "location_entered") if row.data["entry_kind"] == "arrival"]
    progress = [row.event_id for row in events(engine, "activity_progress")
                if row.activity_id == trip.activity_id and row.occurred_at == moment(9)]
    assert arrived.caused_by == progress and len(progress) == 1


def user_prompt(engine, label):
    """这一刻人物会看到的两条消息：系统层与六个用户层，取自一次真实观察。"""
    observed = engine.observe(context(engine, label))
    layers = life_user_layers(observed, {"layer": "人设层"})
    return system_message(False)["content"], user_message(layers)["content"], observed


def test_phone_records_never_reach_any_prompt_layer(engine_factory, tmp_path):
    engine, bought, order_id = coffee(engine_factory, tmp_path, "alipay")
    reach(engine, PAID)
    conclude(engine, bought.activity_id, "coffee-done")
    reach(engine, DELIVERED)
    assert len(phone_events(engine, "app_foreground")) == 2 and len(notices(engine)) == 4
    system, before, observed = user_prompt(engine, "before-records")
    # 进度层和记忆层讲的是人物自己做的事和真实收货；支付 app、通知标题和记录种类都不出现。下单 app 的名称作为购买结果
    # 的事实（channel）出现在进度层。
    for layer in ("progress_layer", "memory_layer", "todo_layer", "environment_layer"):
        text = str(observed[layer])
        assert not [word for word in ("支付宝", "高德地图", "订单已下单", "支付成功", "订单已发货", "订单已送达",
                                      "app_foreground", "order_notification", "location_entered") if word in text]
    assert "冰美式" in str(observed["progress_layer"])
    purchases = [row for record in observed["progress_layer"]["events"] if record.get("tool") == "start_side_activity"
                 for row in record["result"]["details"]["purchases"]]
    assert [(row["product"], row["channel"]) for row in purchases] == [("冰美式", "瑞幸咖啡")]
    # 同一世界再写入四种记录各一条：人物的各层提示逐字节不变。
    state, now = engine.context.state, engine.context.state.current_time
    cause = phone_events(engine, "purchase_payment")[0].event_id
    shared = {"event_type": "environment_event", "occurred_at": now, "agent_id": 1, "visibility": "system",
              "caused_by": [cause]}
    engine.context.store.append_evidence([
        EventDraft(**shared, activity_id=bought.activity_id, data={
            "kind": "app_foreground", "app_id": "wechat", "step": "payment", "interval_start": now - 15,
            "interval_end": now}),
        EventDraft(**shared, data={"kind": "order_notification", "app_id": "luckin", "order_ids": [order_id],
                                   "title": "订单已发货", "summary": "冰美式已发货"}),
        EventDraft(**shared, data={"kind": "location_left", "session_id": "session-1-0001", "location_id": "home"}),
        EventDraft(**shared, data={"kind": "location_entered", "session_id": "session-1-0009",
                                   "location_id": "office", "entry_kind": "arrival"})])
    assert user_prompt(engine, "after-records")[:2] == (system, before)


def test_phone_records_and_export_follow_the_ordering_app_written_in_prefer(engine_factory, tmp_path):
    """人在家经 prefer 写的饿了么买柠檬茶，做完并送达：下单段前台区间的 app、「订单已下单」「订单已发货」「订单已送达」
    的发送方都是饿了么；导出认证通过，purchases.jsonl 的字段集与今天相同，下单 app 是实际的饿了么。"""
    from tests.footprints.recording import artifact_rows, export_completed

    def one_hour(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(hours=1)

    engine = engine_factory(mutate=one_hour)
    _, bought = act(engine, "tea", purchase("柠檬茶", prefer="饿了么"))
    assert bought.status == "in_progress", bought.error_code
    (order,) = engine.context.state.deliveries.values()
    assert (order.store_id, order.app_id) == ("cafe", "eleme")
    finish_activity(engine, bought, "tea")
    reach(engine, order.expected_at)
    assert [row[0] for row in foreground(engine, bought.activity_id) if row[1] == "ordering"] == ["eleme"]
    senders = {title: app for _, title, app, _ in notices(engine)}
    assert {title: senders[title] for title in ("订单已下单", "订单已发货", "订单已送达")} == {
        "订单已下单": "eleme", "订单已发货": "eleme", "订单已送达": "eleme"}
    export_completed(engine, tmp_path / "export")
    (row,) = [row for row in artifact_rows(tmp_path / "export", "purchases") if row["fact_kind"] == "purchase"]
    assert set(row) == {"fact_kind", "agent_id", "order_id", "goods_id", "category", "store_id", "app_id",
                        "payment_app_id", "unit_price_fen", "delivery_fee_fen", "quantity", "total_price_fen",
                        "fulfillment", "created_at", "action_id", "source_event_id"}
    assert (row["store_id"], row["app_id"], row["goods_id"]) == ("cafe", "eleme", "lemon-tea")
