#!/usr/bin/env python3
"""@brief 从 footprints.sqlite 确定性投影人物一天的 timeline/<agent>-<date>.md。

同一 activity_id 的连续 activity_progress 合并为一行（起止与总时长），
静默边界产生的分段折叠不单列；活动目标先取快照标题，再按名称索引解析为品名、地名、
邮件主题、内容标题、生活活动名、任务标题与人名；种类名取 worldmock.life.readable 的中文种类表
（单一来源），解析不到名称时只写中文种类名：时间线里不出现英文种类名与内部标识
（docs/dev/SPEC-dayrun-fidelity.md 7.4）。按 agent_id 与冻结清单的场景时区
的模拟日期过滤。同库同参数输出逐字节相同；name_index 与 day_rows 也接受已排序的事件正文，
质量报告的时间线标签检查据此投影同一张时间线。

用法：
  python3 scripts/render_day.py <sqlite> --agent N --date YYYY-MM-DD [--out DIR]
"""
from __future__ import annotations

from worldmock.footprints.timeline_labels import local_day_bounds
from worldmock.footprints.intervals import valid_interval
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

from worldmock.life.readable import ACTIVITY_KIND_LABELS, BROWSE_LABELS, TRAVEL_WAYS  # noqa: E402

#: 名称索引的类别：品名（带使用方式）、地名、邮件主题、内容标题、生活活动名、任务标题、人名（联系人与同世界人物）。
NAME_KINDS = ("products", "locations", "messages", "contents", "lifestyles", "tasks", "people")
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


def _object_name(label: object) -> object:
    """@brief 私有绑定的对象名去掉类别前缀：「联系人：王磊」→「王磊」。"""
    return label.split("：", 1)[-1] if isinstance(label, str) else label


def _absorb_observation(names: dict, data: dict) -> None:
    """@brief 吸收一条 observation/action_result 里的公开名称索引。"""
    for product in _dict_rows(data.get("products")):
        pid = product.get("product_id")
        if pid and pid not in names["products"] and product.get("name"):
            names["products"][pid] = {"name": product["name"],
                                      "use_modes": product.get("use_modes") or []}
    for location in _dict_rows(data.get("locations")):
        _remember(names["locations"], location.get("location_id"), location.get("name"))
    for message in _dict_rows(data.get("messages")):
        _remember(names["messages"], message.get("message_id"), message.get("subject"))
    progress = data.get("content_progress") if isinstance(data.get("content_progress"), dict) else {}
    for item_id, row in progress.items():
        _remember(names["contents"], item_id, row.get("title") if isinstance(row, dict) else None)
    for item in _dict_rows(data.get("items")):
        _remember(names["contents"], item.get("item_id"), item.get("title"))
        _remember(names["lifestyles"], item.get("activity_id"), item.get("name"))
    for task in _dict_rows(data.get("tasks")):
        _remember(names["tasks"], task.get("task_id"), task.get("title"))
    for binding in _dict_rows(data.get("interaction_bindings")):
        if binding.get("kind") in ("contact", "person"):
            _remember(names["people"], binding.get("object_id"), _object_name(binding.get("object")))
    for binding in _dict_rows(data.get("person_bindings")):
        _remember(names["people"], binding.get("person_id"), _object_name(binding.get("name")))


def name_index(bodies: Iterable[dict]) -> dict:
    """@brief 从 observation/action_result 事件正文（按 event_seq）建立确定性的名称索引。

    同一标识先出现者胜，名称是世界静态事实，与过滤人物无关；其他事件类型忽略。
    解析不到名称时，标签只写中文种类名。
    """
    names: dict = {kind: {} for kind in NAME_KINDS}
    for body in bodies:
        if body.get("event_type") in ("observation", "action_result") and isinstance(body.get("data"), dict):
            _absorb_observation(names, body["data"])
    return names


