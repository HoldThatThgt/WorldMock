#!/usr/bin/env python3
"""@brief 只读浏览一次运行的足迹数据：摘要、事件流、资金流水、身体轨迹、分录与真实复验指标。

用法：
  python3 scripts/inspect_run.py <run_dir> summary
  python3 scripts/inspect_run.py <run_dir> events [--type TYPE] [--agent N] [--tail K] [--full]
  python3 scripts/inspect_run.py <run_dir> event <seq>
  python3 scripts/inspect_run.py <run_dir> money
  python3 scripts/inspect_run.py <run_dir> body [--agent N]
  python3 scripts/inspect_run.py <run_dir> flows [--tail K]
  python3 scripts/inspect_run.py <run_dir> rerun

rerun 只读计算 docs/dev/SPEC-time-and-energy.md 验收台账「真实复验」行的八项指标，只报告，不作门禁。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldmock.life.felt import APETITE_BASE_LEVEL, APPETITE_BANDS, APPETITE_STARVING_LEVEL  # noqa: E402

#: 饥饿分级由轻到重（与体感分级同源）：「最低饥饿分级」取观察中出现过的最重一档。
APPETITE_ORDER = (APETITE_BASE_LEVEL, *(label for _, label in APPETITE_BANDS), APPETITE_STARVING_LEVEL)
#: 「播报唤醒占比」统计的唤醒原因。
PROGRESS_REPORT_REASON = "progress_report_due"
#: 「零点计划是否含早于 08:30 的提醒」的界限（场景本地时刻，时与分）。
EARLY_REMINDER = (8, 30)


def _connect(run_dir: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库。"""
    db = (run_dir / "footprints.sqlite").resolve()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict]:
    """@brief 读取事件体并解析为字典列表。"""
    sql = "SELECT event_seq, body FROM events"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY event_seq"
    return [dict(json.loads(b), seq=s) for s, b in conn.execute(sql, args)]


def _t(epoch: int) -> str:
    """@brief 事件时间转可读时刻。"""
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M:%S")


def cmd_summary(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印运行摘要。"""
    rows = _rows(conn)
    manifest = json.loads((a.run_dir / "manifest.json").read_text())
    status = json.loads((a.run_dir / "status.json").read_text())
    print(f"run_id     : {manifest.get('run_id')}")
    print(f"状态       : {status.get('status')} ({status.get('phase')})")
    print(f"事件总数   : {len(rows)}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["event_type"]] = counts.get(r["event_type"], 0) + 1
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:24s} {v}")


def cmd_events(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印事件流（一行一事件，可过滤）。"""
    where, args = [], []
    if a.type:
        where.append("json_extract(body,'$.event_type')=?")
        args.append(a.type)
    if a.agent is not None:
        where.append("json_extract(body,'$.agent_id')=?")
        args.append(a.agent)
    rows = _rows(conn, " AND ".join(where), tuple(args))
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        agent = f"a{r['agent_id']}" if r.get("agent_id") else "  "
        if a.full:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        brief = _brief(r)
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} {agent} {r['event_type']:20s} {brief}")


def _brief(r: dict) -> str:
    """@brief 按事件类型提取一行摘要。"""
    d = r.get("data", {})
    t = r["event_type"]
    if t == "action_requested":
        payload = d.get("request", d.get("arguments", ""))
        return f"{d.get('tool_name') or d.get('kind','?')} {json.dumps(payload, ensure_ascii=False)[:80]}"
    if t == "action_accepted":
        return f"{d.get('tool_name') or d.get('kind','?')}"
    if t == "action_rejected":
        return f"{d.get('tool_name','?')} → {d.get('error_code','?')}"
    if t == "environment_event":
        return f"{d.get('kind')} {d.get('scheduled_id','')}"
    if t == "activity_progress":
        act = d.get("activity", {})
        return f"{act.get('kind','?')} → {act.get('status','?')} target={act.get('target_id','')}"
    if t == "memory_updated":
        return f"{len(d.get('episode_ids', []))} 条"
    if "resource_changes" in d and d["resource_changes"]:
        return json.dumps(d["resource_changes"], ensure_ascii=False)[:100]
    return json.dumps({k: v for k, v in d.items() if k not in ("request", "response")}, ensure_ascii=False)[:90]


