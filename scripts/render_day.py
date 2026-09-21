#!/usr/bin/env python3
"""@brief 从 footprints.sqlite 确定性投影人物一天的 timeline/<agent>-<date>.md。

同一 activity_id 的连续 activity_progress 合并为一行（起止与总时长），
静默边界产生的分段折叠不单列；活动目标解析为标题/品名/地名，无法解析时
使用可读短标签而不是裸 hex 标识；按 agent_id 与场景时区（Asia/Shanghai）
的模拟日期过滤。同库同参数输出逐字节相同。

用法：
  python3 scripts/render_day.py <sqlite> --agent N --date YYYY-MM-DD [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

#: 场景时区（修订合同：模拟日期按 Asia/Shanghai 切日）。
SCENARIO_TIMEZONE = ZoneInfo("Asia/Shanghai")

#: 无目标活动的一行标签；带目标活动的标签由各解析函数给出。
_PLAIN_KIND_LABELS = {"wait": "空档", "sleep": "睡觉", "work": "工作"}

#: 出行方式与浏览目录的中文标签（browse 目标闭集见引擎 activity_summary）。
TRAVEL_MODES = {"driving": "驾车", "walking": "步行"}
BROWSE_LABELS = {"product_catalog": "商品目录", "foods": "食品目录", "feature_film": "电影",
                 "drama_series": "剧集", "webnovel_excerpt": "网文", "news_feed": "新闻",
                 "joke_feed": "段子", "subtitle_experience": "字幕体验", "game_session": "游戏",
                 "podcast_episode": "播客"}

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
_HEX_ID = re.compile(r"^[0-9a-f]{16,}$")
_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _connect(sqlite_path: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库，投影绝不写回运行目录。"""
    resolved = Path(sqlite_path).resolve()
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _day_bounds(day: date) -> tuple[int, int]:
    """@brief 返回该场景时区日期的左闭右开 epoch 秒边界。"""
    start = datetime(day.year, day.month, day.day, tzinfo=SCENARIO_TIMEZONE)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def _segments_for_day(conn: sqlite3.Connection, agent_id: int, bounds: tuple[int, int]) -> list[dict]:
    """@brief 读取该人物全部 activity_progress 并裁剪到当日区间。

    区间取事件 data 的 interval_start/interval_end（结算权威区间），
    与当日边界相交为空的事件丢弃；跨零点的区间被裁到 00:00。
    """
    day_start, day_end = bounds
    segments = []
    sql = ("SELECT body FROM events WHERE json_extract(body,'$.agent_id')=? "
           "AND json_extract(body,'$.event_type')='activity_progress' ORDER BY event_seq")
    for (body,) in conn.execute(sql, (agent_id,)):
        data = json.loads(body).get("data") or {}
        start = min(max(int(data.get("interval_start", 0)), day_start), day_end)
        end = min(max(int(data.get("interval_end", 0)), day_start), day_end)
        if end <= start:
            continue
        segments.append({"start": start, "end": end, "snapshot": data.get("activity") or {}})
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


def _absorb_observation(names: dict, data: dict) -> None:
    """@brief 吸收一条 observation/action_result 里的公开名称索引。"""
    for product in data.get("products") or []:
        pid = product.get("product_id")
        if pid and pid not in names["products"] and product.get("name"):
            names["products"][pid] = {"name": product["name"],
                                      "use_modes": product.get("use_modes") or []}
    for location in data.get("locations") or []:
        lid = location.get("location_id")
        if lid and lid not in names["locations"] and location.get("name"):
            names["locations"][lid] = location["name"]
    for message in data.get("messages") or []:
        mid = message.get("message_id")
        if mid and mid not in names["messages"] and message.get("subject"):
            names["messages"][mid] = message["subject"]
    for item_id, progress in (data.get("content_progress") or {}).items():
        title = progress.get("title") if isinstance(progress, dict) else None
        if title and item_id not in names["contents"]:
            names["contents"][item_id] = title
    for item in data.get("items") or []:
        _absorb_catalog_item(names, item)


def _absorb_catalog_item(names: dict, item: object) -> None:
    """@brief 吸收 browse 结果目录条目（内容标题与生活活动名称）。"""
    if not isinstance(item, dict):
        return
    item_id = item.get("item_id")
    if item_id and item.get("title") and item_id not in names["contents"]:
        names["contents"][item_id] = item["title"]
    entry_id = item.get("activity_id")
    if entry_id and item.get("name") and entry_id not in names["lifestyles"]:
        names["lifestyles"][entry_id] = item["name"]