def _title(snapshot: dict) -> str | None:
    """@brief 活动快照上引擎写的标题（活动关联对象的名称），没有时为 None。"""
    title = snapshot.get("title")
    return title if isinstance(title, str) and title else None


def _snapshot_data(snapshot: dict) -> dict:
    """@brief 活动快照的数据字典（通话、交谈的对象身份在这里）。"""
    return snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}


def _travel_label(snapshot: dict, names: dict) -> str:
    """@brief 出行标签：方式加目的地地名，地名解析不到时只写方式。"""
    mode = TRAVEL_WAYS.get(str(snapshot.get("travel_mode")), ACTIVITY_KIND_LABELS["travel"])
    destination = names["locations"].get(snapshot.get("destination_id"))
    return f"{mode}去{destination}" if destination else mode


def _media_label(snapshot: dict, names: dict) -> str:
    """@brief 观看与游戏标签：快照标题优先，其次观察目录标题；都没有时只写种类名。"""
    kind = str(snapshot.get("kind"))
    name = _title(snapshot) or names["contents"].get(snapshot.get("target_id"))
    verb = "看" if kind == "watch" else "玩游戏"
    return f"{verb}《{name}》" if name else ACTIVITY_KIND_LABELS[kind]


def _mail_label(snapshot: dict, names: dict) -> str:
    """@brief 读邮件标签：带邮件主题。"""
    subject = names["messages"].get(snapshot.get("target_id"))
    return f"读邮件「{subject}」" if subject else ACTIVITY_KIND_LABELS["read_mail"]


def _consume_label(snapshot: dict, names: dict) -> str:
    """@brief 进食/使用标签：按商品 use_modes 区分动词。"""
    product = names["products"].get(snapshot.get("target_id")) or {}
    if not product.get("name"):
        return ACTIVITY_KIND_LABELS["consume"]
    verb = "吃" if "eat" in (product.get("use_modes") or []) else "用"
    return f"{verb} {product['name']}"


def _browse_label(snapshot: dict, names: dict) -> str:
    """@brief 浏览标签：带目录的中文名。"""
    catalog = BROWSE_LABELS.get(str(snapshot.get("target_id")))
    return f"浏览{catalog}" if catalog else ACTIVITY_KIND_LABELS["browse"]


def _lifestyle_label(snapshot: dict, names: dict) -> str:
    """@brief 生活活动标签：活动名称本身。"""
    return (_title(snapshot) or names["lifestyles"].get(snapshot.get("target_id"))
            or ACTIVITY_KIND_LABELS["lifestyle"])


def _work_label(snapshot: dict, names: dict) -> str:
    """@brief 工作标签：带任务标题。"""
    title = _title(snapshot) or names["tasks"].get(snapshot.get("target_id"))
    return f"工作「{title}」" if title else ACTIVITY_KIND_LABELS["work"]


def _call_label(snapshot: dict, names: dict) -> str:
    """@brief 通话标签：和联系人通话（称谓取快照标题或名称索引）。"""
    name = _title(snapshot) or names["people"].get(_snapshot_data(snapshot).get("contact_id"))
    return f"和{name}通话" if name else ACTIVITY_KIND_LABELS["call"]


def _chat_label(snapshot: dict, names: dict) -> str:
    """@brief 交谈标签：和同世界人物当面交谈（姓名取快照标题或名称索引）。"""
    name = _title(snapshot) or names["people"].get(_snapshot_data(snapshot).get("partner_id"))
    return f"和{name}交谈" if name else ACTIVITY_KIND_LABELS["chat"]


#: 带对象的活动种类 → 标签函数；等待与睡眠只写种类名。
_LABELERS: dict[str, Callable[[dict, dict], str]] = {
    "travel": _travel_label, "watch": _media_label, "play_game": _media_label, "read_mail": _mail_label,
    "consume": _consume_label, "browse": _browse_label, "lifestyle": _lifestyle_label, "work": _work_label,
    "call": _call_label, "chat": _chat_label,
}


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
