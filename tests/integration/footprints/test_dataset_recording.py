"""真实新环境运行的导出、恢复和只读冻结素材认证。"""

import hashlib
import json

import pytest

from tests.life.conftest import start_activity
from tests.life.environment_runtime import act, finish_activity, reach, run_to_end
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import restore_branch
from worldmock.footprints.export import export_run
from worldmock.footprints.models import AssetRef
from worldmock.footprints.validation import validate_export


@pytest.fixture
def completed_engine(engine_factory):
    engine = engine_factory()
    _, started = act(engine, "browse", start_activity("interact", "美团"))
    assert started.status == "in_progress"
    finish_activity(engine, started, "browse")
    run_to_end(engine)
    snapshot = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    return engine, point


def test_complete_run_exports_authenticated_browse_and_frozen_inputs(completed_engine, tmp_path):
    store = completed_engine[0].context.store
    output = tmp_path / "export"
    export_run(store, store.last_event_seq, output)
    manifest, _, activities = validate_export(output)
    assert manifest["experiment_completed"] is True
    assert all(len(row["source_event_ids"]) == len(set(row["source_event_ids"])) for row in activities)
    browse = next(row for row in activities if row["activity_type"] == "interact")
    assert browse["data"]["content_id"] is None
    usage = [json.loads(row) for row in (output / "app-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["fact_kind"] == "app_use" and row["activity_id"] == browse["activity_id"] for row in usage)
    assert not any(row["fact_kind"] == "content_consumed" and row["activity_id"] == browse["activity_id"]
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
    _, started = act(engine, "delivery-purchase", start_activity("purchase", "柠檬茶"))
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
    manifest, exported_events, _ = validate_export(output)
    assert manifest["experiment_completed"] is True
    purchases = [json.loads(row) for row in (output / "purchases.jsonl").read_text(encoding="utf-8").splitlines()]
    purchase = next(row for row in purchases if row["fact_kind"] == "purchase")
    receipts = [row for row in purchases if row["fact_kind"] == "receipt"]
    assert len(receipts) == 1 and receipts[0]["order_id"] == purchase["order_id"] == paid.order_id
    assert receipts[0]["received_at"] == paid.expected_at
    assert receipts[0]["goods_id"] == purchase["goods_id"] == paid.goods_id
    arrival = next(row for row in exported_events if row["data"].get("kind") == "delivery_arrival")
    assert arrival["data"]["orders"][0]["received_at"] == paid.expected_at
    assert not any(row["fact_kind"] == "pickup_ready" for row in purchases)