def _scan_names(conn: sqlite3.Connection) -> dict:
    """@brief 扫描全库 observation/action_result 建立确定性的名称索引。

    同一标识先出现者胜（按 event_seq 升序），名称是世界静态事实，
    与过滤人物无关；解析不到时由标签回退路径处理。
    """
    names = {"products": {}, "locations": {}, "messages": {}, "contents": {}, "lifestyles": {}}
    sql = ("SELECT body FROM events WHERE json_extract(body,'$.event_type') IN "
           "('observation','action_result') ORDER BY event_seq")
    for (body,) in conn.execute(sql):
        _absorb_observation(names, json.loads(body).get("data") or {})
    return names


def _short_ref(value: object) -> str:
    """@brief 生成可读短标签：长 hex 标识折叠为 # 尾 6 位，可读 id 原样保留。"""
    text = str(value or "")
    if _HEX_ID.match(text):
        return "#" + text[-6:]
    return text


def _travel_label(snapshot: dict, names: dict) -> str:
    """@brief 出行标签：方式加目的地地名。"""
    mode = TRAVEL_MODES.get(snapshot.get("travel_mode"), "出行")
    destination = names["locations"].get(snapshot.get("destination_id"))
    return f"{mode}去{destination}" if destination else mode


def _media_label(kind: str, target: object, snapshot: dict, names: dict) -> str:
    """@brief 内容标签：快照标题优先，其次观察目录标题，最后短标签回退。"""
    name = snapshot.get("title") or names["contents"].get(target)
    verb = "看" if kind == "watch" else "玩游戏"
    if name:
        return f"{verb}《{name}》"
    return f"{verb}（{_short_ref(target)}）" if target else verb


def _consume_label(target: object, names: dict) -> str:
    """@brief 进食/使用标签：按商品 use_modes 区分动词。"""
    product = names["products"].get(target) or {}
    verb = "吃" if "eat" in (product.get("use_modes") or []) else "用"
    if product.get("name"):
        return f"{verb} {product['name']}"
    return f"用（{_short_ref(target)}）" if target else "消费"


def _activity_label(snapshot: dict, names: dict) -> str:
    """@brief 把活动快照解析为中文一行标签，不暴露裸 hex 标识。"""
    kind = str(snapshot.get("kind") or "")
    target = snapshot.get("target_id")
    base = _PLAIN_KIND_LABELS.get(kind, kind if kind else "活动")
    if kind == "travel":
        return _travel_label(snapshot, names)
    if kind in ("watch", "play_game"):
        return _media_label(kind, target, snapshot, names)
    if kind == "read_mail":
        subject = names["messages"].get(target)
        return f"读邮件「{subject}」" if subject else "读邮件"
    if kind == "consume":
        return _consume_label(target, names)
    if kind == "browse":
        return f"浏览{BROWSE_LABELS.get(str(target), _short_ref(target))}" if target else "浏览"
    if kind == "lifestyle":
        name = snapshot.get("title") or names["lifestyles"].get(target)
        return str(name) if name else (f"生活活动（{_short_ref(target)}）" if target else "生活活动")
    return f"{base}（{_short_ref(target)}）" if target else base


def _fmt_clock(epoch: int) -> str:
    """@brief epoch 秒转场景时区 HH:MM。"""
    return datetime.fromtimestamp(epoch, SCENARIO_TIMEZONE).strftime("%H:%M")


def _fmt_duration(seconds: int) -> str:
    """@brief 秒数转 X 小时 Y 分钟的中文时长。"""
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} 小时 {rest} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{rest} 分钟"


def render_day_markdown(conn: sqlite3.Connection, agent_id: int, day: date) -> str:
    """@brief 生成该人物该日的确定性时间线 markdown 全文。"""
    segments = _segments_for_day(conn, agent_id, _day_bounds(day))
    runs = sorted(_merge_runs(segments),
                  key=lambda run: (run["start"], run["end"], run["activity_id"]))
    names = _scan_names(conn)
    lines = [f"# 人物 {agent_id} · {day.isoformat()} 周{WEEKDAYS[day.weekday()]}", "",
             "| 起止 | 活动 | 总时长 | 段数 |", "| --- | --- | --- | --- |"]
    for run in runs:
        lines.append(f"| {_fmt_clock(run['start'])}–{_fmt_clock(run['end'])} | "
                     f"{_activity_label(run['snapshot'], names)} | "
                     f"{_fmt_duration(run['seconds'])} | {run['count']} |")
    if not runs:
        lines.append("当日无活动记录。")
    return "\n".join(lines) + "\n"


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
        markdown = render_day_markdown(conn, args.agent, day)
    finally:
        conn.close()
    target_dir = args.out / "timeline"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"{args.agent}-{day.isoformat()}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
