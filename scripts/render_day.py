#!/usr/bin/env python3
"""@brief 从 footprints.sqlite 确定性投影人物一天的 timeline/<agent>-<date>.md。

同一 activity_id 的连续 activity_progress 合并为一行（起止与总时长），
静默边界产生的分段折叠不单列；活动目标按真实内容标题与名称索引解析为地名、
内容标题、app 名称与出行方式名称；种类名取 worldmock.life.readable 的中文种类表
（单一来源），解析不到名称时只写中文种类名：时间线里不出现英文种类名与内部标识
（docs/design/05-recording-engine.md）。按 agent_id 与冻结清单的场景时区
的模拟日期过滤。同库同参数输出逐字节相同；name_index 与 day_rows 也接受已排序的事件正文，
质量报告的时间线标签检查据此投影同一张时间线。

用法：
  python3 scripts/render_day.py <sqlite> --agent N --date YYYY-MM-DD [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from worldmock.footprints.timeline_labels import local_day_bounds  # noqa: E402
from worldmock.footprints.intervals import valid_interval  # noqa: E402
from worldmock.exchange.constants import WALKING_TRAVEL_METHOD_ID, WALKING_TRAVEL_METHOD_NAME  # noqa: E402
from worldmock.life.readable import ACTIVITY_KIND_LABELS  # noqa: E402

#: 名称索引的类别：商品、地点、应用、实际内容与出行方式。
NAME_KINDS = ("goods", "locations", "apps", "contents", "travel_methods")
#: 种类表之外的活动种类的中文名。
_UNKNOWN_KIND_LABEL = "活动"

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _name_bodies(conn: sqlite3.Connection) -> Iterator[dict]:
    """@brief 逐条读取全库 observation/action_result 事件正文（按 event_seq），名称索引的来源。"""
    sql = ("SELECT body FROM events WHERE json_extract(body,'$.event_type') IN "
           "('observation','action_result') ORDER BY event_seq")
    return (json.loads(body) for (body,) in conn.execute(sql))


def _segments_for_day(progress: Iterable[dict], bounds: tuple[int, int]) -> list[dict]:
    """@brief 把 activity_progress 正文裁剪到当日区间。

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
        snapshot = data.get("activity") if isinstance(data.get("activity"), dict) else {}
        segments.append({"start": start, "end": end, "snapshot": snapshot})
    return segments