def cmd_event(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印单个事件全文。"""
    rows = _rows(conn, "event_seq=?", (a.seq,))
    print(json.dumps(rows[0], ensure_ascii=False, indent=1) if rows else f"seq {a.seq} 不存在")


def cmd_money(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印全部资金流水与期末余额。"""
    balances: dict[str, int] = {}
    for r in _rows(conn, "json_extract(body,'$.data.resource_changes') IS NOT NULL"):
        changes = r["data"].get("resource_changes") or {}
        if not changes:
            continue
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} {r['event_type']:18s} {json.dumps(changes, ensure_ascii=False)}")
        for acct, delta in changes.items():
            balances[acct] = balances.get(acct, 0) + delta
    print("── 期末余额 ──")
    for acct in sorted(balances):
        print(f"  {acct:32s} {balances[acct]:>12,}")


def cmd_body(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印身体轨迹（时间/能量/睡眠不足账/饥饿与困意分级），读观察事件的 body.values 与 body.levels。"""
    where = "json_extract(body,'$.event_type')='observation'"
    args: tuple = ()
    if a.agent is not None:
        where += " AND json_extract(body,'$.agent_id')=?"
        args = (a.agent,)
    prev = None
    for r in _rows(conn, where, args):
        b = r["data"].get("body") or {}
        values, levels = b.get("values") or {}, b.get("levels") or {}
        cur = (values.get("energy_kcal"), values.get("sleep_deficit_hours"),
               levels.get("appetite"), levels.get("sleepiness"))
        if cur == prev or cur[0] is None:
            continue
        prev = cur
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} a{r['agent_id']}  能量={cur[0]:>7.1f}  "
              f"睡眠不足={cur[1]:>5.2f}h  饥饿={cur[2]}  困意={cur[3]}")


def cmd_flows(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印资源流水分录。"""
    rows = _rows(conn, "json_extract(body,'$.data.resource_flows') IS NOT NULL")
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        for f in r["data"]["resource_flows"]:
            print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} a{r['agent_id']}  {f['resource']:>18s} {f['direction']:>6s} {f['amount']:>10.2f}  {f['cause']}")


def _select(conn: sqlite3.Connection, event_type: str, columns: str, kind: str | None = None) -> list[tuple]:
    """@brief 只取某类事件的指定列（SQL 表达式，不解析整条事件体），按序号升序；环境事件可再按 data.kind 过滤。"""
    sql = f"SELECT {columns} FROM events WHERE json_extract(body, '$.event_type') = ?"
    args: list = [event_type]
    if kind is not None:
        sql += " AND json_extract(body, '$.data.kind') = ?"
        args.append(kind)
    return list(conn.execute(sql + " ORDER BY event_seq", args))


def _merged(intervals: list[tuple[int, int]]) -> list[list[int]]:
    """@brief 把区间合并为互不重叠、按起点升序的并集。"""
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _uncovered_seconds(base: list[list[int]], cover: list[list[int]]) -> int:
    """@brief 并集 base 中不被并集 cover 覆盖的总秒数（两者都已合并、按起点升序，线性扫描）。"""
    total, first = 0, 0
    for start, end in base:
        while first < len(cover) and cover[first][1] <= start:
            first += 1
        cursor, index = start, first
        while index < len(cover) and cover[index][0] < end:
            total += max(0, cover[index][0] - cursor)
            cursor = max(cursor, cover[index][1])
            index += 1
        total += max(0, end - cursor)
    return total


def _valid_interval(start: object, end: object) -> bool:
    """@brief 结算区间两端都是整数秒且终点晚于起点。"""
    return isinstance(start, int) and isinstance(end, int) and end > start


