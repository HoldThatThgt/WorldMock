"""@brief 质量报告的时间线标签检查：投影出的时间线里不得出现英文种类名与内部标识（docs/design/05-recording-engine.md）。

从同批已认证事件用唯一的时间线渲染器 scripts/render_day.py（与导出同一实现）逐人逐日投影时间线，名称由调用方
从这次运行冻结的环境目录取好交进来；逐行检查活动标签：书名号《》与引号「」里的内容标题不参与检查；其余文字按英文与
数字连成的片段逐段判定，片段恰为 readable 中文种类表的英文键（sleep、interact 等）计一项英文种类名，形如内部标识
（task-zhaoming-client-a、agent:5、长十六进制串、#a1b2c3）计一项内部标识。两项分开计数，只报告不放行。
"""

from __future__ import annotations
from worldmock.foundation.time.calendar import local_day_bounds, split_local_days, window_days
from worldmock.recording_engine.projections.presentation import timeline_renderer

import importlib.util
import re
from pathlib import Path
from worldmock.foundation.errors import IntegrityError
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from worldmock.recording_engine.projections.presentation.display_wording import DisplayNames
from worldmock.foundation.time.intervals import valid_interval
from worldmock.recording_engine.events.models import Event
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id
from worldmock.state.observation.labels import (
    ENGLISH_KIND_NAMES,
)

#: 渲染器读取的事件类型：活动进度与订单记录的来源。
_TIMELINE_EVENT_TYPES = frozenset({"activity_progress", "action_result"})
#: 标签里的内容标题（书名号与引号内），不参与检查。
_QUOTED = re.compile(r"《[^》]*》|「[^」]*」")
#: 英文、数字与标识连接符连成的片段。
_TOKEN = re.compile(r"[A-Za-z0-9_#:\-]+")
#: 内部标识形态：小写片段以 - _ : 相连（task-…、agent:5）、十二位以上十六进制串、# 加六位十六进制；
#: 恰为英文种类名的片段（如 interact）先按种类名计，不重复计。
_ID_SHAPE = re.compile(r"[a-z][a-z0-9]*(?:[-_:][a-z0-9]+)+|[0-9a-f]{12,}|#[0-9a-f]{6}")
#: 两个检查项的名称。
ENGLISH_KIND_NAME = "english_kind_name"
INTERNAL_ID = "internal_id"


def timeline_label_report(events: list[Event], manifest: dict, names: DisplayNames) -> dict:
    """@brief 投影全部人物在运行窗口各日的时间线并统计标签问题。

    @param events 已经认证且按 event_seq 排序的完整事件。
    @param manifest 对应运行的冻结清单（取运行窗口与时区）。
    @param names 这次运行冻结的环境目录给出的显示名称。
    @return rows_checked、english_kind_names、internal_ids 与逐项 findings（人物、日期、起止、标签、检查项、片段）。
    """
    renderer = _load_render_day()
    progress, orders = timeline_sources(events)
    agents = sorted({body["agent_id"] for body in progress if isinstance(body.get("agent_id"), int)})
    rows_checked, findings = 0, []
    for agent_id in agents:
        agent_progress = [body for body in progress if body.get("agent_id") == agent_id]
        for day in window_days(manifest["window"]):
            for row in renderer.day_rows(agent_progress, orders, names, day, manifest["window"]["timezone"]):
                rows_checked += 1
                findings.extend({"agent_id": agent_id, "date": day.isoformat(), "interval": row["interval"],
                                 "label": row["label"], "item": item, "text": text}
                                for item, text in label_findings(row["label"]))
    return {"rows_checked": rows_checked,
            "english_kind_names": sum(item["item"] == ENGLISH_KIND_NAME for item in findings),
            "internal_ids": sum(item["item"] == INTERNAL_ID for item in findings),
            "findings": findings}


def timeline_sources(events: list[Event]) -> tuple[list[dict], dict]:
    """@brief 时间线的两份输入：全部 activity_progress 正文（按事件序号）与 action_result 给出的订单记录。

    导出的时间线文件与质量报告的标签检查从同一份已认证事件取，取的都是 model_dump(mode="json") 的正文。
    """
    bodies = [event.model_dump(mode="json") for event in events if event.event_type in _TIMELINE_EVENT_TYPES]
    progress = [body for body in bodies if body["event_type"] == "activity_progress"]
    return progress, orders_by_id([body for body in bodies if body["event_type"] == "action_result"])


def label_findings(label: str) -> list[tuple[str, str]]:
    """@brief 一行标签里的问题片段：（检查项，片段），按出现顺序；书名号与引号内的名称不检查。"""
    found = []
    for token in _TOKEN.findall(_QUOTED.sub("", label)):
        if token in ENGLISH_KIND_NAMES:
            found.append((ENGLISH_KIND_NAME, token))
        elif _ID_SHAPE.fullmatch(token):
            found.append((INTERNAL_ID, token))
    return found









def _load_render_day():
    """@brief 返回导出和查看命令共同使用的时间线实现。"""
    return timeline_renderer