def _merge_runs(segments: list[dict]) -> list[dict]:
    """@brief 按活动身份和实际区间合并续行，保留并行活动各自的完整行。

    总时长按逐段真实区间累加（静默边界续行折进来），段数保留原始计数；
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
            else:
                runs.append({"activity_id": activity_id, "start": segment["start"], "end": segment["end"],
                             "seconds": segment["end"] - segment["start"], "count": 1,
                             "snapshot": segment["snapshot"]})
    return runs


def _dict_rows(value: object) -> list[dict]:
    """@brief 列表里的对象条目；不是列表时为空。"""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _remember(bucket: dict, key: object, name: object) -> None:
    """@brief 同一标识先出现者胜：非空标识与非空名称才登记。"""
    if isinstance(key, str) and key and isinstance(name, str) and name and key not in bucket:
        bucket[key] = name


def _absorb_observation(names: dict, data: dict) -> None:
    """@brief 吸收一条 observation/action_result 里的公开名称索引。"""
    fields = {"goods": ("goods_id", "name"), "locations": ("location_id", "name"),
              "apps": ("app_id", "name"), "contents": ("content_id", "title")}
    for category, (identity, label) in fields.items():
        for row in _dict_rows(data.get(category)):
            _remember(names[category], row.get(identity), row.get(label))
    # 出行方式的名称取观察里记下的冻结目录绑定：每条出行动作行带出行方式身份与它的名称。
    for binding in _dict_rows(data.get("catalog_bindings")):
        for action in _dict_rows(binding.get("actions")):
            _remember(names["travel_methods"], action.get("travel_method_id"), action.get("detail"))


def name_index(bodies: Iterable[dict]) -> dict:
    """@brief 从 observation/action_result 事件正文（按 event_seq）建立确定性的名称索引。

    同一标识先出现者胜，名称是世界静态事实，与过滤人物无关；其他事件类型忽略。
    解析不到名称时，标签只写中文种类名。引擎自带的步行不依赖观察，始终写「步行」。
    """
    names: dict = {kind: {} for kind in NAME_KINDS}
    names["travel_methods"][WALKING_TRAVEL_METHOD_ID] = WALKING_TRAVEL_METHOD_NAME
    for body in bodies:
        if body.get("event_type") in ("observation", "action_result") and isinstance(body.get("data"), dict):
            _absorb_observation(names, body["data"])
    return names


def _snapshot_data(snapshot: dict) -> dict:
    """@brief 活动快照的实际容器与内容数据。"""
    return snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}


def _travel_label(snapshot: dict, names: dict) -> str:
    """@brief 出行标签：出行方式名称加目的地地名；方式名称解析不到时写中文种类名，地名解析不到时只写方式。"""
    mode = names.get("travel_methods", {}).get(snapshot.get("travel_method_id"), ACTIVITY_KIND_LABELS["travel"])
    destination = names["locations"].get(snapshot.get("destination_id"))
    return f"{mode}去{destination}" if destination else mode


def _interact_label(snapshot: dict, names: dict) -> str:
    """@brief 互动只读取真实内容与应用名称。"""
    data = _snapshot_data(snapshot)
    app = names["apps"].get(data.get("app_id"))
    content = snapshot.get("content_title") or names["contents"].get(data.get("content_id"))
    label = f"使用{app}" if app else ACTIVITY_KIND_LABELS["interact"]
    return f"{label}《{content}》" if content else label


#: 只按四种当前活动读取，动作使用真实目录身份。
_LABELERS: dict[str, Callable[[dict, dict], str]] = {"travel": _travel_label, "interact": _interact_label}


def _activity_label(snapshot: dict, names: dict) -> str:
    """@brief 把活动快照解析为中文一行标签，不写英文种类名与内部标识。"""
    kind = str(snapshot.get("kind") or "")
    labeler = _LABELERS.get(kind)
    if labeler is not None:
        return labeler(snapshot, names)
    return ACTIVITY_KIND_LABELS.get(kind, _UNKNOWN_KIND_LABEL)


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


def day_rows(progress: Iterable[dict], names: dict, day: date, timezone: str) -> list[dict]:
    """@brief 该人物该日时间线的各行：起止、活动标签、总时长与段数，按起止与活动身份排序。

    :param progress: 该人物的 activity_progress 事件正文（按 event_seq）。
    :param names: name_index 建立的名称索引。
    """
    runs = sorted(_merge_runs(_segments_for_day(progress, local_day_bounds(day, timezone))),
                  key=lambda run: (run["start"], run["end"], run["activity_id"]))
    return [{"interval": f"{_fmt_clock(run['start'], timezone)}–{_fmt_clock(run['end'], timezone)}",
             "label": _activity_label(run["snapshot"], names), "duration": _fmt_duration(run["seconds"]),
             "count": run["count"]} for run in runs]


def _markdown(agent_id: int, day: date, rows: list[dict]) -> str:
    """@brief 时间线 markdown 全文：标题行、表头与各行；当日没有活动时写空标记。"""
    lines = [f"# 人物 {agent_id} · {day.isoformat()} 周{WEEKDAYS[day.weekday()]}", "",
             "| 起止 | 活动 | 总时长 | 段数 |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {row['interval']} | {row['label']} | {row['duration']} | {row['count']} |" for row in rows)
    if not rows:
        lines.append("当日无活动记录。")
    return "\n".join(lines) + "\n"


def render_day_markdown(conn: sqlite3.Connection, agent_id: int, day: date, timezone: str) -> str:
    """@brief 生成该人物该日的确定性时间线 markdown 全文。"""
    names = name_index(_name_bodies(conn))
    return _markdown(agent_id, day, day_rows(_progress_bodies(conn, agent_id), names, day, timezone))


def main(argv: list[str] | None = None) -> None:
    """@brief 命令行入口：解析参数、渲染并写出 timeline/<agent>-<date>.md。"""
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
    conn = _connect(args.sqlite)
    try:
        manifest = json.loads((args.sqlite.parent / "manifest.json").read_bytes())
        markdown = render_day_markdown(conn, args.agent, day, manifest["window"]["timezone"])
    finally:
        conn.close()
    target_dir = args.out / "timeline"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"{args.agent}-{day.isoformat()}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
