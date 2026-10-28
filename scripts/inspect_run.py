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
  python3 scripts/inspect_run.py <run_dir> continuation [--topic 话题 ...] [--baseline <run_dir>]
  python3 scripts/inspect_run.py <run_dir> workday --environment <环境目录>

时刻一律按运行清单的 window.timezone 显示；events 里活动与手机事件的写法取 worldmock.recording_engine.projections.presentation.display_wording，
名称取这次运行冻结的环境目录（docs/design/05-recording-engine.md「显示写法与检查工具」）。
rerun 只读计算 docs/design/05-recording-engine.md「显示写法与检查工具」的运行指标，只报告，不作门禁。
continuation 只读给出 docs/design/05-recording-engine.md的逐决策指标与合计（JSON）；--baseline 先核对
两份清单（同一提交、同一环境输入、同一模型身份），不可比即拒绝比较。
workday 只读打印 docs/design/05-recording-engine.md 的 {run_id, workload, routine_deviation, notes}（JSON，只报告）：
班次、住处、播报间隔与下班、到家窗口都取自 --environment 完整输入（原文字节身份须等于运行清单，否则拒绝、不打印）。
event 与 events 打印的观察已把观察分件换回原表（docs/design/05-recording-engine.md「检查工具」）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from worldmock.recording_engine.projections.presentation.display_wording import (
    DisplayNames,
    activity_phrase,
    event_content_id,
    phone_phrase,
)
from worldmock.recording_engine.validation.frozen_environment import frozen_display_names  # noqa: E402
from worldmock.recording_engine.storage.observation_parts import rehydrate_observation  # noqa: E402
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id  # noqa: E402
from worldmock.recording_engine.storage.store import EVENT_PAGE_SIZE  # noqa: E402
from worldmock.runtime.monitoring.status import event_type_counts  # noqa: E402
from worldmock.simulation_engine.body.felt import APETITE_BASE_LEVEL, APPETITE_BANDS, APPETITE_STARVING_LEVEL  # noqa: E402

#: 饥饿分级由轻到重（与体感分级同源）：「最低饥饿分级」取观察中出现过的最重一档。
APPETITE_ORDER = (APETITE_BASE_LEVEL, *(label for _, label in APPETITE_BANDS), APPETITE_STARVING_LEVEL)
#: 「播报唤醒占比」统计的唤醒原因。
PROGRESS_REPORT_REASON = "progress_report_due"
#: 「零点计划是否含早于 08:30 的提醒」的界限（场景本地时刻，时与分）。
EARLY_REMINDER = (8, 30)
#: 活动行写活动名而不写写法的两种活动。
NAMED_KINDS = frozenset({"travel", "sleep"})
#: 写法后跟订单身份的两种手机通知。
NOTICE_KINDS = frozenset({"order_notification", "payment_notification"})


@dataclass(frozen=True)
class Display:
    """@brief events 一行摘要读的只读显示上下文。"""

    #: 这次运行冻结的环境目录给出的显示名称。
    names: DisplayNames
    #: 全库订单身份到商品履约记录，不受事件筛选的影响。
    orders: dict
    #: 运行清单的场景时区。
    zone: ZoneInfo