def idle_seconds(conn: sqlite3.Connection) -> dict[int, int]:
    """@brief 无进行中活动时长：每人共同身体结算区间的并集，减去其活动实际区间（activity_progress）的并集。"""
    settled: dict[int, list] = defaultdict(list)
    active: dict[int, list] = defaultdict(list)
    columns = ("json_extract(body, '$.agent_id'), json_extract(body, '$.data.interval_start'), "
               "json_extract(body, '$.data.interval_end')")
    for target, rows in ((settled, _select(conn, "environment_event", columns, "body_settlement")),
                         (active, _select(conn, "activity_progress", columns))):
        for agent, start, end in rows:
            if _valid_interval(start, end):
                target[agent].append((start, end))
    return {agent: _uncovered_seconds(_merged(spans), _merged(active[agent]))
            for agent, spans in sorted(settled.items())}


def same_moment_rounds(conn: sqlite3.Connection) -> dict[str, int]:
    """@brief 同刻轮数分布：按（人物，occurred_at）计 decision_started 数，返回 {轮数: 出现次数}。

    与质量报告 decision_rhythm 的 same_moment_round_counts 同一口径。
    """
    rounds = Counter(_select(conn, "decision_started",
                             "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at')"))
    distribution = Counter(rounds.values())
    return {str(count): distribution[count] for count in sorted(distribution)}


def progress_report_wakes(conn: sqlite3.Connection) -> dict[str, int]:
    """@brief 播报唤醒占比：提供决策的共同身体结算（decision_offered 为真）中，带与仅因 progress_report_due 的次数。"""
    offered = [set(json.loads(reasons or "[]")) for reasons, flag in _select(
        conn, "environment_event",
        "json_extract(body, '$.data.wake_reasons'), json_type(body, '$.data.decision_offered')",
        "body_settlement") if flag == "true"]
    return {"offered": len(offered),
            "with_report": sum(PROGRESS_REPORT_REASON in reasons for reasons in offered),
            "only_report": sum(reasons == {PROGRESS_REPORT_REASON} for reasons in offered)}


def hungriest_level(conn: sqlite3.Connection) -> str | None:
    """@brief 最低饥饿分级：观察 body.levels.appetite 中最饿的一档；没有可识别的分级时为 None。"""
    seen = {level for (level,) in _select(conn, "observation", "json_extract(body, '$.data.body.levels.appetite')")}
    ranked = [level for level in APPETITE_ORDER if level in seen]
    return ranked[-1] if ranked else None


