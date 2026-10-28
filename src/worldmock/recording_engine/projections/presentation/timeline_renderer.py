"""@brief 导出与查看器共用的真实活动时间线投影。"""
from __future__ import annotations
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id
from worldmock.foundation.time.calendar import local_day_bounds
from worldmock.foundation.time.intervals import valid_interval
from worldmock.recording_engine.projections.presentation.display_wording import (
    DisplayNames, activity_phrase, event_content_id,
)

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")


def _segments_for_day(progress: Iterable[dict], bounds: tuple[int, int]) -> list[dict]:
    """@brief 把 activity_progress 正文裁剪到当日区间，并记下每一段那条事件指向的内容身份。

    区间取事件 data 的 interval_start/interval_end（结算权威区间），
    与当日边界相交为空的事件丢弃；跨零点的区间被裁到 00:00。
    """
    day_start, day_end = bounds
    segments = []
    for body in progress:
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        raw_start, raw_end = data.get("interval_start"), data.get("interval_end")
        if not valid_interval(raw_start, raw_end):
            continue
        start = min(max(raw_start, day_start), day_end)
        end = min(max(raw_end, day_start), day_end)
        if end <= start:
            continue
        segments.append({"start": start, "end": end, "snapshot": data["activity"],
                         "content_id": event_content_id(data)})
    return segments


def _merge_runs(segments: list[dict]) -> list[dict]:
    """@brief 按活动身份和实际区间合并续行，保留并行活动各自的完整行。

    总时长按逐段真实区间累加（静默边界续行折进来），段数保留原始计数；行的快照取首段，内容身份按先后收集各段的；
    被其他活动打断后恢复的同一活动形成独立运行块，不跨断点合并。
    """
    grouped: dict[str, list[dict]] = {}
    for segment in segments:
        activity_id = str(segment["snapshot"].get("activity_id") or "")
        grouped.setdefault(activity_id, []).append(segment)
    runs: list[dict] = []
    for activity_id, parts in grouped.items():
        for segment in sorted(parts, key=lambda item: (item["start"], item["end"])):
            previous = runs[-1] if runs and runs[-1]["activity_id"] == activity_id else None
            if previous is not None and previous["end"] == segment["start"]:
                previous["end"] = segment["end"]
                previous["seconds"] += segment["end"] - segment["start"]
                previous["count"] += 1
                previous["content_ids"].append(segment["content_id"])
            else:
                runs.append({"activity_id": activity_id, "start": segment["start"], "end": segment["end"],
                             "seconds": segment["end"] - segment["start"], "count": 1,
                             "snapshot": segment["snapshot"], "content_ids": [segment["content_id"]]})
    return runs


def _fmt_clock(epoch: int, timezone: str) -> str:
    """@brief epoch 秒转场景时区 HH:MM。"""
    return datetime.fromtimestamp(epoch, ZoneInfo(timezone)).strftime("%H:%M")


def _fmt_duration(seconds: int) -> str:
    """@brief 秒数转 X 小时 Y 分钟的中文时长。"""
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} 小时 {rest} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{rest} 分钟"


def day_rows(progress: Iterable[dict], orders: Mapping[str, dict], names: DisplayNames, day: date,
             timezone: str) -> list[dict]:
    """@brief 该人物该日时间线的各行：起止、活动写法、总时长与段数，按起止与活动身份排序。

    :param progress: 该人物的 activity_progress 事件正文（按 event_seq）。
    :param orders: 订单身份到商品履约记录（footprints.purchases.orders_by_id）。
    :param names: 这次运行冻结的环境目录给出的显示名称。
    """
    runs = sorted(_merge_runs(_segments_for_day(progress, local_day_bounds(day, timezone))),
                  key=lambda run: (run["start"], run["end"], run["activity_id"]))
    return [{"interval": f"{_fmt_clock(run['start'], timezone)}–{_fmt_clock(run['end'], timezone)}",
             "label": activity_phrase(names, run["snapshot"], run["content_ids"], orders),
             "duration": _fmt_duration(run["seconds"]), "count": run["count"]} for run in runs]


def day_markdown(agent_id: int, day: date, rows: list[dict]) -> str:
    """@brief 时间线 markdown 全文：标题行、表头与各行；当日没有活动时写空标记。"""
    lines = [f"# 人物 {agent_id} · {day.isoformat()} 周{WEEKDAYS[day.weekday()]}", "",
             "| 起止 | 活动 | 总时长 | 段数 |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {row['interval']} | {row['label']} | {row['duration']} | {row['count']} |" for row in rows)
    if not rows:
        lines.append("当日无活动记录。")
    return "\n".join(lines) + "\n"


_RESULT_BODIES_SQL = ("SELECT body FROM events WHERE event_seq IN (SELECT event_seq FROM events "
                      "WHERE json_extract(body,'$.event_type')='action_result') ORDER BY event_seq")


def _progress_bodies(conn: sqlite3.Connection, agent_id: int) -> Iterator[dict]:
    """@brief 逐条读取该人物全部 activity_progress 事件正文（按 event_seq）。"""
    sql = ("SELECT body FROM events WHERE json_extract(body,'$.agent_id')=? "
           "AND json_extract(body,'$.event_type')='activity_progress' ORDER BY event_seq")
    return (json.loads(body) for (body,) in conn.execute(sql, (agent_id,)))


def _result_bodies(conn: sqlite3.Connection) -> list[dict]:
    """@brief 全库 action_result 事件正文（按 event_seq），订单记录的来源；经类型表达式索引取序号，只读这些正文。"""
    return [json.loads(body) for (body,) in conn.execute(_RESULT_BODIES_SQL)]


def render_day_markdown(conn: sqlite3.Connection, agent_id: int, day: date, timezone: str,
                        names: DisplayNames) -> str:
    """@brief 生成该人物该日的确定性时间线 markdown 全文；订单记录取自全库的 action_result。"""
    orders = orders_by_id(_result_bodies(conn))
    return day_markdown(agent_id, day, day_rows(_progress_bodies(conn, agent_id), orders, names, day, timezone))