def _connect(run_dir: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库。"""
    db = (run_dir / "footprints.sqlite").resolve()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _paged(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Iterator[tuple]:
    """@brief 按事件序号窗口 (after, after+EVENT_PAGE_SIZE] 逐页执行 sql（前两个参数是窗口两端），按页先后给出各行。

    窗口只覆盖开始时已提交的事件；每页一个语句并一次取完，至多读 EVENT_PAGE_SIZE 条事件，页间不持有读锁。
    """
    last = conn.execute("SELECT COALESCE(MAX(event_seq),0) FROM events").fetchone()[0]
    for after in range(0, last, EVENT_PAGE_SIZE):
        yield from conn.execute(sql, (after, after + EVENT_PAGE_SIZE, *args)).fetchall()


def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict]:
    """@brief 按事件序号窗口分页读取匹配的事件体并解析为字典列表（按 event_seq）；每页一个短语句。"""
    sql = "SELECT event_seq, body FROM events WHERE event_seq>? AND event_seq<=?"
    sql += (f" AND ({where})" if where else "") + " ORDER BY event_seq"
    return [dict(json.loads(body), seq=seq) for seq, body in _paged(conn, sql, args)]


def _rehydrated(conn: sqlite3.Connection, row: dict) -> dict:
    """@brief 观察行的分件引用换回原表（与存储层解码相同），其余事件原样；event 与 events 打印的都是完整事件。"""
    if row["event_type"] != "observation":
        return row
    return {**row, "data": rehydrate_observation(row["data"], conn)}


def _t(epoch: int, zone: ZoneInfo) -> str:
    """@brief 事件时间按运行时区转可读时刻，与运行这条命令的电脑的时区无关。"""
    return datetime.fromtimestamp(epoch, zone).strftime("%m-%d %H:%M:%S")


def _zone(run_dir: Path) -> ZoneInfo:
    """@brief 运行清单 window.timezone 给出的场景时区。"""
    return ZoneInfo(_manifest(run_dir)["window"]["timezone"])


def display_context(conn: sqlite3.Connection, run_dir: Path) -> Display:
    """@brief 读一次冻结名称与全库订单记录，取运行时区；名称取不到时照运行故障失败。"""
    manifest = _manifest(run_dir)
    results = _rows(conn, "event_seq IN (SELECT event_seq FROM events "
                          "WHERE json_extract(body,'$.event_type')='action_result')")
    return Display(frozen_display_names(run_dir, manifest), orders_by_id(results),
                   ZoneInfo(manifest["window"]["timezone"]))


def cmd_summary(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印运行摘要；运行目录里的两份 JSON 一律按 UTF-8 读；条数走类型表达式索引，不读事件正文。"""
    total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    counts = sorted(event_type_counts(conn), key=lambda item: (-item[1], item[2]))
    manifest = _manifest(a.run_dir)
    status = json.loads((a.run_dir / "status.json").read_text(encoding="utf-8"))
    print(f"run_id     : {manifest.get('run_id')}")
    print(f"状态       : {status.get('status')} ({status.get('phase')})")
    print(f"事件总数   : {total}")
    for name, count, _ in counts:
        print(f"  {name:24s} {count}")


def cmd_events(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印事件流（一行一事件，可过滤）；名称与订单记录在筛选之前读一次。"""
    display = display_context(conn, a.run_dir)
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
    for r in (_rehydrated(conn, row) for row in rows):
        agent = f"a{r['agent_id']}" if r.get("agent_id") else "  "
        if a.full:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        brief = _brief(r, display)
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], display.zone)} {agent} {r['event_type']:20s} {brief}")


def _brief(r: dict, display: Display) -> str:
    """@brief 按事件类型提取一行摘要；受理、拒绝、记忆、请求四种行直接取当前格式的键，缺键照 KeyError 失败。"""
    d = r["data"]
    t = r["event_type"]
    if t == "action_requested":
        return f"{d['tool_name']} {json.dumps(d['request'], ensure_ascii=False)[:80]}"
    if t == "action_accepted":
        phrase = _activity_brief(d, display) if "activity" in d else d["message"]
        return f"{phrase} → {d['status']}"
    if t == "action_rejected":
        return f"{d['error_code']}: {d['reason']}"
    if t == "environment_event":
        return _environment_brief(d, display)
    if t == "activity_progress":
        return f"{_activity_brief(d, display)} → {d['activity']['status']}"
    if t == "memory_updated":
        return f"{len(d['episodes'])} 条"
    if r["resource_changes"]:
        return json.dumps(r["resource_changes"], ensure_ascii=False)[:100]
    return json.dumps({k: v for k, v in d.items() if k not in ("request", "response")}, ensure_ascii=False)[:90]


def _activity_brief(d: dict, display: Display) -> str:
    """@brief 受理或进度事件里的活动：出行和睡眠写活动名，其余写这条事件指向的那一条内容的写法。"""
    snapshot = d["activity"]
    if snapshot["kind"] in NAMED_KINDS:
        return snapshot["name"]
    return activity_phrase(display.names, snapshot, [event_content_id(d)], display.orders)


def _environment_brief(d: dict, display: Display) -> str:
    """@brief 环境事件的一行摘要：三种手机事件写写法再跟起止或订单身份，停留进出写地点。"""
    kind = d.get("kind")
    if kind == "app_foreground":
        span = f"{_t(d['interval_start'], display.zone)} → {_t(d['interval_end'], display.zone)}"
        return f"{phone_phrase(display.names, d)} {span}"
    if kind in NOTICE_KINDS:
        return f"{phone_phrase(display.names, d)} {' '.join(d['order_ids'])}"
    if kind in ("location_entered", "location_left"):
        return f"{kind} {d['location_id']} {d.get('entry_kind', '')}".rstrip()
    return f"{kind} {d.get('scheduled_id','')}"


def cmd_event(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印单个事件全文。"""
    rows = [_rehydrated(conn, row) for row in _rows(conn, "event_seq=?", (a.seq,))]
    print(json.dumps(rows[0], ensure_ascii=False, indent=1) if rows else f"seq {a.seq} 不存在")


def cmd_money(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印全部资金流水与期末余额。"""
    zone = _zone(a.run_dir)
    balances: dict[str, int] = {}
    for r in _rows(conn, "json_extract(body,'$.resource_changes')!='{}'"):
        changes = r["resource_changes"]
        if not changes:
            continue
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} {r['event_type']:18s} "
              f"{json.dumps(changes, ensure_ascii=False)}")
        for acct, delta in changes.items():
            balances[acct] = balances.get(acct, 0) + delta
    print("── 期末余额 ──")
    for acct in sorted(balances):
        print(f"  {acct:32s} {balances[acct]:>12,}")


def cmd_body(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印身体轨迹（时间/能量/睡眠不足账/饥饿与困意分级），读观察事件的 body.values 与 body.levels。"""
    zone = _zone(a.run_dir)
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
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} a{r['agent_id']}  能量={cur[0]:>7.1f}  "
              f"睡眠不足={cur[1]:>5.2f}h  饥饿={cur[2]}  困意={cur[3]}")


def cmd_flows(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印资源流水分录。"""
    zone = _zone(a.run_dir)
    rows = _rows(conn, "json_extract(body,'$.data.resource_flows') IS NOT NULL")
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        for f in r["data"]["resource_flows"]:
            print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} a{r['agent_id']}  {f['resource']:>18s} "
                  f"{f['direction']:>6s} {f['amount']:>10.2f}  {f['cause']}")


def _select(conn: sqlite3.Connection, event_type: str, columns: str, kind: str | None = None) -> list[tuple]:
    """@brief 只取某类事件的指定列（SQL 表达式，不解析整条事件体），按序号升序；环境事件可再按 data.kind 过滤。"""
    sql = (f"SELECT {columns} FROM events WHERE event_seq>? AND event_seq<=? "
           "AND json_extract(body, '$.event_type') = ?")
    args: list = [event_type]
    if kind is not None:
        sql += " AND json_extract(body, '$.data.kind') = ?"
        args.append(kind)
    return list(_paged(conn, sql + " ORDER BY event_seq", tuple(args)))


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


def food_receipts(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """@brief 本人食品收货的真实订单身份和发生秒数，不构造用餐活动。"""
    rows = _select(conn, "environment_event",
        "json_extract(body, '$.agent_id'), json_extract(body, '$.data.order_id'), "
        "json_extract(body, '$.occurred_at'), json_extract(body, '$.data.category')", "goods_received")
    return [(agent, order, moment) for agent, order, moment, category in rows
            if category in {"meal", "snack", "drink"}]


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


def _location_meals(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """@brief 在地点吃的一餐：data.content.completion_effects.food 不为 null 的 activity_progress（两个档位都算），
    每条一项，返回人物身份与事件时刻。"""
    rows = _select(conn, "activity_progress",
                   "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at'), "
                   "json_extract(body, '$.data.content.completion_effects.food')")
    return [(agent, moment) for agent, moment, food in rows if food is not None]


def lowest_energy_before_meal(conn: sqlite3.Connection, zone: ZoneInfo) -> list[dict]:
    """@brief 首次进食前最低能量：每人每个本地日第一次进食之前，共同身体结算的最低 energy_kcal。

    与进食次数读同一组进食时刻：食品收货与在地点吃的一餐（_location_meals），当天最早的一次取两者中较早的那个。
    当日没有进食时取全日最低并标 ate=False；进食前没有结算记录时 energy_kcal 为 None。
    """
    first_meal: dict[tuple[int, str], int] = {}
    meals = [(agent, moment) for agent, _, moment in food_receipts(conn)] + _location_meals(conn)
    for agent, moment in meals:
        key = (agent, _local_date(moment, zone))
        first_meal[key] = min(first_meal.get(key, moment), moment)
    report = []
    for (agent, date), points in sorted(_energy_points(conn, zone).items()):
        limit = first_meal.get((agent, date))
        before = [energy for moment, energy in points if limit is None or moment <= limit]
        report.append({"agent_id": agent, "date": date, "ate": limit is not None,
                       "energy_kcal": min(before) if before else None})
    return report


def meal_count(conn: sqlite3.Connection) -> int:
    """@brief 进食次数：本人零食、食品、饮料收货的不同订单数，加上在地点吃的一餐的条数（每条计一次）。"""
    return len({(agent, order_id) for agent, order_id, _ in food_receipts(conn)}) + len(_location_meals(conn))


def walking_share(conn: sqlite3.Connection) -> dict:
    """@brief 步行占比：自带步行的出行时长 / 全部出行时长；没有步行秒数时 available 为 False。"""
    seconds: Counter = Counter()
    for mode, start, end in _select(conn, "activity_progress",
                                     "json_extract(body, '$.data.activity.travel_method_id'), "
                                     "json_extract(body, '$.data.interval_start'), "
                                     "json_extract(body, '$.data.interval_end')"):
        if mode is not None and _valid_interval(start, end):
            seconds[mode] += end - start
    return {"walking_seconds": seconds["walking"], "travel_seconds": sum(seconds.values()),
            "available": seconds["walking"] > 0}


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
        walk = "不适用（没有步行）"
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


#: 续接对比的缺省话题（docs/design/05-recording-engine.md「显示写法与检查工具」中的续接对比示例）。
DEFAULT_TOPICS = ("咖啡",)
#: 两臂清单比较时允许不同的运行身份键；其余键（去掉续接开关）必须相同。
RUN_IDENTITY_KEYS = frozenset({"run_id", "source_group_id", "source_run_ids"})
#: 行动请求的 SQL 判定（与 footprints.store 的行动请求谓词同义）。
_ACTION_REQUEST = ("json_extract(body,'$.event_type')='model_request' AND json_extract(body,'$.data.purpose') IS NULL "
                   "AND json_extract(body,'$.data.request.extra_body.thinking.type')='enabled' AND EXISTS "
                   "(SELECT 1 FROM json_each(body,'$.data.request.tools') "
                   "WHERE json_extract(value,'$.function.name')='start_main_activity')")


def _message(response: dict | None) -> dict:
    """@brief 提供商响应第一条选择的 assistant 消息（缺失时为空字典）。"""
    choices = (response or {}).get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    return first.get("message") if isinstance(first.get("message"), dict) else {}


def _terminal_counts(terminals: list[dict]) -> dict:
    """@brief 协议错误（响应带 error）、传输失败（phase=failure）与其中的 400（单列，不计入传输失败）。"""
    failures = [row["data"].get("error") or "" for row in terminals if row["data"].get("phase") == "failure"]
    bad = sum("400" in text or "BadRequestError" in text for text in failures)
    return {"protocol_errors": sum(bool(row["data"].get("error")) for row in terminals
                                   if row["data"].get("phase") == "response"),
            "transport_failures": len(failures) - bad, "bad_request_400": bad}


def _reasoning(responses: list[dict]) -> dict:
    """@brief 思考量：每个响应都报告 reasoning_tokens 时给 token 数，否则只给思考原文字符数，两者不混算。"""
    tokens = [((row.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
              for row in responses]
    if responses and all(isinstance(value, int) for value in tokens):
        return {"reasoning_tokens": sum(tokens), "reasoning_chars": None}
    texts = [_message(row).get("reasoning_content") or "" for row in responses]
    return {"reasoning_tokens": None, "reasoning_chars": sum(len(text) for text in texts)}


def _usage(pairs: list[tuple[dict, dict]]) -> dict:
    """@brief 缓存命中、输入 token 与计价成本（与质量报告同一冻结价目）；任一响应缺字段时对应合计为 None。"""
    from worldmock.recording_engine.reports.quality import _estimate_cost

    usages = [(request, response["data"]["response"]) for request, response in pairs]
    hits = [(body.get("usage") or {}).get("prompt_cache_hit_tokens") for _, body in usages]
    prompts = [(body.get("usage") or {}).get("prompt_tokens") for _, body in usages]
    costs = [_estimate_cost(body.get("model"), body.get("usage"), request.get("recorded_at"))[0]
             for request, body in usages]
    priced = [cost for cost in costs if cost is not None]
    return {"cache_hit_tokens": sum(hits) if usages and all(isinstance(value, int) for value in hits) else None,
            "prompt_tokens": sum(prompts) if usages and all(isinstance(value, int) for value in prompts) else None,
            "cost_usd": round(sum(priced), 12) if priced else None, "unpriced_responses": len(costs) - len(priced)}


def _decisions(conn: sqlite3.Connection) -> list[dict]:
    """@brief 逐决策收集行动请求与其终态（按 caused_by 严格关联），按首个请求的事件序号排列。"""
    terminals: dict[str, dict] = {}
    for row in _rows(conn, "json_extract(body,'$.event_type')='model_response'"):
        for cause in row.get("caused_by") or []:
            terminals.setdefault(cause, row)
    decisions: dict[str, dict] = {}
    for request in _rows(conn, _ACTION_REQUEST):
        entry = decisions.setdefault(request["decision_id"], {
            "decision_id": request["decision_id"], "agent_id": request["agent_id"],
            "occurred_at": request["occurred_at"], "calls": []})
        entry["calls"].append((request, terminals.get(request["event_id"])))
    return list(decisions.values())


def _decision_metrics(decision: dict, topics: tuple[str, ...]) -> dict:
    """@brief 一次决策的请求数、思考量、错误分类、缓存与成本、话题命中。"""
    terminals = [terminal for _, terminal in decision["calls"] if terminal is not None]
    pairs = [(request, terminal) for request, terminal in decision["calls"] if terminal is not None
             and terminal["data"].get("phase") == "response" and isinstance(terminal["data"].get("response"), dict)]
    responses = [terminal["data"]["response"] for _, terminal in pairs]
    reasoning = "\n".join(_message(row).get("reasoning_content") or "" for row in responses)
    return {"requests": len(decision["calls"]), **_reasoning(responses), **_terminal_counts(terminals),
            **_usage(pairs), "topics": {topic: topic in reasoning for topic in topics}}


def _assistant_matches(message: dict, response: dict | None) -> bool:
    """@brief 续接中的 assistant 与上一决策最终响应的思考、正文和工具调用相同；工具调用是一个，或两个开始工具各一个。"""
    from worldmock.state.interaction.actions import START_TOOLS

    final = _message(response)
    calls = [(call.get("id"), (call.get("function") or {}).get("name"), (call.get("function") or {}).get("arguments"))
             for call in final.get("tool_calls") or []]
    sent = [(call.get("id"), call["function"]["name"], call["function"]["arguments"])
            for call in message.get("tool_calls") or []]
    names = [name for _, name, _ in sent]
    return (message.get("reasoning_content") == final.get("reasoning_content") and bool(final.get("reasoning_content"))
            and message.get("content") == final.get("content") and sent == calls
            and (len(names) == 1 or sorted(names) == sorted(START_TOOLS)))


def _chain_intact(previous: dict, decision: dict) -> bool:
    """@brief 链完整：同人物同秒；首个请求 = 上一决策末次请求的全部消息 + 其最终 assistant + 按调用次序对应其每个工具
    调用身份的各一条 tool 消息（一个世界请求一条，开始组两条）；其中每个 assistant 都带思考。"""
    last_request, last_terminal = previous["calls"][-1]
    before = last_request["data"]["request"]["messages"]
    first = decision["calls"][0][0]["data"]["request"]["messages"]
    size = len(before)
    if len(first) <= size or first[:size] != before or previous["occurred_at"] != decision["occurred_at"]:
        return False
    assistant, tools = first[size], first[size + 1:]
    if not _assistant_matches(assistant, (last_terminal or {}).get("data", {}).get("response")):
        return False
    answered = [(item.get("role"), item.get("tool_call_id")) for item in tools]
    return (answered == [("tool", call["id"]) for call in assistant["tool_calls"]]
            and all(item.get("reasoning_content") for item in first if item.get("role") == "assistant"))


def _chain_rows(decisions: list[dict]) -> list[dict]:
    """@brief 逐人物按决策顺序标出同秒跟进、链位置（链首为 0，断链为 None）、链完整性与工具是否与链首相同。"""
    rows, last, heads = [], {}, {}
    for decision in decisions:
        agent, first = decision["agent_id"], decision["calls"][0][0]["data"]["request"]
        previous = last.get(agent)
        row = {"same_second_follow_up": previous is not None and previous["occurred_at"] == decision["occurred_at"],
               "chain_position": 0, "chain_intact": None, "tools_same_as_head": None}
        if len(first["messages"]) > 2:
            intact = previous is not None and _chain_intact(previous, decision)
            row.update(chain_intact=intact, chain_position=previous["position"] + 1 if intact else None)
            head = heads.get(agent) if intact else None
            row["tools_same_as_head"] = head is not None and first.get("tools") == head.get("tools")
        else:
            heads[agent] = first
        decision["position"] = row["chain_position"] if row["chain_position"] is not None else 0
        last[agent] = decision
        rows.append(row)
    return rows


def _repeat_orders(conn: sqlite3.Connection, zone: ZoneInfo) -> dict[str, int]:
    """@brief 按订单去重后的重复下单：同一人物同一本地日再次购买同一商品的订单数，按下单决策计。"""
    from worldmock.recording_engine.projections.commerce.purchases import project_purchase_facts

    events = _rows(conn, "json_extract(body,'$.event_type') IN ('action_result','action_requested')")
    decisions = {row["action_id"]: row["decision_id"] for row in events if row["event_type"] == "action_requested"}
    seen: set[tuple] = set()
    repeats: Counter = Counter()
    for fact in project_purchase_facts(events):
        if fact["fact_kind"] != "purchase":
            continue
        key = (fact["agent_id"], fact["goods_id"], _local_date(fact["created_at"], zone))
        if key in seen:
            repeats[decisions.get(fact["action_id"])] += 1
        seen.add(key)
    return dict(repeats)


def continuation_report(conn: sqlite3.Connection, topics: tuple[str, ...] = DEFAULT_TOPICS) -> dict:
    """@brief 续接对比（只报告）：逐决策的链位置、请求数、思考量、错误分类、缓存与成本、话题、工具与链首、重复下单与链完整性。"""
    zone = ZoneInfo(json.loads(conn.execute("SELECT body FROM world_state").fetchone()[0])["timezone"])
    decisions = _decisions(conn)
    repeats = _repeat_orders(conn, zone)
    rows = []
    for decision, chain in zip(decisions, _chain_rows(decisions)):
        rows.append({"decision_id": decision["decision_id"], "agent_id": decision["agent_id"],
                     "occurred_at": decision["occurred_at"], **chain, **_decision_metrics(decision, topics),
                     "repeat_orders": repeats.get(decision["decision_id"], 0)})
    return {"decisions": rows, "summary": _summary(rows, topics)}


def _reasoning_total(rows: list[dict]) -> dict:
    """@brief 一组决策的思考量合计：token 与字符分列，各自只合计报告了该口径的决策。"""
    tokens = [row["reasoning_tokens"] for row in rows if row["reasoning_tokens"] is not None]
    chars = [row["reasoning_chars"] for row in rows if row["reasoning_chars"] is not None]
    return {"reasoning_tokens": sum(tokens), "decisions_with_tokens": len(tokens),
            "reasoning_chars": sum(chars), "decisions_with_chars": len(chars)}


def _summary(rows: list[dict], topics: tuple[str, ...]) -> dict:
    """@brief 全运行合计：链长、同秒跟进与其余决策的思考量（分列）、错误、缓存、成本、话题、工具一致与重复下单。"""
    continued = [row for row in rows if row["chain_position"]]
    known = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    return {
        "decisions": len(rows), "continued": len(continued),
        "broken_chains": sum(row["chain_intact"] is False for row in rows),
        "longest_chain": max((row["chain_position"] or 0 for row in rows), default=-1) + 1,
        "same_second_follow_ups": sum(row["same_second_follow_up"] for row in rows),
        "requests": sum(row["requests"] for row in rows),
        "follow_up_reasoning": _reasoning_total([row for row in rows if row["same_second_follow_up"]]),
        "other_reasoning": _reasoning_total([row for row in rows if not row["same_second_follow_up"]]),
        **{key: sum(row[key] for row in rows) for key in ("protocol_errors", "transport_failures", "bad_request_400")},
        "cache_hit_tokens": sum(row["cache_hit_tokens"] or 0 for row in rows),
        "known_cost_usd": round(sum(known), 12) if known else None,
        "unpriced_responses": sum(row["unpriced_responses"] for row in rows),
        "topic_hits": {topic: sum(row["topics"][topic] for row in rows) for topic in topics},
        "tools_same_as_head": sum(row["tools_same_as_head"] is True for row in continued),
        "repeat_orders": sum(row["repeat_orders"] for row in rows),
    }


def _switch(manifest: dict) -> bool | None:
    """@brief 清单冻结的同秒续接开关（与引擎同一读法）；清单没有阶段身份（特性之前的运行、引擎直驱）时为 None。

    特性之前的运行带决策自述阶段，既不是开臂也不是关臂（docs/design/05-recording-engine.md）。
    """
    from worldmock.simulation_engine.decisions.continuation import continuation_enabled

    if not isinstance(manifest.get("stage_model_identity"), dict):
        return None
    return continuation_enabled(manifest)


def _comparable_view(manifest: dict) -> dict:
    """@brief 清单去掉运行身份键与续接开关后的比较视图。"""
    view = {key: value for key, value in manifest.items() if key not in RUN_IDENTITY_KEYS}
    identity = view.get("stage_model_identity")
    if isinstance(identity, dict):
        identity = json.loads(json.dumps(identity))
        identity["stages"]["action"].pop("same_second_continuation", None)
        view["stage_model_identity"] = identity
    return view


def comparison_problems(arm: dict, baseline: dict) -> list[str]:
    """@brief 两臂可比：同一提交、同一场景文件、同一模型身份，只有续接开关不同；返回拒绝比较的原因（空表示可比）。

    没有冻结开关的运行（特性之前的运行）不能作任何一臂。
    """
    switches = (_switch(arm), _switch(baseline))
    if None in switches:
        problems = ["a run without a frozen same_second_continuation (pre-feature manifest) cannot be an arm"]
    else:
        problems = [] if switches[0] != switches[1] else ["same_second_continuation is the same in both runs"]
    left, right = _comparable_view(arm), _comparable_view(baseline)
    problems += [f"manifest field {key} differs" for key in sorted(set(left) | set(right))
                 if left.get(key) != right.get(key)]
    return problems


def _manifest(run_dir: Path) -> dict:
    """@brief 读取运行清单。"""
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _arm(run_dir: Path, conn: sqlite3.Connection, topics: tuple[str, ...]) -> dict:
    """@brief 一臂的运行身份、开关与续接对比报告。"""
    manifest = _manifest(run_dir)
    return {"run_id": manifest.get("run_id"), "same_second_continuation": _switch(manifest),
            **continuation_report(conn, topics)}


def cmd_continuation(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印续接对比（JSON，只报告）；--baseline 先核对两份清单，不可比即以英文错误拒绝比较。"""
    topics = tuple(a.topic or DEFAULT_TOPICS)
    report = _arm(a.run_dir, conn, topics)
    if a.baseline is not None:
        problems = comparison_problems(_manifest(a.run_dir), _manifest(a.baseline))
        if problems:
            raise SystemExit("Runs are not comparable: " + "; ".join(problems))
        other = _connect(a.baseline)
        try:
            report = {"arm": report, "baseline": _arm(a.baseline, other, topics)}
        finally:
            other.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_workday(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 工作安排与下班、到家窗口取已核对两份原文身份的完整环境，不读人物工作区。"""
    from worldmock.configuration.inputs.environment import load_environment
    from tools.analysis.behavior.workday import workday_inputs, workday_reports

    manifest = _manifest(a.run_dir)
    inputs = load_environment(a.environment)
    if (inputs.persona_sha256 != manifest["persona_sha256"]
            or inputs.config_sha256 != manifest["config_sha256"]):
        raise SystemExit("Environment inputs do not match the run manifest")
    report = workday_reports(_rows(conn), inputs.config.timezone, workday_inputs(inputs))
    print(json.dumps({"run_id": manifest["run_id"], **report}, ensure_ascii=False, indent=2))


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
    pc = sub.add_parser("continuation")
    pc.add_argument("--topic", action="append")
    pc.add_argument("--baseline", type=Path)
    pw = sub.add_parser("workday")
    pw.add_argument("--environment", type=Path, required=True)
    args = parser.parse_args()
    conn = _connect(args.run_dir)
    {"summary": cmd_summary, "events": cmd_events, "event": cmd_event, "money": cmd_money, "body": cmd_body,
     "flows": cmd_flows, "rerun": cmd_rerun, "continuation": cmd_continuation,
     "workday": cmd_workday}[args.cmd](conn, args)