def eat_progress(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """@brief 进食正进度：consumption.use_mode 为 eat 且有物品本区间用量为正的活动进展，(人物, 活动, 区间起点)。

    判据与质量报告的食物利用效率取样相同：只看实际食品使用区间。
    """
    rows = _select(conn, "activity_progress",
                   "json_extract(body, '$.agent_id'), json_extract(body, '$.data.activity.activity_id'), "
                   "json_extract(body, '$.data.interval_start'), json_extract(body, '$.data.consumption.use_mode'), "
                   "json_extract(body, '$.data.consumption.items')")
    meals = []
    for agent, activity_id, start, use_mode, items in rows:
        quantities = [item.get("interval_quantity") for item in json.loads(items or "[]") if isinstance(item, dict)]
        if use_mode == "eat" and isinstance(start, int) and any(
                isinstance(value, (int, float)) and value > 0 for value in quantities):
            meals.append((agent, activity_id, start))
    return meals


def _local_date(epoch: int, zone: ZoneInfo) -> str:
    """@brief 场景时区下的本地日期（ISO 格式）。"""
    return datetime.fromtimestamp(epoch, zone).date().isoformat()


def _energy_points(conn: sqlite3.Connection, zone: ZoneInfo) -> dict[tuple[int, str], list[tuple[int, float]]]:
    """@brief 共同身体结算记下的能量点：区间起点取 before、终点取 after 的 energy_kcal，按（人物，本地日）分组。"""
    points: dict[tuple[int, str], list[tuple[int, float]]] = defaultdict(list)
    rows = _select(conn, "environment_event",
                   "json_extract(body, '$.agent_id'), json_extract(body, '$.data.interval_start'), "
                   "json_extract(body, '$.data.body_settlement.before.energy_kcal'), "
                   "json_extract(body, '$.data.interval_end'), "
                   "json_extract(body, '$.data.body_settlement.after.energy_kcal')", "body_settlement")
    for agent, start, before, end, after in rows:
        for moment, energy in ((start, before), (end, after)):
            if isinstance(moment, int) and isinstance(energy, (int, float)):
                points[(agent, _local_date(moment, zone))].append((moment, float(energy)))
    return points


def lowest_energy_before_meal(conn: sqlite3.Connection, zone: ZoneInfo) -> list[dict]:
    """@brief 首次进食前最低能量：每人每个本地日第一次进食正进度之前，共同身体结算的最低 energy_kcal。

    当日没有进食时取全日最低并标 ate=False；进食前没有结算记录时 energy_kcal 为 None。
    """
    first_meal: dict[tuple[int, str], int] = {}
    for agent, _, start in eat_progress(conn):
        key = (agent, _local_date(start, zone))
        first_meal[key] = min(first_meal.get(key, start), start)
    report = []
    for (agent, date), points in sorted(_energy_points(conn, zone).items()):
        limit = first_meal.get((agent, date))
        before = [energy for moment, energy in points if limit is None or moment <= limit]
        report.append({"agent_id": agent, "date": date, "ate": limit is not None,
                       "energy_kcal": min(before) if before else None})
    return report


def meal_count(conn: sqlite3.Connection) -> int:
    """@brief 进食次数：有进食正进度的活动数（同一活动的多段进展只计一次）。"""
    return len({(agent, activity_id) for agent, activity_id, _ in eat_progress(conn)})


def walking_share(conn: sqlite3.Connection) -> dict:
    """@brief 步行占比：步行出行时长 / 全部出行时长；观察的路线从未出现步行（只有驾车）时 available 为 False。"""
    seconds: Counter = Counter()
    for mode, start, end in _select(conn, "activity_progress",
                                     "json_extract(body, '$.data.activity.travel_mode'), "
                                     "json_extract(body, '$.data.interval_start'), "
                                     "json_extract(body, '$.data.interval_end')"):
        if mode is not None and _valid_interval(start, end):
            seconds[mode] += end - start
    modes = {mode for (mode,) in conn.execute(
        "SELECT DISTINCT json_extract(route.value, '$.mode') "
        "FROM events, json_each(events.body, '$.data.routes') route "
        "WHERE json_extract(events.body, '$.event_type') = 'observation'")}
    return {"walking_seconds": seconds["walking"], "travel_seconds": sum(seconds.values()),
            "available": "walking" in modes or seconds["walking"] > 0}


def early_reminders(conn: sqlite3.Connection, zone: ZoneInfo) -> list[dict]:
    """@brief 零点计划是否含早于 08:30 的提醒：逐份 day_plan_generated 列出最早提醒的本地时刻与判定。"""
    report = []
    for agent, occurred_at, goals in _select(conn, "environment_event",
                                             "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at'), "
                                             "json_extract(body, '$.data.day_plan.goals')", "day_plan_generated"):
        moments = sorted(datetime.fromtimestamp(goal["remind_at"], zone) for goal in json.loads(goals or "[]")
                         if isinstance(goal, dict) and isinstance(goal.get("remind_at"), int))
        earliest = moments[0] if moments else None
        report.append({"agent_id": agent, "date": _local_date(occurred_at, zone),
                       "earliest": earliest.strftime("%H:%M") if earliest else None,
                       "early": earliest is not None and (earliest.hour, earliest.minute) < EARLY_REMINDER})
    return report


def rerun_metrics(conn: sqlite3.Connection) -> dict:
    """@brief 汇总「真实复验」八项指标（只读；本地日与提醒时刻按世界状态记录的场景时区）。"""
    zone = ZoneInfo(json.loads(conn.execute("SELECT body FROM world_state").fetchone()[0])["timezone"])
    return {"idle_seconds": idle_seconds(conn), "same_moment_rounds": same_moment_rounds(conn),
            "progress_report_wakes": progress_report_wakes(conn), "hungriest_level": hungriest_level(conn),
            "lowest_energy_before_meal": lowest_energy_before_meal(conn, zone), "meal_count": meal_count(conn),
            "walking_share": walking_share(conn), "early_reminders": early_reminders(conn, zone)}


def _duration(seconds: int) -> str:
    """@brief 人读时长「H小时M分（N 秒）」。"""
    return f"{seconds // 3600}小时{seconds % 3600 // 60}分（{seconds} 秒）"


def _ratio(part: int, whole: int) -> str:
    """@brief 分数与百分比；分母为 0 时不适用。"""
    return f"{part}/{whole}（{part / whole:.0%}）" if whole else "不适用（无提供决策的唤醒）"


def rerun_lines(metrics: dict) -> list[str]:
    """@brief 把八项指标排成中文报告行（只报告，不作门禁）。"""
    idle = metrics["idle_seconds"]
    wakes, walking = metrics["progress_report_wakes"], metrics["walking_share"]
    energy = "；".join(
        f"人物 {row['agent_id']} {row['date']} "
        + ("无结算记录" if row["energy_kcal"] is None else f"{row['energy_kcal']:.1f} kcal")
        + ("" if row["ate"] else "（当日无进食）") for row in metrics["lowest_energy_before_meal"])
    if not walking["available"]:
        walk = "不适用（只有驾车）"
    elif not walking["travel_seconds"]:
        walk = "不适用（无出行）"
    else:
        walk = _ratio(walking["walking_seconds"], walking["travel_seconds"])
    reminders = "；".join(
        f"人物 {row['agent_id']} {row['date']} " + ("是" if row["early"] else "否")
        + (f"（最早提醒 {row['earliest']}）" if row["earliest"] else "（未设提醒）")
        for row in metrics["early_reminders"])
    return [
        "真实复验八项指标（只报告，不作门禁）",
        f"1. 无进行中活动总时长：{_duration(sum(idle.values()))}"
        + "".join(f"；人物 {agent} {_duration(value)}" for agent, value in idle.items()),
        "2. 同刻轮数分布：" + ("、".join(f"{rounds} 轮 × {count}" for rounds, count
                                  in metrics["same_moment_rounds"].items()) or "不适用（无决策）"),
        f"3. 播报唤醒占比：带 progress_report_due {_ratio(wakes['with_report'], wakes['offered'])}；"
        f"仅因它 {_ratio(wakes['only_report'], wakes['offered'])}",
        f"4. 最低饥饿分级：{metrics['hungriest_level'] or '不适用（无饥饿分级观察）'}",
        f"5. 首次进食前最低能量：{energy or '不适用（无共同身体结算）'}",
        f"6. 进食次数：{metrics['meal_count']}",
        f"7. 步行占比：{walk}",
        f"8. 零点计划含早于 08:30 的提醒：{reminders or '不适用（无零点计划）'}",
    ]


def cmd_rerun(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印「真实复验」八项指标。"""
    print("\n".join(rerun_lines(rerun_metrics(conn))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读浏览运行足迹")
    parser.add_argument("run_dir", type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    pe = sub.add_parser("events")
    pe.add_argument("--type")
    pe.add_argument("--agent", type=int)
    pe.add_argument("--tail", type=int)
    pe.add_argument("--full", action="store_true")
    sub.add_parser("event").add_argument("seq", type=int)
    sub.add_parser("money")
    pb = sub.add_parser("body")
    pb.add_argument("--agent", type=int)
    pf = sub.add_parser("flows")
    pf.add_argument("--tail", type=int)
    sub.add_parser("rerun")
    args = parser.parse_args()
    conn = _connect(args.run_dir)
    {"summary": cmd_summary, "events": cmd_events, "event": cmd_event,
     "money": cmd_money, "body": cmd_body, "flows": cmd_flows, "rerun": cmd_rerun}[args.cmd](conn, args)
