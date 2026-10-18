#!/usr/bin/env python3
"""@brief 从 footprints.sqlite 确定性投影人物一天的 timeline/<agent>-<date>.md。

同一 activity_id 首尾相接的 activity_progress 合并为一行（起止、总时长与段数），这一行的「活动」一栏是
worldmock.footprints.display_wording 的写法：内容按各段进度事件指向的内容依次列出（相邻重复只写一次；多于三条时只写
前三条再写「等 K 条」），购买的商品和渠道取订单记录，
名称只取这次运行冻结的环境目录，取不到即运行故障（docs/design/05-recording-engine.md「显示写法与检查工具」）。
按 agent_id 与冻结清单的场景时区的模拟日期过滤；同库同参数输出逐字节相同。day_rows 也接受已排序的事件正文，
质量报告的时间线标签检查据此投影同一张时间线；导出的时间线文件同样由 day_rows 与 day_markdown 写出。
命令行写出的文件是 UTF-8、LF。

用法：
  python3 scripts/render_day.py <sqlite> --agent N --date YYYY-MM-DD [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from worldmock.footprints.display_wording import DisplayNames, activity_phrase, event_content_id  # noqa: E402
from worldmock.footprints.frozen_environment import frozen_display_names  # noqa: E402
from worldmock.footprints.intervals import valid_interval  # noqa: E402
from worldmock.footprints.purchases import orders_by_id  # noqa: E402
from worldmock.footprints.timeline_labels import local_day_bounds  # noqa: E402

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 全库 action_result 正文（按 event_seq）：经类型表达式索引取序号，只读这些正文。
_RESULT_BODIES_SQL = ("SELECT body FROM events WHERE event_seq IN (SELECT event_seq FROM events "
                      "WHERE json_extract(body,'$.event_type')='action_result') ORDER BY event_seq")


def _connect(sqlite_path: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库，投影绝不写回运行目录。"""
    resolved = Path(sqlite_path).resolve()
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _progress_bodies(conn: sqlite3.Connection, agent_id: int) -> Iterator[dict]:
    """@brief 逐条读取该人物全部 activity_progress 事件正文（按 event_seq）。"""
    sql = ("SELECT body FROM events WHERE json_extract(body,'$.agent_id')=? "
           "AND json_extract(body,'$.event_type')='activity_progress' ORDER BY event_seq")
    return (json.loads(body) for (body,) in conn.execute(sql, (agent_id,)))


def _result_bodies(conn: sqlite3.Connection) -> list[dict]:
    """@brief 全库 action_result 事件正文（按 event_seq），订单记录的来源；经类型表达式索引取序号，只读这些正文。"""
    return [json.loads(body) for (body,) in conn.execute(_RESULT_BODIES_SQL)]


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


def render_day_markdown(conn: sqlite3.Connection, agent_id: int, day: date, timezone: str,
                        names: DisplayNames) -> str:
    """@brief 生成该人物该日的确定性时间线 markdown 全文；订单记录取自全库的 action_result。"""
    orders = orders_by_id(_result_bodies(conn))
    return day_markdown(agent_id, day, day_rows(_progress_bodies(conn, agent_id), orders, names, day, timezone))


def main(argv: list[str] | None = None) -> None:
    """@brief 命令行入口：解析参数，按数据库所在运行目录的冻结目录取名称，渲染并写出 timeline/<agent>-<date>.md。"""
    parser = argparse.ArgumentParser(
        description="project one agent-day timeline markdown from footprints.sqlite")
    parser.add_argument("sqlite", type=Path)
    parser.add_argument("--agent", type=int, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if not _DATE_ARG.match(args.date):
        parser.error("--date must be formatted as YYYY-MM-DD")
    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        parser.error(f"--date is not a real calendar date: {args.date}")
        return
    run_dir = args.sqlite.parent
    manifest = json.loads((run_dir / "manifest.json").read_bytes())
    names = frozen_display_names(run_dir, manifest)
    conn = _connect(args.sqlite)
    try:
        markdown = render_day_markdown(conn, args.agent, day, manifest["window"]["timezone"], names)
    finally:
        conn.close()
    target_dir = args.out / "timeline"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"{args.agent}-{day.isoformat()}.md"
    out_path.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
