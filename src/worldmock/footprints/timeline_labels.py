"""@brief 质量报告的时间线标签检查：投影出的时间线里不得出现英文种类名与内部标识（docs/design/05-recording-engine.md ）。

从同批已认证事件用唯一的时间线渲染器 scripts/render_day.py（与导出同一实现）逐人逐日投影时间线，
逐行检查活动标签：书名号《》与引号「」里是作品标题与邮件主题，不参与检查；其余文字按英文与数字连成的片段
逐段判定，片段恰为 readable 中文种类表的英文键（call、work 等）计一项英文种类名，形如内部标识
（task-zhaoming-client-a、agent:5、长十六进制串、#a1b2c3）计一项内部标识。两项分开计数，只报告不放行。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from worldmock.errors import IntegrityError
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from worldmock.footprints.intervals import valid_interval
from worldmock.footprints.models import Event
from worldmock.life.readable import ENGLISH_KIND_NAMES

#: 渲染器读取的事件类型：活动进度与名称来源。
_TIMELINE_EVENT_TYPES = frozenset({"activity_progress", "observation", "action_result"})
#: 标签里的作品标题与邮件主题（书名号与引号内），不参与检查。
_QUOTED = re.compile(r"《[^》]*》|「[^」]*」")
#: 英文、数字与标识连接符连成的片段。
_TOKEN = re.compile(r"[A-Za-z0-9_#:\-]+")
#: 内部标识形态：小写片段以 - _ : 相连（task-…、agent:5）、十二位以上十六进制串、# 加六位十六进制；
#: 恰为英文种类名的片段（如 play_game）先按种类名计，不重复计。
_ID_SHAPE = re.compile(r"[a-z][a-z0-9]*(?:[-_:][a-z0-9]+)+|[0-9a-f]{12,}|#[0-9a-f]{6}")
#: 两个检查项的名称。
ENGLISH_KIND_NAME = "english_kind_name"
INTERNAL_ID = "internal_id"


def timeline_label_report(events: list[Event], manifest: dict) -> dict:
    """@brief 投影全部人物在运行窗口各日的时间线并统计标签问题。

    @param events 已经认证且按 event_seq 排序的完整事件。
    @param manifest 对应运行的冻结清单（取运行窗口与时区）。
    @return rows_checked、english_kind_names、internal_ids 与逐项 findings（人物、日期、起止、标签、检查项、片段）。
    """


    renderer = _load_render_day()
    bodies = [event.model_dump(mode="json") for event in events if event.event_type in _TIMELINE_EVENT_TYPES]
    names = renderer.name_index(bodies)
    agents = sorted({body["agent_id"] for body in bodies
                     if body["event_type"] == "activity_progress" and isinstance(body.get("agent_id"), int)})
    rows_checked, findings = 0, []
    for agent_id in agents:
        progress = [body for body in bodies
                    if body["event_type"] == "activity_progress" and body.get("agent_id") == agent_id]
        for day in window_days(manifest["window"]):
            for row in renderer.day_rows(progress, names, day, manifest["window"]["timezone"]):
                rows_checked += 1
                findings.extend({"agent_id": agent_id, "date": day.isoformat(), "interval": row["interval"],
                                 "label": row["label"], "item": item, "text": text}
                                for item, text in label_findings(row["label"]))
    return {"rows_checked": rows_checked,
            "english_kind_names": sum(item["item"] == ENGLISH_KIND_NAME for item in findings),
            "internal_ids": sum(item["item"] == INTERNAL_ID for item in findings),
            "findings": findings}


def label_findings(label: str) -> list[tuple[str, str]]:
    """@brief 一行标签里的问题片段：（检查项，片段），按出现顺序；书名号与引号内的名称不检查。"""
    found = []
    for token in _TOKEN.findall(_QUOTED.sub("", label)):
        if token in ENGLISH_KIND_NAMES:
            found.append((ENGLISH_KIND_NAME, token))
        elif _ID_SHAPE.fullmatch(token):
            found.append((INTERNAL_ID, token))
    return found


def window_days(window: dict) -> list[date]:
    """@brief 冻结窗口覆盖的本地日期，沿真实本地零点切分。"""
    return [day for day, _, _ in split_local_days(window["start"], window["end"], window["timezone"])]



def local_day_bounds(day: date, timezone: str | ZoneInfo) -> tuple[int, int]:
    """@brief 返回场景本地日的实际整数秒边界，夏令时日不强定为二十四小时。"""
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return int(start.timestamp()), int(end.timestamp())


def split_local_days(start: int, end: int, timezone: str | ZoneInfo) -> list[tuple[date, int, int]]:
    """@brief 将正时长区间按场景本地零点切分，保留每段真实 epoch 秒。"""
    if not valid_interval(start, end):
        return []
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    result = []
    cursor = start
    while cursor < end:
        day = datetime.fromtimestamp(cursor, zone).date()
        _, boundary = local_day_bounds(day, zone)
        stop = min(end, boundary)
        result.append((day, cursor, stop))
        cursor = stop
    return result


def _load_render_day():
    """@brief 按文件路径加载时间线渲染器，保持底层的单一事实源。"""
    path = Path(__file__).resolve().parents[3] / "scripts" / "render_day.py"
    if not path.is_file() or path.is_symlink():
        raise IntegrityError("The timeline projection script is missing or unsafe")
    spec = importlib.util.spec_from_file_location("worldmock_export_render_day", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
