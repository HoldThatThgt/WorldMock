"""真实购买、互动及冻结输入导出的完整来源认证。"""

from copy import deepcopy
import hashlib
import json

import pytest

from tests.footprints.recording import recorded_engine, short_window
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.models import StartActivityRequest


@pytest.fixture
def real_export(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    directory = tmp_path / "actual-export"
    export_run(engine.context.store, point.last_event_seq, directory)
    validate_export(directory)
    return directory


def test_actual_purchase_content_and_current_artifacts_are_authenticated(real_export):
    manifest, events, activities = validate_export(real_export)
    assert manifest["schema_version"] == 4
    assert manifest["checkpoint"]["state"]["schema_version"] == 12
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage"}
    assert {row["activity_type"] for row in activities} >= {"purchase", "interact"}
    receipts = [row for row in events if row["data"].get("kind") == "goods_received"]
    assert len(receipts) == 2 and all(row["agent_id"] == 1 and row["resource_changes"] == {} for row in receipts)
    facts = [json.loads(line) for line in (real_export / "app-usage.jsonl").read_bytes().splitlines()]
    consumed = [row for row in facts if row["fact_kind"] == "content_consumed"]
    assert consumed and all(row["app_id"] == "bilibili" for row in consumed)
    assert len({row["activity_id"] for row in facts if row["fact_kind"] == "app_use"}) == 1
    assert all(len(row["source_event_ids"]) == len(set(row["source_event_ids"])) for row in activities)


@pytest.mark.parametrize("version", ["export", "world", "run"])
def test_retired_schema_is_rejected_before_consumption(real_export, version):
    manifest, _ = read_export(real_export)
    if version == "export":
        manifest["schema_version"] = 3
    elif version == "world":
        manifest["checkpoint"]["state"]["schema_version"] = 11
    else:
        manifest["frozen_run_manifest"]["schema_version"] = 4
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="supported version|world schema|frozen run manifest"):
        validate_export(real_export)


@pytest.mark.parametrize("corruption", [
    "missing_receipt", "duplicate_receipt", "wrong_owner", "receipt_money", "backwards_position",
    "outside_interval", "wrong_app", "retired_kind", "model_content", "private_content",
])
def test_rehashed_sources_and_projections_cannot_invent_real_behavior(real_export, corruption):
    manifest, events = read_export(real_export)
    receipt = next(row for row in events if row["data"].get("kind") == "goods_received")
    progress = next(row for row in events if "content" in row["data"])
    if corruption == "missing_receipt":
        receipt["data"]["kind"] = "omitted_receipt"
    elif corruption == "duplicate_receipt":
        duplicate = deepcopy(receipt)
        duplicate["event_id"] += "-duplicate"
        events.insert(events.index(receipt) + 1, duplicate)
        for sequence, event in enumerate(events, 1):
            event["event_seq"] = sequence
    elif corruption == "wrong_owner":
        receipt["agent_id"] = 2
    elif corruption == "receipt_money":
        receipt["resource_changes"] = {"account:agent:1": -100, "account:external:scenario": 100}
    elif corruption == "backwards_position":
        progress["data"]["content"]["position_after_seconds"] = -1
    elif corruption == "outside_interval":
        progress["data"]["interval_start"] = progress["occurred_at"]
    elif corruption == "wrong_app":
        progress["data"]["content"]["app_id"] = "douyin"
    elif corruption == "retired_kind":
        progress["data"]["activity"]["kind"] = "action"
    elif corruption == "model_content":
        progress["source_kind"] = "model"
    else:
        progress["visibility"] = "system"
    with pytest.raises(IntegrityError):
        reproject(real_export, manifest, events)
        validate_export(real_export)


@pytest.mark.parametrize("artifact", ["activities", "verified_experiences", "purchases", "app_usage"])
def test_rehashed_projection_cannot_disagree_with_actual_events(real_export, artifact):
    manifest, _ = read_export(real_export)
    write_rows(real_export, manifest, artifact, [{"invented": True}])
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="disagree"):
        validate_export(real_export)


def test_two_purchases_on_the_same_route_export_with_one_payment_per_purchase(engine_factory, tmp_path):
    engine = engine_factory(mutate=short_window)
    for label, goods in (("first-visit", "柠檬茶"), ("second-visit", "纸巾")):
        _, result = act(engine, label, StartActivityRequest(items=[{"action": "purchase", "target": goods}]))
        assert result.status == "in_progress", result.error_code
        finish_activity(engine, result, label)
    first, second = sorted(engine.context.state.deliveries.values(), key=lambda order: order.created_at)
    assert first.created_at < second.created_at and first.group_key == second.group_key
    assert all((order.store_id, order.destination_id, order.app_id, order.fulfillment)
               == ("store", "store", None, "pickup") for order in (first, second))
    run_to_end(engine)
    snapshot = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    export_run(engine.context.store, point.last_event_seq, tmp_path / "same-route")
    _, exported, _ = validate_export(tmp_path / "same-route")
    payments = [row["data"] for row in exported if row["data"].get("kind") == "purchase_payment"]
    assert [row["order_ids"] for row in payments] == [[first.order_id], [second.order_id]]
    assert len({row["activity_id"] for row in payments}) == 2


def write_rows(directory, manifest, name, rows):
    """@brief 改写被测导出文件并刷新真实字节摘要和行数，不用损坏哈希代替行为认证。"""
    from worldmock.footprints.models import canonical_json
    descriptor = manifest["artifacts"][name]
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    (directory / descriptor["path"]).write_bytes(raw)
    descriptor.update(sha256=hashlib.sha256(raw).hexdigest(), rows=len(rows))


def reproject(directory, manifest, events):
    """@brief 五份工件和质量全部按被污染后的事件重算，失败只能来自实际语义约束。"""
    from worldmock.footprints.app_usage import project_app_usage
    from worldmock.footprints.export import _verified_experience_rows, reconstruct_activities
    from worldmock.footprints.models import Event, canonical_json, sha256_json
    from worldmock.footprints.purchases import project_purchase_facts
    from worldmock.footprints.quality import build_quality_report
    records = [Event.model_validate(event) for event in events]
    activities = reconstruct_activities(records)
    write_rows(directory, manifest, "events", events)
    write_rows(directory, manifest, "activities", activities)
    write_rows(directory, manifest, "verified_experiences",
               _verified_experience_rows(manifest["frozen_run_manifest"]["agent_ids"], records))
    write_rows(directory, manifest, "purchases", project_purchase_facts(events))
    write_rows(directory, manifest, "app_usage", project_app_usage(events))
    quality = canonical_json(build_quality_report(records, activities, manifest["frozen_run_manifest"])) + b"\n"
    (directory / "quality.json").write_bytes(quality)
    manifest["quality_report"].update(size_bytes=len(quality), sha256=hashlib.sha256(quality).hexdigest())
    checkpoint = manifest["checkpoint"]
    checkpoint["last_event_seq"] = manifest["cutoff_event_seq"] = len(events)
    checkpoint["record_sha256"] = sha256_json({key: value for key, value in checkpoint.items()
                                               if key != "record_sha256"})
    (directory / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    return activities


def read_export(directory):
    """@brief 原始证据是已认证过的当前真实运行导出。"""
    manifest = json.loads((directory / "manifest.json").read_bytes())
    events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    return manifest, events
