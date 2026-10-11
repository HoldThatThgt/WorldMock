"""两个脚本化的完整日用两个开始工具跑到截止、导出并通过认证；与冻结基线的逐字比对只在最终提交上做。"""

import json
import sqlite3
from pathlib import Path

import pytest

from tests.life.day_baseline_runtime import record_complete_day

PROJECT = Path(__file__).resolve().parents[3]


def _requests(case: Path) -> list[tuple[str, dict]]:
    """运行落库的全部开始请求：（工具名，所写参数），按事件次序。"""
    with sqlite3.connect(case / "run" / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    bodies = [json.loads(row[0]) for row in rows]
    return [(body["data"]["tool_name"], body["data"]["request"]) for body in bodies
            if body["event_type"] == "action_requested" and body["data"]["tool_name"].startswith("start_")]


def test_activity_day_names_units_buys_portions_and_sleeps_at_the_office(monkeypatch, tmp_path):
    """活动日：同一轮在家点名休息并买一份牛肉面、两份薯片；同一轮驾车去公司并打开哔哩哔哩；在公司睡 60 分钟；
    之后步行回家、夜里睡 450 分钟直到截止。运行到达截止、无失败事件、导出通过认证（由录制入口断言）。"""
    case = tmp_path / "activity"
    recorded = record_complete_day(monkeypatch, case, PROJECT, "activity")
    assert set(recorded) == {"persona_sha256", "config_sha256", "environment_identity", "seed", "summary"}
    assert recorded["seed"] == 20260105
    starts = _requests(case)
    assert starts[:5] == [
        ("start_main_activity", {"action": "interact", "target": "家", "detail": "测试休息"}),
        ("start_side_activity", {"action": "purchase", "target": ["牛肉面", "薯片", "薯片"]}),
        ("start_main_activity", {"action": "travel", "target": "公司", "detail": "驾车"}),
        ("start_side_activity", {"action": "interact", "target": ["哔哩哔哩"]}),
        ("start_main_activity", {"action": "sleep", "target": "60", "detail": None})]
    later = starts[5:]
    assert later[0] == ("start_main_activity", {"action": "travel", "target": "家", "detail": "步行"})
    assert {tool for tool, _ in later} == {"start_main_activity"}
    assert {"action": "sleep", "target": "450", "detail": None} in [request for _, request in later]
    assert not any(tool == "start_activity" for tool, _ in starts)


def test_callback_day_drives_to_the_store_and_restores_the_state(monkeypatch, tmp_path):
    """回调日：驾车去四季便利，在店里购买纸巾和饭团，driving_days 回到 3（由脚本断言），运行到达截止并导出。"""
    case = tmp_path / "callback"
    recorded = record_complete_day(monkeypatch, case, PROJECT, "callback")
    assert recorded["seed"] == 20260106
    starts = _requests(case)
    assert starts[:2] == [
        ("start_main_activity", {"action": "travel", "target": "四季便利", "detail": "驾车"}),
        ("start_side_activity", {"action": "purchase", "target": ["纸巾", "饭团"]})]
