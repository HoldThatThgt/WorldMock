"""真实购买、互动及冻结输入导出的完整来源认证。"""

from copy import deepcopy
import hashlib
import json

import pytest

from tests.footprints.recording import recorded_engine
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export


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
    assert manifest["checkpoint"]["state"]["schema_version"] == 13
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage"}
    assert {row["activity_type"] for row in activities} >= {"purchase", "interact"}
    receipts = [row for row in events if row["data"].get("kind") == "goods_received"]
    assert len(receipts) == 2 and all(row["agent_id"] == 1 and row["resource_changes"] == {} for row in receipts)
    # 购买尝试只认 start_side_activity 的 purchase 请求，一次请求一行：两份商品的一次购买是一行，用 app 的那次不算。
    side = [row for row in events if row["event_type"] == "action_requested"
            and row["data"]["tool_name"] == "start_side_activity"]
    assert [(row["action_id"], row["data"]["request"]) for row in side] == [
        ("action-recorded-basket", {"action": "purchase", "target": ["柠檬茶", "纸巾"]}),
        ("action-recorded-content", {"action": "interact", "target": ["哔哩哔哩"]})]
    bought = [json.loads(line) for line in (real_export / "purchases.jsonl").read_bytes().splitlines()]
    attempts = [row for row in bought if row["fact_kind"] == "order_attempt"]
    assert [(row["action_id"], row["request_event_id"], row["action"], row["status"], row["error_code"])
            for row in attempts] == [("action-recorded-basket", side[0]["event_id"], "purchase", "in_progress", None)]
    assert {row["action_id"] for row in bought if row["fact_kind"] == "purchase"} == {"action-recorded-basket"}
    assert len([row for row in bought if row["fact_kind"] == "purchase"]) == 2
    facts = [json.loads(line) for line in (real_export / "app-usage.jsonl").read_bytes().splitlines()]
    consumed = [row for row in facts if row["fact_kind"] == "content_consumed"]
    assert consumed and all(row["app_id"] == "bilibili" for row in consumed)
    assert len({row["activity_id"] for row in facts if row["fact_kind"] == "app_use"}) == 1
    assert all(len(row["source_event_ids"]) == len(set(row["source_event_ids"])) for row in activities)


@pytest.mark.parametrize("version,reason", [
    ("export", "Export manifest is not a committed supported version"),
    ("world", "Export checkpoint world schema does not match the current code"),
    ("run", "Export frozen run manifest or environment identity is invalid"),
])
def test_retired_schema_is_rejected_before_consumption(real_export, version, reason):
    """上一版导出清单、上一版世界状态结构（12）与上一版运行清单各自在读取前被拒绝，原因各是各的。"""
    manifest, _ = read_export(real_export)
    if version == "export":
        manifest["schema_version"] = 3
    elif version == "world":
        manifest["checkpoint"]["state"]["schema_version"] = 12
    else:
        manifest["frozen_run_manifest"]["schema_version"] = 4
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
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
