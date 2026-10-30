"""含导航、外卖、快递、到店购买和用 app 的一天：导出六份工件并通过认证，改写任何一条手机记录都被拒绝。"""

from copy import deepcopy
from datetime import timedelta
import json
import shutil

import pytest

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import (
    artifact_rows,
    export_completed,
    read_export,
    reproject,
    write_manifest,
    write_rows,
)
from tests.life.conftest import action_identity, advance_until_ready, purchase, sleep, travel, use_app
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, finish_activity
from tests.life.phone_records import (
    label_where,
    moment,
    paying_label,
    phone_environment,
    phone_window,
    purchase_draws,
    returning_label,
)
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.validation.validation import validate_export
from worldmock.exchange_engine.commerce.shipping import shipping_delay_seconds

DEPART, BUSY, FREE, ARRIVE = moment(8, 20), moment(8, 31), moment(8, 40), moment(8, 50)


def phone_day(engine):
    """一天的手机：夜里在家睡到 08:20；驾车去公司，08:31 到 08:40 路上用哔哩哔哩；到公司后一次买冰美式（外卖）
    和平台笔记本（快递），睡到傍晚；驾车去四季便利当场买纸巾；之后由收尾助手步行回家睡到截止。"""
    _, night = act(engine, "night", sleep(500))
    assert night.status == "in_progress", night.error_code
    finish_activity(engine, night, "night")
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    assert trip.status == "in_progress" and engine.context.state.current_time == DEPART
    assert advance_until_ready(engine) and engine.context.state.current_time == BUSY
    _, watching = act(engine, returning_label(engine, "commute", "watch", True), use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    finish_activity(engine, watching, "watch")
    finish_activity(engine, trip, "commute")
    assert engine.context.state.current_time == ARRIVE
    label = label_where("desk", lambda item: (purchase_draws(engine, action_identity(item), "luckin")[1],
                                              purchase_draws(engine, action_identity(item), "bilibili")[1])
                        == ("alipay", None))
    _, bought = act(engine, label, purchase("冰美式", "平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "desk")
    _, nap = act(engine, "desk-nap", sleep(480))
    assert nap.status == "in_progress", nap.error_code
    finish_activity(engine, nap, "desk-nap")
    _, errand = act(engine, "to-store", travel("四季便利", "驾车"))
    assert errand.status == "in_progress", errand.error_code
    finish_activity(engine, errand, "to-store")
    _, counter = act(engine, paying_label(engine, "counter", {None: "wechat"}), purchase("纸巾"))
    assert counter.status == "in_progress", counter.error_code
    finish_activity(engine, counter, "counter")
    return {"trip": trip.activity_id, "watching": watching.activity_id, "bought": bought.activity_id,
            "counter": counter.activity_id, "errand": errand.activity_id}


@pytest.fixture(scope="module")
def phone_export(tmp_path_factory, frozen_inputs):
    """整份导出只跑一次；每条用例复制一份再改写。"""
    root = tmp_path_factory.mktemp("phone-day")
    with engine_factory_at(root, frozen_inputs) as create:
        engine = create(phone_environment(root / "input"),
                        mutate=phone_window((0, 0), 24, progress_report_interval_seconds=660))
        activities = phone_day(engine)
        manifest, events, rows = export_completed(engine, root / "export")
        state = engine.context.state.model_dump(mode="json")
    return {"directory": root / "export", "activities": activities, "manifest": manifest, "events": events,
            "rows": rows, "state": state}


@pytest.fixture
def exported(phone_export, tmp_path):
    """这条用例自己的一份导出副本。"""
    shutil.copytree(phone_export["directory"], tmp_path / "export")
    return tmp_path / "export"


def of_kind(events, kind):
    return [row for row in events if row["data"].get("kind") == kind]


def test_day_with_every_phone_record_exports_six_artifacts_and_certifies(phone_export):
    manifest, events = phone_export["manifest"], phone_export["events"]
    assert manifest["schema_version"] == 8 and manifest["experiment_completed"] is True
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage",
                                          "location_stays"}
    directory, ids = phone_export["directory"], phone_export["activities"]
    assert sorted(path.name for path in directory.glob("*.jsonl")) == [
        "activities.jsonl", "app-usage.jsonl", "asset-index.jsonl", "events.jsonl", "location-stays.jsonl",
        "purchases.jsonl", "verified-experiences.jsonl"]
    usage = artifact_rows(directory, "app_usage")
    kinds = {row["activity_id"]: row["activity_type"] for row in phone_export["rows"]}
    uses = [row for row in usage if row["fact_kind"] == "app_use"]
    assert usage[:len(uses)] == uses and len(uses) == len({(row["activity_id"], row["app_id"]) for row in uses})
    # 导航是出行活动上的一行，路上被哔哩哔哩隔成两段；人物点名使用的哔哩哔哩是 interact 活动上的一行。
    navigation = next(row for row in uses if row["activity_id"] == ids["trip"])
    assert (navigation["app_id"], navigation["status"], kinds[ids["trip"]]) == ("amap", "completed", "travel")
    assert navigation["segments"] == [{"started_at": DEPART, "ended_at": BUSY},
                                      {"started_at": FREE, "ended_at": ARRIVE}]
    watched = next(row for row in uses if row["activity_id"] == ids["watching"])
    assert (watched["app_id"], kinds[ids["watching"]]) == ("bilibili", "interact")
    assert watched["segments"] == [{"started_at": BUSY, "ended_at": FREE}]
    # 那次购买：瑞幸咖啡下单、支付宝付款；哔哩哔哩下单并直付（下单段与付款段不相接，是两段）。
    bought = {row["app_id"]: row for row in uses if row["activity_id"] == ids["bought"]}
    assert set(bought) == {"luckin", "alipay", "bilibili"} and kinds[ids["bought"]] == "purchase"
    assert len(bought["bilibili"]["segments"]) == 2 and len(bought["alipay"]["segments"]) == 1
    counter = [row for row in uses if row["activity_id"] == ids["counter"]]
    assert [(row["app_id"], len(row["segments"])) for row in counter] == [("wechat", 1)]
    # 同一时刻只有一个 app 在前台。
    spans = sorted((part["started_at"], part["ended_at"]) for row in uses for part in row["segments"])
    assert all(first[1] <= second[0] for first, second in zip(spans, spans[1:]))
    assert not [row for row in usage if row["fact_kind"] == "app_touch"]


def test_exported_notices_follow_each_order_and_trip(phone_export):
    orders = phone_export["state"]["deliveries"]
    coffee, parcel, tissue = (next(row for row in orders.values() if row["goods_id"] == goods)
                              for goods in ("iced-americano", "online-notebook", "tissue"))
    usage = artifact_rows(phone_export["directory"], "app_usage")
    sent = [(row["title"], row["app_id"], row["occurred_at"], row["order_ids"]) for row in usage
            if row["fact_kind"] in {"order_notification", "payment_notification"}]
    paid = coffee["paid_at"]
    assert parcel["paid_at"] == paid
    shipped = {key: paid + shipping_delay_seconds(row["expected_at"] - paid)
               for key, row in (("coffee", coffee), ("parcel", parcel))}
    assert sorted(sent, key=lambda row: (row[2], row[3], row[0])) == sorted([
        ("订单已下单", "bilibili", paid, [parcel["order_id"]]), ("支付成功", "bilibili", paid, [parcel["order_id"]]),
        ("订单已下单", "luckin", paid, [coffee["order_id"]]), ("支付成功", "alipay", paid, [coffee["order_id"]]),
        ("订单已发货", "luckin", shipped["coffee"], [coffee["order_id"]]),
        ("订单已送达", "luckin", coffee["arrived_at"], [coffee["order_id"]]),
        ("订单已发货", "bilibili", shipped["parcel"], [parcel["order_id"]]),
        ("支付成功", "wechat", tissue["paid_at"], [tissue["order_id"]])], key=lambda row: (row[2], row[3], row[0]))
    # 快递要过几天才到站：一天的运行里它已发货、未到站，没有「订单已送达」，也还没有到站取件通知。
    assert parcel["status"] == "pending" and coffee["status"] == tissue["status"] == "delivered"
    assert all(row["activity_id"] is None for row in usage if row["fact_kind"] == "order_notification")
    assert not [row for row in artifact_rows(phone_export["directory"], "purchases")
                if row["fact_kind"] == "pickup_ready"]


def test_exported_stays_match_the_stay_sessions_of_the_final_checkpoint(phone_export):
    stays = artifact_rows(phone_export["directory"], "location_stays")
    sessions = [row for row in phone_export["state"]["sessions"].values() if row["kind"] == "stay"]
    assert [(row["session_id"], row["location_id"], row["entered_at"]) for row in stays] == [
        (row["session_id"], row["location_id"], row["started_at"]) for row in sessions]
    assert [row["location_id"] for row in stays] == ["home", "office", "store", "home"]
    assert [row["entry_kind"] for row in stays] == ["run_start", "arrival", "arrival", "arrival"]
    # 运行开始那一行等于运行开始时刻；截止时还在的那一行没有离开时刻，也没有离开的来源。
    first, last = stays[0], stays[-1]
    assert (first["entered_at"], first["left_at"]) == (moment(0), DEPART)
    assert (last["left_at"], last["leave_source_event_id"]) == (None, None)
    assert [row["left_at"] for row in stays[:-1]] == [row["ended_at"] for row in sessions[:-1]]
    assert stays[1]["entered_at"] == ARRIVE and all(row["agent_id"] == 1 for row in stays)
    sources = {row["event_id"]: row for row in phone_export["events"]}
    assert all(sources[row["enter_source_event_id"]]["data"]["kind"] == "location_entered" for row in stays)
    assert all(sources[row["leave_source_event_id"]]["data"]["kind"] == "location_left" for row in stays[:-1])
    assert phone_export["manifest"]["artifacts"]["location_stays"]["rows"] == 4


def test_quality_report_gains_nothing_from_the_phone_records(phone_export):
    quality = json.loads((phone_export["directory"] / "quality.json").read_bytes())
    assert quality["schema_version"] == 10
    assert not [key for key in quality if any(word in key for word in ("stay", "navigation", "notification"))]


def resign(directory, events):
    """按改写后的事件重算六份工件与质量报告并重新签名。"""
    manifest, _ = read_export(directory)
    for sequence, event in enumerate(events, 1):
        event["event_seq"] = sequence
    reproject(directory, manifest, events)


def rejected(directory, reason, events=None):
    """认证拒绝并给出对应的英文原因；给了事件就先按它们重新投影并签名，投影阶段就拒绝的同样算数。"""
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
        if events is not None:
            resign(directory, events)
        validate_export(directory)


COUNT = "Order notification count disagrees with actual payment, shipment or delivery"
CONTENT = "Order notification disagrees with its actual order event"
PHASES = "Purchase app use disagrees with its order phases"
AUTOMATIC = "Automatic app use disagrees with its foreground events"
NAVIGATION = "Navigation app use overlaps a side activity or leaves its trip"
FROZEN = "Foreground app disagrees with its frozen app role or binding"


@pytest.mark.parametrize("fault,reason", [
    ("placed_removed", COUNT), ("shipped_removed", COUNT), ("delivered_removed", COUNT),
    ("placed_twice", COUNT), ("notice_for_the_store_order", COUNT),
    ("placed_by_another_content_app", CONTENT), ("delivered_caused_by_the_payment", CONTENT),
    ("placed_lists_another_record", CONTENT),
    ("placed_by_a_payment_app", "Order notification disagrees with its frozen content app"),
    ("paid_notice_from_another_payment_app", "Payment notification disagrees with its actual purchase payment"),
    ("paid_notice_from_the_navigation_app", "Payment notification disagrees with its frozen app"),
    ("paid_notice_removed", "Payment notification count disagrees with actual payment"),
])
def test_rewritten_order_notices_are_refused(exported, phone_export, fault, reason):
    _, events = read_export(exported)
    notices = {row["data"]["title"]: row for row in reversed(of_kind(events, "order_notification"))
               if row["data"]["app_id"] == "luckin"}
    paid = next(row for row in of_kind(events, "payment_notification") if row["data"]["app_id"] == "alipay")
    payment = next(row for row in of_kind(events, "purchase_payment") if row["event_id"] == paid["caused_by"][0])
    if fault.endswith("_removed"):
        target = paid if fault == "paid_notice_removed" else notices[
            {"placed": "订单已下单", "shipped": "订单已发货", "delivered": "订单已送达"}[fault.split("_")[0]]]
        events.remove(target)
    elif fault == "placed_twice":
        copy = deepcopy(notices["订单已下单"])
        copy["event_id"] += "-again"
        events.insert(events.index(notices["订单已下单"]) + 1, copy)
    elif fault == "notice_for_the_store_order":
        counter = next(row for row in of_kind(events, "payment_notification") if row["data"]["app_id"] == "wechat")
        copy = deepcopy(notices["订单已下单"])
        copy.update(event_id=copy["event_id"] + "-store", occurred_at=counter["occurred_at"],
                    caused_by=counter["caused_by"], state_version_before=counter["state_version_before"],
                    state_version_after=counter["state_version_after"])
        copy["data"]["order_ids"] = counter["data"]["order_ids"]
        events.insert(events.index(counter) + 1, copy)
    elif fault == "placed_by_another_content_app":
        notices["订单已下单"]["data"]["app_id"] = "douyin"
    elif fault == "delivered_caused_by_the_payment":
        notices["订单已送达"]["caused_by"] = [payment["event_id"]]
    elif fault == "placed_lists_another_record":
        parcel = next(row for row in of_kind(events, "order_notification") if row["data"]["app_id"] == "bilibili")
        notices["订单已下单"]["data"]["order_ids"] = sorted(
            [*notices["订单已下单"]["data"]["order_ids"], *parcel["data"]["order_ids"]])
    elif fault == "placed_by_a_payment_app":
        notices["订单已下单"]["data"]["app_id"] = "alipay"
    elif fault == "paid_notice_from_another_payment_app":
        paid["data"]["app_id"] = "wechat"
    else:
        paid["data"]["app_id"] = "amap"
    rejected(exported, reason, events)


@pytest.mark.parametrize("fault,reason", [
    ("navigation_extended_over_the_app", NAVIGATION), ("navigation_before_departure", NAVIGATION),
    ("navigation_on_the_purchase", NAVIGATION),
    ("navigation_by_a_payment_app", FROZEN), ("ordering_by_a_payment_app", FROZEN),
    ("payment_by_the_navigation_app", FROZEN),
    ("payment_by_another_payment_app", PHASES), ("ordering_by_an_app_of_no_order", PHASES),
    ("payment_interval_removed", PHASES), ("ordering_into_the_payment", "App foreground step or interval is invalid"),
    ("ordering_before_the_purchase", AUTOMATIC), ("interval_of_no_activity", AUTOMATIC),
    ("ordering_over_the_previous_app", "App use intervals overlap or have no positive duration"),
    ("interval_of_the_app_activity", "App usage identity disagrees with its activity"),
])
def test_rewritten_foreground_intervals_are_refused(exported, phone_export, fault, reason):
    _, events = read_export(exported)
    ids = phone_export["activities"]
    front = {key: [row for row in of_kind(events, "app_foreground") if row["activity_id"] == identity]
             for key, identity in ids.items()}
    first, second = front["trip"]
    ordering = next(row for row in front["bought"] if row["data"]["app_id"] == "luckin")
    paying = next(row for row in front["bought"] if row["data"]["app_id"] == "alipay")
    if fault == "navigation_extended_over_the_app":
        second["data"]["interval_start"] -= 300
    elif fault == "navigation_before_departure":
        first["data"]["interval_start"] -= 60
    elif fault == "navigation_on_the_purchase":
        ordering["data"].update(step="navigation", app_id="amap")
        for row in events:
            if row["activity_id"] == ids["bought"] and "activity" in row["data"]:
                row["data"]["activity"]["travel_method_id"] = "driving"
    elif fault == "navigation_by_a_payment_app":
        first["data"]["app_id"] = "alipay"
    elif fault == "ordering_by_a_payment_app":
        ordering["data"]["app_id"] = "wechat"
    elif fault == "payment_by_the_navigation_app":
        paying["data"]["app_id"] = "amap"
    elif fault == "payment_by_another_payment_app":
        paying["data"]["app_id"] = "wechat"
    elif fault == "ordering_by_an_app_of_no_order":
        ordering["data"]["app_id"] = "douyin"
    elif fault == "payment_interval_removed":
        events.remove(paying)
    elif fault == "ordering_into_the_payment":
        ordering["data"]["interval_end"] += 5
    elif fault == "ordering_before_the_purchase":
        # 哔哩哔哩是这次购买的第一个下单段，从购买开始那一秒起；把它提前就越出了购买真实执行的区间。
        earliest = min(front["bought"], key=lambda row: row["data"]["interval_start"])
        assert earliest["data"]["app_id"] == "bilibili" and earliest["data"]["step"] == "ordering"
        earliest["data"]["interval_start"] -= 30
    elif fault == "ordering_over_the_previous_app":
        ordering["data"]["interval_start"] -= 30
    elif fault == "interval_of_the_app_activity":
        ordering["activity_id"] = ids["watching"]
    else:
        second["activity_id"] = f"activity-{action_identity('ghost')}"
    rejected(exported, reason, events)


@pytest.mark.parametrize("fault,reason", [
    ("left_later", "Export location stays disagree with committed events"),
    ("row_removed", "Export location stays disagree with committed events"),
    ("row_added", "Export location stays disagree with committed events"),
    ("usage_notice_removed", "Export app usage facts disagree with committed events"),
    ("usage_interval_shortened", "Export app usage facts disagree with committed events"),
    ("old_manifest", "Export manifest is not a committed supported version"),
    ("five_artifacts", "Export artifact set is invalid"),
])
def test_rewritten_artifacts_and_the_old_manifest_are_refused(exported, fault, reason):
    manifest, _ = read_export(exported)
    stays, usage = artifact_rows(exported, "location_stays"), artifact_rows(exported, "app_usage")
    if fault == "left_later":
        stays[1]["left_at"] += 600
        write_rows(exported, manifest, "location_stays", stays)
    elif fault == "row_removed":
        write_rows(exported, manifest, "location_stays", stays[:-1])
    elif fault == "row_added":
        write_rows(exported, manifest, "location_stays", [*stays, {**stays[-1], "session_id": "session-1-9999"}])
    elif fault == "usage_notice_removed":
        write_rows(exported, manifest, "app_usage", [row for row in usage if row["fact_kind"] != "order_notification"])
    elif fault == "usage_interval_shortened":
        usage[0]["segments"][0]["ended_at"] -= 1
        write_rows(exported, manifest, "app_usage", usage)
    elif fault == "old_manifest":
        manifest["schema_version"] = 7
    else:
        del manifest["artifacts"]["location_stays"]
    write_manifest(exported, manifest)
    rejected(exported, reason)


@pytest.mark.parametrize("fault,reason", [
    ("last_arrival_event_removed", "Location stays disagree with the final checkpoint"),
    ("departure_event_removed", "Location stays overlap or are out of order"),
    ("arrival_event_removed", "Location stays overlap or are out of order"),
    ("arrival_elsewhere", "Location stays overlap or are out of order"),
    ("arrival_as_early_end", "Location stay disagrees with its actual travel"),
    ("arrival_caused_by_the_departure", "Location stay disagrees with its actual travel"),
    ("departure_caused_by_the_arrival", "Location stay disagrees with its actual travel"),
    ("run_start_twice", "Location stays overlap or are out of order"),
    ("stay_event_visible_to_the_person", "Location stay event fields or source are invalid"),
])
def test_rewritten_stay_events_are_refused(exported, fault, reason):
    _, events = read_export(exported)
    entered, left = of_kind(events, "location_entered"), of_kind(events, "location_left")
    if fault == "last_arrival_event_removed":
        events.remove(entered[-1])
    elif fault == "departure_event_removed":
        events.remove(left[-1])
    elif fault == "arrival_event_removed":
        events.remove(entered[1])
    elif fault == "arrival_elsewhere":
        entered[1]["data"]["location_id"] = "park"
    elif fault == "arrival_as_early_end":
        entered[1]["data"]["entry_kind"] = "travel_ended_early"
    elif fault == "arrival_caused_by_the_departure":
        entered[1]["caused_by"] = list(left[0]["caused_by"])
    elif fault == "departure_caused_by_the_arrival":
        left[1]["caused_by"] = list(entered[1]["caused_by"])
    elif fault == "run_start_twice":
        entered[1]["data"]["entry_kind"] = "run_start"
        entered[1]["caused_by"] = []
    else:
        entered[1]["visibility"] = "agent"
    if fault == "stay_event_visible_to_the_person":
        manifest, _ = read_export(exported)
        write_rows(exported, manifest, "events", events)
        write_manifest(exported, manifest)
        with pytest.raises(ValueError, match=reason):
            validate_export(exported)
        return
    rejected(exported, reason, events)


def two_hours(persona, config):
    """两小时的完整运行。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=2)


def test_callback_footprint_must_name_a_rule_of_the_frozen_callbacks(engine_factory, tmp_path):
    """回调足迹的规则身份是冻结的 callbacks.jsonl 里登记的规则：真实运行里足迹链写下的两个时间点通过认证；
    把规则身份改成没有登记的名字，或改成同一文件里一个状态的名字，重新投影并签名后都被拒绝。"""
    root = copy_environment(tmp_path / "input")
    steps = [{"kind": "browse", "app_pool": ["bilibili"], "pick": "random"},
             {"kind": "payment", "app_pool": ["alipay"], "pick": "random"}]
    write_catalog_rows(root, "callbacks.jsonl", [
        {"state": "driving_stock", "initial": 3, "cap": 3, "reset_on_goods": ["tissue"]},
        {"rule_id": "drive-trace", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
         "effect": {"type": "footprint_chain", "steps": steps}}])
    engine = engine_factory(root, mutate=two_hours)
    _, trip = act(engine, "drive", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    export_completed(engine, tmp_path / "export")
    touches = [row for row in artifact_rows(tmp_path / "export", "app_usage") if row["fact_kind"] == "app_touch"]
    assert [(row["app_id"], row["occurred_at"], row["activity_id"]) for row in touches] == [
        ("bilibili", trip.occurred_at, None), ("alipay", trip.occurred_at, None)]
    _, events = read_export(tmp_path / "export")
    assert [(row["data"]["rule_id"], row["data"]["step"]) for row in of_kind(events, "app_touch")] == [
        ("drive-trace", "browse"), ("drive-trace", "payment")]
    for position, name in enumerate(("another-rule", "driving_stock")):
        rewritten = tmp_path / f"rewritten-{position}"
        shutil.copytree(tmp_path / "export", rewritten)
        _, events = read_export(rewritten)
        of_kind(events, "app_touch")[-1]["data"]["rule_id"] = name
        rejected(rewritten, "App touch step disagrees with its source", events)
