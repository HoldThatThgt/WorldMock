"""两个脚本化的完整日用两个开始工具跑到截止、导出并通过认证；与冻结基线的逐字比对只在最终提交上做。"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.footprints.recording import artifact_rows, read_export, write_manifest, write_rows
from tests.life.conftest import action_identity
from tests.life.day_baseline_runtime import record_complete_day
from tests.life.observation_record import assert_observations_round_trip, record_observations
from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export

PROJECT = Path(__file__).resolve().parents[3]


def _requests(case: Path) -> list[tuple[str, dict]]:
    """运行落库的全部开始请求：（工具名，所写参数），按事件次序。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    bodies = [json.loads(row[0]) for row in rows]
    return [(body["data"]["tool_name"], body["data"]["request"]) for body in bodies
            if body["event_type"] == "action_requested" and body["data"]["tool_name"].startswith("start_")]


def _concluded_names(case: Path) -> list[str]:
    """运行落库的全部 conclude_activity 请求点到的活动名，按事件次序。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    bodies = [json.loads(row[0]) for row in rows]
    return [item["activity_name"] for body in bodies
            if body["event_type"] == "action_requested" and body["data"]["tool_name"] == "conclude_activity"
            for item in body["data"]["request"]["items"]]


def test_activity_day_names_units_buys_portions_and_sleeps_at_the_office(monkeypatch, tmp_path):
    """活动日：同一轮在家点名休息并买一份牛肉面、两份薯片（prefer 写「饿了么」）；查询「柠檬茶」（prefer 为 null），
    做完不收尾；同一轮驾车去公司并打开哔哩哔哩，在午睡之前明确收尾这次使用；在公司点名「季度销售数据整理」并做完；
    在公司睡 60 分钟；之后步行回家、夜里睡 450 分钟直到截止。运行到达截止、无失败事件、导出通过认证（由录制入口断言）。"""
    case = tmp_path / "activity"
    observations = record_observations(monkeypatch)
    recorded = record_complete_day(monkeypatch, case, PROJECT, "activity")
    _assert_every_observation_round_trips(case, observations)
    assert set(recorded) == {"persona_sha256", "config_sha256", "environment_identity", "seed", "summary"}
    assert recorded["seed"] == 20260105
    starts = _requests(case)
    assert starts[:7] == [
        ("start_main_activity", {"action": "interact", "target": "家", "detail": "测试休息"}),
        ("start_side_activity", {"action": "purchase", "target": ["牛肉面", "薯片", "薯片"], "prefer": "饿了么"}),
        ("start_side_activity", {"action": "query", "target": ["柠檬茶"], "prefer": None}),
        ("start_main_activity", {"action": "travel", "target": "公司", "detail": "驾车"}),
        ("start_side_activity", {"action": "interact", "target": ["哔哩哔哩"], "prefer": None}),
        ("start_main_activity", {"action": "interact", "target": "公司", "detail": "季度销售数据整理"}),
        ("start_main_activity", {"action": "sleep", "target": "60", "detail": None})]
    assert all(set(request) == {"action", "target", "prefer"}
               for tool, request in starts if tool == "start_side_activity")
    later = starts[7:]
    assert later[0] == ("start_main_activity", {"action": "travel", "target": "家", "detail": "步行"})
    assert {tool for tool, _ in later} == {"start_main_activity"}
    assert {"action": "sleep", "target": "450", "detail": None} in [request for _, request in later]
    assert not any(tool == "start_activity" for tool, _ in starts)
    _assert_preferred_orders_and_a_self_ending_query(case)
    _assert_the_media_use_is_concluded_before_the_nap(case)
    _assert_six_artifacts_with_phone_records(case / "export", tmp_path)


def _assert_every_observation_round_trips(case: Path, observations: list[tuple[str, dict]]) -> None:
    """整天的每条观察：存储层解码读回的与 observe() 构建的逐字节相同，observe() 的返回没有被改动，分件按活动开始的
    场景本地日期（这一天从本地零点、即 UTC 前一天 16:00 开始）；导出 events.jsonl 的每条观察与解码读回的相同。"""
    started = {row["activity_id"]: row["started_at"] for row in artifact_rows(case / "export", "activities")}
    store = FootprintStore(case / "run")
    try:
        assert_observations_round_trip(store, observations, started, store.manifest["window"]["timezone"])
        lines = (case / "export" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        exported = [row for row in map(json.loads, lines) if row["event_type"] == "observation"]
        assert [row["event_id"] for row in exported] == [identity for identity, _ in observations]
        for row in exported:
            assert canonical_json(row["data"]) == canonical_json(store.read_event(row["event_id"]).data)
    finally:
        store.close()


def _assert_the_media_use_is_concluded_before_the_nap(case: Path) -> None:
    """驾车去公司时打开的哔哩哔哩：运行里有一条 conclude_activity 点到这次使用的活动名，落在午睡那条开始请求之前；
    导出的 activities.jsonl 里这项活动的行带完整的 played，至少一项。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    requested = [json.loads(row[0]) for row in rows]
    requested = [body["data"] for body in requested if body["event_type"] == "action_requested"]
    (media,) = [row for row in artifact_rows(case / "export", "activities")
                if row["origin_action_id"] == action_identity("travel-media", 2)]
    assert media["activity_type"] == "interact" and media["data"]["app_id"] == "bilibili"
    assert len(media["data"]["played"]) >= 1 and "played_count" not in media["data"]
    concluding = [index for index, data in enumerate(requested) if data["tool_name"] == "conclude_activity"
                  and media["name"] in [item["activity_name"] for item in data["request"]["items"]]]
    (nap,) = [index for index, data in enumerate(requested)
              if data["tool_name"] == "start_main_activity" and data["request"] == {
                  "action": "sleep", "target": "60", "detail": None}]
    assert concluding and concluding[0] < nap


