"""快递从付款、到站、驿站取件到完整导出认证的真实生命周期。"""

from datetime import timedelta
import json

from tests.footprints.recording import artifact_rows
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import advance_next, do_at, main_arguments, purchase, stage_day_plan, travel
from tests.life.environment_runtime import act, events, finish_activity, reach, run_to_end, settle_decision
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.recording_engine.export.export import export_run
from worldmock.recording_engine.validation.validation import validate_export

STATION = "快递驿站"
#: 驿站绑定的那件事的名称；人物在驿站点名它来取件。
PICKUP = "驿站取件"


def six_local_days(persona, config):
    """夹具的运行窗口只有 24 小时；快递要二到五个本地日才到站。"""
    config["stop_time"] = config["start_time"] + timedelta(days=6)


def environment_events(engine, kind):
    """已提交的某一种环境事实。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def parcel_at_station(engine_factory):
    """经真实请求买下唯一绑定在 app 上的商品，购买正常结束并付款，再运行到快递到站。"""
    engine = engine_factory(mutate=six_local_days)
    stage_day_plan(engine)
    _, bought = act(engine, "parcel-buy", purchase("平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "parcel-buy")
    (order,) = engine.context.state.deliveries.values()
    assert order.fulfillment == "station_pickup" and order.paid_at is not None
    reach(engine, order.expected_at)
    arrived = engine.context.state.deliveries[order.order_id]
    assert arrived.status == "ready_for_pickup" and arrived.arrived_at == order.expected_at
    return engine, arrived


def until_idle(engine, label):
    """处理真实决策机会，直到人物手上没有进行中或待收尾的活动并轮到决策。"""
    while True:
        state = engine.context.state
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
        elif any(item.status == "active" or item.status != "paused" and item.concluded_at is None
                 for item in state.activities.values()):
            settle_decision(engine, f"{label}-{state.current_time}")
        else:
            return


def collect_at_station(engine):
    """人物步行到驿站，在那里点名开始一件事；这一次受理领取本站全部到站包裹。"""
    until_idle(engine, "before-station")
    _, trip = act(engine, "to-station", travel(STATION))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, "to-station")
    until_idle(engine, "at-station")
    _, pickup = act(engine, "parcel-pickup", do_at(STATION, PICKUP))
    assert pickup.status == "in_progress", pickup.error_code
    return pickup


def next_observation(engine, label):
    """推进到人物的下一次决策机会，返回那次决策看到的观察。"""
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    observed, settled = settle_decision(engine, label)
    assert settled.status in {"in_progress", "completed"}, settled.error_code
    return observed


def export_completed_run(engine, directory):
    """用现有收尾助手运行到截止，写最终检查点，导出并认证。"""
    recorded = run_to_end(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    assert any(row.event_type == "run_ended" for row in recorded)
    assert not any(row.event_type == "run_failed" for row in recorded)
    workspace = write_agent_workspace(directory / "agent")
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    export_run(engine.context.store, engine.context.store.last_event_seq, directory / "export")
    manifest, _ = validate_export(directory / "export")
    exported = artifact_rows(directory / "export", "events")
    assert manifest["experiment_completed"] is True
    facts = [json.loads(line) for line in (directory / "export/purchases.jsonl").read_bytes().splitlines()]
    return {row["event_id"]: row for row in exported}, facts


def order_rows(facts, kind, order_id):
    """购买工件里一条商品履约记录的某一种事实行。"""
    return [row for row in facts if row["fact_kind"] == kind and row["order_id"] == order_id]


def assert_exported_notices_without_a_delivered_one(directory, order):
    """快递照发「订单已下单」「支付成功」「订单已发货」；到站只有到站取件通知，没有「订单已送达」。"""
    usage = [json.loads(line) for line in (directory / "export/app-usage.jsonl").read_bytes().splitlines()]
    sent = [(row["title"], row["occurred_at"]) for row in usage
            if row["fact_kind"] in {"order_notification", "payment_notification"}
            and row["order_ids"] == [order.order_id]]
    shipped = order.paid_at + -(-(order.arrived_at - order.paid_at) // 10)
    assert sent == [("订单已下单", order.paid_at), ("支付成功", order.paid_at), ("订单已发货", shipped)]
    assert not [row for row in usage if row.get("title") == "订单已送达"]


def assert_exported_arrival_caused_by_payment(sources, facts, order):
    """到站取件通知恰一行，来源到站事件的起因恰为列出这条记录的付款事件。"""
    (ready,) = order_rows(facts, "pickup_ready", order.order_id)
    assert ready["pickup_location_id"] == "parcel-station" == order.destination_id
    assert ready["arrived_at"] == order.arrived_at == order.expected_at
    arrival = sources[ready["source_event_id"]]
    assert arrival["data"]["kind"] == "delivery_arrival"
    assert [row["order_id"] for row in arrival["data"]["orders"]] == [order.order_id]
    (cause,) = arrival["caused_by"]
    payment = sources[cause]
    assert payment["event_type"] == "environment_event" and payment["data"]["kind"] == "purchase_payment"
    assert order.order_id in payment["data"]["order_ids"]
    assert payment["occurred_at"] == order.paid_at


def test_parcel_arrival_is_caused_by_its_payment_and_exports_before_collection(engine_factory, tmp_path):
    engine, order = parcel_at_station(engine_factory)
    (payment,) = environment_events(engine, "purchase_payment")
    (arrival,) = environment_events(engine, "delivery_arrival")
    assert payment.data["order_ids"] == [order.order_id]
    assert arrival.caused_by == [payment.event_id]
    sources, facts = export_completed_run(engine, tmp_path)
    final = engine.context.state.deliveries[order.order_id]
    assert final == order and final.received_at is None
    assert_exported_arrival_caused_by_payment(sources, facts, final)
    assert_exported_notices_without_a_delivered_one(tmp_path, final)
    assert order_rows(facts, "receipt", order.order_id) == []


def test_parcel_arrival_collection_and_export_are_authenticated(engine_factory, tmp_path):
    engine, order = parcel_at_station(engine_factory)
    pickup = collect_at_station(engine)
    collected = engine.context.state.deliveries[order.order_id]
    assert collected.status == "delivered" and collected.received_at == pickup.occurred_at
    accepted = next(row for row in events(engine, "action_result") if row.action_id == pickup.action_id)
    progress = next_observation(engine, "after-pickup")["progress_layer"]
    (shown,) = [row["result"]["details"] for row in progress["events"] if row["type"] == "tool_call"
                and row["arguments"] == main_arguments("interact", STATION, PICKUP)]
    assert "purchases" not in shown and "买了" not in str(progress)
    sources, facts = export_completed_run(engine, tmp_path)
    final = engine.context.state.deliveries[order.order_id]
    assert final == collected
    assert pickup.data["orders"] == [final.model_dump(mode="json")]
    assert_exported_arrival_caused_by_payment(sources, facts, final)
    assert_exported_notices_without_a_delivered_one(tmp_path, final)
    stays = [json.loads(line) for line in (tmp_path / "export/location-stays.jsonl").read_bytes().splitlines()]
    visits = [row for row in stays if row["location_id"] == "parcel-station"]
    assert [row["entry_kind"] for row in visits] == ["arrival"] and visits[0]["entered_at"] <= final.received_at
    (receipt,) = order_rows(facts, "receipt", order.order_id)
    assert receipt["received_at"] == final.received_at
    received = sources[receipt["source_event_id"]]
    assert received["data"]["kind"] == "goods_received" and received["caused_by"] == [accepted.event_id]
    result = sources[accepted.event_id]
    assert result["event_type"] == "action_result" and result["data"]["status"] == "in_progress"
    assert result["data"]["data"]["orders"] == [final.model_dump(mode="json")]
