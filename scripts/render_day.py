#!/usr/bin/env python3
"""@brief 从 footprints.sqlite 确定性投影人物一天的 timeline/<agent>-<date>.md。

同一 activity_id 首尾相接的 activity_progress 合并为一行（起止、总时长与段数），这一行的「活动」一栏是
worldmock.recording_engine.projections.presentation.display_wording 的写法：内容按各段进度事件指向的内容依次列出（相邻重复只写一次；多于三条时只写
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

from worldmock.recording_engine.projections.presentation.display_wording import (
    DisplayNames,
    activity_phrase,
    event_content_id,
)  # noqa: E402
from worldmock.recording_engine.validation.frozen_environment import frozen_display_names  # noqa: E402
from worldmock.foundation.time.intervals import valid_interval  # noqa: E402
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id  # noqa: E402
from worldmock.foundation.time.calendar import local_day_bounds
from worldmock.recording_engine.projections.presentation.timeline_renderer import day_rows, day_markdown, render_day_markdown  # noqa: E402

_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 全库 action_result 正文（按 event_seq）：经类型表达式索引取序号，只读这些正文。


def _connect(sqlite_path: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库，投影绝不写回运行目录。"""
    resolved = Path(sqlite_path).resolve()
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn




















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
