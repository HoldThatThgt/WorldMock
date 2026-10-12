"""真实购买、互动及冻结输入导出的完整来源认证。"""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from tests.footprints.recording import (artifact_rows, final_checkpoint, read_export, recorded_engine, reproject,
                                        short_window, write_manifest, write_rows)
from tests.life.conftest import purchase
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, finish_activity, run_to_end
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
    assert manifest["schema_version"] == 6
    assert manifest["checkpoint"]["state"]["schema_version"] == 14
    assert {name: row["path"] for name, row in manifest["artifacts"].items()} == {
        "events": "events.jsonl", "activities": "activities.jsonl",
        "verified_experiences": "verified-experiences.jsonl", "purchases": "purchases.jsonl",
        "app_usage": "app-usage.jsonl", "location_stays": "location-stays.jsonl"}
    assert all(set(row) == {"path", "rows", "sha256"} for row in manifest["artifacts"].values())
    # 地点停留每段一行：运行开始时已在便利店的那一段排最前；截止时还在的那一段没有离开时刻。
    stays = artifact_rows(real_export, "location_stays")
    assert (stays[0]["location_id"], stays[0]["entry_kind"]) == ("store", "run_start")
    assert (stays[-1]["left_at"], stays[-1]["leave_source_event_id"]) == (None, None)
    assert all(row["left_at"] is not None for row in stays[:-1])
    assert manifest["artifacts"]["location_stays"]["rows"] == len(stays)
    assert {row["activity_type"] for row in activities} >= {"purchase", "interact"}
    receipts = [row for row in events if row["data"].get("kind") == "goods_received"]
    assert len(receipts) == 2 and all(row["agent_id"] == 1 and row["resource_changes"] == {} for row in receipts)
    # 购买尝试只认 start_side_activity 的 purchase 请求，一次请求一行：两份商品的一次购买是一行，用 app 的那次不算。
    side = [row for row in events if row["event_type"] == "action_requested"
            and row["data"]["tool_name"] == "start_side_activity"]
    assert [(row["action_id"], row["data"]["request"]) for row in side] == [
        ("action-recorded-basket", {"action": "purchase", "target": ["柠檬茶", "纸巾"], "prefer": None}),
        ("action-recorded-content", {"action": "interact", "target": ["哔哩哔哩"], "prefer": None})]
    bought = artifact_rows(real_export, "purchases")
    attempts = [row for row in bought if row["fact_kind"] == "order_attempt"]
    assert [(row["action_id"], row["request_event_id"], row["action"], row["status"], row["error_code"])
            for row in attempts] == [("action-recorded-basket", side[0]["event_id"], "purchase", "in_progress", None)]
    assert {row["action_id"] for row in bought if row["fact_kind"] == "purchase"} == {"action-recorded-basket"}
    assert len([row for row in bought if row["fact_kind"] == "purchase"]) == 2
    facts = artifact_rows(real_export, "app_usage")
    consumed = [row for row in facts if row["fact_kind"] == "content_consumed"]
    assert consumed and all(row["app_id"] == "bilibili" for row in consumed)
    by_type = {row["activity_id"]: row["activity_type"] for row in activities}
    used = [row for row in facts if row["fact_kind"] == "app_use"]
    assert [by_type[row["activity_id"]] for row in used if row["app_id"] == "bilibili"] == ["interact"]
    # 其余 app 使用行都是引擎代为使用的：到店购买只可能有支付 app 的付款区间（到店订单没有下单 app），
    # 之后走回家的那趟出行是导航。
    others = {(by_type[row["activity_id"]], row["app_id"]) for row in used if row["app_id"] != "bilibili"}
    assert others <= {("purchase", "alipay"), ("purchase", "wechat"), ("travel", "amap")}
    assert all(len(row["source_event_ids"]) == len(set(row["source_event_ids"])) for row in activities)


@pytest.mark.parametrize("version,reason", [
    ("export", "Export manifest is not a committed supported version"),
    ("world", "Export checkpoint world schema does not match the current code"),
    ("run", "Export frozen run manifest or environment identity is invalid"),
    ("five_artifacts", "Export artifact set is invalid"),
    ("extra_artifact", "Export artifact set is invalid"),
])
def test_retired_schema_is_rejected_before_consumption(real_export, version, reason):
    """上一版导出清单（5）、上一版世界状态结构（13）、上一版运行清单（5），以及没有地点停留的五份工件集合，各自在读取前
    被拒绝，原因各是各的。"""
    manifest, _ = read_export(real_export)
    if version == "export":
        manifest["schema_version"] = 5
    elif version == "world":
        manifest["checkpoint"]["state"]["schema_version"] = 13
    elif version == "five_artifacts":
        del manifest["artifacts"]["location_stays"]
    elif version == "extra_artifact":
        manifest["artifacts"]["navigation"] = dict(manifest["artifacts"]["location_stays"])
    else:
        assert manifest["frozen_run_manifest"]["schema_version"] == 6
        manifest["frozen_run_manifest"]["schema_version"] = 5
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
        validate_export(real_export)


def test_quality_report_carrying_the_previous_structure_number_is_rejected(real_export):
    """质量报告的结构号是 8：把导出里的报告改回上一版的 7，并把清单里它的长度与摘要重写成与新字节一致，
    认证按事件重算整份报告后仍然拒绝；只改报告不改清单时先被字节摘要拦下。"""
    from worldmock.footprints.models import canonical_json

    manifest, _ = read_export(real_export)
    path = real_export / "quality.json"
    quality = json.loads(path.read_bytes())
    assert quality["schema_version"] == 8
    retired = canonical_json({**quality, "schema_version": 7})
    path.write_bytes(retired)
    with pytest.raises(IntegrityError, match="^Export quality report bytes do not match its manifest$"):
        validate_export(real_export)
    manifest["quality_report"].update(size_bytes=len(retired), sha256=hashlib.sha256(retired).hexdigest())
    write_manifest(real_export, manifest)
    with pytest.raises(IntegrityError, match="^Export quality report disagrees with authoritative events$"):
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


