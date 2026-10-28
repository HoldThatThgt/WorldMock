"""真实新环境运行的导出、恢复和只读冻结素材认证。"""

import hashlib
import json

import pytest

from tests.footprints.recording import artifact_rows
from tests.life.conftest import conclude_request, do_at, errand_add, purchase, travel, use_app
from tests.life.environment_runtime import act, finish_activity, reach, run_to_end
from tests.life.prerequisite_inputs import OFFICE, dingtalk_environment, example_window, moment
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.checkpoint import restore_branch
from worldmock.recording_engine.export.export import export_run
from worldmock.recording_engine.events.models import AssetRef
from worldmock.recording_engine.validation.validation import validate_export


@pytest.fixture
def completed_engine(engine_factory):
    engine = engine_factory()
    _, started = act(engine, "keep", use_app("Keep"))
    assert started.status == "in_progress"
    finish_activity(engine, started, "keep")
    run_to_end(engine)
    snapshot = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    return engine, point


def test_complete_run_exports_authenticated_app_use_and_frozen_inputs(completed_engine, tmp_path):
    """打开 Keep 的完整运行导出后通过认证：每个 interact 都有内容身份，这项活动既有 app_use 行也有 content_consumed 行。"""
    store = completed_engine[0].context.store
    output = tmp_path / "export"
    export_run(store, store.last_event_seq, output)
    manifest, activities = validate_export(output)
    assert manifest["experiment_completed"] is True
    assert all(len(row["source_event_ids"]) == len(set(row["source_event_ids"])) for row in activities)
    interactions = [row for row in activities if row["activity_type"] == "interact"]
    assert interactions and all(row["data"]["content_id"] is not None for row in interactions)
    keep = next(row for row in interactions if row["data"]["app_id"] == "keep")
    usage = [json.loads(row) for row in (output / "app-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["fact_kind"] == "app_use" and row["activity_id"] == keep["activity_id"] for row in usage)
    assert any(row["fact_kind"] == "content_consumed" and row["activity_id"] == keep["activity_id"]
               for row in usage)
    for reference in store.manifest["environment_assets"].values():
        assert (output / "assets" / reference["sha256"]).read_bytes() == store.assets.read(AssetRef.model_validate(reference))


def test_branch_copies_all_frozen_environment_assets(completed_engine, tmp_path):
    engine, point = completed_engine
    store = engine.context.store
    child = restore_branch(store, point.checkpoint_id, tmp_path / "branch")
    try:
        assert child.manifest["environment_assets"] == store.manifest["environment_assets"]
        for reference in child.manifest["environment_assets"].values():
            data = child.assets.read(AssetRef.model_validate(reference))
            assert hashlib.sha256(data).hexdigest() == reference["sha256"]
    finally:
        child.close()


def test_export_rejects_corrupt_frozen_input(completed_engine, tmp_path):
    output = tmp_path / "export"
    store = completed_engine[0].context.store
    export_run(store, store.last_event_seq, output)
    identity = store.manifest["environment_assets"]["persona.toml"]["sha256"]
    (output / "assets" / identity).write_bytes(b"corrupt")
    with pytest.raises(IntegrityError):
        validate_export(output)


def test_real_delivery_export_links_payment_arrival_and_one_receipt(engine_factory, tmp_path):
    engine = engine_factory()
    _, started = act(engine, "delivery-purchase", purchase("柠檬茶"))
    assert started.status == "in_progress"
    finish_activity(engine, started, "delivery-payment")
    paid = next(iter(engine.context.state.deliveries.values()))
    assert paid.paid_at is not None and paid.received_at is None
    accounts = dict(engine.context.state.accounts)
    reach(engine, paid.expected_at)
    assert engine.context.state.accounts == accounts
    run_to_end(engine)
    output = tmp_path / "delivery-export"
    store = engine.context.store
    snapshot = store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    engine.checkpoint({"agent:1": snapshot})
    export_run(store, store.last_event_seq, output)
    manifest, _ = validate_export(output)
    exported_events = artifact_rows(output, "events")
    assert manifest["experiment_completed"] is True
    purchases = [json.loads(row) for row in (output / "purchases.jsonl").read_text(encoding="utf-8").splitlines()]
    bought = next(row for row in purchases if row["fact_kind"] == "purchase")
    receipts = [row for row in purchases if row["fact_kind"] == "receipt"]
    assert len(receipts) == 1 and receipts[0]["order_id"] == bought["order_id"] == paid.order_id
    assert receipts[0]["received_at"] == paid.expected_at
    assert receipts[0]["goods_id"] == bought["goods_id"] == paid.goods_id
    arrival = next(row for row in exported_events if row["data"].get("kind") == "delivery_arrival")
    assert arrival["data"]["orders"][0]["received_at"] == paid.expected_at
    assert not any(row["fact_kind"] == "pickup_ready" for row in purchases)


def test_clock_in_exports_one_app_use_and_one_content_consumed_row(engine_factory, tmp_path):
    """规格完整例子的一天：08:00 在家打开钉钉被拒，开车到办公室做事，08:55:00 再打开钉钉，引擎挑中打卡并在 08:56:00
    做完。导出里钉钉恰有一行 app_use 和一行 content_consumed，形状与任何一次 app 内容使用相同；被拒的那一次
    不留使用记录；导出自查通过。"""
    engine = engine_factory(dingtalk_environment(tmp_path / "input"), mutate=example_window(days=1))
    _, refused = act(engine, "clock-at-home", use_app("钉钉"))
    assert refused.status == "rejected" and refused.error_code == "CONTENT_PREREQUISITE_UNMET"
    _, commute = act(engine, "commute", travel(OFFICE, "驾车"))
    assert commute.status == "in_progress", commute.error_code
    trip = engine.context.state.activities[commute.activity_id]
    assert trip.planned_end_at < moment(12, 8, 55)
    reach(engine, trip.planned_end_at)
    # 到达时记一件 08:55 提醒的事：提醒到点给出一次恰在 08:55:00 的决策机会
    errand = errand_add("下班前买纸巾", "纸巾", remind_time="08:55")
    _, arrived = act(engine, "arrive", conclude_request(trip.name, errands=[errand]), "conclude_activity")
    assert arrived.status == "completed", arrived.error_code
    _, work = act(engine, "work", do_at(OFFICE, "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    reach(engine, moment(12, 8, 55))
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "reminder_due" in engine.context.state.agents[1].pending_wake_reasons
    _, clock = act(engine, "clock", use_app("钉钉"))
    assert clock.status == "in_progress", clock.error_code
    finish_activity(engine, clock, "clock")
    run_to_end(engine)
    store = engine.context.store
    snapshot = store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    output = tmp_path / "export"
    export_run(store, point.last_event_seq, output)
    manifest, activities = validate_export(output)
    assert manifest["experiment_completed"] is True
    usage = [json.loads(row) for row in (output / "app-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [{key: value for key, value in row.items() if key not in {"activity_id", "agent_id", "source_event_id"}}
            for row in usage if row.get("app_id") == "dingtalk"]
    assert rows == [
        {"fact_kind": "app_use", "app_id": "dingtalk", "status": "completed",
         "segments": [{"started_at": 1791766500, "ended_at": 1791766560}]},
        {"fact_kind": "content_consumed", "app_id": "dingtalk", "content_id": "dingtalk-clock", "completed": True,
         "interval_start": 1791766500, "interval_end": 1791766560, "position_before": 0.0, "position_after": 60.0,
         "push_id": None}]
    assert (moment(12, 8, 55), moment(12, 8, 56)) == (1791766500, 1791766560)
    exported = [row for row in activities if row["data"].get("app_id") == "dingtalk"]
    assert [(row["activity_id"], row["status"], row["data"]["content_id"]) for row in exported] == [
        (clock.activity_id, "completed", "dingtalk-clock")]
