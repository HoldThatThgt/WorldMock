"""两个脚本化的完整日用两个开始工具跑到截止、导出并通过认证；与冻结基线的逐字比对只在最终提交上做。"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.footprints.recording import artifact_rows, read_export, write_manifest, write_rows
from tests.life.day_baseline_runtime import record_complete_day
from worldmock.errors import IntegrityError
from worldmock.footprints.validation import validate_export

PROJECT = Path(__file__).resolve().parents[3]


def _requests(case: Path) -> list[tuple[str, dict]]:
    """运行落库的全部开始请求：（工具名，所写参数），按事件次序。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    bodies = [json.loads(row[0]) for row in rows]
    return [(body["data"]["tool_name"], body["data"]["request"]) for body in bodies
            if body["event_type"] == "action_requested" and body["data"]["tool_name"].startswith("start_")]


def test_activity_day_names_units_buys_portions_and_sleeps_at_the_office(monkeypatch, tmp_path):
    """活动日：同一轮在家点名休息并买一份牛肉面、两份薯片；同一轮驾车去公司并打开哔哩哔哩；在公司点名
    「季度销售数据整理」并做完；在公司睡 60 分钟；
    之后步行回家、夜里睡 450 分钟直到截止。运行到达截止、无失败事件、导出通过认证（由录制入口断言）。"""
    case = tmp_path / "activity"
    recorded = record_complete_day(monkeypatch, case, PROJECT, "activity")
    assert set(recorded) == {"persona_sha256", "config_sha256", "environment_identity", "seed", "summary"}
    assert recorded["seed"] == 20260105
    starts = _requests(case)
    assert starts[:6] == [
        ("start_main_activity", {"action": "interact", "target": "家", "detail": "测试休息"}),
        ("start_side_activity", {"action": "purchase", "target": ["牛肉面", "薯片", "薯片"]}),
        ("start_main_activity", {"action": "travel", "target": "公司", "detail": "驾车"}),
        ("start_side_activity", {"action": "interact", "target": ["哔哩哔哩"]}),
        ("start_main_activity", {"action": "interact", "target": "公司", "detail": "季度销售数据整理"}),
        ("start_main_activity", {"action": "sleep", "target": "60", "detail": None})]
    later = starts[6:]
    assert later[0] == ("start_main_activity", {"action": "travel", "target": "家", "detail": "步行"})
    assert {tool for tool, _ in later} == {"start_main_activity"}
    assert {"action": "sleep", "target": "450", "detail": None} in [request for _, request in later]
    assert not any(tool == "start_activity" for tool, _ in starts)
    _assert_six_artifacts_with_phone_records(case / "export", tmp_path)


def _assert_six_artifacts_with_phone_records(export: Path, scratch: Path) -> None:
    """活动日的导出含六份工件和各类手机记录；把任何一份带新记录的工件改写后重新签名，认证都拒绝。"""
    manifest, _ = read_export(export)
    assert manifest["schema_version"] == 5
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage",
                                          "location_stays"}
    usage, stays = artifact_rows(export, "app_usage"), artifact_rows(export, "location_stays")
    kinds = {row["activity_id"]: row["activity_type"] for row in artifact_rows(export, "activities")}
    # app 使用行既有人物点名使用的（interact），也有引擎代为使用的导航（travel）和下单、支付（purchase）。
    assert {kinds[row["activity_id"]] for row in usage if row["fact_kind"] == "app_use"} == {
        "interact", "travel", "purchase"}
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
    """回调日：驾车去四季便利，在店里购买纸巾和饭团，driving_days 回到 3（由脚本断言），运行到达截止并导出。"""
    case = tmp_path / "callback"
    recorded = record_complete_day(monkeypatch, case, PROJECT, "callback")
    assert recorded["seed"] == 20260106
    starts = _requests(case)
    assert starts[:2] == [
        ("start_main_activity", {"action": "travel", "target": "四季便利", "detail": "驾车"}),
        ("start_side_activity", {"action": "purchase", "target": ["纸巾", "饭团"]})]