def _assert_preferred_orders_and_a_self_ending_query(case: Path) -> None:
    """prefer 写「饿了么」让订单换了店：薯片经饿了么从三杯咖啡来，牛肉面另选到一心面馆的美团，一次购买两张订单；
    那次查询做完即结束，运行里没有任何一条 conclude_activity 点到它。"""
    export = case / "export"
    bought = [row for row in artifact_rows(export, "purchases")
              if row["fact_kind"] == "purchase" and row["action_id"] == action_identity("home-basket", 2)]
    assert {(row["goods_id"], row["app_id"], row["store_id"]) for row in bought} == {
        ("chips", "eleme", "cafe"), ("beef-noodles", "meituan", "noodle-1")}
    placed = [row for row in artifact_rows(export, "app_usage")
              if row["fact_kind"] == "order_notification" and row["title"] == "订单已下单"]
    assert sorted(row["app_id"] for row in placed) == ["eleme", "meituan"]
    queries = [row for row in artifact_rows(export, "activities") if row["activity_type"] == "query"]
    assert [row["status"] for row in queries] == ["completed"]
    assert queries[0]["name"] not in _concluded_names(case)


def _assert_six_artifacts_with_phone_records(export: Path, scratch: Path) -> None:
    """活动日的导出含六份工件和各类手机记录；把任何一份带新记录的工件改写后重新签名，认证都拒绝。"""
    manifest, _ = read_export(export)
    assert manifest["schema_version"] == 8
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage",
                                          "location_stays"}
    usage, stays = artifact_rows(export, "app_usage"), artifact_rows(export, "location_stays")
    kinds = {row["activity_id"]: row["activity_type"] for row in artifact_rows(export, "activities")}
    # app 使用行既有人物点名使用的（interact），也有引擎代为使用的导航（travel）、下单与支付（purchase）和查询那几分钟
    # 所在的 app（query）。
    assert {kinds[row["activity_id"]] for row in usage if row["fact_kind"] == "app_use"} == {
        "interact", "travel", "purchase", "query"}
    assert {row["title"] for row in usage if row["fact_kind"] == "order_notification"} == {
        "订单已下单", "订单已发货", "订单已送达"}
    assert not [row for row in usage if row["fact_kind"] == "app_touch"]
    # 家、公司、家三段停留：运行开始那一段不是一次真实的进入，截止时还在家的那一段没有离开时刻。
    assert [(row["location_id"], row["entry_kind"]) for row in stays] == [
        ("home", "run_start"), ("office", "arrival"), ("home", "arrival")]
    assert stays[-1]["left_at"] is None and all(row["left_at"] is not None for row in stays[:-1])
    rewritten = {"location_stays": stays[:-1],
                 "app_usage": [row for row in usage if row["fact_kind"] != "order_notification"]}
    for name, rows in rewritten.items():
        copy = scratch / f"rewritten-{name}"
        shutil.copytree(export, copy)
        changed, _ = read_export(copy)
        write_rows(copy, changed, name, rows)
        write_manifest(copy, changed)
        with pytest.raises(IntegrityError, match="disagree with committed events$"):
            validate_export(copy)


def test_callback_day_drives_to_the_store_and_restores_the_state(monkeypatch, tmp_path):
    """回调日：驾车去四季便利，在店里购买纸巾和饭团（prefer 为 null，到店买，没有下单 app），driving_days 回到 3
    （由脚本断言），运行到达截止并导出。"""
    case = tmp_path / "callback"
    recorded = record_complete_day(monkeypatch, case, PROJECT, "callback")
    assert recorded["seed"] == 20260106
    starts = _requests(case)
    assert starts[:2] == [
        ("start_main_activity", {"action": "travel", "target": "四季便利", "detail": "驾车"}),
        ("start_side_activity", {"action": "purchase", "target": ["纸巾", "饭团"], "prefer": None})]
    bought = [row for row in artifact_rows(case / "export", "purchases")
              if row["fact_kind"] == "purchase" and row["action_id"] == action_identity("reset-purchase")]
    assert {(row["goods_id"], row["app_id"], row["store_id"]) for row in bought} == {
        ("tissue", None, "store"), ("rice-ball", None, "store")}