@pytest.mark.parametrize("artifact", ["activities", "verified_experiences", "purchases", "app_usage",
                                      "location_stays"])
def test_rehashed_projection_cannot_disagree_with_actual_events(real_export, artifact):
    manifest, _ = read_export(real_export)
    write_rows(real_export, manifest, artifact, [{"invented": True}])
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="disagree"):
        validate_export(real_export)


def test_location_stays_artifact_is_compared_with_the_projection_of_the_events(engine_factory, tmp_path):
    """一小时里什么都没买的运行：人物从便利店走回家，停留工件两行；删掉后一行并重新签名，认证按停留工件与事件不符拒绝。"""
    engine, point = recorded_engine(engine_factory, purchases=False, content=False)
    directory = tmp_path / "quiet-hour"
    export_run(engine.context.store, point.last_event_seq, directory)
    validate_export(directory)
    manifest, _ = read_export(directory)
    stays = artifact_rows(directory, "location_stays")
    assert [(row["location_id"], row["entry_kind"]) for row in stays] == [("store", "run_start"), ("home", "arrival")]
    write_rows(directory, manifest, "location_stays", stays[:1])
    write_manifest(directory, manifest)
    with pytest.raises(IntegrityError, match="^Export location stays disagree with committed events$"):
        validate_export(directory)


def test_two_purchases_on_the_same_route_export_with_one_payment_per_purchase(engine_factory, tmp_path):
    engine = engine_factory(mutate=short_window)
    for label, goods in (("first-visit", "柠檬茶"), ("second-visit", "纸巾")):
        _, result = act(engine, label, purchase(goods))
        assert result.status == "in_progress", result.error_code
        finish_activity(engine, result, label)
    first, second = sorted(engine.context.state.deliveries.values(), key=lambda order: order.created_at)
    assert first.created_at < second.created_at and first.group_key == second.group_key
    assert all((order.store_id, order.destination_id, order.app_id, order.fulfillment)
               == ("store", "store", None, "pickup") for order in (first, second))
    run_to_end(engine)
    point = final_checkpoint(engine)
    export_run(engine.context.store, point.last_event_seq, tmp_path / "same-route")
    _, exported, _ = validate_export(tmp_path / "same-route")
    payments = [row["data"] for row in exported if row["data"].get("kind") == "purchase_payment"]
    assert [row["order_ids"] for row in payments] == [[first.order_id], [second.order_id]]
    assert len({row["activity_id"] for row in payments}) == 2


def test_one_order_over_two_delivery_groups_exports_with_a_single_payment(engine_factory, tmp_path):
    """同一个内容 app 上两家店的外卖是一张订单：导出自查按订单归组，一条付款事件列出两个配送组的记录即通过。"""
    root = copy_environment(tmp_path / "one-app")
    goods = catalog_rows(root, "merchant-catalog.jsonl")
    next(row for row in goods if row["goods_id"] == "lemon-tea")["store_kinds"] = ["cafe"]
    write_catalog_rows(root, "merchant-catalog.jsonl", goods)
    places = {row["location_id"]: row for row in catalog_rows(root, "location.jsonl")}
    places["cafe"]["app_ids"], places["store"]["app_ids"] = ["meituan"], ["eleme"]
    write_catalog_rows(root, "location.jsonl", list(places.values()))
    engine = engine_factory(environment_root=root, mutate=lambda persona, config: config.update(
        stop_time=config["start_time"] + timedelta(hours=1)))
    _, result = act(engine, "two-stores", purchase("牛肉面", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    finish_activity(engine, result, "two-stores")
    orders = sorted(engine.context.state.deliveries.values(), key=lambda order: order.order_id)
    assert len({order.group_key for order in orders}) == 2 and {order.app_id for order in orders} == {"meituan"}
    run_to_end(engine)
    assert {order.status for order in engine.context.state.deliveries.values()} == {"delivered"}
    point = final_checkpoint(engine)
    export_run(engine.context.store, point.last_event_seq, tmp_path / "one-order")
    _, exported, _ = validate_export(tmp_path / "one-order")
    (payment,) = [row for row in exported if row["data"].get("kind") == "purchase_payment"]
    assert payment["data"]["order_ids"] == [order.order_id for order in orders]
    assert payment["data"]["amount_fen"] == sum(order.total_price_fen for order in orders) == 5000
    arrivals = [row for row in exported if row["data"].get("kind") == "delivery_arrival"]
    assert len(arrivals) == 2 and all(row["caused_by"] == [payment["event_id"]] for row in arrivals)
    payment["data"]["order_ids"] = [orders[0].order_id]
    manifest, _ = read_export(tmp_path / "one-order")
    with pytest.raises(IntegrityError, match="^Purchase payment disagrees with its actual order$"):
        reproject(tmp_path / "one-order", manifest, exported)
        validate_export(tmp_path / "one-order")


@pytest.mark.parametrize("rules_version", [4, 6, "5", True])
def test_export_frozen_under_another_rules_version_is_rejected_before_consumption(real_export, rules_version):
    """@brief 冻结运行清单里的规则版本不是当前规则版本时，导出在读取工件之前被拒绝。"""
    manifest, _ = read_export(real_export)
    assert manifest["frozen_run_manifest"]["environment_identity"]["rules_version"] == 5
    manifest["frozen_run_manifest"]["environment_identity"]["rules_version"] = rules_version
    (real_export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="^Export frozen run manifest or environment identity is invalid$"):
        validate_export(real_export)
